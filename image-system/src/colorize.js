"use strict";
// Black-and-white colorization through the local ComfyUI engine (DDColor).
// This is a restoration operation, not a creative transform: no text prompt
// reaches the engine, the source luminance is preserved, and the output keeps
// the source dimensions. It reuses the bounded ComfyUI helpers from comfy.js
// (upload, submit, wait, publisher import) under the same XPU lease.
const crypto = require("crypto");
const { AagError } = require("./errors");
const comfy = require("./comfy");
const { COMFY } = require("./endpoints");

const WORKFLOW_ID = "colorize.restore.auto.v1";
const WORKFLOW_REVISION = "colorize.ddcolor.v1";
const ROUTE = "comfy-colorize-v1";
const NODE = "DDColor_Colorize";
const REQUIRED_NODES = Object.freeze(["LoadImage", NODE, "SaveImage"]);
// Checkpoint names are the ones the installed node enumerates; the sha256 values
// pin the exact files that passed the isolated XPU acceptance on 2026-10-26.
const PROFILES = Object.freeze({
  fast: Object.freeze({ name: "fast", checkpoint: "ddcolor_paper_tiny.pth", checkpoint_sha256: "81dd643904f4664c3718", input_size: 512, encoder: "convnext-t" }),
  quality: Object.freeze({ name: "quality", checkpoint: "ddcolor_modelscope.pth", checkpoint_sha256: "17c460d7e55b32a59837", input_size: 1024, encoder: "convnext-l" }),
});
// The engine keeps the full-resolution source luminance; the network itself
// runs at input_size. A bounded upper limit keeps shared-GPU memory predictable.
const MAX_SOURCE_MEGAPIXELS = 4.0;

function profileFor(task = {}) {
  return task.quality === "quality" ? PROFILES.quality : PROFILES.fast;
}

function graph(config) {
  return {
    "1": { class_type: "LoadImage", inputs: { image: config.sourceName } },
    "2": { class_type: NODE, inputs: { image: ["1", 0], model_input_size: config.profile.input_size, checkpoint: config.profile.checkpoint } },
    "3": { class_type: "SaveImage", inputs: { filename_prefix: config.prefix, images: ["2", 0] } },
  };
}

async function verifyRuntime(profile, leaseToken, deps = {}) {
  const nodes = await comfy.fetchJson(`${COMFY}/object_info`, {}, 30_000, leaseToken, deps);
  if (REQUIRED_NODES.some(name => !nodes?.[name])) {
    throw new AagError("CAPABILITY_UNAVAILABLE", "The trusted local colorization workflow is unavailable because a required component is missing.");
  }
  const checkpoints = nodes[NODE]?.input?.required?.checkpoint?.[0];
  if (!Array.isArray(checkpoints) || !checkpoints.includes(profile.checkpoint)) {
    throw new AagError("CAPABILITY_UNAVAILABLE", "The trusted local colorization workflow is unavailable because the required checkpoint is not registered.");
  }
}

async function prepareSource(normalized, deps = {}) {
  if (!normalized?.bytes || !normalized?.width || !normalized?.height) {
    throw new AagError("SOURCE_REQUIRED", "An authorized normalized source image is required.");
  }
  const pixels = normalized.width * normalized.height;
  if (pixels <= MAX_SOURCE_MEGAPIXELS * 1_000_000 && normalized.bytes.length <= comfy.MAX_ENGINE_REFERENCE_BYTES) return normalized;
  // Only oversized sources are downscaled, and only to the bounded limit.
  return comfy.prepareGeneralReference(normalized, MAX_SOURCE_MEGAPIXELS, deps);
}

async function execute(task, normalized, leaseToken, deps = {}) {
  const started = Date.now();
  const profile = profileFor(task);
  await verifyRuntime(profile, leaseToken, deps);
  const source = await prepareSource(normalized, deps);
  const sourceName = await comfy.upload(source, leaseToken, deps);
  const prefix = `COLOR-H2-${new Date().toISOString().replace(/[:.]/g, "-")}-${profile.name}`;
  const built = graph({ sourceName, profile, prefix });
  // No prompt reaches the engine; the disclosure boundary records that explicitly.
  deps.onFinalPrompt?.({ prompt: "", boundary: "colorize.no-prompt" });
  const clientId = `aag-image-${crypto.randomUUID()}`;
  const submission = await comfy.fetchJson(`${COMFY}/prompt`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: built, client_id: clientId }) }, 30_000, leaseToken, deps);
  if (!submission?.prompt_id) throw new AagError("ENGINE_CRASH", "The image engine did not return a job identifier.", true);
  const promptId = String(submission.prompt_id);
  deps.onEngineMetadata?.({
    adapter: ROUTE, prompt_id: promptId, submitted_at: new Date().toISOString(), model: profile.name,
    model_file: profile.checkpoint, model_sha256_prefix: profile.checkpoint_sha256,
    workflow_id: WORKFLOW_ID, workflow_sha256: comfy.workflowHash(built), model_input_size: profile.input_size,
    source_dimensions: `${source.width}x${source.height}`,
    prompt_policy: "none-restoration-only", offline_policy: "local-only-zero-external-policy",
  });
  deps.onExecutionDetails?.({
    engine: "DDColor / PyTorch XPU (ComfyUI)", image_model: profile.checkpoint, model_family: "DDColor",
    resolved_quality: profile.name, workflow_revision: WORKFLOW_REVISION, route: ROUTE,
    model_input_size: profile.input_size,
  });
  deps.onProcessingStage?.({ stage: "colorize", model: profile.checkpoint, input_dimensions: `${source.width} × ${source.height}` });
  deps.onEngineProgress?.({ engine_progress_source: "comfy-websocket-bridge", last_engine_progress_at: new Date().toISOString(), last_engine_progress_event: "prompt_submitted" });
  const image = await comfy.waitForImage(promptId, leaseToken, deps, { task, jobId: task._aag_parent_job_id });
  // The trusted publisher accepts GEN/REF kinds only; a colorized source is a reference-derived artifact.
  const filename = await comfy.importImage(image, "REF", leaseToken, deps);
  deps.onEngineMetadata?.({ completed_at: new Date().toISOString(), elapsed_seconds: (Date.now() - started) / 1000 });
  return [filename];
}

module.exports = { WORKFLOW_ID, WORKFLOW_REVISION, ROUTE, NODE, REQUIRED_NODES, PROFILES, MAX_SOURCE_MEGAPIXELS, profileFor, graph, verifyRuntime, prepareSource, execute };
