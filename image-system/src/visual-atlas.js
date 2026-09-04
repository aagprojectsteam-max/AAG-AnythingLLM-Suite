"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { AagError } = require("./errors");

const MODULE = "visual-atlas";
const PLAN_SCHEMA = "aag.selective-knowledge.plan.v1";
const MARKER_PREFIX = "AAG_ATLAS_SELECTION_V1";
const SAFE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const MODES = new Set(["auto", "manual_taxonomy", "manual_browse"]);
const MAX_CONTEXT_CHARS = 720;
let cached = null;

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function normalizeText(value) {
  return String(value || "").normalize("NFKC").toLocaleLowerCase("und")
    .replace(/[’]/gu, "'").replace(/[־]/gu, "-")
    .replace(/[^\p{L}\p{N}_\u0590-\u05ff]+/gu, " ").replace(/\s+/gu, " ").trim();
}

function contains(text, phrase) {
  return Boolean(phrase) && ` ${text} `.includes(` ${phrase} `);
}

function resolveAtlasRoot() {
  const candidates = [
    process.env.AAG_VISUAL_ATLAS_ROOT,
    "/app/server/storage/aag-visual-atlas",
    path.resolve(__dirname, "../..", "visual-atlas"),
  ].filter(Boolean);
  const root = candidates.find(candidate => fs.existsSync(path.join(candidate, "manifest", "atlas-manifest.json")));
  if (!root) throw new AagError("ATLAS_UNAVAILABLE", "The Visual Atlas manifest is unavailable.");
  return fs.realpathSync(root);
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { throw new AagError("ATLAS_INVALID", "The Visual Atlas metadata is invalid.", false, error?.message); }
}

function safeAsset(root, relative, suffix) {
  if (typeof relative !== "string" || !relative.endsWith(suffix) || path.isAbsolute(relative)) throw new AagError("ATLAS_INVALID", "The Visual Atlas contains an unsafe asset path.");
  const resolved = path.resolve(root, relative);
  if (!resolved.startsWith(`${root}${path.sep}`)) throw new AagError("ATLAS_INVALID", "The Visual Atlas asset escapes its root.");
  return resolved;
}

function load() {
  if (cached) return cached;
  const root = resolveAtlasRoot();
  const manifestFile = path.join(root, "manifest", "atlas-manifest.json");
  const aliasFile = path.join(root, "manifest", "retrieval-aliases.json");
  const taxonomyCandidates = [
    process.env.AAG_VISUAL_TAXONOMY_PATH,
    path.join(__dirname, "visual-taxonomy.json"),
    path.join(root, "..", "image-agent", "integrations", "model-neutral-compatibility", "composer", "visual-taxonomy.json"),
    path.resolve(__dirname, "..", "integrations", "model-neutral-compatibility", "composer", "visual-taxonomy.json"),
  ].filter(Boolean);
  const taxonomyFile = taxonomyCandidates.find(file => fs.existsSync(file));
  if (!taxonomyFile) throw new AagError("ATLAS_UNAVAILABLE", "The canonical Visual Atlas taxonomy is unavailable.");
  const manifest = readJson(manifestFile);
  const aliases = readJson(aliasFile);
  const taxonomy = readJson(taxonomyFile);
  const taxonomySha256 = sha256File(taxonomyFile);
  if (manifest.taxonomy_sha256 !== taxonomySha256) throw new AagError("ATLAS_INVALID", "The Visual Atlas taxonomy hash does not match its manifest.");
  if (aliases.atlas_version !== manifest.atlas_version) throw new AagError("ATLAS_INVALID", "The Visual Atlas alias layer targets a different Atlas version.");

  const familyLabels = new Map();
  const subfamilyLabels = new Map();
  const canonicalPairs = [];
  for (const family of taxonomy.families || []) {
    if (!SAFE_ID.test(family.id)) throw new AagError("ATLAS_INVALID", "The Visual Atlas contains an invalid family ID.");
    familyLabels.set(family.id, String(family.label || family.id));
    for (const subfamily of family.subfamilies || []) {
      if (!SAFE_ID.test(subfamily.id)) throw new AagError("ATLAS_INVALID", "The Visual Atlas contains an invalid subfamily ID.");
      const key = `${family.id}/${subfamily.id}`;
      if (subfamilyLabels.has(key)) throw new AagError("ATLAS_INVALID", "The Visual Atlas contains a duplicate style.");
      subfamilyLabels.set(key, String(subfamily.label || subfamily.id));
      canonicalPairs.push(key);
    }
  }
  if (canonicalPairs.length !== 493 || !Array.isArray(manifest.entries) || manifest.entries.length !== 493) throw new AagError("ATLAS_INVALID", "The completed Visual Atlas must contain exactly 493 styles.");
  const entries = new Map();
  for (const entry of manifest.entries) {
    const key = `${entry.family_id}/${entry.subfamily_id}`;
    if (!subfamilyLabels.has(key) || entries.has(key) || entry.status !== "COMPLETED") throw new AagError("ATLAS_INVALID", "The Visual Atlas manifest does not map one-to-one to completed taxonomy entries.");
    const preview = safeAsset(root, entry.output_path, ".png");
    const thumbnail = safeAsset(root, entry.thumbnail_path, ".webp");
    const previewPresent = fs.existsSync(preview);
    const thumbnailPresent = fs.existsSync(thumbnail);
    if (previewPresent !== thumbnailPresent) throw new AagError("ATLAS_INVALID", "The Visual Atlas contains a partial asset pair.");
    if (previewPresent && (!fs.statSync(preview).isFile() || !fs.statSync(thumbnail).isFile() || fs.statSync(preview).size < 128 || fs.statSync(thumbnail).size < 128 || fs.readFileSync(thumbnail).subarray(0, 4).toString("ascii") !== "RIFF")) throw new AagError("ATLAS_INVALID", "The Visual Atlas contains an empty or invalid asset.");
    entries.set(key, { ...entry, assets_available: previewPresent });
  }
  if (canonicalPairs.some(key => !entries.has(key))) throw new AagError("ATLAS_INVALID", "The Visual Atlas taxonomy and manifest style sets differ.");

  const aliasMap = new Map();
  for (const [key, values] of Object.entries(aliases.entries || {})) {
    if (!entries.has(key) || !Array.isArray(values) || values.some(value => typeof value !== "string")) throw new AagError("ATLAS_INVALID", `The Visual Atlas alias target is invalid: ${key}`);
    aliasMap.set(key, [...new Set(values.map(normalizeText).filter(Boolean))].sort((a, b) => b.split(" ").length - a.split(" ").length || a.localeCompare(b)));
  }
  cached = Object.freeze({
    root, manifest, taxonomy, aliases, entries, familyLabels, subfamilyLabels, aliasMap,
    styleCues: (aliases.style_cues || []).map(normalizeText).filter(Boolean),
    topK: Math.min(2, Math.max(1, Number(aliases.top_k || 2))),
    minimumConfidence: Number(aliases.minimum_confidence || 0.72),
    taxonomySha256,
    manifestSha256: sha256File(manifestFile),
  });
  return cached;
}

