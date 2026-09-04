"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError, classifyError, redact } = require("./errors");
const { text, one, integer, scope, sameOwner, ownerKey, stateRoot, ensureDirectory, atomicJson, readJson, sha256 } = require("./util");
const images = require("./image");
const store = require("./store");
const scheduler = require("./scheduler");
const adapters = require("./adapters");
const identityRouting = require("./identity-routing");
const promptQuality = require("./prompt-quality");
const selectiveKnowledge = require("./selective-knowledge");

const VERSION = "0.9.0-preview.13";
const PROVIDER_POLICY = "OPEN_BY_CAPABILITY";
const TASK_KEYS = new Set(["operation", "request", "prompt", "source_policy", "source_index", "preservation", "quality", "final_output_quality", "aspect_ratio", "width", "height", "scale", "count", "seed"]);

function normalizeTask(args = {}, runtime = {}) {
  if (!args || typeof args !== "object" || Array.isArray(args)) throw new AagError("INVALID_ARGUMENT", "The image task arguments are invalid.");
  const unexpected = Object.keys(args).filter(key => !TASK_KEYS.has(key));
  if (unexpected.length) throw new AagError("INVALID_ARGUMENT", "The image task contains an unsupported argument.", false, unexpected.join(","));
  const owner = scope(runtime);
  const attachmentCount = images.imageAttachments(runtime).length;
  const operation = args.operation === undefined || args.operation === null || args.operation === ""
    ? (attachmentCount === 0 ? "generate" : one(args.operation, ["generate", "transform", "upscale"]))
    : one(args.operation, ["generate", "transform", "upscale"]);
  // The server-injected current invocation is authoritative. Provider-written
  // request/prompt fields are proposals and may improve visual language, but
  // cannot replace the user's semantic request.
  const authoritativeRequest = text(runtime.AAG_INVOCATION_PROMPT, 4000);
  const upstreamRequest = text(args.request, 4000);
  const request = authoritativeRequest || upstreamRequest;
  if (!request) throw new AagError("INVALID_ARGUMENT", "A request is required.");
  const task = {
    operation,
    request,
    prompt: text(args.prompt, 8000),
    source_policy: one(args.source_policy, ["auto", "current_attachment", "previous_artifact"], "auto"),
    preservation: one(args.preservation, ["auto", "identity", "subject", "none"], "auto"),
    quality: one(args.quality, ["auto", "fast", "balanced", "quality"], "auto"),
    final_output_quality: one(args.final_output_quality, ["standard", "enhanced_2x"], "standard"),
    aspect_ratio: one(args.aspect_ratio, ["auto", "1:1", "16:9", "9:16", "4:3", "3:2", "landscape", "portrait"], "auto"),
    count: integer(args.count, 1, 1, 2),
    seed: args.seed === undefined || args.seed === null || args.seed === "" ? null : integer(args.seed, 0, 0, 2_147_483_647),
    owner,
    _aag_authoritative_request: authoritativeRequest || upstreamRequest,
    _aag_upstream_request: upstreamRequest,
  };
  if (args.source_index !== undefined) task.source_index = integer(args.source_index, 1, 1, 8);
  if (task.source_index !== undefined && task.source_policy !== "current_attachment") {
    throw new AagError("INVALID_ARGUMENT", "source_index is valid only with current_attachment.");
  }
  if (args.width !== undefined) task.width = integer(args.width, 512, 256, 2048);
  if (args.height !== undefined) task.height = integer(args.height, 512, 256, 2048);
  if (operation === "generate") {
    if (task.source_policy !== "auto" || ["identity", "subject"].includes(task.preservation)) throw new AagError("INVALID_ARGUMENT", "Generate cannot select or preserve a source image.");
    if (args.scale !== undefined || args.source_index !== undefined) throw new AagError("INVALID_ARGUMENT", "Generate contains an argument that belongs to a source-image operation.");
    task.preservation = "none";
  } else if (operation === "upscale") {
    if (task.final_output_quality !== "standard") throw new AagError("INVALID_ARGUMENT", "Upscale uses its dedicated scale and cannot add a second final enhancement.");
    if (!["auto", "none"].includes(task.preservation)) throw new AagError("INVALID_ARGUMENT", "Upscale preserves content and cannot request generative preservation.");
    task.preservation = "none";
    task.scale = integer(args.scale, 4, 2, 4);
    if (![2, 3, 4].includes(task.scale)) throw new AagError("INVALID_ARGUMENT", "Upscale scale must be 2, 3, or 4.");
    if (task.count !== 1) throw new AagError("INVALID_ARGUMENT", "Upscale accepts one output per task.");
  } else {
    if (args.scale !== undefined) throw new AagError("INVALID_ARGUMENT", "Transform cannot specify an upscale factor.");
    if (task.preservation === "none") throw new AagError("INVALID_ARGUMENT", "Transform requires identity or subject preservation.");
  }
  identityRouting.canonicalizeIdentity(task);
  capabilityCheck(task);
  if (owner.workspace_id === "unknown" || owner.thread_id === "unknown") throw new AagError("OWNER_SCOPE_REQUIRED", "A trusted workspace and conversation scope is required.");
  if (owner.invocation_id === "unknown" || owner.turn_id === "unknown") throw new AagError("TURN_SCOPE_REQUIRED", "A trusted invocation and user-turn scope is required.");
  return task;
}

