"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError, classifyError, redact } = require("./errors");
const { text, one, integer, scope, sameOwner, stateRoot, ensureDirectory, sha256 } = require("./util");
const store = require("./store");
const scheduler = require("./scheduler");
const adapters = require("./adapters");
const promptQuality = require("./prompt-quality");
const selectiveKnowledge = require("./selective-knowledge");

const VERSION = "0.9.0-preview.13";
const MIN_BATCH_COUNT = 2;
const MAX_BATCH_COUNT = 10;
const BATCH_KEYS = new Set(["operation", "request", "collection_brief", "count", "quality", "final_output_quality", "items"]);
const ITEM_KEYS = new Set(["prompt", "aspect_ratio", "width", "height", "seed"]);

function requiredField(args, name) {
  if (!Object.hasOwn(args, name)) throw new AagError("INVALID_ARGUMENT", `The batch field ${name} is required.`);
}

function normalizeBatch(args = {}, runtime = {}) {
  if (!args || typeof args !== "object" || Array.isArray(args)) throw new AagError("INVALID_ARGUMENT", "The image batch arguments are invalid.");
  const unexpected = Object.keys(args).filter((key) => !BATCH_KEYS.has(key));
  if (unexpected.length) throw new AagError("INVALID_ARGUMENT", "The image batch contains an unsupported argument.", false, unexpected.join(","));
  for (const name of ["operation", "collection_brief", "count", "quality", "items"]) requiredField(args, name);
  const operation = one(args.operation, ["multi_generate"]);
  const owner = scope(runtime);
  if (owner.workspace_id === "unknown" || owner.thread_id === "unknown") throw new AagError("OWNER_SCOPE_REQUIRED", "A trusted workspace and conversation scope is required.");
  if (owner.invocation_id === "unknown" || owner.turn_id === "unknown") throw new AagError("TURN_SCOPE_REQUIRED", "A trusted invocation and user-turn scope is required.");
  const authoritativeRequest = text(runtime.AAG_INVOCATION_PROMPT, 4000);
  const upstreamRequest = text(args.request, 4000);
  const request = authoritativeRequest || upstreamRequest;
  if (!request) throw new AagError("INVALID_ARGUMENT", "A trusted batch request is required.");
  const collectionBrief = text(args.collection_brief, 4000, true);
  const count = integer(args.count, null, MIN_BATCH_COUNT, MAX_BATCH_COUNT);
  const quality = one(args.quality, ["auto", "fast", "balanced", "quality"]);
  const finalOutputQuality = one(args.final_output_quality, ["standard", "enhanced_2x"], "standard");
  if (!Array.isArray(args.items) || args.items.length !== count) throw new AagError("BATCH_COUNT_MISMATCH", "The batch must contain exactly one intended item for every requested image.");

  const fidelity = promptQuality.semanticFidelity(request, collectionBrief);
  if (fidelity.status !== "PASS") throw new AagError("PROMPT_SEMANTIC_DRIFT", "The workspace language model collection brief does not preserve the authoritative user request.", false, JSON.stringify(fidelity));

  const items = args.items.map((input, index) => {
    if (!input || typeof input !== "object" || Array.isArray(input)) throw new AagError("INVALID_ARGUMENT", `Batch item ${index + 1} is invalid.`);
    const extra = Object.keys(input).filter((key) => !ITEM_KEYS.has(key));
    if (extra.length) throw new AagError("INVALID_ARGUMENT", `Batch item ${index + 1} contains an unsupported argument.`, false, extra.join(","));
    requiredField(input, "prompt");
    const prompt = text(input.prompt, promptQuality.MAX_PROMPT_CHARS, true);
    const validated = promptQuality.validate({ authoritative: prompt, proposal: prompt });
    const item = {
      logical_child_id: `item-${String(index + 1).padStart(4, "0")}`,
      index: index + 1,
      prompt: validated.prompt,
      prompt_contract: validated.contract,
      aspect_ratio: one(input.aspect_ratio, ["auto", "1:1", "16:9", "9:16", "4:3", "3:2", "landscape", "portrait"], "auto"),
      seed: input.seed === undefined || input.seed === null || input.seed === "" ? null : integer(input.seed, null, 0, 2_147_483_647),
    };
    if (input.width !== undefined) item.width = integer(input.width, null, 256, 2048);
    if (input.height !== undefined) item.height = integer(input.height, null, 256, 2048);
    const atlasTask = {
      operation: "generate",
      request,
      prompt: item.prompt,
      preservation: "none",
      _aag_authoritative_request: item.prompt,
      _aag_upstream_request: upstreamRequest,
      _aag_prompt_contract: item.prompt_contract,
    };
    selectiveKnowledge.applyToTask(atlasTask);
    item.prompt = atlasTask.prompt;
    item.prompt_contract = atlasTask._aag_prompt_contract;
    item.atlas = atlasTask.atlas;
    return item;
  });

  return {
    operation,
    request,
    collection_brief: collectionBrief,
    count,
    quality,
    final_output_quality: finalOutputQuality,
    items,
    owner,
    _aag_authoritative_request: authoritativeRequest || upstreamRequest,
    _aag_upstream_request: upstreamRequest,
  };
}