function selection(data, key, confidence, matched) {
  const entry = data.entries.get(key);
  const [familyId, subfamilyId] = key.split("/");
  return {
    family_id: familyId,
    family_label: data.familyLabels.get(familyId),
    subfamily_id: subfamilyId,
    subfamily_label: data.subfamilyLabels.get(key),
    atlas_index: entry.index,
    style_descriptor: entry.style_descriptor,
    confidence: Number(confidence.toFixed(3)),
    matched: matched.slice(0, 4),
  };
}

function plan(data, { used = false, mode = "auto", reason, selections = [] }) {
  return {
    schema: PLAN_SCHEMA,
    module: MODULE,
    used,
    mode,
    reason,
    confidence: selections.reduce((best, item) => Math.max(best, item.confidence), 0),
    selections: selections.slice(0, data?.topK || 2),
    visual_reference_used: false,
    context_chars: 0,
    estimated_context_tokens: 0,
    taxonomy_sha256: data?.taxonomySha256 || null,
    manifest_sha256: data?.manifestSha256 || null,
  };
}

function select(text, options = {}) {
  const data = load();
  const mode = options.mode || "auto";
  if (!MODES.has(mode)) throw new AagError("ATLAS_SELECTION_INVALID", "The Visual Atlas selection mode is invalid.");
  if (options.operation === "upscale") return plan(data, { mode, reason: "operation_excluded" });
  if (options.preservation === "identity") return plan(data, { mode, reason: "identity_reference_protected" });

  const familyId = options.familyId || null;
  const subfamilyId = options.subfamilyId || null;
  if (mode !== "auto" || (familyId && subfamilyId)) {
    if (!familyId || !subfamilyId) throw new AagError("ATLAS_SELECTION_INVALID", "Manual Visual Atlas selection requires a family and subfamily.");
    const key = `${familyId}/${subfamilyId}`;
    if (!data.entries.has(key)) throw new AagError("ATLAS_SELECTION_INVALID", "The selected Visual Atlas style does not exist.");
    return plan(data, { used: true, mode: mode === "auto" ? "manual_taxonomy" : mode, reason: "explicit_user_selection", selections: [selection(data, key, 1, ["manual"])] });
  }

  const normalized = normalizeText(text);
  if (!normalized) return plan(data, { mode: "auto", reason: "empty_request" });
  if (!data.styleCues.some(cue => contains(normalized, cue))) return plan(data, { mode: "auto", reason: "no_style_intent" });
  const scores = new Map();
  for (const [key, aliases] of data.aliasMap) {
    const matched = aliases.filter(alias => contains(normalized, alias));
    if (matched.length) {
      const longest = Math.max(...matched.map(alias => alias.split(" ").length));
      scores.set(key, [Math.min(0.99, 0.88 + 0.02 * longest), matched]);
    }
  }
  for (const key of data.entries.keys()) {
    if (scores.has(key)) continue;
    const [familyId] = key.split("/");
    const familyPhrase = normalizeText(data.familyLabels.get(familyId));
    const subfamilyPhrase = normalizeText(data.subfamilyLabels.get(key));
    if (contains(normalized, subfamilyPhrase)) {
      const confidence = subfamilyPhrase.split(" ").length >= 2 || contains(normalized, familyPhrase) ? 0.75 : 0.72;
      scores.set(key, [confidence, [subfamilyPhrase]]);
    }
  }
  const ranked = [...scores.entries()].sort((a, b) => b[1][0] - a[1][0] || data.entries.get(a[0]).index - data.entries.get(b[0]).index);
  if (!ranked.length || ranked[0][1][0] < data.minimumConfidence) return plan(data, { mode: "auto", reason: "no_reliable_match" });
  if (ranked.length > 1 && ranked[0][1][0] === ranked[1][1][0] && ranked[0][1][0] < 0.88) return plan(data, { mode: "auto", reason: "ambiguous_match" });
  const best = ranked[0][1][0];
  const chosen = ranked.filter(([, [score]]) => score >= Math.max(data.minimumConfidence, best - 0.08)).slice(0, data.topK)
    .map(([key, [score, matched]]) => selection(data, key, score, matched));
  return plan(data, { used: true, mode: "auto", reason: "deterministic_alias_match", selections: chosen });
}

