"use strict";

const crypto = require("crypto");

const TASK_HANDLER = "/app/server/storage/plugins/agent-skills/aag-image-task/handler.js";
const SLASH = /^\/aag-generate(?:\s|$)/i;
const GENERATE = /^(?:(?:please\s+)?(?:create|generate|make)(?:\s+me)?\s+(?:an?\s+)?(?:image|picture)|(?:בבקשה\s+)?(?:צור|תיצור|תייצר|עשה|תעשה|הכן|תכין)(?:\s+לי)?\s+תמונה)/i;
const UPSCALE = /\b(?:upscale|enlarge|sharpen|enhance|increase\s+(?:the\s+)?resolution)\b|(?:תחדד|חדד|שפר(?:\s+את)?\s+האיכות|תשפר(?:\s+את)?\s+האיכות|הגדל(?:\s+את)?\s+הרזולוציה)/i;
const IDENTITY = /\b(?:preserve|keep|retain|same).{0,24}\b(?:face|identity)\b|(?:שמור|תשמור|שמר|תשמר).{0,24}(?:הפנים|פנים|הזהות|זהות)/i;
const TRANSFORM = /\b(?:edit|transform|change|restyle|replace|recompose)\b|(?:שנה|תשנה|ערוך|תערוך|החלף|תחליף|סגנון)/i;
const ASPECTS = new Set(["auto", "1:1", "16:9", "9:16", "4:3", "3:2", "square", "landscape", "portrait"]);
const QUALITIES = new Set(["auto", "fast", "balanced", "quality"]);
const COMPOSER_ENVELOPE_MARKER = /(?:^|\n)AAG_COMPOSER_(?:STRUCTURED_REQUIREMENTS|INTENT_SIGNATURE)_V1=/;

