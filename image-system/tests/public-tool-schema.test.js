"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const schema = require("../schemas/provider-task.schema.json");
const plugin = require("../skills/aag-image-task/plugin.json");
const batchSchema = require("../schemas/provider-batch.schema.json");
const batchPlugin = require("../skills/aag-image-batch/plugin.json");
const { loadPublicSchema, validatePublicArguments, withPublicToolSchema } = require("../integrations/anythingllm/aagPublicToolSchema");

const EXPECTED = Object.freeze({
  operation: ["generate", "transform", "upscale"],
  source_policy: ["auto", "current_attachment", "previous_artifact"],
  preservation: ["auto", "identity", "subject", "none"],
  quality: ["auto", "fast", "balanced", "quality"],
  aspect_ratio: ["auto", "1:1", "16:9", "9:16", "4:3", "3:2", "landscape", "portrait"],
  scale: [2, 3, 4],
});

test("public task schema publishes the complete closed enum contract", () => {
  assert.deepEqual(schema.required, ["operation", "prompt", "source_policy", "preservation"]);
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(Object.keys(schema.properties), Object.keys(plugin.entrypoint.params));
  for (const [field, values] of Object.entries(EXPECTED)) {
    assert.deepEqual(schema.properties[field].enum, values);
    assert.match(schema.properties[field].description, /exact|listed|Do not invent|do not invent/i);
  }
  assert.match(schema.properties.prompt.description, /NON-NEGOTIABLE REQUIRED JSON ARGUMENT/);
  assert.match(schema.properties.prompt.description, /Never make a routing-only generate\/transform call/);
  assert.match(schema.properties.prompt.description, /3D/);
  assert.equal(schema.properties.style, undefined);
  assert.match(schema.description, /no structured style field/);
  assert.match(`${plugin.description}\n${schema.description}`, /never invent (?:enum )?values|never invent another/i);
  assert.match(`${plugin.description}\n${schema.description}`, /belong(?:s)? (?:only )?in prompt|stay only in prompt/);
  assert.equal(plugin.public_schema, "provider-task.schema.json");
  assert.match(plugin.description, /always include exact documented operation, prompt, source_policy, and preservation/i);
  assert.match(schema.properties.operation.description, /at most once per user turn/i);
  assert.match(schema.properties.operation.description, /including failure/i);
  assert.match(schema.properties.operation.description, /COMPLETE-CALL CHECKLIST[\s\S]*explicit speed\/fastest intent MUST emit quality=fast/i);
  assert.equal(plugin.same_turn_retry, "forbidden");
});

test("public contract documents every supported operation without exposing internals", () => {
  const visible = JSON.stringify({ description: plugin.description, schema });
  for (const term of ["generate", "transform", "upscale", "current_attachment", "previous_artifact", "identity", "subject", "coloring-page", "photorealism", "fast", "balanced", "quality", "16:9", "portrait"]) assert.match(visible, new RegExp(term.replace(":", "\\:"), "i"));
  for (const hidden of ["lease_token", "model_sha256", "checkpoint", "sampler", "scheduler", "CFG", "steps", "workflow_id", "contract_sha256"]) assert.doesNotMatch(visible, new RegExp(hidden, "i"));
});

