"use strict";

const crypto = require("crypto");
const { WorkspaceChats } = require("../../../models/workspaceChats");
const { writeResponseChunk } = require("../../helpers/chat/responses");

const TASK_HANDLER = "/app/server/storage/plugins/agent-skills/aag-image-task/handler.js";
const JOB_HANDLER = "/app/server/storage/plugins/agent-skills/aag-image-job/handler.js";
const IDENTITY_COMMAND = /^\/aag-identity(?:\s|$)/i;
const JOB_COMMAND = /^\/aag-image(?:\s|$)/i;
const JOB_ID = /^aag-[0-9a-f-]{36}$/i;

function isAagLocalImageCommand(message) {
  const value = String(message || "").trim();
  return IDENTITY_COMMAND.test(value) || JOB_COMMAND.test(value);
}

function safeScopePart(value, fallback) {
  const out = String(value ?? fallback).replace(/[^a-zA-Z0-9._:-]/g, "-").slice(0, 120);
  return out || fallback;
}

function runtimeArgs(workspace, user, thread, msgUUID, context = {}, attachments = []) {
  const invocationId = safeScopePart(msgUUID, crypto.randomUUID());
  const threadId = thread?.id != null
    ? `thread:${thread.id}`
    : context.apiSessionId
      ? `api:${safeScopePart(context.apiSessionId, "default")}`
      : "workspace-main";
  return {
    AAG_WORKSPACE_ID: safeScopePart(workspace?.id, "unknown"),
    AAG_THREAD_ID: safeScopePart(threadId, "unknown"),
    AAG_USER_ID: safeScopePart(user?.id, "local-user"),
    AAG_INVOCATION_UUID: invocationId,
    AAG_TURN_ID: invocationId,
    AAG_INVOCATION_ATTACHMENTS: attachments,
    AAG_INVOCATION_PROMPT: context.originalMessage || "",
  };
}

function parseIdentity(message, attachments) {
  const match = String(message || "").trim().match(/^\/aag-identity(?:\s+(?:--seed\s+)?([0-9]{1,10}))?\s*$/i);
  if (!match) throw new Error("Use /aag-identity [--seed N] with exactly one authorized image attachment.");
  if (!Array.isArray(attachments) || attachments.length !== 1) {
    throw new Error("Human Identity requires exactly one current authorized image attachment.");
  }
  const attachment = attachments[0];
  if (!String(attachment?.mime || "").toLowerCase().startsWith("image/") || !String(attachment?.contentString || "").startsWith("data:image/")) {
    throw new Error("Human Identity accepts one JPG, PNG, or WEBP image attachment.");
  }
  const seed = match[1] === undefined ? undefined : Number(match[1]);
  if (seed !== undefined && (!Number.isSafeInteger(seed) || seed < 0 || seed > 2_147_483_647)) {
    throw new Error("The seed must be an integer from 0 through 2147483647.");
  }
  return {
    handler: TASK_HANDLER,
    args: {
      operation: "transform",
      request: "Local Human Identity Production-v1 Contract B portrait",
      source_policy: "current_attachment",
      source_index: 1,
      preservation: "identity",
      quality: "quality",
      aspect_ratio: "auto",
      count: 1,
      ...(seed === undefined ? {} : { seed }),
    },
  };
}

function parseJob(message, attachments) {
  if (Array.isArray(attachments) && attachments.length) {
    throw new Error("Status and cancel commands do not accept attachments.");
  }
  const match = String(message || "").trim().match(/^\/aag-image\s+(status|cancel)\s+(aag-[0-9a-f-]{36})\s*$/i);
  if (!match || !JOB_ID.test(match[2])) throw new Error("Use /aag-image status JOB_ID or /aag-image cancel JOB_ID.");
  return { handler: JOB_HANDLER, args: { action: match[1].toLowerCase(), job_id: match[2] } };
}

function attachmentAudit(attachments) {
  if (!Array.isArray(attachments) || attachments.length !== 1) return [];
  const attachment = attachments[0];
  const raw = String(attachment?.contentString || "");
  const encoded = raw.includes(",") ? raw.slice(raw.indexOf(",") + 1) : "";
  let digest = null;
  try { digest = crypto.createHash("sha256").update(Buffer.from(encoded, "base64")).digest("hex"); } catch {}
  return [{
    name: String(attachment?.name || "reference-image").slice(0, 160),
    mime: String(attachment?.mime || "").slice(0, 80),
    sha256: digest,
    storage: "not-persisted",
    transport: "local-command-only",
  }];
}

async function persistResult(workspace, message, result, user, thread, context, audit) {
  try {
    const { chat } = await WorkspaceChats.new({
      workspaceId: workspace.id,
      prompt: String(message),
      response: {
        text: result,
        sources: [],
        type: "aagLocalImageCommand",
        attachments: [],
        localReferenceAudit: audit,
        metrics: { localOnly: true, llmInvoked: false, telemetryPayload: false },
      },
      include: true,
      threadId: thread?.id || null,
      apiSessionId: context.apiSessionId || null,
      user,
    });
    return chat?.id || null;
  } catch {
    return null;
  }
}

async function executeAagLocalImageCommand(
  workspace,
  message,
  msgUUID,
  user = null,
  thread = null,
  response = null,
  attachments = [],
  _signal = null,
  context = {}
) {
  const originalMessage = String(message || "");
  try {
    const parsed = IDENTITY_COMMAND.test(originalMessage.trim())
      ? parseIdentity(originalMessage, attachments)
      : parseJob(originalMessage, attachments);

    if (response) {
      writeResponseChunk(response, {
        uuid: msgUUID,
        type: "statusResponse",
        textResponse: "AAG local image command accepted; reference data remains inside the local AnythingLLM boundary.",
        sources: [],
        close: false,
        animate: true,
        error: null,
      });
    }

    const imported = require(parsed.handler);
    const callContext = {
      runtimeArgs: runtimeArgs(workspace, user, thread, msgUUID, { ...context, originalMessage }, attachments),
      logger: () => {},
      introspect: () => {},
    };
    const result = String(await imported.runtime.handler.call(callContext, parsed.args));
    const audit = attachmentAudit(attachments);
    const chatId = await persistResult(workspace, originalMessage, result, user, thread, context, audit);
    return {
      uuid: msgUUID,
      id: msgUUID,
      type: "textResponse",
      textResponse: result,
      sources: [],
      outputs: [],
      chatId,
      close: true,
      error: null,
      metrics: { localOnly: true, llmInvoked: false, attachmentBytesPersisted: false },
    };
  } catch (error) {
    return {
      uuid: msgUUID,
      id: msgUUID,
      type: "textResponse",
      textResponse: "",
      sources: [],
      outputs: [],
      close: true,
      error: String(error?.message || "The local AAG image command failed.").slice(0, 300),
      metrics: { localOnly: true, llmInvoked: false, attachmentBytesPersisted: false },
    };
  }
}

module.exports = { isAagLocalImageCommand, executeAagLocalImageCommand };
