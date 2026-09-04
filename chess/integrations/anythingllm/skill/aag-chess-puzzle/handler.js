"use strict";

const crypto = require("crypto");
const fs = require("fs/promises");
const http = require("http");
const path = require("path");

const SOCKET_PATH = "/app/server/storage/aag-chess-puzzle/bridge.sock";
const ARTIFACT_ROOT = "/app/server/storage/aag-chess-puzzle/outputs";
const CREATE_FILES_LIB =
  "/app/server/utils/agents/aibitat/plugins/create-files/lib.js";
const FILES_LIB = "/app/server/utils/files";
const WORKSPACE_CHATS_MODEL = "/app/server/models/workspaceChats";
const BRIDGE_TIMEOUT_MS = 250000;
const MAX_RESPONSE_BYTES = 1024 * 1024;
const MAX_ARTIFACT_BYTES = 20 * 1024 * 1024;
const MAX_COUNT = 10;
const MAX_ATTEMPTS = 2000;
const MAX_STOCKFISH_NODES = 200000;
const SAFE_RESULT_SCHEMA = "aag-anythingllm-chess-result-v1";
const FOLLOWUP_RESULT_SCHEMA = "aag-anythingllm-chess-followup-v1";
const PUBLIC_ID_PATTERN = /^aag-[0-9a-f]{20}$/;
const FORBIDDEN_KEYS = new Set([
  "bestmove",
  "best_move",
  "certificate",
  "continuation",
  "key_move",
  "key_moves",
  "mate_move",
  "proof",
  "proof_tree",
  "pv",
  "solution",
  "stockfish_pv"
]);

class SkillError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

function integer(value, fallback, name, minimum, maximum) {
  const resolved = value === undefined || value === null ? fallback : value;
  if (!Number.isSafeInteger(resolved)) {
    throw new SkillError("invalid_request", `${name} must be an integer`);
  }
  if (resolved < minimum || resolved > maximum) {
    throw new SkillError(
      "invalid_request",
      `${name} must be between ${minimum} and ${maximum}`
    );
  }
  return resolved;
}

function choice(value, fallback, name, allowed) {
  const resolved = value === undefined || value === null ? fallback : value;
  if (typeof resolved !== "string" || !allowed.includes(resolved)) {
    throw new SkillError(
      "invalid_request",
      `${name} must be one of: ${allowed.join(", ")}`
    );
  }
  return resolved;
}

function normalizeFormats(value) {
  if (value === undefined || value === null || value === "") return "png";
  if (Array.isArray(value)) {
    if (
      value.length < 1 ||
      value.length > 2 ||
      new Set(value).size !== value.length ||
      value.some(item => !["png", "svg"].includes(item))
    ) {
      throw new SkillError(
        "invalid_request",
        "formats may contain only png and svg without duplicates"
      );
    }
    return value.length === 2 ? "svg+png" : value[0];
  }
  if (typeof value !== "string") {
    throw new SkillError("invalid_request", "formats must be png, svg, or svg+png");
  }
  const normalized = value.toLowerCase().replace(/\s/g, "");
  const aliases = {
    png: "png",
    svg: "svg",
    "svg+png": "svg+png",
    "png+svg": "svg+png",
    "svg,png": "svg+png",
    "png,svg": "svg+png",
    both: "svg+png"
  };
  if (!aliases[normalized]) {
    throw new SkillError("invalid_request", "formats must be png, svg, or svg+png");
  }
  return aliases[normalized];
}