function workflow(task) {
  if (task.operation === "generate") return task.quality === "quality" ? "generation.text.quality.v1" : "generation.text.fast.v1";
  if (task.operation === "upscale") return "upscale.preserve.auto.v1";
  if (task.preservation === "identity") return task._aag_identity_contract === identityRouting.SCENE ? "transform.human.identity.scene.v1" : "transform.human.identity.portrait.v1";
  if (task.preservation === "auto") throw new AagError("SOURCE_AMBIGUOUS", "Choose identity or subject preservation for a transform.");
  return task.quality === "quality" ? "transform.general.quality.v1" : "transform.general.fast.v1";
}

function capabilityCheck(task) {
  if (task.operation === "transform" && task.preservation === "identity") {
    if (![identityRouting.PORTRAIT, identityRouting.SCENE].includes(task._aag_identity_contract)) throw new AagError("IDENTITY_ROUTE_UNRESOLVED", "The trusted identity contract could not be selected.");
    if (task.width !== undefined || task.height !== undefined || task.aspect_ratio !== "auto") throw new AagError("IDENTITY_FRAMING_NORMALIZATION_FAILED", "The trusted identity framing hints were not canonicalized.");
    if (task.source_policy !== "current_attachment") throw new AagError("IDENTITY_SOURCE_POLICY_UNSUPPORTED", "Human Identity requires the trusted current attachment.");
  }
}

function contextFile(root, owner) { return path.join(root, "contexts", `${ownerKey(owner)}.json`); }

