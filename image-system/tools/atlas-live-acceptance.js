"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const runtimeRoot = process.env.AAG_LIVE_RUNTIME_ROOT || path.resolve(__dirname, "../src");
const runtimeApi = require(path.join(runtimeRoot, "runtime"));
const atlas = require(path.join(runtimeRoot, "visual-atlas"));

const STATE_ROOT = process.env.AAG_IMAGE_AGENT_STATE_ROOT || (fs.existsSync("/app/server/storage")
  ? "/app/server/storage/aag-image-agent-state"
  : "/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-state");
const output = process.argv[2];

function parseEnvelope(value) {
  return Object.fromEntries(String(value || "").split("\n").slice(1).map(line => {
    const split = line.indexOf("=");
    return split < 0 ? [line, ""] : [line.slice(0, split), line.slice(split + 1)];
  }));
}

function jobNames() {
  const root = path.join(STATE_ROOT, "jobs");
  return new Set(fs.readdirSync(root).filter(name => /^aag-[0-9a-f-]+\.json$/iu.test(name)));
}

async function queue() {
  const queueUrl = process.env.AAG_LIVE_QUEUE_URL || (fs.existsSync("/app/server/storage")
    ? "http://172.18.0.1:18188/queue"
    : "http://127.0.0.1:8188/queue");
  const response = await fetch(queueUrl, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`ComfyUI queue probe failed: ${response.status}`);
  return response.json();
}

