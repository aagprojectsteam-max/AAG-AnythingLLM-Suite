"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("fs"), os = require("os"), path = require("path"), crypto = require("crypto");
const cp = require("child_process");
const store = require("../src/store"), recovery = require("../src/recovery");
const { createJobIndex } = require("../src/job-index");
const progress = require("../integrations/anythingllm/aagImageProgress");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const owner = { workspace_id: "10", thread_id: "20", user_id: "unknown", invocation_id: "missing" };
function fixture(t, count = 0) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aag-index-test-"));
  fs.mkdirSync(path.join(root, "jobs"));
  const jobs = [];
  for (let i = 0; i < count; i++) {
    const job = { job_id: `aag-${crypto.randomUUID()}`, owner, parent_job_id: null, status: "COMPLETED", created_at: new Date(1000 + i).toISOString(), artifacts: [{ filename: "historical.png" }] };
    fs.writeFileSync(store.jobFile(root, job.job_id), JSON.stringify(job)); jobs.push(job);
  }
  t.after(() => { store.closeJobIndex(root); fs.rmSync(root, { recursive: true, force: true }); });
  return { root, jobs };
}
function instrument(t, root) {
  const counts = { reads: [], scans: 0 }, read = store.read, readdir = fs.readdirSync;
  store.read = function(r, id) { if (r === root) counts.reads.push(id); return read(r, id); };
  fs.readdirSync = function(p, ...args) { if (p === path.join(root, "jobs")) counts.scans++; return readdir.call(this, p, ...args); };
  t.after(() => { store.read = read; fs.readdirSync = readdir; });
  return counts;
}

test("5000 historical jobs: startup once, idle reconciler and progress polling never reopen history", async t => {
  const { root, jobs } = fixture(t, 5000), counts = instrument(t, root);
  let lookups = 0;
  const server = { WorkspaceChats: { async where() { return []; } }, WorkspaceAgentInvocation: { async get() { lookups++; return null; } } };
  const core = { store, recovery, runtime: {} };
  const reconciler = progress.startImageReconciler({ stateRoot: root, core, server, intervalMs: 15 });
  try {
    await reconciler.settle();
    assert.equal(counts.scans, 1); assert.equal(counts.reads.length, 5000); assert.equal(lookups, 5000);
    counts.reads.length = 0; counts.scans = 0;
    for (let i = 0; i < 8; i++) {
      await progress.threadProgress({ workspace: { id: 10 }, thread: { id: 20 } }, { stateRoot: root, core, server });
      await sleep(20);
    }
    assert.deepEqual(counts.reads, []); assert.equal(counts.scans, 0); assert.equal(lookups, 5000);
    assert.equal(fs.readdirSync(path.join(root, "jobs")).length, 5000);
    assert.equal(store.read(root, jobs[0].job_id).artifacts[0].filename, "historical.png");
  } finally { reconciler.close(); }
});

test("5000 historical + 2 active: recurring recovery touches only active parents and children", async t => {
  const { root } = fixture(t, 5000);
  const active = [];
  for (let i = 0; i < 1; i++) {
    const parent = store.createRecord(root, { owner, count: 1, workflow_id: "generation.text.fast.v1", operation: "generate" });
    const child = store.createRecord(root, { owner, parent_job_id: parent.job_id });
    for (const job of [parent, child]) { job.status = "RUNNING"; job.created_at = "2020-01-01T00:00:00Z"; store.write(root, job); }
    parent.child_jobs = [child.job_id]; store.write(root, parent); active.push(parent, child);
  }
  const counts = instrument(t, root), index = store.jobIndex(root);
  counts.reads.length = 0; counts.scans = 0;
  for (let i = 0; i < 6; i++) await recovery.recoverCompletedJobs(root, { jobs: index.activeJobs(), createdBefore: "2026-01-01T00:00:00Z" });
  assert.equal(counts.scans, 0); assert.equal(counts.reads.length, 12);
  assert.deepEqual(new Set(counts.reads), new Set(active.map(job => job.job_id)));
});