function batchIdempotencyKey(batch) {
  return sha256(JSON.stringify([
    batch.owner.workspace_id,
    batch.owner.thread_id,
    batch.owner.user_id,
    batch.owner.invocation_id,
    batch.owner.turn_id,
    batch.operation,
  ]));
}

function plannedSeed(item) {
  return item.seed === null || item.seed === undefined
    ? crypto.randomInt(0, 2_147_483_648)
    : item.seed;
}

function childWorkflow(quality) {
  return quality === "quality" ? "generation.text.quality.v1" : "generation.text.fast.v1";
}

function countsFor(root, parent) {
  const children = (parent.child_jobs || []).map((id) => store.read(root, id));
  const completed = children.filter((child) => child.status === "COMPLETED" && child.artifacts?.length === 1).length;
  const failed = children.filter((child) => ["FAILED", "TIMED_OUT"].includes(child.status)).length;
  const cancelled = children.filter((child) => child.status === "CANCELLED").length;
  return {
    requested: Number(parent.requested_count || parent.count || children.length),
    completed,
    pending: children.length - completed - failed - cancelled,
    failed,
    cancelled,
    children,
  };
}

function orderedArtifacts(children) {
  return [...children]
    .sort((left, right) => Number(left.child_index) - Number(right.child_index))
    .filter((child) => child.status === "COMPLETED" && child.artifacts?.length === 1)
    .map((child) => child.artifacts[0]);
}

function resultEnvelope(root, job) {
  const snapshot = countsFor(root, job);
  const childrenById = new Map(snapshot.children.map((child) => [child.job_id, child]));
  const artifacts = orderedArtifacts(snapshot.children);
  const lines = [
    "AAG_IMAGE_RESULT",
    `status=${String(job.status || "FAILED").toLowerCase()}`,
    `job_id=${job.job_id || ""}`,
    `operation=${job.operation || "multi_generate"}`,
    `workflow=${job.workflow_id || "generation.batch.sequential.v1"}`,
    `release=${job.release || VERSION}`,
    `collection_id=${job.job_id || ""}`,
    `plan_sha256=${job.plan_sha256 || ""}`,
    `requested_count=${snapshot.requested}`,
    `completed_count=${snapshot.completed}`,
    `pending_count=${snapshot.pending}`,
    `failed_count=${snapshot.failed}`,
    `cancelled_count=${snapshot.cancelled}`,
    `artifact_count=${artifacts.length}`,
  ];
  artifacts.forEach((artifact, index) => {
    const n = index + 1;
    const child = childrenById.get(artifact.child_job_id);
    lines.push(`artifact_${n}_id=${artifact.artifact_id}`);
    lines.push(`artifact_${n}_url=${artifact.url}`);
    lines.push(`artifact_${n}_sha256=${artifact.sha256}`);
    lines.push(`artifact_${n}_dimensions=${artifact.width}x${artifact.height}`);
    lines.push(`artifact_${n}_child_job_id=${artifact.child_job_id}`);
    lines.push(`artifact_${n}_logical_index=${child?.child_index || n}`);
  });
  lines.push(`batch_export_ready=${job.status === "COMPLETED" && artifacts.length === snapshot.requested}`);
  lines.push(`resume_supported=${job.status !== "COMPLETED"}`);
  if (job.error) {
    lines.push(`error_code=${job.error.code}`);
    lines.push(`message=${job.error.message}`);
    lines.push("retryable=false");
    lines.push("same_turn_retry=forbidden");
    lines.push(`partial=${artifacts.length > 0}`);
  }
  return lines.join("\n");
}

function cancelMarker(root, jobId) {
  return path.join(root, "cancel", jobId);
}

