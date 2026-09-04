#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const WebSocket = require("/app/server/node_modules/ws");
const { Workspace } = require("/app/server/models/workspace");
const { WorkspaceThread } = require("/app/server/models/workspaceThread");
const {
  WorkspaceAgentInvocation,
} = require("/app/server/models/workspaceAgentInvocation");

const output = process.argv[2];
const prompt = process.argv[3];
const label = process.argv[4] || "routing-agent-acceptance";
if (!output || !prompt) throw new Error("Usage: script OUTPUT PROMPT [LABEL]");

const jobRoot = "/app/server/storage/aag-image-agent-state/jobs";

function jobs() {
  try {
    return new Set(
      fs.readdirSync(jobRoot).filter((name) => /^aag-[0-9a-f-]+\.json$/i.test(name))
    );
  } catch (error) {
    if (error.code === "ENOENT") return new Set();
    throw error;
  }
}

function readJob(filename) {
  return JSON.parse(fs.readFileSync(path.resolve(jobRoot, filename), "utf8"));
}

async function main() {
  const workspace = await Workspace.get({ slug: "image-generator" });
  const { thread, message } = await WorkspaceThread.new(workspace, null, {
    name: `${label} ${new Date().toISOString()}`,
  });
  if (!thread) throw new Error(message || "Could not create fresh thread");
  const { invocation, message: invocationError } =
    await WorkspaceAgentInvocation.new({
      prompt,
      workspace,
      user: null,
      thread,
    });
  if (!invocation) throw new Error(invocationError || "Could not create invocation");

  const before = jobs();
  const startedAt = new Date().toISOString();
  const events = [];
  const socket = new WebSocket(
    `ws://127.0.0.1:3001/api/agent-invocation/${invocation.uuid}`
  );
  let completionSeen = false;
  let closeTimer = null;
  const startedMs = Date.now();

  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      socket.terminate();
      reject(new Error("Agent acceptance timed out after 600 seconds"));
    }, 600_000);
    socket.on("message", (bytes) => {
      const raw = bytes.toString();
      let event;
      try {
        event = JSON.parse(raw);
      } catch {
        event = { type: "unparsed", content: raw };
      }
      events.push({ receivedAt: new Date().toISOString(), ...event });
      const streamEventType =
        event.type === "reportStreamEvent" ? event.content?.type : event.type;
      if (["fullTextResponse", "wssFailure"].includes(streamEventType)) {
        completionSeen = true;
        closeTimer = setTimeout(() => socket.close(), 1500);
      }
    });
    socket.on("error", (error) => {
      clearTimeout(timeout);
      if (closeTimer) clearTimeout(closeTimer);
      reject(error);
    });
    socket.on("close", () => {
      clearTimeout(timeout);
      if (closeTimer) clearTimeout(closeTimer);
      completionSeen ? resolve() : reject(new Error("Socket closed before completion"));
    });
  });

  const after = jobs();
  const createdFiles = [...after].filter((filename) => !before.has(filename));
  const createdJobs = createdFiles.map(readJob);
  const parentJobs = createdJobs.filter((job) => !job.parent_job_id);
  const childJobs = createdJobs.filter((job) => !!job.parent_job_id);
  const artifacts = new Map();
  for (const job of createdJobs) {
    for (const artifact of job.artifacts || [])
      artifacts.set(artifact.artifact_id, artifact);
  }
  const result = {
    schema: "aag.anythingllm.live-agent-routing-acceptance.v1",
    startedAt,
    completedAt: new Date().toISOString(),
    elapsedMs: Date.now() - startedMs,
    workspace: {
      id: workspace.id,
      slug: workspace.slug,
      chatProvider: workspace.chatProvider,
      chatModel: workspace.chatModel,
    },
    thread: { id: thread.id, slug: thread.slug, name: thread.name },
    invocation: { id: invocation.id, uuid: invocation.uuid },
    prompt,
    jobsBefore: before.size,
    jobsAfter: after.size,
    createdJobFiles: createdFiles,
    parentJobCount: parentJobs.length,
    childJobCount: childJobs.length,
    uniqueArtifactCount: artifacts.size,
    parentJobs,
    childJobs,
    artifacts: [...artifacts.values()],
    events,
  };
  fs.writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 });
  console.log(
    JSON.stringify({
      output,
      thread: result.thread,
      invocation: result.invocation,
      elapsedMs: result.elapsedMs,
      parentJobCount: result.parentJobCount,
      childJobCount: result.childJobCount,
      uniqueArtifactCount: result.uniqueArtifactCount,
      eventTypes: events.map((event) => event.type),
      fullText: events.find((event) => event.type === "fullTextResponse")?.content || null,
      usage: events.find((event) => event.type === "usageMetrics")?.metrics || null,
    })
  );
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
