"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { AagError, redact } = require("../src/errors");
const image = require("../src/image");
const r = require("../src/runtime");
const promptQuality = require("../src/prompt-quality");
const sceneIdentity = require("../src/scene-identity");

const owner = { AAG_WORKSPACE_ID: "w1", AAG_THREAD_ID: "t1", AAG_USER_ID: "u1", AAG_INVOCATION_UUID: "i1", AAG_TURN_ID: "turn-1" };

function temporary(prefix = "aag-test-") { return fs.mkdtempSync(path.join(os.tmpdir(), prefix)); }
function fakePng(byte = 1) {
  const value = Buffer.alloc(160, byte);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(value);
  value.writeUInt32BE(8, 16); value.writeUInt32BE(8, 20);
  return value;
}
function attachment(bytes = fakePng(), name = "source.png", mime = "image/png") {
  return { name, mime, contentString: `data:${mime};base64,${bytes.toString("base64")}` };
}
function response(bytes, contentType = "image/png") {
  return { ok: true, status: 200, headers: { get: key => key.toLowerCase() === "content-type" ? contentType : null }, arrayBuffer: async () => bytes };
}
function mockDeps(options = {}) {
  let executeCount = 0;
  return {
    scheduler: { engineActivity: async () => ({ active: false }), disableHeartbeat: true, sleep: ms => new Promise(resolve => setTimeout(resolve, Math.min(ms, 2))) },
    adapters: {
      execute: options.execute || (async () => [`result-${++executeCount}.png`]),
      fetch: options.fetch || (async url => response(fakePng(String(url).includes("result-2") ? 2 : 1))),
      inspectOutput: options.inspectOutput || (async () => ({ width: 8, height: 8, format: "png" })),
    },
    normalizeImage: options.normalizeImage || (async parsed => ({ bytes: parsed.bytes, width: 8, height: 8, format: "png", original_format: parsed.actual, alpha_policy: "flatten-white", orientation_policy: "exif-transpose", metadata_policy: "stripped" })),
    fetch: options.sourceFetch,
  };
}
function runtime(root, invocation = "i1", turn = invocation) {
  return { ...owner, AAG_INVOCATION_UUID: invocation, AAG_TURN_ID: turn, AAG_IMAGE_AGENT_STATE_ROOT: root, AAG_IMAGE_QUEUE_TIMEOUT_MS: 1000, AAG_IMAGE_LEASE_STALE_MS: 5000, AAG_IMAGE_QUEUE_POLL_MS: 25 };
}
const rawCreateTask = r.createTask;
function productionPrompt(request) {
  return `${String(request || "The requested scene").trim()}. A polished professional illustration with the primary subject actively interacting with the important object inside a clearly established environment. Show readable relationships, believable contact, coherent anatomy and balanced physical posture. Use purposeful composition, clear framing, consistent spatial perspective, strong subject separation, scene-appropriate lighting, controlled shadows, refined background detail and sufficient professional visual clarity throughout the complete image.`;
}
r.createTask = (args, trusted, context) => {
  if (args.operation === "upscale") return rawCreateTask(args, trusted, context);
  let prompt = args.prompt;
  try { promptQuality.validate({ authoritative: trusted?.AAG_INVOCATION_PROMPT || args.request, proposal: prompt, identityScene: args.preservation === "identity" }); }
  catch { prompt = productionPrompt(trusted?.AAG_INVOCATION_PROMPT || args.request); }
  return rawCreateTask({ ...args, prompt }, trusted, context);
};
async function waitFor(check, timeout = 1000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { const value = check(); if (value) return value; await new Promise(resolve => setTimeout(resolve, 5)); }
  throw new Error("condition timeout");
}

test("normalizes provider-neutral bounded arguments", () => {
  assert.equal(r.PROVIDER_POLICY, "OPEN_BY_CAPABILITY");
  const task = r.normalizeTask({ operation: "generate", request: "צור תמונה", count: 2, quality: "fast", seed: 2147483647 }, owner);
  assert.equal(task.operation, "generate"); assert.equal(task.count, 2); assert.equal(task.preservation, "none");
  assert.equal(r.seedFor(task, 1), 0);
});

test("missing technical operation safely defaults only to generation without attachments", () => {
  const generated = r.normalizeTask({ prompt: productionPrompt("one cat in a studio") }, { ...owner, AAG_INVOCATION_PROMPT: "one cat in a studio" });
  assert.equal(generated.operation, "generate");
  assert.throws(
    () => r.normalizeTask({ prompt: productionPrompt("edit this image") }, { ...owner, AAG_INVOCATION_PROMPT: "edit this image", AAG_INVOCATION_ATTACHMENTS: [attachment()] }),
    error => error.code === "INVALID_ARGUMENT",
  );
});

