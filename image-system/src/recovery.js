"use strict";

const fs = require("fs");
const path = require("path");
const { AagError } = require("./errors");
const { readJson, sha256 } = require("./util");
const store = require("./store");
const scheduler = require("./scheduler");
const adapters = require("./adapters");

const JOB_ID_RE = /^aag-[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const REQUEST_ID_RE = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;

function listJson(directory) {
  try {
    return fs.readdirSync(directory)
      .filter((name) => REQUEST_ID_RE.test(name.replace(/\.json$/, "")) && name.endsWith(".json"))
      .sort();
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

function existsRegular(file) {
  try {
    const stat = fs.lstatSync(file);
    return stat.isFile() && !stat.isSymbolicLink();
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

function recoveryEligible(job, createdBefore) {
  if (!createdBefore) return true;
  const created = Date.parse(String(job?.created_at || ""));
  const cutoff = Date.parse(String(createdBefore));
  return Number.isFinite(created) && Number.isFinite(cutoff) && created < cutoff;
}

function parentIdFromProcess(processFile, stateRoot) {
  const report = readJson(processFile);
  if (!Array.isArray(report.argv))
    throw new AagError("OUTPUT_POLICY_VIOLATION", "Identity recovery process evidence is invalid.");
  const index = report.argv.indexOf("--cancel-file");
  if (index < 0 || typeof report.argv[index + 1] !== "string")
    throw new AagError("OUTPUT_POLICY_VIOLATION", "Identity recovery process evidence has no governed job binding.");
  const cancelFile = path.resolve(report.argv[index + 1]);
  const parentId = path.basename(cancelFile);
  const canonicalSuffix = path.join("aag-image-agent-state", "cancel", parentId);
  if (
    !JOB_ID_RE.test(parentId) ||
    cancelFile.includes(`${path.sep}..${path.sep}`) ||
    !cancelFile.endsWith(canonicalSuffix)
  )
    throw new AagError("OUTPUT_POLICY_VIOLATION", "Identity recovery job binding escaped the governed state root.");
  return parentId;
}

function exactRunnableChild(root, parent) {
  if (
    parent.parent_job_id ||
    parent.status !== "RUNNING" ||
    Number(parent.count) !== 1 ||
    !Array.isArray(parent.child_jobs) ||
    parent.child_jobs.length !== 1
  )
    throw new AagError("RECOVERY_AMBIGUOUS", "Interrupted image job is not an unambiguous single-output parent.");
  const child = store.read(root, parent.child_jobs[0]);
  if (
    child.parent_job_id !== parent.job_id ||
    child.status !== "RUNNING" ||
    JSON.stringify(child.owner) !== JSON.stringify(parent.owner)
  )
    throw new AagError("RECOVERY_AMBIGUOUS", "Interrupted image child binding is inconsistent.");
  return child;
}

async function maybeEnhanceRecovered({ root, parent, child, artifact, leaseToken, deps }) {
  if ((parent.final_output_quality || "standard") !== "enhanced_2x") return artifact;
  if (!leaseToken)
    throw new AagError("RECOVERY_REQUIRES_LEASE", "Final enhancement recovery requires the governed XPU lease.", true);
  child.intermediate_artifacts = [artifact];
  parent.intermediate_artifacts = [artifact];
  child.progress = { ...(child.progress || {}), postprocess_started_at: new Date().toISOString() };
  store.write(root, child);
  store.write(root, parent);
  const filename = await adapters.finalOutputPostprocess(
    artifact.filename,
    "enhanced_2x",
    leaseToken,
    deps
  );
  const enhanced = await adapters.verifyArtifact(
    filename,
    parent.job_id,
    child.job_id,
    { width: artifact.width, height: artifact.height },
    "upscale",
    2,
    deps
  );
  enhanced.derived_from_artifact_id = artifact.artifact_id;
  enhanced.final_output_quality = "enhanced_2x";
  return enhanced;
}

function commitRecovered(root, parent, child, artifact, runtime) {
  if (parent.artifacts?.length || child.artifacts?.length)
    throw new AagError("RECOVERY_AMBIGUOUS", "Interrupted image job already contains partial final-artifact state.");
  child.artifacts = [artifact];
  child.progress = { ...(child.progress || {}), artifact_verified_at: artifact.verified_at };
  store.transitionAndWrite(root, child, "COMPLETED", "Recovered verified engine output after application restart");
  parent.artifacts = [artifact];
  parent.progress = { ...(parent.progress || {}), artifact_verified_at: artifact.verified_at };
  store.transitionAndWrite(root, parent, "COMPLETED", "Recovered the uniquely bound verified artifact after application restart");
  runtime.rememberArtifact(root, parent.owner, artifact);
  return store.read(root, parent.job_id);
}

async function recoverIdentityResponses(root, options = {}) {
  const runtime = options.runtime || require("./runtime");
  const deps = options.deps || {};
  const recovered = [];
  const bridges = options.identityBridges || [
    {
      workflow: "transform.human.identity.scene.v1",
      bridge: adapters.sceneIdentity,
      stateRoot: adapters.sceneIdentity.STATE_ROOT,
    },
    {
      workflow: "transform.human.identity.portrait.v1",
      bridge: adapters.humanIdentity,
      stateRoot: adapters.humanIdentity.STATE_ROOT,
    },
  ];
  for (const item of bridges) {
    const responseRoot = path.join(item.stateRoot, "responses");
    for (const name of listJson(responseRoot)) {
      const requestId = name.slice(0, -5);
      if (existsRegular(path.join(item.stateRoot, "acks", name))) continue;
      let parent;
      let child;
      let response;
      try {
        response = item.bridge.validateResponse(readJson(path.join(responseRoot, name)), requestId);
        if (response.status !== "PASS") continue;
        const parentId = parentIdFromProcess(
          path.join(item.stateRoot, "process", requestId, "identity_worker.json"),
          root
        );
        parent = store.read(root, parentId);
        if (!recoveryEligible(parent, options.createdBefore)) continue;
        if (parent.workflow_id !== item.workflow) continue;
        child = exactRunnableChild(root, parent);
        if (child.engine?.prompt_id && child.engine.prompt_id !== requestId)
          throw new AagError("RECOVERY_AMBIGUOUS", "Interrupted identity request ID differs from recorded engine state.");
        const base = await adapters.verifyArtifact(
          response.artifact_filename,
          parent.job_id,
          child.job_id,
          parent.source,
          parent.operation,
          undefined,
          deps
        );
        if (base.sha256 !== response.artifact_sha256)
          throw new AagError("OUTPUT_INVALID", "Recovered identity artifact hash differs from the committed worker response.");
        item.bridge.acknowledgeRecovered(
          requestId,
          response.artifact_filename,
          true,
          "verified committed response recovered after application restart"
        );
        child.engine = {
          ...(child.engine || {}),
          prompt_id: requestId,
          completed_at: response.completed_at,
          elapsed_seconds: Number(response.total_latency_seconds || 0),
          adapter: item.bridge.ADAPTER_ID || item.bridge.ROUTE,
        };
        child.progress = {
          ...(child.progress || {}),
          engine_started_at: child.started_at,
          engine_completed_at: response.completed_at,
          processing_started_at: response.completed_at,
        };
        store.write(root, child);
        let artifact = base;
        if ((parent.final_output_quality || "standard") === "enhanced_2x") {
          const lease = await scheduler.acquire(root, parent, { waitMs: 60_000 });
          try {
            artifact = await maybeEnhanceRecovered({ root, parent, child, artifact: base, leaseToken: lease.token, deps });
          } finally {
            lease.release();
          }
        }
        recovered.push(commitRecovered(root, parent, child, artifact, runtime));
      } catch (error) {
        options.logger?.(`[AAG-IMAGE] identity recovery skipped ${requestId}: ${String(error?.code || error?.message || error)}`);
      }
    }
  }
  return recovered;
}

async function recoverComfyJobs(root, options = {}) {
  const runtime = options.runtime || require("./runtime");
  const deps = options.deps || {};
  const recovered = [];
  for (const parent of store.listJobs(root).filter((job) =>
    !job.parent_job_id &&
    job.status === "RUNNING" &&
    recoveryEligible(job, options.createdBefore) &&
    job.operation !== "multi_generate" &&
    ["generation.text.fast.v1", "generation.text.quality.v1", "transform.general.fast.v1", "transform.general.quality.v1"].includes(job.workflow_id)
  )) {
    let lease;
    try {
      const child = exactRunnableChild(root, parent);
      const promptId = String(child.engine?.prompt_id || "");
      if (!/^[A-Za-z0-9-]{8,128}$/.test(promptId)) continue;
      lease = await scheduler.acquire(root, parent, { waitMs: 60_000 });
      const history = await adapters.comfy.fetchJson(
        `${adapters.comfy.COMFY}/history/${encodeURIComponent(promptId)}`,
        {},
        30_000,
        lease.token,
        deps
      );
      const entry = history?.[promptId];
      if (!entry || entry.status?.status_str === "error" || !entry.status?.completed) continue;
      const images = adapters.comfy.imagesFrom(entry);
      if (images.length !== 1)
        throw new AagError("OUTPUT_INVALID", "Recovered image-engine job does not have exactly one output.");
      const kind = parent.operation === "transform" ? "REF" : "GEN";
      const filename = await adapters.comfy.importImage(images[0], kind, lease.token, deps);
      child.progress = {
        ...(child.progress || {}),
        engine_completed_at: new Date().toISOString(),
        processing_started_at: new Date().toISOString(),
      };
      store.write(root, child);
      const base = await adapters.verifyArtifact(
        filename,
        parent.job_id,
        child.job_id,
        parent.source,
        parent.operation,
        undefined,
        deps
      );
      const artifact = await maybeEnhanceRecovered({ root, parent, child, artifact: base, leaseToken: lease.token, deps });
      recovered.push(commitRecovered(root, parent, child, artifact, runtime));
    } catch (error) {
      options.logger?.(`[AAG-IMAGE] Comfy recovery skipped ${parent.job_id}: ${String(error?.code || error?.message || error)}`);
    } finally {
      try { lease?.release(); } catch {}
    }
  }
  return recovered;
}

async function recoverCompletedJobs(root, options = {}) {
  const identity = await recoverIdentityResponses(root, options);
  const comfy = await recoverComfyJobs(root, options);
  return [...identity, ...comfy];
}

function progressSnapshot(job, children = []) {
  const terminal = store.TERMINAL.has(job.status);
  const failed = ["FAILED", "CANCELLED", "TIMED_OUT"].includes(job.status);
  const all = [job, ...children];
  const workflowStarted = (job.transitions || []).some(
    (transition) => transition.status === "RUNNING"
  );
  const engineStarted = all.some((item) => item.progress?.engine_started_at || item.engine?.submitted_at || item.engine?.prompt_id);
  const engineCompleted = all.some((item) => item.progress?.engine_completed_at || item.engine?.completed_at);
  const processingStarted = all.some((item) => item.progress?.processing_started_at);
  const artifactVerified = Array.isArray(job.artifacts) && job.artifacts.length > 0 && all.some((item) => item.progress?.artifact_verified_at || item.artifacts?.length);
  const recoveryItem = all.find((item) => item.progress?.stall_detected_at || item.progress?.recovery_action);
  const recoveryAction = String(recoveryItem?.progress?.recovery_action || "");
  const recoveryOutcome = String(recoveryItem?.progress?.recovery_outcome || "");
  const stallDetected = Boolean(recoveryItem?.progress?.stall_detected_at);
  const engineRecovered = recoveryAction === "INTERRUPT_SUCCEEDED" || recoveryOutcome === "XPU_LANE_RELEASED";
  const serviceRecoveryRequired = recoveryAction === "SERVICE_RECOVERY_REQUIRED" || recoveryOutcome === "INTERRUPT_DID_NOT_RELEASE_LANE";
  const recoveryInProgress = stallDetected && recoveryAction === "INTERRUPT_REQUESTED" && !engineRecovered && !serviceRecoveryRequired;
  return {
    jobId: job.job_id,
    status: job.status,
    failed,
    terminal,
    startedAt: job.started_at || job.created_at,
    updatedAt: job.updated_at,
    errorCode: failed ? String(job.error?.code || "IMAGE_REQUEST_FAILED") : null,
    lifecycle: {
      requestReceived: true,
      instructionsPrepared: true,
      workflowStarted,
      generatingStarted: engineStarted || workflowStarted,
      generatingCompleted: engineCompleted || processingStarted || artifactVerified,
      processingStarted: processingStarted || artifactVerified,
      processingCompleted: artifactVerified,
      stallDetected,
      recoveryInProgress,
      engineRecovered,
      serviceRecoveryRequired,
    },
    recovery: stallDetected ? {
      action: recoveryAction || null,
      outcome: recoveryOutcome || null,
      detectedAt: recoveryItem?.progress?.stall_detected_at || null,
      startedAt: recoveryItem?.progress?.recovery_started_at || null,
      completedAt: recoveryItem?.progress?.recovery_completed_at || null,
    } : null,
  };
}

module.exports = {
  listJson,
  existsRegular,
  parentIdFromProcess,
  recoveryEligible,
  exactRunnableChild,
  recoverIdentityResponses,
  recoverComfyJobs,
  recoverCompletedJobs,
  progressSnapshot,
};
