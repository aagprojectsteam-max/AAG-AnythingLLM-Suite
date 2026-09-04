"use strict";

const crypto = require("crypto");
const path = require("path");
const { AagError } = require("./errors");
const ordinaryPolicy = require("./ordinary-policy");

const COMFY = "http://172.18.0.1:18188";
const HUB = "http://172.18.0.1:18190";
const PROFILES = Object.freeze({
  fast: {
    name: "fast", unet: "flux-2-klein-4b-fp8.safetensors", unet_sha256: "97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6",
    clip: "qwen_3_4b.safetensors", clip_sha256: "6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a",
    vae: "flux2-vae.safetensors", vae_sha256: "868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3", steps: 4,
  },
  quality: {
    name: "quality", unet: "flux-2-klein-9b-fp8.safetensors", unet_sha256: "865ba09f5b4c3cbd3468a4bd3acb9fcb2f8740c54317482f0bcd4ed1d3655cee",
    clip: "qwen_3_8b_fp8mixed.safetensors", clip_sha256: "abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6",
    vae: "flux2-vae.safetensors", vae_sha256: "868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3", steps: 4,
  },
});
const COMMON_NODES = ["UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "ConditioningZeroOut", "CFGGuider", "KSamplerSelect", "Flux2Scheduler", "RandomNoise", "EmptyFlux2LatentImage", "SamplerCustomAdvanced", "VAEDecode", "SaveImage"];
const GENERAL_NODES = ["LoadImage", "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent"];
const MAX_ENGINE_REFERENCE_BYTES = 8 * 1024 * 1024;
// These phase limits are deliberately well above observed successful maxima
// (fast 92.238s, quality 120.952s, identity 329.536s, upscale 26.847s).
// Only real engine events reset them; scheduler/process heartbeats never do.
const STALL_PROFILES = Object.freeze({
  fast: Object.freeze({ load: 300_000, sample: 180_000, output: 180_000, default: 300_000 }),
  quality: Object.freeze({ load: 600_000, sample: 360_000, output: 240_000, default: 600_000 }),
  identity: Object.freeze({ load: 600_000, sample: 600_000, output: 300_000, default: 600_000 }),
  upscale: Object.freeze({ load: 180_000, sample: 180_000, output: 180_000, default: 180_000 }),
});
const HISTORY_POLL_MS = 5_000;
const INTERRUPT_GRACE_MS = 45_000;

async function fetchJson(url, options, timeoutMs, leaseToken, deps = {}) {
  const fetchImpl = deps.fetch || fetch;
  const headers = new Headers(options?.headers || {});
  headers.set("X-AAG-Lease-Token", leaseToken);
  let response;
  try { response = await fetchImpl(url, { ...options, headers, signal: AbortSignal.timeout(timeoutMs) }); }
  catch (error) {
    if (error?.name === "AbortError" || error?.name === "TimeoutError") throw new AagError("ENGINE_TIMEOUT", "The local image engine timed out.", true);
    throw error;
  }
  const text = await response.text();
  let body = null; if (text) { try { body = JSON.parse(text); } catch { body = text; } }
  if (!response.ok) throw new AagError("ENGINE_CRASH", "The local image engine rejected the trusted workflow.", true, typeof body === "string" ? body : JSON.stringify(body));
  return body;
}

function productionDimensions(task) {
  const sizes = { "1:1": [512,512], "16:9": [768,448], "9:16": [448,768], "4:3": [640,512], "3:2": [768,512], landscape: [768,512], portrait: [512,768], auto: [512,512] };
  const fallback = sizes[task.aspect_ratio] || sizes.auto;
  let width = Math.min(1024, task.width || fallback[0]), height = Math.min(1024, task.height || fallback[1]);
  const maxPixels = task.quality === "quality" ? 589_824 : 393_216;
  if (width * height > maxPixels) { const scale = Math.sqrt(maxPixels / (width * height)); width *= scale; height *= scale; }
  const round = value => Math.max(256, Math.round(value / 64) * 64);
  return { width: round(width), height: round(height) };
}

function ordinaryDimensions(task) {
  const decision = ordinaryPolicy.select(task);
  if (decision.policy === "production-0.9.0-preview.3") {
    return { ...productionDimensions(task), decision };
  }
  const bounded = productionDimensions({
    ...task,
    width: decision.width === undefined ? task.width : decision.width,
    height: decision.height === undefined ? task.height : decision.height,
    aspect_ratio: decision.aspect || task.aspect_ratio,
  });
  return { ...bounded, decision };
}

function dimensions(task) { return ordinaryDimensions(task); }

function workflowHash(graph) {
  return crypto.createHash("sha256").update(JSON.stringify(graph)).digest("hex");
}

function generationGraph(config) {
  return {
    "1": { class_type: "UNETLoader", inputs: { unet_name: config.model.unet, weight_dtype: "default" } },
    "2": { class_type: "CLIPLoader", inputs: { clip_name: config.model.clip, type: "flux2", device: "default" } },
    "3": { class_type: "VAELoader", inputs: { vae_name: config.model.vae } },
    "4": { class_type: "CLIPTextEncode", inputs: { text: config.prompt, clip: ["2",0] } },
    "5": { class_type: "ConditioningZeroOut", inputs: { conditioning: ["4",0] } },
    "6": { class_type: "CFGGuider", inputs: { cfg: 1.0, model: ["1",0], positive: ["4",0], negative: ["5",0] } },
    "7": { class_type: "KSamplerSelect", inputs: { sampler_name: "euler" } },
    "8": { class_type: "Flux2Scheduler", inputs: { steps: config.model.steps, width: config.width, height: config.height } },
    "9": { class_type: "RandomNoise", inputs: { noise_seed: config.seed } },
    "10": { class_type: "EmptyFlux2LatentImage", inputs: { width: config.width, height: config.height, batch_size: 1 } },
    "11": { class_type: "SamplerCustomAdvanced", inputs: { noise: ["9",0], guider: ["6",0], sampler: ["7",0], sigmas: ["8",0], latent_image: ["10",0] } },
    "12": { class_type: "VAEDecode", inputs: { samples: ["11",0], vae: ["3",0] } },
    "13": { class_type: "SaveImage", inputs: { filename_prefix: config.prefix, images: ["12",0] } },
  };
}

function generalGraph(config) {
  return {
    "1": { class_type: "UNETLoader", inputs: { unet_name: config.model.unet, weight_dtype: "default" } },
    "2": { class_type: "CLIPLoader", inputs: { clip_name: config.model.clip, type: "flux2", device: "default" } },
    "3": { class_type: "VAELoader", inputs: { vae_name: config.model.vae } },
    "4": { class_type: "LoadImage", inputs: { image: config.referenceName } },
    "5": { class_type: "ImageScaleToTotalPixels", inputs: { image: ["4",0], upscale_method: "area", megapixels: config.referenceMegapixels, resolution_steps: 1 } },
    "6": { class_type: "VAEEncode", inputs: { pixels: ["5",0], vae: ["3",0] } },
    "7": { class_type: "CLIPTextEncode", inputs: { text: config.prompt, clip: ["2",0] } },
    "8": { class_type: "ReferenceLatent", inputs: { conditioning: ["7",0], latent: ["6",0] } },
    "9": { class_type: "ConditioningZeroOut", inputs: { conditioning: ["8",0] } },
    "10": { class_type: "CFGGuider", inputs: { cfg: 1.0, model: ["1",0], positive: ["8",0], negative: ["9",0] } },
    "11": { class_type: "KSamplerSelect", inputs: { sampler_name: "euler" } },
    "12": { class_type: "Flux2Scheduler", inputs: { steps: config.model.steps, width: config.width, height: config.height } },
    "13": { class_type: "RandomNoise", inputs: { noise_seed: config.seed } },
    "14": { class_type: "EmptyFlux2LatentImage", inputs: { width: config.width, height: config.height, batch_size: 1 } },
    "15": { class_type: "SamplerCustomAdvanced", inputs: { noise: ["13",0], guider: ["10",0], sampler: ["11",0], sigmas: ["12",0], latent_image: ["14",0] } },
    "16": { class_type: "VAEDecode", inputs: { samples: ["15",0], vae: ["3",0] } },
    "17": { class_type: "SaveImage", inputs: { filename_prefix: config.prefix, images: ["16",0] } },
  };
}

async function verifyRuntime(model, transform, leaseToken, deps) {
  const [nodes, diffusion, encoders, vaes] = await Promise.all([
    fetchJson(`${COMFY}/object_info`, {}, 30_000, leaseToken, deps),
    fetchJson(`${COMFY}/models/diffusion_models`, {}, 20_000, leaseToken, deps),
    fetchJson(`${COMFY}/models/text_encoders`, {}, 20_000, leaseToken, deps),
    fetchJson(`${COMFY}/models/vae`, {}, 20_000, leaseToken, deps),
  ]);
  const required = transform ? [...COMMON_NODES, ...GENERAL_NODES] : COMMON_NODES;
  if (required.some(name => !nodes?.[name]) || !diffusion?.includes(model.unet) || !encoders?.includes(model.clip) || !vaes?.includes(model.vae)) throw new AagError("CAPABILITY_UNAVAILABLE", "The trusted local image workflow is unavailable because a required component is missing.");
}

async function upload(normalized, leaseToken, deps) {
  if (!normalized?.bytes) throw new AagError("SOURCE_REQUIRED", "An authorized normalized source image is required.");
  const name = `aag-source-${crypto.randomUUID()}.png`;
  const form = new FormData();
  form.append("image", new Blob([normalized.bytes], { type: "image/png" }), name);
  form.append("type", "input"); form.append("subfolder", "AAG-Image-Agent-Source"); form.append("overwrite", "false");
  const response = await fetchJson(`${COMFY}/upload/image`, { method: "POST", body: form }, 120_000, leaseToken, deps);
  const filename = path.posix.basename(String(response?.name || name));
  const subfolder = String(response?.subfolder || "AAG-Image-Agent-Source");
  if (!/^[A-Za-z0-9._~-]+$/.test(filename) || !/^[A-Za-z0-9_-]+$/.test(subfolder)) throw new AagError("ENGINE_CRASH", "The image engine returned an unsafe upload path.");
  return path.posix.join(subfolder, filename);
}

function loadSharp() {
  for (const name of ["sharp", "/app/server/node_modules/sharp"]) {
    try { return require(name); } catch {}
  }
  return null;
}

async function prepareGeneralReference(normalized, targetMegapixels, deps = {}) {
  if (!normalized?.bytes || !normalized?.width || !normalized?.height) {
    throw new AagError("SOURCE_REQUIRED", "An authorized normalized source image is required.");
  }
  const megapixels = Math.max(0.01, Math.min(1, Number(targetMegapixels) || 0.5));
  let prepared;
  if (deps.prepareGeneralReference) {
    prepared = await deps.prepareGeneralReference(normalized, megapixels);
  } else {
    const sharp = loadSharp();
    if (!sharp) throw new AagError("SOURCE_NORMALIZATION_FAILED", "The trusted image decoder is unavailable.", true);
    const scale = Math.min(1, Math.sqrt((megapixels * 1_000_000) / (normalized.width * normalized.height)));
    const width = Math.max(64, Math.round(normalized.width * scale));
    const height = Math.max(64, Math.round(normalized.height * scale));
    try {
      const output = await sharp(normalized.bytes, { failOn: "warning", sequentialRead: true })
        .resize({ width, height, fit: "inside", withoutEnlargement: true })
        .png({ compressionLevel: 9, adaptiveFiltering: true })
        .toBuffer({ resolveWithObject: true });
      prepared = { bytes: output.data, width: output.info.width, height: output.info.height, format: "png" };
    } catch (error) {
      throw new AagError("SOURCE_NORMALIZATION_FAILED", "The authorized reference could not be prepared for the local image engine.", false, error?.message);
    }
  }
  if (!Buffer.isBuffer(prepared?.bytes) || prepared.bytes.length < 32 || prepared.bytes.length > MAX_ENGINE_REFERENCE_BYTES) {
    throw new AagError("SOURCE_TOO_LARGE", "The prepared reference exceeds the bounded local-engine input limit.");
  }
  if (!Number.isInteger(prepared.width) || !Number.isInteger(prepared.height) || prepared.width < 1 || prepared.height < 1 || prepared.width * prepared.height > 1_050_000) {
    throw new AagError("SOURCE_NORMALIZATION_FAILED", "The prepared reference dimensions are invalid.");
  }
  return prepared;
}

function imagesFrom(entry) {
  const images = [];
  for (const value of Object.values(entry?.outputs || {})) for (const image of value?.images || []) if (image?.filename) images.push({ filename: String(image.filename), subfolder: String(image.subfolder || ""), type: String(image.type || "output") });
  return images;
}

function clock(deps = {}) { return typeof deps.now === "function" ? Number(deps.now()) : Date.now(); }

function workflowClass(task = {}) {
  if (task.operation === "upscale") return "upscale";
  if (task.preservation === "identity") return "identity";
  return task.quality === "quality" ? "quality" : "fast";
}

function enginePhase(progress = {}) {
  const className = String(progress.current_engine_node_class || "");
  if (/Sampler|KSampler/i.test(className)) return "sample";
  if (/VAEDecode|SaveImage|PreviewImage|Output/i.test(className)) return "output";
  if (/Loader|LoadImage|Scale|Encode|Conditioning|ReferenceLatent|Scheduler|Noise|Latent/i.test(className)) return "load";
  return "default";
}

function noProgressThreshold(task, progress = {}, deps = {}) {
  if (Number.isFinite(Number(deps.noProgressThresholdMs)) && Number(deps.noProgressThresholdMs) > 0) {
    return Number(deps.noProgressThresholdMs);
  }
  const profile = STALL_PROFILES[workflowClass(task)] || STALL_PROFILES.fast;
  return profile[enginePhase(progress)] || profile.default;
}

function queuePromptIds(queue, key) {
  return (queue?.[key] || [])
    .filter(item => Array.isArray(item) && item.length > 1)
    .map(item => String(item[1]));
}

function validEngineProgress(progress, promptId, expectedJobId) {
  return Boolean(
    progress?.ok === true &&
    progress.schema_version === "aag.comfy-engine-progress.v1" &&
    String(progress.prompt_id || "") === String(promptId) &&
    String(progress.job_id || "") === String(expectedJobId || "") &&
    Number.isInteger(Number(progress.sequence)) &&
    Number(progress.sequence) >= 1 &&
    Number.isFinite(Date.parse(String(progress.last_engine_progress_at || "")))
  );
}

function progressFields(progress = {}) {
  const result = {
    engine_progress_source: "comfy-websocket-bridge",
    last_engine_progress_at: String(progress.last_engine_progress_at || ""),
    last_engine_progress_event: String(progress.last_engine_progress_event || "").slice(0, 80),
    current_engine_node: String(progress.current_engine_node || "").slice(0, 100),
    current_engine_node_class: String(progress.current_engine_node_class || "").slice(0, 160),
  };
  for (const key of ["current_engine_step", "current_engine_step_max", "sequence"]) {
    if (Number.isFinite(Number(progress[key]))) result[key] = Number(progress[key]);
  }
  return result;
}

function actionableStallEvidence(progress) {
  return Boolean(
    ["node_started", "sampler_step", "node_completed"].includes(
      String(progress?.last_engine_progress_event || "")
    ) &&
    String(progress?.current_engine_node || "") &&
    String(progress?.current_engine_node_class || "") &&
    !progress?.terminal_event
  );
}

async function optionalEngineProgress(promptId, leaseToken, deps) {
  try {
    return await fetchJson(`${COMFY}/aag/engine-progress/${encodeURIComponent(promptId)}`, {}, 10_000, leaseToken, deps);
  } catch (error) {
    deps.onEngineProgressUnavailable?.(error);
    return null;
  }
}

async function exactPromptRecovery(promptId, leaseToken, deps, context = {}) {
  const sleep = deps.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
  const history = await fetchJson(`${COMFY}/history/${encodeURIComponent(promptId)}`, {}, 30_000, leaseToken, deps);
  if (history?.[promptId]) return { completed: history[promptId] };
  const queue = await fetchJson(`${COMFY}/queue`, {}, 30_000, leaseToken, deps);
  const running = queuePromptIds(queue, "queue_running");
  if (running.length !== 1 || running[0] !== promptId) {
    deps.onEngineProgress?.({
      stall_detected_at: new Date(clock(deps)).toISOString(),
      recovery_action: "INTERRUPT_WITHHELD",
      recovery_outcome: "OWNERSHIP_UNVERIFIED",
    });
    throw new AagError(
      "ENGINE_INTERRUPT_FAILED",
      "Image generation stopped progressing, but exact engine ownership could not be proven. The image engine was not interrupted.",
      false
    );
  }
  try {
    const response = await fetchJson(`${COMFY}/aag/interrupt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt_id: promptId }),
    }, 30_000, leaseToken, deps);
    if (response?.action === "INTERRUPT_WITHHELD_PROGRESS_CHANGED") {
      return { resumed: true };
    }
    if (response?.ok !== true || response?.prompt_id !== promptId) throw new Error("exact interrupt acknowledgement mismatch");
    const stallDetectedAt = new Date(clock(deps)).toISOString();
    deps.onEngineProgress?.({
      stall_detected_at: stallDetectedAt,
      recovery_action: "INTERRUPT_REQUESTED",
      recovery_started_at: stallDetectedAt,
    });
  } catch (error) {
    deps.onEngineProgress?.({ recovery_outcome: "INTERRUPT_REJECTED" });
    throw new AagError(
      "ENGINE_INTERRUPT_FAILED",
      "Image generation stopped progressing and the exact safe interrupt was rejected.",
      false,
      error?.message
    );
  }
  const deadline = clock(deps) + (Number(deps.interruptGraceMs) || INTERRUPT_GRACE_MS);
  while (clock(deps) < deadline) {
    await sleep(Math.min(1_000, Math.max(1, deadline - clock(deps))));
    const [currentHistory, currentQueue] = await Promise.all([
      fetchJson(`${COMFY}/history/${encodeURIComponent(promptId)}`, {}, 30_000, leaseToken, deps),
      fetchJson(`${COMFY}/queue`, {}, 30_000, leaseToken, deps),
    ]);
    if (currentHistory?.[promptId]) {
      const entry = currentHistory[promptId];
      const completedImages = imagesFrom(entry);
      if (entry.status?.completed && completedImages.length === 1) return { completed: entry };
    }
    if (!queuePromptIds(currentQueue, "queue_running").includes(promptId)) {
      const recoveredAt = new Date(clock(deps)).toISOString();
      deps.onEngineProgress?.({
        recovery_action: "INTERRUPT_SUCCEEDED",
        recovery_completed_at: recoveredAt,
        recovery_outcome: "XPU_LANE_RELEASED",
      });
      throw new AagError(
        "ENGINE_STALLED_RECOVERED",
        "Image generation stopped responding. The image engine was safely recovered. You can try again.",
        true
      );
    }
  }
  deps.onEngineProgress?.({
    recovery_action: "SERVICE_RECOVERY_REQUIRED",
    recovery_outcome: "INTERRUPT_DID_NOT_RELEASE_LANE",
  });
  throw new AagError(
    "ENGINE_SERVICE_RECOVERY_REQUIRED",
    "Image generation stopped responding and the image engine requires controlled service recovery before another request.",
    false
  );
}

function imageFromHistoryEntry(entry) {
  if (!entry) return null;
  if (entry.status?.status_str === "error") throw new AagError("ENGINE_CRASH", "The image engine failed during execution.", true, JSON.stringify(entry.status?.messages));
  const images = imagesFrom(entry);
  if (images.length === 1) return images[0];
  if (images.length > 1) throw new AagError("OUTPUT_INVALID", "One child image job produced multiple artifacts.");
  if (entry.status?.completed) throw new AagError("OUTPUT_MISSING", "The image engine completed without a final image.");
  return null;
}

async function waitForImage(promptId, leaseToken, deps = {}, context = {}) {
  const deadline = clock(deps) + 20 * 60 * 1000;
  const sleep = deps.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
  let observedSequence = 0;
  while (clock(deps) < deadline) {
    const history = await fetchJson(`${COMFY}/history/${encodeURIComponent(promptId)}`, {}, 30_000, leaseToken, deps);
    const ready = imageFromHistoryEntry(history?.[promptId]);
    if (ready) return ready;
    const progress = await optionalEngineProgress(promptId, leaseToken, deps);
    if (validEngineProgress(progress, promptId, context.jobId)) {
      if (Number(progress.sequence) > observedSequence) {
        observedSequence = Number(progress.sequence);
        deps.onEngineProgress?.(progressFields(progress));
      }
      const quietFor = clock(deps) - Date.parse(progress.last_engine_progress_at);
      if (
        actionableStallEvidence(progress) &&
        quietFor > noProgressThreshold(context.task || {}, progress, deps)
      ) {
        const recovered = await exactPromptRecovery(promptId, leaseToken, deps, context);
        if (recovered?.resumed) {
          observedSequence = Number(progress.sequence);
          await sleep(Number(deps.historyPollMs) || HISTORY_POLL_MS);
          continue;
        }
        const recoveredImage = imageFromHistoryEntry(recovered?.completed);
        if (recoveredImage) return recoveredImage;
      }
    }
    await sleep(Number(deps.historyPollMs) || HISTORY_POLL_MS);
  }
  throw new AagError("ENGINE_TIMEOUT", "The local image engine timed out.", true);
}

async function importImage(image, kind, leaseToken, deps) {
  const imported = await fetchJson(`${HUB}/import-comfyui`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: image.filename, subfolder: image.subfolder, type: image.type, kind }) }, 120_000, leaseToken, deps);
  if (imported?.ok !== true || !/^[A-Za-z0-9][A-Za-z0-9._~-]{0,239}$/.test(String(imported.filename || ""))) throw new AagError("PUBLISH_FAILED", "The trusted publisher did not accept the image artifact.", true);
  return String(imported.filename);
}

async function execute(task, normalized, leaseToken, deps = {}) {
  const started = Date.now();
  const model = task.quality === "quality" ? PROFILES.quality : PROFILES.fast;
  const transform = task.operation === "transform";
  await verifyRuntime(model, transform, leaseToken, deps);
  const size = transform ? productionDimensions(task) : ordinaryDimensions(task);
  let graph, kind, finalEnginePrompt;
  if (!transform) {
    finalEnginePrompt = task.prompt || task.request;
    graph = generationGraph({ model, prompt: finalEnginePrompt, seed: task.seed, width: size.width, height: size.height, prefix: `GEN-H2-${new Date().toISOString().replace(/[:.]/g,"-")}-${task.seed}` });
    kind = "GEN";
  } else {
    const referenceMegapixels = task.quality === "quality" ? 1.0 : task.quality === "fast" ? 0.25 : 0.5;
    const engineReference = await prepareGeneralReference(normalized, referenceMegapixels, deps);
    const referenceName = await upload(engineReference, leaseToken, deps);
    const base = String(task.prompt || task.request).trim().replace(/[.\s]+$/, "");
    finalEnginePrompt = `${base}. Use the supplied authorized reference as the visual source for the main subject. Preserve the subject's recognizable shape, proportions, colors, markings, materials, texture, design, and distinctive details unless explicitly requested otherwise. Apply the requested action, scene, setting, and composition faithfully.`;
    graph = generalGraph({ model, referenceName, prompt: finalEnginePrompt, seed: task.seed, width: size.width, height: size.height, referenceMegapixels, prefix: `REF-H2-subject-${new Date().toISOString().replace(/[:.]/g,"-")}-${task.seed}` });
    kind = "REF";
  }
  const clientId = `aag-image-${crypto.randomUUID()}`;
  const submission = await fetchJson(`${COMFY}/prompt`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: graph, client_id: clientId }) }, 30_000, leaseToken, deps);
  if (!submission?.prompt_id) throw new AagError("ENGINE_CRASH", "The image engine did not return a job identifier.", true);
  const promptId = String(submission.prompt_id);
  const promptContract = task._aag_prompt_contract || {};
  const promptMetadata = {
    prompt_contract: promptContract.id || "none",
    prompt_author: promptContract.author || "unknown",
    prompt_quality_status: promptContract.status || "UNKNOWN",
    prompt_fidelity_status: promptContract.fidelity_status || "UNKNOWN",
    prompt_structure_status: promptContract.structure_status || "UNKNOWN",
    final_prompt_sha256: crypto.createHash("sha256").update(String(finalEnginePrompt || "")).digest("hex"),
  };
  const metadata = transform ? {
    adapter: "comfy-subject-v1", prompt_id: promptId, submitted_at: new Date().toISOString(), model: model.name, ...promptMetadata,
  } : {
    adapter: "comfy-generation-v2", prompt_id: promptId, submitted_at: new Date().toISOString(), model: model.name,
    recipe_id: ordinaryPolicy.RECIPE_ID,
    model_file: model.unet, model_sha256: model.unet_sha256,
    text_encoder_file: model.clip, text_encoder_sha256: model.clip_sha256,
    vae_file: model.vae, vae_sha256: model.vae_sha256,
    workflow_sha256: workflowHash(graph), sampler: "euler", scheduler: "Flux2Scheduler", steps: model.steps, cfg: 1.0,
    dimensions: `${size.width}x${size.height}`, aspect: size.decision?.aspect || task.aspect_ratio || "auto",
    aspect_source: size.decision?.source || size.decision?.policy || "explicit-profile",
    prompt_policy: "workspace-llm-authored-validation-only-v1", negative_prompt_policy: "ConditioningZeroOut",
    offline_policy: "local-only-zero-external-policy",
    ...promptMetadata,
  };
  deps.onEngineMetadata?.(metadata);
  deps.onEngineProgress?.({
    engine_progress_source: "comfy-websocket-bridge",
    last_engine_progress_at: new Date().toISOString(),
    last_engine_progress_event: "prompt_submitted",
  });
  const filename = await importImage(await waitForImage(promptId, leaseToken, deps, {
    task,
    jobId: task._aag_parent_job_id,
  }), kind, leaseToken, deps);
  deps.onEngineMetadata?.({ completed_at: new Date().toISOString(), elapsed_seconds: (Date.now() - started) / 1000 });
  return [filename];
}

module.exports = {
  COMFY, HUB, PROFILES, COMMON_NODES, GENERAL_NODES, MAX_ENGINE_REFERENCE_BYTES,
  STALL_PROFILES, HISTORY_POLL_MS, INTERRUPT_GRACE_MS,
  fetchJson, dimensions, productionDimensions, ordinaryDimensions, workflowHash,
  generationGraph, generalGraph, verifyRuntime, upload, prepareGeneralReference,
  imagesFrom, workflowClass, enginePhase, noProgressThreshold, queuePromptIds,
  validEngineProgress, progressFields, optionalEngineProgress, exactPromptRecovery,
  actionableStallEvidence,
  imageFromHistoryEntry, waitForImage, importImage, execute,
};
