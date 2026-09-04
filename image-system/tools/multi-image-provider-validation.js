#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const schema = require("../schemas/provider-batch.schema.json");
const plugin = require("../skills/aag-image-batch/plugin.json");
const { validatePublicArguments } = require("../integrations/anythingllm/aagPublicToolSchema");

const root = path.resolve(__dirname, "..");
const outputIndex = process.argv.indexOf("--output");
const output = outputIndex >= 0 ? path.resolve(process.argv[outputIndex + 1]) : null;
const providerIndex = process.argv.indexOf("--provider");
const provider = providerIndex >= 0 ? process.argv[providerIndex + 1] : "gemma";

const cases = [
  { id: "english_distinct_3", count: 3, quality: "auto", text: "Create exactly three distinct cinematic watercolor lighthouse images at dawn. Each lighthouse and coastline should be different. I have no technical speed or quality preference." },
  { id: "english_variants_2_quality", count: 2, quality: "quality", text: "Create exactly two coherent variants of one friendly robot gardener, keeping the same character design but changing the garden composition. Use maximum generation quality; speed does not matter." },
  { id: "english_larger_6_fast", count: 6, quality: "fast", text: "Create exactly six ordered storybook scenes showing one paper boat traveling from a rainy street to the sea. Explicitly prioritize fastest supported generation and low latency." },
  { id: "hebrew_coherent_3", count: 3, quality: "auto", text: "צור בדיוק שלוש תמונות מאוירות בסדרה עקבית של שועל קטן המטייל ביער: בוקר, צהריים ולילה. אין לי העדפה טכנית בין מהירות לאיכות." },
];

function envValue(name) {
  const source = fs.readFileSync("/mnt/data/AI/Apps/AnythingLLM/storage/.env", "utf8");
  const line = source.split(/\r?\n/).find((entry) => entry.startsWith(`${name}=`));
  if (!line) return "";
  return line.slice(name.length + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
}

function endpointConfig() {
  if (provider === "gemma") return {
    endpoint: "http://127.0.0.1:8080/v1/chat/completions",
    model: "gemma-4-E2B-it-Q4_K_M",
    headers: { "content-type": "application/json", connection: "close" },
  };
  if (provider === "gemini") {
    const key = envValue("GEMINI_API_KEY");
    if (!key) throw new Error("GEMINI_API_KEY is unavailable");
    return {
      endpoint: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
      model: envValue("GEMINI_LLM_MODEL_PREF") || "gemini-2.5-flash",
      headers: { "content-type": "application/json", authorization: `Bearer ${key}`, connection: "close" },
    };
  }
  throw new Error(`unsupported provider ${provider}`);
}

function body(testCase, config) {
  return {
    model: config.model,
    stream: false,
    temperature: 0,
    max_tokens: testCase.count >= 6 ? 4096 : 2500,
    messages: [
      { role: "system", content: `You are the AnythingLLM workspace model. Follow the current user request and call the one available tool exactly once. ${plugin.description}` },
      { role: "user", content: testCase.text },
    ],
    tools: [{ type: "function", function: { name: "aag-image-batch", description: plugin.description, parameters: schema } }],
    tool_choice: "required",
  };
}

async function run(testCase, config) {
  const startedAt = new Date().toISOString();
  const started = Date.now();
  try {
    const response = await fetch(config.endpoint, {
      method: "POST",
      headers: config.headers,
      body: JSON.stringify(body(testCase, config)),
      signal: AbortSignal.timeout(240000),
    });
    const raw = await response.text();
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${raw.slice(0, 500)}`);
    const payload = JSON.parse(raw);
    const calls = payload.choices?.[0]?.message?.tool_calls || [];
    const call = calls[0];
    const args = JSON.parse(call?.function?.arguments || "null");
    const validation = validatePublicArguments(schema, args);
    const itemCount = Array.isArray(args?.items) ? args.items.length : -1;
    const checks = {
      exactly_one_tool_call: calls.length === 1,
      canonical_tool: call?.function?.name === "aag-image-batch",
      closed_schema_valid: validation.valid,
      operation: args?.operation === "multi_generate",
      exact_count: args?.count === testCase.count && itemCount === testCase.count,
      quality_semantics: args?.quality === testCase.quality,
      collection_brief: typeof args?.collection_brief === "string" && args.collection_brief.trim().length > 0,
      every_prompt_authored: itemCount === testCase.count && args.items.every((item) => typeof item?.prompt === "string" && item.prompt.trim().length > 0),
      no_structured_style: !Object.hasOwn(args || {}, "style") && (args?.items || []).every((item) => !Object.hasOwn(item || {}, "style")),
    };
    return {
      id: testCase.id,
      input: testCase.text,
      expectedCount: testCase.count,
      expectedQuality: testCase.quality,
      startedAt,
      durationMs: Date.now() - started,
      toolCallCount: calls.length,
      arguments: args,
      schemaErrors: validation.errors,
      checks,
      pass: Object.values(checks).every(Boolean),
    };
  } catch (error) {
    return { id: testCase.id, input: testCase.text, expectedCount: testCase.count, expectedQuality: testCase.quality, startedAt, durationMs: Date.now() - started, error: error.message, pass: false };
  }
}

async function main() {
  const config = endpointConfig();
  const selected = provider === "gemini" ? cases.filter((entry) => ["english_distinct_3", "hebrew_coherent_3"].includes(entry.id)) : cases;
  const results = [];
  for (const testCase of selected) {
    const result = await run(testCase, config);
    results.push(result);
    console.log(`${result.pass ? "PASS" : "FAIL"} ${provider} ${result.id} count=${result.arguments?.count ?? "none"} items=${result.arguments?.items?.length ?? "none"} quality=${result.arguments?.quality ?? "none"} duration_ms=${result.durationMs}${result.error ? ` error=${result.error}` : ""}`);
  }
  const evidence = {
    schema: "aag.multi-image.provider-validation.v1",
    generatedAt: new Date().toISOString(),
    provider,
    endpoint: provider === "gemma" ? config.endpoint : "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    model: config.model,
    connectionHeader: "close",
    publicSchema: schema,
    publicSchemaAdditionalProperties: schema.additionalProperties,
    results,
    pass: results.every((result) => result.pass),
  };
  if (output) fs.writeFileSync(output, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  if (!evidence.pass) process.exitCode = 1;
}

main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
