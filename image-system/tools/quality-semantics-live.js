#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const schema = require("../schemas/provider-task.schema.json");
const plugin = require("../skills/aag-image-task/plugin.json");

const endpoint = process.env.AAG_QUALITY_TEST_ENDPOINT || "http://127.0.0.1:8080/v1/chat/completions";
const model = process.env.AAG_QUALITY_TEST_MODEL || "gemma-4-E2B-it-Q4_K_M";
const timeoutMs = Number(process.env.AAG_QUALITY_TEST_TIMEOUT_MS || 180000);
const outputIndex = process.argv.indexOf("--output");
const outputPath = outputIndex >= 0 ? path.resolve(process.argv[outputIndex + 1]) : null;
const caseIndex = process.argv.indexOf("--case");
const selectedCase = caseIndex >= 0 ? process.argv[caseIndex + 1] : null;

const cases = [
  { id: "en_prioritize_speed", expected: "fast", text: "Create a cinematic detailed 3D storybook illustration of a child reading under an oak tree. Explicitly prioritize speed." },
  { id: "en_fastest_possible", expected: "fast", text: "Generate a watercolor lighthouse at dawn using the fastest possible supported generation." },
  { id: "en_maximum_quality", expected: "quality", text: "Create a realistic mountain landscape at maximum generation quality; speed does not matter." },
  { id: "en_balanced", expected: "balanced", text: "Create an illustration of a city garden and explicitly balance speed and generation quality." },
  { id: "en_creative_only", expected: "auto", text: "Create a cinematic detailed polished 3D storybook illustration of a child reading under an oak tree." },
  { id: "he_prioritize_speed", expected: "fast", text: "צור איור תלת־ממדי קולנועי של ילד קורא מתחת לעץ. יש להעדיף במפורש מהירות וזמן המתנה קצר." },
  { id: "he_fastest_possible", expected: "fast", text: "צור מגדלור בצבעי מים בזריחה במהירות ההפקה הנתמכת הגבוהה ביותר האפשרית." },
  { id: "he_maximum_quality", expected: "quality", text: "צור נוף הרים מציאותי באיכות יצירת התמונה המרבית והגבוהה ביותר. המהירות אינה חשובה; אני מעדיף במפורש איכות על פני מהירות." },
  { id: "he_balanced", expected: "balanced", text: "צור איור של גינה עירונית. אני מעדיף במפורש איזון טכני בין מהירות היצירה לבין איכות התמונה." },
  { id: "he_creative_only", expected: "auto", text: "צור איור ספר ילדים תלת־ממדי, קולנועי, מפורט, מלוטש ויפה של ילד קורא מתחת לעץ אלון." },
];

function requestBody(testCase) {
  return {
    model,
    stream: false,
    temperature: 0,
    max_tokens: 512,
    messages: [
      { role: "system", content: plugin.description },
      { role: "user", content: testCase.text },
    ],
    tools: [{
      type: "function",
      function: {
        name: "aag-image-task",
        description: plugin.description,
        parameters: schema,
      },
    }],
    tool_choice: "required",
  };
}

async function runCase(testCase) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const startedAt = new Date().toISOString();
  const start = Date.now();
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json", connection: "close" },
      body: JSON.stringify(requestBody(testCase)),
      signal: controller.signal,
    });
    const raw = await response.text();
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${raw.slice(0, 500)}`);
    const payload = JSON.parse(raw);
    const calls = payload.choices?.[0]?.message?.tool_calls || [];
    const call = calls[0];
    const args = JSON.parse(call?.function?.arguments || "null");
    const keys = args && typeof args === "object" ? Object.keys(args) : [];
    const unknown = keys.filter(key => !Object.hasOwn(schema.properties, key));
    const actualQuality = args?.quality ?? null;
    const qualitySelection = testCase.expected === "auto"
      ? actualQuality === "auto" || actualQuality === null
      : actualQuality === testCase.expected;
    const checks = {
      exactly_one_tool_call: calls.length === 1,
      canonical_tool: call?.function?.name === "aag-image-task",
      operation_generate: args?.operation === "generate",
      quality_selection: qualitySelection,
      required_source_policy: args?.source_policy === "auto",
      required_preservation: args?.preservation === "none",
      no_style: !Object.hasOwn(args || {}, "style"),
      documented_fields_only: unknown.length === 0,
    };
    return {
      id: testCase.id,
      input: testCase.text,
      expected_quality: testCase.expected,
      actual_quality: actualQuality,
      tool_call_count: calls.length,
      started_at: startedAt,
      duration_ms: Date.now() - start,
      arguments: args,
      prompt_authored_in_call: typeof args?.prompt === "string" && args.prompt.trim().length > 0,
      unknown_fields: unknown,
      checks,
      pass: Object.entries(checks).filter(([name]) => name !== "exactly_one_tool_call").every(([, value]) => value),
    };
  } catch (error) {
    return {
      id: testCase.id,
      input: testCase.text,
      expected_quality: testCase.expected,
      started_at: startedAt,
      duration_ms: Date.now() - start,
      error: error.name === "AbortError" ? `timeout after ${timeoutMs}ms` : error.message,
      pass: false,
    };
  } finally {
    clearTimeout(timer);
  }
}

async function main() {
  const results = [];
  const selectedCases = selectedCase ? cases.filter(testCase => testCase.id === selectedCase) : cases;
  if (selectedCases.length === 0) throw new Error(`unknown case: ${selectedCase}`);
  for (const testCase of selectedCases) {
    const result = await runCase(testCase);
    results.push(result);
    process.stdout.write(`${result.pass ? "PASS" : "FAIL"} ${result.id} expected=${result.expected_quality} actual=${result.actual_quality ?? "none"} duration_ms=${result.duration_ms}${result.error ? ` error=${result.error}` : ""}\n`);
  }
  const evidence = {
    generated_at: new Date().toISOString(),
    endpoint,
    model,
    provider_connection_header: "close",
    schema_additional_properties: schema.additionalProperties,
    schema_required: schema.required,
    public_fields: Object.keys(schema.properties),
    results,
    pass: results.every(result => result.pass),
  };
  if (outputPath) fs.writeFileSync(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  if (!evidence.pass) process.exitCode = 1;
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