test("atomic writes from another process update latest scope, children, terminal state and explicit batch resume", async t => {
  const { root } = fixture(t), index = store.jobIndex(root), counts = instrument(t, root);
  const childId = store.newId(), parentId = store.newId();
  const child = { job_id: childId, parent_job_id: parentId, owner, status: "RUNNING", created_at: "2026-01-01T00:00:00Z" };
  const parent = { ...child, job_id: parentId, parent_job_id: null, child_jobs: [childId], schema_version: 3, transitions: [] };
  const external = (job) => cp.execFileSync(process.execPath, ["-e", 'require(process.argv[1]).write(process.argv[2], JSON.parse(process.argv[3]))', require.resolve("../src/store"), root, JSON.stringify(job)]);
  external(child); external(parent); await sleep(65);
  assert.equal(index.latest(owner).job_id, parentId); assert.equal(index.get(childId).status, "RUNNING");
  assert.equal(index.latest({ ...owner, user_id: "different" }), null);
  assert.equal(index.activeJobs().length, 1);
  external({ ...parent, status: "PARTIAL", artifacts: [{ filename: "a.png" }] }); await sleep(65);
  assert.equal(index.activeJobs().length, 0); assert.equal(index.takePending().length, 1);
  store.reopenAndWrite(root, store.read(root, parentId)); await sleep(65);
  assert.equal(index.activeJobs().length, 1);
  fs.unlinkSync(store.jobFile(root, parentId)); await sleep(65); assert.equal(index.latest(owner), null);
  assert.equal(counts.scans, 0);
});

test("watcher handles missing and replaced jobs directory; temporary files never become jobs", async t => {
  const { root } = fixture(t); fs.rmdirSync(path.join(root, "jobs"));
  const index = createJobIndex(root, store); t.after(() => index.close());
  fs.mkdirSync(path.join(root, "jobs"));
  const job = store.createRecord(root, { owner }); await sleep(100);
  assert.equal(index.get(job.job_id).status, "CREATED");
  fs.writeFileSync(path.join(root, "jobs", job.job_id + ".json.123.tmp"), "incomplete");
  fs.renameSync(path.join(root, "jobs"), path.join(root, "old-jobs")); fs.mkdirSync(path.join(root, "jobs"));
  const second = store.createRecord(root, { owner }); await sleep(100);
  assert.equal(index.get(job.job_id), null); assert.equal(index.get(second.job_id).status, "CREATED");
});

test("identity response index watches late worker responses and only returns exact interrupted parents", async t => {
  const { root } = fixture(t), bridgeRoot = path.join(root, "bridge");
  for (const name of ["responses", "process", "acks"]) fs.mkdirSync(path.join(bridgeRoot, name), { recursive: true });
  const item = { stateRoot: bridgeRoot }, active = [{ job_id: store.newId() }];
  const responseIndex = recovery.createResponseIndex(root); t.after(() => responseIndex.close());
  assert.deepEqual(responseIndex.names(item, active), []);
  const id = crypto.randomUUID(); fs.mkdirSync(path.join(bridgeRoot, "process", id));
  fs.writeFileSync(path.join(bridgeRoot, "process", id, "identity_worker.json"), JSON.stringify({ argv: ["worker", "--cancel-file", `/trusted/aag-image-agent-state/cancel/${active[0].job_id}`] }));
  fs.writeFileSync(path.join(bridgeRoot, "responses", id + ".json"), "{}"); await sleep(65);
  assert.deepEqual(responseIndex.names(item, active), [id + ".json"]);
  assert.deepEqual(responseIndex.names(item, [{ job_id: store.newId() }]), []);
});

test("new terminal writes wake a settled reconciler, retry a native-row race, then retire completed work", async t => {
  const { root } = fixture(t); let lookups = 0;
  const core = { store, recovery, runtime: {} };
  const server = { WorkspaceChats: { async where() { return []; } }, WorkspaceAgentInvocation: { async get() { lookups++; return null; } } };
  const controller = progress.startImageReconciler({ stateRoot: root, core, server, intervalMs: 20 });
  try {
    await controller.settle(); assert.equal(lookups, 0);
    const job = store.createRecord(root, { owner });
    job.status = "COMPLETED"; job.artifacts = [{ filename: "new.png" }]; store.write(root, job);
    await sleep(140); assert.ok(lookups >= 2, "Current completion must retry a briefly missing native chat binding");
    job.presentation = { status: "COMPLETED" }; store.write(root, job);
    await sleep(100); const settled = lookups; await sleep(100); assert.equal(lookups, settled);
    assert.equal(store.read(root, job.job_id).artifacts[0].filename, "new.png");
  } finally { controller.close(); }
});