function normalizeArgs(args) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    throw new SkillError("invalid_request", "Chess parameters must be an object");
  }
  const allowed = new Set([
    "action",
    "mate",
    "side",
    "difficulty",
    "count",
    "engine",
    "formats",
    "seed",
    "max_attempts",
    "stockfish_nodes",
    "density",
    "puzzle_number",
    "public_id"
  ]);
  const unexpected = Object.keys(args).filter(key => !allowed.has(key));
  if (unexpected.length) {
    throw new SkillError(
      "invalid_request",
      `Unsupported chess parameters: ${unexpected.sort().join(", ")}`
    );
  }
  const action = choice(args.action, "generate", "action", [
    "generate",
    "hint",
    "solution"
  ]);
  if (action !== "generate") {
    const generationOnly = [
      "mate", "side", "difficulty", "count", "engine", "formats", "seed",
      "max_attempts", "stockfish_nodes", "density"
    ].filter(key => args[key] !== undefined && args[key] !== null);
    if (generationOnly.length) {
      throw new SkillError(
        "invalid_request",
        "Hint and solution follow-ups accept only a puzzle number or public ID"
      );
    }
    const puzzleNumber = args.puzzle_number === undefined || args.puzzle_number === null
      ? null
      : integer(args.puzzle_number, undefined, "puzzle_number", 1, MAX_COUNT);
    const publicId = args.public_id === undefined || args.public_id === null
      ? null
      : args.public_id;
    if (publicId !== null && (typeof publicId !== "string" || !PUBLIC_ID_PATTERN.test(publicId))) {
      throw new SkillError("invalid_request", "public_id is invalid");
    }
    if (puzzleNumber !== null && publicId !== null) {
      throw new SkillError("invalid_request", "Select a puzzle by number or public ID, not both");
    }
    return { action, puzzle_number: puzzleNumber, public_id: publicId };
  }
  const difficultyDefaulted = args.difficulty === undefined || args.difficulty === null;
  const seedDefaulted = args.seed === undefined || args.seed === null;
  return {
    action,
    mate: integer(args.mate, undefined, "mate", 1, 3),
    side: choice(args.side, "white", "side", ["white", "black"]),
    difficulty: choice(args.difficulty, "medium", "difficulty", [
      "easy",
      "medium",
      "hard"
    ]),
    count: integer(args.count, 1, "count", 1, MAX_COUNT),
    engine: choice(args.engine, "auto", "engine", [
      "auto",
      "stockfish",
      "builtin"
    ]),
    formats: normalizeFormats(args.formats),
    seed: integer(args.seed, 0, "seed", 0, Number.MAX_SAFE_INTEGER),
    max_attempts: integer(
      args.max_attempts,
      1000,
      "max_attempts",
      1,
      MAX_ATTEMPTS
    ),
    stockfish_nodes: integer(
      args.stockfish_nodes,
      20000,
      "stockfish_nodes",
      100,
      MAX_STOCKFISH_NODES
    ),
    density: choice(args.density, "auto", "density", [
      "auto",
      "sparse",
      "normal",
      "rich"
    ]),
    _difficulty_defaulted: difficultyDefaulted,
    _seed_defaulted: seedDefaulted
  };
}

function cleanScope(value) {
  const cleaned = String(value ?? "unknown")
    .replace(/[^a-zA-Z0-9_-]/g, "_")
    .slice(0, 96);
  return cleaned || "unknown";
}

function trustedScope(runtimeArgs = {}) {
  return {
    workspace_id: cleanScope(runtimeArgs.AAG_WORKSPACE_ID),
    thread_id: cleanScope(runtimeArgs.AAG_THREAD_ID),
    user_id: cleanScope(runtimeArgs.AAG_USER_ID)
  };
}

function assertNoSolutionLeak(value) {
  if (Array.isArray(value)) {
    value.forEach(assertNoSolutionLeak);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (FORBIDDEN_KEYS.has(key.toLowerCase())) {
      throw new SkillError(
        "unsafe_bridge_response",
        "The chess service returned prohibited private analysis"
      );
    }
    assertNoSolutionLeak(child);
  }
}

