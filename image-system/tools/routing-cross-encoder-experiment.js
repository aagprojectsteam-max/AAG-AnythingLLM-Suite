#!/usr/bin/env node
"use strict";

const fs = require("fs");
const {
  NativeEmbeddingReranker,
} = require("/app/server/utils/EmbeddingRerankers/native");

const live = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const metadata = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const outputPath = process.argv[4];
const queries = JSON.parse(fs.readFileSync(process.argv[5], "utf8"));
const mode = process.argv[6] || "after";

function tokens(value) {
  return String(value || "")
    .toLocaleLowerCase("und")
    .normalize("NFKC")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .split(/\s+/u)
    .filter(Boolean);
}

function phraseScore(request, alias) {
  const query = tokens(request);
  const phrase = tokens(alias);
  if (!query.length || !phrase.length) return 0;
  for (let start = 0; start <= query.length - phrase.length; start += 1) {
    if (phrase.every((token, index) => token === query[start + index])) {
      const position = 1 - start / Math.max(1, query.length - 1);
      return 1 + Math.min(phrase.length, 4) * 0.15 + position * 0.3;
    }
  }
  let best = 0;
  for (let start = 0; start < query.length; start += 1) {
    if (query[start] !== phrase[0]) continue;
    let cursor = start + 1;
    let matched = 1;
    for (let index = 1; index < phrase.length; index += 1) {
      while (cursor < query.length && query[cursor] !== phrase[index]) cursor += 1;
      if (cursor >= query.length) break;
      matched += 1;
      cursor += 1;
    }
    if (matched !== phrase.length) continue;
    const span = cursor - start;
    const tightness = phrase.length / span;
    const position = 1 - start / Math.max(1, query.length - 1);
    best = Math.max(best, 0.65 * tightness * (1 + Math.min(phrase.length, 4) * 0.1) + 0.15 * position);
  }
  return best;
}

function intentScore(request, profile) {
  return Math.max(0, ...(profile.aliases || []).map((alias) => phraseScore(request, alias)));
}

async function main() {
  const reranker = new NativeEmbeddingReranker();
  await reranker.initClient();
  const documents = live.tools.map((tool) => {
    if (mode === "before")
      return { text: tool.document.legacyRanked || tool.document.ranked };
    // The live audit reconstructs the production selector's exact, truncated
    // ranking document. Reuse it byte-for-byte so this matrix cannot drift
    // from the deployed #routingDocument/#truncateText implementation.
    return { text: tool.document.ranked };
  });
  const results = [];
  const started = Date.now();
  for (const query of queries) {
    const scored = [];
    for (let offset = 0; offset < documents.length; offset += 25) {
      const chunk = documents.slice(offset, offset + 25);
      const chunkResults = await reranker.rerank(query.request, chunk, { topK: chunk.length });
      for (const item of chunkResults) {
        const tool = live.tools[offset + item.rerank_corpus_id];
        const intent = mode === "before"
          ? 0
          : intentScore(query.request, metadata.tools[tool.name]);
        scored.push({
          name: tool.name,
          score: item.rerank_score + 4 * intent,
          semanticScore: item.rerank_score,
          intentScore: intent,
        });
      }
    }
    scored.sort((left, right) => right.score - left.score);
    const expectedRank = scored.findIndex((item) => item.name === query.expected) + 1;
    results.push({ ...query, expectedRank, top10: scored.slice(0, 10), all51: scored });
  }
  const output = {
    capturedAt: new Date().toISOString(),
    mode,
    latencyMs: Date.now() - started,
    averageLatencyMs: Math.round((Date.now() - started) / queries.length),
    model: reranker.model,
    candidateCount: live.tools.length,
    topN: 10,
    top10Passes: results.filter((item) => item.expectedRank <= 10).length,
    top1Passes: results.filter((item) => item.expectedRank === 1).length,
    results,
  };
  fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify({ outputPath, latencyMs: output.latencyMs, top10Passes: output.top10Passes, top1Passes: output.top1Passes }));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
