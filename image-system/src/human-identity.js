"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError } = require("./errors");
const { atomicJson, atomicWrite, ensureDirectory, readJson, sha256, sleep } = require("./util");

const RELEASE = "0.9.0-preview.3";
const CANDIDATE_RELEASE = "0.9.0-preview.3-candidate.4-contract-b-confirmation";
const CONTRACT_SHA256 = "d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4";
const CONTRACT_ID = "structured-close-b";
const ROUTE = "pulid-v1.1-juggernaut-xl-v9-single-original-structured-composition";
const STATE_ROOT = "/app/server/storage/aag-human-identity-state";
const SHARED_AGENT_ROOT = "/app/server/storage/aag-image-agent-state";
const RESPONSE_TIMEOUT_MS = 45 * 60 * 1000;
const ACTIVE_GENERATION = path.join(SHARED_AGENT_ROOT, "active-generation.json");
const PROMPTS = Object.freeze({
  adult: "a still mid shot portrait photograph of an adult man, full head, both shoulders, and upper torso visible in frame, camera at eye level, centered with natural background space, face clearly visible, looking at the camera",
  baby: "a still mid shot portrait photograph of a baby, full head, both shoulders, and upper torso visible in frame, camera at eye level, centered with natural background space, face clearly visible, looking at the camera",
});
const FIXTURES = Object.freeze({
  "8b131e3030a094173004ae17df02b9fa94d523cb273398b027ea6bb31e1f2c61": { fixture_id: "authorized-adult-01", domain: "adult" },
  "93665635711952c6a5da892bea90cc892b7c0a4a6748416e13a69ffd124eced6": { fixture_id: "authorized-baby-01", domain: "baby" },
});
const REFERENCE_KINDS = Object.freeze({
  HISTORICAL: "historical_validation_fixture",
  RUNTIME: "trusted_runtime_reference",
});
const CHILD_REFERENCE_CUE = /(?:\bbaby\b|\bchild\b|\bkid\b|\bgirl\b|\bboy\b|תינוק|תינוקת|ילד|ילדה)/iu;
const pendingAcknowledgements = new Map();

function safeId(value) {
  const out = String(value || "");
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(out) || out.includes("..")) {
    throw new AagError("INTERNAL_ERROR", "The local identity request identifier is invalid.");
  }
  return out;
}

function responseFile(requestId) { return path.join(STATE_ROOT, "responses", `${safeId(requestId)}.json`); }
function ackFile(requestId) { return path.join(STATE_ROOT, "acks", `${safeId(requestId)}.json`); }
function stagedReferenceFile(requestId, root = STATE_ROOT) { return path.join(root, "references", `${safeId(requestId)}.png`); }
function stagedProvenanceFile(requestId, root = STATE_ROOT) { return path.join(root, "references", `${safeId(requestId)}.provenance.json`); }

function contractDomain(task, fixture = null) {
  if (fixture?.domain) return fixture.domain;
  return CHILD_REFERENCE_CUE.test(`${task.request || ""}\n${task.prompt || ""}`) ? "baby" : "adult";
}

function classifySource(task) {
  const source = task._aag_source;
  if (!source || source.kind !== "current_attachment") {
    throw new AagError("SOURCE_REQUIRED", "Human Identity requires one current authorized original reference attachment.");
  }
  if (["path", "reference_path", "filesystem_path", "url"].some(key => source[key] !== undefined)) {
    throw new AagError("SOURCE_UNAUTHORIZED", "Human Identity does not accept caller-selected reference locations.");
  }
  for (const key of ["original_sha256", "normalized_sha256"]) {
    if (!/^[0-9a-f]{64}$/.test(String(source[key] || ""))) {
      throw new AagError("SOURCE_CORRUPT", "The trusted current attachment is missing validated image provenance.");
    }
  }
  if (!Number.isSafeInteger(source.index) || source.index < 1 || !Number.isSafeInteger(source.width) || source.width < 1 || !Number.isSafeInteger(source.height) || source.height < 1) {
    throw new AagError("SOURCE_CORRUPT", "The trusted current attachment provenance is incomplete.");
  }
  const fixture = FIXTURES[String(source.original_sha256)] || null;
  const domain = contractDomain(task, fixture);
  return fixture
    ? { reference_kind: REFERENCE_KINDS.HISTORICAL, fixture_id: fixture.fixture_id, domain }
    : { reference_kind: REFERENCE_KINDS.RUNTIME, fixture_id: null, domain };
}