function requestBridge(payload, options = {}) {
  const socketPath = options.socketPath || SOCKET_PATH;
  const timeoutMs = options.timeoutMs || BRIDGE_TIMEOUT_MS;
  const endpoint = options.endpoint || "/v1/generate";
  const expectedSchema = options.expectedSchema || SAFE_RESULT_SCHEMA;
  const body = Buffer.from(JSON.stringify(payload), "utf8");
  return new Promise((resolve, reject) => {
    const request = http.request(
      {
        socketPath,
        path: endpoint,
        method: "POST",
        timeout: timeoutMs,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Content-Length": body.length
        }
      },
      response => {
        const chunks = [];
        let size = 0;
        response.on("data", chunk => {
          size += chunk.length;
          if (size > MAX_RESPONSE_BYTES) {
            request.destroy(
              new SkillError("invalid_bridge_response", "Chess response was too large")
            );
            return;
          }
          chunks.push(chunk);
        });
        response.on("end", () => {
          let parsed;
          try {
            parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          } catch {
            reject(
              new SkillError("invalid_bridge_response", "Chess service response was malformed")
            );
            return;
          }
          if (response.statusCode !== 200) {
            reject(
              new SkillError(
                typeof parsed.error === "string" ? parsed.error : "generation_failed",
                typeof parsed.message === "string"
                  ? parsed.message
                  : "The verified chess puzzle could not be generated"
              )
            );
            return;
          }
          try {
            if (parsed.schema !== expectedSchema || parsed.status !== "success") {
              throw new SkillError(
                "invalid_bridge_response",
                "Chess service returned an unsupported result"
              );
            }
            if (expectedSchema === SAFE_RESULT_SCHEMA) assertNoSolutionLeak(parsed);
            resolve(parsed);
          } catch (error) {
            reject(error);
          }
        });
      }
    );
    request.on("timeout", () => {
      request.destroy(
        new SkillError(
          "skill_timeout",
          `Chess generation exceeded ${Math.round(timeoutMs / 1000)} seconds`
        )
      );
    });
    request.on("error", error => {
      if (error instanceof SkillError) reject(error);
      else reject(new SkillError("bridge_unavailable", "Local chess service is unavailable"));
    });
    request.end(body);
  });
}

function safeRelativePath(value, kind) {
  if (typeof value !== "string" || value.includes("\\") || value.includes("\0")) {
    throw new SkillError("unsafe_artifact", `${kind} path is invalid`);
  }
  const parts = value.split("/");
  const requestPattern = /^request-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const artifactPattern = /^puzzle-[0-9]{4}\.(png|svg)$/;
  if (
    (kind === "artifact" &&
      (parts.length !== 3 ||
        !requestPattern.test(parts[0]) ||
        parts[1] !== "puzzles" ||
        !artifactPattern.test(parts[2]))) ||
    (kind === "manifest" &&
      (parts.length !== 2 ||
        !requestPattern.test(parts[0]) ||
        parts[1] !== "manifest.json"))
  ) {
    throw new SkillError("unsafe_artifact", `${kind} path is outside the request directory`);
  }
  return parts.join("/");
}

async function readVerifiedFile(relative, expectedHash, kind) {
  const safe = safeRelativePath(relative, kind);
  const absolute = path.join(ARTIFACT_ROOT, ...safe.split("/"));
  const realRoot = await fs.realpath(ARTIFACT_ROOT);
  const realFile = await fs.realpath(absolute);
  if (!realFile.startsWith(`${realRoot}${path.sep}`)) {
    throw new SkillError("unsafe_artifact", `${kind} resolved outside the output root`);
  }
  const stat = await fs.stat(realFile);
  if (!stat.isFile() || stat.size > MAX_ARTIFACT_BYTES) {
    throw new SkillError("unsafe_artifact", `${kind} is unavailable or too large`);
  }
  const buffer = await fs.readFile(realFile);
  const actualHash = crypto.createHash("sha256").update(buffer).digest("hex");
  if (!/^[0-9a-f]{64}$/.test(expectedHash) || actualHash !== expectedHash) {
    throw new SkillError("integrity_failure", `${kind} hash does not match`);
  }
  return buffer;
}

