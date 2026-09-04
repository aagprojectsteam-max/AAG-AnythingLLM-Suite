"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const COMFY = process.env.AAG_IDENTITY_COMFY_URL || "http://127.0.0.1:8188";
const HUB = process.env.AAG_IDENTITY_HUB_URL || "http://127.0.0.1:18190";
const MODELS = {
  unet: "flux-2-klein-9b-fp8.safetensors",
  clip: "qwen_3_8b_fp8mixed.safetensors",
  vae: "flux2-vae.safetensors",
  pulid: "pulid_flux2_klein_v2.safetensors",
};

function imageKind(bytes) {
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return { mime: "image/jpeg", ext: ".jpg" };
  if (bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return { mime: "image/png", ext: ".png" };
  if (bytes.subarray(0,4).toString() === "RIFF" && bytes.subarray(8,12).toString() === "WEBP") return { mime: "image/webp", ext: ".webp" };
  throw new Error("unsupported source image");
}

async function json(url, options = {}, timeout = 120000) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(timeout) });
  const text = await response.text();
  let body; try { body = JSON.parse(text); } catch { body = text; }
  if (!response.ok) throw new Error(`${response.status} ${typeof body === "string" ? body.slice(0,500) : JSON.stringify(body)}`);
  return body;
}

async function upload(file) {
  const bytes = fs.readFileSync(file);
  if (bytes.length < 128 || bytes.length > 50 * 1024 * 1024) throw new Error("source size out of bounds");
  const kind = imageKind(bytes);
  const name = `h2-identity-${crypto.randomUUID()}${kind.ext}`;
  const form = new FormData();
  form.append("image", new Blob([bytes], { type: kind.mime }), name);
  form.append("type", "input"); form.append("subfolder", "AAG-Hardening2-Identity"); form.append("overwrite", "false");
  const result = await json(`${COMFY}/upload/image`, { method: "POST", body: form });
  return { bytes, sha256: crypto.createHash("sha256").update(bytes).digest("hex"), name: result.name || name, imageInput: path.posix.join(result.subfolder || "AAG-Hardening2-Identity", result.name || name) };
}

function graph(c) {
  const result = {
    "1": { class_type: "UNETLoader", inputs: { unet_name: MODELS.unet, weight_dtype: "default" } },
    "2": { class_type: "CLIPLoader", inputs: { clip_name: MODELS.clip, type: "flux2", device: "default" } },
    "3": { class_type: "VAELoader", inputs: { vae_name: MODELS.vae } },
    "4": { class_type: "LoadImage", inputs: { image: c.referenceName } },
    "5": { class_type: "ImageScaleToTotalPixels", inputs: { image: ["4",0], upscale_method: "area", megapixels: 0.25, resolution_steps: 1 } },
    "6": { class_type: "PuLIDInsightFaceLoader", inputs: { provider: "CPU" } },
    "7": { class_type: "PuLIDEVACLIPLoader", inputs: {} },
    "8": { class_type: "PuLIDModelLoader", inputs: { pulid_file: MODELS.pulid } },
    "9": { class_type: "ApplyPuLIDFlux2", inputs: { model: ["1",0], pulid_model: ["8",0], strength: c.strength, eva_clip: ["7",0], face_analysis: ["6",0], image: ["5",0], face_index: 0, debug_mode: false } },
    "10": { class_type: "VAEEncode", inputs: { pixels: ["5",0], vae: ["3",0] } },
    "11": { class_type: "CLIPTextEncode", inputs: { text: c.prompt, clip: ["2",0] } },
    "12": { class_type: "ReferenceLatent", inputs: { conditioning: ["11",0], latent: ["10",0] } },
    "13": { class_type: "ConditioningZeroOut", inputs: { conditioning: ["12",0] } },
    "14": { class_type: "CFGGuider", inputs: { cfg: 1.0, model: ["9",0], positive: ["12",0], negative: ["13",0] } },
    "15": { class_type: "KSamplerSelect", inputs: { sampler_name: "euler" } },
    "16": { class_type: "Flux2Scheduler", inputs: { steps: c.steps, width: c.width, height: c.height } },
    "17": { class_type: "RandomNoise", inputs: { noise_seed: c.seed } },
    "18": { class_type: "EmptyFlux2LatentImage", inputs: { width: c.width, height: c.height, batch_size: 1 } },
    "19": { class_type: "SamplerCustomAdvanced", inputs: { noise: ["17",0], guider: ["14",0], sampler: ["15",0], sigmas: ["16",0], latent_image: ["18",0] } },
    "20": { class_type: "VAEDecode", inputs: { samples: ["19",0], vae: ["3",0] } },
    "21": { class_type: "SaveImage", inputs: { filename_prefix: c.prefix, images: ["20",0] } },
  };
  if (c.variant === "pulid-only") {
    result["13"].inputs.conditioning = ["11",0];
    result["14"].inputs.positive = ["11",0];
    delete result["10"];
    delete result["12"];
  }
  return result;
}

