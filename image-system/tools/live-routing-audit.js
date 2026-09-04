#!/usr/bin/env node
"use strict";

/**
 * Read-only live AnythingLLM tool-registration and reranker-document audit.
 * Run this script inside the AnythingLLM container, where /app/server exists.
 */

const fs = require("fs");
const AIbitat = require("/app/server/utils/agents/aibitat");
const AgentPlugins = require("/app/server/utils/agents/aibitat/plugins");
const ImportedPlugin = require("/app/server/utils/agents/imported");
const { WORKSPACE_AGENT } = require("/app/server/utils/agents/defaults");
const { Workspace } = require("/app/server/models/workspace");
const {
  withPublicToolSchema,
} = require("/app/server/storage/aag-image-agent-integration/runtime-context-bridge/aagPublicToolSchema.js");
const { TokenManager } = require("/app/server/utils/helpers/tiktoken");
const {
  ToolReranker,
} = require("/app/server/utils/agents/aibitat/utils/toolReranker");

const MAX_TEXT_LENGTH = 1000;
const ROUTING_METADATA_PATH =
  "/app/server/storage/aag-image-agent-integration/routing-correction/tool-routing-metadata.json";

function truncateText(text, maxLength = MAX_TEXT_LENGTH) {
  if (!text || text.length <= maxLength) return text;
  const truncated = text.slice(0, maxLength);
  const lastSpace = truncated.lastIndexOf(" ");
  return lastSpace > maxLength * 0.8
    ? truncated.slice(0, lastSpace)
    : truncated;
}

function routingDocument(tool, profile) {
  if (!profile) return null;
  return [
    `Primary action: ${profile.action}.`,
    `Domain: ${profile.domain}.`,
    `Specific operation: ${profile.operation}.`,
    Array.isArray(profile.aliases) && profile.aliases.length
      ? `Multilingual intent aliases: ${profile.aliases.join("; ")}.`
      : "",
    profile.excludes
      ? `Scope boundary: Do not use for ${profile.excludes}.`
      : "",
    `Registered function: ${tool.name}.`,
  ]
    .filter(Boolean)
    .join(" ");
}

function toolToDocument(tool, tokenManager, routingMetadata) {
  const parts = [tool.name];
  if (tool.description) parts.push(tool.description);
  if (tool.parameters?.properties) {
    const descriptions = Object.entries(tool.parameters.properties).map(
      ([name, property]) =>
        property.description ? `${name}: ${property.description}` : name
    );
    if (descriptions.length) parts.push(descriptions.join(", "));
  }
  const examples = Array.isArray(tool.examples)
    ? tool.examples.map((example) => example?.prompt).filter(Boolean)
    : [];
  if (examples.length) parts.push(examples.join("; "));
  const raw = parts.join("\n");
  const profile = tool.config?.routing || routingMetadata[tool.name] || null;
  const rankingSource = routingDocument(tool, profile) || raw;
  return {
    raw,
    legacyRanked: truncateText(raw),
    ranked: truncateText(rankingSource),
    rawCharacters: raw.length,
    legacyRankedCharacters: truncateText(raw).length,
    rankedCharacters: truncateText(rankingSource).length,
    schemaTokens: tokenManager.countFromString(raw),
  };
}

function registerFunction(aibitat, identifier) {
  if (identifier.includes("#")) {
    const [parent, childName] = identifier.split("#");
    const child = AgentPlugins[parent]?.plugin?.find(
      (candidate) => candidate.name === childName
    );
    if (!child) throw new Error(`Missing child plugin: ${identifier}`);
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
          AAG_WORKSPACE_ID: 10,
          AAG_THREAD_ID: 0,
          AAG_USER_ID: null,
          AAG_INVOCATION_UUID: "routing-audit-read-only",
          AAG_TURN_ID: "routing-audit-read-only",
          AAG_INVOCATION_PROMPT: "read-only routing audit",
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

async function main() {
  const output = process.argv[2];
  const workspaceSlug = process.argv[3] || "image-generator";
  if (!output) throw new Error("Output path is required.");

  const workspace = await Workspace.get({ slug: workspaceSlug });
  if (!workspace) throw new Error(`Workspace not found: ${workspaceSlug}`);
  const definition = await WORKSPACE_AGENT.getDefinition(
    workspace.chatProvider,
    workspace,
    null,
    "read-only routing audit"
  );
  const aibitat = new AIbitat({ handlerProps: { log() {} } });
  const tokenManager = new TokenManager();
  const routingMetadata = JSON.parse(
    fs.readFileSync(ROUTING_METADATA_PATH, "utf8")
  ).tools;
  const tools = [];
  const registeredTools = [];

  for (const identifier of definition.functions) {
    const registeredName = registerFunction(aibitat, identifier);
    const registered = aibitat.functions.get(registeredName);
    if (!registered)
      throw new Error(`Function did not register: ${identifier}`);
    registeredTools.push(registered);
    tools.push({
      identifier,
      name: registered.name,
      description: registered.description || "",
      parameters: registered.parameters || {},
      examples: registered.examples || [],
      importedRoutingMetadata: registered.config?.routing || null,
      document: toolToDocument(registered, tokenManager, routingMetadata),
    });
  }

  const smokeQuery = process.argv[4] || null;
  const selected = smokeQuery
    ? await new ToolReranker().rerank(smokeQuery, registeredTools, { topN: 10 })
    : [];
  const result = {
    schema: "aag.anythingllm.live-routing-audit.v1",
    capturedAt: new Date().toISOString(),
    workspace: {
      id: workspace.id,
      slug: workspace.slug,
      chatProvider: workspace.chatProvider,
      chatModel: workspace.chatModel,
    },
    effectiveTopN: Number(process.env.AGENT_SKILL_RERANKER_TOP_N || 15),
    candidateCount: tools.length,
    totalToolSchemaTokens: tools.reduce(
      (sum, tool) => sum + tool.document.schemaTokens,
      0
    ),
    smokeQuery,
    smokeTop10: selected.map((tool) => tool.name),
    tools,
  };
  fs.writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`, {
    mode: 0o600,
  });
  console.log(
    JSON.stringify({
      output,
      candidateCount: result.candidateCount,
      effectiveTopN: result.effectiveTopN,
      totalToolSchemaTokens: result.totalToolSchemaTokens,
    })
  );
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
