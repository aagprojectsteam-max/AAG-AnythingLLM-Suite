"use strict";

const fs = require("fs");
const path = require("path");

// Persistence is the authority. Watch atomic renames from ALL skill processes;
// only startup or an explicit watcher invalidation enumerates the directory.
function watchDirectory(directory, { change, resync, logger = () => {} }) {
  let watcher, ancestor, closed = false, refreshTimer;
  let watchedAncestor, directoryIdentity;
  const identity = () => {
    try { const s = fs.lstatSync(directory); return `${s.dev}:${s.ino}`; }
    catch (error) { if (error.code === "ENOENT") return null; throw error; }
  };
  const refresh = () => {
    clearTimeout(refreshTimer);
    if (closed) return;
    watcher?.close(); ancestor?.close();
    watcher = ancestor = null;
    let parent = path.dirname(directory);
    while (!fs.existsSync(parent) && parent !== path.dirname(parent)) parent = path.dirname(parent);
    watchedAncestor = parent;
    directoryIdentity = identity();
    const invalidated = () => {
      if (closed || refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        refresh();
        resync();
      }, 25);
      refreshTimer.unref?.();
    };
    const onError = (error) => {
      logger(`[AAG-IMAGE] filesystem index invalidated: ${error.message}`);
      invalidated();
    };
    ancestor = fs.watch(parent, { persistent: false }, (event, name) => {
      const relative = path.relative(watchedAncestor, directory).split(path.sep)[0];
      // Follow newly created intermediate directories too: the final jobs
      // directory may be created later, after an asynchronous readiness check.
      if (!name || (String(name) === relative && (watchedAncestor !== path.dirname(directory) || identity() !== directoryIdentity))) invalidated();
    });
    ancestor.on("error", onError);
    try {
      const stat = fs.lstatSync(directory);
      if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("Job index directory must be a physical directory");
      watcher = fs.watch(directory, { persistent: false }, (event, name) => {
        if (!name) invalidated();
        else change(String(name));
      });
      watcher.on("error", onError);
    } catch (error) {
      if (error.code !== "ENOENT") { ancestor.close(); throw error; }
    }
  };
  refresh();
  return { close() { closed = true; clearTimeout(refreshTimer); watcher?.close(); ancestor?.close(); } };
}

function scope(owner) {
  return JSON.stringify([String(owner?.workspace_id), String(owner?.thread_id), String(owner?.user_id ?? "unknown")]);
}

function createJobIndex(root, store, { logger = () => {} } = {}) {
  const records = new Map(), owners = new Map(), latest = new Map();
  const active = new Set(), pending = new Set(), changed = new Set(), listeners = new Set();
  let closed = false, flushTimer;
  const notify = () => { for (const listener of listeners) listener(); };
  const updateLatest = (key) => {
    let newest = null;
    for (const id of owners.get(key) || []) {
      const job = records.get(id);
      if (!newest || Date.parse(job.created_at) > Date.parse(newest.created_at)) newest = job;
    }
    if (newest) latest.set(key, newest.job_id); else latest.delete(key);
  };
  const remove = (id) => {
    const old = records.get(id);
    if (old && !old.parent_job_id) {
      const key = scope(old.owner);
      owners.get(key)?.delete(id);
      records.delete(id);
      if (latest.get(key) === id) updateLatest(key);
      if (!owners.get(key)?.size) owners.delete(key);
    } else records.delete(id);
    active.delete(id); pending.delete(id);
  };
  const put = (job) => {
    const old = records.get(job.job_id);
    if (old && (old.parent_job_id !== job.parent_job_id || scope(old.owner) !== scope(job.owner) || old.created_at !== job.created_at)) remove(job.job_id);
    active.delete(job.job_id); pending.delete(job.job_id);
    records.set(job.job_id, job);
    if (!job.parent_job_id) {
      const key = scope(job.owner);
      if (!owners.has(key)) owners.set(key, new Set());
      owners.get(key).add(job.job_id);
      const previous = records.get(latest.get(key));
      if (!previous || Date.parse(job.created_at) >= Date.parse(previous.created_at)) latest.set(key, job.job_id);
      if (!store.TERMINAL.has(job.status)) active.add(job.job_id);
      if (["COMPLETED", "PARTIAL"].includes(job.status) && job.artifacts?.length && job.presentation?.status !== "COMPLETED") pending.add(job.job_id);
    }
  };
  const readChanged = (id) => {
    try {
      const job = store.read(root, id);
      if (job.job_id !== id) throw new Error("Job filename and record identity differ");
      put(job);
    }
    catch (error) {
      if (error.code === "JOB_NOT_FOUND" || error.code === "ENOENT") remove(id);
      else logger(`[AAG-IMAGE] cannot index ${id}: ${error.message}`);
    }
  };
  const flush = () => {
    clearTimeout(flushTimer); flushTimer = null;
    if (closed || !changed.size) return;
    const ids = [...changed]; changed.clear();
    for (const id of ids) readChanged(id);
    notify();
  };
  const resync = () => {
    if (closed) return;
    const found = new Set();
    // A resync is startup/watcher recovery, never a periodic archive poll.
    let names;
    try { names = fs.readdirSync(path.join(root, "jobs")); }
    catch (error) { if (error.code !== "ENOENT") throw error; names = []; }
    for (const name of names) {
      if (!name.endsWith(".json") || !store.validId(name.slice(0, -5))) continue;
      const id = name.slice(0, -5); found.add(id); readChanged(id);
    }
    for (const id of records.keys()) if (!found.has(id)) remove(id);
    notify();
  };
  const observer = watchDirectory(path.join(root, "jobs"), {
    logger, resync,
    change(name) {
      if (!name.endsWith(".json") || !store.validId(name.slice(0, -5))) return;
      changed.add(name.slice(0, -5));
      if (!flushTimer) { flushTimer = setTimeout(flush, 20); flushTimer.unref?.(); }
    },
  });
  resync();
  return {
    get(id) { flush(); return records.get(id) || null; },
    latest(owner) { flush(); return records.get(latest.get(scope(owner))) || null; },
    activeJobs() { flush(); return [...active].map(id => records.get(id)); },
    takePending() { flush(); const jobs = [...pending].map(id => records.get(id)); pending.clear(); return jobs; },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    close() { closed = true; clearTimeout(flushTimer); observer.close(); listeners.clear(); },
  };
}

module.exports = { watchDirectory, createJobIndex };