async function main() {
  if (!output) throw new Error("Evidence output path is required.");
  const naturalRequest = "Create a gentle watercolor illustration of a quiet Jerusalem stone street at sunset with exactly two ordinary adult pedestrians walking beside one another.";
  const productionPrompt = "A gentle watercolor illustration of a quiet Jerusalem stone street at sunset, showing exactly two ordinary adult pedestrians walking beside one another. Both people are fully separated and clearly visible, each with exactly one head, two arms, two hands, and two legs, with anatomically plausible hands and natural adult proportions. Use a balanced street-level composition, coherent stone architecture and perspective, warm soft-pastel sunset lighting, controlled shadows, delicate pigment blooms, visible watercolor paper texture, natural depth, refined subject readability, and a polished professional finish.";
  const upstreamRequest = `${naturalRequest}\n${atlas.marker("manual_browse", "fine-art-traditional-media", "watercolor")}`;
  const attestedJobId = process.env.AAG_LIVE_ATTEST_JOB_ID || null;
  const attestedJob = attestedJobId ? runtimeApi.store.read(STATE_ROOT, attestedJobId) : null;
  const priorHarnessJobId = process.env.AAG_LIVE_PRIOR_HARNESS_JOB_ID || null;
  const priorHarnessJob = priorHarnessJobId ? runtimeApi.store.read(STATE_ROOT, priorHarnessJobId) : null;
  const priorHarnessChild = priorHarnessJob?.child_jobs?.[0]
    ? runtimeApi.store.read(STATE_ROOT, priorHarnessJob.child_jobs[0])
    : null;
  const invocationId = attestedJob?.owner?.invocation_id || `atlas-live-${crypto.randomUUID()}`;
  const trustedRuntime = {
    AAG_WORKSPACE_ID: "visual-atlas-production-acceptance",
    AAG_THREAD_ID: "manual-watercolor",
    AAG_USER_ID: "controlled-local-acceptance",
    AAG_INVOCATION_UUID: invocationId,
    AAG_TURN_ID: invocationId,
    AAG_INVOCATION_PROMPT: naturalRequest,
    AAG_INVOCATION_ATTACHMENTS: [],
    AAG_IMAGE_AGENT_STATE_ROOT: STATE_ROOT,
    AAG_IMAGE_QUEUE_TIMEOUT_MS: 30 * 60 * 1000,
  };
  const args = {
    operation: "generate",
    request: upstreamRequest,
    prompt: productionPrompt,
    source_policy: "auto",
    preservation: "none",
    quality: "fast",
    final_output_quality: "standard",
    aspect_ratio: "1:1",
    count: 1,
    seed: 26090312,
  };

  const beforeJobs = jobNames();
  const queueBefore = await queue();
  const firstResult = await runtimeApi.createTask(args, trustedRuntime, {
    logger: message => process.stderr.write(`${message}\n`),
  });
  const afterFirstJobs = jobNames();
  const secondResult = await runtimeApi.createTask(args, trustedRuntime, {
    logger: message => process.stderr.write(`${message}\n`),
  });
  const afterReplayJobs = jobNames();
  const queueAfter = await queue();
  const envelope = parseEnvelope(firstResult);
  if (envelope.status !== "completed" || !envelope.job_id) throw new Error(`Live generation failed:\n${firstResult}`);
  if (secondResult !== firstResult) throw new Error("Idempotent replay returned a different result envelope.");

  const parent = runtimeApi.store.read(STATE_ROOT, envelope.job_id);
  const child = runtimeApi.store.read(STATE_ROOT, parent.child_jobs[0]);
  const artifact = parent.artifacts[0];
  const artifactFetchUrl = fs.existsSync("/app/server/storage")
    ? `${runtimeApi.adapters.HUB_INTERNAL}/files/${encodeURIComponent(artifact.filename)}`
    : artifact.url;
  const response = await fetch(artifactFetchUrl, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`Published artifact is unreadable: ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  const artifactSha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  if (artifactSha256 !== artifact.sha256) throw new Error("Published artifact hash differs from the verified job artifact.");

  const firstCreated = [...afterFirstJobs].filter(name => !beforeJobs.has(name));
  const replayCreated = [...afterReplayJobs].filter(name => !afterFirstJobs.has(name));
  const selected = parent.atlas?.selections?.[0];
  const checks = {
    completed: parent.status === "COMPLETED" && child.status === "COMPLETED",
    release_current: parent.release === runtimeApi.VERSION,
    exactly_one_verified_artifact: parent.artifacts.length === 1 && child.artifacts.length === 1,
    manual_browse_authoritative: parent.atlas?.used === true && parent.atlas?.mode === "manual_browse" && parent.atlas?.reason === "explicit_user_selection",
    selected_watercolor: selected?.family_id === "fine-art-traditional-media" && selected?.subfamily_id === "watercolor" && selected?.confidence === 1,
    textual_knowledge_only: parent.atlas?.visual_reference_used === false && parent.atlas?.context_chars > 0 && parent.atlas?.context_chars <= atlas.MAX_CONTEXT_CHARS,
    anatomy_contract_present: /exactly one head, two arms, two hands, and two legs/iu.test(productionPrompt),
    prompt_contract_passed: child.engine?.prompt_quality_status === "PRODUCTION_READY" && child.engine?.prompt_fidelity_status === "PASS" && child.engine?.prompt_structure_status === "PASS",
    production_engine_submitted_once: Boolean(child.engine?.prompt_id) && child.transitions.filter(item => item.status === "RUNNING").length === 1,
    idempotent_replay_same_result: secondResult === firstResult,
    idempotent_replay_created_no_jobs: replayCreated.length === 0,
    job_topology_parent_and_child_only: parent.child_jobs.length === 1 && child.parent_job_id === parent.job_id,
    invocation_job_creation_bounded: attestedJob ? firstCreated.length === 0 : firstCreated.length === 2,
    queues_idle_after: (queueAfter.queue_running || []).length === 0 && (queueAfter.queue_pending || []).length === 0,
    published_artifact_hash_verified: artifactSha256 === artifact.sha256,
  };
  if (Object.values(checks).some(value => value !== true)) throw new Error(`Live acceptance checks failed: ${JSON.stringify(checks)}`);

  const evidence = {
    schema: "aag.visual-atlas.live-acceptance.v1",
    verified_at: new Date().toISOString(),
    release: runtimeApi.VERSION,
    natural_request: naturalRequest,
    explicit_selection: { mode: "manual_browse", family_id: "fine-art-traditional-media", subfamily_id: "watercolor" },
    result: { envelope: parseEnvelope(firstResult), parent_job_id: parent.job_id, child_job_id: child.job_id, parent_status: parent.status, child_status: child.status },
    atlas: parent.atlas,
    engine: child.engine,
    artifact: { ...artifact, published_sha256: artifactSha256 },
    idempotency: { attested_existing_job: Boolean(attestedJob), first_created_jobs: firstCreated, replay_created_jobs: replayCreated, identical_result: secondResult === firstResult },
    queue: { before: queueBefore, after: queueAfter },
    harness_history: priorHarnessJob ? {
      prior_job_id: priorHarnessJob.job_id,
      prior_status: priorHarnessJob.status,
      engine_prompt_id: priorHarnessChild?.engine?.prompt_id || null,
      engine_completed_at: priorHarnessChild?.engine?.completed_at || null,
      post_generation_error: priorHarnessJob.error,
      classification: "host-only acceptance harness lacked the trusted Sharp decoder; rerun occurred inside the deployed AnythingLLM runtime",
    } : null,
    checks,
  };
  fs.writeFileSync(output, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  process.stdout.write(`${JSON.stringify({ output, job_id: parent.job_id, artifact_url: artifact.url, elapsed_seconds: child.engine?.elapsed_seconds, checks }, null, 2)}\n`);
}

main().catch(error => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