test("provider compatibility is capability-based without vendor or model policy", () => {
  const source = fs.readFileSync(path.join(__dirname, "../src/runtime.js"), "utf8");
  assert.equal(r.PROVIDER_POLICY, "OPEN_BY_CAPABILITY");
  assert.doesNotMatch(source, /(?:provider|model)\s*(?:===|==|includes\s*\()/i);
  for (const runtimeProviderNoise of [
    {},
    { AAG_PROVIDER: "future-provider", AAG_MODEL: "future-model" },
    { AAG_PROVIDER: "local-compatible", AAG_MODEL: "arbitrary-capable-model" },
  ]) {
    const task = r.normalizeTask({ operation: "generate", request: "a quiet forest" }, { ...owner, ...runtimeProviderNoise });
    assert.equal(task.request, "a quiet forest");
    assert.equal(r.workflow(task), "generation.text.fast.v1");
  }
});

test("omitted provider request uses only the trusted current invocation prompt", () => {
  const trusted = { ...owner, AAG_INVOCATION_PROMPT: "same girl riding a camel in the desert" };
  const task = r.normalizeTask({
    operation: "transform",
    prompt: "The girl from the reference image riding a camel.",
    preservation: "identity",
    source_policy: "current_attachment",
    source_index: 1,
  }, trusted);
  assert.equal(task.request, trusted.AAG_INVOCATION_PROMPT);
  assert.equal(r.workflow(task), "transform.human.identity.scene.v1");
  assert.throws(
    () => r.normalizeTask({ operation: "generate", prompt: "only a model prompt" }, owner),
    error => error.code === "INVALID_ARGUMENT" && error.message === "A request is required.",
  );
});

test("under-specified workspace prompt is measured and routed unchanged without a retry loop", async () => {
  const root = temporary();
  let observed = null;
  let schedulerChecks = 0;
  const trustedRuntime = {
    ...runtime(root, "prompt-enrichment-session", "prompt-enrichment-turn"),
    AAG_INVOCATION_PROMPT: "תעשה לי תמונה מצויירת של חתול ועכבר בזירת אגרוף",
  };
  const deps = mockDeps({ execute: async task => { observed = task; return ["unexpected.png"]; } });
  deps.scheduler.engineActivity = async () => { schedulerChecks += 1; return { active: false }; };
  const result = await rawCreateTask({
    operation: "generate",
    request: "cat picture",
    prompt: "A hand-drawn illustration of a cat and a mouse engaged in a boxing match. Dynamic action, cartoon style.",
    seed: 77,
  }, trustedRuntime, { deps });
  assert.match(result, /status=completed/);
  assert.equal(observed.prompt, "A hand-drawn illustration of a cat and a mouse engaged in a boxing match. Dynamic action, cartoon style.");
  assert.equal(observed._aag_prompt_contract.status, "UNDER_SPECIFIED");
  assert.ok(schedulerChecks > 0);
  assert.equal(fs.readdirSync(path.join(root, "jobs")).filter(name => /^aag-.*\.json$/.test(name)).length, 2);
});

test("rich workspace prompt creates exactly one job and rejects every structured style field", async () => {
  const root = temporary();
  const trusted = runtime(root, "bounded-style-session", "bounded-style-turn");
  const authoritative = "תעשה לי תמונה מצויירת של חתול ועכבר בזירת אגרוף";
  const prompt = "A polished cartoon illustration featuring exactly one expressive cat and one agile mouse actively boxing one another inside a clearly recognizable boxing ring. Show both characters clearly in coherent fighting stances with boxing gloves, believable balance and readable interaction. Include ring ropes and canvas, a purposeful arena background, attractive scene-appropriate lighting, clear subject separation, expressive faces, consistent cartoon rendering, balanced composition and polished visual detail.";
  let observed = null;
  const result = await rawCreateTask({ operation: "generate", prompt, seed: 18 }, { ...trusted, AAG_INVOCATION_PROMPT: authoritative }, { deps: mockDeps({ execute: async task => { observed = task; return ["rich.png"]; } }) });
  assert.match(result, /status=completed/);
  assert.equal(observed.prompt, prompt);
  assert.equal(observed._aag_prompt_contract.author, "workspace-llm");
  assert.equal(fs.readdirSync(path.join(root, "jobs")).filter(name => /^aag-.*\.json$/.test(name)).length, 2);
  for (const style of ["illustration", "storybook illustration", "provider-custom", "3D illustration"]) {
    assert.throws(
      () => r.normalizeTask({ operation: "generate", prompt: "A detailed illustration of a cat.", style }, trusted),
      error => error.code === "INVALID_ARGUMENT" && /unsupported argument/.test(error.message),
    );
  }
});

test("rejects infrastructure, workflow, model, path, and command fields", () => {
  for (const extra of [{ workflow_id: "evil" }, { output_path: "/tmp/x" }, { command: "rm" }, { executable: "/bin/sh" }, { model: "arbitrary" }, { publisher_url: "http://evil" }]) {
    assert.throws(() => r.normalizeTask({ operation: "generate", request: "x", ...extra }, owner), error => error.code === "INVALID_ARGUMENT");
  }
});

test("requires trusted workspace and thread owner scope", () => {
  assert.throws(() => r.normalizeTask({ operation: "generate", request: "x" }, { AAG_WORKSPACE_ID: "w1" }), error => error.code === "OWNER_SCOPE_REQUIRED");
});

test("requires trusted invocation and per-turn scope", () => {
  assert.throws(() => r.normalizeTask({ operation: "generate", request: "x" }, { ...owner, AAG_TURN_ID: undefined }), error => error.code === "TURN_SCOPE_REQUIRED");
  assert.throws(() => r.normalizeTask({ operation: "generate", request: "x" }, { ...owner, AAG_INVOCATION_UUID: undefined }), error => error.code === "TURN_SCOPE_REQUIRED");
});

test("workflow registry selection is bounded and transform auto is explicit ambiguity", () => {
  assert.equal(r.workflow(r.normalizeTask({ operation: "upscale", request: "חדד", scale: 4 }, owner)), "upscale.preserve.auto.v1");
  const task = r.normalizeTask({ operation: "transform", request: "שנה", preservation: "auto" }, owner);
  assert.throws(() => r.workflow(task), error => error.code === "SOURCE_AMBIGUOUS");
});

test("identity framing hints canonicalize and route internally without exposing contracts", () => {
  const task = r.normalizeTask({ operation: "transform", request: "same person portrait", preservation: "identity", source_policy: "current_attachment" }, owner);
  assert.equal(r.workflow(task), "transform.human.identity.portrait.v1");
  assert.equal(task._aag_identity_profile, "contract-b-portrait");
  const explicit = r.normalizeTask({ operation: "transform", request: "same person portrait", preservation: "identity", source_policy: "current_attachment", width: 896, height: 1152 }, owner);
  assert.equal(r.workflow(explicit), "transform.human.identity.portrait.v1");
  assert.equal(explicit.width, undefined); assert.equal(explicit.height, undefined); assert.equal(explicit.aspect_ratio, "auto");
  const scene = r.normalizeTask({ operation: "transform", request: "same girl riding a camel in the desert", preservation: "identity", source_policy: "current_attachment", aspect_ratio: "landscape" }, owner);
  assert.equal(r.workflow(scene), "transform.human.identity.scene.v1");
  assert.equal(scene._aag_identity_profile, "scene-c-landscape");
  assert.deepEqual([scene._aag_internal_width, scene._aag_internal_height], [1152, 896]);
  assert.throws(
    () => r.normalizeTask({ operation: "transform", request: "two", preservation: "identity", count: 2 }, owner),
    error => error.code === "IDENTITY_COUNT_UNSUPPORTED",
  );
  assert.throws(
    () => r.normalizeTask({ operation: "transform", request: "wide", preservation: "identity", width: 1024 }, owner),
    error => error.code === "IDENTITY_FRAMING_INCOMPLETE",
  );
});

test("identity routing uses combined semantic evidence and precise unsupported errors", () => {
  for (const request of ["same child sitting in a garden", "same person walking in the rain", "same person standing beside a car", "תעשה לי תמונה של הילדה הזו רוכבת על גמל"]) {
    const task = r.normalizeTask({ operation: "transform", request, preservation: "identity", source_policy: "current_attachment" }, owner);
    assert.equal(r.workflow(task), "transform.human.identity.scene.v1", request);
  }
  assert.equal(r.workflow(r.normalizeTask({ operation: "transform", request: "professional portrait of the same person", preservation: "identity", source_policy: "current_attachment", aspect_ratio: "portrait" }, owner)), "transform.human.identity.portrait.v1");
  assert.throws(() => r.normalizeTask({ operation: "transform", request: "portrait of the same person", preservation: "identity", source_policy: "current_attachment", aspect_ratio: "landscape" }, owner), error => error.code === "IDENTITY_FRAMING_CONFLICT");
  assert.throws(() => r.normalizeTask({ operation: "transform", request: "same person walking in a crowd", preservation: "identity", source_policy: "current_attachment" }, owner), error => error.code === "SCENE_IDENTITY_ENVELOPE_UNSUPPORTED");
  assert.throws(() => r.normalizeTask({ operation: "transform", request: "same person walking in rain", preservation: "identity", source_policy: "previous_artifact" }, owner), error => error.code === "IDENTITY_SOURCE_POLICY_UNSUPPORTED");
});

test("identity preservation rejects unvalidated stylization before creating a job", async () => {
  for (const request of [
    "Make the same person a children's-book watercolor illustration",
    "צור את אותו אדם באיור ספר ילדים בצבעי מים",
  ]) {
    assert.throws(
      () => r.normalizeTask({ operation: "transform", request, preservation: "identity", source_policy: "current_attachment" }, owner),
      error => error.code === "IDENTITY_RENDERING_STYLE_UNSUPPORTED" && /validated realistic rendering only/.test(error.message),
    );
    const root = temporary("aag-identity-style-conflict-");
    const result = await rawCreateTask(
      { operation: "transform", request, preservation: "identity", source_policy: "current_attachment" },
      { ...runtime(root, `identity-style-${request.length}`, `identity-style-turn-${request.length}`), AAG_INVOCATION_PROMPT: request, AAG_INVOCATION_ATTACHMENTS: [attachment()] },
      { deps: mockDeps({ execute: async () => { throw new Error("must not execute"); } }) },
    );
    assert.match(result, /error_code=IDENTITY_RENDERING_STYLE_UNSUPPORTED/);
    assert.equal(r.store.listJobs(root).length, 0);
  }
  const general = r.normalizeTask({
    operation: "transform",
    request: "Create a children's-book watercolor illustration from this reference",
    preservation: "subject",
    source_policy: "current_attachment",
  }, owner);
  assert.equal(r.workflow(general), "transform.general.fast.v1");
});

test("general reference preparation bounds the engine-only upload without changing the governed source", async () => {
  const original = {
    bytes: Buffer.alloc(19_255_773, 0x5a),
    width: 3888,
    height: 5184,
    format: "png",
  };
  const prepared = await r.adapters.comfy.prepareGeneralReference(original, 0.5, {
    prepareGeneralReference: async (source, megapixels) => {
      assert.equal(source, original);
      assert.equal(megapixels, 0.5);
      return { bytes: Buffer.alloc(600_000, 0x33), width: 612, height: 816, format: "png" };
    },
  });
  assert.equal(prepared.bytes.length, 600_000);
  assert.deepEqual([prepared.width, prepared.height], [612, 816]);
  assert.equal(original.bytes.length, 19_255_773);
  await assert.rejects(
    r.adapters.comfy.prepareGeneralReference(original, 0.5, {
      prepareGeneralReference: async () => ({ bytes: Buffer.alloc(r.adapters.comfy.MAX_ENGINE_REFERENCE_BYTES + 1), width: 612, height: 816, format: "png" }),
    }),
    error => error.code === "SOURCE_TOO_LARGE",
  );
});

test("scene identity preserves the semantic user scene and applies bounded composition instructions", () => {
  const task = r.normalizeTask({
    operation: "transform",
    request: "תעשה לי תמונה של הילדה הזו רוכבת על גמל",
    prompt: "the same young girl from the reference photo riding a camel in the desert",
    preservation: "identity",
    source_policy: "current_attachment",
    source_index: 1,
    aspect_ratio: "landscape",
  }, owner);
  assert.equal(r.workflow(task), "transform.human.identity.scene.v1");
  assert.equal(task._aag_identity_profile, "scene-c-landscape");
  assert.equal(task._aag_internal_width, 1152);
  assert.equal(task._aag_internal_height, 896);
  const normalized = sceneIdentity.scenePrompt(task);
  assert.match(normalized, /exactly one toddler girl/i);
  assert.match(normalized, /exactly one friendly camel/i);
  assert.match(normalized, /young facial proportions/);
  assert.match(normalized, /visibly riding/);
  assert.match(normalized, /medium-wide landscape/);
  assert.doesNotMatch(normalized, /still mid shot portrait photograph/);
  assert.equal(task.preservation, "identity");
  const fallback = sceneIdentity.scenePrompt({ ...task, prompt: "" });
  assert.match(fallback, /תעשה לי תמונה של הילדה הזו רוכבת על גמל/u);
});

test("scene contract is separately frozen while Contract B hash remains unchanged", () => {
  assert.equal(sceneIdentity.RELEASE, r.VERSION);
  assert.equal(sceneIdentity.CONTRACT_RELEASE, "0.9.0-preview.5");
  const crypto = require("node:crypto");
  const sceneFile = path.join(__dirname, "../human-identity-scene/config/SCENE-CONTRACT.json");
  const contractB = path.join(__dirname, "../human-identity/config/CONTRACT-B-FREEZE.json");
  const digest = file => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  assert.equal(digest(sceneFile), sceneIdentity.CONTRACT_SHA256);
  assert.equal(digest(contractB), "d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4");
  assert.notEqual(sceneIdentity.CONTRACT_SHA256, r.adapters.humanIdentity.CONTRACT_SHA256);
});

test("Scene C response integrity uses its frozen contract release, not the agent release", () => {
  const base = {
    schema_version: "aag.human-identity.scene.response.v1",
    request_id: "request-1",
    release: sceneIdentity.CONTRACT_RELEASE,
    scene_contract_sha256: sceneIdentity.CONTRACT_SHA256,
    contract_id: sceneIdentity.CONTRACT_ID,
  };
  assert.equal(sceneIdentity.validateResponse(base, "request-1"), base);
  assert.throws(
    () => sceneIdentity.validateResponse({ ...base, release: sceneIdentity.RELEASE }, "request-1"),
    error => error.code === "ENGINE_CRASH",
  );
});

function identitySource(originalSha256 = "0".repeat(64), extra = {}) {
  return { kind: "current_attachment", index: 1, original_sha256: originalSha256, normalized_sha256: "1".repeat(64), width: 896, height: 1152, ...extra };
}

test("identity source boundary distinguishes historical fixtures from trusted dynamic current attachments", () => {
  const human = r.adapters.humanIdentity;
  assert.deepEqual(
    human.classifySource({ _aag_source: identitySource("8b131e3030a094173004ae17df02b9fa94d523cb273398b027ea6bb31e1f2c61") }),
    { reference_kind: "historical_validation_fixture", fixture_id: "authorized-adult-01", domain: "adult" },
  );
  assert.deepEqual(
    human.classifySource({ _aag_source: identitySource("93665635711952c6a5da892bea90cc892b7c0a4a6748416e13a69ffd124eced6") }),
    { reference_kind: "historical_validation_fixture", fixture_id: "authorized-baby-01", domain: "baby" },
  );
  assert.deepEqual(
    human.classifySource({ request: "same person", _aag_source: identitySource("2".repeat(64)) }),
    { reference_kind: "trusted_runtime_reference", fixture_id: null, domain: "adult" },
  );
  assert.equal(human.classifySource({ request: "same girl", _aag_source: identitySource("3".repeat(64)) }).domain, "baby");
  assert.throws(() => human.classifySource({ _aag_source: identitySource("4".repeat(64), { path: "/tmp/person.png" }) }), error => error.code === "SOURCE_UNAUTHORIZED");
  assert.throws(() => human.classifySource({}), error => error.code === "SOURCE_REQUIRED");
});

test("identity staging is request-bound, hash-checked, private, and contains no caller path", () => {
  const root = temporary("aag-identity-stage-");
  const bytes = fakePng(5);
  const source = identitySource("5".repeat(64), { normalized_sha256: require("crypto").createHash("sha256").update(bytes).digest("hex"), width: 8, height: 8 });
  const target = r.adapters.humanIdentity.stageReference("11111111-1111-4111-8111-111111111111", { bytes, width: 8, height: 8, format: "png" }, source, { workspace_id: "w", thread_id: "t", user_id: "u", invocation_id: "i" }, root);
  assert.equal(target, path.join(root, "references", "11111111-1111-4111-8111-111111111111.png"));
  assert.equal(fs.statSync(target).mode & 0o777, 0o600);
  assert.deepEqual(fs.readFileSync(target), bytes);
  const provenance = JSON.parse(fs.readFileSync(path.join(root, "references", "11111111-1111-4111-8111-111111111111.provenance.json")));
  assert.deepEqual(provenance.caller, { workspace_id: "w", thread_id: "t", user_id: "u", invocation_id: "i" });
  assert.throws(() => r.adapters.humanIdentity.stageReference("22222222-2222-4222-8222-222222222222", { bytes, width: 8, height: 8, format: "png" }, { ...source, normalized_sha256: "f".repeat(64) }, owner, root), error => error.code === "SOURCE_CORRUPT");
});

test("legacy Comfy identity graph is unreachable from the active Contract B adapter", () => {
  assert.notEqual(r.adapters.humanIdentity, r.adapters.identity);
  assert.equal(r.adapters.humanIdentity.CONTRACT_ID, "structured-close-b");
  assert.equal(r.adapters.humanIdentity.CONTRACT_SHA256, "d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4");
});

test("trusted turn scope does not alter the frozen Human Identity caller schema", () => {
  const normalized = r.normalizeTask({ operation: "transform", request: "identity", preservation: "identity" }, owner);
  assert.deepEqual(r.adapters.humanIdentity.callerScope(normalized.owner), {
    workspace_id: "w1",
    thread_id: "t1",
    user_id: "u1",
    invocation_id: "i1",
  });
});

test("multiple current images require an explicit source index", async () => {
  const task = r.normalizeTask({ operation: "upscale", request: "x", source_policy: "current_attachment" }, owner);
  await assert.rejects(r.resolveSource(task, { ...owner, AAG_INVOCATION_ATTACHMENTS: [attachment(fakePng(1), "one.png"), attachment(fakePng(2), "two.png")] }, temporary(), mockDeps()), error => error.code === "SOURCE_AMBIGUOUS");
});

test("selected attachment is normalized and exclusively forwarded", async () => {
  const first = fakePng(1), second = fakePng(2);
  const task = r.normalizeTask({ operation: "upscale", request: "x", source_policy: "current_attachment", source_index: 2 }, owner);
  const selected = await r.resolveSource(task, { ...owner, AAG_INVOCATION_ATTACHMENTS: [attachment(first, "one.png"), attachment(second, "two.png")] }, temporary(), mockDeps());
  assert.equal(selected.source.index, 2);
  assert.equal(selected.source.original_sha256, require("crypto").createHash("sha256").update(second).digest("hex"));
  assert.equal(selected.runtime.AAG_INVOCATION_ATTACHMENTS.length, 1);
  assert.equal(selected.runtime.AAG_INVOCATION_ATTACHMENTS[0].name, `${selected.source.normalized_sha256}.png`);
});

test("source index is current-attachment-only and unsupported thread references fail closed", () => {
  for (const source_policy of ["auto", "previous_artifact"]) {
    assert.throws(
      () => r.normalizeTask({ operation: "transform", request: "edit source", prompt: productionPrompt("edit source"), preservation: "subject", source_policy, source_index: 1 }, owner),
      error => error.code === "INVALID_ARGUMENT" && /only with current_attachment/.test(error.message),
    );
  }
  assert.throws(
    () => r.normalizeTask({ operation: "transform", request: "edit older image", prompt: productionPrompt("edit older image"), preservation: "subject", source_policy: "thread_reference" }, owner),
    error => error.code === "INVALID_ARGUMENT",
  );
});

test("explicit current and previous policies resolve only their declared source collections", async () => {
  const root = temporary();
  const baseDeps = mockDeps();
  const generated = await r.createTask({ operation: "generate", request: "base image" }, runtime(root, "source-seed", "source-seed-turn"), { deps: baseDeps });
  assert.match(generated, /status=completed/);

  const currentBytes = fakePng(7);
  const currentRuntime = { ...runtime(root, "source-select", "source-select-current"), AAG_INVOCATION_ATTACHMENTS: [attachment(currentBytes, "current.png")] };
  const currentTask = r.normalizeTask({ operation: "transform", request: "edit the attached image", prompt: productionPrompt("edit the attached image"), preservation: "subject", source_policy: "current_attachment" }, currentRuntime);
  const current = await r.resolveSource(currentTask, currentRuntime, root, mockDeps());
  assert.equal(current.source.kind, "current_attachment");
  assert.equal(current.source.original_sha256, require("crypto").createHash("sha256").update(currentBytes).digest("hex"));

  const priorBytes = fakePng(9);
  const previousRuntime = { ...runtime(root, "source-select", "source-select-previous"), AAG_INVOCATION_ATTACHMENTS: [attachment(currentBytes, "current.png")] };
  const previousTask = r.normalizeTask({ operation: "transform", request: "edit the previous generated image", prompt: productionPrompt("edit the previous generated image"), preservation: "subject", source_policy: "previous_artifact" }, previousRuntime);
  const previous = await r.resolveSource(previousTask, previousRuntime, root, mockDeps({ sourceFetch: async () => response(priorBytes) }));
  assert.equal(previous.source.kind, "previous_artifact");
  assert.notEqual(previous.source.original_sha256, require("crypto").createHash("sha256").update(currentBytes).digest("hex"));
  assert.ok(previous.source.artifact_id);
});

test("missing explicit sources and invalid source indexes create zero jobs", async () => {
  for (const testCase of [
    { name: "current", args: { operation: "transform", request: "edit attached image", preservation: "subject", source_policy: "current_attachment" } },
    { name: "previous", args: { operation: "transform", request: "edit last image", preservation: "subject", source_policy: "previous_artifact" } },
    { name: "bad-index", args: { operation: "transform", request: "edit attached image", preservation: "subject", source_policy: "current_attachment", source_index: 9 } },
  ]) {
    const root = temporary(`aag-source-${testCase.name}-`);
    const result = await r.createTask(testCase.args, runtime(root, `missing-${testCase.name}`, `missing-${testCase.name}-turn`), { deps: mockDeps() });
    assert.match(result, /status=failed/);
    assert.equal(r.store.listJobs(root).length, 0);
  }
});

test("non-retryable source failures explicitly forbid a same-turn retry", async () => {
  const root = temporary("aag-source-no-retry-");
  const result = await r.createTask(
    {
      operation: "transform",
      request: "edit the last image",
      prompt: productionPrompt("edit the last image"),
      preservation: "subject",
      source_policy: "previous_artifact",
    },
    runtime(root, "missing-no-retry", "missing-no-retry-turn"),
    { deps: mockDeps() },
  );
  assert.match(result, /error_code=SOURCE_REQUIRED/);
  assert.match(result, /retryable=false/);
  assert.match(result, /same_turn_retry=forbidden/);
  assert.equal(r.store.listJobs(root).length, 0);
});

test("retryable engine failures still forbid an autonomous same-turn retry", async () => {
  const root = temporary("aag-engine-no-same-turn-retry-");
  const result = await r.createTask(
    { operation: "generate", request: "a detailed fox reading beside a lantern" },
    runtime(root, "engine-no-retry", "engine-no-retry-turn"),
    { deps: mockDeps({ execute: async () => { throw new AagError("ENGINE_UNAVAILABLE", "The selected local image engine is unavailable.", true); } }) },
  );
  assert.match(result, /error_code=ENGINE_UNAVAILABLE/);
  assert.match(result, /retryable=true/);
  assert.match(result, /same_turn_retry=forbidden/);
  assert.equal(r.store.listJobs(root).filter(job => !job.parent_job_id).length, 1);
});

test("ordinary non-human transforms use subject preservation", async () => {
  const root = temporary();
  let observed = null;
  const current = { ...runtime(root, "subject-session", "subject-turn"), AAG_INVOCATION_ATTACHMENTS: [attachment(fakePng(4), "product.png")] };
  const result = await r.createTask({ operation: "transform", request: "keep the same ceramic mug but make it watercolor", source_policy: "current_attachment", preservation: "subject" }, current, { deps: mockDeps({ execute: async task => { observed = task; return ["subject.png"]; } }) });
  assert.match(result, /status=completed/);
  assert.equal(observed.preservation, "subject");
  assert.match(r.store.listJobs(root).find(job => !job.parent_job_id).workflow_id, /^transform\.general\./);
});

test("rejects malformed base64, bad magic, deceptive extension, and decoder failure", async () => {
  assert.throws(() => image.parseAttachment({ name: "x.png", contentString: "data:image/png;base64,%%%%" }), error => error.code === "SOURCE_FORMAT_UNSUPPORTED" || error.code === "SOURCE_CORRUPT");
  assert.throws(() => image.parseAttachment(attachment(Buffer.alloc(160), "x.png")), error => error.code === "SOURCE_CORRUPT");
  assert.throws(() => image.parseAttachment(attachment(fakePng(), "x.jpg")), error => error.code === "SOURCE_FORMAT_UNSUPPORTED");
  const parsed = image.parseAttachment(attachment(fakePng()));
  await assert.rejects(image.normalizeBytes(parsed, { normalizeImage: async () => { throw new AagError("SOURCE_CORRUPT", "The attachment cannot be decoded safely."); } }), error => error.code === "SOURCE_CORRUPT");
});

test("valid multi-megabyte reference base64 does not overflow the validator stack", () => {
  const source = Buffer.alloc(4_701_000, 0x5a);
  source[0] = 0xff; source[1] = 0xd8; source[2] = 0xff;
  const decoded = image.strictBase64(source.toString("base64"));
  assert.equal(decoded.length, source.length);
  assert.equal(decoded.subarray(0, 3).toString("hex"), "ffd8ff");
  assert.throws(() => image.strictBase64("QU=JDRA="), error => error.code === "SOURCE_CORRUPT");
});

test("legal lifecycle matrix rejects illegal transitions", () => {
  const job = { status: "CREATED", transitions: [] };
  assert.throws(() => r.transition(job, "COMPLETED"), error => error.code === "ILLEGAL_STATE_TRANSITION");
  r.transition(job, "VALIDATED"); r.transition(job, "QUEUED"); r.transition(job, "RUNNING"); r.transition(job, "COMPLETED");
  assert.throws(() => r.transition(job, "FAILED"), error => error.code === "ILLEGAL_STATE_TRANSITION");
});

test("count two creates real child jobs, unique IDs/seeds/artifacts, sequential execution, and durable idempotency", async () => {
  const root = temporary();
  const deps = mockDeps();
  const first = await r.createTask({ operation: "generate", request: "two", count: 2, seed: 44 }, runtime(root), { deps });
  assert.match(first, /status=completed/); assert.match(first, /artifact_count=2/);
  const second = await r.createTask({ operation: "generate", request: "two", count: 2, seed: 44 }, runtime(root), { deps });
  assert.equal(second, first);
  const records = r.store.listJobs(root);
  assert.equal(records.length, 3);
  const parent = records.find(job => !job.parent_job_id);
  const children = parent.child_jobs.map(id => r.store.read(root, id));
  assert.equal(parent.status, "COMPLETED"); assert.equal(parent.artifacts.length, 2);
  assert.ok(children.every(job => job.status === "COMPLETED" && job.artifacts.length === 1));
  assert.notEqual(children[0].job_id, children[1].job_id); assert.notEqual(children[0].seed, children[1].seed);
  assert.notEqual(parent.artifacts[0].filename, parent.artifacts[1].filename); assert.notEqual(parent.artifacts[0].sha256, parent.artifacts[1].sha256);
  for (const record of records) assert.equal(fs.statSync(r.store.jobFile(root, record.job_id)).mode & 0o777, 0o600);
  assert.equal(fs.statSync(path.join(root, "jobs")).mode & 0o777, 0o700);
  assert.equal(fs.readdirSync(path.join(root, "jobs")).some(name => name.endsWith(".tmp")), false);
});

test("idempotency v2 separates three turns in one Agent session and deduplicates a same-turn retry", async () => {
  const root = temporary();
  let submissions = 0;
  const deps = mockDeps({ execute: async () => [`turn-${++submissions}.png`] });
  const firstRuntime = runtime(root, "persistent-session", "turn-a");
  const first = await r.createTask({ operation: "generate", request: "dog surfing", prompt: "a cheerful dog surfing" }, firstRuntime, { deps });
  const retry = await r.createTask({ operation: "generate", request: "dog surfing", prompt: "a cheerful dog surfing" }, firstRuntime, { deps });
  const second = await r.createTask({ operation: "generate", request: "red bicycle", prompt: "a red vintage bicycle beside a wall" }, runtime(root, "persistent-session", "turn-b"), { deps });
  const third = await r.createTask({ operation: "generate", request: "elephant coloring page", prompt: "a friendly elephant holding one balloon" }, runtime(root, "persistent-session", "turn-c"), { deps });
  const ids = [first, second, third].map(value => value.match(/job_id=(aag-[a-f0-9-]+)/)[1]);
  assert.equal(retry, first);
  assert.equal(new Set(ids).size, 3);
  assert.equal(submissions, 3);
  assert.equal(r.store.listJobs(root).filter(job => !job.parent_job_id).length, 3);
});

test("idempotency v2 separates operations and refreshes current attachments by turn", async () => {
  const root = temporary();
  let submissions = 0;
  const deps = mockDeps({ execute: async () => [`operation-${++submissions}.png`] });
  const generated = await r.createTask({ operation: "generate", request: "base" }, runtime(root, "session", "turn-generate"), { deps });
  const sourceOne = fakePng(4), sourceTwo = fakePng(8);
  const firstAttachmentRuntime = { ...runtime(root, "session", "turn-source-1"), AAG_INVOCATION_ATTACHMENTS: [attachment(sourceOne, "one.png")] };
  const secondAttachmentRuntime = { ...runtime(root, "session", "turn-source-2"), AAG_INVOCATION_ATTACHMENTS: [attachment(sourceTwo, "two.png")] };
  const upscaled = await r.createTask({ operation: "upscale", request: "upscale", source_policy: "current_attachment", scale: 2 }, firstAttachmentRuntime, { deps });
  const transformed = await r.createTask({ operation: "transform", request: "restyle", source_policy: "current_attachment", preservation: "subject" }, secondAttachmentRuntime, { deps });
  const parentIds = [generated, upscaled, transformed].map(value => value.match(/job_id=(aag-[a-f0-9-]+)/)[1]);
  assert.equal(new Set(parentIds).size, 3);
  assert.equal(submissions, 3);
  const parents = r.store.listJobs(root).filter(job => !job.parent_job_id);
  assert.notEqual(parents.find(job => job.operation === "upscale").source.original_sha256, parents.find(job => job.operation === "transform").source.original_sha256);
});

test("trusted dynamic identity retry is idempotent and never routes through subject preservation", async () => {
  const root = temporary();
  let submissions = 0;
  let observed = null;
  const deps = mockDeps({ execute: async task => { submissions += 1; observed = task; return ["identity-dynamic.png"]; } });
  const current = { ...runtime(root, "identity-session", "identity-turn"), AAG_INVOCATION_ATTACHMENTS: [attachment(fakePng(6), "new-person.png")] };
  const args = { operation: "transform", request: "Use the same person", source_policy: "current_attachment", source_index: 1, preservation: "identity", count: 1 };
  const first = await r.createTask(args, current, { deps });
  const retry = await r.createTask(args, current, { deps });
  assert.equal(first, retry);
  assert.equal(submissions, 1);
  assert.equal(observed.preservation, "identity");
  assert.equal(r.store.listJobs(root).find(job => !job.parent_job_id).workflow_id, "transform.human.identity.portrait.v1");
});

test("idempotency v2 isolates thread and workspace boundaries", async () => {
  const root = temporary();
  let submissions = 0;
  const deps = mockDeps({ execute: async () => [`boundary-${++submissions}.png`] });
  const base = runtime(root, "session", "turn");
  const first = await r.createTask({ operation: "generate", request: "one" }, base, { deps });
  const second = await r.createTask({ operation: "generate", request: "two" }, { ...base, AAG_THREAD_ID: "t2" }, { deps });
  const third = await r.createTask({ operation: "generate", request: "three" }, { ...base, AAG_WORKSPACE_ID: "w2" }, { deps });
  const ids = [first, second, third].map(value => value.match(/job_id=(aag-[a-f0-9-]+)/)[1]);
  assert.equal(new Set(ids).size, 3);
  assert.equal(submissions, 3);
  assert.throws(() => r.jobAction({ action: "status", job_id: ids[0] }, { ...base, AAG_WORKSPACE_ID: "w2" }), error => error.code === "JOB_NOT_AUTHORIZED");
});

test("child 1 success and child 2 failure preserves partial provenance and never completes parent", async () => {
  const root = temporary(); let calls = 0;
  const deps = mockDeps({ execute: async () => { calls += 1; if (calls === 2) throw new Error("second child crash /mnt/data/private/token"); return ["partial-one.png"]; } });
  const out = await r.createTask({ operation: "generate", request: "partial", count: 2 }, runtime(root, "partial"), { deps });
  assert.match(out, /status=failed/); assert.match(out, /artifact_count=1/); assert.match(out, /partial=true/); assert.doesNotMatch(out, /mnt\/data|second child crash/);
  const parent = r.store.listJobs(root).find(job => !job.parent_job_id);
  assert.equal(parent.status, "FAILED"); assert.equal(parent.artifacts.length, 1);
  const children = parent.child_jobs.map(id => r.store.read(root, id));
  assert.deepEqual(children.map(child => child.status), ["COMPLETED", "FAILED"]);
});

test("failure of child 1 cancels unstarted child 2", async () => {
  const root = temporary();
  const out = await r.createTask({ operation: "generate", request: "fail first", count: 2 }, runtime(root, "failfirst"), { deps: mockDeps({ execute: async () => { throw new Error("process exited"); } }) });
  assert.match(out, /error_code=ENGINE_CRASH/);
  const parent = r.store.listJobs(root).find(job => !job.parent_job_id);
  assert.deepEqual(parent.child_jobs.map(id => r.store.read(root, id).status), ["FAILED", "CANCELLED"]);
});

test("missing, invalid, and duplicate outputs prevent false completion", async () => {
  for (const [invocation, execute, fetch, expected] of [
    ["missing", async () => [], null, "OUTPUT_INVALID"],
    ["invalid", async () => ["bad.png"], async () => response(Buffer.alloc(12)), "OUTPUT_INVALID"],
  ]) {
    const root = temporary();
    const out = await r.createTask({ operation: "generate", request: "x" }, runtime(root, invocation), { deps: mockDeps({ execute, fetch: fetch || undefined }) });
    assert.match(out, new RegExp(`error_code=${expected}`)); assert.doesNotMatch(out, /status=completed/);
  }
  const root = temporary();
  const out = await r.createTask({ operation: "generate", request: "dupe", count: 2 }, runtime(root, "dupe"), { deps: mockDeps({ execute: async () => ["same.png"], fetch: async () => response(fakePng(7)) }) });
  assert.match(out, /error_code=OUTPUT_COLLISION/); assert.match(out, /artifact_count=1/);
});

test("queued cancellation is owner-scoped and observed by waiting scheduler", async () => {
  const root = temporary();
  const deps = mockDeps();
  deps.scheduler.engineActivity = async () => ({ active: true, comfy_running: 1 });
  const pending = r.createTask({ operation: "generate", request: "wait" }, runtime(root, "queued"), { deps });
  const parent = await waitFor(() => r.store.listJobs(root).find(job => !job.parent_job_id && job.status === "QUEUED"));
  const cancelled = r.jobAction({ action: "cancel", job_id: parent.job_id }, runtime(root, "other-invocation"));
  assert.match(cancelled, /status=cancelled/);
  const completed = await pending;
  assert.match(completed, /status=cancelled/); assert.match(completed, /error_code=JOB_CANCELLED/);
  assert.equal(r.store.read(root, parent.child_jobs[0]).status, "CANCELLED");
});

test("running cancellation returns stable unsupported status without pretending to cancel", async () => {
  const root = temporary(); let finish;
  const deps = mockDeps({ execute: () => new Promise(resolve => { finish = () => resolve(["running.png"]); }) });
  const pending = r.createTask({ operation: "generate", request: "running" }, runtime(root, "running"), { deps });
  const parent = await waitFor(() => r.store.listJobs(root).find(job => !job.parent_job_id && job.status === "RUNNING"));
  assert.throws(() => r.jobAction({ action: "cancel", job_id: parent.job_id }, runtime(root, "status")), error => error.code === "CANCEL_NOT_SUPPORTED");
  finish(); assert.match(await pending, /status=completed/);
});

test("terminal cancellation and cross-thread status are rejected", async () => {
  const root = temporary();
  const out = await r.createTask({ operation: "generate", request: "done" }, runtime(root, "done"), { deps: mockDeps() });
  const id = out.match(/job_id=(aag-[a-f0-9-]+)/)[1];
  assert.throws(() => r.jobAction({ action: "cancel", job_id: id }, runtime(root, "later")), error => error.code === "JOB_ALREADY_TERMINAL");
  assert.throws(() => r.jobAction({ action: "status", job_id: id }, { ...runtime(root), AAG_THREAD_ID: "other" }), error => error.code === "JOB_NOT_AUTHORIZED");
  assert.match(r.jobAction({ action: "status", job_id: id }, runtime(root, "later")), /status=completed/);
});

test("engine timeout maps to TIMED_OUT and next job succeeds", async () => {
  const root = temporary();
  const timed = await r.createTask({ operation: "generate", request: "timeout" }, runtime(root, "timeout"), { deps: mockDeps({ execute: async () => { throw new DOMException("timed out", "AbortError"); } }) });
  assert.match(timed, /status=timed_out/); assert.match(timed, /error_code=ENGINE_TIMEOUT/);
  const next = await r.createTask({ operation: "generate", request: "next" }, runtime(root, "next"), { deps: mockDeps() });
  assert.match(next, /status=completed/);
});

test("stale nonterminal state is recovered without touching terminal jobs", () => {
  const root = temporary();
  const job = r.store.createRecord(root, { release: r.VERSION, owner: r.normalizeTask({ operation: "generate", request: "x" }, owner).owner, operation: "generate", workflow_id: "generation.text.fast.v1" });
  r.store.transition(job, "VALIDATED"); r.store.transition(job, "QUEUED"); job.updated_at = "2020-01-01T00:00:00.000Z"; r.store.write(root, job); job.updated_at = "2020-01-01T00:00:00.000Z"; fs.writeFileSync(r.store.jobFile(root, job.job_id), JSON.stringify(job));
  const recovered = r.store.recoverStale(root, 1000);
  assert.equal(recovered.length, 1); assert.equal(r.store.read(root, job.job_id).error.code, "STALE_STATE_RECOVERED");
});

test("new task automatically recovers restart-stale state and persists bounded engine metadata", async () => {
  const root = temporary();
  const stale = r.store.createRecord(root, { release: r.VERSION, owner: r.normalizeTask({ operation: "generate", request: "old" }, owner).owner, operation: "generate", workflow_id: "generation.text.fast.v1" });
  r.store.transition(stale, "VALIDATED"); r.store.transition(stale, "QUEUED");
  stale.updated_at = "2020-01-01T00:00:00.000Z"; fs.writeFileSync(r.store.jobFile(root, stale.job_id), JSON.stringify(stale));
  const deps = mockDeps({ execute: async (_task, _normalized, _runtime, _context, _token, adapterDeps) => {
    adapterDeps.onEngineMetadata({ adapter: "test-engine", prompt_id: "prompt-123", secret: "must-not-persist", elapsed_seconds: 1.25 });
    return ["metadata.png"];
  } });
  const out = await r.createTask({ operation: "generate", request: "new" }, runtime(root, "metadata"), { deps });
  assert.match(out, /status=completed/);
  assert.equal(r.store.read(root, stale.job_id).error.code, "STALE_STATE_RECOVERED");
  const parent = r.store.listJobs(root).find(job => !job.parent_job_id && job.job_id !== stale.job_id);
  const child = r.store.read(root, parent.child_jobs[0]);
  assert.deepEqual(child.engine, { adapter: "test-engine", prompt_id: "prompt-123", elapsed_seconds: 1.25 });
});

test("previous-artifact follow-up is scoped and re-normalized", async () => {
  const root = temporary();
  const generated = await r.createTask({ operation: "generate", request: "first" }, runtime(root, "first"), { deps: mockDeps() });
  assert.match(generated, /status=completed/);
  const sourceBytes = fakePng(9);
  const deps = mockDeps({ sourceFetch: async () => response(sourceBytes) });
  deps.adapters.fetch = async url => String(url).includes("result-") ? response(fakePng(3)) : response(sourceBytes);
  const followRuntime = runtime(root, "follow", "follow-turn");
  const transformed = await r.createTask({ operation: "transform", request: "follow", preservation: "subject", source_policy: "previous_artifact" }, followRuntime, { deps });
  const retry = await r.createTask({ operation: "transform", request: "follow", preservation: "subject", source_policy: "previous_artifact" }, followRuntime, { deps });
  assert.match(transformed, /status=completed/);
  assert.equal(retry, transformed);
  const parent = r.store.listJobs(root).find(job => !job.parent_job_id && job.operation === "transform");
  assert.equal(parent.source.kind, "previous_artifact");
  assert.equal(r.store.listJobs(root).filter(job => !job.parent_job_id && job.operation === "transform").length, 1);
});

test("error and token redaction never exposes internal paths or secrets", () => {
  const value = redact("failed /mnt/data/AI/private/file sk-proj-TESTFIXTURE authorization: Bearer-test AIzaTESTFIXTURE");
  assert.doesNotMatch(value, /\/mnt\/data|sk-proj|Bearer-secret|AIzaABCDEFGHIJKLMNOP/);
  const mapped = r.classifyError(new Error("boom /app/server/storage/x"));
  assert.equal(mapped.code, "INTERNAL_ERROR"); assert.equal(mapped.message, "The image task failed safely."); assert.doesNotMatch(mapped.message, /app\/server/);
});

test("provider schemas are shallow, activated, preview-labelled, identity-truthful, and forbid extra fields", () => {
  for (const name of ["aag-image-task", "aag-image-job"]) {
    const plugin = require(`../skills/${name}/plugin.json`);
    assert.equal(plugin.active, true); assert.equal(plugin.version, r.VERSION);
    for (const value of Object.values(plugin.entrypoint.params)) assert.ok(["string", "number"].includes(value.type));
    if (name === "aag-image-task") {
      assert.match(plugin.description, /same-person/);
      assert.match(plugin.description, /current human attachment/);
      assert.match(plugin.description, /artifact_N_url/);
      assert.match(plugin.description, /never report success without the artifact URL/);
      assert.doesNotMatch(plugin.description, /Preview-only/);
    }
  }
  for (const name of ["provider-task.schema.json", "provider-job.schema.json"]) assert.equal(require(`../schemas/${name}`).additionalProperties, false);
  assert.deepEqual(require("../schemas/provider-task.schema.json").required, ["operation", "prompt", "source_policy", "preservation"]);
});