function stageReference(requestId, normalized, source, owner, root = STATE_ROOT) {
  if (!normalized?.bytes || !Buffer.isBuffer(normalized.bytes)) throw new AagError("SOURCE_REQUIRED", "A normalized trusted current attachment is required.");
  if (sha256(normalized.bytes) !== source.normalized_sha256 || normalized.width !== source.width || normalized.height !== source.height || normalized.format !== "png") {
    throw new AagError("SOURCE_CORRUPT", "The normalized identity reference does not match its trusted attachment provenance.");
  }
  const target = stagedReferenceFile(requestId, root);
  const provenance = stagedProvenanceFile(requestId, root);
  ensureDirectory(path.dirname(target));
  atomicWrite(target, normalized.bytes);
  const stat = fs.lstatSync(target);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size !== normalized.bytes.length) {
    try { fs.unlinkSync(target); } catch {}
    throw new AagError("SOURCE_UNAUTHORIZED", "The private staged identity reference is unsafe.");
  }
  const scopedOwner = callerScope(owner);
  if (Object.values(scopedOwner).some(value => typeof value !== "string" || !value)) {
    try { fs.unlinkSync(target); } catch {}
    throw new AagError("OWNER_SCOPE_REQUIRED", "Trusted attachment owner provenance is incomplete.");
  }
  try {
    atomicJson(provenance, {
      schema_version: "aag.human-identity.staged-reference-provenance.v1",
      request_id: requestId,
      caller: scopedOwner,
      source: {
        kind: "current_attachment", index: source.index,
        original_sha256: source.original_sha256, normalized_sha256: source.normalized_sha256,
        width: source.width, height: source.height, format: "png",
      },
    });
  } catch (error) {
    try { fs.unlinkSync(target); } catch {}
    throw error;
  }
  return target;
}

function callerScope(owner = {}) {
  return {
    workspace_id: owner.workspace_id,
    thread_id: owner.thread_id,
    user_id: owner.user_id,
    invocation_id: owner.invocation_id,
  };
}

function verifyActiveGeneration() {
  const active = readJson(ACTIVE_GENERATION);
  if (active.schema_version !== "aag.image-agent.active-generation.v1" || active.release !== RELEASE || active.contract_b_sha256 !== CONTRACT_SHA256 || active.commit_state !== "COMMITTED_LIVE") {
    throw new AagError("CAPABILITY_INCONSISTENT", "Human Identity is not backed by a complete committed activation generation.");
  }
  return active;
}

function validateResponse(value, requestId) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AagError("ENGINE_CRASH", "The local identity worker returned an invalid response.", true);
  if (value.schema_version !== "aag.human-identity.response.v1" || value.request_id !== requestId || value.release !== RELEASE || value.contract_b_sha256 !== CONTRACT_SHA256) {
    throw new AagError("ENGINE_CRASH", "The local identity worker response failed integrity validation.", true);
  }
  return value;
}

async function waitForResponse(requestId, timeoutMs = RESPONSE_TIMEOUT_MS) {
  const target = responseFile(requestId);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { return validateResponse(readJson(target), requestId); }
    catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    await sleep(500);
  }
  throw new AagError("ENGINE_TIMEOUT", "The local identity worker timed out.", true);
}

