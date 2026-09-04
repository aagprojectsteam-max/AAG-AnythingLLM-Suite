"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const version = fs.readFileSync(path.join(root, "VERSION"), "utf8").trim();
const revision = "07bd65f80b3d9ba3031ed7afb8786627326bd134";
const sourceUrl = "https://github.com/Mintplex-Labs/anything-llm.git";
const localSource = process.env.AAG_ANYTHINGLLM_SOURCE_DIR
  ? path.resolve(process.env.AAG_ANYTHINGLLM_SOURCE_DIR)
  : null;
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "aag-anythingllm-build-"));
const checkout = path.join(temporary, "source");
const output = path.join(root, "releases", "staged", version, "anythingllm-public");

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, stdio: "inherit", env: process.env });
  if (result.status !== 0) throw new Error(`${command} failed with status ${result.status}`);
}

function copy(source, destination) {
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
}

function replaceOnce(source, expected, replacement, label) {
  const first = source.indexOf(expected);
  if (first < 0 || source.indexOf(expected, first + expected.length) >= 0) {
    throw new Error(`AnythingLLM ${label} patch anchor is missing or ambiguous`);
  }
  return source.slice(0, first) + replacement + source.slice(first + expected.length);
}

function patchPromptInput(checkoutRoot) {
  const promptPath = path.join(
    checkoutRoot,
    "frontend",
    "src",
    "components",
    "WorkspaceChat",
    "ChatContainer",
    "PromptInput",
    "index.jsx"
  );
  let source = fs.readFileSync(promptPath, "utf8");
  source = replaceOnce(
    source,
    'import { useIsAgentSessionActive } from "@/utils/chat/agent";',
    'import { useIsAgentSessionActive } from "@/utils/chat/agent";\nimport AagImageComposerPanel from "./AagImageComposerPanel";\nimport AagImageProgress from "./AagImageProgress";',
    "PromptInput import"
  );
  source = replaceOnce(
    source,
    "  const formRef = useRef(null);\n  const textareaRef = useRef(null);",
    "  const formRef = useRef(null);\n  const textareaRef = useRef(null);\n  const composerPanelRef = useRef(null);\n  const [composerMode, setComposerMode] = useState(\"auto\");\n  const isImageGenerator = (workspace?.slug || workspaceSlug) === \"image-generator\";",
    "PromptInput state"
  );
  source = replaceOnce(
    source,
    "  threadSlug = null,\n}) {",
    "  threadSlug = null,\n  history = [],\n}) {",
    "PromptInput structured history prop"
  );
  source = replaceOnce(
    source,
    "  function handleSubmit(e) {\n    // Ignore submits from portaled modals (slash command preset forms)\n    if (e.target !== e.currentTarget) return;\n    setFocused(false);\n    setShowTools(false);\n    submit(e);\n  }",
    "  async function handleSubmit(e) {\n    // Ignore submits from portaled modals (slash command preset forms)\n    if (e.target !== e.currentTarget) return;\n    setFocused(false);\n    setShowTools(false);\n    if (isImageGenerator && composerMode === \"advanced\") {\n      e.preventDefault();\n      const prepared = await composerPanelRef.current?.prepare();\n      if (!prepared) return;\n      return submit(e, prepared);\n    }\n    return submit(e);\n  }",
    "PromptInput submit"
  );
  source = replaceOnce(
    source,
    "      setShowTools(false);\n      return submit(event);",
    "      setShowTools(false);\n      return handleSubmit(event);",
    "PromptInput enter"
  );
  source = replaceOnce(
    source,
    '          <div className="relative w-[95vw] md:w-[750px]">\n            <ToolsMenu',
    '          <div className="relative w-[95vw] md:w-[750px]">\n            {isImageGenerator && (\n              <AagImageProgress\n                workspaceSlug={workspace?.slug || workspaceSlug}\n                threadSlug={threadSlug}\n              />\n            )}\n            {isImageGenerator && (\n              <AagImageComposerPanel\n                ref={composerPanelRef}\n                mode={composerMode}\n                onModeChange={setComposerMode}\n                prompt={promptInput}\n                history={history}\n                threadSlug={threadSlug}\n                disabled={isStreaming || isDisabled}\n              />\n            )}\n            <ToolsMenu',
    "PromptInput render"
  );
  fs.writeFileSync(promptPath, source);
}