test("one-shot batch reconciliation preserves logical child IDs and collection counts", async t => {
  const { root } = fixture(t), runtime = require("../src/runtime"), presentation = require("../integrations/anythingllm/aagArtifactPresentation");
  const parent = store.createRecord(root, { schema_version: 3, owner, operation: "multi_generate", count: 2, requested_count: 2 });
  const children = [1, 2].map(index => {
    const child = store.createRecord(root, { owner, parent_job_id: parent.job_id, child_index: index });
    child.status = "COMPLETED"; child.artifacts = [{ artifact_id: `artifact-${crypto.randomUUID()}`, child_job_id: child.job_id, url: `http://127.0.0.1:18190/files/GEN-batch-${index}.png`, sha256: "a".repeat(64), width: 512, height: 512 }]; store.write(root, child); return child;
  });
  parent.child_jobs = children.map(child => child.job_id); parent.artifacts = children.flatMap(child => child.artifacts); parent.status = "COMPLETED"; store.write(root, parent);
  let chat = { id: 1, prompt: "batch request", createdAt: 1000, response: "{}", include: true };
  const server = {
    WorkspaceAgentInvocation: { async get() { return { uuid: "missing", workspace_id: 10, thread_id: 20, user_id: null, prompt: "signed batch request", createdAt: 1000 }; }, async close() {} },
    WorkspaceChats: { async where() { return [chat]; }, async upsert(id, values) { chat = { ...chat, response: JSON.stringify(values.response) }; return {}; }, async get() { return chat; } },
    WorkspaceThread: { async get() { return null; } }, Workspace: { async get() { return null; } },
    history: { visibleComposerPrompt() { return "batch request"; } },
    presentation: { ...presentation, async buildPresentationOutputs({ parsed }) {
      assert.equal(parsed.isBatch, true); assert.equal(parsed.collectionComplete, true); assert.equal(parsed.requestedCount, 2); assert.equal(parsed.completedCount, 2);
      assert.deepEqual(parsed.artifacts.map(a => a.childJobId), children.map(c => c.job_id)); assert.deepEqual(parsed.artifacts.map(a => a.logicalIndex), [1, 2]);
      return parsed.artifacts.map(artifact => ({ type: "imageGenerationCard", payload: { jobId: parent.job_id, artifactId: artifact.artifactId } }));
    } },
  };
  // The previous single-image serializer cannot parse a batch card at all.
  assert.throws(() => presentation.parseAagImageResult(runtime.resultEnvelope(parent)), { code: "AAG_ARTIFACT_PRESENTATION_FAILED" });
  const result = await progress.bindCompletedJob(parent, { store, runtime }, server, { stateRoot: root });
  assert.equal(result.recovered, true); assert.equal(JSON.parse(chat.response).outputs.length, 2); assert.equal(store.read(root, parent.job_id).presentation.status, "COMPLETED");
});

test("a transient binding error retains only that pending completion, including historical recovery", async t => {
  const { root, jobs } = fixture(t, 25), seen = [], failedId = jobs[0].job_id;
  const errorOwner = { ...owner, invocation_id: failedId };
  jobs[0].owner = errorOwner; store.write(root, jobs[0]);
  let outage = true;
  const core = { store, recovery, runtime: {} };
  const server = { WorkspaceChats: { async where() { return []; } }, WorkspaceAgentInvocation: { async get({ uuid }) {
    seen.push(uuid); if (uuid === failedId && outage) throw new Error("Temporary database outage"); return null;
  } } };
  const controller = progress.startImageReconciler({ stateRoot: root, core, server, intervalMs: 20 });
  try {
    await sleep(160); assert.equal(seen.filter(id => id === "missing").length, 24); assert.ok(seen.filter(id => id === failedId).length >= 2);
    outage = false; await sleep(80); const count = seen.length; await sleep(80); assert.equal(seen.length, count);
  } finally { controller.close(); }
});

test("a first-install state root created in separate steps is observed before its first job", async t => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "aag-index-first-install-"));
  const root = path.join(base, "nested", "state"), index = createJobIndex(root, store);
  t.after(() => { index.close(); fs.rmSync(base, { recursive: true, force: true }); });
  fs.mkdirSync(path.join(base, "nested")); await sleep(60);
  fs.mkdirSync(root); await sleep(60);
  fs.mkdirSync(path.join(root, "jobs")); const job = store.createRecord(root, { owner }); await sleep(100);
  assert.equal(index.get(job.job_id)?.status, "CREATED"); assert.equal(index.activeJobs().length, 1);
});