test("multi-image schema exposes one closed provider-neutral exact-plan contract", () => {
  assert.equal(batchPlugin.public_schema, "provider-batch.schema.json");
  assert.equal(batchPlugin.same_turn_retry, "forbidden");
  assert.deepEqual(batchSchema.required, ["operation", "collection_brief", "count", "quality", "items"]);
  assert.equal(batchSchema.additionalProperties, false);
  assert.deepEqual(batchSchema.properties.operation.enum, ["multi_generate"]);
  assert.equal(batchSchema.properties.count.minimum, 2);
  assert.equal(batchSchema.properties.count.maximum, 10);
  assert.equal(batchSchema.properties.items.minItems, 2);
  assert.equal(batchSchema.properties.items.maxItems, 10);
  assert.equal(batchSchema.properties.items.items.additionalProperties, false);
  assert.deepEqual(batchSchema.properties.quality.enum, ["auto", "fast", "balanced", "quality"]);
  assert.equal(batchSchema.properties.style, undefined);
  assert.equal(batchSchema.properties.items.items.properties.style, undefined);
  const visible = JSON.stringify({ plugin: batchPlugin, schema: batchSchema });
  for (const expected of ["distinct", "variants", "coherent series", "sequential scenes", "exactly count", "workspace model", "never invents missing images", "no structured style field"]) assert.match(visible, new RegExp(expected, "i"));
  for (const hidden of ["lease_token", "checkpoint", "model_sha256", "sampler", "scheduler", "workflow_id", "CFG", "steps"]) assert.doesNotMatch(visible, new RegExp(hidden, "i"));
});

test("canonical pre-execution validator closes nested batch fields without provider grammar", () => {
  const valid = {
    operation: "multi_generate",
    collection_brief: "Exactly two ordered watercolor forest scenes.",
    count: 2,
    quality: "auto",
    items: [{ prompt: "First complete watercolor forest scene." }, { prompt: "Second complete watercolor forest scene.", aspect_ratio: "portrait" }],
  };
  assert.equal(validatePublicArguments(batchSchema, valid).valid, true);
  for (const invalid of [
    { ...valid, style: "watercolor" },
    { ...valid, quality: "highest" },
    { ...valid, count: 11 },
    { ...valid, items: [{ prompt: "Only one" }] },
    { ...valid, items: [{ prompt: "One", style: "watercolor" }, valid.items[1]] },
    { operation: "multi_generate", count: 2, quality: "auto", items: valid.items },
  ]) assert.equal(validatePublicArguments(batchSchema, invalid).valid, false);
});

test("public contract teaches source selection by semantic category", () => {
  const contract = `${plugin.description}\n${schema.description}\n${schema.properties.source_policy.description}`;
  assert.match(contract, /current_attachment[^.]*CURRENT (?:user )?(?:message|turn)/i);
  assert.match(contract, /previous_artifact[^.]*most recently returned (?:eligible )?AAG image/i);
  assert.match(contract, /last\/previous image|last image/i);
  assert.match(contract, /image just (?:made|created|generated)/i);
  assert.match(contract, /auto[^.]*only (?:when|for).*unspecified|Use auto only if the user did not identify/i);
  assert.match(contract, /non-latest older thread artifact.*not currently a public capability|not publicly selectable/i);
  assert.match(contract, /do not invent thread_reference/i);
  assert.deepEqual(schema.properties.source_policy.enum, ["auto", "current_attachment", "previous_artifact"]);
});

test("source index and preservation semantics are explicit and closed", () => {
  const index = schema.properties.source_index.description;
  assert.match(index, /ONE-BASED/i);
  assert.match(index, /only the image attachments on the CURRENT user (?:message|turn)/i);
  assert.match(index, /Valid only when source_policy is current_attachment/i);
  assert.match(index, /Omit for auto, previous_artifact/i);
  assert.match(index, /never indexes prior artifacts or thread history/i);

  const preservation = schema.properties.preservation.description;
  assert.equal(Object.hasOwn(schema.properties.preservation, "default"), false);
  assert.match(preservation, /same recognizable HUMAN PERSON/i);
  assert.match(preservation, /Never use identity for animals, objects, products, logos/i);
  assert.match(preservation, /subject means preserve ordinary non-human subject, object, content, layout, or composition/i);
  assert.match(preservation, /auto does not decide identity versus subject/i);
  assert.match(preservation, /never select auto for generate or upscale/i);
});