function patchChatContainer(checkoutRoot) {
  const chatPath = path.join(
    checkoutRoot,
    "frontend",
    "src",
    "components",
    "WorkspaceChat",
    "ChatContainer",
    "index.jsx"
  );
  let source = fs.readFileSync(chatPath, "utf8");
  source = replaceOnce(
    source,
    'import MemoriesSidebar from "./MemoriesSidebar";',
    'import MemoriesSidebar from "./MemoriesSidebar";\n\n// Binary attachments cannot be serialized through sessionStorage: Brave\n// enforces a quota below the encoded size of a valid Composer reference. Keep\n// the one-navigation handoff in page memory; the native chat request then\n// places it in AnythingLLM\'s private per-invocation attachment cache.\nlet aagPendingNativeSubmission = null;',
    "ChatContainer bounded pending handoff state"
  );
  source = replaceOnce(
    source,
    "  const handleSubmit = async (event) => {\n    event.preventDefault();\n    const currentMessage =\n      document.getElementById(PROMPT_INPUT_ID)?.value || \"\";\n    if (!currentMessage) return false;",
    "  const handleSubmit = async (event, submission = {}) => {\n    event.preventDefault();\n    const currentMessage =\n      document.getElementById(PROMPT_INPUT_ID)?.value || \"\";\n    if (!currentMessage) return false;\n    const modelMessage =\n      typeof submission?.modelMessage === \"string\" && submission.modelMessage\n        ? submission.modelMessage\n        : currentMessage;\n    const composerAttachments = Array.isArray(submission?.composerAttachments)\n      ? submission.composerAttachments\n      : [];\n    const outgoingAttachments = [\n      ...composerAttachments,\n      ...parseAttachments(),\n    ];",
    "ChatContainer native submit augmentation"
  );
  source = replaceOnce(
    source,
    "        sessionStorage.setItem(\n          PENDING_HOME_MESSAGE,\n          JSON.stringify({\n            message: currentMessage,\n            attachments: parseAttachments(),\n          })\n        );",
    "        aagPendingNativeSubmission = {\n          message: currentMessage,\n          modelMessage,\n          attachments: outgoingAttachments,\n          hasInMemoryAttachments: outgoingAttachments.length > 0,\n        };\n        sessionStorage.setItem(\n          PENDING_HOME_MESSAGE,\n          JSON.stringify({\n            message: currentMessage,\n            modelMessage,\n            hasInMemoryAttachments: outgoingAttachments.length > 0,\n          })\n        );",
    "ChatContainer bounded native pending thread handoff"
  );
  source = replaceOnce(
    source,
    "        content: currentMessage,\n        role: \"user\",\n        attachments: parseAttachments(),\n      },\n      {\n        content: \"\",\n        role: \"assistant\",\n        pending: true,\n        userMessage: currentMessage,",
    "        content: currentMessage,\n        role: \"user\",\n        attachments: outgoingAttachments,\n      },\n      {\n        content: \"\",\n        role: \"assistant\",\n        pending: true,\n        userMessage: modelMessage,\n        attachments: outgoingAttachments,",
    "ChatContainer visible and model message split"
  );
  source = replaceOnce(
    source,
    "   * @param {Object[]} options.history - The history of the chat prior to this message for overriding the current chat history",
    "   * @param {string|null} options.modelMessage - private execution text while the visible user text remains options.text\n   * @param {Object[]} options.history - The history of the chat prior to this message for overriding the current chat history",
    "ChatContainer sendCommand documentation"
  );
  source = replaceOnce(
    source,
    "    text = \"\",\n    autoSubmit = false,",
    "    text = \"\",\n    modelMessage = null,\n    autoSubmit = false,",
    "ChatContainer sendCommand model message"
  );
  source = replaceOnce(
    source,
    "    if (!text || text === \"\") return false;\n\n    // If on a bare workspace route",
    "    if (!text || text === \"\") return false;\n    const executionMessage =\n      typeof modelMessage === \"string\" && modelMessage ? modelMessage : text;\n\n    // If on a bare workspace route",
    "ChatContainer execution message derivation"
  );
  source = replaceOnce(
    source,
    "        sessionStorage.setItem(\n          PENDING_HOME_MESSAGE,\n          JSON.stringify({ message: text, attachments })\n        );",
    "        aagPendingNativeSubmission = {\n          message: text,\n          modelMessage: executionMessage,\n          attachments,\n          hasInMemoryAttachments: attachments.length > 0,\n        };\n        sessionStorage.setItem(\n          PENDING_HOME_MESSAGE,\n          JSON.stringify({\n            message: text,\n            modelMessage: executionMessage,\n            hasInMemoryAttachments: attachments.length > 0,\n          })\n        );",
    "ChatContainer bounded sendCommand pending thread handoff"
  );
  const sendCommandUserMessageAnchor = "          userMessage: text,";
  if (source.split(sendCommandUserMessageAnchor).length - 1 !== 2)
    throw new Error("AnythingLLM ChatContainer sendCommand message anchors are missing or ambiguous");
  source = source.replaceAll(
    sendCommandUserMessageAnchor,
    "          userMessage: executionMessage,"
  );
  source = replaceOnce(
    source,
    "    const pending = safeJsonParse(sessionStorage.getItem(PENDING_HOME_MESSAGE));\n    if (pending?.message) {\n      setTimeout(() => {\n        sessionStorage.removeItem(PENDING_HOME_MESSAGE);\n        sendCommand({\n          text: pending.message,\n          attachments: pending.attachments || [],\n          autoSubmit: true,\n        });\n      }, 100);\n    }",
    "    const memoryPending = aagPendingNativeSubmission;\n    const pending =\n      memoryPending ||\n      safeJsonParse(sessionStorage.getItem(PENDING_HOME_MESSAGE));\n    aagPendingNativeSubmission = null;\n    if (pending?.message) {\n      setTimeout(() => {\n        sessionStorage.removeItem(PENDING_HOME_MESSAGE);\n        if (pending.hasInMemoryAttachments && !memoryPending) {\n          // A full reload cannot recover browser File objects safely. Restore\n          // the exact visible text without submitting a source-less request.\n          sendCommand({\n            text: pending.message,\n            modelMessage: pending.modelMessage || pending.message,\n            autoSubmit: false,\n          });\n          return;\n        }\n        sendCommand({\n          text: pending.message,\n          modelMessage: pending.modelMessage || pending.message,\n          attachments: pending.attachments || [],\n          autoSubmit: true,\n        });\n      }, 100);\n    }",
    "ChatContainer bounded pending message restore"
  );
  source = replaceOnce(
    source,
    "                    attachments={files}\n                    centered={true}",
    "                    attachments={files}\n                    history={chatHistory}\n                    threadSlug={activeThreadSlug}\n                    centered={true}",
    "ChatContainer empty-state Composer history"
  );
  source = replaceOnce(
    source,
    "                  attachments={files}\n                  centered={false}",
    "                  attachments={files}\n                  history={chatHistory}\n                  threadSlug={activeThreadSlug}\n                  centered={false}",
    "ChatContainer thread Composer history"
  );
  fs.writeFileSync(chatPath, source);
}

