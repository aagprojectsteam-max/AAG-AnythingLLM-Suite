"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const policy = require("../src/ordinary-policy");
const comfy = require("../src/comfy");

test("balanced default is square and prompt is not rewritten", () => {
  const prompt = "A red kettle product photo.";
  assert.deepEqual(policy.select({ quality: "auto", aspect_ratio: "auto", prompt }), {
    recipe_id: policy.RECIPE_ID, policy: "balanced-aspect-v1", aspect: "square", source: "square-default", width: 640, height: 640,
  });
  assert.equal(prompt, "A red kettle product photo.");
});

test("explicit aspect has priority", () => {
  assert.equal(policy.select({ quality: "auto", aspect_ratio: "landscape", prompt: "a portrait" }).width, 768);
  assert.equal(policy.select({ quality: "auto", aspect_ratio: "portrait", prompt: "a wide room" }).height, 768);
});

test("narrow cues deterministically select held-out aspect families", () => {
  assert.equal(policy.select({ quality: "auto", aspect_ratio: "auto", prompt: "A full-body dancer with both feet visible" }).aspect, "portrait");
  assert.equal(policy.select({ quality: "balanced", aspect_ratio: "auto", prompt: "A realistic architectural photograph of a kitchen" }).aspect, "landscape");
  assert.equal(policy.select({ quality: "auto", aspect_ratio: "auto", prompt: "A linocut of terraced rice fields" }).aspect, "landscape");
});

test("Hebrew orientation cues remain deterministic without prompt rewriting", () => {
  assert.equal(policy.select({ quality: "auto", aspect_ratio: "auto", prompt: "תעשה לי תמונה לאורך של אגרטל" }).aspect, "portrait");
  assert.equal(policy.select({ quality: "auto", aspect_ratio: "auto", prompt: "תמונה פנורמית לרוחב של חוף" }).aspect, "landscape");
});

test("fast and quality remain on the protected production dimension policy", () => {
  assert.equal(policy.select({ quality: "fast", aspect_ratio: "auto" }).policy, "production-0.9.0-preview.3");
  assert.equal(policy.select({ quality: "quality", aspect_ratio: "auto" }).policy, "production-0.9.0-preview.3");
});

test("offline environment fails closed without every guard", () => {
  const complete = { ORT_DISABLE_TELEMETRY:"1", HF_HUB_OFFLINE:"1", TRANSFORMERS_OFFLINE:"1", HF_DATASETS_OFFLINE:"1", HF_HUB_DISABLE_TELEMETRY:"1", DO_NOT_TRACK:"1" };
  assert.equal(policy.requiredOfflineEnvironment(complete), true);
  assert.equal(policy.requiredOfflineEnvironment({ ...complete, ORT_DISABLE_TELEMETRY:"0" }), false);
});

test("active ordinary dimensions and graph exactly preserve the frozen recipe", () => {
  assert.deepEqual(comfy.ordinaryDimensions({ quality: "auto", aspect_ratio: "auto", prompt: "A red kettle" }), {
    width: 640, height: 640,
    decision: { recipe_id: policy.RECIPE_ID, policy: "balanced-aspect-v1", aspect: "square", source: "square-default", width: 640, height: 640 },
  });
  assert.equal(comfy.ordinaryDimensions({ quality: "balanced", aspect_ratio: "portrait", prompt: "x" }).height, 768);
  assert.equal(comfy.ordinaryDimensions({ quality: "auto", aspect_ratio: "landscape", prompt: "x" }).width, 768);
  const prompt = "Three precise objects: a blue cup, a brass key, and a red book.";
  const graph = comfy.generationGraph({ model: comfy.PROFILES.fast, prompt, seed: 314159, width: 640, height: 640, prefix: "GEN-test" });
  assert.equal(graph["4"].inputs.text, prompt);
  assert.equal(graph["5"].class_type, "ConditioningZeroOut");
  assert.equal(graph["6"].inputs.cfg, 1.0);
  assert.equal(graph["7"].inputs.sampler_name, "euler");
  assert.equal(graph["8"].class_type, "Flux2Scheduler");
  assert.equal(graph["8"].inputs.steps, 4);
  assert.match(comfy.workflowHash(graph), /^[0-9a-f]{64}$/);
});
