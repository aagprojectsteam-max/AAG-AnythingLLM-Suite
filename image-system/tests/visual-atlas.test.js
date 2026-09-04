"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const atlas = require("../src/visual-atlas");
const selectiveKnowledge = require("../src/selective-knowledge");

const projectRoot = path.resolve(__dirname, "../..");
const atlasRoot = path.join(projectRoot, "visual-atlas");

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function selected(request) {
  return atlas.select(request).selections.map(
    item => `${item.family_id}/${item.subfamily_id}`
  );
}

test("completed Atlas remains an exact 493-entry taxonomy/manifest/asset bijection", () => {
  const data = atlas.load();
  assert.equal(data.taxonomy.families.length, 28);
  assert.equal(data.entries.size, 493);
  assert.equal(
    data.taxonomy.families.reduce(
      (total, family) => total + family.subfamilies.length,
      0
    ),
    493
  );
  assert.equal(data.manifest.taxonomy_sha256, data.taxonomySha256);
  for (const entry of data.manifest.entries) {
    const preview = path.join(atlasRoot, entry.output_path);
    const thumbnail = path.join(atlasRoot, entry.thumbnail_path);
    assert.equal(entry.status, "COMPLETED");
    assert.equal(sha256(preview), entry.sha256);
    assert.ok(fs.statSync(thumbnail).size > 0);
    assert.ok(fs.statSync(thumbnail).size < fs.statSync(preview).size);
  }
});

test("representative style language selects relevant families and subfamilies deterministically", () => {
  const cases = new Map([
    ["an old Jerusalem street in watercolor", "fine-art-traditional-media/watercolor"],
    ["a clean technical diagram", "infographic-educational/technical-explainer"],
    ["a children's book illustration", "illustration/childrens-book"],
    ["a cinematic realistic photograph", "photography/cinematic"],
    ["a vintage travel poster", "retro-vintage/vintage-travel-poster"],
    ["a simple coloring page", "coloring-page-line-art/simple"],
    ["editorial illustration of a market", "illustration/editorial"],
    ["pencil drawing of an old doorway", "fine-art-traditional-media/graphite"],
    ["an illustrated map of the old city", "map-spatial-illustration/illustrated-map"],
    ["anime-like generic city scene", "anime-manga/anime-inspired"],
    ["a futuristic sci-fi style skyline", "sci-fi-futuristic/general"],
    ["minimalist", "minimal-clean/minimal-illustration"],
    ["surreal", "surreal-dreamlike/surreal-illustration"],
    ["איור ספר ילדים של רחוב בירושלים", "illustration/childrens-book"],
    ["תרשים טכני נקי", "infographic-educational/technical-explainer"],
  ]);
  for (const [request, expected] of cases) {
    const first = atlas.select(request);
    const second = atlas.select(request);
    assert.equal(first.used, true, request);
    assert.equal(selected(request)[0], expected, request);
    assert.deepEqual(first, second, request);
    assert.ok(first.selections.length <= 2);
  }
});

test("ordinary subject requests and operational controls receive no Atlas context", () => {
  for (const request of [
    "a portrait-like scene of ordinary people at a bus stop",
    "a landscape with a river",
    "an old Jerusalem street",
    "a product on a table",
    "a stone building",
    "status of my last image",
    "cancel the last image",
  ]) {
    const decision = atlas.select(request);
    assert.equal(decision.used, false, request);
    assert.equal(decision.selections.length, 0);
  }
  assert.equal(atlas.select("watercolor", { operation: "upscale" }).used, false);
  assert.equal(
    atlas.select("watercolor", {
      operation: "transform",
      preservation: "identity",
    }).reason,
    "identity_reference_protected"
  );
});