function treeHash(directory) {
  const files = [];
  function walk(current) {
    for (const entry of fs.readdirSync(current, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) walk(absolute);
      else if (entry.isFile()) files.push(path.relative(directory, absolute));
    }
  }
  walk(directory);
  const digest = crypto.createHash("sha256");
  for (const relative of files) {
    digest.update(relative);
    digest.update("\0");
    digest.update(fs.readFileSync(path.join(directory, relative)));
    digest.update("\0");
  }
  return { sha256: digest.digest("hex"), files: files.length };
}

try {
  // The frontend overlay never reads server, collector, or documentation
  // sources. An explicitly supplied local source may avoid a network fetch,
  // but only when it is the exact pinned revision and its frontend is clean.
  if (localSource) {
    const localRevision = spawnSync("git", ["rev-parse", "HEAD"], {
      cwd: localSource,
      encoding: "utf8",
    });
    const localStatus = spawnSync(
      "git",
      ["status", "--porcelain", "--untracked-files=no", "--", "frontend"],
      { cwd: localSource, encoding: "utf8" }
    );
    if (
      localRevision.status !== 0 ||
      localRevision.stdout.trim() !== revision ||
      localStatus.status !== 0 ||
      localStatus.stdout.trim()
    ) {
      throw new Error("AnythingLLM local frontend source is not the clean pinned revision");
    }
    fs.mkdirSync(checkout, { recursive: true });
    fs.cpSync(path.join(localSource, "frontend"), path.join(checkout, "frontend"), {
      recursive: true,
      force: true,
    });
  } else {
    // Fetch only the pinned frontend tree so transient network drops do not
    // require downloading unrelated repository blobs before every rebuild.
    fs.mkdirSync(checkout, { recursive: true });
    run("git", ["init", "--quiet"], checkout);
    run("git", ["remote", "add", "origin", sourceUrl], checkout);
    run("git", ["sparse-checkout", "init", "--cone"], checkout);
    run("git", ["sparse-checkout", "set", "frontend"], checkout);
    run("git", ["fetch", "--depth=1", "--filter=blob:none", "origin", revision], checkout);
    run("git", ["checkout", "--detach", "FETCH_HEAD"], checkout);
  }
  const resolved = localSource
    ? spawnSync("git", ["rev-parse", "HEAD"], { cwd: localSource, encoding: "utf8" })
    : spawnSync("git", ["rev-parse", "HEAD"], { cwd: checkout, encoding: "utf8" });
  if (resolved.status !== 0 || resolved.stdout.trim() !== revision) throw new Error("AnythingLLM source revision mismatch");

  const overlay = path.join(root, "integrations", "anythingllm", "frontend");
  copy(path.join(overlay, "ImageGenerationCard", "index.jsx"), path.join(checkout, "frontend", "src", "components", "WorkspaceChat", "ChatContainer", "ChatHistory", "ImageGenerationCard", "index.jsx"));
  copy(path.join(overlay, "HistoricalOutputs", "index.jsx"), path.join(checkout, "frontend", "src", "components", "WorkspaceChat", "ChatContainer", "ChatHistory", "HistoricalMessage", "HistoricalOutputs", "index.jsx"));
  copy(path.join(overlay, "AagImageCollection.jsx"), path.join(checkout, "frontend", "src", "components", "WorkspaceChat", "ChatContainer", "ChatHistory", "AagImageCollection", "index.jsx"));
  copy(path.join(overlay, "aagArtifactExport.js"), path.join(checkout, "frontend", "src", "utils", "aagArtifactExport.js"));
  fs.cpSync(path.join(overlay, "AagImageComposerPanel"), path.join(checkout, "frontend", "src", "components", "WorkspaceChat", "ChatContainer", "PromptInput", "AagImageComposerPanel"), { recursive: true, force: true });
  fs.cpSync(path.join(overlay, "AagImageProgress"), path.join(checkout, "frontend", "src", "components", "WorkspaceChat", "ChatContainer", "PromptInput", "AagImageProgress"), { recursive: true, force: true });
  patchPromptInput(checkout);
  patchChatContainer(checkout);

  run("corepack", ["yarn", "install", "--frozen-lockfile", "--network-timeout", "100000"], path.join(checkout, "frontend"));
  run("corepack", ["yarn", "build"], path.join(checkout, "frontend"));
  fs.rmSync(output, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.cpSync(path.join(checkout, "frontend", "dist"), output, { recursive: true, force: true });
  const built = treeHash(output);
  const provenance = {
    schema: "aag.anythingllm-frontend-build.v1",
    aagRelease: version,
    anythingllmSource: sourceUrl,
    anythingllmRevision: revision,
    overlayFiles: [
      "ImageGenerationCard/index.jsx",
      "HistoricalOutputs/index.jsx",
      "AagImageCollection.jsx",
      "aagArtifactExport.js",
      "AagImageComposerPanel/index.jsx",
      "AagImageComposerPanel/styles.css",
      "AagImageComposerPanel/localization.js",
      "AagImageComposerPanel/heTaxonomyLabels.js",
      "AagImageProgress/index.jsx",
      "AagImageProgress/styles.css",
      "PromptInput/index.jsx (deterministic inline patch)",
      "ChatContainer/index.jsx (deterministic native-message augmentation patch)"
    ],
    publicFiles: built.files,
    publicTreeSha256: built.sha256,
    builtAt: new Date().toISOString(),
  };
  fs.writeFileSync(path.join(output, "AAG-BUILD-PROVENANCE.json"), `${JSON.stringify(provenance, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify(provenance, null, 2));
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
