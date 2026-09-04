"use strict";

const path = require("path");

const DEFAULT_STATE_ROOT = "/app/server/storage/aag-image-agent-state";
const ANYTHINGLLM_SERVER_ROOT = "/app/server";
const DEPLOYED_SKILL_ROOT = "/app/server/storage/plugins/agent-skills/aag-image-task";
const DEPLOYED_RUNTIME_BRIDGE_ROOT =
  "/app/server/storage/aag-image-agent-integration/runtime-context-bridge";
const RECONCILE_INTERVAL_MS = 5_000;
const FRIENDLY_ERRORS = Object.freeze({
  SOURCE_REQUIRED: "The selected source image is unavailable. Choose a valid source and try again.",
  ENGINE_CRASH: "Image generation stopped safely. You can try again with a new message.",
  ENGINE_TIMEOUT: "Image generation took too long and stopped safely.",
  ENGINE_STALLED: "Image generation stopped progressing.",
  ENGINE_STALLED_RECOVERED: "Image generation stopped responding. The image engine was safely recovered. You can try again.",
  ENGINE_INTERRUPT_FAILED: "Image generation stopped progressing, but a safe engine interrupt could not be completed.",
  ENGINE_SERVICE_RECOVERY_REQUIRED: "Image generation stopped responding and the image engine requires controlled recovery before another request.",
  ENGINE_DEVICE_HANG: "The image device stopped responding and requires controlled recovery.",
  CAPABILITY_INCONSISTENT: "This image capability is temporarily unavailable.",
  PROMPT_SEMANTIC_DRIFT: "The prepared image instructions did not preserve your request, so generation was stopped safely.",
  JOB_CANCELLED: "The image request was cancelled.",
});

function loadCore(overrides = {}) {
  if (overrides.runtime && overrides.recovery && overrides.store)
    return overrides;
  const sourceRoot = path.resolve(__dirname, "../../src");
  function load(name) {
    try { return require(path.join(DEPLOYED_SKILL_ROOT, name)); }
    catch { return require(path.join(sourceRoot, name)); }
  }
  return {
    runtime: overrides.runtime || load("runtime.js"),
    recovery: overrides.recovery || load("recovery.js"),
    store: overrides.store || load("store.js"),
  };
}

function loadServer(overrides = {}) {
  if (overrides.WorkspaceChats) return overrides;
  return {
    WorkspaceChats: require(path.join(ANYTHINGLLM_SERVER_ROOT, "models/workspaceChats.js")).WorkspaceChats,
    WorkspaceAgentInvocation: require(path.join(ANYTHINGLLM_SERVER_ROOT, "models/workspaceAgentInvocation.js")).WorkspaceAgentInvocation,
    WorkspaceThread: require(path.join(ANYTHINGLLM_SERVER_ROOT, "models/workspaceThread.js")).WorkspaceThread,
    Workspace: require(path.join(ANYTHINGLLM_SERVER_ROOT, "models/workspace.js")).Workspace,
    generatedImagesPath: require(path.join(ANYTHINGLLM_SERVER_ROOT, "utils/files/index.js")).generatedImagesPath,
    presentation: require(path.join(DEPLOYED_RUNTIME_BRIDGE_ROOT, "aagArtifactPresentation.js")),
    history: require(path.join(DEPLOYED_RUNTIME_BRIDGE_ROOT, "aagComposerHistory.js")),
  };
}