function queueNativeInlineOutput(aibitat, imagePayload) {
  if (!aibitat || typeof aibitat.newMessage !== "function") {
    throw new SkillError("artifact_delivery_unavailable", "AnythingLLM inline output hook is unavailable");
  }
  if (!aibitat._aagChessOriginalNewMessage) {
    aibitat._aagChessOriginalNewMessage = aibitat.newMessage.bind(aibitat);
    aibitat.newMessage = function (message) {
      const queued = Array.isArray(aibitat._aagChessQueuedInlineOutputs)
        ? aibitat._aagChessQueuedInlineOutputs
        : [];
      if (message?.from !== "USER" && queued.length) {
        const existing = Array.isArray(message.outputs) ? message.outputs : [];
        message = { ...message, outputs: [...existing, ...queued] };
        aibitat._aagChessQueuedInlineOutputs = [];
      }
      return aibitat._aagChessOriginalNewMessage(message);
    };
  }
  if (!Array.isArray(aibitat._aagChessQueuedInlineOutputs)) {
    aibitat._aagChessQueuedInlineOutputs = [];
  }
  aibitat._aagChessQueuedInlineOutputs.push({
    type: "imageGenerationCard",
    payload: imagePayload
  });
}

function inlineBrowserUrl(storageFilename) {
  if (!/^img-[0-9a-f-]{36}\.png$/i.test(storageFilename || "")) {
    throw new SkillError("unsafe_artifact", "Inline image storage name is invalid");
  }
  return `/api/image-generation/generated-images/${encodeURIComponent(storageFilename)}`;
}

async function primeImageOwnership(aibitat, imagePayload, WorkspaceChats) {
  const invocation = aibitat?.handlerProps?.invocation;
  const chatId = aibitat?.trackedChatId;
  if (!invocation?.workspace_id) {
    throw new SkillError("artifact_delivery_unavailable", "AnythingLLM chat ownership context is unavailable");
  }
  // Developer-API agent turns do not expose a tracked browser chat row. Their
  // generated-file cards are still registered, but there is no browser-owned
  // image URL to prime during this turn.
  if (!Number.isInteger(chatId) || chatId < 1) return false;
  const chat = await WorkspaceChats.get({ id: chatId });
  if (!chat || Number(chat.workspaceId) !== Number(invocation.workspace_id)) {
    throw new SkillError("artifact_delivery_unavailable", "AnythingLLM chat ownership does not match");
  }
  const expectedUser = invocation.user_id ? Number(invocation.user_id) : null;
  const expectedThread = invocation.thread_id ? Number(invocation.thread_id) : null;
  if ((chat.user_id ?? null) !== expectedUser || (chat.thread_id ?? null) !== expectedThread) {
    throw new SkillError("artifact_delivery_unavailable", "AnythingLLM conversation ownership does not match");
  }
  // Developer-API chat rows are intentionally hidden from browser history.
  // A live browser row starts as include=false and is promoted by the normal
  // final chat store, so prime only that non-API row for the imminent image GET.
  if (chat.api_session_id) return false;
  let response = {};
  try {
    response = JSON.parse(chat.response || "{}");
  } catch {
    response = {};
  }
  const outputs = Array.isArray(response.outputs) ? response.outputs : [];
  if (!outputs.some(item => item?.payload?.storageFilename === imagePayload.storageFilename)) {
    outputs.push({ type: "imageGenerationCard", payload: imagePayload });
  }
  const updated = await WorkspaceChats._update(chatId, {
    response: JSON.stringify({ ...response, outputs }),
    include: true
  });
  if (!updated) {
    throw new SkillError("artifact_delivery_unavailable", "AnythingLLM image ownership registration failed");
  }
  return true;
}

