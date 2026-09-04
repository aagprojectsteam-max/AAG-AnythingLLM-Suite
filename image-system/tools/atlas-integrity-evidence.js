#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const project = path.resolve(__dirname, "../..");
const atlasRoot = path.join(project, "visual-atlas");
const output = path.resolve(process.argv[2] || path.join(project, "evaluation/visual-atlas-integrity.json"));
const manifestFile = path.join(atlasRoot, "manifest/atlas-manifest.json");
const taxonomyFile = path.join(project, "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json");
const aliasFile = path.join(atlasRoot, "manifest/retrieval-aliases.json");
const stateFile = path.join(atlasRoot, "state/atlas-state.json");

function sha(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function readJson(file) { return JSON.parse(fs.readFileSync(file, "utf8")); }
function safeFile(relative, suffix) {
  if (path.isAbsolute(relative) || !relative.endsWith(suffix)) throw new Error(`Unsafe Atlas path: ${relative}`);
  const file = path.resolve(atlasRoot, relative);
  if (!file.startsWith(`${atlasRoot}${path.sep}`)) throw new Error(`Escaping Atlas path: ${relative}`);
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`Invalid Atlas file: ${relative}`);
  return { file, stat };
}
function walk(root, suffix) {
  const found = [];
  for (const item of fs.readdirSync(root, { withFileTypes: true })) {
    const file = path.join(root, item.name);
    if (item.isDirectory()) found.push(...walk(file, suffix));
    else if (item.isFile() && item.name.endsWith(suffix)) found.push(file);
  }
  return found;
}

const manifest = readJson(manifestFile);
const taxonomy = readJson(taxonomyFile);
const aliases = readJson(aliasFile);
const state = readJson(stateFile);
const canonical = new Set();
for (const family of taxonomy.families || []) {
  for (const subfamily of family.subfamilies || []) canonical.add(`${family.id}/${subfamily.id}`);
}
const referenceRows = [];
const thumbnailRows = [];
const seen = new Set();
for (const entry of manifest.entries || []) {
  const key = `${entry.family_id}/${entry.subfamily_id}`;
  if (!canonical.has(key) || seen.has(key) || entry.status !== "COMPLETED") throw new Error(`Invalid manifest entry: ${key}`);
  seen.add(key);
  const preview = safeFile(entry.output_path, ".png");
  const previewBytes = fs.readFileSync(preview.file);
  const previewSha = sha(previewBytes);
  const width = previewBytes.readUInt32BE(16);
  const height = previewBytes.readUInt32BE(20);
  if (previewSha !== entry.sha256 || width !== 512 || height !== 512) throw new Error(`Reference integrity failed: ${key}`);
  referenceRows.push(`${key}\0${previewSha}\0${preview.stat.size}\n`);

  const thumbnail = safeFile(entry.thumbnail_path, ".webp");
  const thumbnailBytes = fs.readFileSync(thumbnail.file);
  if (thumbnail.stat.size < 128 || thumbnailBytes.subarray(0, 4).toString("ascii") !== "RIFF" || thumbnailBytes.subarray(8, 12).toString("ascii") !== "WEBP") throw new Error(`Thumbnail integrity failed: ${key}`);
  thumbnailRows.push(`${key}\0${sha(thumbnailBytes)}\0${thumbnail.stat.size}\n`);
}
const diskReferences = walk(path.join(atlasRoot, "images"), "preview.png");
const diskThumbnails = walk(path.join(atlasRoot, "thumbs"), ".webp");
const checks = {
  canonical_styles_493: canonical.size === 493,
  manifest_entries_493: manifest.entries?.length === 493,
  completed_entries_493: seen.size === 493,
  canonical_manifest_bijection: canonical.size === seen.size && [...canonical].every(key => seen.has(key)),
  reference_files_493: diskReferences.length === 493,
  reference_hashes_and_dimensions_valid: referenceRows.length === 493,
  thumbnail_files_493: diskThumbnails.length === 493,
  thumbnails_nonempty_riff_webp: thumbnailRows.length === 493,
  taxonomy_hash_matches_manifest: sha(fs.readFileSync(taxonomyFile)) === manifest.taxonomy_sha256,
  state_manifest_hash_matches: sha(fs.readFileSync(manifestFile)) === state.manifest_sha256,
  state_complete_and_idle: state.total === 493 && state.completed === 493 && state.pending === 0 && state.queued === 0 && state.generating === 0 && state.failed_retryable === 0 && state.failed_final === 0 && state.engine_health === "HEALTHY" && state.xpu_lane === "IDLE",
  thermal_policy_preserved: state.thermal_submit_limit_c === 100 && state.thermal_resume_c === 95 && state.thermal_critical_c === 105,
  alias_layer_targets_atlas_v1: aliases.atlas_version === manifest.atlas_version,
};
if (Object.values(checks).some(value => value !== true)) throw new Error(`Atlas integrity checks failed: ${JSON.stringify(checks)}`);
const repairedKey = "3d-cgi/game-environment";
const repairedIndex = (manifest.entries || []).findIndex(entry => `${entry.family_id}/${entry.subfamily_id}` === repairedKey);
const repairedThumbnail = thumbnailRows[repairedIndex].split("\0");
const report = {
  schema: "aag.visual-atlas.integration-integrity.v1",
  captured_at: new Date().toISOString(),
  atlas_version: manifest.atlas_version,
  taxonomy_sha256: sha(fs.readFileSync(taxonomyFile)),
  manifest_sha256: sha(fs.readFileSync(manifestFile)),
  alias_sha256: sha(fs.readFileSync(aliasFile)),
  reference_count: referenceRows.length,
  reference_total_bytes: (manifest.entries || []).reduce((sum, entry) => sum + fs.statSync(path.resolve(atlasRoot, entry.output_path)).size, 0),
  reference_set_sha256: sha(referenceRows.sort().join("")),
  thumbnail_count: thumbnailRows.length,
  thumbnail_total_bytes: (manifest.entries || []).reduce((sum, entry) => sum + fs.statSync(path.resolve(atlasRoot, entry.thumbnail_path)).size, 0),
  thumbnail_set_sha256: sha(thumbnailRows.sort().join("")),
  repaired_derivative: { key: repairedKey, sha256: repairedThumbnail[1], bytes: Number(repairedThumbnail[2]?.trim()) },
  preserved_runner_state: state.runner_state,
  checks,
  result: "PASS",
};
fs.mkdirSync(path.dirname(output), { recursive: true, mode: 0o700 });
fs.writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
