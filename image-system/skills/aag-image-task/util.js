"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError } = require("./errors");

function text(value, max, required = false) {
  const out = typeof value === "string" ? value.trim() : "";
  if (required && !out) throw new AagError("INVALID_ARGUMENT", "A request is required.");
  if (out.length > max) throw new AagError("INVALID_ARGUMENT", `Text exceeds ${max} characters.`);
  return out;
}

function one(value, allowed, fallback) {
  const v = String(value ?? fallback ?? "").trim().toLowerCase();
  if (!allowed.includes(v)) throw new AagError("INVALID_ARGUMENT", "An argument contains an unsupported value.");
  return v;
}

function integer(value, fallback, min, max) {
  const n = value === undefined || value === null || value === "" ? fallback : Number(value);
  if (!Number.isSafeInteger(n) || n < min || n > max) {
    throw new AagError("INVALID_ARGUMENT", `Integer must be ${min}..${max}.`);
  }
  return n;
}

function cleanScope(value) {
  return String(value ?? "unknown").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 96) || "unknown";
}

function scope(runtime = {}) {
  return {
    workspace_id: cleanScope(runtime.AAG_WORKSPACE_ID),
    thread_id: cleanScope(runtime.AAG_THREAD_ID),
    user_id: cleanScope(runtime.AAG_USER_ID),
    invocation_id: cleanScope(runtime.AAG_INVOCATION_UUID),
    turn_id: cleanScope(runtime.AAG_TURN_ID),
  };
}

function sameOwner(a, b) {
  return Boolean(a && b) && a.workspace_id === b.workspace_id && a.thread_id === b.thread_id && a.user_id === b.user_id;
}

function ownerKey(owner) {
  return [owner.workspace_id, owner.thread_id, owner.user_id].map(cleanScope).join("--");
}

function stateRoot(runtime = {}) {
  const root = path.resolve(runtime.AAG_IMAGE_AGENT_STATE_ROOT || process.env.AAG_IMAGE_AGENT_STATE_ROOT || "/app/server/storage/aag-image-agent-state");
  if (root === path.parse(root).root) throw new AagError("OUTPUT_POLICY_VIOLATION", "The private state root is unsafe.");
  return root;
}

function ensureDirectory(dir) {
  const absolute = path.resolve(dir);
  const parsed = path.parse(absolute);
  let current = parsed.root;
  for (const component of absolute.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    try {
      const item = fs.lstatSync(current);
      if (item.isSymbolicLink() || !item.isDirectory()) throw new AagError("OUTPUT_POLICY_VIOLATION", "A private state directory is unsafe.");
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      break;
    }
  }
  if (!fs.existsSync(absolute)) fs.mkdirSync(absolute, { recursive: true, mode: 0o700 });
  const stat = fs.lstatSync(absolute);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new AagError("OUTPUT_POLICY_VIOLATION", "A private state directory is unsafe.");
  fs.chmodSync(absolute, 0o700);
}

function fsyncDirectory(dir) {
  let fd;
  try { fd = fs.openSync(dir, "r"); fs.fsyncSync(fd); } catch {} finally { if (fd !== undefined) fs.closeSync(fd); }
}

function atomicWrite(file, bytes, options = {}) {
  ensureDirectory(path.dirname(file));
  const tmp = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
  let fd;
  try {
    fd = fs.openSync(tmp, options.exclusive === false ? "w" : "wx", 0o600);
    fs.writeFileSync(fd, bytes);
    fs.fsyncSync(fd);
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
  fs.renameSync(tmp, file);
  fs.chmodSync(file, 0o600);
  fsyncDirectory(path.dirname(file));
}

function atomicJson(file, value) {
  atomicWrite(file, JSON.stringify(value, null, 2) + "\n");
}

function readFileNoFollow(file, encoding = null) {
  let fd;
  try {
    fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    const stat = fs.fstatSync(fd);
    if (!stat.isFile()) throw new AagError("OUTPUT_POLICY_VIOLATION", "A private state file is unsafe.");
    return fs.readFileSync(fd, encoding ? { encoding } : undefined);
  } catch (error) {
    if (["ELOOP", "EMLINK"].includes(error?.code)) throw new AagError("OUTPUT_POLICY_VIOLATION", "A private state file is unsafe.");
    throw error;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function readJson(file, fallback) {
  try { return JSON.parse(readFileNoFollow(file, "utf8")); } catch (error) {
    if (fallback !== undefined && error?.code === "ENOENT") return fallback;
    throw error;
  }
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

module.exports = { text, one, integer, scope, sameOwner, ownerKey, stateRoot, ensureDirectory, atomicWrite, atomicJson, readFileNoFollow, readJson, sha256, sleep };