function failureFromResponse(response) {
  const code = String(response.error_code || "ENGINE_CRASH");
  const safe = new Set([
    "JOB_CANCELLED", "REFERENCE_INVALID_PATH", "REFERENCE_NOT_FOUND", "REFERENCE_PATH_FORBIDDEN",
    "REFERENCE_SYMLINK", "REFERENCE_NOT_REGULAR", "REFERENCE_HARDLINK_AMBIGUOUS",
    "REFERENCE_SIZE_INVALID", "REFERENCE_FORMAT_UNSUPPORTED", "REFERENCE_DECODE_FAILED",
    "REFERENCE_DIMENSIONS_INVALID", "REFERENCE_NO_FACE", "REFERENCE_MULTIPLE_FACES",
    "REFERENCE_UNSUITABLE", "IDENTITY_QUALITY_REJECTED", "OUTPUT_PROMPT_COMPOSITION_REJECTED",
    "OUTPUT_SEVERE_ARTIFACT_SUSPECTED", "OUTPUT_IDENTITY_UNEVALUABLE", "OUTPUT_NO_FACE",
    "OUTPUT_MULTIPLE_FACES", "CONTRACT_INTEGRITY_FAILURE", "IDENTITY_DOMAIN_UNSUPPORTED",
    "REFERENCE_CHANGED", "REQUEST_INVALID", "XPU_LEASE_LOST", "ENGINE_CRASH",
  ]);
  const selected = safe.has(code) ? code : "ENGINE_CRASH";
  const retryable = ["ENGINE_CRASH", "XPU_LEASE_LOST"].includes(selected);
  return new AagError(selected, String(response.message || "The local Human Identity request failed safely.").slice(0, 300), retryable);
}

async function execute(task, normalized, leaseToken, deps = {}) {
  verifyActiveGeneration();
  if (task.count !== 1 || task.width !== undefined || task.height !== undefined || task.aspect_ratio !== "auto") {
    throw new AagError("IDENTITY_CONTRACT_REQUIRED", "Human Identity Production v1 uses one fixed 896x1152 Contract B output.");
  }
  const reference = classifySource(task);
  const requestId = crypto.randomUUID();
  const staged = stageReference(requestId, normalized, task._aag_source, task.owner, STATE_ROOT);
  const message = {
    schema_version: "aag.human-identity.bridge-request.v2",
    request_id: requestId,
    parent_job_id: safeId(task._aag_parent_job_id),
    child_job_id: safeId(task._aag_child_job_id),
    reference_kind: reference.reference_kind,
    fixture_id: reference.fixture_id,
    identity_domain: reference.domain,
    prompt: PROMPTS[reference.domain],
    reference_sha256: task._aag_source.normalized_sha256,
    original_sha256: task._aag_source.original_sha256,
    reference_width: task._aag_source.width,
    reference_height: task._aag_source.height,
    source_index: task._aag_source.index,
    seed: task.seed,
    width: 896,
    height: 1152,
    contract_id: CONTRACT_ID,
    contract_b_sha256: CONTRACT_SHA256,
    release: RELEASE,
    candidate_release: CANDIDATE_RELEASE,
    route: ROUTE,
    lease_token: String(leaseToken || ""),
    // Contract B intentionally freezes the worker-facing caller schema at the
    // four ownership fields. AAG's trusted per-turn ID remains private to task
    // idempotency and must not alter the Human Identity production contract.
    caller: callerScope(task.owner),
    submitted_at: new Date().toISOString(),
  };
  if (!/^[0-9a-f-]{36}$/i.test(message.lease_token)) throw new AagError("XPU_LEASE_LOST", "The delegated XPU lease token is invalid.", true);

  ensureDirectory(path.join(STATE_ROOT, "inbox"));
  ensureDirectory(path.join(STATE_ROOT, "responses"));
  ensureDirectory(path.join(STATE_ROOT, "acks"));
  try { atomicJson(path.join(STATE_ROOT, "inbox", `${requestId}.json`), message); }
  catch (error) { try { fs.unlinkSync(staged); } catch {} try { fs.unlinkSync(stagedProvenanceFile(requestId)); } catch {} throw error; }
  const response = await waitForResponse(requestId);
  if (response.status !== "PASS") throw failureFromResponse(response);
  const filename = String(response.artifact_filename || "");
  if (!/^REF-[0-9a-f-]{36}\.png$/i.test(filename)) throw new AagError("OUTPUT_POLICY_VIOLATION", "The identity worker returned an unsafe artifact name.");
  const evaluation = response.evaluation || {};
  if (evaluation.status !== "PASS" || Number(evaluation.intended_cosine) < 0.5026416499167681 || Number(evaluation.minimum_negative_margin) <= 0) {
    throw new AagError("IDENTITY_QUALITY_REJECTED", "The identity artifact did not pass the locked quality contract.");
  }
  pendingAcknowledgements.set(filename, { requestId, responseFile: responseFile(requestId) });
  deps.onEngineMetadata?.({
    adapter: "local-pulid-v1.1-contract-b-v1",
    prompt_id: requestId,
    completed_at: response.completed_at,
    elapsed_seconds: Number(response.total_latency_seconds || 0),
    model: "Juggernaut XL V9 + PuLID v1.1",
    identity_cosine: Number(evaluation.intended_cosine),
    negative_margin: Number(evaluation.minimum_negative_margin),
    composition_result: String(evaluation.prompt_composition_result || ""),
    blur_result: Number(evaluation.face_blur_variance) >= Number(evaluation.face_blur_variance_floor) ? "PASS" : "FAIL",
    network_result: Number(response.external_network_events || 0) === 0 ? "PASS" : "FAIL",
    cleanup_result: String(response.cleanup_result || ""),
    contract_id: CONTRACT_ID,
  });
  return [filename];
}

