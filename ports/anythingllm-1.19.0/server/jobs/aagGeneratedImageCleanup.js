"use strict";

// One scheduled cleanup pass. Persisted typed outputs own native generated-image
// copies; filename syntax bounds the namespace, never discovers ownership.
const fs = require("fs");
const path = require("path");
const MIN_AGE_MS = 60 * 60 * 1000;
const PAGE_SIZE = 100;
const UUID = "[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}";
const own = (value) => value && typeof value === "object" && !Array.isArray(value);
const fail = (message) => { throw new Error(message); };
const sameFile = (a, b) => ["dev", "ino", "size", "mtimeMs", "ctimeMs", "nlink"].every(k => a[k] === b[k]);
function validName(name, pattern) {
  return typeof name === "string" && name.length <= 255 && !/[\\/\0]/.test(name) &&
    name !== "." && name !== ".." && new RegExp(pattern.source, pattern.flags.replace(/[gy]/g, "")).test(name);
}

async function rows(tx, table, fields, where, visit) {
  // table/fields/where are fixed internal SQL, never request input. Keyset pages
  // and a count from the SAME transaction detect incomplete enumeration.
  const counts = await tx.$queryRawUnsafe(`SELECT COUNT(*) AS n FROM ${table} WHERE ${where}`);
  if (!Array.isArray(counts) || counts.length !== 1) fail("Reference count unavailable: " + table);
  const expected = Number(counts[0].n);
  if (!Number.isSafeInteger(expected) || expected < 0) fail("Invalid reference count: " + table);
  let last = 0, seen = 0;
  while (true) {
    const page = await tx.$queryRawUnsafe(`SELECT id, ${fields} FROM ${table} WHERE ${where} AND id > ? ORDER BY id ASC LIMIT ?`, last, PAGE_SIZE);
    if (!Array.isArray(page) || page.length > PAGE_SIZE) fail("Invalid reference page: " + table);
    for (const row of page) {
      const id = Number(row.id);
      if (!Number.isSafeInteger(id) || id <= last) fail("Non-monotonic reference page: " + table);
      last = id; seen++; await visit(row);
    }
    if (page.length < PAGE_SIZE) break;
  }
  if (seen !== expected) fail("Incomplete reference scan: " + table);
  return seen;
}

async function discoverReferences(tx, pattern) {
  const referenced = new Set(), uncertain = new Set(), ledger = new Map();
  const tables = await tx.$queryRawUnsafe("SELECT name FROM sqlite_master WHERE type='table' AND name='aag_artifact_filenames'");
  if (!Array.isArray(tables) || tables.length > 1) fail("Naming ledger discovery unavailable");
  if (tables.length) {
    const names = await tx.$queryRawUnsafe("SELECT artifact_id, job_id, workspace_id, thread_id, filename FROM aag_artifact_filenames");
    const count = await tx.$queryRawUnsafe("SELECT COUNT(*) AS n FROM aag_artifact_filenames");
    if (!Array.isArray(names) || !Array.isArray(count) || count.length !== 1 || Number(count[0].n) !== names.length) fail("Naming ledger incomplete");
    for (const row of names) {
      if (!new RegExp(`^artifact-${UUID}$`).test(row.artifact_id) || !new RegExp(`^aag-${UUID}$`).test(row.job_id) ||
          !Number.isSafeInteger(Number(row.workspace_id)) || Number(row.workspace_id) < 1 ||
          !Number.isSafeInteger(Number(row.thread_id)) || Number(row.thread_id) < 0 ||
          !validName(row.filename, pattern) || ledger.has(row.artifact_id)) fail("Malformed naming ledger");
      ledger.set(row.artifact_id, row);
    }
    // A ledger entry is a permanent namespace reservation, NOT ownership. Only
    // unresolved presentation in an existing conversation makes it uncertain.
    // This covers a crash between filename allocation and native chat commit.
    const pending = await tx.$queryRawUnsafe(`SELECT DISTINCT f.filename FROM aag_artifact_filenames f
      JOIN workspace_agent_invocations i ON i.workspace_id=f.workspace_id AND COALESCE(i.thread_id,0)=f.thread_id
      WHERE i.closed=0 AND EXISTS (SELECT 1 FROM workspace_chats c WHERE c."include"=1 AND c.workspaceId=f.workspace_id AND COALESCE(c.thread_id,0)=f.thread_id)`);
    if (!Array.isArray(pending)) fail("Pending presentation discovery unavailable");
    for (const row of pending) {
      if (!validName(row.filename, pattern)) fail("Malformed pending filename binding");
      uncertain.add(row.filename);
    }
  }
  function extract(raw, label, nullable = false) {
    if (raw === null && nullable) return;
    if (typeof raw !== "string") fail("Missing persisted JSON: " + label);
    let value;
    try { value = JSON.parse(raw); } catch { fail("Malformed persisted JSON: " + label); }
    if (!own(value)) fail("Invalid persisted response: " + label);
    if (!Object.hasOwn(value, "outputs")) return; // Normal text-only response.
    if (!Array.isArray(value.outputs)) fail("Invalid persisted outputs: " + label);
    for (const output of value.outputs) {
      if (!own(output) || typeof output.type !== "string") fail("Malformed output: " + label);
      if (output.type !== "imageGenerationCard") continue;
      const p = output.payload;
      if (!own(p)) fail("Malformed image card: " + label);
      const binding = p.artifactId ? ledger.get(p.artifactId) : null;
      // Exact persisted artifact identity can recover its assigned filename.
      // Display labels, arbitrary application paths and model prose cannot.
      let name = p.storageFilename;
      if (name === undefined && binding && binding.job_id === p.jobId) name = binding.filename;
      if (!validName(name, pattern)) fail("Unresolvable image card: " + label);
      if (binding && (binding.filename !== name || binding.job_id !== p.jobId)) fail("Conflicting artifact identity: " + label);
      referenced.add(name);
    }
  }
  const chats = await rows(tx, "workspace_chats", "response", '"include"=1', row => extract(row.response, "chat " + row.id));
  const runs = await rows(tx, "scheduled_job_runs", "result", "status='completed'", row => extract(row.result, "scheduled run " + row.id, true));
  return { referenced, uncertain, chats, runs };
}

