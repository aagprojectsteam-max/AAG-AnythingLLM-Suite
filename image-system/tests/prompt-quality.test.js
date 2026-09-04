"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const quality = require("../src/prompt-quality");

const authoritative = "תעשה לי תמונה מצויירת של חתול ועכבר בזירת אגרוף";
const weak = "A hand-drawn illustration of a cat and a mouse engaged in a boxing match. Dynamic action, cartoon style.";
const rich = "A polished cartoon illustration featuring exactly one expressive cat and one agile mouse actively boxing one another inside a clearly recognizable boxing ring. Show both characters clearly in coherent fighting stances with boxing gloves, believable balance and readable interaction. Include ring ropes and canvas, a purposeful arena background, attractive scene-appropriate lighting, clear subject separation, expressive faces, consistent cartoon rendering, balanced composition and polished visual detail.";
const detailed = "A dynamic and whimsical cartoon illustration of a humorous boxing match between an expressive cat and a clever little mouse. In the center of a brightly lit vintage boxing ring, the determined cat wearing blue boxing shorts and matching boxing gloves squares off against a tiny, agile mouse wearing red boxing shorts and oversized red boxing gloves in a classic fighter stance. Vibrant stadium arena lighting, clean character design, lively animated movie style, detailed boxing ropes and canvas floor, playful atmosphere, balanced composition, rich colors and expressive faces.";

test("weak caption-like proposal is classified under-specified, preserved, and not rejected", () => {
  const dimensions = quality.qualityDimensions(weak, authoritative);
  assert.equal(dimensions.status, "UNDER_SPECIFIED");
  assert.ok(dimensions.missing_dimensions.includes("environment"));
  assert.ok(dimensions.missing_dimensions.includes("composition"));
  assert.ok(dimensions.missing_dimensions.includes("physical_spatial_coherence"));
  const result = quality.validate({ authoritative, proposal: weak });
  assert.equal(result.prompt, weak);
  assert.equal(result.contract.status, "UNDER_SPECIFIED");
  assert.equal(result.contract.structure_status, "UNDER_SPECIFIED");
});

test("detailed cloud-style prompt passes byte-preserved after caller trimming", () => {
  const result = quality.validate({ authoritative, proposal: detailed });
  assert.equal(result.prompt, detailed);
  assert.equal(result.contract.author, "workspace-llm");
  assert.equal(result.contract.strategy, "preserved-llm-authored-prompt");
  assert.equal(result.contract.status, "PRODUCTION_READY");
});

test("rewritten rich local prompt passes unchanged", () => {
  const result = quality.validate({ authoritative, proposal: rich });
  assert.equal(result.prompt, rich);
  assert.equal(result.contract.fidelity_status, "PASS");
  assert.equal(result.contract.structure_status, "PASS");
});

test("same-language semantic drift, count drift, and style drift fail closed", () => {
  const source = "A photorealistic scene of exactly two red bicycles beside a stone wall in a countryside garden";
  const drift = "A polished cartoon illustration of one blue dog running inside a city arena, with balanced composition, believable anatomy, detailed background, strong interaction, coherent perspective, professional visual detail, expressive pose, clean subject separation, realistic lighting and refined shadows across the complete scene.";
  assert.throws(() => quality.validate({ authoritative: source, proposal: drift }), error => error.code === "PROMPT_SEMANTIC_DRIFT");
});

test("structured aspect ratio and dimensions are not misclassified as creative subject counts", () => {
  const source = "Create exactly two yellow mugs on a wooden table. Prioritize speed and use a 16:9 aspect ratio at 768x448 pixels.";
  const prompt = "Exactly two yellow mugs on a wooden table.";
  assert.deepEqual(quality.quantities(source), ["2"]);
  const result = quality.validate({ authoritative: source, proposal: prompt });
  assert.equal(result.prompt, prompt);
  assert.equal(result.contract.fidelity_checks.quantities_preserved, true);
});

test("cartoon, photorealistic, and coloring-page modes remain unchanged", () => {
  const photo = "A polished photorealistic photographic scene of one red bicycle standing beside a weathered stone wall in a quiet countryside setting. Use balanced composition with clear subject separation, believable spatial perspective and natural proportions. Show refined material detail, coherent contact with the ground, realistic daylight, controlled shadows, an uncluttered background and professional visual clarity throughout the complete image.";
  const coloring = "A clean printable coloring-page line-art illustration of exactly one friendly elephant holding one balloon beside its raised trunk. Use a balanced full-page composition on a pure white background, clear interaction and believable contact, coherent anatomy and proportions, uncluttered spatial placement, bold closed outlines, simple readable environment details and polished professional visual clarity suitable for children to color.";
  for (const [source, prompt, expected] of [
    [authoritative, rich, "illustration"],
    ["Create a photorealistic red bicycle beside a stone wall", photo, "photorealistic"],
    ["Create a printable coloring page of one elephant holding one balloon", coloring, "coloring-page"],
  ]) {
    const result = quality.validate({ authoritative: source, proposal: prompt });
    assert.equal(quality.mode(result.prompt), expected);
    assert.equal(result.prompt, prompt);
  }
});

test("identity scene requires and preserves explicit same-person identity language", () => {
  const source = "Create the same girl riding one camel in the desert";
  const prompt = "Create a polished realistic scene of the same recognizable girl actively riding one camel through a broad desert environment. Keep her identity, facial proportions and age consistent with the authorized reference while showing a believable riding interaction, coherent seated balance and physical contact. Use a medium-wide landscape composition, natural perspective, detailed dunes, scene-appropriate sunlight, grounded shadows, clear subject separation and refined professional visual detail.";
  const result = quality.validate({ authoritative: source, proposal: prompt, identityScene: true });
  assert.equal(result.prompt, prompt);
  assert.equal(result.contract.fidelity_checks.identity_requirement_preserved, true);
});

test("quality gate contains no prompt writer, provider branch, template, or prose append path", () => {
  const source = fs.readFileSync(path.join(__dirname, "../src/prompt-quality.js"), "utf8");
  assert.doesNotMatch(source, /(?:gemini|gemma|openai|anthropic|claude|qwen)|(?:provider|model)\s*(?:===|==|includes\s*\()/i);
  assert.doesNotMatch(source, /boxingPrompt|genericEnrichment|basePrompt|canonical-scene-template|deterministic-structural-constraints|\.replace\([^\n]+requested setting/i);
});

module.exports = { authoritative, weak, rich, detailed };
