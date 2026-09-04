const fs = require("fs");
const path = require("path");
const { TokenManager } = require("../../../helpers/tiktoken");
const {
  NativeEmbeddingReranker,
} = require("../../../EmbeddingRerankers/native");

const CHUNK_SIZE = 25;
const MAX_TEXT_LENGTH = 1000;
const INTENT_WEIGHT = 4;
const DEFAULT_METADATA_PATH = path.resolve(
  process.env.STORAGE_DIR || path.resolve(__dirname, "../../../../storage"),
  "aag-image-agent-integration/routing-correction/tool-routing-metadata.json"
);

function normalizedTokens(value) {
  return String(value || "")
    .toLocaleLowerCase("und")
    .normalize("NFKC")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .split(/\s+/u)
    .filter(Boolean);
}

/**
 * Generic ordered-phrase relevance. Exact, early, compact action phrases score
 * above later/gapped subject-matter mentions. No tool or sentence is hardcoded.
 */
function phraseScore(request, alias) {
  const query = normalizedTokens(request);
  const phrase = normalizedTokens(alias);
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
    best = Math.max(
      best,
      0.65 * tightness * (1 + Math.min(phrase.length, 4) * 0.1) +
        0.15 * position
    );
  }
  return best;
}

function intentScore(request, profile) {
  if (!Array.isArray(profile?.aliases)) return 0;
  return Math.max(
    0,
    ...profile.aliases.map((alias) => phraseScore(request, alias))
  );
}

class ToolReranker {
  static defaultTopN = 15;

  static instance = null;

  constructor() {
    if (ToolReranker.instance) return ToolReranker.instance;
    ToolReranker.instance = this;
    this.tokenManager = new TokenManager();
    this.reranker = null;
    this.routingMetadata = null;
    this.routingMetadataMtime = 0;
  }

  log(text, ...args) {
    console.log(`\x1b[33m[IntelligentSkillSelector]\x1b[0m ${text}`, ...args);
  }

  static isEnabled() {
    if (!("AGENT_SKILL_RERANKER_ENABLED" in process.env)) return true;
    return process.env.AGENT_SKILL_RERANKER_ENABLED !== "false";
  }

  static getTopN() {
    const envTopN = parseInt(process.env.AGENT_SKILL_RERANKER_TOP_N, 10);
    return !isNaN(envTopN) && envTopN > 0
      ? envTopN
      : ToolReranker.defaultTopN;
  }

