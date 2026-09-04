#!/usr/bin/env node
"use strict";

/** Reconstructs the exact first native-tool provider payload for a fresh thread. */
const fs = require("fs");
const AIbitat = require("/app/server/utils/agents/aibitat");
const AgentPlugins = require("/app/server/utils/agents/aibitat/plugins");
const ImportedPlugin = require("/app/server/utils/agents/imported");
const { WORKSPACE_AGENT } = require("/app/server/utils/agents/defaults");
const { Workspace } = require("/app/server/models/workspace");
const { WorkspaceParsedFiles } = require("/app/server/models/workspaceParsedFiles");
const { DocumentManager } = require("/app/server/utils/DocumentManager");
const {
  SystemPromptVariables,
} = require("/app/server/models/systemPromptVariables");
const { promptWithMemories } = require("/app/server/utils/memories");
const {
  withPublicToolSchema,
} = require("/app/server/storage/aag-image-agent-integration/runtime-context-bridge/aagPublicToolSchema.js");
const {
  ToolReranker,
} = require("/app/server/utils/agents/aibitat/utils/toolReranker");

const BEFORE_TOP10 = [
  "aag-chess-puzzle",
  "aag-context-memory-v1",
  "filesystem-search-files",
  "create-text-file",
  "aag-governed-orchestration-v1",
  "create-pptx-presentation",
  "filesystem-read-multiple-files",
  "create-excel-file",
  "filesystem-read-text-file",
  "create-pdf-file",
];

function registerFunction(aibitat, identifier, workspaceId, prompt) {
  if (identifier.includes("#")) {
    const [parent, childName] = identifier.split("#");
    const child = AgentPlugins[parent]?.plugin?.find(
      (candidate) => candidate.name === childName
    );
    if (!child) throw new Error(`Missing child: ${identifier}`);
    aibitat.use(child.plugin({}));
    return childName;
  }
  if (identifier.startsWith("@@")) {
    const hubId = identifier.slice(2);
    const imported = ImportedPlugin.loadPluginByHubId(hubId);
    if (!imported) throw new Error(`Missing imported plugin: ${hubId}`);
    aibitat.use(
      withPublicToolSchema(
        imported,
        imported.plugin({
          AAG_WORKSPACE_ID: workspaceId,
          AAG_THREAD_ID: 0,
          AAG_USER_ID: null,
          AAG_INVOCATION_UUID: "routing-input-capture",
          AAG_TURN_ID: "routing-input-capture",
          AAG_INVOCATION_PROMPT: prompt,
          AAG_INVOCATION_ATTACHMENTS: [],
        })
      )
    );
    return hubId;
  }
  const plugin = AgentPlugins[identifier];
  if (!plugin) throw new Error(`Missing plugin: ${identifier}`);
  aibitat.use(plugin.plugin({}));
  return plugin.name;
}

function providerTool(tool) {
  return {
    type: "function",
    function: {
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
    },
  };
}

async function parsedFileContext(workspace) {
  const manager = new DocumentManager({ workspace });
  const [parsedFiles, pinnedDocs] = await Promise.all([
    WorkspaceParsedFiles.getContextFiles(workspace, null, null),
    manager.pinnedDocs(),
  ]);
  const all = [
    ...(parsedFiles || []).map((document) => ({
      title: document.name,
      content: document.content,
    })),
    ...(pinnedDocs || []).map((document) => ({
      title: document.title || document.docpath || "Pinned document",
      content: document.pageContent || document.content || "",
    })),
  ].filter((document) => document.content);
  if (!all.length) return "";
  return `\n\n<document_context>\n${all
    .map((document) => `<document title="${document.title}">\n${document.content}\n</document>`)
    .join("\n")}\n</document_context>`;
}

async function main() {
  const output = process.argv[2];
  const prompt =
    process.argv[3] ||
    "תעשה לי תמונה איכותית של קוף משחק שחמט עם פיל";
  if (!output) throw new Error("Output path is required.");
  const workspace = await Workspace.get({ slug: "image-generator" });
  const definition = await WORKSPACE_AGENT.getDefinition(
    workspace.chatProvider,
    workspace,
    null,
    prompt
  );
  const expandedWorkspacePrompt =
    await SystemPromptVariables.expandSystemPromptVariables(
      workspace.openAiPrompt,
      null,
      workspace.id
    );
  const memoryExpandedPrompt = await promptWithMemories({
    systemPrompt: expandedWorkspacePrompt,
    userId: null,
    workspaceId: workspace.id,
    prompt,
  });
  const documentContext = await parsedFileContext(workspace);
  const actualUserContent = `${prompt}${documentContext}`;
  const aibitat = new AIbitat({ handlerProps: { log() {} } });
  const registered = [];
  for (const identifier of definition.functions) {
    const name = registerFunction(aibitat, identifier, workspace.id, prompt);
    const tool = aibitat.functions.get(name);
    if (!tool) throw new Error(`Did not register ${identifier}`);
    registered.push(tool);
  }
  const selectedAfter = await new ToolReranker().rerank(
    actualUserContent,
    registered,
    { topN: 10 }
  );
  const byName = new Map(registered.map((tool) => [tool.name, tool]));
  const selectedBefore = BEFORE_TOP10.map((name) => byName.get(name));
  if (selectedBefore.some((tool) => !tool))
    throw new Error("A recorded pre-change tool is no longer registered.");

  const messages = [
    { role: "system", content: definition.role },
    { role: "user", content: actualUserContent },
  ];
  const makePayload = (tools) => ({
    model: workspace.chatModel,
    stream: true,
    stream_options: { include_usage: true },
    messages,
    tools: tools.map(providerTool),
  });
  const result = {
    schema: "aag.anythingllm.model-input-capture.v1",
    capturedAt: new Date().toISOString(),
    captureMethod:
      "Exact reconstruction of the first fresh-thread tooledStream payload using live WORKSPACE_AGENT, registered functions, and selector output.",
    workspace: {
      id: workspace.id,
      slug: workspace.slug,
      chatProvider: workspace.chatProvider,
      chatModel: workspace.chatModel,
    },
    components: {
      baseSystemAgent: "",
      workspacePrompt: expandedWorkspacePrompt,
      memoryContext:
        memoryExpandedPrompt.startsWith(expandedWorkspacePrompt)
          ? memoryExpandedPrompt.slice(expandedWorkspacePrompt.length)
          : memoryExpandedPrompt,
      agentInstructions:
        definition.role.startsWith(memoryExpandedPrompt)
          ? definition.role.slice(memoryExpandedPrompt.length)
          : "",
      threadHistory: [],
      currentUserMessage: prompt,
      ragContext: documentContext,
      attachmentContext: [],
    },
    before: {
      selectedToolNames: selectedBefore.map((tool) => tool.name),
      payload: makePayload(selectedBefore),
    },
    after: {
      selectedToolNames: selectedAfter.map((tool) => tool.name),
      payload: makePayload(selectedAfter),
    },
  };
  fs.writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`, {
    mode: 0o600,
  });
  console.log(
    JSON.stringify({
      output,
      before: result.before.selectedToolNames,
      after: result.after.selectedToolNames,
      roleCharacters: definition.role.length,
      documentContextCharacters: documentContext.length,
    })
  );
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
