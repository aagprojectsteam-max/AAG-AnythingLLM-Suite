"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const r = require("../src/runtime");
const comfy = require("../src/comfy");
const colorize = require("../src/colorize");
const endpoints = require("../src/endpoints");

const owner = { AAG_WORKSPACE_ID: "w1", AAG_THREAD_ID: "t1", AAG_USER_ID: "u1", AAG_INVOCATION_UUID: "i1", AAG_TURN_ID: "turn-1", AAG_INVOCATION_PROMPT: "colorize this old photo" };
function fakePng(byte = 1) {
  const value = Buffer.alloc(160, byte);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(value);
  value.writeUInt32BE(8, 16); value.writeUInt32BE(8, 20);
  return value;
}
const attachment = () => ({ name: "old.png", mime: "image/png", contentString: `data:image/png;base64,${fakePng().toString("base64")}` });
const withAttachment = { ...owner, AAG_INVOCATION_ATTACHMENTS: [attachment()] };

test("colorize is a closed operation with restoration semantics", () => {
  const task = r.normalizeTask({ operation: "colorize", prompt: "colorize naturally", source_policy: "current_attachment", preservation: "none" }, withAttachment);
  assert.equal(task.operation, "colorize");
  assert.equal(task.preservation, "none");
  assert.equal(task.count, 1);
  assert.equal(r.workflow(task), "colorize.restore.auto.v1");
  const auto = r.normalizeTask({ operation: "colorize", prompt: "colorize", source_policy: "auto", preservation: "auto" }, withAttachment);
  assert.equal(auto.preservation, "none");
});

test("colorize rejects creative, framing and multi-output arguments", () => {
  const base = { operation: "colorize", prompt: "colorize", source_policy: "current_attachment", preservation: "none" };
  for (const bad of [
    { ...base, scale: 2 },
    { ...base, preservation: "identity" },
    { ...base, preservation: "subject" },
    { ...base, width: 512, height: 512 },
    { ...base, aspect_ratio: "16:9" },
    { ...base, count: 2 },
  ]) {
    assert.throws(() => r.normalizeTask(bad, withAttachment), error => error.code === "INVALID_ARGUMENT", JSON.stringify(bad));
  }
});

test("colorize keeps the existing ownership boundary", () => {
  assert.throws(
    () => r.normalizeTask({ operation: "colorize", prompt: "colorize", source_policy: "current_attachment", preservation: "none" }, { ...withAttachment, AAG_WORKSPACE_ID: "unknown" }),
    error => error.code === "OWNER_SCOPE_REQUIRED"
  );
});

test("colorize profiles, graph and stall classification are bounded and prompt-free", () => {
  assert.equal(colorize.profileFor({ quality: "quality" }).checkpoint, "ddcolor_modelscope.pth");
  assert.equal(colorize.profileFor({ quality: "fast" }).checkpoint, "ddcolor_paper_tiny.pth");
  assert.equal(colorize.profileFor({}).checkpoint, "ddcolor_paper_tiny.pth");
  const graph = colorize.graph({ sourceName: "AAG-Image-Agent-Source/x.png", profile: colorize.PROFILES.quality, prefix: "COLOR-test" });
  assert.deepEqual(Object.values(graph).map(node => node.class_type), ["LoadImage", "DDColor_Colorize", "SaveImage"]);
  assert.equal(graph["2"].inputs.model_input_size, 1024);
  assert.ok(!JSON.stringify(graph).includes("CLIPTextEncode"), "no text conditioning in a restoration graph");
  assert.equal(comfy.workflowClass({ operation: "colorize" }), "colorize");
  assert.equal(comfy.enginePhase({ current_engine_node_class: "DDColor_Colorize" }), "sample");
  assert.ok(comfy.STALL_PROFILES.colorize.sample >= 60_000);
});

test("colorize execute drives the engine without a prompt and publishes through the hub", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const path = String(url).replace(endpoints.COMFY, "").replace(endpoints.HUB_INTERNAL, "HUB");
    calls.push(`${options.method || "GET"} ${path}`);
    const reply = body => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });
    if (path === "/object_info") return reply({ LoadImage: {}, SaveImage: {}, DDColor_Colorize: { input: { required: { checkpoint: [["ddcolor_paper_tiny.pth", "ddcolor_modelscope.pth"]] } } } });
    if (path === "/upload/image") return reply({ name: "old.png", subfolder: "AAG-Image-Agent-Source" });
    if (path === "/prompt") {
      const graph = JSON.parse(options.body).prompt;
      assert.equal(graph["2"].inputs.checkpoint, "ddcolor_paper_tiny.pth");
      assert.ok(!JSON.stringify(graph).toLowerCase().includes("prompt"), "graph carries no prompt text");
      return reply({ prompt_id: "p-1" });
    }
    if (path.startsWith("/history/")) return reply({ "p-1": { outputs: { "3": { images: [{ filename: "COLOR-1.png", subfolder: "", type: "output" }] } } } });
    if (path === "HUB/import-comfyui") return reply({ ok: true, filename: "REF-COLOR-1.png" });
    throw new Error("unexpected " + path);
  };
  const details = [];
  const outputs = await colorize.execute(
    { operation: "colorize", quality: "fast", request: "colorize this old photo", prompt: "colorize" },
    { bytes: fakePng(), width: 8, height: 8, format: "png" },
    "lease-1",
    { fetch: fetchImpl, historyPollMs: 1, sleep: async () => {}, onExecutionDetails: value => details.push(value), onFinalPrompt: value => details.push(value) }
  );
  assert.deepEqual(outputs, ["REF-COLOR-1.png"]);
  assert.ok(calls.includes("POST /upload/image") && calls.includes("POST /prompt") && calls.includes("POST HUB/import-comfyui"));
  assert.ok(details.some(d => d.boundary === "colorize.no-prompt" && d.prompt === ""));
  assert.ok(details.some(d => d.model_family === "DDColor" && d.image_model === "ddcolor_paper_tiny.pth"));
});

test("colorize fails closed when the engine lacks the node or checkpoint", async () => {
  const fetchImpl = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ LoadImage: {}, SaveImage: {} }) });
  await assert.rejects(
    colorize.execute({ operation: "colorize", quality: "fast" }, { bytes: fakePng(), width: 8, height: 8 }, "lease", { fetch: fetchImpl }),
    error => error.code === "CAPABILITY_UNAVAILABLE"
  );
});