test("manual taxonomy and visual browsing are authoritative over AUTO", () => {
  for (const mode of ["manual_taxonomy", "manual_browse"]) {
    const decision = atlas.select("make it a vintage travel poster", {
      mode,
      familyId: "fine-art-traditional-media",
      subfamilyId: "watercolor",
      operation: "generate",
      preservation: "none",
    });
    assert.equal(decision.used, true);
    assert.equal(decision.mode, mode);
    assert.equal(decision.reason, "explicit_user_selection");
    assert.deepEqual(
      decision.selections.map(item => `${item.family_id}/${item.subfamily_id}`),
      ["fine-art-traditional-media/watercolor"]
    );
    assert.equal(decision.visual_reference_used, false);
  }
  assert.throws(
    () => atlas.select("x", { mode: "manual_browse", familyId: "photography" }),
    error => error.code === "ATLAS_SELECTION_INVALID"
  );
});

test("style enrichment is bounded, subject-safe, anatomy-safe, and separately observable", () => {
  const original = "A Jerusalem street at sunset with two adults and one child, all fully visible with coherent anatomy.";
  const task = {
    operation: "generate",
    request: original,
    prompt: original,
    preservation: "none",
    _aag_authoritative_request: `${original} Render this in watercolor.`,
    _aag_upstream_request: "",
    _aag_prompt_contract: { final_prompt_sha256: "before" },
  };
  selectiveKnowledge.applyToTask(task);
  assert.equal(task.atlas.used, true);
  assert.equal(task.atlas.visual_reference_used, false);
  assert.ok(task.atlas.context_chars > 0);
  assert.ok(task.atlas.context_chars <= atlas.MAX_CONTEXT_CHARS);
  assert.ok(task.atlas.estimated_context_tokens <= 250);
  assert.match(task.prompt, /Watercolor treatment/);
  assert.match(task.prompt, /user's subject/);
  assert.match(task.prompt, /Human anatomy/);
  assert.doesNotMatch(task.prompt, /\bfox\b|reading a book|beneath a large old tree/i);
  assert.equal(task._aag_prompt_contract.selective_knowledge[0], "visual-atlas");
  assert.notEqual(task._aag_prompt_contract.final_prompt_sha256, "before");

  const plain = {
    operation: "generate",
    request: "a Jerusalem street",
    prompt: "a Jerusalem street",
    preservation: "none",
    _aag_authoritative_request: "a Jerusalem street",
    _aag_upstream_request: "",
  };
  selectiveKnowledge.applyToTask(plain);
  assert.equal(plain.prompt, "a Jerusalem street");
  assert.equal(plain.atlas.used, false);
});

test("signed manual marker is exact, bounded, and distinct from identity/source roles", () => {
  const encoded = atlas.marker(
    "manual_browse",
    "retro-vintage",
    "vintage-travel-poster"
  );
  assert.deepEqual(atlas.parseMarker(encoded), {
    mode: "manual_browse",
    familyId: "retro-vintage",
    subfamilyId: "vintage-travel-poster",
  });
  assert.equal(atlas.parseMarker("AAG_ATLAS_SELECTION_V1 mode=auto family=x subfamily=y"), null);
  const source = fs.readFileSync(path.join(projectRoot, "image-agent/src/visual-atlas.js"), "utf8");
  assert.doesNotMatch(source, /flux|sdxl|comfy|ollama|qwen/i);
  assert.match(source, /visual_reference_used: false/);
  assert.match(source, /identity_reference_protected/);
});

test("deployed providers carry the exact canonical taxonomy snapshot", () => {
  const source = fs.readFileSync(path.join(projectRoot, "image-agent", "src", "visual-atlas.js"), "utf8");
  assert.match(source, /path\.join\(__dirname, "visual-taxonomy\.json"\)/);
  const build = fs.readFileSync(path.join(projectRoot, "image-agent", "tools", "build.js"), "utf8");
  assert.match(build, /file: "visual-taxonomy\.json", source: "integrations\/model-neutral-compatibility\/composer\/visual-taxonomy\.json"/);
});
