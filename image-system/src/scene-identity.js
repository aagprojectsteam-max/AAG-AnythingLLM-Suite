"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError } = require("./errors");
const { atomicJson, ensureDirectory, readJson, sha256, sleep } = require("./util");
const portraitIdentity = require("./human-identity");

const RELEASE = "0.9.0-preview.13";
// Contract C is independently frozen at its validated implementation release.
// Agent integration releases must not rewrite or impersonate that contract tag.
const CONTRACT_RELEASE = "0.9.0-preview.5";
const CONTRACT_ID = "structured-scene-c";
const CONTRACT_SHA256 = "09c8869e0f9d7099ee4a8b2bce6c8c041e449becb5924240a950352a14b18de6";
const ROUTE = "pulid-v1.1-juggernaut-xl-v9-single-original-scene";
const ADAPTER_ID = "local-pulid-v1.1-contract-c-v1";
const STATE_ROOT = "/app/server/storage/aag-human-identity-scene-state";
const SHARED_AGENT_ROOT = "/app/server/storage/aag-image-agent-state";
const ACTIVE_GENERATION = path.join(SHARED_AGENT_ROOT, "scene-active-generation.json");
const RESPONSE_TIMEOUT_MS = 45 * 60 * 1000;
const pendingAcknowledgements = new Map();

function safeId(value) {
  const output = String(value || "");
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(output) || output.includes("..")) throw new AagError("INTERNAL_ERROR", "The local Scene Identity request identifier is invalid.");
  return output;
}

function responseFile(requestId) { return path.join(STATE_ROOT, "responses", `${safeId(requestId)}.json`); }
function ackFile(requestId) { return path.join(STATE_ROOT, "acks", `${safeId(requestId)}.json`); }

function words(value, maximum) {
  return String(value || "").replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().split(" ").filter(Boolean).slice(0, maximum).join(" ");
}

function scenePrompt(task) {
  const combined = `${task.request || ""} ${task.prompt || ""}`;
  const baby = /(?:\bbaby\b|\btoddler\b|תינוק|תינוקת)/iu.test(combined);
  const girl = /(?:\bgirl\b|ילדה)/iu.test(combined);
  const boy = /(?:\bboy\b|ילד)/iu.test(combined);
  const child = baby || girl || boy || /(?:\bchild\b|\bkid\b)/iu.test(combined);
  const camel = /(?:\bcamel\b|גמל)/iu.test(combined);
  // Prefer the provider's visual-language prompt when present. Raw non-Latin
  // user text can consume the CLIP token budget before the locked identity and
  // composition clauses; request remains the trusted fallback and job record.
  const primary = words(task.prompt || task.request, camel ? 28 : 32);
  if (!primary) throw new AagError("SCENE_IDENTITY_PROMPT_INVALID", "Scene Identity requires a bounded scene or action description.");
  const subject = baby ? "toddler" : girl ? "young girl" : boy ? "young boy" : child ? "young child" : "person";
  if (camel && girl && /(?:\byoung girl\b|\btoddler girl\b)/iu.test(combined)) {
    return "Realistic photograph, exactly one toddler girl with the same recognizable face and young facial proportions as the authorized reference, visibly riding exactly one friendly camel, the camel's complete head, neck and torso entirely inside the frame with margin, coherent saddle contact, warm desert dunes, medium-wide landscape, unobstructed detailed face, no other people or camels.";
  }
  const clauses = [
    "Realistic photograph.", primary,
    `Exactly one primary ${subject} with the same recognizable face and age as the authorized reference.`,
  ];
  if (camel) clauses.push(`Exactly one friendly camel; its complete head, neck and torso inside frame with margin; the ${subject} visibly riding with coherent saddle contact; warm desert visible; medium-wide landscape composition; unobstructed detailed face; no other people or camels.`);
  else clauses.push("Requested action, environment and important object clearly visible; medium-wide composition with the primary person prominent; coherent pose and physical contact; unobstructed evaluable face; no other people.");
  const prompt = words(clauses.join(" "), 70);
  if (prompt.length < 20 || prompt.length > 700) throw new AagError("SCENE_IDENTITY_PROMPT_INVALID", "The normalized scene prompt is outside the bounded Scene Identity prompt envelope.");
  return prompt;
}

