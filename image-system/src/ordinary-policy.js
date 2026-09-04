"use strict";

const RECIPE_ID = "ordinary.flux2-klein-4b.balanced-aspect.v1";
const BALANCED = Object.freeze({ square: [640, 640], portrait: [512, 768], landscape: [768, 512] });
const EXPLICIT = Object.freeze({
  "1:1": BALANCED.square,
  "16:9": [768, 448],
  "9:16": [448, 768],
  "4:3": [640, 512],
  "3:2": BALANCED.landscape,
  landscape: BALANCED.landscape,
  portrait: BALANCED.portrait,
});

function cueAspect(prompt) {
  const value = String(prompt || "").toLowerCase();
  if (/\b(landscape framing|landscape orientation|wide framing|wide composition|panoramic)\b/.test(value) || /(?:לרוחב|אופקי(?:ת)?|פנורמ(?:ה|י|ית))/.test(value)) return { aspect: "landscape", source: "exact-prompt-cue" };
  if (/\b(portrait framing|portrait orientation|vertical framing|vertical composition)\b/.test(value) || /(?:לאורך|אנכי(?:ת)?)/.test(value)) return { aspect: "portrait", source: "exact-prompt-cue" };
  if (/\b(full[- ]body|head[- ]to[- ]toe|both feet visible|environmental portrait)\b/.test(value)) return { aspect: "portrait", source: "content-cue" };
  if (/\b(architectural|architecture|interior|kitchen|living room|rice fields?|terraced fields?)\b/.test(value)) return { aspect: "landscape", source: "content-cue" };
  return { aspect: "square", source: "square-default" };
}

function select(task = {}) {
  const quality = ["auto", "fast", "balanced", "quality"].includes(task.quality) ? task.quality : "auto";
  const prompt = task.prompt || task.request || "";
  if (quality === "fast" || quality === "quality") return { recipe_id: RECIPE_ID, policy: "production-0.9.0-preview.3", aspect: task.aspect_ratio || "auto" };
  if (task.width !== undefined || task.height !== undefined) return { recipe_id: RECIPE_ID, policy: "explicit-dimensions", width: task.width, height: task.height, source: "caller" };
  const requested = task.aspect_ratio && task.aspect_ratio !== "auto" ? task.aspect_ratio : null;
  const decision = requested ? { aspect: requested, source: "caller-aspect" } : cueAspect(prompt);
  const dimensions = EXPLICIT[decision.aspect] || BALANCED[decision.aspect] || BALANCED.square;
  return { recipe_id: RECIPE_ID, policy: "balanced-aspect-v1", aspect: decision.aspect, source: decision.source, width: dimensions[0], height: dimensions[1] };
}

function requiredOfflineEnvironment(env = process.env) {
  return ["ORT_DISABLE_TELEMETRY", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"].every(key => env[key] === "1");
}

module.exports = { RECIPE_ID, BALANCED, EXPLICIT, cueAspect, select, requiredOfflineEnvironment };
