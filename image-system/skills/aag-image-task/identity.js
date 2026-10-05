"use strict";

const crypto = require("crypto");
const path = require("path");
const { AagError } = require("./errors");

const COMFY = "http://172.18.0.1:18188";
const HUB = "http://172.18.0.1:18190";
const MODELS = Object.freeze({
  unet: "flux-2-klein-9b-fp8.safetensors",
  clip: "qwen_3_8b_fp8mixed.safetensors",
  vae: "flux2-vae.safetensors",
  pulid: "pulid_flux2_klein_v2.safetensors",
});
const REQUIRED_NODES = ["UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "ImageScaleToTotalPixels", "PuLIDInsightFaceLoader", "PuLIDEVACLIPLoader", "PuLIDModelLoader", "ApplyPuLIDFlux2", "VAEEncode", "ReferenceLatent", "CLIPTextEncode", "ConditioningZeroOut", "CFGGuider", "KSamplerSelect", "Flux2Scheduler", "RandomNoise", "EmptyFlux2LatentImage", "SamplerCustomAdvanced", "VAEDecode", "SaveImage"];

async function fetchJson(url, options, timeoutMs, leaseToken, deps = {}) {
  const fetchImpl = deps.fetch || fetch;
  const headers = new Headers(options?.headers || {});
  headers.set("X-AAG-Lease-Token", leaseToken);
  let response;
  try { response = await fetchImpl(url, { ...options, headers, signal: AbortSignal.timeout(timeoutMs) }); }
  catch (error) {
    if (error?.name === "AbortError" || error?.name === "TimeoutError") throw new AagError("ENGINE_TIMEOUT", "The identity engine timed out.", true);
    throw error;
  }
  const text = await response.text();
  let body = null;
  if (text) { try { body = JSON.parse(text); } catch { body = text; } }
  if (!response.ok) throw new AagError("ENGINE_CRASH", "The identity engine rejected the trusted workflow.", true, typeof body === "string" ? body : JSON.stringify(body));
  return body;
}

function dimensions(task) {
  const ratios = { "1:1": [512,512], "16:9": [768,448], "9:16": [448,768], "4:3": [640,512], "3:2": [768,512], landscape: [768,512], portrait: [512,768], auto: [512,512] };
  const fallback = ratios[task.aspect_ratio] || ratios.auto;
  let width = task.width || fallback[0], height = task.height || fallback[1];
  const pixels = width * height;
  if (pixels > 589_824) { const scale = Math.sqrt(589_824 / pixels); width *= scale; height *= scale; }
  const round = value => Math.max(256, Math.round(value / 64) * 64);
  return { width: round(width), height: round(height) };
}

function identityPrompt(task) {
  const request = String(task.prompt || task.request || "").trim().replace(/[.\s]+$/, "");
  return `${request}. Preserve the exact recognizable identity, facial structure, proportions, eye shape, nose, mouth, and distinctive facial features of the person in the supplied authorized identity reference. Keep the face unobstructed and naturally detailed. Realistic skin texture, correct anatomy, high-quality image.`;
}

function workflow(config) {
  return {
    "1": { class_type: "UNETLoader", inputs: { unet_name: MODELS.unet, weight_dtype: "default" } },
    "2": { class_type: "CLIPLoader", inputs: { clip_name: MODELS.clip, type: "flux2", device: "default" } },
    "3": { class_type: "VAELoader", inputs: { vae_name: MODELS.vae } },
    "4": { class_type: "LoadImage", inputs: { image: config.referenceName } },
    "5": { class_type: "ImageScaleToTotalPixels", inputs: { image: ["4",0], upscale_method: "area", megapixels: 0.25, resolution_steps: 1 } },
    "6": { class_type: "PuLIDInsightFaceLoader", inputs: { provider: "CPU" } },
    "7": { class_type: "PuLIDEVACLIPLoader", inputs: {} },
    "8": { class_type: "PuLIDModelLoader", inputs: { pulid_file: MODELS.pulid } },
    "9": { class_type: "ApplyPuLIDFlux2", inputs: { model: ["1",0], pulid_model: ["8",0], strength: 1.3, eva_clip: ["7",0], face_analysis: ["6",0], image: ["5",0], face_index: 0, debug_mode: false } },
    "10": { class_type: "VAEEncode", inputs: { pixels: ["5",0], vae: ["3",0] } },
    "11": { class_type: "CLIPTextEncode", inputs: { text: config.prompt, clip: ["2",0] } },
    "12": { class_type: "ReferenceLatent", inputs: { conditioning: ["11",0], latent: ["10",0] } },
    "13": { class_type: "ConditioningZeroOut", inputs: { conditioning: ["12",0] } },
    "14": { class_type: "CFGGuider", inputs: { cfg: 1.0, model: ["9",0], positive: ["12",0], negative: ["13",0] } },
    "15": { class_type: "KSamplerSelect", inputs: { sampler_name: "euler" } },
    "16": { class_type: "Flux2Scheduler", inputs: { steps: 4, width: config.width, height: config.height } },
    "17": { class_type: "RandomNoise", inputs: { noise_seed: config.seed } },
    "18": { class_type: "EmptyFlux2LatentImage", inputs: { width: config.width, height: config.height, batch_size: 1 } },
    "19": { class_type: "SamplerCustomAdvanced", inputs: { noise: ["17",0], guider: ["14",0], sampler: ["15",0], sigmas: ["16",0], latent_image: ["18",0] } },
    "20": { class_type: "VAEDecode", inputs: { samples: ["19",0], vae: ["3",0] } },
    "21": { class_type: "SaveImage", inputs: { filename_prefix: config.prefix, images: ["20",0] } },
  };
}

async function verifyRuntime(leaseToken, deps) {
  const [nodes, diffusion, encoders, vaes] = await Promise.all([
    fetchJson(`${COMFY}/object_info`, {}, 30_000, leaseToken, deps),
    fetchJson(`${COMFY}/models/diffusion_models`, {}, 20_000, leaseToken, deps),
    fetchJson(`${COMFY}/models/text_encoders`, {}, 20_000, leaseToken, deps),
    fetchJson(`${COMFY}/models/vae`, {}, 20_000, leaseToken, deps),
  ]);
  if (REQUIRED_NODES.some(name => !nodes?.[name]) || !diffusion?.includes(MODELS.unet) || !encoders?.includes(MODELS.clip) || !vaes?.includes(MODELS.vae)) {
    throw new AagError("CAPABILITY_UNAVAILABLE", "Human identity preservation is unavailable because its trusted local components are incomplete.");
  }
  const pulid = nodes?.PuLIDModelLoader?.input?.required?.pulid_file?.[0];
  if (!Array.isArray(pulid) || !pulid.includes(MODELS.pulid)) throw new AagError("CAPABILITY_UNAVAILABLE", "Human identity preservation is unavailable because its trusted PuLID model is missing.");
}

async function upload(normalized, leaseToken, deps) {
  const name = `h2-identity-${crypto.randomUUID()}.png`;
  const form = new FormData();
  form.append("image", new Blob([normalized.bytes], { type: "image/png" }), name);
  form.append("type", "input"); form.append("subfolder", "AAG-Image-Agent-Identity"); form.append("overwrite", "false");
  const response = await fetchJson(`${COMFY}/upload/image`, { method: "POST", body: form }, 120_000, leaseToken, deps);
  const filename = path.posix.basename(String(response?.name || name));
  const subfolder = String(response?.subfolder || "AAG-Image-Agent-Identity");
  if (!/^[A-Za-z0-9._~-]+$/.test(filename) || !/^[A-Za-z0-9_-]+$/.test(subfolder)) throw new AagError("ENGINE_CRASH", "The identity engine returned an unsafe upload path.");
  return path.posix.join(subfolder, filename);
}

function resultImage(entry) {
  for (const value of Object.values(entry?.outputs || {})) {
    for (const image of value?.images || []) if (image?.filename) return { filename: String(image.filename), subfolder: String(image.subfolder || ""), type: String(image.type || "output") };
  }
  return null;
}

async function waitForImage(promptId, leaseToken, deps) {
  const deadline = Date.now() + 20 * 60 * 1000;
  while (Date.now() < deadline) {
    const history = await fetchJson(`${COMFY}/history/${encodeURIComponent(promptId)}`, {}, 30_000, leaseToken, deps);
    const entry = history?.[promptId];
    if (entry) {
      if (entry.status?.status_str === "error") throw new AagError("ENGINE_CRASH", "The identity engine failed during execution.", true, JSON.stringify(entry.status?.messages));
      const image = resultImage(entry);
      if (image) return image;
      if (entry.status?.completed) throw new AagError("OUTPUT_MISSING", "The identity engine completed without a final image.");
    }
    await (deps.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms))))(1500);
  }
  throw new AagError("ENGINE_TIMEOUT", "The identity engine timed out.", true);
}

