"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const scheduler = require("../src/scheduler");

function root() { return fs.mkdtempSync(path.join(os.tmpdir(), "aag-scheduler-")); }
function job(id, operation = "generate") { return { job_id: `aag-00000000-0000-4000-8000-${String(id).padStart(12, "0")}`, operation, owner: { workspace_id: "w", thread_id: "t", user_id: "u" } }; }
const idle = { engineActivity: async () => ({ active: false }), disableHeartbeat: true, sleep: ms => new Promise(resolve => setTimeout(resolve, Math.min(ms, 3))) };

test("shared filesystem lease has owner metadata and excludes a second active owner", async () => {
  const state = root();
  const first = await scheduler.acquire(state, job(1), { waitMs: 200, staleMs: 100, pollMs: 2, deps: idle });
  const owner = scheduler.readOwner(state);
  assert.equal(owner.job_id, job(1).job_id); assert.equal(owner.kind, "agent"); assert.equal(owner.token, first.token);
  await assert.rejects(scheduler.acquire(state, job(2), { waitMs: 30, staleMs: 100, pollMs: 2, deps: idle }), error => error.code === "QUEUE_WAIT_TIMEOUT");
  assert.equal(first.release(), true);
  const second = await scheduler.acquire(state, job(2), { waitMs: 100, staleMs: 100, pollMs: 2, deps: idle });
  assert.equal(second.release(), true);
});

test("FIFO sequence is deterministic across queued waiters", async () => {
  const state = root();
  const holder = await scheduler.acquire(state, job(1), { waitMs: 200, staleMs: 1000, pollMs: 2, deps: idle });
  const order = [];
  const run = async id => {
    const lease = await scheduler.acquire(state, job(id), { waitMs: 500, staleMs: 1000, pollMs: 2, deps: idle });
    order.push(id); await new Promise(resolve => setTimeout(resolve, 5)); lease.release();
  };
  const second = run(2); await new Promise(resolve => setTimeout(resolve, 5)); const third = run(3);
  await new Promise(resolve => setTimeout(resolve, 10)); holder.release();
  await Promise.all([second, third]); assert.deepEqual(order, [2, 3]);
  const sequences = fs.readdirSync(path.join(state, "scheduler", "sequence")).sort();
  assert.deepEqual(sequences, ["00000000000000000001", "00000000000000000002", "00000000000000000003"]);
});

test("authorized external ComfyUI activity delays agent acquisition and is never killed", async () => {
  const state = root(); let checks = 0;
  const deps = { ...idle, engineActivity: async () => ({ active: ++checks < 4, comfy_running: checks < 4 ? 1 : 0 }) };
  const lease = await scheduler.acquire(state, job(1), { waitMs: 300, staleMs: 100, pollMs: 2, deps });
  assert.ok(checks >= 4); assert.ok(lease.waited_ms > 0); lease.release();
});

test("authorized external Upscale activity delays agent acquisition", async () => {
  const state = root(); let checks = 0;
  const deps = { ...idle, engineActivity: async () => ({ active: ++checks < 3, upscale_busy: checks < 3 }) };
  const lease = await scheduler.acquire(state, job(1, "upscale"), { waitMs: 300, staleMs: 100, pollMs: 2, deps });
  assert.ok(checks >= 3); lease.release();
});

test("stale owner is recovered only when both engines are idle", async () => {
  const state = root();
  fs.mkdirSync(scheduler.leaseRoot(state), { recursive: true, mode: 0o700 });
  fs.writeFileSync(scheduler.ownerFile(state), JSON.stringify({ token: "old", pid: 99999999, acquired_at: "2020-01-01T00:00:00.000Z", heartbeat_at: "2020-01-01T00:00:00.000Z" }), { mode: 0o600 });
  const held = await scheduler.reconcileStaleLease(state, { engineActivity: async () => ({ active: true, comfy_running: 1 }) }, 10);
  assert.equal(held.removed, false); assert.equal(fs.existsSync(scheduler.leaseRoot(state)), true);
  const recovered = await scheduler.reconcileStaleLease(state, { engineActivity: async () => ({ active: false }) }, 10);
  assert.equal(recovered.removed, true); assert.equal(fs.existsSync(scheduler.leaseRoot(state)), false);
});

test("orphan lease directory from owner crash is boundedly recovered", async () => {
  const state = root();
  fs.mkdirSync(scheduler.leaseRoot(state), { recursive: true, mode: 0o700 });
  const old = new Date(Date.now() - 5000); fs.utimesSync(scheduler.leaseRoot(state), old, old);
  const recovered = await scheduler.reconcileStaleLease(state, { engineActivity: async () => ({ active: false }) }, 10);
  assert.equal(recovered.removed, true); assert.equal(recovered.reason, "orphan-directory-idle");
});

test("stale crashed waiter is pruned and cannot block the FIFO", () => {
  const state = root(); const waiters = scheduler.queueRoot(state);
  fs.mkdirSync(waiters, { recursive: true });
  const stale = path.join(waiters, "00000000000000000001-11111111-1111-4111-8111-111111111111.json");
  fs.writeFileSync(stale, JSON.stringify({ ticket: "11111111-1111-4111-8111-111111111111", queued_at: "2020-01-01T00:00:00.000Z", heartbeat_at: "2020-01-01T00:00:00.000Z" }));
  assert.deepEqual(scheduler.orderedWaiters(state, 10), []);
  assert.equal(fs.existsSync(stale), false);
});

test("unsafe symlink lease path is rejected", async () => {
  const state = root(); const outside = root();
  fs.mkdirSync(path.join(state, "scheduler"), { recursive: true }); fs.symlinkSync(outside, scheduler.leaseRoot(state));
  await assert.rejects(scheduler.reconcileStaleLease(state, idle, 10), error => error.code === "XPU_LEASE_UNSAFE");
});

test("bounded queue rejects overflow with stable QUEUE_FULL", async () => {
  const state = root();
  const holder = await scheduler.acquire(state, job(1), { waitMs: 200, staleMs: 1000, pollMs: 2, maxQueue: 2, deps: idle });
  const waitingTwo = scheduler.acquire(state, job(2), { waitMs: 500, staleMs: 1000, pollMs: 2, maxQueue: 2, deps: idle });
  const waitingThree = scheduler.acquire(state, job(3), { waitMs: 500, staleMs: 1000, pollMs: 2, maxQueue: 2, deps: idle });
  await new Promise(resolve => setTimeout(resolve, 10));
  await assert.rejects(scheduler.acquire(state, job(4), { waitMs: 100, staleMs: 1000, pollMs: 2, maxQueue: 2, deps: idle }), error => error.code === "QUEUE_FULL");
  holder.release();
  const second = await waitingTwo; second.release();
  const third = await waitingThree; third.release();
});
