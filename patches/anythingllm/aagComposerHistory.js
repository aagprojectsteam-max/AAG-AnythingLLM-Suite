"use strict";

const crypto = require("crypto");

const INTENT_PREFIX = "AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1=";
const USER_PREFIX = "USER_CREATIVE_DIRECTION=\n";
const SIGNATURE_PREFIX = "\nAAG_COMPOSER_INTENT_SIGNATURE_V1=";
const HISTORY_PATCH = Symbol.for("aag.composer.visible-history.v1");

function assertValidUnicode(value) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff))
        throw new TypeError("Composer JSON contains invalid Unicode");
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new TypeError("Composer JSON contains invalid Unicode");
    }
  }
}

/** RFC 8785/JCS serialization for the signed cross-runtime Composer value. */
function composerCanonicalJson(value) {
  if (value === null) return "null";
  if (typeof value === "string") {
    assertValidUnicode(value);
    return JSON.stringify(value);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value))
      throw new TypeError("Composer JSON contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value))
    return `[${value.map((item) => composerCanonicalJson(item)).join(",")}]`;
  if (value && typeof value === "object") {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null)
      throw new TypeError("Composer JSON contains an unsupported object");
    return `{${Object.keys(value)
      .sort()
      .map((key) => {
        assertValidUnicode(key);
        return `${JSON.stringify(key)}:${composerCanonicalJson(value[key])}`;
      })
      .join(",")}}`;
  }
  throw new TypeError(`Unsupported Composer JSON type: ${typeof value}`);
}

function inspectComposerPrompt(prompt) {
  if (typeof prompt !== "string")
    return { kind: "ordinary", visiblePrompt: prompt };
  const hasReservedMarker = [INTENT_PREFIX, USER_PREFIX, SIGNATURE_PREFIX].some(
    (marker) => prompt.includes(marker)
  );
  if (!hasReservedMarker) return { kind: "ordinary", visiblePrompt: prompt };

  const invalid = () => ({ kind: "invalid", visiblePrompt: "" });
  const intentAt = prompt.indexOf(INTENT_PREFIX);
  const userAt = prompt.indexOf(USER_PREFIX);
  const signatureAt = prompt.lastIndexOf(SIGNATURE_PREFIX);
  if (
    intentAt < 0 ||
    userAt < 0 ||
    signatureAt < userAt ||
    prompt.indexOf(INTENT_PREFIX, intentAt + INTENT_PREFIX.length) !== -1 ||
    prompt.indexOf(USER_PREFIX, userAt + USER_PREFIX.length) !== -1 ||
    prompt.indexOf(SIGNATURE_PREFIX) !== signatureAt
  )
    return invalid();

  const intentEnd = prompt.indexOf("\n", intentAt);
  if (intentEnd < 0 || intentEnd >= userAt) return invalid();
  const rawIntent = prompt.slice(intentAt + INTENT_PREFIX.length, intentEnd);
  const signature = prompt.slice(signatureAt + SIGNATURE_PREFIX.length);
  if (!/^[0-9a-f]{64}$/.test(signature)) return invalid();

  try {
    const intent = JSON.parse(rawIntent);
    if (composerCanonicalJson(intent) !== rawIntent) return invalid();
    const userRequest = prompt.slice(userAt + USER_PREFIX.length, signatureAt);
    const requestHash = crypto
      .createHash("sha256")
      .update(userRequest, "utf8")
      .digest("hex");
    if (intent?.user_request_sha256 !== requestHash) return invalid();
    return { kind: "valid", visiblePrompt: userRequest, intent };
  } catch {
    return invalid();
  }
}

/**
 * Return the exact native user text for a structurally valid signed Composer
 * envelope. HMAC authorization remains exclusively in the compatibility
 * boundary. Reserved but invalid envelopes return empty text, never hidden
 * envelope content.
 */
function visibleComposerPrompt(prompt) {
  return inspectComposerPrompt(prompt).visiblePrompt;
}

