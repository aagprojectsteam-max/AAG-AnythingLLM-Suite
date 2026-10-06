"use strict";

// The ledger belongs to the existing AnythingLLM SQLite database. It stores only
// filename assignments; workspace/thread/chat records are never updated here.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const http = require("http");
const UUID = "[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}";
const NAME = /^(?:img-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\.png|(?:AAG-Image-)?[1-9][0-9]*-(?:0|[1-9][0-9]*)-[0-9]{8}-[0-9]{6}(?:-[1-9][0-9]*)?\.(?:png|jpe?g|webp))$/;
const EXTENSION = Object.freeze({png:"png",jpeg:"jpg",webp:"webp"});
function fail() { throw new Error("AAG_ARTIFACT_NAMING_FAILED"); }
function positive(value) {
  if (!/^[1-9][0-9]*$/.test(String(value)) || !Number.isSafeInteger(Number(value))) fail();
  return Number(value);
}
function readJob(root, jobId) {
  if (!new RegExp(`^aag-${UUID}$`).test(jobId)) fail();
  const fd = fs.openSync(path.join(root, "jobs", jobId + ".json"), fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    if (!fs.fstatSync(fd).isFile() || fs.fstatSync(fd).size > 8 * 1024 * 1024) fail();
    return JSON.parse(fs.readFileSync(fd, "utf8"));
  } finally { fs.closeSync(fd); }
}
function evidence(job, artifact) {
  const saved = job.artifacts?.find(item => item.artifact_id === artifact.artifactId);
  if (!saved || saved.sha256 !== artifact.sha256 || saved.width !== artifact.width || saved.height !== artifact.height || !EXTENSION[saved.format] || artifact.format && artifact.format !== saved.format) fail();
  const instant = new Date(saved.verified_at);
  if (!/^\d{4}-\d{2}-\d{2}T/.test(saved.verified_at || "") || !Number.isFinite(instant.getTime())) fail();
  // Preserve the canonical UTC event independently of local filename formatting.
  const timestamp = instant.toISOString();
  return { timestamp, extension: EXTENSION[saved.format] };
}
function hostTimezone(socketPath="/app/server/storage/aag-image-lifecycle/control.sock") {
  return new Promise((resolve,reject)=>{
    const req=http.get({socketPath,path:"/artifacts/timezone",timeout:5000},res=>{
      let body="";res.on("data",chunk=>{body+=chunk;if(body.length>2048)req.destroy(new Error("HOST_TIMEZONE_UNAVAILABLE"));});
      res.on("error",reject);res.on("end",()=>{try{const data=JSON.parse(body);if(res.statusCode!==200||data.schema!=="aag.host-timezone.v1"||typeof data.timezone!=="string")fail();resolve(data.timezone);}catch(error){reject(error);}});
    });
    req.on("timeout",()=>req.destroy(new Error("HOST_TIMEZONE_UNAVAILABLE")));req.on("error",reject);
  });
}
function localStamp(timestamp, timezone) {
  if (typeof timezone!=="string" || !timezone || timezone.length>128) fail();
  const parts=new Intl.DateTimeFormat("en-GB-u-ca-gregory-nu-latn",{timeZone:timezone,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).formatToParts(new Date(timestamp));
  const p=Object.fromEntries(parts.map(item=>[item.type,item.value]));
  const stamp=`${p.year}${p.month}${p.day}-${p.hour}${p.minute}${p.second}`;
  if (!/^[0-9]{8}-[0-9]{6}$/.test(stamp)) fail();
  return stamp;
}
async function schema(db) {
  await db.$executeRawUnsafe(`CREATE TABLE IF NOT EXISTS aag_thread_display_order (
    workspace_id INTEGER NOT NULL, thread_id INTEGER NOT NULL, position INTEGER NOT NULL,
    PRIMARY KEY(workspace_id, thread_id))`);
  await db.$executeRawUnsafe(`CREATE TABLE IF NOT EXISTS aag_workspace_serials (
    workspace_id INTEGER PRIMARY KEY, serial INTEGER NOT NULL UNIQUE)`);
  await db.$executeRawUnsafe(`CREATE TABLE IF NOT EXISTS aag_visible_thread_serials (
    workspace_id INTEGER NOT NULL, thread_id INTEGER NOT NULL, serial INTEGER NOT NULL,
    PRIMARY KEY(workspace_id, thread_id), UNIQUE(workspace_id, serial))`);
  await db.$executeRawUnsafe(`CREATE TABLE IF NOT EXISTS aag_artifact_serials (
    artifact_id TEXT PRIMARY KEY, workspace_serial INTEGER NOT NULL)`);
  await db.$executeRawUnsafe(`CREATE TABLE IF NOT EXISTS aag_thread_serials (
    workspace_id INTEGER NOT NULL, thread_id INTEGER NOT NULL, serial INTEGER NOT NULL,
    PRIMARY KEY(workspace_id, thread_id), UNIQUE(workspace_id, serial))`);
  await db.$executeRawUnsafe(`CREATE TABLE IF NOT EXISTS aag_artifact_filenames (
    artifact_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, workspace_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL, thread_serial INTEGER NOT NULL, sha256 TEXT NOT NULL,
    generated_at TEXT NOT NULL, timezone TEXT NOT NULL, filename TEXT NOT NULL UNIQUE)`);
}
// Workspace serials and historical filename assignments remain stable. Current
// thread numbers are ranks in the current visible list, not identity allocations.
async function allocateWorkspace(tx, workspaceId) {
  await tx.$executeRawUnsafe(`INSERT OR IGNORE INTO aag_workspace_serials(workspace_id, serial)
    SELECT ?, COALESCE(MAX(serial), 0) + 1 FROM aag_workspace_serials`, workspaceId);
}
async function allocateThread(tx, workspaceId, threadId) {
  await tx.$executeRawUnsafe(`INSERT OR IGNORE INTO aag_visible_thread_serials(workspace_id, thread_id, serial)
    SELECT ?, ?, COALESCE(MAX(serial), 0) + 1 FROM aag_visible_thread_serials WHERE workspace_id = ?`, workspaceId, threadId, workspaceId);
}
function orderedIds(values) {
  if (!Array.isArray(values) || values.length > 10000) fail();
  const ids = values.map(positive);
  if (new Set(ids).size !== ids.length) fail();
  return ids;
}
async function initializeSerials(db, snapshot) {
  const workspaceIds = orderedIds(snapshot?.workspaceIds);
  if (!workspaceIds.length || !Array.isArray(snapshot.threads) || snapshot.threads.length !== workspaceIds.length) fail();
  await schema(db);
  return db.$transaction(async tx => {
    await tx.$executeRawUnsafe("UPDATE aag_workspace_serials SET serial = serial WHERE 0");
    for (let i = 0; i < workspaceIds.length; i++) {
      const workspaceId = workspaceIds[i], group = snapshot.threads[i];
      if (group?.workspaceId !== workspaceId || !(await tx.$queryRawUnsafe("SELECT id FROM workspaces WHERE id = ?", workspaceId)).length) fail();
      await allocateWorkspace(tx, workspaceId);
      for (const [position, threadId] of orderedIds(group.threadIds).entries()) {
        if (!(await tx.$queryRawUnsafe("SELECT id FROM workspace_threads WHERE id = ? AND workspace_id = ?", threadId, workspaceId)).length) fail();
        await allocateThread(tx, workspaceId, threadId);
        await tx.$executeRawUnsafe(`INSERT INTO aag_thread_display_order(workspace_id,thread_id,position)
          VALUES(?,?,?) ON CONFLICT(workspace_id,thread_id) DO UPDATE SET position=excluded.position`, workspaceId, threadId, position);
      }
    }
    return { initialized: true };
  }, { maxWait: 30000, timeout: 30000 });
}
async function forwardSerials(tx, workspaceId, threadId) {
  // The initial order must come from the authenticated sidebar snapshot. Never
  // guess it from native IDs if a browser has not initialized this installation.
  if (!(await tx.$queryRawUnsafe("SELECT workspace_id FROM aag_workspace_serials LIMIT 1")).length)
    throw new Error("AAG_SERIALS_NOT_INITIALIZED");
  if (!(await tx.$queryRawUnsafe("SELECT id FROM workspaces WHERE id = ?", workspaceId)).length) fail();
  await allocateWorkspace(tx, workspaceId);
  if (threadId !== 0) {
    if (!(await tx.$queryRawUnsafe("SELECT id FROM workspace_threads WHERE id = ? AND workspace_id = ?", threadId, workspaceId)).length) fail();
  }
  const workspace = (await tx.$queryRawUnsafe("SELECT serial FROM aag_workspace_serials WHERE workspace_id = ?", workspaceId))[0];
  const thread = await currentThreadReference(tx, workspaceId, threadId);
  return { workspaceSerial: positive(workspace.serial), threadSerial: threadId === 0 ? 0 : positive(thread?.ordinal) };
}

async function currentThreadReference(db, workspaceId, threadId) {
  if (!threadId) return {ordinal:null, state:"DEFAULT"};
  workspaceId=positive(workspaceId);threadId=positive(threadId);
  // Filter against live native rows before ranking: deleted reservations never
  // leave holes. User scope matches the native sidebar's own-thread list.
  // New native rows absent from the last snapshot append in native creation
  // order. IDs are comparison keys only; they are never the displayed number.
  const hasOrder=(await db.$queryRawUnsafe("SELECT name FROM sqlite_master WHERE type='table' AND name='aag_thread_display_order'")).length>0;
  const rows=await db.$queryRawUnsafe(`SELECT t.id FROM workspace_threads t
    ${hasOrder ? "LEFT JOIN aag_thread_display_order o ON o.workspace_id=t.workspace_id AND o.thread_id=t.id" : ""}
    WHERE t.workspace_id=? AND t.user_id IS
      (SELECT user_id FROM workspace_threads WHERE workspace_id=? AND id=?)
    ORDER BY ${hasOrder ? "CASE WHEN o.position IS NULL THEN 1 ELSE 0 END, o.position," : ""} t.id`, workspaceId,workspaceId,threadId);
  const index=rows.findIndex(row=>Number(row.id)===threadId);
  return {ordinal:index<0?null:index+1,state:index<0?"DELETED":"CURRENT"};
}

async function artifactThreadReference(db, artifactId) {
  if (!new RegExp(`^artifact-${UUID}$`).test(artifactId || "")) return null;
  const rows=await db.$queryRawUnsafe(`SELECT f.workspace_id,f.thread_id,f.thread_serial,
    w.slug AS workspace_slug,t.slug AS thread_slug FROM aag_artifact_filenames f
    JOIN workspaces w ON w.id=f.workspace_id
    LEFT JOIN workspace_threads t ON t.workspace_id=f.workspace_id AND t.id=f.thread_id
    WHERE f.artifact_id=?`, artifactId);
  if (!rows.length) return null;
  const row=rows[0], current=await currentThreadReference(db, Number(row.workspace_id), Number(row.thread_id));
  return {workspaceSlug:row.workspace_slug,threadSlug:row.thread_slug || null,
    ...current,creationOrdinal:Number(row.thread_serial)>0?Number(row.thread_serial):null};
}
function writeExclusive(target, bytes) {
  const fd = fs.openSync(target, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); }
  catch (error) { fs.closeSync(fd); fs.unlinkSync(target); throw error; }
  fs.closeSync(fd);
}
function verify(target, sha256) {
  const fd = fs.openSync(target, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size < 128 || stat.size > 200 * 1024 * 1024) fail();
    if (crypto.createHash("sha256").update(fs.readFileSync(fd)).digest("hex") !== sha256) fail();
  } finally { fs.closeSync(fd); }
}
async function historicalFilename(tx, jobId, artifactId, workspaceId) {
  const rows = await tx.$queryRawUnsafe('SELECT response FROM workspace_chats WHERE workspaceId = ? AND response LIKE ?', workspaceId, "%" + artifactId + "%");
  for (const row of rows) {
    let outputs;
    try { outputs = JSON.parse(row.response).outputs; } catch { continue; }
    for (const output of outputs || []) {
      const p = output?.payload;
      if (output?.type === "imageGenerationCard" && p?.jobId === jobId && p.artifactId === artifactId && NAME.test(p.storageFilename)) return p.storageFilename;
    }
  }
  return null;
}
async function persist({ generatedImagesPath, bytes, parsed, artifact, stateRoot, database, job, resolveTimezone=hostTimezone }) {
  const db = database || require("/app/server/utils/prisma");
  job = job || readJob(stateRoot || path.resolve(generatedImagesPath, "../aag-image-agent-state"), parsed.jobId);
  if (job.job_id !== parsed.jobId || !new RegExp(`^artifact-${UUID}$`).test(artifact.artifactId)) fail();
  const workspaceId = positive(job.owner?.workspace_id);
  const threadId = [null, undefined, "unknown", "0", 0].includes(job.owner?.thread_id) ? 0 : positive(job.owner.thread_id);
  const { timestamp, extension } = evidence(job, artifact);
  fs.mkdirSync(generatedImagesPath, { recursive: true });
  if (fs.realpathSync(generatedImagesPath) !== path.resolve(generatedImagesPath)) fail();
  await schema(db);
  // Acquire SQLite's write reservation before any read in this transaction.
  // This serializes namespace allocation across processes; O_EXCL protects files
  // from writers outside this ledger and from debris after an interrupted write.
  return db.$transaction(async tx => {
    await tx.$executeRawUnsafe("UPDATE aag_workspace_serials SET serial = serial WHERE 0");
    const existing = (await tx.$queryRawUnsafe("SELECT * FROM aag_artifact_filenames WHERE artifact_id = ?", artifact.artifactId))[0];
    if (existing) {
      if (existing.job_id !== parsed.jobId || Number(existing.workspace_id) !== workspaceId || Number(existing.thread_id) !== threadId || existing.sha256 !== artifact.sha256 || !NAME.test(existing.filename)) fail();
      verify(path.join(generatedImagesPath, existing.filename), artifact.sha256);
      const assigned = (await tx.$queryRawUnsafe("SELECT workspace_serial FROM aag_artifact_serials WHERE artifact_id = ?", artifact.artifactId))[0];
      return { storageFilename: existing.filename, fileSize: bytes.length, generatedAt: existing.generated_at, workspaceSerial: assigned ? positive(assigned.workspace_serial) : workspaceId, threadSerial: Number(existing.thread_serial), filenameTimezone: existing.timezone };
    }
    let filename = await historicalFilename(tx, parsed.jobId, artifact.artifactId, workspaceId);
    let timezone="UTC", workspaceSerial=workspaceId, serial=0;
    if (filename) verify(path.join(generatedImagesPath, filename), artifact.sha256);
    else {
      const assigned = await forwardSerials(tx, workspaceId, threadId);
      workspaceSerial = assigned.workspaceSerial; serial = assigned.threadSerial;
      timezone=await resolveTimezone();
      const stem = `AAG-Image-${workspaceSerial}-${serial}-${localStamp(timestamp,timezone)}`;
      let allocated = false;
      for (let suffix = 1; suffix <= 100000; suffix++) {
        filename = `${stem}${suffix === 1 ? "" : "-" + suffix}.${extension}`;
        // Ledger reservations are kept even if somebody later removes a file.
        if ((await tx.$queryRawUnsafe("SELECT artifact_id FROM aag_artifact_filenames WHERE filename = ?", filename)).length) continue;
        try { writeExclusive(path.join(generatedImagesPath, filename), bytes); allocated = true; break; }
        catch (error) { if (error.code !== "EEXIST") throw error; filename = null; }
      }
      if (!allocated) fail();
      const directory = fs.openSync(generatedImagesPath, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
      try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
    }
    await tx.$executeRawUnsafe("INSERT INTO aag_artifact_filenames VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", artifact.artifactId, parsed.jobId, workspaceId, threadId, serial, artifact.sha256, timestamp, timezone, filename);
    await tx.$executeRawUnsafe("INSERT INTO aag_artifact_serials VALUES (?, ?)", artifact.artifactId, workspaceSerial);
    return { storageFilename: filename, fileSize: bytes.length, generatedAt: timestamp, workspaceSerial, threadSerial: serial, filenameTimezone: timezone };
  }, { maxWait: 30000, timeout: 30000 });
}
module.exports = { persist, NAME, schema, initializeSerials, currentThreadReference, artifactThreadReference, hostTimezone, localStamp };