function classify(candidate, references, now) {
  if (candidate.unsafe) return { state: "UNKNOWN", eligible: false, reason: candidate.unsafe };
  if (references.referenced.has(candidate.name)) return { state: "REFERENCED", eligible: false, reason: "persisted image output" };
  if (references.uncertain.has(candidate.name)) return { state: "UNKNOWN", eligible: false, reason: "pending presentation" };
  return { state: "ORPHAN", eligible: now - candidate.stat.mtimeMs > MIN_AGE_MS, reason: now - candidate.stat.mtimeMs > MIN_AGE_MS ? "no persisted reference" : "retention grace" };
}

async function cleanupGeneratedImages({ root, database, filenamePattern, dryRun = false, now = Date.now(), log = () => {} }) {
  const report = { result: "PASS", dryRun, decisions: [], deleted: [], diagnostics: [] };
  let directory;
  const diagnostic = message => { report.diagnostics.push(message); log(message); };
  try {
    if (!path.isAbsolute(root) || !(filenamePattern instanceof RegExp)) fail("Invalid configured generated-image root");
    root = path.resolve(root);
    let rootStat;
    try { rootStat = fs.lstatSync(root); }
    catch (error) { if (error.code === "ENOENT") return report; throw error; }
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink() || fs.realpathSync(root) !== root) fail("Generated-image root redirected");
    directory = fs.openSync(root, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
    const anchored = `/proc/self/fd/${directory}`;
    if (fs.readlinkSync(anchored) !== root || !sameFile(rootStat, fs.fstatSync(directory))) fail("Generated-image root changed");
    const candidates = fs.readdirSync(anchored).filter(name => validName(name, filenamePattern)).map(name => {
      try {
        const stat = fs.lstatSync(path.join(anchored, name));
        return { name, stat, unsafe: !stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 ? "not an exclusive regular file" : null };
      } catch { return { name, unsafe: "candidate metadata unavailable" }; }
    });
    if (!candidates.length) return report;
    report.decisions = candidates.map(candidate => ({ name: candidate.name, state: "UNKNOWN", eligible: false, reason: "reference discovery incomplete" }));
    await database.$transaction(async tx => {
      // Serialize reference writers until decisions/deletions finish. A failed
      // DB read must reject, unlike native model helpers that catch and return [].
      if (!dryRun) await tx.$executeRawUnsafe('UPDATE workspace_chats SET id=id WHERE 0');
      const references = await discoverReferences(tx, filenamePattern);
      report.sources = { chats: references.chats, scheduledRuns: references.runs };
      report.decisions = candidates.map(candidate => ({ name: candidate.name, ...classify(candidate, references, now) }));
      for (let i = 0; i < candidates.length; i++) {
        const decision = report.decisions[i], candidate = candidates[i];
        if (dryRun || !decision.eligible) continue;
        // Pin deletion to the already-open directory. Symlink leaf replacement
        // cannot make unlink follow an outside target; inode changes are skipped.
        const currentRoot = fs.lstatSync(root);
        if (!currentRoot.isDirectory() || currentRoot.isSymbolicLink() || fs.realpathSync(root) !== root || currentRoot.dev !== rootStat.dev || currentRoot.ino !== rootStat.ino) fail("Generated-image root changed before deletion");
        let current;
        try { current = fs.lstatSync(path.join(anchored, candidate.name)); }
        catch { decision.state = "UNKNOWN"; decision.eligible = false; decision.reason = "file changed before deletion"; continue; }
        if (!current.isFile() || !sameFile(current, candidate.stat)) {
          decision.state = "UNKNOWN"; decision.eligible = false; decision.reason = "file changed before deletion"; continue;
        }
        fs.unlinkSync(path.join(anchored, candidate.name));
        report.deleted.push(candidate.name);
      }
    }, { maxWait: 5000, timeout: 30000 });
    for (const row of report.decisions.filter(row => row.state === "UNKNOWN")) diagnostic(`Skipping ${row.name}: ${row.reason}`);
  } catch (error) {
    report.result = "SKIPPED_UNKNOWN";
    // No reference decision is trusted after any incomplete discovery. Deletion
    // starts only after all sources were read successfully in the transaction.
    for (const row of report.decisions) if (!report.deleted.includes(row.name)) { row.state = "UNKNOWN"; row.eligible = false; }
    diagnostic("Cleanup ownership unavailable; deletion skipped: " + String(error.message || error.code || error.name).slice(0, 200));
  } finally { if (directory !== undefined) fs.closeSync(directory); }
  return report;
}
module.exports = { MIN_AGE_MS, PAGE_SIZE, validName, discoverReferences, classify, cleanupGeneratedImages };