function verifyActiveGeneration() {
  const active = readJson(ACTIVE_GENERATION);
  if (active.schema_version !== "aag.image-agent.scene-active-generation.v1" || active.release !== RELEASE || active.scene_contract_sha256 !== CONTRACT_SHA256 || active.contract_b_sha256 !== portraitIdentity.CONTRACT_SHA256 || active.commit_state !== "COMMITTED_LIVE") {
    throw new AagError("CAPABILITY_INCONSISTENT", "Scene Identity is not backed by a complete committed activation generation.");
  }
  return active;
}

function validateResponse(value, requestId) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AagError("ENGINE_CRASH", "The local Scene Identity worker returned an invalid response.", true);
  if (value.schema_version !== "aag.human-identity.scene.response.v1" || value.request_id !== requestId || value.release !== CONTRACT_RELEASE || value.scene_contract_sha256 !== CONTRACT_SHA256 || value.contract_id !== CONTRACT_ID) {
    throw new AagError("ENGINE_CRASH", "The local Scene Identity worker response failed integrity validation.", true);
  }
  return value;
}

async function waitForResponse(requestId, timeoutMs = RESPONSE_TIMEOUT_MS) {
  const target = responseFile(requestId);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { return validateResponse(readJson(target), requestId); }
    catch (error) { if (error?.code !== "ENOENT") throw error; }
    await sleep(500);
  }
  throw new AagError("ENGINE_TIMEOUT", "The local Scene Identity worker timed out.", true);
}

function failureFromResponse(response) {
  const safe = new Set([
    "JOB_CANCELLED", "REFERENCE_NOT_FOUND", "REFERENCE_NOT_REGULAR", "REFERENCE_CHANGED",
    "REFERENCE_SIZE_INVALID", "REFERENCE_FORMAT_UNSUPPORTED", "REFERENCE_DECODE_FAILED",
    "REFERENCE_DIMENSIONS_INVALID", "REFERENCE_NO_FACE", "REFERENCE_MULTIPLE_FACES", "REFERENCE_UNSUITABLE",
    "SOURCE_UNAUTHORIZED", "REQUEST_INVALID", "XPU_LEASE_LOST", "ENGINE_CRASH",
    "SCENE_IDENTITY_CONTRACT_INTEGRITY_FAILURE", "SCENE_IDENTITY_PROMPT_INVALID",
    "SCENE_IDENTITY_PROMPT_INTEGRITY_FAILURE", "SCENE_IDENTITY_FRAMING_UNSUPPORTED",
    "SCENE_IDENTITY_SEED_INVALID", "SCENE_IDENTITY_OUTPUT_DIMENSIONS_INVALID",
    "SCENE_IDENTITY_REFERENCE_DRIFT", "SCENE_IDENTITY_OUTPUT_FACE_COUNT_INVALID",
    "SCENE_IDENTITY_QUALITY_REJECTED", "SCENE_IDENTITY_FACE_UNEVALUABLE",
  ]);
  const code = safe.has(String(response.error_code)) ? String(response.error_code) : "ENGINE_CRASH";
  return new AagError(code, String(response.message || "The local Scene Identity request failed safely.").slice(0, 300), ["ENGINE_CRASH", "XPU_LEASE_LOST"].includes(code));
}

