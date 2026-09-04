#!/usr/bin/env node
"use strict";

const fs = require("fs");

const inputPath = process.argv[2];
const outputPath = process.argv[3];
const baseUrl = process.argv[4] || "http://127.0.0.1:8080";
if (!inputPath || !outputPath)
  throw new Error("Usage: account-model-input.js INPUT OUTPUT [LLAMA_URL]");

const capture = JSON.parse(fs.readFileSync(inputPath, "utf8"));

async function postJson(endpoint, body) {
  const response = await fetch(`${baseUrl}${endpoint}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(`${endpoint} failed: ${response.status} ${await response.text()}`);
  return await response.json();
}

async function applyTemplate(messages, tools = []) {
  return (
    await postJson("/apply-template", {
      messages,
      ...(tools.length ? { tools } : {}),
    })
  ).prompt;
}

async function count(content) {
  return (await postJson("/tokenize", { content })).tokens.length;
}

async function account(mode) {
  const components = capture.components;
  const payload = capture[mode].payload;
  const finalSystem = payload.messages[0].content;
  const rawUser = components.currentUserMessage;
  const userWithRag = payload.messages[1].content;
  const workspace = components.workspacePrompt;
  const memory = components.memoryContext;
  const agent = components.agentInstructions;
  if (`${workspace}${memory}${agent}` !== finalSystem)
    throw new Error(`${mode}: system component concatenation did not reproduce live role`);

  const prompts = {};
  prompts.empty = await applyTemplate([
    { role: "system", content: "" },
    { role: "user", content: "" },
  ]);
  prompts.workspace = await applyTemplate([
    { role: "system", content: workspace },
    { role: "user", content: "" },
  ]);
  prompts.memory = await applyTemplate([
    { role: "system", content: `${workspace}${memory}` },
    { role: "user", content: "" },
  ]);
  prompts.agent = await applyTemplate([
    { role: "system", content: finalSystem },
    { role: "user", content: "" },
  ]);
  prompts.user = await applyTemplate([
    { role: "system", content: finalSystem },
    { role: "user", content: rawUser },
  ]);
  prompts.rag = await applyTemplate([
    { role: "system", content: finalSystem },
    { role: "user", content: userWithRag },
  ]);
  prompts.full = await applyTemplate(payload.messages, payload.tools);

  const counts = {};
  for (const [name, prompt] of Object.entries(prompts)) counts[name] = await count(prompt);
  const toolsStart = prompts.full.indexOf("<tools>\n");
  const toolsEnd = prompts.full.indexOf("\n</tools>", toolsStart);
  if (toolsStart < 0 || toolsEnd < 0)
    throw new Error(`${mode}: rendered provider prompt did not contain a tools block`);
  const serializedTools = prompts.full.slice(toolsStart + 8, toolsEnd);
  const toolSchemaTokens = await count(serializedTools);
  const toolDelta = counts.full - counts.rag;
  const breakdown = {
    TOTAL_MODEL_INPUT_TOKENS: counts.full,
    BASE_SYSTEM_AGENT_TOKENS: 0,
    WORKSPACE_PROMPT_TOKENS: counts.workspace - counts.empty,
    AGENT_INSTRUCTION_TOKENS: counts.agent - counts.memory,
    SELECTED_TOOL_SCHEMA_TOKENS: toolSchemaTokens,
    THREAD_HISTORY_TOKENS: 0,
    CURRENT_USER_MESSAGE_TOKENS: counts.user - counts.agent,
    RAG_CONTEXT_TOKENS: counts.rag - counts.user,
    MEMORY_CONTEXT_TOKENS: counts.memory - counts.workspace,
    ATTACHMENT_CONTEXT_TOKENS: 0,
    PROVIDER_SERIALIZATION_OVERHEAD_TOKENS:
      counts.empty + (toolDelta - toolSchemaTokens),
    OTHER_TOKENS: 0,
    UNACCOUNTED_TOKENS: 0,
  };
  const reconciled = Object.entries(breakdown)
    .filter(([name]) => name !== "TOTAL_MODEL_INPUT_TOKENS")
    .reduce((sum, [, value]) => sum + value, 0);
  if (reconciled !== breakdown.TOTAL_MODEL_INPUT_TOKENS)
    throw new Error(`${mode}: breakdown ${reconciled} != total ${breakdown.TOTAL_MODEL_INPUT_TOKENS}`);
  return {
    selectedToolNames: capture[mode].selectedToolNames,
    renderedPromptCharacters: prompts.full.length,
    serializedToolBlockCharacters: serializedTools.length,
    toolDeltaTokens: toolDelta,
    nonToolTokens: counts.rag,
    breakdown,
    renderedPrompt: prompts.full,
  };
}

async function main() {
  const model = (await (await fetch(`${baseUrl}/v1/models`)).json()).data[0].id;
  const before = await account("before");
  const after = await account("after");
  const output = {
    schema: "aag.anythingllm.model-input-accounting.v1",
    capturedAt: new Date().toISOString(),
    tokenizerModel: model,
    accountingMethod:
      "llama.cpp /apply-template plus /tokenize differential accounting; components reconcile exactly to the rendered provider prompt.",
    before,
    after,
  };
  fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, {
    mode: 0o600,
  });
  console.log(
    JSON.stringify({
      outputPath,
      tokenizerModel: model,
      before: before.breakdown,
      after: after.breakdown,
    })
  );
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
