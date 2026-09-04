"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const comfy = require("../src/comfy");
const recovery = require("../src/recovery");
const progressUi = require("../integrations/anythingllm/aagImageProgress");

function response(value, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() { return value === null ? "" : JSON.stringify(value); },
  };
}

function completedHistory(promptId) {
  return {
    [promptId]: {
      status: { completed: true, status_str: "success" },
      outputs: { save: { images: [{ filename: "GEN-safe.png", subfolder: "", type: "output" }] } },
    },
  };
}

function telemetry(promptId, jobId, sequence, at, extra = {}) {
  return {
    ok: true,
    schema_version: "aag.comfy-engine-progress.v1",
    prompt_id: promptId,
    job_id: jobId,
    sequence,
    last_engine_progress_at: new Date(at).toISOString(),
    last_engine_progress_event: "sampler_step",
    current_engine_node: "15",
    current_engine_node_class: "SamplerCustomAdvanced",
    current_engine_step: sequence,
    current_engine_step_max: 4,
    ...extra,
  };
}

test("workflow-specific no-progress thresholds are conservative and distinct", () => {
  assert.equal(comfy.workflowClass({ quality: "quality" }), "quality");
  assert.equal(comfy.workflowClass({ quality: "fast" }), "fast");
  assert.equal(comfy.workflowClass({ operation: "upscale" }), "upscale");
  assert.equal(comfy.workflowClass({ preservation: "identity" }), "identity");
  assert.ok(comfy.STALL_PROFILES.quality.sample > comfy.STALL_PROFILES.fast.sample);
  assert.ok(comfy.STALL_PROFILES.identity.default > comfy.STALL_PROFILES.upscale.default);
  assert.equal(comfy.enginePhase({ current_engine_node_class: "SamplerCustomAdvanced" }), "sample");
  assert.equal(comfy.enginePhase({ current_engine_node_class: "UNETLoader" }), "load");
  assert.equal(comfy.enginePhase({ current_engine_node_class: "VAEDecode" }), "output");
});

test("slow but advancing real sampler events never trigger the no-progress interrupt", async () => {
  const promptId = crypto.randomUUID();
  const jobId = `aag-${crypto.randomUUID()}`;
  let now = 1_000;
  let polls = 0;
  let interrupts = 0;
  const fetch = async (url, options = {}) => {
    if (String(url).includes("/history/")) {
      polls += 1;
      return response(polls >= 5 ? completedHistory(promptId) : {});
    }
    if (String(url).includes("/aag/engine-progress/")) {
      return response(telemetry(promptId, jobId, polls, now));
    }
    if (String(url).endsWith("/aag/interrupt")) {
      interrupts += 1;
      return response({ ok: true, prompt_id: promptId });
    }
    throw new Error(`unexpected URL ${url} ${options.method || "GET"}`);
  };
  const image = await comfy.waitForImage(promptId, "lease", {
    fetch,
    now: () => now,
    sleep: async (milliseconds) => { now += milliseconds; },
    noProgressThresholdMs: 7_000,
    historyPollMs: 2_000,
  }, { task: { quality: "quality" }, jobId });
  assert.equal(image.filename, "GEN-safe.png");
  assert.equal(interrupts, 0);
});

test("same sampler step with no output is detected and only the exact prompt is interrupted", async () => {
  const promptId = crypto.randomUUID();
  const jobId = `aag-${crypto.randomUUID()}`;
  let now = 100;
  let running = true;
  let interrupts = 0;
  const events = [];
  const fetch = async (url, options = {}) => {
    if (String(url).includes("/history/")) return response({});
    if (String(url).includes("/aag/engine-progress/")) return response(telemetry(promptId, jobId, 1, 0));
    if (String(url).endsWith("/queue")) {
      return response({ queue_running: running ? [[1, promptId, {}, {}, []]] : [], queue_pending: [] });
    }
    if (String(url).endsWith("/aag/interrupt") && options.method === "POST") {
      interrupts += 1;
      assert.deepEqual(JSON.parse(options.body), { prompt_id: promptId });
      running = false;
      return response({ ok: true, prompt_id: promptId, action: "INTERRUPT_REQUESTED" });
    }
    throw new Error(`unexpected URL ${url}`);
  };
  await assert.rejects(
    comfy.waitForImage(promptId, "lease", {
      fetch,
      now: () => now,
      sleep: async (milliseconds) => { now += milliseconds; },
      noProgressThresholdMs: 10,
      interruptGraceMs: 10,
      onEngineProgress(value) { events.push(value); },
    }, { task: { quality: "quality" }, jobId }),
    error => error.code === "ENGINE_STALLED_RECOVERED" && error.retryable === true
  );
  assert.equal(interrupts, 1);
  assert.ok(events.some(event => event.recovery_action === "INTERRUPT_REQUESTED"));
  assert.ok(events.some(event => event.recovery_outcome === "XPU_LANE_RELEASED"));
});

