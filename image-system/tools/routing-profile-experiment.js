#!/usr/bin/env node
"use strict";

/**
 * Offline/read-only experiment for action-domain-operation routing profiles.
 * Intended to run inside the AnythingLLM container.
 */

const fs = require("fs");
const { NativeEmbedder } = require("/app/server/utils/EmbeddingEngines/native");

const liveAuditPath = process.argv[2];
const metadataPath = process.argv[3];
const outputPath = process.argv[4];
if (!liveAuditPath || !metadataPath || !outputPath) {
  throw new Error("Usage: routing-profile-experiment.js LIVE_AUDIT METADATA OUTPUT");
}

const matrix = [
  ["he-image-chess", "תעשה לי תמונה איכותית של קוף משחק שחמט עם פיל", "aag-image-task"],
  ["he-image-ubuntu", "תעשה לי תמונה של מחשב אובונטו מקולקל", "aag-image-task"],
  ["he-drawing-chess", "תעשה לי ציור של לוח שחמט", "aag-image-task"],
  ["he-image-batch", "תעשה לי 3 תמונות שונות של חתול", "aag-image-batch"],
  ["he-chess", "תיצור לי חידת שחמט", "aag-chess-puzzle"],
  ["he-diagnose", "תבדוק למה המחשב איטי", "aag-ubuntu-live-audit"],
  ["he-pdf", "תיצור לי קובץ PDF", "create-pdf-file"],
  ["he-excel", "תיצור לי קובץ אקסל", "create-excel-file"],
  ["he-file-search", "תחפש לי קובץ במחשב", "filesystem-search-files"],
  ["en-image-chess", "Create a cinematic image of a chess board", "aag-image-task"],
  ["en-image-ubuntu", "Generate an illustration of a broken Ubuntu laptop", "aag-image-task"],
  ["en-image-batch", "Create three different images of a cat", "aag-image-batch"],
  ["en-chess", "Create a chess puzzle", "aag-chess-puzzle"],
  ["en-diagnose", "Diagnose why Ubuntu is slow", "aag-ubuntu-live-audit"],
  ["en-pdf", "Create a PDF file", "create-pdf-file"],
  ["en-excel", "Create an Excel spreadsheet", "create-excel-file"],
  ["en-file-search", "Search for a file on disk", "filesystem-search-files"],
  ["en-image-chess-puzzle", "Create an image of a chess puzzle", "aag-image-task"],
  ["en-image-terminal", "Create an image of an Ubuntu terminal", "aag-image-task"],
  ["en-diagnose-terminal", "Diagnose my Ubuntu terminal problem", "aag-ubuntu-live-audit"],
  ["en-image-excel", "Create an image of an Excel spreadsheet", "aag-image-task"],
  ["en-image-pdf", "Create an illustration of a PDF document", "aag-image-task"],
  ["en-send-email", "Send an email", "gmail-send-email"],
  ["en-list-drafts", "List my drafts", "gmail-list-drafts"],
  ["en-search-mail", "Search my mail", "gmail-search"],
  ["he-send-email", "שלח אימייל", "gmail-send-email"],
  ["he-list-drafts", "הצג את טיוטות האימייל שלי", "gmail-list-drafts"],
  ["he-search-mail", "חפש בדואר שלי", "gmail-search"],
  ["en-read-file", "Read the text file /tmp/notes.txt", "filesystem-read-text-file"],
  ["en-list-dir", "List the files in /tmp", "filesystem-list-directory"],
  ["en-sql-query", "Query the customers table for overdue accounts", "sql-query"],
  ["en-web-search", "Search the web for today's weather", "web-browsing"],
  ["en-web-scrape", "Extract the article text from https://example.com/news", "web-scraping"],
  ["en-summary", "Summarize the attached document", "document-summarizer"],
  ["en-schedule", "Schedule this task to run every Monday", "create-scheduled-job"]
];

function dot(a, b) {
  let total = 0;
  for (let index = 0; index < a.length; index += 1) total += a[index] * b[index];
  return total;
}

function leadingIntentSegment(request) {
  return request.trim().split(/\s+/u).slice(0, 5).join(" ");
}

async function main() {
  const started = Date.now();
  const live = JSON.parse(fs.readFileSync(liveAuditPath, "utf8"));
  const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  const missing = live.tools
    .map((tool) => tool.name)
    .filter((name) => !metadata.tools[name]);
  if (missing.length) throw new Error(`Missing routing profiles: ${missing.join(", ")}`);

  const embedder = new NativeEmbedder();
  if (embedder.model !== "MintplexLabs/multilingual-e5-small") {
    throw new Error(`Expected multilingual-e5-small, got ${embedder.model}`);
  }

  const fields = ["action", "domain", "operation"];
  const profileTexts = [];
  for (const tool of live.tools) {
    const profile = metadata.tools[tool.name];
    for (const field of fields) {
      const aliases = field === "action" && Array.isArray(profile.aliases)
        ? ` Multilingual intent aliases: ${profile.aliases.join("; ")}.`
        : "";
      profileTexts.push(`passage: ${profile[field]}${aliases}`);
    }
  }
  const profileVectors = await embedder.embedChunks(profileTexts);
  const queryVectors = await embedder.embedChunks(
    matrix.map(([, request]) => `query: ${request}`)
  );
  const actionQueryVectors = await embedder.embedChunks(
    matrix.map(([, request]) => `query: ${leadingIntentSegment(request)}`)
  );
  const results = [];

  for (let queryIndex = 0; queryIndex < matrix.length; queryIndex += 1) {
    const [id, request, expected] = matrix[queryIndex];
    const queryVector = queryVectors[queryIndex];
    const actionQueryVector = actionQueryVectors[queryIndex];
    const ranked = live.tools
      .map((tool, toolIndex) => {
        const base = toolIndex * fields.length;
        const action = dot(actionQueryVector, profileVectors[base]);
        const domain = dot(queryVector, profileVectors[base + 1]);
        const operation = dot(queryVector, profileVectors[base + 2]);
        return {
          name: tool.name,
          score: 0.7 * action + 0.05 * domain + 0.25 * operation,
          action,
          domain,
          operation,
        };
      })
      .sort((left, right) => right.score - left.score);
    const expectedRank = ranked.findIndex((tool) => tool.name === expected) + 1;
    results.push({
      id,
      request,
      expected,
      expectedRank,
      passTop10: expectedRank > 0 && expectedRank <= 10,
      passTop1: expectedRank === 1,
      top10: ranked.slice(0, 10),
      all51: ranked,
    });
  }

  const output = {
    schema: "aag.anythingllm.routing-profile-experiment.v1",
    capturedAt: new Date().toISOString(),
    embeddingModel: embedder.model,
    actionSegmentation: "first five whitespace-delimited tokens",
    weights: { action: 0.7, domain: 0.05, operation: 0.25 },
    latencyMs: Date.now() - started,
    candidateCount: live.tools.length,
    queryCount: matrix.length,
    top10Passes: results.filter((result) => result.passTop10).length,
    top1Passes: results.filter((result) => result.passTop1).length,
    results,
  };
  fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, { mode: 0o600 });
  console.log(
    JSON.stringify({
      outputPath,
      embeddingModel: output.embeddingModel,
      latencyMs: output.latencyMs,
      top10Passes: `${output.top10Passes}/${output.queryCount}`,
      top1Passes: `${output.top1Passes}/${output.queryCount}`,
      failures: results
        .filter((result) => !result.passTop1)
        .map((result) => `${result.id}:${result.expectedRank}`),
    })
  );
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
