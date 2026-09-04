"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const batch = require("../src/batch");
const runtimeCore = require("../src/runtime");

function temporary() { return fs.mkdtempSync(path.join(os.tmpdir(), "aag-batch-test-")); }
function fakePng(byte = 1) {
  const value = Buffer.alloc(160, byte);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(value);
  value.writeUInt32BE(8, 16);
  value.writeUInt32BE(8, 20);
  return value;
}
function response(bytes) {
  return { ok: true, status: 200, headers: { get: () => "image/png" }, arrayBuffer: async () => bytes };
}
function trusted(root, turn = "turn-1", prompt = "Create exactly three distinct illustrated city parks as a coherent series") {
  return {
    AAG_WORKSPACE_ID: "workspace-1",
    AAG_THREAD_ID: "thread-1",
    AAG_USER_ID: "user-1",
    AAG_INVOCATION_UUID: "invocation-1",
    AAG_TURN_ID: turn,
    AAG_INVOCATION_PROMPT: prompt,
    AAG_IMAGE_AGENT_STATE_ROOT: root,
    AAG_IMAGE_QUEUE_TIMEOUT_MS: 1000,
    AAG_IMAGE_LEASE_STALE_MS: 5000,
    AAG_IMAGE_QUEUE_POLL_MS: 25,
  };
}
function item(name, aspect_ratio = "auto") {
  return {
    prompt: `A polished professional illustration of ${name} in a clearly established city park environment. Show the primary subject actively interacting with the setting, readable relationships, believable physical contact and coherent spatial perspective. Use purposeful composition, clear framing, balanced proportions, strong subject separation, scene-appropriate lighting and shadows, refined environmental detail, consistent materials, expressive color and sufficient professional visual clarity throughout the complete image.`,
    aspect_ratio,
  };
}
function args(count = 3) {
  return {
    operation: "multi_generate",
    collection_brief: `Create exactly ${count} distinct illustrated city parks as a coherent ordered series with consistent visual language.`,
    count,
    quality: "auto",
    items: Array.from({ length: count }, (_, index) => item(`park scene ${index + 1}`, index % 2 ? "portrait" : "landscape")),
  };
}
function deps(options = {}) {
  let call = 0;
  const bytes = new Map();
  return {
    scheduler: { engineActivity: async () => ({ active: false }), disableHeartbeat: true, sleep: async () => {} },
    adapters: {
      execute: async (task) => {
        call += 1;
        if (options.failCalls?.has(call)) throw new Error("process exited");
        if (typeof options.blockCall === "function") await options.blockCall(call, task);
        const filename = `batch-${call}-${task._aag_child_job_id}.png`;
        bytes.set(filename, fakePng(call + (options.byteOffset || 0)));
        return [filename];
      },
      fetch: async (url) => response(bytes.get(decodeURIComponent(String(url).split("/").pop())) || fakePng(99)),
      inspectOutput: async () => ({ width: 8, height: 8, format: "png" }),
    },
    calls: () => call,
  };
}

test("batch public normalization fails closed on bounds, missing fields, count mismatch, and unknown fields", () => {
  const root = temporary();
  const base = trusted(root);
  assert.equal(batch.normalizeBatch(args(3), base).items.length, 3);
  for (const bad of [
    { ...args(3), count: 1 },
    { ...args(3), count: 11 },
    { ...args(3), items: args(3).items.slice(0, 2) },
    { ...args(3), style: "storybook" },
    { ...args(3), quality: "maximum" },
  ]) assert.throws(() => batch.normalizeBatch(bad, base));
  const missingQuality = args(3);
  delete missingQuality.quality;
  assert.throws(() => batch.normalizeBatch(missingQuality, base), /quality is required/);
  const itemStyle = args(3);
  itemStyle.items[0].style = "watercolor";
  assert.throws(() => batch.normalizeBatch(itemStyle, base), /unsupported argument/);
});