test("a real progress race withholds interrupt and lets observation continue", async () => {
  const promptId = crypto.randomUUID();
  const jobId = `aag-${crypto.randomUUID()}`;
  let now = 100;
  let polls = 0;
  let interruptRequests = 0;
  const fetch = async (url, options = {}) => {
    if (String(url).includes("/history/")) {
      polls += 1;
      return response(polls >= 4 ? completedHistory(promptId) : {});
    }
    if (String(url).includes("/aag/engine-progress/")) {
      return response(telemetry(promptId, jobId, 1, 0));
    }
    if (String(url).endsWith("/queue")) {
      return response({ queue_running: [[1, promptId, {}, {}, []]], queue_pending: [] });
    }
    if (String(url).endsWith("/aag/interrupt") && options.method === "POST") {
      interruptRequests += 1;
      return response({
        ok: false,
        prompt_id: promptId,
        action: "INTERRUPT_WITHHELD_PROGRESS_CHANGED",
      });
    }
    throw new Error(`unexpected URL ${url}`);
  };
  const image = await comfy.waitForImage(promptId, "lease", {
    fetch,
    now: () => now,
    sleep: async (milliseconds) => { now += milliseconds; },
    noProgressThresholdMs: 10,
    historyPollMs: 10,
  }, { task: { quality: "fast" }, jobId });
  assert.equal(image.filename, "GEN-safe.png");
  assert.equal(interruptRequests, 1);
});

test("interrupt failure becomes service-recovery-required without restart or retry", async () => {
  const promptId = crypto.randomUUID();
  const jobId = `aag-${crypto.randomUUID()}`;
  let now = 100;
  let interrupts = 0;
  let promptSubmissions = 0;
  const events = [];
  const fetch = async (url, options = {}) => {
    if (String(url).includes("/history/")) return response({});
    if (String(url).includes("/aag/engine-progress/")) return response(telemetry(promptId, jobId, 1, 0));
    if (String(url).endsWith("/queue")) return response({ queue_running: [[1, promptId, {}, {}, []]], queue_pending: [] });
    if (String(url).endsWith("/aag/interrupt")) { interrupts += 1; return response({ ok: true, prompt_id: promptId }); }
    if (String(url).endsWith("/prompt")) { promptSubmissions += 1; return response({}); }
    throw new Error(`unexpected URL ${url} ${options.method || "GET"}`);
  };
  await assert.rejects(
    comfy.waitForImage(promptId, "lease", {
      fetch,
      now: () => now,
      sleep: async (milliseconds) => { now += milliseconds; },
      noProgressThresholdMs: 10,
      interruptGraceMs: 10,
      onEngineProgress(value) { events.push(value); },
    }, { task: { quality: "quality" }, jobId }),
    error => error.code === "ENGINE_SERVICE_RECOVERY_REQUIRED" && error.retryable === false
  );
  assert.equal(interrupts, 1);
  assert.equal(promptSubmissions, 0);
  assert.ok(events.some(event => event.recovery_action === "SERVICE_RECOVERY_REQUIRED"));
});

test("wrong prompt ownership blocks cancellation", async () => {
  const promptId = crypto.randomUUID();
  const otherPrompt = crypto.randomUUID();
  const jobId = `aag-${crypto.randomUUID()}`;
  let interrupts = 0;
  const fetch = async (url) => {
    if (String(url).includes("/history/")) return response({});
    if (String(url).endsWith("/queue")) return response({ queue_running: [[1, otherPrompt, {}, {}, []]], queue_pending: [] });
    if (String(url).endsWith("/aag/interrupt")) { interrupts += 1; return response({}); }
    throw new Error(`unexpected URL ${url}`);
  };
  await assert.rejects(
    comfy.exactPromptRecovery(promptId, "lease", { fetch }, { jobId }),
    error => error.code === "ENGINE_INTERRUPT_FAILED"
  );
  assert.equal(interrupts, 0);
  assert.equal(comfy.validEngineProgress(telemetry(promptId, jobId, 1, 0), promptId, `aag-${crypto.randomUUID()}`), false);
});

test("durable stall and recovery evidence reconstructs the correct progress UI after refresh", () => {
  const job = {
    job_id: `aag-${crypto.randomUUID()}`,
    status: "FAILED",
    created_at: new Date(0).toISOString(),
    updated_at: new Date(2_000).toISOString(),
    transitions: [{ status: "RUNNING" }],
    artifacts: [],
    error: { code: "ENGINE_STALLED_RECOVERED" },
  };
  const child = {
    status: "FAILED",
    progress: {
      engine_started_at: new Date(100).toISOString(),
      stall_detected_at: new Date(1_000).toISOString(),
      recovery_action: "INTERRUPT_SUCCEEDED",
      recovery_started_at: new Date(1_100).toISOString(),
      recovery_completed_at: new Date(1_500).toISOString(),
      recovery_outcome: "XPU_LANE_RELEASED",
    },
  };
  const snapshot = recovery.progressSnapshot(job, [child]);
  const rows = progressUi.stageRows(snapshot, false);
  assert.equal(snapshot.lifecycle.engineRecovered, true);
  assert.equal(rows.find(row => row.key === "generationStalled").state, "warning");
  assert.equal(rows.find(row => row.key === "imageGenerationFailed").state, "failed");
  assert.equal(rows.find(row => row.key === "engineRecovered").state, "complete");
  assert.equal(rows.find(row => row.key === "processingResult").state, "future");
  assert.equal(rows.find(row => row.key === "complete").state, "future");
});