function cancellationRequested(root, jobId) {
  try {
    const stat = fs.lstatSync(cancelMarker(root, jobId));
    return stat.isFile() && !stat.isSymbolicLink();
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

function clearCancellation(root, jobId) {
  try { fs.unlinkSync(cancelMarker(root, jobId)); }
  catch (error) { if (error?.code !== "ENOENT") throw error; }
}

function metadataDeps(root, child, base = {}) {
  const upstreamMetadata = base.onEngineMetadata;
  const upstreamProgress = base.onEngineProgress;
  return {
    ...base,
    onEngineMetadata(metadata = {}) {
      const allowed = {};
      for (const key of ["adapter", "prompt_id", "submitted_at", "completed_at", "elapsed_seconds", "model", "recipe_id", "model_file", "model_sha256", "text_encoder_file", "text_encoder_sha256", "vae_file", "vae_sha256", "workflow_sha256", "sampler", "scheduler", "steps", "cfg", "dimensions", "aspect", "aspect_source", "prompt_policy", "negative_prompt_policy", "offline_policy", "prompt_contract", "prompt_author", "prompt_quality_status", "prompt_fidelity_status", "prompt_structure_status", "final_prompt_sha256"]) {
        if (["elapsed_seconds", "steps", "cfg"].includes(key) && Number.isFinite(Number(metadata[key]))) allowed[key] = Number(metadata[key]);
        else if (metadata[key] !== undefined) allowed[key] = String(metadata[key]).slice(0, 200);
      }
      child.engine = { ...(child.engine || {}), ...allowed };
      child.progress = {
        ...(child.progress || {}),
        ...(allowed.submitted_at && !child.progress?.engine_started_at
          ? { engine_started_at: allowed.submitted_at }
          : {}),
        ...(allowed.completed_at
          ? { engine_completed_at: allowed.completed_at }
          : {}),
      };
      store.write(root, child);
      upstreamMetadata?.(allowed);
    },
    onEngineProgress(progress = {}) {
      const allowed = {};
      for (const key of [
        "engine_progress_source", "last_engine_progress_at",
        "last_engine_progress_event", "current_engine_node",
        "current_engine_node_class", "stall_detected_at",
        "recovery_action", "recovery_started_at",
        "recovery_completed_at", "recovery_outcome",
      ]) {
        if (progress[key] !== undefined) allowed[key] = String(progress[key]).slice(0, 200);
      }
      for (const key of ["current_engine_step", "current_engine_step_max", "sequence"]) {
        if (Number.isInteger(Number(progress[key])) && Number(progress[key]) >= 0) allowed[key] = Number(progress[key]);
      }
      child.progress = { ...(child.progress || {}), ...allowed };
      store.write(root, child);
      upstreamProgress?.(allowed);
    },
  };
}

function taskFor(parent, child) {
  const item = child.public_arguments;
  return {
    operation: "generate",
    request: parent.collection.request,
    prompt: item.prompt,
    source_policy: "auto",
    preservation: "none",
    quality: parent.collection.quality,
    final_output_quality: parent.final_output_quality || "standard",
    aspect_ratio: item.aspect_ratio,
    width: item.width,
    height: item.height,
    count: 1,
    seed: child.seed,
    owner: parent.owner,
    _aag_authoritative_request: item.prompt,
    _aag_prompt_contract: item.prompt_contract,
    atlas: child.atlas || null,
    _aag_parent_job_id: parent.job_id,
    _aag_child_job_id: child.job_id,
  };
}

function markUnfinishedCancelled(root, parent, detail) {
  for (const childId of parent.child_jobs || []) {
    const child = store.read(root, childId);
    if (store.TERMINAL.has(child.status)) continue;
    child.error = { code: "PARENT_CANCELLED", message: "The child was cancelled before execution.", retryable: false };
    store.transitionAndWrite(root, child, "CANCELLED", detail);
  }
}

async function executeParent(root, parent, runtime, context = {}) {
  const deps = context.deps || {};
  let lease;
  try {
    lease = await scheduler.acquire(root, parent, {
      waitMs: integer(runtime.AAG_IMAGE_QUEUE_TIMEOUT_MS, 30 * 60 * 1000, 1_000, 60 * 60 * 1000),
      staleMs: integer(runtime.AAG_IMAGE_LEASE_STALE_MS, 2 * 60 * 1000, 5_000, 15 * 60 * 1000),
      pollMs: integer(runtime.AAG_IMAGE_QUEUE_POLL_MS, 250, 25, 5_000),
      maxQueue: integer(runtime.AAG_IMAGE_QUEUE_MAX, 8, 1, 64),
      deps: deps.scheduler || {},
      isCancelled: () => cancellationRequested(root, parent.job_id),
    });
    parent = store.read(root, parent.job_id);
    if (cancellationRequested(root, parent.job_id)) throw new AagError("JOB_CANCELLED", "The image batch was cancelled.");
    parent.scheduler = { lease_token_hash: sha256(lease.token), acquired_at: lease.owner.acquired_at, waited_ms: lease.waited_ms, queue_kind: "filesystem-fifo", owner_kind: "agent-batch" };
    parent.progress = { ...(parent.progress || {}), workflow_started_at: new Date().toISOString() };
    store.transitionAndWrite(root, parent, "RUNNING", "Acquired one shared XPU lease for sequential batch children");

    for (const childId of parent.child_jobs) {
      if (cancellationRequested(root, parent.job_id)) break;
      let child = store.read(root, childId);
      if (child.status === "COMPLETED" && child.artifacts?.length === 1) continue;
      if (child.status !== "QUEUED") throw new AagError("ILLEGAL_STATE_TRANSITION", "A resumable batch child is not queued.");
      child.scheduler = { parent_lease_token_hash: sha256(lease.token), waited_ms: lease.waited_ms, execution_order: child.child_index };
      const attempt = { number: (child.attempts || []).length + 1, started_at: new Date().toISOString(), status: "RUNNING" };
      child.attempts = [...(child.attempts || []), attempt];
      store.transitionAndWrite(root, child, "RUNNING", "Running sequentially under the batch parent XPU lease");
      const task = taskFor(parent, child);
      try {
        const baseAdapterDeps = deps.adapters || deps;
        const filenames = await adapters.execute(task, null, { ...runtime, AAG_INVOCATION_ATTACHMENTS: [] }, context, lease.token, metadataDeps(root, child, baseAdapterDeps));
        if (!Array.isArray(filenames) || filenames.length !== 1) throw new AagError("OUTPUT_INVALID", "A batch child must produce exactly one final artifact.");
        child.progress = { ...(child.progress || {}), processing_started_at: new Date().toISOString() };
        store.write(root, child);
        const baseArtifact = await adapters.verifyArtifact(filenames[0], parent.job_id, child.job_id, null, "generate", undefined, deps.adapters || deps);
        let artifact = baseArtifact;
        if ((parent.final_output_quality || "standard") === "enhanced_2x") {
          child.intermediate_artifacts = [baseArtifact];
          child.progress = { ...(child.progress || {}), postprocess_started_at: new Date().toISOString() };
          store.write(root, child);
          const enhancedFilename = await (
            baseAdapterDeps.finalOutputPostprocess || adapters.finalOutputPostprocess
          )(
            baseArtifact.filename,
            parent.final_output_quality,
            lease.token,
            metadataDeps(root, child, baseAdapterDeps)
          );
          artifact = await adapters.verifyArtifact(
            enhancedFilename,
            parent.job_id,
            child.job_id,
            { width: baseArtifact.width, height: baseArtifact.height },
            "upscale",
            2,
            deps.adapters || deps
          );
          artifact.derived_from_artifact_id = baseArtifact.artifact_id;
          artifact.final_output_quality = parent.final_output_quality;
        }
        const siblings = countsFor(root, parent).children.filter((candidate) => candidate.job_id !== child.job_id);
        if (siblings.some((candidate) => candidate.artifacts?.some((existing) => existing.filename === artifact.filename || existing.sha256 === artifact.sha256))) {
          throw new AagError("OUTPUT_COLLISION", "Two intended batch children produced the same declared artifact.");
        }
        child.artifacts = [artifact];
        child.progress = { ...(child.progress || {}), artifact_verified_at: artifact.verified_at };
        attempt.status = "COMPLETED";
        attempt.finished_at = new Date().toISOString();
        store.transitionAndWrite(root, child, "COMPLETED", "One intended artifact decoded and verified");
      } catch (error) {
        const mapped = classifyError(error);
        attempt.status = mapped.code === "ENGINE_TIMEOUT" ? "TIMED_OUT" : "FAILED";
        attempt.finished_at = new Date().toISOString();
        attempt.error_code = mapped.code;
        child.error = { code: mapped.code, message: mapped.message, retryable: mapped.retryable };
        store.transitionAndWrite(root, child, attempt.status, "Batch child failed without invalidating verified siblings");
      }
      parent = store.read(root, parent.job_id);
      const snapshot = countsFor(root, parent);
      parent.artifacts = orderedArtifacts(snapshot.children);
      store.write(root, parent);
    }

    parent = store.read(root, parent.job_id);
    const snapshot = countsFor(root, parent);
    parent.artifacts = orderedArtifacts(snapshot.children);
    if (cancellationRequested(root, parent.job_id) && snapshot.completed < snapshot.requested) {
      parent.error = { code: "JOB_CANCELLED", message: "The batch was cancelled; verified images were preserved.", retryable: false };
      store.transitionAndWrite(root, parent, "CANCELLED", "Cancellation stopped remaining batch children");
      markUnfinishedCancelled(root, parent, "Parent cancellation stopped remaining work");
    } else if (snapshot.completed === snapshot.requested && parent.artifacts.length === snapshot.requested && snapshot.children.every((child) => child.status === "COMPLETED" && child.artifacts?.length === 1)) {
      store.transitionAndWrite(root, parent, "COMPLETED", "Exactly every intended ordered child has one verified artifact");
    } else if (snapshot.completed > 0) {
      parent.error = { code: "BATCH_PARTIAL", message: "The batch is incomplete; verified images were preserved and failed children may be resumed in a new turn.", retryable: false };
      store.transitionAndWrite(root, parent, "PARTIAL", "One or more children failed; verified siblings remain durable");
    } else {
      parent.error = { code: "BATCH_FAILED", message: "No intended batch child produced a verified artifact.", retryable: false };
      store.transitionAndWrite(root, parent, "FAILED", "No batch child completed successfully");
    }
    return store.read(root, parent.job_id);
  } catch (error) {
    const mapped = classifyError(error);
    parent = store.read(root, parent.job_id);
    const snapshot = countsFor(root, parent);
    parent.artifacts = orderedArtifacts(snapshot.children);
    if (!store.TERMINAL.has(parent.status)) {
      if (mapped.code === "JOB_CANCELLED") {
        parent.error = { code: "JOB_CANCELLED", message: "The image batch was cancelled; verified images were preserved.", retryable: false };
        store.transitionAndWrite(root, parent, "CANCELLED", "Batch cancellation observed before the next child");
      } else if (snapshot.completed > 0) {
        parent.error = { code: mapped.code, message: mapped.message, retryable: false };
        store.transitionAndWrite(root, parent, "PARTIAL", "Batch orchestration stopped; verified children remain resumable");
      } else {
        parent.error = { code: mapped.code, message: mapped.message, retryable: false };
        store.transitionAndWrite(root, parent, mapped.code === "ENGINE_TIMEOUT" ? "TIMED_OUT" : "FAILED", "Batch orchestration failed safely");
      }
    } else store.write(root, parent);
    markUnfinishedCancelled(root, parent, "Parent stopped before child execution");
    return store.read(root, parent.job_id);
  } finally {
    try { lease?.release(); } catch (error) { context.logger?.(`[AAG-IMAGE] batch lease release failed: ${redact(error?.message)}`); }
  }
}

async function createBatch(args, runtime = {}, context = {}) {
  let batch;
  try { batch = normalizeBatch(args, runtime); }
  catch (error) { return { error: classifyError(error) }; }
  const root = stateRoot(runtime);
  ensureDirectory(path.join(root, "jobs"));
  const key = batchIdempotencyKey(batch);
  try {
    const prior = store.getIdempotent(root, key, batch.owner);
    if (prior) return { job: prior, idempotent: true };
  } catch (error) { return { error: classifyError(error) }; }

  const publicPlan = batch.items.map((item) => ({
    logical_child_id: item.logical_child_id,
    index: item.index,
    prompt: item.prompt,
    aspect_ratio: item.aspect_ratio,
    ...(item.width !== undefined ? { width: item.width } : {}),
    ...(item.height !== undefined ? { height: item.height } : {}),
    ...(item.seed !== null ? { seed: item.seed } : {}),
    prompt_contract: item.prompt_contract,
  }));
  const planSha256 = sha256(JSON.stringify(publicPlan));
  const atlasSummary = {
    schema: "aag.selective-knowledge.batch-summary.v1",
    module: "visual-atlas",
    used: batch.items.some((item) => item.atlas?.used),
    mode: batch.items.find((item) => item.atlas?.used)?.atlas?.mode || "auto",
    reason: "batch_item_plans",
    visual_reference_used: false,
    item_count: batch.items.length,
    used_item_count: batch.items.filter((item) => item.atlas?.used).length,
    context_chars: batch.items.reduce((total, item) => total + Number(item.atlas?.context_chars || 0), 0),
    estimated_context_tokens: batch.items.reduce((total, item) => total + Number(item.atlas?.estimated_context_tokens || 0), 0),
  };
  const parent = store.createRecord(root, {
    schema_version: 3,
    release: VERSION,
    owner: batch.owner,
    operation: batch.operation,
    workflow_id: "generation.batch.sequential.v1",
    count: batch.count,
    requested_count: batch.count,
    collection: { request: batch.request, brief: batch.collection_brief, quality: batch.quality, final_output_quality: batch.final_output_quality },
    plan: publicPlan,
    plan_sha256: planSha256,
    public_arguments: { operation: batch.operation, collection_brief: batch.collection_brief, count: batch.count, quality: batch.quality, final_output_quality: batch.final_output_quality },
    atlas: atlasSummary,
    final_output_quality: batch.final_output_quality,
  });
  const claim = store.claimIdempotency(root, key, parent.job_id);
  if (!claim.claimed) {
    store.transitionAndWrite(root, parent, "CANCELLED", "Suppressed duplicate batch provider invocation");
    return { job: store.read(root, claim.job_id), idempotent: true };
  }
  try {
    for (const item of publicPlan) {
      const child = store.createRecord(root, {
        schema_version: 3,
        release: VERSION,
        parent_job_id: parent.job_id,
        child_index: item.index,
        logical_child_id: item.logical_child_id,
        owner: batch.owner,
        operation: "generate",
        workflow_id: childWorkflow(batch.quality),
        seed: plannedSeed(item),
        public_arguments: item,
        atlas: batch.items[item.index - 1].atlas,
        attempts: [],
        final_output_quality: batch.final_output_quality,
      });
      store.transition(child, "VALIDATED", "Stable planned child validated without backend creative enrichment");
      store.transitionAndWrite(root, child, "QUEUED", "Waiting behind the batch parent XPU lease");
      parent.child_jobs.push(child.job_id);
    }
    store.transition(parent, "VALIDATED", "Exact ordered workspace-LLM plan and every child validated");
    store.transitionAndWrite(root, parent, "QUEUED", "Waiting for one shared XPU lease");
    return { job: await executeParent(root, parent, runtime, context), idempotent: false };
  } catch (error) {
    const mapped = classifyError(error);
    const current = store.read(root, parent.job_id);
    if (!store.TERMINAL.has(current.status)) {
      current.error = { code: mapped.code, message: mapped.message, retryable: false };
      store.transitionAndWrite(root, current, "FAILED", "Failed while constructing the governed batch");
    }
    return { job: store.read(root, parent.job_id) };
  }
}

async function resumeBatch(jobId, runtime = {}, context = {}) {
  const root = stateRoot(runtime);
  const owner = scope(runtime);
  let parent = store.read(root, jobId);
  if (!sameOwner(parent.owner, owner)) throw new AagError("JOB_NOT_AUTHORIZED", "Image job is outside this conversation scope.");
  if (parent.parent_job_id || parent.operation !== "multi_generate" || Number(parent.schema_version) < 3) throw new AagError("RESUME_NOT_SUPPORTED", "Only a governed multi-image parent can be resumed.");
  if (parent.status === "COMPLETED") throw new AagError("JOB_ALREADY_TERMINAL", "The image batch is already complete.");
  if (!store.TERMINAL.has(parent.status)) return parent;
  clearCancellation(root, parent.job_id);
  for (const childId of parent.child_jobs || []) {
    const child = store.read(root, childId);
    if (child.status === "COMPLETED" && child.artifacts?.length === 1) continue;
    if (!store.TERMINAL.has(child.status)) throw new AagError("RESUME_STATE_INVALID", "An incomplete batch child has an unsafe resume state.");
    store.reopenAndWrite(root, child, "Explicit new-turn resume retained this stable child identity");
  }
  parent = store.read(root, parent.job_id);
  store.reopenAndWrite(root, parent, "Explicit new-turn resume will run only unverified children");
  return executeParent(root, store.read(root, parent.job_id), runtime, context);
}

module.exports = {
  VERSION,
  MIN_BATCH_COUNT,
  MAX_BATCH_COUNT,
  normalizeBatch,
  batchIdempotencyKey,
  countsFor,
  orderedArtifacts,
  resultEnvelope,
  createBatch,
  resumeBatch,
  cancellationRequested,
  cancelMarker,
};
