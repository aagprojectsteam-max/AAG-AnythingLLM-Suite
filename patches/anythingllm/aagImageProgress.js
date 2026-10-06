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
  const publish = () => bindCompletedJobLocked(job, core, server, options);
  // Production callers share the exact presentation module used by AgentHandler.
  // Injected test adapters without a publisher retain the prior contract.
  return server.presentation?.withArtifactPublication
    ? server.presentation.withArtifactPublication(job.job_id, publish)
    : publish();
}

async function bindCompletedJobLocked(job, core, server, options = {}) {
  if (
    job.parent_job_id ||
    !["COMPLETED", "PARTIAL"].includes(job.status) ||
    !Array.isArray(job.artifacts) ||
    job.artifacts.length < 1
  ) return null;
  const binding = await presentationBinding(job, server);
  if (!binding) return null;
  if (!binding.alreadyBound) {
    // Batch cards require their logical child IDs/counts from the existing
    // status serializer; a single-image envelope cannot represent a collection.
    const result = job.operation === "multi_generate" && Number(job.schema_version) >= 3
      ? await core.runtime.jobAction({ action: "status", job_id: job.job_id }, {
          AAG_IMAGE_AGENT_STATE_ROOT: options.stateRoot || DEFAULT_STATE_ROOT,
          AAG_WORKSPACE_ID: job.owner.workspace_id,
          AAG_THREAD_ID: job.owner.thread_id,
          AAG_USER_ID: job.owner.user_id,
        })
      : core.runtime.resultEnvelope(job);
    const parsed = server.presentation.parseAagImageResult(result);
    if (!parsed) return null;
    const outputs = await server.presentation.buildPresentationOutputs({
      parsed,
      prompt: binding.visiblePrompt,
      generatedImagesPath: server.generatedImagesPath,
      fetchImpl: options.fetchImpl || globalThis.fetch,
      naming: { ...options.naming, job, stateRoot: options.stateRoot || DEFAULT_STATE_ROOT },
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
  const index = options.index || core.store.jobIndex(root, options);
  const recovered = await core.recovery.recoverCompletedJobs(root, {
    ...options,
    runtime: core.runtime,
    jobs: index.activeJobs(),
    createdBefore: options.recoveryCutoff,
  });
  const pending = new Map(index.takePending().map(job => [job.job_id, job]));
  for (const id of options.retryIds || []) {
    const job = index.get(id);
    if (job) pending.set(id, job);
  }
  for (const job of recovered) pending.set(job.job_id, job);
  const bindings = [], unbound = [], errors = [];
  for (const job of pending.values()) {
    if (job.presentation?.status === "COMPLETED") continue;
    try {
      const bound = await bindCompletedJob(structuredClone(job), core, server, options);
      if (bound) bindings.push(bound);
      else unbound.push(job.job_id);
    } catch (error) {
      errors.push(job.job_id);
      options.logger?.(`[AAG-IMAGE] presentation reconciliation skipped ${job.job_id}: ${error.message}`);
    }
  }
  return { recovered, bindings, unbound, errors };
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
  const index = options.index || core.store.jobIndex(root, options);
  const job = index.latest({ workspace_id: workspace.id, thread_id: thread.id, user_id: ownerUser });
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
  const children = (job.child_jobs || []).map((id) => index.get(id)).filter(Boolean);
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

const reconcilers = new Map();
function startImageReconciler(options = {}) {
  const root = options.stateRoot || DEFAULT_STATE_ROOT;
  if (reconcilers.has(root)) return reconcilers.get(root);
  const core = loadCore(options.core || {});
  const index = core.store.jobIndex(root, options);
  // Do not race the original verification path of a job created by this server.
  const recoveryCutoff = options.recoveryCutoff || new Date().toISOString();
  const retry = new Map();
  let timer = null, running = false, dirty = false, closed = false;
  const schedule = (delay = 25) => {
    if (closed) return;
    if (running) { dirty = true; return; }
    clearTimeout(timer);
    timer = setTimeout(tick, delay); timer.unref?.();
  };
  const responseIndex = core.recovery.createResponseIndex(root, { ...options, onChange: schedule });
  const tick = async () => {
    if (closed || running) return;
    clearTimeout(timer); timer = null; running = true; dirty = false;
    try {
      const result = await reconcileImageState({ ...options, core, index, recoveryCutoff, responseIndex, retryIds: retry.keys() });
      const remaining = new Set([...result.unbound, ...result.errors]);
      for (const id of retry.keys()) if (!remaining.has(id)) retry.delete(id);
      for (const id of remaining) {
        const job = index.get(id);
        // Missing historical invocations are not unfinished image work. New
        // completions get a bounded grace period for concurrent native chat writes.
        if (result.errors.includes(id)) {
          // A failed fetch/durability operation is still pending work. Preserve
          // recovery across temporary outages without revisiting the archive.
          retry.set(id, Infinity);
        } else if (eventTime(job?.created_at) < eventTime(recoveryCutoff)) {
          retry.delete(id);
        } else if (!retry.has(id) || !Number.isFinite(retry.get(id))) {
          retry.set(id, Date.now() + 60_000);
        }
        if (retry.get(id) <= Date.now()) retry.delete(id);
      }
    } catch (error) {
      options.logger?.(`[AAG-IMAGE] reconciliation pass failed: ${error.message}`);
    } finally {
      running = false;
      if (dirty) schedule();
      else if (retry.size || index.activeJobs().some(job => core.recovery.recoveryEligible(job, recoveryCutoff))) schedule(options.intervalMs || RECONCILE_INTERVAL_MS);
    }
  };
  const unsubscribe = index.subscribe(schedule);
  const controller = {
    close() {
      closed = true; clearTimeout(timer); unsubscribe(); responseIndex.close();
      core.store.closeJobIndex(root); reconcilers.delete(root);
    },
    async settle() { while (running || timer) await new Promise(resolve => setTimeout(resolve, 10)); },
  };
  reconcilers.set(root, controller);
  schedule(0);
  return controller;
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