function safeScopePart(value, fallback) {
  const output = String(value ?? fallback).replace(/[^a-zA-Z0-9._:-]/g, "-").slice(0, 120);
  return output || fallback;
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

function oneImage(attachments) {
  if (!Array.isArray(attachments) || attachments.length !== 1) throw new Error("This local image intent requires exactly one current image attachment.");
  const attachment = attachments[0];
  if (!String(attachment?.mime || "").toLowerCase().startsWith("image/") || !String(attachment?.contentString || "").startsWith("data:image/")) {
    throw new Error("The local image intent accepts one JPG, PNG, or WEBP attachment.");
  }
}

function seedFrom(value) {
  if (value === undefined) return undefined;
  const seed = Number(value);
  if (!Number.isSafeInteger(seed) || seed < 0 || seed > 2_147_483_647) throw new Error("The seed must be an integer from 0 through 2147483647.");
  return seed;
}

function parseSlash(message, attachments) {
  if (Array.isArray(attachments) && attachments.length) throw new Error("Ordinary generation does not accept an image attachment.");
  let rest = String(message).trim().replace(/^\/aag-generate\b/i, "").trim();
  let aspect = "auto", quality = "auto", seed;
  while (rest.startsWith("--") && !rest.startsWith("-- ")) {
    const match = rest.match(/^--(aspect|quality|seed)\s+(\S+)(?:\s+|$)/i);
    if (!match) break;
    const key = match[1].toLowerCase(), value = match[2].toLowerCase();
    if (key === "aspect") {
      if (!ASPECTS.has(value)) throw new Error("Unsupported aspect. Use square, portrait, landscape, 1:1, 16:9, 9:16, 4:3, or 3:2.");
      aspect = value === "square" ? "1:1" : value;
    } else if (key === "quality") {
      if (!QUALITIES.has(value)) throw new Error("Unsupported quality. Use auto, fast, balanced, or quality.");
      quality = value;
    } else seed = seedFrom(value);
    rest = rest.slice(match[0].length).trim();
  }
  if (rest.startsWith("--")) rest = rest.slice(2).trim();
  if (!rest || rest.startsWith("--")) throw new Error("Use /aag-generate [--aspect VALUE] [--seed N] -- PROMPT.");
  return {
    operation: "generate", request: rest, prompt: rest, source_policy: "auto",
    preservation: "none", quality, aspect_ratio: aspect, count: 1,
    ...(seed === undefined ? {} : { seed }),
  };
}

function parseNatural(message, attachments) {
  const prompt = String(message || "").trim();
  const images = Array.isArray(attachments) ? attachments.length : 0;
  const seedMatch = prompt.match(/(?:--seed|\bseed|זרע)\s+([0-9]{1,10})/i);
  const seed = seedFrom(seedMatch?.[1]);
  if (images === 0 && GENERATE.test(prompt)) {
    return { operation: "generate", request: prompt, prompt, source_policy: "auto", preservation: "none", quality: "auto", aspect_ratio: "auto", count: 1, ...(seed === undefined ? {} : { seed }) };
  }
  if (images !== 1) return null;
  if (IDENTITY.test(prompt)) {
    oneImage(attachments);
    return { operation: "transform", request: "Local Human Identity Production-v1 Contract B portrait", source_policy: "current_attachment", source_index: 1, preservation: "identity", quality: "quality", aspect_ratio: "auto", count: 1, ...(seed === undefined ? {} : { seed }) };
  }
  if (UPSCALE.test(prompt)) {
    oneImage(attachments);
    return { operation: "upscale", request: prompt, source_policy: "current_attachment", source_index: 1, preservation: "none", quality: "auto", aspect_ratio: "auto", scale: 4, count: 1 };
  }
  if (TRANSFORM.test(prompt)) {
    oneImage(attachments);
    return { operation: "transform", request: prompt, prompt, source_policy: "current_attachment", source_index: 1, preservation: "subject", quality: "auto", aspect_ratio: "auto", count: 1, ...(seed === undefined ? {} : { seed }) };
  }
  return null;
}

function parseOrdinary(message, attachments = []) {
  if (SLASH.test(String(message || "").trim())) return parseSlash(message, attachments);
  const parsed = parseNatural(message, attachments);
  if (!parsed) throw new Error("The request is not an unambiguous local image intent.");
  return parsed;
}

function isAagOrdinaryCommand(message, attachments = []) {
  const value = String(message || "").trim();
  // Advanced Composer requests carry signed, authoritative structured intent
  // and must reach the shared model-neutral boundary.  The boundary verifies
  // the HMAC before any tool call can execute.  Deferring the signed envelope
  // also keeps the original human-readable request available to the existing
  // downstream semantic-fidelity gate instead of replacing it with ciphertext.
  if (COMPOSER_ENVELOPE_MARKER.test(value)) return false;
  if (SLASH.test(value)) return true;
  if ((!attachments || attachments.length === 0) && GENERATE.test(value)) return true;
  if (Array.isArray(attachments) && attachments.length === 1 && (IDENTITY.test(value) || UPSCALE.test(value) || TRANSFORM.test(value))) return true;
  return false;
}

function attachmentAudit(attachments) {
  if (!Array.isArray(attachments) || attachments.length !== 1) return [];
  const attachment = attachments[0];
  const raw = String(attachment?.contentString || ""), encoded = raw.includes(",") ? raw.slice(raw.indexOf(",") + 1) : "";
  let sha256 = null;
  try { sha256 = crypto.createHash("sha256").update(Buffer.from(encoded, "base64")).digest("hex"); } catch {}
  return [{ name: String(attachment?.name || "image").slice(0, 160), mime: String(attachment?.mime || "").slice(0, 80), sha256, storage: "not-persisted", transport: "local-command-only" }];
}

async function persistResult(workspace, message, result, user, thread, context, audit) {
  try {
    const { WorkspaceChats } = require("../../../models/workspaceChats");
    const { chat } = await WorkspaceChats.new({
      workspaceId: workspace.id, prompt: String(message),
      response: { text: result, sources: [], type: "aagLocalOrdinaryCommand", attachments: [], localReferenceAudit: audit, metrics: { localOnly: true, llmInvoked: false, telemetryPayload: false } },
      include: true, threadId: thread?.id || null, apiSessionId: context.apiSessionId || null, user,
    });
    return chat?.id || null;
  } catch { return null; }
}

async function executeAagOrdinaryCommand(workspace, message, msgUUID, user = null, thread = null, response = null, attachments = [], _signal = null, context = {}) {
  const originalMessage = String(message || "");
  try {
    const args = parseOrdinary(originalMessage, attachments);
    if (response) {
      const { writeResponseChunk } = require("../../helpers/chat/responses");
      writeResponseChunk(response, { uuid: msgUUID, type: "statusResponse", textResponse: "AAG local image task accepted; no LLM or remote inference will be invoked.", sources: [], close: false, animate: true, error: null });
    }
    const imported = require(TASK_HANDLER);
    const callContext = { runtimeArgs: runtimeArgs(workspace, user, thread, msgUUID, { ...context, originalMessage }, attachments), logger: () => {}, introspect: () => {} };
    const result = String(await imported.runtime.handler.call(callContext, args));
    const chatId = await persistResult(workspace, originalMessage, result, user, thread, context, attachmentAudit(attachments));
    return { uuid: msgUUID, id: msgUUID, type: "textResponse", textResponse: result, sources: [], outputs: [], chatId, close: true, error: null, metrics: { localOnly: true, llmInvoked: false, attachmentBytesPersisted: false } };
  } catch (error) {
    return { uuid: msgUUID, id: msgUUID, type: "textResponse", textResponse: "", sources: [], outputs: [], close: true, error: String(error?.message || "The local AAG image intent failed.").slice(0, 300), metrics: { localOnly: true, llmInvoked: false, attachmentBytesPersisted: false } };
  }
}

module.exports = { isAagOrdinaryCommand, executeAagOrdinaryCommand, parseOrdinary, parseNatural, parseSlash };