function parseResponse(value) {
  if (value && typeof value === "object") return value;
  try {
    const parsed = JSON.parse(String(value || "{}"));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function outputHasJob(chat, jobId) {
  return (parseResponse(chat?.response).outputs || []).some(
    (output) =>
      output?.type === "imageGenerationCard" &&
      output?.payload?.jobId === jobId
  );
}

function ownerMatches(job, workspaceId, threadId, userId) {
  return Boolean(
    job?.owner &&
    String(job.owner.workspace_id) === String(workspaceId) &&
    String(job.owner.thread_id) === String(threadId) &&
    String(job.owner.user_id) === String(userId ?? "unknown")
  );
}

function eventTime(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function closestPendingChat(chats, visiblePrompt, invocationCreatedAt) {
  const candidates = chats
    .filter((chat) => chat.prompt === visiblePrompt)
    .map((chat) => ({ chat, distance: Math.abs(eventTime(chat.createdAt) - invocationCreatedAt) }))
    .filter(({ distance }) => distance <= 15_000)
    .sort((left, right) => left.distance - right.distance || right.chat.id - left.chat.id);
  if (!candidates.length) return null;
  if (candidates.length > 1 && candidates[0].distance === candidates[1].distance)
    return null;
  return candidates[0].chat;
}

async function presentationBinding(job, server) {
  const invocation = await server.WorkspaceAgentInvocation.get({
    uuid: String(job.owner.invocation_id),
  });
  if (
    !invocation ||
    String(invocation.workspace_id) !== String(job.owner.workspace_id) ||
    String(invocation.thread_id) !== String(job.owner.thread_id) ||
    String(invocation.user_id ?? "unknown") !== String(job.owner.user_id)
  ) return null;
  const chats = await server.WorkspaceChats.where({
    workspaceId: Number(job.owner.workspace_id),
    thread_id: Number(job.owner.thread_id),
    user_id: invocation.user_id || null,
  });
  const alreadyBound = chats.find((chat) => outputHasJob(chat, job.job_id));
  if (alreadyBound) return { invocation, chat: alreadyBound, alreadyBound: true };
  const visiblePrompt = server.history.visibleComposerPrompt(invocation.prompt);
  if (visiblePrompt === invocation.prompt) return null;
  const chat = closestPendingChat(
    chats,
    visiblePrompt,
    eventTime(invocation.createdAt)
  );
  return chat ? { invocation, chat, visiblePrompt, alreadyBound: false } : null;
}

async function bindCompletedJob(job, core, server, options = {}) {
  if (
    job.parent_job_id ||
    !["COMPLETED", "PARTIAL"].includes(job.status) ||
    !Array.isArray(job.artifacts) ||
    job.artifacts.length < 1
  ) return null;
  const binding = await presentationBinding(job, server);
  if (!binding) return null;
  if (!binding.alreadyBound) {
    const parsed = server.presentation.parseAagImageResult(
      core.runtime.resultEnvelope(job)
    );
    if (!parsed) return null;
    const outputs = await server.presentation.buildPresentationOutputs({
      parsed,
      prompt: binding.visiblePrompt,
      generatedImagesPath: server.generatedImagesPath,
      fetchImpl: options.fetchImpl || globalThis.fetch,
    });
    const { message } = await server.WorkspaceChats.upsert(binding.chat.id, {
      workspaceId: Number(job.owner.workspace_id),
      prompt: binding.visiblePrompt,
      response: {
        text: server.presentation.normalizeFinalText("", parsed.artifacts.map((artifact) => artifact.url)),
        sources: [],
        type: "chat",
        attachments: [],
        outputs,
      },
      user: { id: binding.invocation.user_id || null },
      threadId: Number(job.owner.thread_id),
      include: true,
    });
    if (message) throw new Error(`AnythingLLM chat persistence failed: ${message}`);
    const committed = await server.WorkspaceChats.get({ id: Number(binding.chat.id) });
    if (!committed || committed.include !== true || !outputHasJob(committed, job.job_id))
      throw new Error("AnythingLLM artifact/chat durability verification failed");
    await server.WorkspaceAgentInvocation.close(binding.invocation.uuid);
    const thread = await server.WorkspaceThread.get({ id: Number(job.owner.thread_id) });
    const workspace = await server.Workspace.get({ id: Number(job.owner.workspace_id) });
    if (thread && workspace) {
      await server.WorkspaceThread.autoRenameThread({
        workspace,
        thread,
        user: binding.invocation.user_id ? { id: binding.invocation.user_id } : null,
        prompt: binding.visiblePrompt,
      });
    }
  }
  job.presentation = {
    status: "COMPLETED",
    chat_id: Number(binding.chat.id),
    committed_at: new Date().toISOString(),
    recovered: !binding.alreadyBound,
  };
  core.store.write(options.stateRoot || DEFAULT_STATE_ROOT, job);
  return { jobId: job.job_id, chatId: Number(binding.chat.id), recovered: !binding.alreadyBound };
}

async function reconcileImageState(options = {}) {
  const core = loadCore(options.core || {});
  const server = loadServer(options.server || {});
  const root = options.stateRoot || DEFAULT_STATE_ROOT;
  const recovered = await core.recovery.recoverCompletedJobs(root, {
    runtime: core.runtime,
    logger: options.logger,
    createdBefore: options.recoveryCutoff,
  });
  const jobs = core.store.listJobs(root)
    .filter((job) => !job.parent_job_id && ["COMPLETED", "PARTIAL"].includes(job.status))
    .sort((left, right) => eventTime(left.updated_at) - eventTime(right.updated_at));
  const bindings = [];
  for (const job of jobs) {
    if (job.presentation?.status === "COMPLETED") continue;
    try {
      const bound = await bindCompletedJob(job, core, server, options);
      if (bound) bindings.push(bound);
    } catch (error) {
      options.logger?.(`[AAG-IMAGE] presentation reconciliation skipped ${job.job_id}: ${error.message}`);
    }
  }
  return { recovered, bindings };
}

function stageRows(snapshot, chatDurable) {
  const lifecycle = snapshot?.lifecycle || {};
  if (lifecycle.stallDetected) {
    const rows = [
      { key: "requestReceived", state: "complete" },
      { key: "preparingInstructions", state: "complete" },
      { key: "workflowStarted", state: "complete" },
      { key: "generationStalled", state: "warning" },
    ];
    if (lifecycle.recoveryInProgress) {
      rows.push({ key: "recoveringEngine", state: "current" });
    } else {
      rows.push({ key: "imageGenerationFailed", state: "failed" });
      if (lifecycle.engineRecovered) rows.push({ key: "engineRecovered", state: "complete" });
      else if (lifecycle.serviceRecoveryRequired) rows.push({ key: "engineRecoveryRequired", state: "warning" });
    }
    rows.push(
      { key: "processingResult", state: "future" },
      { key: "returningToChat", state: "future" },
      { key: "complete", state: "future" }
    );
    return rows;
  }
  const completed = [
    true,
    Boolean(lifecycle.instructionsPrepared),
    Boolean(lifecycle.workflowStarted),
    Boolean(lifecycle.generatingCompleted),
    Boolean(lifecycle.processingCompleted),
    Boolean(chatDurable),
    Boolean(chatDurable && snapshot?.status === "COMPLETED"),
  ];
  const keys = [
    "requestReceived",
    "preparingInstructions",
    "workflowStarted",
    "creatingImage",
    "processingResult",
    "returningToChat",
    "complete",
  ];
  const failedAt = snapshot?.failed
    ? Math.max(0, completed.findIndex((value) => !value))
    : -1;
  let currentFound = false;
  return keys.map((key, index) => {
    let state = completed[index] ? "complete" : "future";
    if (index === failedAt) state = "failed";
    else if (!snapshot?.failed && !completed[index] && !currentFound) {
      state = "current";
      currentFound = true;
    }
    return { key, state };
  });
}

async function threadProgress({ workspace, thread, userId = null }, options = {}) {
  const core = loadCore(options.core || {});
  const server = loadServer(options.server || {});
  const root = options.stateRoot || DEFAULT_STATE_ROOT;
  const ownerUser = userId ?? thread.user_id ?? "unknown";
  const parents = core.store.listJobs(root)
    .filter((job) => !job.parent_job_id && ownerMatches(job, workspace.id, thread.id, ownerUser))
    .sort((left, right) => eventTime(right.created_at) - eventTime(left.created_at));
  const job = parents[0] || null;
  const chats = await server.WorkspaceChats.where(
    { workspaceId: Number(workspace.id), thread_id: Number(thread.id), user_id: userId || null },
    20,
    { createdAt: "desc" }
  );
  const pending = chats.find(
    (chat) => parseResponse(chat.response).aagImagePending === true
  );
  if (
    pending &&
    (!job || eventTime(pending.createdAt) > eventTime(job.created_at))
  ) {
    if (!pending) return { active: false };
    const snapshot = {
      status: "PREPARING",
      failed: false,
      startedAt: new Date(eventTime(pending.createdAt)).toISOString(),
      updatedAt: new Date(eventTime(pending.lastUpdatedAt || pending.createdAt)).toISOString(),
      lifecycle: { instructionsPrepared: false },
    };
    return { active: true, status: snapshot.status, startedAt: snapshot.startedAt, stages: stageRows(snapshot, false) };
  }
  if (!job) return { active: false };
  const children = (job.child_jobs || []).map((id) => core.store.read(root, id));
  const snapshot = core.recovery.progressSnapshot(job, children);
  const bound = chats.some((chat) => outputHasJob(chat, job.job_id));
  const presentationAt = eventTime(job.presentation?.committed_at);
  const recentlyCompleted =
    bound && presentationAt > 0 && Date.now() - presentationAt < 12_000;
  const visibleFailure = snapshot.failed
    ? FRIENDLY_ERRORS[snapshot.errorCode] || "The image request stopped safely. You can try again with a new message."
    : null;
  return {
    active: !bound || snapshot.failed || recentlyCompleted,
    jobId: snapshot.jobId,
    status: snapshot.status,
    startedAt: snapshot.startedAt,
    updatedAt: snapshot.updatedAt,
    activeStageStartedAt: (() => {
      const current = stageRows(snapshot, bound).find(
        (stage) => stage.state === "current"
      )?.key;
      const child =
        children.find((item) => item.status === "RUNNING") || children.at(-1);
      if (current === "creatingImage")
        return (
          child?.progress?.engine_started_at ||
          child?.started_at ||
          snapshot.startedAt
        );
      if (current === "processingResult")
        return child?.progress?.processing_started_at || snapshot.updatedAt;
      if (current === "returningToChat")
        return child?.progress?.artifact_verified_at || snapshot.updatedAt;
      if (current === "recoveringEngine")
        return snapshot.recovery?.startedAt || snapshot.recovery?.detectedAt || snapshot.updatedAt;
      return snapshot.updatedAt || snapshot.startedAt;
    })(),
    stages: stageRows(snapshot, bound),
    failure: visibleFailure,
    technicalCode: snapshot.failed ? snapshot.errorCode : null,
    recovery: snapshot.recovery,
  };
}

let interval = null;
function startImageReconciler(options = {}) {
  if (interval) return interval;
  // Only jobs which predate this server process are eligible for restart
  // recovery. A live job may already have an engine output while its original
  // request is still verifying it; the reconciler must never race that path.
  const recoveryCutoff = options.recoveryCutoff || new Date().toISOString();
  let running = false;
  const tick = async () => {
    if (running) return;
    running = true;
    try { await reconcileImageState({ ...options, recoveryCutoff }); }
    catch (error) { options.logger?.(`[AAG-IMAGE] reconciliation pass failed: ${error.message}`); }
    finally { running = false; }
  };
  void tick();
  interval = setInterval(tick, options.intervalMs || RECONCILE_INTERVAL_MS);
  interval.unref?.();
  return interval;
}

module.exports = {
  DEFAULT_STATE_ROOT,
  FRIENDLY_ERRORS,
  parseResponse,
  outputHasJob,
  ownerMatches,
  eventTime,
  closestPendingChat,
  presentationBinding,
  bindCompletedJob,
  reconcileImageState,
  stageRows,
  threadProgress,
  startImageReconciler,
};
