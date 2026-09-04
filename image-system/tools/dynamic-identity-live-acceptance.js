"use strict";

// Run inside the AnythingLLM container from /app/server. It reuses the exact
// current attachment saved with a real authorized chat, but sends only its
// in-memory data URL through the deployed imported-skill runtime boundary.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { PrismaClient } = require("@prisma/client");
const sharp = require("sharp");

const HISTORICAL = new Set([
  "8b131e3030a094173004ae17df02b9fa94d523cb273398b027ea6bb31e1f2c61",
  "93665635711952c6a5da892bea90cc892b7c0a4a6748416e13a69ffd124eced6",
]);
const CHAT_ID = Number(process.env.AAG_ACCEPTANCE_CHAT_ID || 431);
const STATE = "/app/server/storage/aag-image-agent-state";
const handler = require("/app/server/storage/plugins/agent-skills/aag-image-task/handler.js");

function digest(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }

async function main() {
  const prisma = new PrismaClient();
  try {
    const chat = await prisma.workspace_chats.findUnique({ where: { id: CHAT_ID } });
    if (!chat) throw new Error("acceptance source chat not found");
    const response = JSON.parse(chat.response || "{}");
    const attachments = Array.isArray(response.attachments) ? response.attachments : [];
    if (attachments.length !== 1 || !String(attachments[0]?.mime || "").startsWith("image/")) throw new Error("acceptance source does not contain exactly one image attachment");
    const match = String(attachments[0].contentString || "").match(/^data:image\/(?:jpeg|jpg|png|webp);base64,(.+)$/s);
    if (!match) throw new Error("acceptance attachment data URL is invalid");
    const sourceBytes = Buffer.from(match[1], "base64");
    const sourceSha256 = digest(sourceBytes);
    if (HISTORICAL.has(sourceSha256)) throw new Error("acceptance reference is a historical fixture");

    const invocation = `dynamic-identity-live-${crypto.randomUUID()}`;
    const runtimeArgs = {
      AAG_WORKSPACE_ID: String(chat.workspaceId),
      AAG_THREAD_ID: String(chat.thread_id),
      AAG_USER_ID: String(chat.user_id ?? "unknown"),
      AAG_INVOCATION_UUID: invocation,
      AAG_TURN_ID: crypto.randomUUID(),
      AAG_INVOCATION_PROMPT: String(chat.prompt || ""),
      AAG_INVOCATION_ATTACHMENTS: attachments,
    };
    const task = {
      operation: "transform",
      request: String(chat.prompt || "Use the person in the attached image and create the same person in a different scene."),
      source_policy: "current_attachment",
      source_index: 1,
      preservation: "identity",
      quality: "quality",
      aspect_ratio: "auto",
      count: 1,
    };
    const started = new Date().toISOString();
    const result = await handler.runtime.handler.call({ runtimeArgs, logger() {} }, task);
    const jobId = String(result).match(/^job_id=(aag-[0-9a-f-]+)$/m)?.[1];
    if (!jobId) throw new Error(`provider returned no job ID: ${result}`);
    const job = JSON.parse(fs.readFileSync(path.join(STATE, "jobs", `${jobId}.json`), "utf8"));
    const children = job.child_jobs.map(id => JSON.parse(fs.readFileSync(path.join(STATE, "jobs", `${id}.json`), "utf8")));
    const artifact = job.artifacts?.[0];
    let completedArtifact = null;
    if (artifact?.filename) {
      const fetched = await fetch(`http://172.18.0.1:18190/files/${encodeURIComponent(artifact.filename)}`);
      if (!fetched.ok) throw new Error(`completed artifact fetch failed: ${fetched.status}`);
      const bytes = Buffer.from(await fetched.arrayBuffer());
      const metadata = await sharp(bytes).metadata();
      completedArtifact = { ...artifact, fetched_sha256: digest(bytes), fetched_bytes: bytes.length, decoded_width: metadata.width, decoded_height: metadata.height, decoded_format: metadata.format };
    }
    const evidence = {
      schema_version: "aag.dynamic-trusted-identity.live-acceptance.v1",
      started_at: started,
      completed_at: new Date().toISOString(),
      source_chat_id: CHAT_ID,
      source_name: attachments[0].name,
      source_mime: attachments[0].mime,
      source_sha256: sourceSha256,
      source_is_historical_fixture: HISTORICAL.has(sourceSha256),
      trusted_scope: { workspace_id: runtimeArgs.AAG_WORKSPACE_ID, thread_id: runtimeArgs.AAG_THREAD_ID, user_id: runtimeArgs.AAG_USER_ID, invocation_id: invocation, turn_id: runtimeArgs.AAG_TURN_ID },
      task,
      provider_result: result,
      parent_job: job,
      child_jobs: children,
      completed_artifact: completedArtifact,
      assertions: {
        completed: job.status === "COMPLETED",
        identity_workflow: job.workflow_id === "transform.human.identity.v1",
        current_attachment: job.source?.kind === "current_attachment" && job.source?.index === 1,
        new_reference_hash: !HISTORICAL.has(sourceSha256),
        no_subject_fallback: task.preservation === "identity" && job.workflow_id !== "transform.general.fast.v1" && job.workflow_id !== "transform.general.quality.v1",
        artifact_verified: Boolean(completedArtifact) && completedArtifact.fetched_sha256 === artifact?.sha256 && completedArtifact.decoded_width === 896 && completedArtifact.decoded_height === 1152,
      },
    };
    process.stdout.write(JSON.stringify(evidence, null, 2) + "\n");
    if (!Object.values(evidence.assertions).every(Boolean)) process.exitCode = 1;
  } finally {
    await prisma.$disconnect();
  }
}

main().catch(error => { console.error(error?.stack || error); process.exit(1); });
