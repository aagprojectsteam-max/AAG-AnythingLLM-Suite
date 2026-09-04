#!/usr/bin/env node
"use strict";

const fs = require("fs");

const [beforePath, afterPath, outputPath] = process.argv.slice(2);
if (!beforePath || !afterPath || !outputPath) {
  throw new Error(
    "Usage: compile-routing-report.js BEFORE_JSON AFTER_JSON OUTPUT_JSON"
  );
}

const before = JSON.parse(fs.readFileSync(beforePath, "utf8"));
const after = JSON.parse(fs.readFileSync(afterPath, "utf8"));
const domains = [
  "aag-image-task",
  "aag-image-batch",
  "aag-image-job",
  "aag-chess-puzzle",
  "aag-governed-orchestration-v1",
  "aag-ubuntu-live-audit",
  "create-pdf-file",
  "create-excel-file",
  "filesystem-search-files",
  "gmail-send-email",
  "gmail-list-drafts",
  "gmail-search",
];

const actualBefore = {
  "he-image-chess": "aag-governed-orchestration-v1 x1 (historical Qwen regression)",
};
const actualAfter = {
  "he-image-chess": "aag-image-task x1 (Qwen; completed artifact)",
  "he-chess":
    "aag-chess-puzzle x2 (Qwen; same domain, second call corrected missing mate argument)",
  "he-diagnose": "aag-ubuntu-live-audit x1 (Qwen)",
  "en-image-chess":
    "aag-image-task x1 (Gemma; routing passed, harness-induced XPU contention interrupted execution)",
};

function rank(row, name) {
  const index = row.all51.findIndex((entry) => entry.name === name);
  return index < 0 ? null : index + 1;
}

function shape(row, actualCalls, phase) {
  const wrongRanks = Object.fromEntries(
    domains
      .filter((name) => name !== row.expected)
      .map((name) => [name, rank(row, name)])
  );
  return {
    REQUEST: row.request,
    EXPECTED_CLASS: row.expected,
    TOP_10: row.top10.map((entry) => entry.name),
    EXPECTED_TOOL_RANK: row.expectedRank,
    WRONG_DOMAIN_TOOL_RANK: wrongRanks,
    ACTUAL_MODEL_TOOL_CALL:
      actualCalls[row.id] || "NOT_RUN (selector/reranker matrix only)",
    PASS_FAIL:
      phase === "before"
        ? row.expectedRank > 0 && row.expectedRank <= 10
          ? "PASS_TOP10"
          : "FAIL_TOP10"
        : row.expectedRank === 1
          ? "PASS"
          : "FAIL",
  };
}

const afterById = new Map(after.results.map((row) => [row.id, row]));
const report = {
  schema: "aag.anythingllm.routing-matrix-comparison.v1",
  generatedAt: new Date().toISOString(),
  candidates: 51,
  topN: 10,
  beforeSummary: {
    top10Passes: before.top10Passes,
    top1Passes: before.top1Passes,
    cases: before.results.length,
    averageLatencyMs: before.averageLatencyMs,
  },
  afterSummary: {
    top10Passes: after.top10Passes,
    top1Passes: after.top1Passes,
    cases: after.results.length,
    averageLatencyMs: after.averageLatencyMs,
  },
  tests: before.results.map((beforeRow) => {
    const afterRow = afterById.get(beforeRow.id);
    if (!afterRow) throw new Error(`Missing after row ${beforeRow.id}`);
    return {
      id: beforeRow.id,
      before: shape(beforeRow, actualBefore, "before"),
      after: shape(afterRow, actualAfter, "after"),
    };
  }),
};

fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`, {
  mode: 0o600,
});
console.log(
  JSON.stringify({
    outputPath,
    tests: report.tests.length,
    before: report.beforeSummary,
    after: report.afterSummary,
  })
);