test("one parent creates exactly N stable ordered children and same-turn duplicate executes nothing twice", async () => {
  const root = temporary();
  const mock = deps();
  const first = await batch.createBatch(args(3), trusted(root), { deps: mock });
  const second = await batch.createBatch(args(3), trusted(root), { deps: mock });
  assert.equal(first.job.status, "COMPLETED");
  assert.equal(second.idempotent, true);
  assert.equal(second.job.job_id, first.job.job_id);
  assert.equal(mock.calls(), 3);
  const parent = runtimeCore.store.read(root, first.job.job_id);
  assert.equal(parent.requested_count, 3);
  assert.equal(parent.child_jobs.length, 3);
  const children = parent.child_jobs.map((id) => runtimeCore.store.read(root, id));
  assert.deepEqual(children.map((child) => child.logical_child_id), ["item-0001", "item-0002", "item-0003"]);
  assert.deepEqual(children.map((child) => child.status), ["COMPLETED", "COMPLETED", "COMPLETED"]);
  assert.equal(new Set(children.map((child) => child.job_id)).size, 3);
  assert.ok(children.every((child) => Number.isInteger(child.seed) && child.seed >= 0 && child.seed <= 2_147_483_647));
  assert.equal(new Set(children.map((child) => child.seed)).size, 3);
  assert.equal(parent.artifacts.length, 3);
  assert.deepEqual(parent.artifacts.map((artifact) => artifact.child_job_id), children.map((child) => child.job_id));
  assert.match(batch.resultEnvelope(root, parent), /requested_count=3[\s\S]*completed_count=3[\s\S]*batch_export_ready=true/);
});

test("partial failure preserves siblings and explicit new-turn resume runs only failed children", async () => {
  const root = temporary();
  const firstDeps = deps({ failCalls: new Set([2]) });
  const first = await batch.createBatch(args(3), trusted(root), { deps: firstDeps });
  assert.equal(first.job.status, "PARTIAL");
  assert.equal(firstDeps.calls(), 3);
  let children = first.job.child_jobs.map((id) => runtimeCore.store.read(root, id));
  assert.deepEqual(children.map((child) => child.status), ["COMPLETED", "FAILED", "COMPLETED"]);
  const preserved = [children[0].artifacts[0].sha256, children[2].artifacts[0].sha256];
  const resumeDeps = deps({ byteOffset: 20 });
  const resumed = await batch.resumeBatch(first.job.job_id, trusted(root, "turn-2"), { deps: resumeDeps });
  assert.equal(resumed.status, "COMPLETED");
  assert.equal(resumeDeps.calls(), 1);
  children = resumed.child_jobs.map((id) => runtimeCore.store.read(root, id));
  assert.deepEqual([children[0].artifacts[0].sha256, children[2].artifacts[0].sha256], preserved);
  assert.deepEqual(children.map((child) => child.attempts.length), [1, 2, 1]);
  assert.equal(resumed.artifacts.length, 3);
});

test("running cancellation preserves completed child and prevents all remaining executions", async () => {
  const root = temporary();
  let releaseSecond;
  const mock = deps({ blockCall: (call) => call === 2 ? new Promise((resolve) => { releaseSecond = resolve; }) : undefined });
  const pending = batch.createBatch(args(3), trusted(root), { deps: mock });
  while (!releaseSecond) await new Promise((resolve) => setTimeout(resolve, 2));
  const parent = runtimeCore.store.listJobs(root).find((job) => job.operation === "multi_generate");
  const cancellation = runtimeCore.jobAction({ action: "cancel", job_id: parent.job_id }, trusted(root, "cancel-turn"));
  assert.match(cancellation, /cancellation_requested=true/);
  releaseSecond();
  const result = await pending;
  assert.equal(result.job.status, "CANCELLED");
  assert.equal(mock.calls(), 2);
  const children = result.job.child_jobs.map((id) => runtimeCore.store.read(root, id));
  assert.deepEqual(children.map((child) => child.status), ["COMPLETED", "COMPLETED", "CANCELLED"]);
  assert.equal(result.job.artifacts.length, 2);
});

test("provider-neutral batch execution has no provider/model branch and preserves prompts unchanged", async () => {
  const source = fs.readFileSync(path.join(__dirname, "../src/batch.js"), "utf8");
  assert.doesNotMatch(source, /(?:provider|model)\s*(?:===|==|includes\s*\()/i);
  const root = temporary();
  const observed = [];
  const mock = deps();
  const original = mock.adapters.execute;
  mock.adapters.execute = async (task, ...rest) => { observed.push(task.prompt); return original(task, ...rest); };
  const planned = args(2);
  await batch.createBatch(planned, trusted(root, "neutral", "Create exactly two distinct illustrated city parks as a coherent series"), { deps: mock });
  assert.deepEqual(observed, planned.items.map((entry) => entry.prompt));
});
