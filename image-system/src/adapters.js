"use strict";

const crypto = require("crypto");
const { AagError } = require("./errors");
const { inspectOutput } = require("./image");
const identity = require("./identity");
const humanIdentity = require("./human-identity");
const sceneIdentity = require("./scene-identity");
const comfy = require("./comfy");

const HUB_INTERNAL = "http://172.18.0.1:18190";
const HUB_PUBLIC = "http://127.0.0.1:18190";
const UPSCALE_INTERNAL = "http://172.18.0.1:18191";

function publicArtifactUrl(filename) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._~-]{0,239}$/.test(String(filename || "")) || String(filename).includes("..")) {
    throw new AagError("OUTPUT_POLICY_VIOLATION", "The artifact filename is unsafe.");
  }
  return `${HUB_PUBLIC}/files/${encodeURIComponent(filename)}`;
}

function extractCanonicalFilenames(value) {
  const urls = [...String(value || "").matchAll(/[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s)]+/g)].map(match => match[0]);
  if (!urls.length) throw new AagError("OUTPUT_MISSING", "The engine completed without a published artifact.");
  const files = [];
  for (const raw of urls) {
    let parsed;
    try { parsed = new URL(raw); } catch { throw new AagError("OUTPUT_POLICY_VIOLATION", "The adapter returned an invalid artifact URL."); }
    if (parsed.protocol !== "http:" || parsed.username || parsed.password || !["127.0.0.1", "localhost"].includes(parsed.hostname) || parsed.port !== "18190" || parsed.search || parsed.hash) {
      throw new AagError("OUTPUT_POLICY_VIOLATION", "The adapter returned a non-canonical artifact URL.");
    }
    const match = parsed.pathname.match(/^\/files\/([^/]+)$/);
    if (!match) throw new AagError("OUTPUT_POLICY_VIOLATION", "The adapter returned a non-canonical artifact path.");
    let filename;
    try { filename = decodeURIComponent(match[1]); }
    catch { throw new AagError("OUTPUT_POLICY_VIOLATION", "The adapter returned an invalid artifact filename."); }
    publicArtifactUrl(filename);
    files.push(filename);
  }
  return [...new Set(files)];
}

async function verifyArtifact(filename, jobId, childId, source, operation, scale, deps = {}) {
  let verified = false;
  try {
    const fetchImpl = deps.fetch || fetch;
    const response = await fetchImpl(`${HUB_INTERNAL}/files/${encodeURIComponent(filename)}`, { signal: AbortSignal.timeout(15_000) });
    if (!response.ok) throw new AagError("PUBLISH_FAILED", "The trusted publisher could not retrieve the artifact.", true);
    const contentType = String(response.headers?.get?.("content-type") || "").split(";", 1)[0].toLowerCase();
    if (!["image/png", "image/jpeg", "image/webp", ""].includes(contentType)) throw new AagError("OUTPUT_INVALID", "The trusted publisher returned an invalid artifact type.");
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length < 128 || bytes.length > 200 * 1024 * 1024) throw new AagError("OUTPUT_INVALID", "The produced artifact size is invalid.");
    const decoded = await inspectOutput(bytes, deps);
    if (operation === "upscale" && source?.width && source?.height) {
      if (decoded.width !== source.width * scale || decoded.height !== source.height * scale) {
        throw new AagError("OUTPUT_INVALID", "The upscale artifact dimensions do not match the declared scale.");
      }
    }
    verified = true;
    return {
      artifact_id: `artifact-${crypto.randomUUID()}`,
      job_id: jobId,
      child_job_id: childId,
      filename,
      url: publicArtifactUrl(filename),
      mime: contentType || `image/${decoded.format === "jpeg" ? "jpeg" : decoded.format}`,
      bytes: bytes.length,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
      width: decoded.width,
      height: decoded.height,
      verified_at: new Date().toISOString(),
    };
  } finally {
    humanIdentity.acknowledge(filename, verified, verified ? "trusted publisher verification passed" : "trusted publisher verification failed");
    sceneIdentity.acknowledge(filename, verified, verified ? "trusted publisher verification passed" : "trusted publisher verification failed");
  }
}