async function execute(task, normalized, leaseToken, deps = {}) {
  const started = Date.now();
  if (!normalized?.bytes) throw new AagError("SOURCE_REQUIRED", "An authorized normalized identity reference is required.");
  await verifyRuntime(leaseToken, deps);
  const referenceName = await upload(normalized, leaseToken, deps);
  const size = dimensions(task);
  const prefix = `REF-H2-identity-${new Date().toISOString().replace(/[:.]/g,"-")}-${task.seed}`;
  const prompt = identityPrompt(task);
  const submission = await fetchJson(`${COMFY}/prompt`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: workflow({ referenceName, prompt, seed: task.seed, width: size.width, height: size.height, prefix }), client_id: `aag-image-identity-${crypto.randomUUID()}` }) }, 30_000, leaseToken, deps);
  if (!submission?.prompt_id) throw new AagError("ENGINE_CRASH", "The identity engine did not return a job identifier.", true);
  const promptId = String(submission.prompt_id);
  deps.onEngineMetadata?.({ adapter: "comfy-human-identity-preview-v1", prompt_id: promptId, submitted_at: new Date().toISOString(), model: "quality+pulid+reference-latent" });
  const image = await waitForImage(promptId, leaseToken, deps);
  const imported = await fetchJson(`${HUB}/import-comfyui`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: image.filename, subfolder: image.subfolder, type: image.type, kind: "REF" }) }, 120_000, leaseToken, deps);
  if (imported?.ok !== true || !/^[A-Za-z0-9][A-Za-z0-9._~-]{0,239}$/.test(String(imported.filename || ""))) throw new AagError("PUBLISH_FAILED", "The trusted publisher did not accept the identity artifact.", true);
  deps.onEngineMetadata?.({ completed_at: new Date().toISOString(), elapsed_seconds: (Date.now() - started) / 1000 });
  return [String(imported.filename)];
}

module.exports = { COMFY, HUB, MODELS, REQUIRED_NODES, dimensions, identityPrompt, workflow, verifyRuntime, upload, resultImage, waitForImage, execute };