function acknowledge(filename, verified, detail = "") {
  const pending = pendingAcknowledgements.get(String(filename || ""));
  if (!pending) return false;
  pendingAcknowledgements.delete(filename);
  atomicJson(ackFile(pending.requestId), {
    schema_version: "aag.human-identity.ack.v1",
    request_id: pending.requestId,
    artifact_filename: filename,
    verified: Boolean(verified),
    detail: String(detail || "").slice(0, 200),
    acknowledged_at: new Date().toISOString(),
  });
  return true;
}

function acknowledgeRecovered(requestId, filename, verified, detail = "") {
  const safeRequestId = safeId(requestId);
  const safeFilename = String(filename || "");
  const response = validateResponse(readJson(responseFile(safeRequestId)), safeRequestId);
  if (
    response.status !== "PASS" ||
    response.artifact_filename !== safeFilename ||
    !/^REF-[0-9a-f-]{36}\.png$/i.test(safeFilename)
  )
    throw new AagError(
      "OUTPUT_POLICY_VIOLATION",
      "Recovered Human Identity acknowledgement does not match its committed response."
    );
  const target = ackFile(safeRequestId);
  try {
    const existing = readJson(target);
    return existing.request_id === safeRequestId &&
      existing.artifact_filename === safeFilename &&
      existing.verified === Boolean(verified);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  atomicJson(target, {
    schema_version: "aag.human-identity.ack.v1",
    request_id: safeRequestId,
    artifact_filename: safeFilename,
    verified: Boolean(verified),
    detail: String(detail || "").slice(0, 200),
    acknowledged_at: new Date().toISOString(),
    recovery: "validated-committed-response",
  });
  return true;
}

module.exports = {
  RELEASE, CONTRACT_SHA256, CONTRACT_ID, ROUTE, PROMPTS, FIXTURES, REFERENCE_KINDS,
  classifySource, contractDomain, stagedReferenceFile, stagedProvenanceFile, stageReference, callerScope, verifyActiveGeneration, validateResponse, waitForResponse, execute, acknowledge, acknowledgeRecovered,
  STATE_ROOT, SHARED_AGENT_ROOT,
};
