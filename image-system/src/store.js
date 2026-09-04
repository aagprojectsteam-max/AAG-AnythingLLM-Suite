"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError } = require("./errors");
const { atomicJson, ensureDirectory, readFileNoFollow, readJson, sameOwner } = require("./util");

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"]);
const TRANSITIONS = Object.freeze({
  CREATED: new Set(["VALIDATED", "FAILED", "CANCELLED"]),
  VALIDATED: new Set(["QUEUED", "FAILED", "CANCELLED"]),
  QUEUED: new Set(["RUNNING", "FAILED", "CANCELLED", "TIMED_OUT"]),
  RUNNING: new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"]),
  COMPLETED: new Set(),
  PARTIAL: new Set(),
  FAILED: new Set(),
  CANCELLED: new Set(),
  TIMED_OUT: new Set(),
});

function timestamp() { return new Date().toISOString(); }
function validId(id) { return /^aag-[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(String(id || "")); }
function newId() { return `aag-${crypto.randomUUID()}`; }

function jobFile(root, id) {
  if (!validId(id)) throw new AagError("INVALID_ARGUMENT", "The job ID is invalid.");
  return path.join(root, "jobs", `${id}.json`);
}

function createRecord(root, fields) {
  const now = timestamp();
  const record = {
    schema_version: fields.schema_version || 2,
    release: fields.release,
    job_id: fields.job_id || newId(),
    parent_job_id: fields.parent_job_id || null,
    child_index: fields.child_index || null,
    owner: fields.owner,
    operation: fields.operation,
    workflow_id: fields.workflow_id,
    capability: fields.capability || null,
    atlas: fields.atlas || null,
    status: "CREATED",
    created_at: now,
    updated_at: now,
    started_at: null,
    finished_at: null,
    transitions: [{ status: "CREATED", at: now, detail: fields.detail || null }],
    source: fields.source || null,
    count: fields.count || 1,
    seed: fields.seed ?? null,
    child_jobs: fields.child_jobs || [],
    artifacts: [],
    intermediate_artifacts: fields.intermediate_artifacts || [],
    error: null,
    scheduler: {},
    engine: {},
    logical_child_id: fields.logical_child_id || null,
    requested_count: fields.requested_count || fields.count || 1,
    collection: fields.collection || null,
    plan: fields.plan || null,
    plan_sha256: fields.plan_sha256 || null,
    public_arguments: fields.public_arguments || null,
    attempts: fields.attempts || [],
    final_output_quality: fields.final_output_quality || "standard",
    progress: fields.progress || {},
  };
  atomicJson(jobFile(root, record.job_id), record);
  return record;
}

function read(root, id) {
  try { return readJson(jobFile(root, id)); }
  catch (error) {
    if (error instanceof AagError) throw error;
    if (error?.code === "ENOENT" || error instanceof SyntaxError) throw new AagError("JOB_NOT_FOUND", "Image job not found.");
    throw error;
  }
}

function write(root, job) {
  job.updated_at = timestamp();
  atomicJson(jobFile(root, job.job_id), job);
  return job;
}

function transition(job, next, detail = null) {
  if (!TRANSITIONS[job.status]?.has(next)) {
    throw new AagError("ILLEGAL_STATE_TRANSITION", "The requested job state transition is illegal.", false, `${job.status}->${next}`);
  }
  const now = timestamp();
  job.status = next;
  job.updated_at = now;
  if (next === "RUNNING") job.started_at = now;
  if (TERMINAL.has(next)) job.finished_at = now;
  job.transitions.push({ status: next, at: now, detail });
  return job;
}

function transitionAndWrite(root, job, next, detail = null) {
  transition(job, next, detail);
  return write(root, job);
}

function reopenAndWrite(root, job, detail = null) {
  if (Number(job.schema_version) < 3 || !["PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"].includes(job.status)) {
    throw new AagError("ILLEGAL_STATE_TRANSITION", "Only an incomplete resumable batch record can be reopened.");
  }
  const now = timestamp();
  job.status = "QUEUED";
  job.updated_at = now;
  job.finished_at = null;
  job.error = null;
  job.transitions.push({ status: "QUEUED", at: now, detail: detail || "Explicit batch resume requested" });
  return write(root, job);
}

function idempotencyFile(root, key) {
  if (!/^[a-f0-9]{64}$/.test(key)) throw new AagError("INTERNAL_ERROR", "The idempotency key is invalid.");
  return path.join(root, "idempotency", `${key}.txt`);
}

function getIdempotent(root, key, owner) {
  let id;
  try { id = readFileNoFollow(idempotencyFile(root, key), "utf8").trim(); }
  catch (error) { if (error?.code === "ENOENT") return null; throw error; }
  const job = read(root, id);
  if (!sameOwner(job.owner, owner)) throw new AagError("JOB_NOT_AUTHORIZED", "Image job is outside this conversation scope.");
  return job;
}

function claimIdempotency(root, key, id) {
  ensureDirectory(path.join(root, "idempotency"));
  const file = idempotencyFile(root, key);
  try {
    fs.writeFileSync(file, `${id}\n`, { mode: 0o600, flag: "wx" });
    fs.chmodSync(file, 0o600);
    return { claimed: true, job_id: id };
  }
  catch (error) {
    if (error?.code !== "EEXIST") throw error;
    return { claimed: false, job_id: readFileNoFollow(file, "utf8").trim() };
  }
}

function listJobs(root) {
  const dir = path.join(root, "jobs");
  try {
    return fs.readdirSync(dir).filter(name => /^aag-.*\.json$/.test(name)).map(name => readJson(path.join(dir, name)));
  } catch (error) { if (error?.code === "ENOENT") return []; throw error; }
}

function recoverStale(root, staleMs, nowMs = Date.now()) {
  const recovered = [];
  for (const job of listJobs(root)) {
    if (TERMINAL.has(job.status)) continue;
    const age = nowMs - Date.parse(job.updated_at || job.created_at);
    if (!Number.isFinite(age) || age <= staleMs) continue;
    const prior = job.status;
    transition(job, "FAILED", "Recovered stale non-terminal state after restart");
    job.error = { code: "STALE_STATE_RECOVERED", message: "A stale interrupted image job was recovered safely.", retryable: true };
    write(root, job);
    recovered.push({ job_id: job.job_id, prior_status: prior });
  }
  return recovered;
}

module.exports = {
  TERMINAL, TRANSITIONS, validId, newId, jobFile, createRecord, read, write,
  transition, transitionAndWrite, reopenAndWrite, getIdempotent, claimIdempotency, listJobs,
  recoverStale,
};