  #truncateText(text, maxLength = MAX_TEXT_LENGTH) {
    if (!text || text.length <= maxLength) return text;
    const truncated = text.slice(0, maxLength);
    const lastSpace = truncated.lastIndexOf(" ");
    return lastSpace > maxLength * 0.8
      ? truncated.slice(0, lastSpace)
      : truncated;
  }

  #metadataPath() {
    return process.env.AGENT_SKILL_ROUTING_METADATA_PATH || DEFAULT_METADATA_PATH;
  }

  #loadRoutingMetadata() {
    const metadataPath = this.#metadataPath();
    try {
      const stat = fs.statSync(metadataPath);
      if (
        this.routingMetadata &&
        this.routingMetadataMtime === stat.mtimeMs
      )
        return this.routingMetadata;
      const parsed = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
      if (!parsed?.tools || typeof parsed.tools !== "object")
        throw new Error("metadata must contain a tools object");
      this.routingMetadata = parsed.tools;
      this.routingMetadataMtime = stat.mtimeMs;
      this.log(
        `Loaded action-domain-operation metadata for ${Object.keys(parsed.tools).length} tools`
      );
      return this.routingMetadata;
    } catch (error) {
      this.log(`Routing metadata unavailable: ${error.message}`);
      this.routingMetadata = {};
      return this.routingMetadata;
    }
  }

  #profileForTool(tool) {
    const inline = tool?.config?.routing;
    if (
      inline?.action &&
      inline?.domain &&
      inline?.operation
    )
      return inline;
    return this.#loadRoutingMetadata()[tool?.name] || null;
  }

  #routingDocument(tool, profile) {
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

  async #getReranker() {
    if (!this.reranker) {
      this.reranker = new NativeEmbeddingReranker();
      await this.reranker.initClient();
    }
    return this.reranker;
  }

  async #chunkedRerank(query, documents) {
    const reranker = await this.#getReranker();
    if (documents.length <= CHUNK_SIZE)
      return await reranker.rerank(query, documents, {
        topK: documents.length,
      });

    this.log(
      `Processing ${documents.length} documents in chunks of ${CHUNK_SIZE}...`
    );
    const allScored = [];
    for (let offset = 0; offset < documents.length; offset += CHUNK_SIZE) {
      const chunk = documents.slice(offset, offset + CHUNK_SIZE);
      const chunkNumber = Math.floor(offset / CHUNK_SIZE) + 1;
      const totalChunks = Math.ceil(documents.length / CHUNK_SIZE);
      this.log(
        `Processing chunk ${chunkNumber}/${totalChunks} (${chunk.length} docs)...`
      );
      const chunkResults = await reranker.rerank(query, chunk, {
        topK: chunk.length,
      });
      for (const result of chunkResults) {
        allScored.push({
          ...result,
          rerank_corpus_id: result.rerank_corpus_id + offset,
        });
      }
    }
    return allScored;
  }

  #toolToDocument(tool) {
    const parts = [];
    if (!tool?.name)
      return {
        text: null,
        rankingText: null,
        toolName: null,
        tool: null,
        tokens: 0,
        profile: null,
      };

    parts.push(tool.name);
    if (tool.description) parts.push(tool.description);
    if (tool.parameters?.properties) {
      const parameterDescriptions = Object.entries(
        tool.parameters.properties
      ).map(([name, property]) =>
        property.description ? `${name}: ${property.description}` : name
      );
      if (parameterDescriptions.length)
        parts.push(parameterDescriptions.join(", "));
    }
    const examplePrompts = Array.isArray(tool.examples)
      ? tool.examples.map((example) => example?.prompt).filter(Boolean)
      : [];
    if (examplePrompts.length) parts.push(examplePrompts.join("; "));

    const text = parts.join("\n");
    const profile = this.#profileForTool(tool);
    return {
      text,
      rankingText: this.#routingDocument(tool, profile) || text,
      toolName: tool.name,
      tool,
      tokens: this.tokenManager.countFromString(text),
      profile,
    };
  }

  async rerank(userPrompt, tools = [], options = {}) {
    if (!ToolReranker.isEnabled()) return tools;
    if (!Array.isArray(tools) || tools.length === 0) return tools;
    const { topN = ToolReranker.getTopN() } = options;
    if (tools.length <= topN) {
      this.log(`${tools.length} tools <= ${topN}, skipping reranking`);
      return tools;
    }

    try {
      this.log(`Starting tool reranking for ${tools.length} tools...`);
      const documents = tools.map((tool) => this.#toolToDocument(tool));
      const originalTokenCount = documents.reduce(
        (sum, document) => sum + document.tokens,
        0
      );
      const rerankDocuments = documents.map((document) => ({
        text: this.#truncateText(document.rankingText),
      }));

      const started = Date.now();
      const semanticResults = await this.#chunkedRerank(
        userPrompt,
        rerankDocuments
      );
      const scored = semanticResults
        .map((result) => {
          const index = result.rerank_corpus_id;
          const semanticScore = result.rerank_score;
          const actionIntentScore = intentScore(
            userPrompt,
            documents[index].profile
          );
          return {
            index,
            semanticScore,
            actionIntentScore,
            score: semanticScore + INTENT_WEIGHT * actionIntentScore,
          };
        })
        .sort((left, right) => right.score - left.score)
        .slice(0, topN);
      const elapsedMs = Date.now() - started;
      const rerankedTools = scored.map(({ index }) => documents[index].tool);
      const newTokenCount = scored.reduce(
        (sum, { index }) => sum + documents[index].tokens,
        0
      );
      const percentSaved = Math.round(
        ((originalTokenCount - newTokenCount) / originalTokenCount) * 100
      );

      this.log(`
Identified top ${rerankedTools.length} of ${tools.length} tools in ${elapsedMs}ms
${originalTokenCount.toLocaleString()} -> ${newTokenCount.toLocaleString()} tokens \x1b[0;93m(${percentSaved}% reduction)\x1b[0m`);
      let selectionLog = "Selected tools (action -> domain -> operation):\n";
      scored.forEach(
        ({ index, score, semanticScore, actionIntentScore }, rank) => {
          selectionLog += `  ${rank + 1}. ${documents[index].toolName} score=${score.toFixed(6)} semantic=${semanticScore.toFixed(6)} intent=${actionIntentScore.toFixed(6)}\n`;
        }
      );
      this.log(selectionLog);
      return rerankedTools;
    } catch (error) {
      this.log(`Error during tool reranking: ${error.message}`);
      this.log("Falling back to original tool set");
      return tools;
    }
  }
}

module.exports = { ToolReranker };
