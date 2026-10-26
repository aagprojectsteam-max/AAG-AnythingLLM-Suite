"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("crypto");
const scaffold = require("../src/scaffold");
const r = require("../src/runtime");
const comfy = require("../src/comfy");
const endpoints = require("../src/endpoints");

const owner = { AAG_WORKSPACE_ID: "w1", AAG_THREAD_ID: "t1", AAG_USER_ID: "u1", AAG_INVOCATION_UUID: "i1", AAG_TURN_ID: "turn-1", AAG_INVOCATION_PROMPT: "a hanukkiah coloring page" };
// nine candles: eight branches + raised shamash, written the way a workspace LLM would
const CANDLES = [-4, -3, -2, -1, 1, 2, 3, 4].map(k => `<rect x="${485 + k * 85}" y="220" width="30" height="110"/><rect x="${478 + k * 85}" y="330" width="44" height="18"/>`).join("");
const NINE = `<svg viewBox="0 0 1000 1000"><g fill="none" stroke="black"><ellipse cx="500" cy="880" rx="190" ry="28"/><line x1="500" y1="860" x2="500" y2="420"/>${CANDLES}<rect x="485" y="140" width="30" height="110"/><path d="M 470 420 Q 300 500 160 348"/><path d="M 530 420 Q 700 500 840 348"/></g></svg>`;
const fakePng = () => { const b = Buffer.alloc(200, 7); Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(b); return b; };