/**
 * Return trusted runtime request text or stop before a governed tool can load.
 * A malformed reserved envelope is never promoted to authoritative user text.
 */
function composerInvocationPrompt(prompt) {
  const inspection = inspectComposerPrompt(prompt);
  if (inspection.kind === "invalid") {
    const error = new Error("Signed Composer envelope validation failed.");
    error.code = "AAG_COMPOSER_ENVELOPE_INVALID";
    throw error;
  }
  return inspection.visiblePrompt;
}

/**
 * Composer reference images are private invocation material for governed AAG
 * image tools. The signed envelope gives the language model the reference
 * semantics; sending the same binary as vision content is redundant and can
 * exceed bounded OpenAI-compatible request limits.
 *
 * This is deliberately structural, not an authorization decision. The model-
 * neutral boundary remains the sole HMAC validator and rejects forged intent.
 */
function composerModelAttachments(prompt, attachments = []) {
  const normalized = Array.isArray(attachments) ? attachments : [];
  return visibleComposerPrompt(prompt) === prompt ? normalized : [];
}

/**
 * Remove Composer reference binaries from the provider-facing history copy
 * without mutating AIbitat's native message objects. The native objects retain
 * their attachments for normal AnythingLLM persistence and visible history.
 */
function composerModelHistory(messages = []) {
  if (!Array.isArray(messages)) return [];
  return messages.map((message) => {
    if (
      message?.role !== "user" ||
      visibleComposerPrompt(message?.content) === message?.content ||
      !Object.prototype.hasOwnProperty.call(message, "attachments")
    )
      return message;
    const { attachments: _privateReference, ...modelMessage } = message;
    return modelMessage;
  });
}

/**
 * Keep the server-held current-turn attachments available only to governed
 * imported tools after they have been withheld from the model-visible turn.
 */
function composerRuntimeAttachments(
  prompt,
  invocationAttachments = [],
  messageAttachments = [],
  { allowInvocationFallback = true } = {}
) {
  const privateAttachments = Array.isArray(invocationAttachments)
    ? invocationAttachments
    : [];
  const visibleAttachments = Array.isArray(messageAttachments)
    ? messageAttachments
    : [];
  if (visibleComposerPrompt(prompt) === prompt) return visibleAttachments;
  if (visibleAttachments.length > 0) return visibleAttachments;
  return allowInvocationFallback ? privateAttachments : [];
}

/** Keep AnythingLLM's standard history plugin; only adapt its prompt value. */
function withComposerVisibleHistory(plugin) {
  if (!plugin || typeof plugin !== "object")
    throw new TypeError("AnythingLLM chat-history plugin is unavailable");
  for (const method of ["_store", "_storeSpecial"]) {
    const original = plugin[method];
    if (typeof original !== "function")
      throw new TypeError(`AnythingLLM chat-history ${method} hook is unavailable`);
    plugin[method] = async function (aibitat, values = {}) {
      return original.call(this, aibitat, {
        ...values,
        prompt: visibleComposerPrompt(values.prompt),
      });
    };
  }
  return plugin;
}

/**
 * Normalize every standard AnythingLLM persistence boundary. This covers the
 * initial pending row and final direct-tool output as well as chat-history
 * plugin storage. Ordinary messages pass through unchanged; malformed
 * reserved envelopes fail closed without persisting hidden content.
 */