async function execute(task, normalized, leaseToken, deps = {}) {
  verifyActiveGeneration();
  if (task._aag_identity_contract !== "scene-c" || !["scene-c-landscape", "scene-c-portrait"].includes(task._aag_identity_profile)) throw new AagError("IDENTITY_ROUTE_UNRESOLVED", "The task did not select a bounded Scene Identity profile.");
  const reference = portraitIdentity.classifySource(task);
  const requestId = crypto.randomUUID();
  const staged = portraitIdentity.stageReference(requestId, normalized, task._aag_source, task.owner, STATE_ROOT);
  const prompt = scenePrompt(task);
  const promptSha256 = sha256(prompt);
  const message = {
    schema_version: "aag.human-identity.scene.bridge-request.v1",
    request_id: requestId,
    parent_job_id: safeId(task._aag_parent_job_id), child_job_id: safeId(task._aag_child_job_id),
    reference_kind: reference.reference_kind, fixture_id: reference.fixture_id, identity_domain: reference.domain,
    prompt, prompt_sha256: promptSha256,
    reference_sha256: task._aag_source.normalized_sha256, original_sha256: task._aag_source.original_sha256,
    reference_width: task._aag_source.width, reference_height: task._aag_source.height, source_index: task._aag_source.index,
    seed: task.seed, width: task._aag_internal_width, height: task._aag_internal_height,
    contract_id: CONTRACT_ID, scene_contract_sha256: CONTRACT_SHA256, scene_profile: task._aag_identity_profile,
    release: CONTRACT_RELEASE, route: ROUTE, lease_token: String(leaseToken || ""),
    caller: portraitIdentity.callerScope(task.owner), submitted_at: new Date().toISOString(),
  };
  if (!/^[0-9a-f-]{36}$/i.test(message.lease_token)) throw new AagError("XPU_LEASE_LOST", "The delegated XPU lease token is invalid.", true);
  for (const directory of ["inbox", "responses", "acks"]) ensureDirectory(path.join(STATE_ROOT, directory));
  try { atomicJson(path.join(STATE_ROOT, "inbox", `${requestId}.json`), message); }
  catch (error) {
    try { fs.unlinkSync(staged); } catch {}
    try { fs.unlinkSync(portraitIdentity.stagedProvenanceFile(requestId, STATE_ROOT)); } catch {}
    throw error;
  }
  const response = await waitForResponse(requestId);
  if (response.status !== "PASS") throw failureFromResponse(response);
  const filename = String(response.artifact_filename || "");
  if (!/^REF-[0-9a-f-]{36}\.png$/i.test(filename)) throw new AagError("OUTPUT_POLICY_VIOLATION", "The Scene Identity worker returned an unsafe artifact name.");
  const evaluation = response.evaluation || {};
  if (evaluation.status !== "PASS" || Number(evaluation.intended_cosine) < 0.55 || Number(evaluation.minimum_negative_margin) <= 0 || evaluation.face_evaluable !== true) {
    throw new AagError("SCENE_IDENTITY_QUALITY_REJECTED", "The scene artifact did not pass the locked identity and structural contract.");
  }
  pendingAcknowledgements.set(filename, { requestId });
  deps.onEngineMetadata?.({
    adapter: ADAPTER_ID, prompt_id: requestId, completed_at: response.completed_at,
    elapsed_seconds: Number(response.total_latency_seconds || 0), model: "Juggernaut XL V9 + PuLID v1.1",
    identity_cosine: Number(evaluation.intended_cosine), negative_margin: Number(evaluation.minimum_negative_margin),
    composition_result: String(evaluation.structural_scene_result || ""), blur_result: Number(evaluation.face_blur_variance) >= Number(evaluation.face_blur_variance_floor) ? "PASS" : "FAIL",
    network_result: Number(response.external_network_events || 0) === 0 ? "PASS" : "FAIL", cleanup_result: String(response.cleanup_result || ""),
    contract_id: CONTRACT_ID, scene_profile: task._aag_identity_profile, contract_sha256: CONTRACT_SHA256,
    prompt_sha256: promptSha256, dimensions: `${task._aag_internal_width}x${task._aag_internal_height}`,
    prompt_contract: task._aag_prompt_contract?.id || "none",
    prompt_enrichment_strategy: task._aag_prompt_contract?.strategy || "none",
    prompt_completeness_score: Number(task._aag_prompt_contract?.final_completeness_score || 0),
    prompt_fidelity_status: task._aag_prompt_contract?.fidelity_status || "UNKNOWN",
    prompt_structure_status: task._aag_prompt_contract?.structure_status || "UNKNOWN",
    final_prompt_sha256: promptSha256,
  });
  return [filename];
}

function acknowledge(filename, verified, detail = "") {
  const pending = pendingAcknowledgements.get(String(filename || ""));
  if (!pending) return false;
  pendingAcknowledgements.delete(filename);
  atomicJson(ackFile(pending.requestId), { schema_version: "aag.human-identity.scene.ack.v1", request_id: pending.requestId, artifact_filename: filename, verified: Boolean(verified), detail: String(detail).slice(0, 200), acknowledged_at: new Date().toISOString() });
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
      "Recovered Scene Identity acknowledgement does not match its committed response."
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
    schema_version: "aag.human-identity.scene.ack.v1",
    request_id: safeRequestId,
    artifact_filename: safeFilename,
    verified: Boolean(verified),
    detail: String(detail).slice(0, 200),
    acknowledged_at: new Date().toISOString(),
    recovery: "validated-committed-response",
  });
  return true;
}

module.exports = { RELEASE, CONTRACT_RELEASE, CONTRACT_ID, CONTRACT_SHA256, ROUTE, ADAPTER_ID, STATE_ROOT, scenePrompt, verifyActiveGeneration, validateResponse, waitForResponse, execute, acknowledge, acknowledgeRecovered };