test("quality semantics follow explicit technical preference without creative-word promotion", () => {
  const quality = schema.properties.quality.description;
  const contract = `${plugin.description}\n${schema.description}\n${quality}`;
  assert.match(quality, /fast when the user explicitly prioritizes speed, quick output, low latency, or the fastest/i);
  assert.match(quality, /quality ONLY when the user explicitly requests maximum, best, or highest generation quality/i);
  assert.match(quality, /balanced (?:ONLY )?when the user explicitly requests a compromise or balance between speed and quality/i);
  assert.match(quality, /DEFAULT DECISION IS auto[\s\S]*omission is equivalent/i);
  for (const creative of ["3D", "cinematic", "detailed", "polished", "professional", "realistic", "storybook", "watercolor", "beautiful", "highly detailed"]) {
    assert.match(contract, new RegExp(creative, "i"));
  }
  assert.match(contract, /MUST NOT (?:by themselves |alone )?(?:imply quality|select (?:any non-auto )?quality)/i);
  assert.match(contract, /meaning in any (?:user )?language|Interpret meaning in any user language/i);
  assert.match(contract, /rather than (?:matching|applying) a provider-specific (?:phrase list|keyword rule)/i);
});

test("provider-neutral boundary makes canonical validation authoritative without provider grammar", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aag-no-provider-grammar-"));
  const skill = path.join(root, "plugins", "agent-skills", "aag-image-task");
  fs.mkdirSync(skill, { recursive: true });
  fs.writeFileSync(path.join(skill, "provider-task.schema.json"), JSON.stringify(schema));
  const functions = new Map();
  let calls = 0;
  const imported = { name: "aag-image-task", config: { public_schema: "provider-task.schema.json", same_turn_retry: "forbidden" } };
  const configured = { setup(target) { target.functions.set("aag-image-task", { async handler() { calls += 1; return "unsafe"; } }); } };
  const aibitat = { functions, skipHandleExecution: false };
  withPublicToolSchema(imported, configured, { pluginsRoot: root }).setup(aibitat);
  const handler = functions.get("aag-image-task").handler;
  const malformed = [
    { operation: "generate", source_policy: "auto", preservation: "none", prompt: "Creative prompt", unknown_field: true },
    { operation: "generate", source_policy: "auto", prompt: "Creative prompt" },
    { operation: "generate", source_policy: "automatic", preservation: "none", prompt: "Creative prompt" },
  ];
  for (const args of malformed) {
    aibitat.skipHandleExecution = false;
    const result = await handler(args);
    assert.match(result, /error_code=PUBLIC_SCHEMA_VIOLATION/);
    assert.match(result, /same_turn_retry=forbidden/);
    assert.equal(aibitat.skipHandleExecution, true);
  }
  assert.equal(calls, 0);
  const adapter = fs.readFileSync(path.join(__dirname, "../integrations/anythingllm/aagPublicToolSchema.js"), "utf8");
  assert.doesNotMatch(adapter, /llama\.cpp|LM Studio|Ollama|Gemini|OpenAI/i);
});

test("provider boundary documentation keeps runtime grammar optional", () => {
  const document = fs.readFileSync(path.join(__dirname, "../docs/PROVIDER-NEUTRAL-BOUNDARY.md"), "utf8");
  assert.match(document, /ANY PROVIDER \/ ANY MODEL[\s\S]*provider adapter[\s\S]*canonical AAG public tool contract[\s\S]*strict canonical pre-execution validation[\s\S]*governed execution/i);
  assert.match(document, /optional defense in depth, not a security or[\s\S]*correctness dependency/i);
  assert.match(document, /LM Studio and Ollama/i);
  assert.match(document, /Gemini and OpenAI-compatible cloud providers/i);
  assert.match(document, /No Image System rewrite or[\s\S]*provider\/model allow-list is required/i);
  assert.match(document, /Unknown properties, missing required fields, invalid enums[\s\S]*fail closed/i);
});