async function fetchPrevious(task, runtime, root, deps = {}) {
  const file = contextFile(root, task.owner);
  let stat;
  try { stat = fs.lstatSync(file); } catch (error) { if (error?.code === "ENOENT") throw new AagError("SOURCE_REQUIRED", "No previous AAG artifact exists in this conversation."); throw error; }
  if (!stat.isFile() || stat.isSymbolicLink()) throw new AagError("SOURCE_UNAUTHORIZED", "The previous-artifact record is unsafe.");
  const context = readJson(file);
  if (!sameOwner(context.owner, task.owner)) throw new AagError("SOURCE_UNAUTHORIZED", "The previous artifact is outside this conversation scope.");
  const filename = context.artifact?.filename;
  adapters.publicArtifactUrl(filename);
  const fetchImpl = deps.fetch || fetch;
  const response = await fetchImpl(`${adapters.HUB_INTERNAL}/files/${encodeURIComponent(filename)}`, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new AagError("SOURCE_REQUIRED", "The previous artifact is no longer publisher-readable.");
  const bytes = Buffer.from(await response.arrayBuffer());
  const mime = String(response.headers?.get?.("content-type") || context.artifact.mime || "image/png").split(";", 1)[0];
  const selected = await images.currentAttachment({ ...task, source_index: 1 }, { ...runtime, AAG_INVOCATION_ATTACHMENTS: [{ name: filename, mime, contentString: `data:${mime};base64,${bytes.toString("base64")}` }] }, deps);
  selected.source.kind = "previous_artifact";
  selected.source.artifact_id = context.artifact.artifact_id;
  selected.source.artifact_sha256 = context.artifact.sha256;
  return selected;
}

async function resolveSource(task, runtime, root, deps = {}) {
  if (task.operation === "generate") return { source: null, normalized: null, runtime: { ...runtime, AAG_INVOCATION_ATTACHMENTS: [] } };
  const current = images.imageAttachments(runtime);
  if (task.source_policy === "current_attachment" || (task.source_policy === "auto" && current.length)) return images.currentAttachment(task, runtime, deps);
  if (task.source_policy === "current_attachment") throw new AagError("SOURCE_REQUIRED", "A current image attachment is required.");
  if (["auto", "previous_artifact"].includes(task.source_policy)) return fetchPrevious(task, runtime, root, deps);
  throw new AagError("SOURCE_REQUIRED", "An approved image source is required.");
}

function idempotencyKey(task) {
  const material = [
    task.owner.workspace_id,
    task.owner.thread_id,
    task.owner.user_id,
    task.owner.invocation_id,
    task.owner.turn_id,
    task.operation,
  ];
  return sha256(JSON.stringify(material));
}

function seedFor(task, index) { return task.seed === null ? crypto.randomInt(0, 2_147_483_648) : (task.seed + index) % 2_147_483_648; }

function artifactLines(artifacts) {
  const lines = [`artifact_count=${artifacts.length}`];
  artifacts.forEach((artifact, index) => {
    const n = index + 1;
    lines.push(`artifact_${n}_id=${artifact.artifact_id}`);
    lines.push(`artifact_${n}_url=${artifact.url}`);
    lines.push(`artifact_${n}_sha256=${artifact.sha256}`);
    lines.push(`artifact_${n}_dimensions=${artifact.width}x${artifact.height}`);
  });
  return lines;
}

function resultEnvelope(job) {
  const lines = ["AAG_IMAGE_RESULT", `status=${String(job.status || "FAILED").toLowerCase()}`, `job_id=${job.job_id || ""}`, `operation=${job.operation || ""}`, `workflow=${job.workflow_id || ""}`, `release=${job.release || VERSION}`, ...artifactLines(job.artifacts || [])];
  if (job.capability?.status) {
    lines.push(`capability_status=${job.capability.status}`);
    lines.push(`warning_code=${job.capability.warning_code}`);
    lines.push(`warning=${job.capability.message}`);
  }
  if (job.error) {
    lines.push(`error_code=${job.error.code}`);
    lines.push(`message=${job.error.message}`);
    lines.push(`retryable=${Boolean(job.error.retryable)}`);
    lines.push("same_turn_retry=forbidden");
    lines.push(`partial=${Boolean((job.artifacts || []).length)}`);
  }
  return lines.join("\n");
}

function failureEnvelope(error, jobId = "") {
  const mapped = classifyError(error);
  const target = process.env.AAG_IMAGE_PROVIDER_FAILURE_EVIDENCE;
  if (target) {
    const evidence = {
      schema_version: "aag.image-provider.failure-evidence.v1",
      at: new Date().toISOString(),
      exception_type: String(error?.name || error?.constructor?.name || "Error").slice(0, 120),
      message: redact(error?.message || error),
      detail: redact(error?.detail || mapped.detail || ""),
      stack: redact(error?.stack || ""),
      classification: mapped.code,
      retryable: Boolean(mapped.retryable),
      job_id: String(jobId || "").slice(0, 80),
    };
    try { fs.writeFileSync(target, JSON.stringify(evidence, null, 2) + "\n", { mode: 0o600, flag: "wx" }); } catch {}
  }
  const lines = ["AAG_IMAGE_RESULT", "status=failed", `job_id=${jobId}`, `error_code=${mapped.code}`, `message=${mapped.message}`, `retryable=${Boolean(mapped.retryable)}`, "same_turn_retry=forbidden", "artifact_count=0"];
  if (["PROMPT_UNDER_SPECIFIED", "PROMPT_SEMANTIC_DRIFT"].includes(mapped.code)) {
    try {
      const detail = JSON.parse(mapped.detail || "{}");
      if (Array.isArray(detail.missing_dimensions)) lines.push(`missing_dimensions=${JSON.stringify(detail.missing_dimensions.slice(0, 12))}`);
      if (Array.isArray(detail.advisory_missing_dimensions)) lines.push(`advisory_missing_dimensions=${JSON.stringify(detail.advisory_missing_dimensions.slice(0, 12))}`);
    } catch {}
  }
  return lines.join("\n");
}

function rememberArtifact(root, owner, artifact) {
  ensureDirectory(path.join(root, "contexts"));
  atomicJson(contextFile(root, owner), { schema_version: 2, owner, artifact, updated_at: new Date().toISOString() });
}

function childFailureStatus(mapped) {
  if (["ENGINE_TIMEOUT", "QUEUE_WAIT_TIMEOUT"].includes(mapped.code)) return "TIMED_OUT";
  if (mapped.code === "JOB_CANCELLED") return "CANCELLED";
  return "FAILED";
}

async function createTask(args, runtime = {}, context = {}) {
  let task;
  try {
    task = normalizeTask(args, runtime);
    capabilityCheck(task, runtime);
    promptQuality.applyToTask(task);
    selectiveKnowledge.applyToTask(task, context);
  } catch (error) { return failureEnvelope(error); }
  const deps = context.deps || {};
  const root = stateRoot(runtime);
  ensureDirectory(path.join(root, "jobs"));
  let resolved;
  try { resolved = await resolveSource(task, runtime, root, deps); } catch (error) { return failureEnvelope(error); }
  if (task.preservation === "identity") {
    try { adapters.humanIdentity.classifySource({ _aag_source: resolved.source }); }
    catch (error) { return failureEnvelope(error); }
  }
  let selected;
  try { selected = workflow(task); } catch (error) { return failureEnvelope(error); }
  const key = idempotencyKey(task);
  try { const prior = store.getIdempotent(root, key, task.owner); if (prior) return resultEnvelope(prior); } catch (error) { return failureEnvelope(error); }

  const capability = task.preservation === "identity" ? {
    status: "ACTIVE_VALIDATED_SCOPE",
    capability: selected,
    release: VERSION,
    contract_id: task._aag_identity_contract === identityRouting.SCENE ? "structured-scene-c" : "structured-close-b",
    profile: task._aag_identity_profile,
    requested_framing: task._aag_requested_framing,
    normalized_framing: { orientation: task._aag_normalized_orientation, width: task._aag_internal_width, height: task._aag_internal_height },
    validated_scope: "historical-fixtures-and-trusted-current-attachment-runtime-references",
  } : null;
  const parent = store.createRecord(root, { release: VERSION, owner: task.owner, operation: task.operation, workflow_id: selected, source: resolved.source, count: task.count, capability, atlas: task.atlas, final_output_quality: task.final_output_quality });
  const claim = store.claimIdempotency(root, key, parent.job_id);
  if (!claim.claimed) {
    store.transitionAndWrite(root, parent, "CANCELLED", "Suppressed duplicate provider invocation");
    try { return resultEnvelope(store.read(root, claim.job_id)); } catch (error) { return failureEnvelope(error); }
  }
  const children = [];
  try {
    for (let index = 0; index < task.count; index++) {
      const child = store.createRecord(root, { release: VERSION, parent_job_id: parent.job_id, child_index: index + 1, owner: task.owner, operation: task.operation, workflow_id: selected, source: resolved.source, seed: seedFor(task, index), capability, atlas: task.atlas, final_output_quality: task.final_output_quality });
      store.transition(child, "VALIDATED", "Child arguments and source validated");
      store.transitionAndWrite(root, child, "QUEUED", "Waiting behind parent XPU lease");
      children.push(child);
      parent.child_jobs.push(child.job_id);
    }
    store.transition(parent, "VALIDATED", "Arguments, source, capability, and workflow validated");
    store.transitionAndWrite(root, parent, "QUEUED", "Waiting for shared XPU lease");
  } catch (error) {
    const mapped = classifyError(error);
    parent.error = { code: mapped.code, message: mapped.message, retryable: mapped.retryable };
    if (!store.TERMINAL.has(parent.status)) store.transitionAndWrite(root, parent, "FAILED", "Failed while constructing child records");
    return resultEnvelope(parent);
  }

  let lease;
  try {
    lease = await scheduler.acquire(root, parent, {
      waitMs: integer(runtime.AAG_IMAGE_QUEUE_TIMEOUT_MS, 30 * 60 * 1000, 1_000, 60 * 60 * 1000),
      staleMs: integer(runtime.AAG_IMAGE_LEASE_STALE_MS, 2 * 60 * 1000, 5_000, 15 * 60 * 1000),
      pollMs: integer(runtime.AAG_IMAGE_QUEUE_POLL_MS, 250, 25, 5_000),
      maxQueue: integer(runtime.AAG_IMAGE_QUEUE_MAX, 8, 1, 64),
      deps: deps.scheduler || {},
      isCancelled: () => { try { return store.read(root, parent.job_id).status === "CANCELLED"; } catch { return false; } },
    });
    const current = store.read(root, parent.job_id);
    if (current.status === "CANCELLED") throw new AagError("JOB_CANCELLED", "The queued image job was cancelled.");
    const recovered = store.recoverStale(root, integer(runtime.AAG_IMAGE_JOB_STALE_MS, 2 * 60 * 60 * 1000, 60 * 60 * 1000, 24 * 60 * 60 * 1000));
    if (recovered.length) context.logger?.(`[AAG-IMAGE] recovered ${recovered.length} stale non-terminal job record(s) after the XPU lane became idle`);
    parent.scheduler = { lease_token_hash: sha256(lease.token), acquired_at: lease.owner.acquired_at, waited_ms: lease.waited_ms, queue_kind: "filesystem-fifo", owner_kind: "agent" };
    parent.progress = { ...(parent.progress || {}), workflow_started_at: new Date().toISOString() };
    store.transitionAndWrite(root, parent, "RUNNING", "Acquired shared XPU lease");

    for (let index = 0; index < children.length; index++) {
      const child = children[index];
      child.scheduler = { parent_lease_token_hash: sha256(lease.token), waited_ms: lease.waited_ms };
      store.transitionAndWrite(root, child, "RUNNING", "Running sequentially under parent XPU lease");
      const childTask = {
        ...task,
        seed: child.seed,
        _aag_source: resolved.source,
        _aag_parent_job_id: parent.job_id,
        _aag_child_job_id: child.job_id,
      };
      try {
        const baseAdapterDeps = deps.adapters || deps;
        const upstreamMetadata = baseAdapterDeps.onEngineMetadata;
        const upstreamProgress = baseAdapterDeps.onEngineProgress;
        const adapterDeps = {
          ...baseAdapterDeps,
          onEngineMetadata(metadata = {}) {
            const allowed = {};
            for (const key of ["adapter", "prompt_id", "submitted_at", "completed_at", "elapsed_seconds", "model", "identity_cosine", "negative_margin", "composition_result", "blur_result", "network_result", "cleanup_result", "contract_id", "contract_sha256", "scene_profile", "prompt_sha256", "recipe_id", "model_file", "model_sha256", "text_encoder_file", "text_encoder_sha256", "vae_file", "vae_sha256", "workflow_sha256", "sampler", "scheduler", "steps", "cfg", "dimensions", "aspect", "aspect_source", "prompt_policy", "negative_prompt_policy", "offline_policy", "prompt_contract", "prompt_author", "prompt_quality_status", "prompt_fidelity_status", "prompt_structure_status", "final_prompt_sha256"]) {
              if (["elapsed_seconds", "identity_cosine", "negative_margin", "steps", "cfg"].includes(key) && Number.isFinite(Number(metadata[key]))) allowed[key] = Number(metadata[key]);
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
            const strings = [
              "engine_progress_source", "last_engine_progress_at",
              "last_engine_progress_event", "current_engine_node",
              "current_engine_node_class", "stall_detected_at",
              "recovery_action", "recovery_started_at",
              "recovery_completed_at", "recovery_outcome",
            ];
            for (const key of strings) {
              if (progress[key] !== undefined) allowed[key] = String(progress[key]).slice(0, 200);
            }
            for (const key of ["current_engine_step", "current_engine_step_max", "sequence"]) {
              if (Number.isInteger(Number(progress[key])) && Number(progress[key]) >= 0) {
                allowed[key] = Number(progress[key]);
              }
            }
            child.progress = { ...(child.progress || {}), ...allowed };
            store.write(root, child);
            upstreamProgress?.(allowed);
          },
        };
        const filenames = await adapters.execute(childTask, resolved.normalized, resolved.runtime, context, lease.token, adapterDeps);
        if (!Array.isArray(filenames) || filenames.length !== 1) throw new AagError("OUTPUT_INVALID", "A child image job must produce exactly one final artifact.");
        child.progress = { ...(child.progress || {}), processing_started_at: new Date().toISOString() };
        store.write(root, child);
        const baseArtifact = await adapters.verifyArtifact(filenames[0], parent.job_id, child.job_id, resolved.source, task.operation, task.scale, deps.adapters || deps);
        let artifact = baseArtifact;
        if (task.final_output_quality === "enhanced_2x") {
          child.intermediate_artifacts = [baseArtifact];
          child.progress = { ...(child.progress || {}), postprocess_started_at: new Date().toISOString() };
          store.write(root, child);
          const enhancedFilename = await (
            baseAdapterDeps.finalOutputPostprocess || adapters.finalOutputPostprocess
          )(
            baseArtifact.filename,
            task.final_output_quality,
            lease.token,
            adapterDeps
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
          artifact.final_output_quality = task.final_output_quality;
          parent.intermediate_artifacts.push(baseArtifact);
        }
        if (parent.artifacts.some(existing => existing.filename === artifact.filename || existing.sha256 === artifact.sha256)) throw new AagError("OUTPUT_COLLISION", "Two declared image outputs are not unique.");
        child.artifacts.push(artifact);
        parent.artifacts.push(artifact);
        child.progress = { ...(child.progress || {}), artifact_verified_at: artifact.verified_at };
        store.transitionAndWrite(root, child, "COMPLETED", "Final artifact decoded and verified");
        store.write(root, parent);
      } catch (error) {
        const mapped = classifyError(error);
        child.error = { code: mapped.code, message: mapped.message, retryable: mapped.retryable };
        store.transitionAndWrite(root, child, childFailureStatus(mapped), "Child execution or artifact verification failed");
        throw mapped;
      }
    }
    const completedChildren = children.map(child => store.read(root, child.job_id));
    if (parent.artifacts.length !== parent.count || completedChildren.some(child => child.status !== "COMPLETED" || child.artifacts.length !== 1)) throw new AagError("OUTPUT_MISSING", "Not every declared child has one verified final artifact.");
    store.transitionAndWrite(root, parent, "COMPLETED", "Every declared child artifact exists and passed validation");
    rememberArtifact(root, task.owner, parent.artifacts[parent.artifacts.length - 1]);
    return resultEnvelope(parent);
  } catch (error) {
    const mapped = classifyError(error);
    let latest;
    try { latest = store.read(root, parent.job_id); } catch { latest = parent; }
    if (latest.status === "CANCELLED" || mapped.code === "JOB_CANCELLED") {
      if (latest.status !== "CANCELLED" && !store.TERMINAL.has(latest.status)) store.transition(latest, "CANCELLED", "Cancelled while queued");
      latest.error = { code: "JOB_CANCELLED", message: "The queued image job was cancelled.", retryable: false };
    } else if (!store.TERMINAL.has(latest.status)) {
      latest.error = { code: mapped.code, message: mapped.message, retryable: mapped.retryable };
      store.transition(latest, childFailureStatus(mapped), "Parent stopped because a child or scheduler failed");
    }
    store.write(root, latest);
    for (const child of children) {
      let childCurrent;
      try { childCurrent = store.read(root, child.job_id); } catch { continue; }
      if (!store.TERMINAL.has(childCurrent.status)) {
        childCurrent.error = { code: "PARENT_TERMINATED", message: "The child did not run because its parent terminated.", retryable: false };
        store.transitionAndWrite(root, childCurrent, "CANCELLED", "Parent terminated before child execution");
      }
    }
    return resultEnvelope(latest);
  } finally {
    try { lease?.release(); } catch (error) { context.logger?.(`[AAG-IMAGE] lease release failed: ${redact(error?.message)}`); }
  }
}

function jobAction(args, runtime = {}, context = {}) {
  if (!args || typeof args !== "object" || Array.isArray(args) || Object.keys(args).some(key => !["action", "job_id"].includes(key))) throw new AagError("INVALID_ARGUMENT", "The job action arguments are invalid.");
  const action = one(args.action, ["status", "cancel", "resume"]);
  const id = text(args.job_id, 40, true);
  const owner = scope(runtime);
  if (owner.workspace_id === "unknown" || owner.thread_id === "unknown") throw new AagError("OWNER_SCOPE_REQUIRED", "A trusted workspace and conversation scope is required.");
  const root = stateRoot(runtime);
  const job = store.read(root, id);
  if (!sameOwner(job.owner, owner)) throw new AagError("JOB_NOT_AUTHORIZED", "Image job is outside this conversation scope.");
  const batch = job.operation === "multi_generate" && Number(job.schema_version) >= 3 ? require("./batch") : null;
  if (action === "status") return batch ? batch.resultEnvelope(root, job) : resultEnvelope(job);
  if (action === "resume") {
    if (!batch) throw new AagError("RESUME_NOT_SUPPORTED", "Only an incomplete governed multi-image parent can be resumed.");
    return batch.resumeBatch(job.job_id, runtime, context).then((resumed) => batch.resultEnvelope(root, resumed));
  }
  if (job.parent_job_id) throw new AagError("INVALID_ARGUMENT", "Cancel the parent image job, not an internal child job.");
  if (store.TERMINAL.has(job.status)) throw new AagError("JOB_ALREADY_TERMINAL", "Image job has already finished.");
  if (batch && job.status === "RUNNING") {
    ensureDirectory(path.join(root, "cancel"));
    const marker = batch.cancelMarker(root, job.job_id);
    try { fs.writeFileSync(marker, `${new Date().toISOString()}\n`, { mode: 0o600, flag: "wx" }); }
    catch (error) { if (error?.code !== "EEXIST") throw error; }
    return `${batch.resultEnvelope(root, job)}\ncancellation_requested=true`;
  }
  if (job.status === "RUNNING" && ["transform.human.identity.portrait.v1", "transform.human.identity.scene.v1"].includes(job.workflow_id)) {
    ensureDirectory(path.join(root, "cancel"));
    const marker = path.join(root, "cancel", job.job_id);
    try { fs.writeFileSync(marker, `${new Date().toISOString()}\n`, { mode: 0o600, flag: "wx" }); }
    catch (error) { if (error?.code !== "EEXIST") throw error; }
    return `${resultEnvelope(job)}\ncancellation_requested=true`;
  }
  if (job.status !== "QUEUED") throw new AagError("CANCEL_NOT_SUPPORTED", "Safe targeted cancellation is not supported after engine execution begins.");
  job.error = { code: "JOB_CANCELLED", message: "The queued image job was cancelled.", retryable: false };
  store.transitionAndWrite(root, job, "CANCELLED", "Cancelled by the owning conversation before engine start");
  for (const childId of job.child_jobs || []) {
    const child = store.read(root, childId);
    if (!store.TERMINAL.has(child.status)) {
      child.error = { code: "PARENT_CANCELLED", message: "The child was cancelled before execution.", retryable: false };
      store.transitionAndWrite(root, child, "CANCELLED", "Parent cancelled while queued");
    }
  }
  return batch ? batch.resultEnvelope(root, job) : resultEnvelope(job);
}

module.exports = {
  VERSION, PROVIDER_POLICY, AagError, normalizeTask, workflow, capabilityCheck, resolveSource, identityRouting, promptQuality, selectiveKnowledge,
  idempotencyKey, seedFor, resultEnvelope, failureEnvelope, rememberArtifact, createTask, jobAction,
  classifyError, transition: store.transition, acquire: scheduler.acquire,
  stateRoot, ownerKey, store, scheduler, adapters,
};