function installComposerHistoryPersistence({ WorkspaceChats, Workspace = null }) {
  if (!WorkspaceChats || typeof WorkspaceChats !== "object")
    throw new TypeError("AnythingLLM WorkspaceChats model is unavailable");
  if (WorkspaceChats[HISTORY_PATCH]) return WorkspaceChats;

  for (const [method, valuesIndex] of [
    ["new", 0],
    ["upsert", 1],
  ]) {
    const original = WorkspaceChats[method];
    if (typeof original !== "function")
      throw new TypeError(`AnythingLLM WorkspaceChats.${method} is unavailable`);
    WorkspaceChats[method] = async function (...args) {
      const values = args[valuesIndex];
      if (
        values &&
        typeof values === "object" &&
        !Array.isArray(values) &&
        Object.prototype.hasOwnProperty.call(values, "prompt")
      ) {
        const visiblePrompt = visibleComposerPrompt(values.prompt);
        const isComposerEnvelope = visiblePrompt !== values.prompt;
        let isImageWorkspacePending = false;
        if (
          method === "new" &&
          values.include === false &&
          values.threadId != null &&
          values.response &&
          typeof values.response === "object" &&
          !Array.isArray(values.response) &&
          Object.keys(values.response).length === 0 &&
          Workspace &&
          typeof Workspace.get === "function"
        ) {
          try {
            const workspace = await Workspace.get({ id: Number(values.workspaceId) });
            isImageWorkspacePending = workspace?.slug === "image-generator";
          } catch {
            // Preserve stock AnythingLLM behavior when workspace resolution is
            // unavailable. The reconciler never broadens scope on uncertainty.
          }
        }
        args[valuesIndex] = {
          ...values,
          prompt: visiblePrompt,
          ...(method === "new" && (isComposerEnvelope || isImageWorkspacePending)
            ? {
                // The native user turn must be durable before any long-running
                // model or image work. Final recovery replaces this marker
                // atomically on the same chat row.
                include: true,
                response:
                  values.response && Object.keys(values.response).length > 0
                    ? values.response
                    : {
                        text: "",
                        sources: [],
                        type: "chat",
                        attachments: [],
                        outputs: [],
                        aagImagePending: true,
                      },
              }
            : {}),
        };
      }
      return original.apply(this, args);
    };
  }

  Object.defineProperty(WorkspaceChats, HISTORY_PATCH, {
    value: true,
    enumerable: false,
  });
  return WorkspaceChats;
}

/** Persist a failed native agent turn once, using the same tracked chat row. */
function installComposerFailurePersistence({ aibitat, WorkspaceChats }) {
  if (!aibitat?.onError || !WorkspaceChats?.upsert) return;
  aibitat.onError(async (error) => {
    try {
      const chatId = aibitat.trackedChatId;
      if (!chatId || aibitat._aagComposerFailureStored === chatId) return;
      const latestUser = (aibitat._chats || []).findLast(
        (message) =>
          typeof message?.content === "string" &&
          visibleComposerPrompt(message.content) !== message.content
      );
      const visiblePrompt = visibleComposerPrompt(latestUser?.content);
      if (visiblePrompt === latestUser?.content) return;
      aibitat._aagComposerFailureStored = chatId;
      const invocation = aibitat.handlerProps?.invocation;
      await WorkspaceChats.upsert(chatId, {
        workspaceId: Number(invocation.workspace_id),
        prompt: visiblePrompt,
        response: {
          text: error instanceof Error ? error.message : String(error),
          sources: [],
          type: "chat",
          // This is native AnythingLLM history, not provider input. Preserve
          // the exact current-turn attachment so it remains visible after a
          // failed governed turn and a page reload. Provider isolation is
          // applied only by composerModelHistory at the LLM boundary.
          attachments: Array.isArray(latestUser?.attachments)
            ? latestUser.attachments
            : [],
        },
        user: { id: invocation?.user_id || null },
        threadId: invocation?.thread_id || null,
        include: true,
      });
    } catch (persistenceError) {
      aibitat.handlerProps?.log?.(
        "Composer failure history persistence failed",
        persistenceError.message
      );
    }
  });
}

module.exports = {
  composerCanonicalJson,
  composerInvocationPrompt,
  composerModelAttachments,
  composerModelHistory,
  composerRuntimeAttachments,
  installComposerFailurePersistence,
  installComposerHistoryPersistence,
  visibleComposerPrompt,
  withComposerVisibleHistory,
};
