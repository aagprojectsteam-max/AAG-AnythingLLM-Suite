#!/usr/bin/env node
"use strict";

const fs = require("fs");

const envPath = "/mnt/data/AI/Apps/AnythingLLM/storage/.env";
const capturePath =
  "/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/routing-model-input-capture.json";
const outputPath = process.argv[2];

if (!outputPath) {
  throw new Error("Usage: gemini-routing-validation.js OUTPUT_JSON");
}

function envValue(name) {
  const source = fs.readFileSync(envPath, "utf8");
  const line = source
    .split(/\r?\n/)
    .find((entry) => entry.startsWith(`${name}=`));
  if (!line) return "";
  return line
    .slice(name.length + 1)
    .trim()
    .replace(/^(['"])(.*)\1$/, "$2");
}

async function main() {
  const key = envValue("GEMINI_API_KEY");
  if (!key) throw new Error("GEMINI_API_KEY is unavailable");
  const model = envValue("GEMINI_LLM_MODEL_PREF") || "gemini-2.5-flash";
  const capture = JSON.parse(fs.readFileSync(capturePath, "utf8"));
  const payload = structuredClone(capture.after.payload);
  payload.model = model;
  payload.stream = false;
  delete payload.stream_options;
  payload.temperature = 0;

  const startedAt = new Date().toISOString();
  const started = Date.now();
  const response = await fetch(
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${key}`,
        connection: "close",
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(240000),
    }
  );
  const raw = await response.text();
  if (!response.ok) {
    throw new Error(`Gemini HTTP ${response.status}: ${raw.slice(0, 500)}`);
  }
  const result = JSON.parse(raw);
  const calls = result.choices?.[0]?.message?.tool_calls || [];
  const toolNames = calls.map((call) => call.function?.name).filter(Boolean);
  const evidence = {
    schema: "aag.routing.gemini-provider-validation.v1",
    startedAt,
    completedAt: new Date().toISOString(),
    durationMs: Date.now() - started,
    provider: "Gemini OpenAI-compatible endpoint",
    model,
    query: capture.components.currentUserMessage,
    selectedToolNames: capture.after.selectedToolNames,
    toolCalls: calls.map((call) => ({
      name: call.function?.name,
      arguments: call.function?.arguments,
    })),
    finishReason: result.choices?.[0]?.finish_reason || null,
    usage: result.usage || null,
    pass: toolNames.length === 1 && toolNames[0] === "aag-image-task",
  };
  fs.writeFileSync(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, {
    mode: 0o600,
  });
  console.log(
    JSON.stringify({
      outputPath,
      model,
      durationMs: evidence.durationMs,
      toolNames,
      pass: evidence.pass,
    })
  );
  if (!evidence.pass) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