async function publishInlinePng(
  context,
  buffer,
  displayFilename,
  filesLib,
  createFilesLib,
  WorkspaceChats
) {
  if (!Buffer.isBuffer(buffer) || !displayFilename.endsWith(".png")) {
    throw new SkillError("unsafe_artifact", "Inline chess image is invalid");
  }
  const imageSaved = await filesLib.saveGeneratedImage({
    buffer,
    prompt: `AAG verified unsolved chess puzzle ${displayFilename}`
  });
  if (!/^img-[0-9a-f-]{36}\.png$/i.test(imageSaved.storageFilename || "")) {
    throw new SkillError("artifact_delivery_unavailable", "AnythingLLM image storage rejected the PNG");
  }
  const imagePayload = {
    storageFilename: imageSaved.storageFilename,
    filename: displayFilename,
    fileSize: imageSaved.fileSize,
    prompt: "Verified unsolved AAG chess puzzle"
  };
  // AnythingLLM's agent final-response path renders registered native outputs.
  // Sending this payload through the legacy file-card socket signature causes
  // the chat renderer to receive a non-text content object and crash.
  createFilesLib.registerOutput(context.super, "imageGenerationCard", imagePayload);
  const browserOwned = await primeImageOwnership(
    context.super,
    imagePayload,
    WorkspaceChats
  );
  queueNativeInlineOutput(context.super, imagePayload);
  return {
    ...imagePayload,
    browserUrl: browserOwned ? inlineBrowserUrl(imagePayload.storageFilename) : null
  };
}

async function publishFiles(context, result, createFilesLib, filesLib, WorkspaceChats) {
  const attachments = [];
  const inlineImages = [];
  const verifiedArtifacts = [];
  for (const artifact of result.artifacts) {
    const extension = path.extname(artifact.filename).slice(1).toLowerCase();
    if (!["png", "svg"].includes(extension)) {
      throw new SkillError("unsafe_artifact", "Unexpected chess artifact format");
    }
    const buffer = await readVerifiedFile(
      artifact.relative_path,
      artifact.sha256,
      "artifact"
    );
    verifiedArtifacts.push({ artifact, extension, buffer });
  }
  const manifestBuffer = await readVerifiedFile(
    result.manifest.relative_path,
    result.manifest.sha256,
    "manifest"
  );

  for (const { artifact, extension, buffer } of verifiedArtifacts) {
    const displayFilename = `chess-${path.basename(artifact.filename)}`;
    const saved = await createFilesLib.saveGeneratedFile({
      fileType: "chess",
      extension,
      buffer,
      displayFilename
    });
    const payload = {
      filename: saved.displayFilename,
      storageFilename: saved.filename,
      fileSize: saved.fileSize
    };
    context.super.socket.send("fileDownloadCard", payload);
    createFilesLib.registerOutput(context.super, "TextFileDownload", payload);
    attachments.push({
      filename: saved.displayFilename,
      storage_filename: saved.filename,
      media_type: artifact.media_type,
      sha256: artifact.sha256,
      delivery: "AnythingLLM fileDownloadCard"
    });
    if (extension === "png") {
      const imagePayload = await publishInlinePng(
        context,
        buffer,
        displayFilename,
        filesLib,
        createFilesLib,
        WorkspaceChats
      );
      inlineImages.push({
        filename: displayFilename,
        storage_filename: imagePayload.storageFilename,
        media_type: "image/png",
        delivery: "AnythingLLM native authenticated inline image card",
        browser_url: imagePayload.browserUrl
      });
    }
  }
  const manifestSaved = await createFilesLib.saveGeneratedFile({
    fileType: "chess",
    extension: "json",
    buffer: manifestBuffer,
    displayFilename: "chess-puzzles-manifest.json"
  });
  const manifestPayload = {
    filename: manifestSaved.displayFilename,
    storageFilename: manifestSaved.filename,
    fileSize: manifestSaved.fileSize
  };
  context.super.socket.send("fileDownloadCard", manifestPayload);
  createFilesLib.registerOutput(context.super, "TextFileDownload", manifestPayload);
  return {
    attachments,
    manifest_attachment: {
      filename: manifestSaved.displayFilename,
      storage_filename: manifestSaved.filename,
      media_type: "application/json",
      sha256: result.manifest.sha256,
      delivery: "AnythingLLM fileDownloadCard"
    },
    inline_images: inlineImages
  };
}