async function wait(promptId) {
  const start = Date.now();
  while (Date.now() - start < 20 * 60 * 1000) {
    const history = await json(`${COMFY}/history/${encodeURIComponent(promptId)}`, {}, 30000);
    const entry = history[promptId];
    if (entry) {
      if (entry.status?.status_str === "error") throw new Error(`execution failed ${JSON.stringify(entry.status.messages).slice(0,1000)}`);
      const images = Object.values(entry.outputs || {}).flatMap(value => value.images || []);
      if (images.length) return { image: images[0], elapsed_seconds: (Date.now() - start) / 1000 };
      if (entry.status?.completed) throw new Error("completed without image");
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
  throw new Error("identity candidate timeout");
}

async function main() {
  const [source, prompt, seedValue, strengthValue, variantValue] = process.argv.slice(2);
  if (!source || !prompt) throw new Error("usage: identity-candidate.js SOURCE PROMPT [SEED] [STRENGTH]");
  const resolved = fs.realpathSync(source);
  const seed = Number.isSafeInteger(Number(seedValue)) ? Number(seedValue) : 24082601;
  const strength = Number.isFinite(Number(strengthValue)) ? Math.max(0, Math.min(2, Number(strengthValue))) : 1.3;
  const variant = variantValue === "pulid-only" ? "pulid-only" : "pulid-reference-latent-v1";
  const [stats, objectInfo] = await Promise.all([json(`${COMFY}/system_stats`), json(`${COMFY}/object_info`)]);
  for (const node of ["UNETLoader","CLIPLoader","VAELoader","LoadImage","ImageScaleToTotalPixels","PuLIDInsightFaceLoader","PuLIDEVACLIPLoader","PuLIDModelLoader","ApplyPuLIDFlux2","VAEEncode","ReferenceLatent","Flux2Scheduler","SamplerCustomAdvanced","SaveImage"]) if (!objectInfo[node]) throw new Error(`missing node ${node}`);
  const reference = await upload(resolved);
  const prefix = `H2-ID-${new Date().toISOString().replace(/[:.]/g,"-")}-${seed}`;
  const request = graph({ referenceName: reference.imageInput, prompt, strength, seed, steps: 4, width: 512, height: 512, prefix, variant });
  const submission = await json(`${COMFY}/prompt`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: request, client_id: `h2-identity-${crypto.randomUUID()}` }) });
  const result = await wait(submission.prompt_id);
  const imported = await json(`${HUB}/import-comfyui`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: result.image.filename, subfolder: result.image.subfolder || "", type: result.image.type || "output", kind: "REF" }) });
  const artifactUrl = `${HUB}${imported.public_path}`;
  const artifactBytes = Buffer.from(await (await fetch(artifactUrl)).arrayBuffer());
  process.stdout.write(JSON.stringify({ candidate: variant, source: resolved, source_sha256: reference.sha256, prompt, seed, strength, width: 512, height: 512, steps: 4, prompt_id: submission.prompt_id, elapsed_seconds: result.elapsed_seconds, comfy_image: result.image, artifact_filename: imported.filename, artifact_url: artifactUrl, artifact_sha256: crypto.createHash("sha256").update(artifactBytes).digest("hex"), artifact_bytes: artifactBytes.length, system: stats?.system?.os || "local" }, null, 2) + "\n");
}

main().catch(error => { process.stderr.write(`IDENTITY_CANDIDATE_FAILED ${error.stack || error}\n`); process.exitCode = 1; });