async function upscale(task, normalized, leaseToken, deps = {}) {
  const fetchImpl = deps.fetch || fetch;
  const response = await fetchImpl(`${UPSCALE_INTERNAL}/upscale`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-AAG-Lease-Token": leaseToken },
    body: JSON.stringify({ image_base64: normalized.bytes.toString("base64"), scale: task.scale, model: "standard", lease_token: leaseToken }),
    signal: AbortSignal.timeout(30 * 60 * 1000),
  });
  let body;
  try { body = await response.json(); } catch { throw new AagError("ENGINE_CRASH", "The local upscale engine returned an invalid response.", true); }
  if (!response.ok || body?.ok !== true) {
    if (response.status === 409) throw new AagError("XPU_RESOURCE_BUSY", "The shared image engine is busy.", true);
    if (response.status === 504) throw new AagError("ENGINE_TIMEOUT", "The local upscale engine timed out.", true);
    throw new AagError("ENGINE_CRASH", "The local upscale engine failed during execution.", true, body?.error);
  }
  const expectedPath = `/files/${encodeURIComponent(String(body.filename || ""))}`;
  if (body.public_path !== expectedPath) throw new AagError("OUTPUT_POLICY_VIOLATION", "The upscale engine returned an unsafe artifact path.");
  publicArtifactUrl(body.filename);
  deps.onEngineMetadata?.({ adapter: "upscale-local-v1", completed_at: new Date().toISOString(), elapsed_seconds: Number(body.elapsed_seconds || 0), model: String(body.model || "standard") });
  return [body.filename];
}

async function finalOutputPostprocess(filename, finalOutputQuality, leaseToken, deps = {}) {
  if (finalOutputQuality === "standard") return null;
  if (finalOutputQuality !== "enhanced_2x") {
    throw new AagError("INVALID_ARGUMENT", "The final output quality is not supported.");
  }
  const fetchImpl = deps.fetch || fetch;
  const response = await fetchImpl(
    `${HUB_INTERNAL}/files/${encodeURIComponent(filename)}`,
    { signal: AbortSignal.timeout(15_000) }
  );
  if (!response.ok)
    throw new AagError(
      "PUBLISH_FAILED",
      "The verified base artifact could not be read for final enhancement.",
      true
    );
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length < 128 || bytes.length > 200 * 1024 * 1024)
    throw new AagError(
      "OUTPUT_INVALID",
      "The verified base artifact is outside final enhancement bounds."
    );
  const outputs = await upscale(
    { scale: 2 },
    { bytes },
    leaseToken,
    deps
  );
  if (!Array.isArray(outputs) || outputs.length !== 1)
    throw new AagError(
      "OUTPUT_INVALID",
      "Final enhancement must produce exactly one artifact."
    );
  return outputs[0];
}

async function execute(task, normalized, runtime, context, leaseToken, deps = {}) {
  if (deps.execute) return deps.execute(task, normalized, runtime, context, leaseToken, deps);
  if (task.operation === "upscale") return upscale(task, normalized, leaseToken, deps);
  if (task.operation === "transform" && task.preservation === "identity") {
    return task._aag_identity_contract === "scene-c" ? sceneIdentity.execute(task, normalized, leaseToken, deps) : humanIdentity.execute(task, normalized, leaseToken, deps);
  }
  return comfy.execute(task, normalized, leaseToken, deps);
}

module.exports = {
  HUB_INTERNAL, HUB_PUBLIC, UPSCALE_INTERNAL, publicArtifactUrl,
  extractCanonicalFilenames, verifyArtifact, upscale, finalOutputPostprocess,
  execute, comfy, identity, humanIdentity, sceneIdentity,
};