const DIFFICULTY_HE = {
  easy: "קל",
  medium: "בינוני",
  hard: "קשה"
};

function generationMessage(result) {
  const count = result.generated_count;
  const opening = count === 1
    ? `חידת מט ב־${result.mate} מוכנה.`
    : `${count} חידות מט ב־${result.mate} מוכנות.`;
  const side = result.side === "black" ? "השחור נוסע." : "הלבן נוסע.";
  const difficulty = DIFFICULTY_HE[result.measured_difficulty];
  if (!difficulty) {
    throw new SkillError("invalid_bridge_response", "Chess difficulty is invalid");
  }
  return `${opening}\n\n${side}\n\nרמת קושי: ${difficulty}.`;
}

function cleanGenerationResult(result, published) {
  return {
    message_he: generationMessage(result),
    inline_images: published.inline_images.map(image => ({
      alt: "חידת שחמט",
      browser_url: image.browser_url
    })),
    downloads_available: published.attachments.length > 0
  };
}

function cleanFollowupResult(followup) {
  if (typeof followup.answer_he !== "string" || !followup.answer_he.trim()) {
    throw new SkillError("invalid_bridge_response", "Chess follow-up text is invalid");
  }
  return { answer_he: followup.answer_he };
}

module.exports.runtime = {
  handler: async function (args) {
    try {
      const normalized = normalizeArgs(args);
      const scope = trustedScope(this.runtimeArgs || {});
      if (normalized.action !== "generate") {
        const followup = await requestBridge(
          {
            action: normalized.action,
            puzzle_number: normalized.puzzle_number,
            public_id: normalized.public_id,
            _scope: scope
          },
          {
            endpoint: "/v1/followup",
            expectedSchema: FOLLOWUP_RESULT_SCHEMA
          }
        );
        return JSON.stringify(cleanFollowupResult(followup));
      }

      const { action, puzzle_number, public_id, ...generation } = normalized;
      void action;
      void puzzle_number;
      void public_id;
      const result = await requestBridge({ ...generation, _scope: scope });
      const createFilesLib = require(CREATE_FILES_LIB);
      const filesLib = require(FILES_LIB);
      const { WorkspaceChats } = require(WORKSPACE_CHATS_MODEL);
      if (!this.super?.socket?.send) {
        throw new SkillError(
          "artifact_delivery_unavailable",
          "AnythingLLM attachment delivery is unavailable"
        );
      }
      if (typeof filesLib.saveGeneratedImage !== "function") {
        throw new SkillError(
          "artifact_delivery_unavailable",
          "AnythingLLM inline image delivery is unavailable"
        );
      }
      const published = await publishFiles(
        this,
        result,
        createFilesLib,
        filesLib,
        WorkspaceChats
      );
      if (
        typeof result.context_token !== "string" ||
        !/^ctx_[0-9a-f]{64}$/.test(result.context_token)
      ) {
        throw new SkillError("invalid_bridge_response", "Chess context token is invalid");
      }
      const safeResult = cleanGenerationResult(result, published);
      assertNoSolutionLeak(safeResult);
      return JSON.stringify(safeResult);
    } catch (error) {
      const safe =
        error instanceof SkillError
          ? error
          : new SkillError("skill_internal_error", "Chess puzzle generation failed safely");
      this.logger?.(`[AAG-CHESS-SKILL] ${safe.code}: ${safe.message}`);
      return JSON.stringify({
        schema: "aag-anythingllm-chess-error-v1",
        status: "error",
        error: safe.code,
        message: safe.message
      });
    }
  }
};

module.exports._test = {
  SkillError,
  assertNoSolutionLeak,
  cleanFollowupResult,
  cleanGenerationResult,
  inlineBrowserUrl,
  normalizeArgs,
  normalizeFormats,
  generationMessage,
  publishFiles,
  publishInlinePng,
  primeImageOwnership,
  queueNativeInlineOutput,
  readVerifiedFile,
  requestBridge,
  safeRelativePath,
  trustedScope
};