function marker(mode, familyId, subfamilyId) {
  if (!MODES.has(mode) || mode === "auto" || !SAFE_ID.test(familyId || "") || !SAFE_ID.test(subfamilyId || "")) throw new AagError("ATLAS_SELECTION_INVALID", "The Visual Atlas marker fields are invalid.");
  return `${MARKER_PREFIX} mode=${mode} family=${familyId} subfamily=${subfamilyId}`;
}

function parseMarker(value) {
  const match = String(value || "").match(/(?:^|\n)AAG_ATLAS_SELECTION_V1 mode=(manual_taxonomy|manual_browse) family=([a-z0-9]+(?:-[a-z0-9]+)*) subfamily=([a-z0-9]+(?:-[a-z0-9]+)*)(?:\n|$)/u);
  return match ? { mode: match[1], familyId: match[2], subfamilyId: match[3] } : null;
}

function contextFor(selected) {
  if (!selected?.length) return "";
  const styles = selected.map(item => `${item.family_label} → ${item.subfamily_label}: ${item.style_descriptor}`).join(" ");
  return `Visual Atlas style guidance: ${styles} Apply only the visual presentation, medium, composition, lighting, and rendering traits. Keep the user's subject and requested details authoritative; do not copy the Atlas benchmark subject. Human anatomy and identity-preservation constraints remain authoritative.`.slice(0, MAX_CONTEXT_CHARS);
}

function applyToTask(task, { logger } = {}) {
  const explicit = parseMarker(task?._aag_upstream_request);
  let atlasPlan;
  try {
    atlasPlan = select(task?._aag_authoritative_request || task?.request || task?.prompt, {
      mode: explicit?.mode || "auto",
      familyId: explicit?.familyId,
      subfamilyId: explicit?.subfamilyId,
      operation: task?.operation,
      preservation: task?.preservation,
    });
  } catch (error) {
    if (explicit) throw error;
    logger?.(`[AAG-IMAGE] Visual Atlas auto retrieval bypassed: ${String(error?.code || error?.message || "unavailable")}`);
    atlasPlan = plan(null, { mode: "auto", reason: "atlas_unavailable" });
  }
  if (atlasPlan.used) {
    const context = contextFor(atlasPlan.selections);
    task.prompt = `${String(task.prompt || "").trim()}\n\n${context}`;
    atlasPlan.context_chars = context.length;
    atlasPlan.estimated_context_tokens = Math.ceil(Buffer.byteLength(context, "utf8") / 4);
    if (task._aag_prompt_contract) {
      task._aag_prompt_contract = {
        ...task._aag_prompt_contract,
        selective_knowledge: [MODULE],
        pre_knowledge_prompt_sha256: task._aag_prompt_contract.final_prompt_sha256,
        final_prompt_sha256: crypto.createHash("sha256").update(task.prompt).digest("hex"),
      };
    }
  }
  task.atlas = atlasPlan;
  return task;
}

function resetCacheForTests() { cached = null; }

module.exports = { MODULE, PLAN_SCHEMA, MARKER_PREFIX, MAX_CONTEXT_CHARS, normalizeText, load, select, marker, parseMarker, contextFor, applyToTask, resetCacheForTests };