test("deployed-schema adapter installs the exact closed schema seen by providers", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aag-public-schema-"));
  const skill = path.join(root, "plugins", "agent-skills", "aag-image-task");
  fs.mkdirSync(skill, { recursive: true });
  fs.writeFileSync(path.join(skill, "provider-task.schema.json"), JSON.stringify(schema));
  const imported = { name: "aag-image-task", config: { public_schema: "provider-task.schema.json", same_turn_retry: "forbidden" } };
  assert.deepEqual(loadPublicSchema(imported, { pluginsRoot: root }), schema);
  const functions = new Map();
  let calls = 0;
  const configured = { setup(target) { target.functions.set("aag-image-task", { name: "aag-image-task", parameters: { type: "object", properties: {} }, async handler() { calls += 1; return "completed"; } }); } };
  const aibitat = { functions, skipHandleExecution: false };
  withPublicToolSchema(imported, configured, { pluginsRoot: root }).setup(aibitat);
  assert.deepEqual(functions.get("aag-image-task").parameters, schema);
  assert.equal(await functions.get("aag-image-task").handler({ operation: "generate", source_policy: "auto", preservation: "none", prompt: "A rich storybook illustration." }), "completed");
  assert.equal(calls, 1);
  assert.equal(aibitat.skipHandleExecution, true);
});

test("execution adapter rejects unknown, missing, and invalid fields before the handler", async () => {
  for (const args of [
    { operation: "generate", source_policy: "auto", preservation: "none", prompt: "A storybook illustration.", style: "storybook illustration" },
    { operation: "generate", prompt: "A storybook illustration." },
    { operation: "generate", source_policy: "auto", preservation: "none", quality: "ultra" },
  ]) {
    const validation = validatePublicArguments(schema, args);
    assert.equal(validation.valid, false);
    assert.ok(validation.errors.length > 0);
  }

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aag-public-execution-"));
  const skill = path.join(root, "plugins", "agent-skills", "aag-image-task");
  fs.mkdirSync(skill, { recursive: true });
  fs.writeFileSync(path.join(skill, "provider-task.schema.json"), JSON.stringify(schema));
  const functions = new Map();
  let calls = 0;
  const imported = { name: "aag-image-task", config: { public_schema: "provider-task.schema.json", same_turn_retry: "forbidden" } };
  const configured = { setup(target) { target.functions.set("aag-image-task", { async handler() { calls += 1; return "unexpected"; } }); } };
  const aibitat = { functions, skipHandleExecution: false };
  withPublicToolSchema(imported, configured, { pluginsRoot: root }).setup(aibitat);
  const result = await functions.get("aag-image-task").handler({ operation: "generate", prompt: "A storybook illustration.", style: "storybook illustration" });
  assert.match(result, /error_code=PUBLIC_SCHEMA_VIOLATION/);
  assert.match(result, /same_turn_retry=forbidden/);
  assert.equal(calls, 0);
  assert.equal(aibitat.skipHandleExecution, true);
});

test("canonical schema structurally requires prompt for every governed operation", () => {
  for (const operation of ["generate", "transform", "upscale"]) {
    const validation = validatePublicArguments(schema, {
      operation,
      source_policy: operation === "generate" ? "auto" : "previous_artifact",
      preservation: operation === "transform" ? "subject" : "none",
    });
    assert.equal(validation.valid, false);
    assert.ok(validation.errors.some(error => error.code === "REQUIRED" && error.location === "$.prompt"));
  }
  assert.equal(validatePublicArguments(schema, {
    operation: "upscale",
    source_policy: "previous_artifact",
    preservation: "none",
    prompt: "Upscale the selected source without creative alteration.",
  }).valid, true);
});

test("schema adapter rejects unsafe references and open schemas", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aag-public-schema-bad-"));
  const skill = path.join(root, "plugins", "agent-skills", "aag-image-task");
  fs.mkdirSync(skill, { recursive: true });
  assert.throws(() => loadPublicSchema({ name: "aag-image-task", config: { public_schema: "../escape.json" } }, { pluginsRoot: root }));
  fs.writeFileSync(path.join(skill, "bad.json"), JSON.stringify({ type: "object", properties: {}, required: [], additionalProperties: true }));
  assert.throws(() => loadPublicSchema({ name: "aag-image-task", config: { public_schema: "bad.json" } }, { pluginsRoot: root }));
});
