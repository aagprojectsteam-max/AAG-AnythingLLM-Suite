"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError } = require("./errors");
const { atomicJson, ensureDirectory, readJson, sleep } = require("./util");

const DEFAULT_WAIT_MS = 30 * 60 * 1000;
const DEFAULT_STALE_MS = 2 * 60 * 1000;
const DEFAULT_MAX_QUEUE = 8;

function schedulerRoot(root) { return path.join(root, "scheduler"); }
function queueRoot(root) { return path.join(schedulerRoot(root), "waiters"); }
function leaseRoot(root) { return path.join(schedulerRoot(root), "lease"); }
function ownerFile(root) { return path.join(leaseRoot(root), "owner.json"); }

function nextSequence(root) {
  const dir = path.join(schedulerRoot(root), "sequence");
  ensureDirectory(dir);
  for (;;) {
    const values = fs.readdirSync(dir).filter(name => /^\d{20}$/.test(name)).map(Number);
    const next = (values.length ? Math.max(...values) : 0) + 1;
    const name = String(next).padStart(20, "0");
    try { fs.mkdirSync(path.join(dir, name), { mode: 0o700 }); return name; }
    catch (error) { if (error?.code !== "EEXIST") throw error; }
  }
}

function queueName(sequence, ticket) { return `${sequence}-${ticket}.json`; }

function enqueue(root, job, maxQueue = DEFAULT_MAX_QUEUE, staleMs = DEFAULT_STALE_MS) {
  const dir = queueRoot(root); ensureDirectory(dir);
  if (orderedWaiters(root, staleMs).length >= maxQueue) throw new AagError("QUEUE_FULL", "The bounded image queue is full.", true);
  const ticket = crypto.randomUUID();
  const sequence = nextSequence(root);
  const file = path.join(dir, queueName(sequence, ticket));
  const now = new Date().toISOString();
  atomicJson(file, { ticket, sequence, job_id: job.job_id, owner: job.owner, pid: process.pid, queued_at: now, heartbeat_at: now });
  return { ticket, file, queued_at_ms: Date.now() };
}

function touchWaiter(queued) {
  const value = readJson(queued.file);
  if (value.ticket !== queued.ticket) throw new AagError("XPU_QUEUE_LOST", "The shared XPU queue ticket was lost.", true);
  value.heartbeat_at = new Date().toISOString();
  atomicJson(queued.file, value);
}

function orderedWaiters(root, staleMs = DEFAULT_STALE_MS) {
  const dir = queueRoot(root);
  try {
    const names = fs.readdirSync(dir).filter(name => /^\d{20}-[a-f0-9-]{36}\.json$/.test(name)).sort();
    return names.filter(name => {
      const file = path.join(dir, name);
      try {
        const value = readJson(file);
        const heartbeat = Date.parse(value.heartbeat_at || value.queued_at || "");
        if (Number.isFinite(heartbeat) && Date.now() - heartbeat > staleMs) { fs.unlinkSync(file); return false; }
        return true;
      } catch { return true; }
    });
  }
  catch (error) { if (error?.code === "ENOENT") return []; throw error; }
}

function pidAlive(pid) {
  if (!Number.isSafeInteger(Number(pid)) || Number(pid) <= 0) return false;
  try { process.kill(Number(pid), 0); return true; }
  catch (error) { return error?.code !== "ESRCH"; }
}

async function defaultEngineActivity() {
  const activity = { comfy_running: 0, comfy_pending: 0, upscale_busy: false, checked_at: new Date().toISOString(), reachable: {} };
  try {
    const response = await fetch("http://172.18.0.1:18188/queue", { signal: AbortSignal.timeout(3000) });
    if (response.ok) {
      const body = await response.json();
      activity.comfy_running = Array.isArray(body.queue_running) ? body.queue_running.length : 0;
      activity.comfy_pending = Array.isArray(body.queue_pending) ? body.queue_pending.length : 0;
      activity.reachable.comfyui = true;
    }
  } catch { activity.reachable.comfyui = false; }
  try {
    const response = await fetch("http://172.18.0.1:18191/health", { signal: AbortSignal.timeout(3000) });
    if (response.ok) {
      const body = await response.json();
      activity.upscale_busy = Boolean(body.busy);
      activity.reachable.upscale = true;
    }
  } catch { activity.reachable.upscale = false; }
  activity.active = activity.comfy_running > 0 || activity.comfy_pending > 0 || activity.upscale_busy;
  return activity;
}

function readOwner(root) {
  try { return readJson(ownerFile(root)); }
  catch (error) { if (error?.code === "ENOENT") return null; return { corrupt: true }; }
}