test("scaffold parser accepts the strict subset and re-emits a canonical drawing", () => {
  const parsed = scaffold.parse(NINE);
  assert.equal(parsed.elements.filter(e => e.name === "rect").length, 17);
  const built = scaffold.canonical(parsed);
  assert.equal(built.width, 1024);
  assert.ok(built.svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000"'));
  assert.ok(!built.svg.includes("onload") && !built.svg.includes("href"));
  assert.match(scaffold.summarize(parsed.elements), /rect:17/);
});

test("scaffold parser tolerates XML comments but nothing else outside the subset", () => {
  const commented = `<svg viewBox="0 0 1000 1000" xmlns="http://www.w3.org/2000/svg">\n  <!-- Base -->\n  <rect x="1" y="1" width="9" height="9" fill="none" stroke="black" stroke-width="8"/>\n  <!-- Shamash: <text> is mentioned here only in a comment -->\n  <line x1="0" y1="0" x2="9" y2="9"/>\n</svg>`;
  assert.equal(scaffold.parse(commented).elements.length, 2);
  assert.throws(() => scaffold.parse('<svg><!-- open comment <rect width="1" height="1"/></svg>'), error => error.code === "SCAFFOLD_INVALID");
  // decorative colors are coerced to "no fill" rather than rejected; black/white are kept
  assert.equal(scaffold.parse('<svg><rect width="1" height="1" fill="red"/></svg>').elements[0].fill, undefined);
  assert.equal(scaffold.parse('<svg><rect width="1" height="1" fill="black"/></svg>').elements[0].fill, "black");
});

test("scaffold parser fails closed on anything outside the subset", () => {
  for (const bad of [
    "", "hello", "<svg></svg>",
    "<svg><text>9</text></svg>",
    '<svg><image href="file:///etc/passwd"/></svg>',
    '<!DOCTYPE svg [<!ENTITY e "x">]><svg><rect width="1" height="1"/></svg>',
    '<svg><rect width="1" height="1" onload="x"/></svg>',
    '<svg><rect width="1" height="1" fill="url(#g)"/></svg>',
    '<svg><rect width="99999" height="1"/></svg>',
    '<svg><style>x</style><rect width="1" height="1"/></svg>',
    "<svg><g><rect width=\"1\" height=\"1\"/></svg>",
    `<svg>${'<circle cx="1" cy="1" r="1"/>'.repeat(scaffold.MAX_ELEMENTS + 1)}</svg>`,
  ]) {
    assert.throws(() => scaffold.parse(bad), error => error.code === "SCAFFOLD_INVALID", bad.slice(0, 40));
  }
});

test("scaffold is accepted on generate only and selects the scaffold workflow", () => {
  const task = r.normalizeTask({ operation: "generate", prompt: "friendly coloring page", source_policy: "auto", preservation: "none", scaffold: NINE }, owner);
  assert.equal(task.scaffold, NINE);
  assert.equal(task._aag_scaffold.elements, 21);
  assert.equal(r.workflow(task), "generation.scaffold.fast.v1");
  assert.equal(r.workflow(r.normalizeTask({ operation: "generate", prompt: "x", source_policy: "auto", preservation: "none" }, owner)), "generation.text.fast.v1");
  const withAttachment = { ...owner, AAG_INVOCATION_ATTACHMENTS: [{ name: "a.png", mime: "image/png", contentString: `data:image/png;base64,${fakePng().toString("base64")}` }] };
  for (const bad of [
    { operation: "transform", prompt: "x", source_policy: "current_attachment", preservation: "subject", scaffold: NINE },
    { operation: "upscale", prompt: "x", source_policy: "current_attachment", preservation: "none", scaffold: NINE },
    { operation: "colorize", prompt: "x", source_policy: "current_attachment", preservation: "none", scaffold: NINE },
  ]) assert.throws(() => r.normalizeTask(bad, withAttachment), error => error.code === "INVALID_ARGUMENT", bad.operation);
  assert.throws(() => r.normalizeTask({ operation: "generate", prompt: "x", source_policy: "auto", preservation: "none", scaffold: "<svg><text>9</text></svg>" }, owner), error => error.code === "SCAFFOLD_INVALID");
});

test("scaffold source resolves through an injected rasterizer with provenance", async () => {
  const task = r.normalizeTask({ operation: "generate", prompt: "friendly coloring page", source_policy: "auto", preservation: "none", scaffold: NINE }, owner);
  const resolved = await r.resolveSource(task, owner, "/tmp/unused", { renderScaffold: async built => ({ bytes: fakePng(), width: built.width, height: built.height, format: "png" }) });
  assert.equal(resolved.source.kind, "structural_scaffold");
  assert.equal(resolved.source.scaffold_elements, 21);
  assert.match(resolved.source.scaffold_sha256, /^[a-f0-9]{64}$/);
  assert.equal(resolved.normalized.width, 1024);
  assert.deepEqual(resolved.runtime.AAG_INVOCATION_ATTACHMENTS, []);
});

test("comfy execute routes a scaffolded generate through the reference graph on the fast profile", async () => {
  const submitted = [];
  const fetchImpl = async (url, options = {}) => {
    const path = String(url).replace(endpoints.COMFY, "").replace(endpoints.HUB_INTERNAL, "HUB");
    const reply = body => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });
    if (path === "/object_info") return reply(Object.fromEntries([...comfy.COMMON_NODES, ...comfy.GENERAL_NODES].map(n => [n, {}])));
    if (path === "/models/diffusion_models") return reply(Object.values(comfy.PROFILES).map(p => p.unet));
    if (path === "/models/text_encoders") return reply(Object.values(comfy.PROFILES).map(p => p.clip));
    if (path === "/models/vae") return reply(["flux2-vae.safetensors"]);
    if (path === "/upload/image") return reply({ name: "scaffold.png", subfolder: "AAG-Image-Agent-Source" });
    if (path === "/prompt") { submitted.push(JSON.parse(options.body).prompt); return reply({ prompt_id: "p-s" }); }
    if (path.startsWith("/history/")) return reply({ "p-s": { outputs: { "17": { images: [{ filename: "SCAF-1.png", subfolder: "", type: "output" }] } } } });
    if (path === "HUB/import-comfyui") return reply({ ok: true, filename: "REF-SCAF-1.png" });
    throw new Error("unexpected " + path);
  };
  const details = [];
  const task = { operation: "generate", quality: "quality", request: "a hanukkiah coloring page", prompt: "friendly coloring page", scaffold: NINE, seed: 7, aspect_ratio: "auto",
    _aag_source: { kind: "structural_scaffold", scaffold_sha256: "ab".repeat(32), scaffold_elements: 21, scaffold_kinds: "rect:17", scaffold_canvas: "1000×1000" } };
  const normalized = { bytes: fakePng(), width: 1024, height: 1024, format: "png" };
  const outputs = await comfy.execute(task, normalized, "lease", { fetch: fetchImpl, historyPollMs: 1, sleep: async () => {}, prepareGeneralReference: async n => n, onExecutionDetails: v => details.push(v), onEngineMetadata: v => details.push(v) });
  assert.deepEqual(outputs, ["REF-SCAF-1.png"]);
  const graph = submitted[0];
  assert.equal(graph["1"].inputs.unet_name, comfy.PROFILES.fast.unet, "quality is forced to the fast profile when scaffolded");
  assert.ok(Object.values(graph).some(n => n.class_type === "ReferenceLatent"));
  assert.ok(graph["7"].inputs.text.includes(scaffold.ENGINE_SUFFIX));
  assert.ok(details.some(d => d.adapter === "comfy-scaffold-v1" && d.scaffold_elements === 21));
  assert.ok(details.some(d => d.structural_scaffold === "yes" && d.workflow_revision === "comfy.scaffold.v1"));
});
