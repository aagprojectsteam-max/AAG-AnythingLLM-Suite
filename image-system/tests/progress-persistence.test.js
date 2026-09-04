"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const runtime = require("../src/runtime");
const store = require("../src/store");
const recovery = require("../src/recovery");
const progress = require("../integrations/anythingllm/aagImageProgress");
const history = require("../integrations/anythingllm/aagComposerHistory");
const presentation = require("../integrations/anythingllm/aagArtifactPresentation");

function temporary(prefix = "aag-progress-test-") {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function fakePng(width = 8, height = 8, byte = 1) {
  const value = Buffer.alloc(160, byte);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(value);
  value.write("IHDR", 12, "ascii");
  value.writeUInt32BE(width, 16);
  value.writeUInt32BE(height, 20);
  return value;
}

function signedPrompt(userText) {
  const intent = {
    user_request_sha256: crypto.createHash("sha256").update(userText).digest("hex"),
  };
  return [
    `AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1=${JSON.stringify(intent)}`,
    "USER_CREATIVE_DIRECTION=",
    userText,
    `AAG_COMPOSER_INTENT_SIGNATURE_V1=${"a".repeat(64)}`,
  ].join("\n");
}

test("Composer initial native row is included and durably marked pending before long work", async () => {
  const calls = [];
  const WorkspaceChats = {
    async new(values) { calls.push(values); return { chat: { id: 1 }, message: null }; },
    async upsert() { return { chat: null, message: null }; },
  };
  const Workspace = {
    async get({ id }) { return { id, slug: id === 10 ? "image-generator" : "other" }; },
  };
  history.installComposerHistoryPersistence({ WorkspaceChats, Workspace });
  await WorkspaceChats.new({
    workspaceId: 10,
    prompt: signedPrompt("exact visible request"),
    response: {},
    include: false,
    threadId: 20,
  });
  assert.equal(calls[0].prompt, "exact visible request");
  assert.equal(calls[0].include, true);
  assert.equal(calls[0].response.aagImagePending, true);
  assert.deepEqual(calls[0].response.outputs, []);

  await WorkspaceChats.new({
    workspaceId: 10,
    prompt: "plain native image request",
    response: {},
    include: false,
    threadId: 21,
  });
  assert.equal(calls[1].prompt, "plain native image request");
  assert.equal(calls[1].include, true);
  assert.equal(calls[1].response.aagImagePending, true);

  await WorkspaceChats.new({
    workspaceId: 11,
    prompt: "ordinary workspace request",
    response: {},
    include: false,
    threadId: 22,
  });
  assert.equal(calls[2].include, false);
  assert.deepEqual(calls[2].response, {});
});

test("progress stations never check an unproved later boundary", () => {
  const generating = progress.stageRows({
    status: "RUNNING",
    failed: false,
    lifecycle: {
      instructionsPrepared: true,
      workflowStarted: true,
      generatingCompleted: false,
      processingCompleted: false,
    },
  }, false);
  assert.deepEqual(generating.map((stage) => stage.state), [
    "complete", "complete", "complete", "current", "future", "future", "future",
  ]);
  const failed = progress.stageRows({
    status: "FAILED",
    failed: true,
    lifecycle: {
      instructionsPrepared: true,
      workflowStarted: true,
      generatingCompleted: false,
      processingCompleted: false,
    },
  }, false);
  assert.equal(failed[3].state, "failed");
  assert.ok(failed.slice(4).every((stage) => stage.state === "future"));
  assert.match(progress.FRIENDLY_ERRORS.ENGINE_CRASH, /stopped safely/i);
  assert.doesNotMatch(progress.FRIENDLY_ERRORS.ENGINE_CRASH, /ENGINE_CRASH|ComfyUI/i);
});

test("restart recovery excludes jobs created by the current server process", () => {
  const cutoff = "2026-09-02T17:00:00.000Z";
  assert.equal(
    recovery.recoveryEligible({ created_at: "2026-09-02T16:59:59.999Z" }, cutoff),
    true
  );
  assert.equal(
    recovery.recoveryEligible({ created_at: "2026-09-02T17:00:00.000Z" }, cutoff),
    false
  );
  assert.equal(
    recovery.recoveryEligible({ created_at: "2026-09-02T17:00:00.001Z" }, cutoff),
    false
  );
  assert.equal(recovery.recoveryEligible({ created_at: "invalid" }, cutoff), false);
  assert.equal(recovery.recoveryEligible({ created_at: "invalid" }, null), true);
});

test("completed identity response is recovered only through exact parent evidence and hash", async () => {
  const root = temporary();
  const bridgeRoot = temporary("aag-identity-recovery-");
  fs.mkdirSync(path.join(root, "jobs"), { recursive: true });
  for (const directory of ["responses", "acks", "process"]) fs.mkdirSync(path.join(bridgeRoot, directory), { recursive: true });
  const owner = {
    workspace_id: "10", thread_id: "224", user_id: "unknown",
    invocation_id: "invocation", turn_id: "turn",
  };
  const parent = store.createRecord(root, {
    release: "test", owner, operation: "transform",
    workflow_id: "transform.human.identity.scene.v1", count: 1,
  });
  const child = store.createRecord(root, {
    release: "test", owner, operation: "transform",
    workflow_id: parent.workflow_id, parent_job_id: parent.job_id, child_index: 1,
  });
  parent.child_jobs = [child.job_id];
  store.transition(parent, "VALIDATED"); store.transition(parent, "QUEUED"); store.transitionAndWrite(root, parent, "RUNNING");
  store.transition(child, "VALIDATED"); store.transition(child, "QUEUED"); store.transitionAndWrite(root, child, "RUNNING");
  const requestId = crypto.randomUUID();
  const filename = `REF-${requestId}.png`;
  const bytes = fakePng();
  const responseValue = {
    request_id: requestId,
    status: "PASS",
    artifact_filename: filename,
    artifact_sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
    completed_at: new Date().toISOString(),
  };
  fs.writeFileSync(path.join(bridgeRoot, "responses", `${requestId}.json`), JSON.stringify(responseValue));
  fs.mkdirSync(path.join(bridgeRoot, "process", requestId));
  fs.writeFileSync(path.join(bridgeRoot, "process", requestId, "identity_worker.json"), JSON.stringify({
    argv: ["worker", "--cancel-file", `/trusted/aag-image-agent-state/cancel/${parent.job_id}`],
  }));
  let acknowledged = false;
  const recovered = await recovery.recoverIdentityResponses(root, {
    identityBridges: [{
      workflow: parent.workflow_id,
      stateRoot: bridgeRoot,
      bridge: {
        validateResponse(value, expected) { assert.equal(expected, requestId); return value; },
        acknowledgeRecovered(id, output, verified) {
          acknowledged = id === requestId && output === filename && verified;
        },
      },
    }],
    runtime: { rememberArtifact() {} },
    deps: {
      fetch: async () => ({ ok: true, headers: { get: () => "image/png" }, arrayBuffer: async () => bytes }),
      inspectOutput: async () => ({ width: 8, height: 8, format: "png" }),
    },
  });
  assert.equal(recovered.length, 1);
  assert.equal(acknowledged, true);
  assert.equal(store.read(root, parent.job_id).status, "COMPLETED");
  assert.equal(store.read(root, child.job_id).artifacts[0].sha256, responseValue.artifact_sha256);
});

test("reconciler binds an existing verified artifact to the exact pending native chat without regeneration", async () => {
  const generatedImagesPath = temporary("aag-presented-");
  const bytes = fakePng();
  const digest = crypto.createHash("sha256").update(bytes).digest("hex");
  const userText = "persist this exact request";
  const invocation = {
    uuid: "invocation-1", workspace_id: 10, thread_id: 224, user_id: null,
    prompt: signedPrompt(userText), createdAt: 1_000,
  };
  let chat = {
    id: 715, workspaceId: 10, thread_id: 224, user_id: null,
    prompt: userText, response: JSON.stringify({ aagImagePending: true, outputs: [] }),
    include: true, createdAt: 1_100,
  };
  const job = {
    job_id: `aag-${crypto.randomUUID()}`,
    parent_job_id: null,
    owner: { workspace_id: "10", thread_id: "224", user_id: "unknown", invocation_id: invocation.uuid, turn_id: "turn" },
    operation: "generate", workflow_id: "generation.text.fast.v1", release: "test",
    status: "COMPLETED", artifacts: [{
      artifact_id: `artifact-${crypto.randomUUID()}`,
      filename: "GEN-existing.png", url: "http://127.0.0.1:18190/files/GEN-existing.png",
      sha256: digest, width: 8, height: 8,
    }],
  };
  let writeCount = 0;
  const server = {
    WorkspaceAgentInvocation: {
      async get() { return invocation; },
      async close() {},
    },
    WorkspaceChats: {
      async where() { return [chat]; },
      async upsert(id, values) {
        assert.equal(id, chat.id);
        chat = { ...chat, response: JSON.stringify(values.response), include: values.include };
        return { chat: null, message: null };
      },
      async get() { return chat; },
    },
    WorkspaceThread: { async get() { return null; }, async autoRenameThread() {} },
    Workspace: { async get() { return null; } },
    generatedImagesPath,
    presentation,
    history,
  };
  const bound = await progress.bindCompletedJob(
    job,
    { runtime, store: { write() { writeCount += 1; } } },
    server,
    {
      stateRoot: temporary("aag-state-"),
      fetchImpl: async () => ({ ok: true, headers: { get: () => "image/png" }, arrayBuffer: async () => bytes }),
    }
  );
  assert.equal(bound.recovered, true);
  assert.equal(writeCount, 1);
  assert.equal(chat.include, true);
  assert.equal(progress.outputHasJob(chat, job.job_id), true);
  assert.equal(fs.readdirSync(generatedImagesPath).length, 1);
});

test("final output quality remains postprocess-only and identity generation routing stays locked", async () => {
  const root = temporary("aag-quality-");
  const owner = {
    AAG_WORKSPACE_ID: "w", AAG_THREAD_ID: "t", AAG_USER_ID: "u",
    AAG_INVOCATION_UUID: "i", AAG_TURN_ID: "turn", AAG_IMAGE_AGENT_STATE_ROOT: root,
    AAG_INVOCATION_PROMPT: "a carefully composed neutral image",
    AAG_IMAGE_QUEUE_TIMEOUT_MS: 1000, AAG_IMAGE_LEASE_STALE_MS: 5000, AAG_IMAGE_QUEUE_POLL_MS: 25,
  };
  let enhanced = 0;
  const result = await runtime.createTask({
    operation: "generate",
    prompt: "A carefully composed neutral image with one clear subject, coherent lighting, balanced framing, readable depth, refined materials, accurate geometry, stable perspective, clean edges, detailed environment, natural shadows and professional visual finish.",
    source_policy: "auto",
    preservation: "none",
    final_output_quality: "enhanced_2x",
  }, owner, { deps: {
    scheduler: { engineActivity: async () => ({ active: false }), disableHeartbeat: true, sleep: async () => {} },
    adapters: {
      execute: async () => ["base.png"],
      finalOutputPostprocess: async (filename, quality) => {
        assert.equal(filename, "base.png"); assert.equal(quality, "enhanced_2x"); enhanced += 1; return "enhanced.png";
      },
      fetch: async (url) => ({ ok: true, headers: { get: () => "image/png" }, arrayBuffer: async () => url.includes("enhanced") ? fakePng(16, 16, 2) : fakePng(8, 8, 1) }),
      inspectOutput: async (bytes) => ({ width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), format: "png" }),
    },
  }});
  assert.match(result, /status=completed/);
  assert.match(result, /artifact_1_dimensions=16x16/);
  assert.equal(enhanced, 1);
  const identityTask = runtime.normalizeTask({
    operation: "transform", request: "same person in a new realistic scene",
    prompt: "same recognizable person in a new realistic scene",
    source_policy: "current_attachment", preservation: "identity",
    final_output_quality: "enhanced_2x",
  }, { ...owner, AAG_INVOCATION_ATTACHMENTS: [] });
  assert.equal(identityTask.quality, "auto");
  assert.equal(identityTask.final_output_quality, "enhanced_2x");
  assert.match(runtime.workflow(identityTask), /^transform\.human\.identity\./);
});