async function reconcileStaleLease(root, deps, staleMs) {
  const dir = leaseRoot(root);
  let stat;
  try { stat = fs.lstatSync(dir); } catch (error) { if (error?.code === "ENOENT") return { removed: false }; throw error; }
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new AagError("XPU_LEASE_UNSAFE", "The shared XPU lease path is unsafe.");
  const owner = readOwner(root);
  if (!owner || owner.corrupt) {
    const directoryAge = Date.now() - stat.mtimeMs;
    if (directoryAge <= staleMs) return { removed: false, owner, reason: "owner-being-created" };
    const activity = await (deps.engineActivity || defaultEngineActivity)();
    if (activity.active) return { removed: false, owner, activity, reason: "corrupt-owner-engine-active" };
    try { fs.unlinkSync(ownerFile(root)); } catch (error) { if (error?.code !== "ENOENT") throw error; }
    try { fs.rmdirSync(dir); } catch (error) { if (error?.code !== "ENOENT") throw error; }
    return { removed: true, owner, activity, reason: "orphan-directory-idle" };
  }
  const heartbeat = Date.parse(owner.heartbeat_at || owner.acquired_at || "");
  const stale = Number.isFinite(heartbeat) && Date.now() - heartbeat > staleMs;
  const alive = pidAlive(owner.pid);
  if (!stale) return { removed: false, owner, reason: "fresh" };
  const activity = await (deps.engineActivity || defaultEngineActivity)();
  if (activity.active) return { removed: false, owner, activity, reason: "engine-active" };
  try {
    fs.unlinkSync(ownerFile(root));
    fs.rmdirSync(dir);
    return { removed: true, owner, activity, reason: alive ? "stale-task-idle" : "dead-stale-idle" };
  } catch (error) {
    if (error?.code === "ENOENT") return { removed: true, owner, activity, reason: "already-removed" };
    throw new AagError("XPU_RESOURCE_BUSY", "The stale XPU lease could not be recovered safely.", true, error?.message);
  }
}

function heartbeat(root, token) {
  const owner = readOwner(root);
  if (!owner || owner.token !== token) throw new AagError("XPU_LEASE_LOST", "The shared XPU lease was lost.", true);
  owner.heartbeat_at = new Date().toISOString();
  atomicJson(ownerFile(root), owner);
}

function releaseLease(root, token) {
  const owner = readOwner(root);
  if (!owner || owner.token !== token || Number(owner.pid) !== process.pid) return false;
  try { fs.unlinkSync(ownerFile(root)); } catch (error) { if (error?.code !== "ENOENT") throw error; }
  try { fs.rmdirSync(leaseRoot(root)); } catch (error) { if (error?.code !== "ENOENT") throw error; }
  return true;
}

async function acquire(root, job, options = {}) {
  const waitMs = options.waitMs ?? DEFAULT_WAIT_MS;
  const staleMs = options.staleMs ?? DEFAULT_STALE_MS;
  const pollMs = options.pollMs ?? 250;
  const maxQueue = options.maxQueue ?? DEFAULT_MAX_QUEUE;
  const deps = options.deps || {};
  const queued = enqueue(root, job, maxQueue, staleMs);
  const deadline = Date.now() + waitMs;
  let lastActivity = null;
  let lastTouch = 0;
  try {
    for (;;) {
      if (options.isCancelled?.()) throw new AagError("JOB_CANCELLED", "The queued image job was cancelled.");
      if (Date.now() >= deadline) throw new AagError("QUEUE_WAIT_TIMEOUT", "The image job timed out while waiting for the shared XPU resource.", true);
      if (Date.now() - lastTouch > Math.max(1000, Math.floor(staleMs / 3))) { touchWaiter(queued); lastTouch = Date.now(); }
      const waiters = orderedWaiters(root, staleMs);
      if (!waiters.length || !waiters[0].endsWith(`${queued.ticket}.json`)) { await (deps.sleep || sleep)(pollMs); continue; }
      await reconcileStaleLease(root, deps, staleMs);
      if (fs.existsSync(leaseRoot(root))) { await (deps.sleep || sleep)(pollMs); continue; }
      lastActivity = await (deps.engineActivity || defaultEngineActivity)();
      if (lastActivity.active) { await (deps.sleep || sleep)(pollMs); continue; }
      try { fs.mkdirSync(leaseRoot(root), { mode: 0o700 }); }
      catch (error) { if (error?.code === "EEXIST") { await (deps.sleep || sleep)(pollMs); continue; } throw error; }
      const token = crypto.randomUUID();
      const now = new Date().toISOString();
      const owner = { schema_version: 1, token, kind: "agent", job_id: job.job_id, operation: job.operation, owner: job.owner, pid: process.pid, acquired_at: now, heartbeat_at: now };
      try {
        atomicJson(ownerFile(root), owner);
        const confirm = await (deps.engineActivity || defaultEngineActivity)();
        if (confirm.active) {
          releaseLease(root, token);
          lastActivity = confirm;
          await (deps.sleep || sleep)(pollMs);
          continue;
        }
        try { fs.unlinkSync(queued.file); } catch {}
        let timer = null;
        if (!deps.disableHeartbeat) timer = setInterval(() => { try { heartbeat(root, token); } catch {} }, Math.max(1000, Math.floor(staleMs / 3))).unref();
        return {
          token, owner, waited_ms: Date.now() - queued.queued_at_ms, activity_before: lastActivity,
          heartbeat: () => heartbeat(root, token),
          release: () => { if (timer) clearInterval(timer); return releaseLease(root, token); },
        };
      } catch (error) {
        try { releaseLease(root, token); } catch {}
        throw error;
      }
    }
  } catch (error) {
    try { fs.unlinkSync(queued.file); } catch {}
    throw error;
  }
}

module.exports = {
  DEFAULT_WAIT_MS, DEFAULT_STALE_MS, DEFAULT_MAX_QUEUE, schedulerRoot, queueRoot, leaseRoot, ownerFile,
  nextSequence, enqueue, touchWaiter, orderedWaiters, pidAlive, defaultEngineActivity, readOwner,
  reconcileStaleLease, heartbeat, releaseLease, acquire,
};
