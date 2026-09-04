#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const DEFAULT_PROJECT_ROOT = path.resolve(__dirname, "../..");
const EXPECTED_VERSION = "1.0.0";
const EXPECTED_FAMILIES = 28;
const EXPECTED_STYLES = 493;
const PRODUCT_MANIFEST = "visual-atlas/manifest/product-assets.json";
const REQUIRED_METADATA = Object.freeze([
  "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json",
  "visual-atlas/manifest/atlas-manifest.json",
  "visual-atlas/manifest/preview-index.json",
  "visual-atlas/manifest/retrieval-aliases.json",
  "visual-atlas/state/atlas-state.json",
  "visual-atlas/reports/completeness-audit.json",
  "visual-atlas/README.md",
]);

function sha256Bytes(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function sha256File(file) {
  return sha256Bytes(fs.readFileSync(file));
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function canonicalFile(projectRoot, relative, suffix = "") {
  if (path.isAbsolute(relative) || (suffix && !relative.endsWith(suffix)))
    throw new Error(`Unsafe Visual Atlas product path: ${relative}`);
  const resolved = path.resolve(projectRoot, relative);
  if (!resolved.startsWith(`${path.resolve(projectRoot)}${path.sep}`))
    throw new Error(`Visual Atlas product path escapes its product root: ${relative}`);
  const stat = fs.lstatSync(resolved);
  if (!stat.isFile() || stat.isSymbolicLink())
    throw new Error(`Visual Atlas product asset is not a regular file: ${relative}`);
  return { resolved, stat };
}

function assetSetHash(rows) {
  return sha256Bytes([...rows].sort().join(""));
}

function inventory(projectRoot) {
  const atlasRoot = path.join(projectRoot, "visual-atlas");
  const taxonomyPath = path.join(
    projectRoot,
    "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json"
  );
  const manifestPath = path.join(atlasRoot, "manifest/atlas-manifest.json");
  const previewIndexPath = path.join(atlasRoot, "manifest/preview-index.json");
  const aliasesPath = path.join(atlasRoot, "manifest/retrieval-aliases.json");
  const statePath = path.join(atlasRoot, "state/atlas-state.json");
  const manifest = readJson(manifestPath);
  const taxonomy = readJson(taxonomyPath);
  const previewIndex = readJson(previewIndexPath);
  const aliases = readJson(aliasesPath);
  const state = readJson(statePath);
  const taxonomyKeys = new Set();
  for (const family of taxonomy.families || []) {
    for (const style of family.subfamilies || []) {
      const key = `${family.id}/${style.id}`;
      if (taxonomyKeys.has(key)) throw new Error(`Duplicate taxonomy style: ${key}`);
      taxonomyKeys.add(key);
    }
  }
  const entries = [];
  const manifestKeys = new Set();
  for (const entry of manifest.entries || []) {
    const key = `${entry.family_id}/${entry.subfamily_id}`;
    if (manifestKeys.has(key) || !taxonomyKeys.has(key) || entry.status !== "COMPLETED")
      throw new Error(`Invalid Visual Atlas manifest entry: ${key}`);
    manifestKeys.add(key);
    const referencePath = `visual-atlas/${entry.output_path}`;
    const thumbnailPath = `visual-atlas/${entry.thumbnail_path}`;
    const reference = canonicalFile(projectRoot, referencePath, ".png");
    const thumbnail = canonicalFile(projectRoot, thumbnailPath, ".webp");
    const referenceBytes = fs.readFileSync(reference.resolved);
    const thumbnailBytes = fs.readFileSync(thumbnail.resolved);
    const referenceSha256 = sha256Bytes(referenceBytes);
    const thumbnailSha256 = sha256Bytes(thumbnailBytes);
    if (
      referenceSha256 !== entry.sha256 ||
      referenceBytes.length < 24 ||
      referenceBytes.readUInt32BE(16) !== 512 ||
      referenceBytes.readUInt32BE(20) !== 512
    )
      throw new Error(`Canonical reference integrity failed: ${key}`);
    if (
      thumbnailBytes.length < 128 ||
      thumbnailBytes.subarray(0, 4).toString("ascii") !== "RIFF" ||
      thumbnailBytes.subarray(8, 12).toString("ascii") !== "WEBP"
    )
      throw new Error(`Canonical thumbnail integrity failed: ${key}`);
    entries.push({
      key,
      reference: {
        path: referencePath,
        sha256: referenceSha256,
        bytes: reference.stat.size,
      },
      thumbnail: {
        path: thumbnailPath,
        sha256: thumbnailSha256,
        bytes: thumbnail.stat.size,
      },
    });
  }
  entries.sort((left, right) => left.key.localeCompare(right.key));
  const metadata = Object.fromEntries(
    REQUIRED_METADATA.map((relative) => [
      relative,
      {
        sha256: sha256File(path.join(projectRoot, relative)),
        bytes: fs.statSync(path.join(projectRoot, relative)).size,
      },
    ])
  );
  const referenceRows = entries.map(
    (entry) => `${entry.key}\0${entry.reference.sha256}\0${entry.reference.bytes}\n`
  );
  const thumbnailRows = entries.map(
    (entry) => `${entry.key}\0${entry.thumbnail.sha256}\0${entry.thumbnail.bytes}\n`
  );
  return {
    atlasRoot,
    manifest,
    taxonomy,
    previewIndex,
    aliases,
    state,
    taxonomyKeys,
    manifestKeys,
    entries,
    metadata,
    referenceSetSha256: assetSetHash(referenceRows),
    thumbnailSetSha256: assetSetHash(thumbnailRows),
  };
}

function sealedManifest(projectRoot) {
  const data = inventory(projectRoot);
  return {
    schema: "aag.visual-atlas.product-assets.v1",
    atlas_version: EXPECTED_VERSION,
    expected_families: EXPECTED_FAMILIES,
    expected_styles: EXPECTED_STYLES,
    packaging: {
      mode: "canonical-product-root",
      canonical_root: "visual-atlas",
      runtime_mount: "/app/server/storage/aag-visual-atlas",
      normal_runtime_access: "read-only",
      separated_asset_bundle: "mandatory-versioned-product-component",
    },
    canonical_locations: {
      taxonomy:
        "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json",
      manifest: "visual-atlas/manifest/atlas-manifest.json",
      preview_index: "visual-atlas/manifest/preview-index.json",
      retrieval_aliases: "visual-atlas/manifest/retrieval-aliases.json",
      state: "visual-atlas/state/atlas-state.json",
      references: "visual-atlas/images/<family>/<subfamily>/preview.png",
      thumbnails: "visual-atlas/thumbs/<family>/<subfamily>.webp",
    },
    metadata: data.metadata,
    reference_set_sha256: data.referenceSetSha256,
    thumbnail_set_sha256: data.thumbnailSetSha256,
    entries: data.entries,
  };
}

function seal(projectRoot = DEFAULT_PROJECT_ROOT) {
  const target = path.join(projectRoot, PRODUCT_MANIFEST);
  const temporary = `${target}.tmp-${process.pid}`;
  const document = sealedManifest(projectRoot);
  fs.writeFileSync(temporary, `${JSON.stringify(document, null, 2)}\n`, {
    mode: 0o644,
    flag: "wx",
  });
  fs.renameSync(temporary, target);
  return verify(projectRoot);
}

function verify(projectRoot = DEFAULT_PROJECT_ROOT) {
  const data = inventory(projectRoot);
  const productManifestPath = path.join(projectRoot, PRODUCT_MANIFEST);
  const product = readJson(productManifestPath);
  const expected = sealedManifest(projectRoot);
  const checks = {
    atlas_version_1_0_0:
      data.manifest.atlas_version === EXPECTED_VERSION &&
      data.state.atlas_version === EXPECTED_VERSION &&
      product.atlas_version === EXPECTED_VERSION,
    families_28: data.taxonomy.families?.length === EXPECTED_FAMILIES,
    styles_493:
      data.taxonomyKeys.size === EXPECTED_STYLES &&
      data.manifestKeys.size === EXPECTED_STYLES &&
      data.entries.length === EXPECTED_STYLES,
    taxonomy_manifest_bijection:
      data.taxonomyKeys.size === data.manifestKeys.size &&
      [...data.taxonomyKeys].every((key) => data.manifestKeys.has(key)),
    taxonomy_hash_matches_manifest:
      data.metadata[
        "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json"
      ].sha256 === data.manifest.taxonomy_sha256,
    state_manifest_hash_matches:
      data.metadata["visual-atlas/manifest/atlas-manifest.json"].sha256 ===
      data.state.manifest_sha256,
    state_complete_healthy_idle:
      data.state.total === EXPECTED_STYLES &&
      data.state.completed === EXPECTED_STYLES &&
      data.state.pending === 0 &&
      data.state.queued === 0 &&
      data.state.generating === 0 &&
      data.state.failed_retryable === 0 &&
      data.state.failed_final === 0 &&
      data.state.engine_health === "HEALTHY" &&
      data.state.xpu_lane === "IDLE",
    thermal_policy_preserved:
      data.state.thermal_submit_limit_c === 100 &&
      data.state.thermal_resume_c === 95 &&
      data.state.thermal_critical_c === 105,
    preview_index_complete:
      Array.isArray(data.previewIndex.entries) &&
      data.previewIndex.entries.length === EXPECTED_STYLES,
    aliases_versioned: data.aliases.atlas_version === EXPECTED_VERSION,
    product_manifest_exact:
      JSON.stringify(product) === JSON.stringify(expected),
    references_valid_493: data.entries.length === EXPECTED_STYLES,
    thumbnails_valid_493: data.entries.length === EXPECTED_STYLES,
  };
  const failed = Object.entries(checks)
    .filter(([, passed]) => !passed)
    .map(([name]) => name);
  if (failed.length) throw new Error(`Visual Atlas product gate failed: ${failed.join(", ")}`);
  return {
    schema: "aag.visual-atlas.product-gate.v1",
    captured_at: new Date().toISOString(),
    atlas_version: EXPECTED_VERSION,
    canonical_product_root: path.resolve(projectRoot),
    product_manifest: PRODUCT_MANIFEST,
    product_manifest_sha256: sha256File(productManifestPath),
    expected_families: EXPECTED_FAMILIES,
    expected_styles: EXPECTED_STYLES,
    reference_set_sha256: data.referenceSetSha256,
    thumbnail_set_sha256: data.thumbnailSetSha256,
    gate: {
      EXPECTED_ATLAS_STYLES: EXPECTED_STYLES,
      REFERENCES_VALID: `${data.entries.length}/${EXPECTED_STYLES}`,
      THUMBNAILS_VALID: `${data.entries.length}/${EXPECTED_STYLES}`,
      MANIFEST_INTEGRITY: "PASS",
    },
    checks,
    result: "PASS",
  };
}

function copyFile(sourceRoot, destinationRoot, relative) {
  const source = canonicalFile(sourceRoot, relative).resolved;
  const destination = path.join(destinationRoot, relative);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
}

function exportBundle(projectRoot, destination) {
  const report = verify(projectRoot);
  if (fs.existsSync(destination)) throw new Error(`Export destination already exists: ${destination}`);
  fs.mkdirSync(destination, { recursive: true, mode: 0o755 });
  const product = readJson(path.join(projectRoot, PRODUCT_MANIFEST));
  const files = new Set([...REQUIRED_METADATA, PRODUCT_MANIFEST]);
  for (const entry of product.entries) {
    files.add(entry.reference.path);
    files.add(entry.thumbnail.path);
  }
  try {
    for (const relative of [...files].sort()) copyFile(projectRoot, destination, relative);
    verify(destination);
  } catch (error) {
    throw new Error(
      `Incomplete export remains at ${destination}; remove it after inspection. ${error.message}`
    );
  }
  return { ...report, exported_to: destination, exported_files: files.size };
}

function installFrom(bundleRoot, projectRoot) {
  const sourceReport = verify(bundleRoot);
  const sourceTaxonomy = path.join(
    bundleRoot,
    "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json"
  );
  const destinationTaxonomy = path.join(
    projectRoot,
    "image-agent/integrations/model-neutral-compatibility/composer/visual-taxonomy.json"
  );
  if (!fs.existsSync(destinationTaxonomy) || sha256File(destinationTaxonomy) !== sha256File(sourceTaxonomy))
    throw new Error("Installed AAG code taxonomy does not match Visual Atlas 1.0.0");
  const destination = path.join(projectRoot, "visual-atlas");
  if (fs.existsSync(destination)) return verify(projectRoot);
  const staging = path.join(projectRoot, `.visual-atlas.install-${process.pid}`);
  fs.cpSync(path.join(bundleRoot, "visual-atlas"), staging, {
    recursive: true,
    errorOnExist: true,
    force: false,
  });
  fs.renameSync(staging, destination);
  try {
    return verify(projectRoot);
  } catch (error) {
    fs.renameSync(destination, `${destination}.failed-${Date.now()}`);
    throw error;
  }
}

function parseCli(argv) {
  const projectFlag = argv.indexOf("--project-root");
  const projectRoot =
    projectFlag >= 0 ? path.resolve(argv[projectFlag + 1]) : DEFAULT_PROJECT_ROOT;
  if (argv.includes("--seal")) return seal(projectRoot);
  const exportFlag = argv.indexOf("--export");
  if (exportFlag >= 0) return exportBundle(projectRoot, path.resolve(argv[exportFlag + 1]));
  const installFlag = argv.indexOf("--install-from");
  if (installFlag >= 0) return installFrom(path.resolve(argv[installFlag + 1]), projectRoot);
  return verify(projectRoot);
}

if (require.main === module) {
  try {
    const report = parseCli(process.argv.slice(2));
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
    process.stdout.write(`EXPECTED_ATLAS_STYLES=${report.gate?.EXPECTED_ATLAS_STYLES || EXPECTED_STYLES}\n`);
    process.stdout.write(`REFERENCES_VALID=${report.gate?.REFERENCES_VALID || `${EXPECTED_STYLES}/${EXPECTED_STYLES}`}\n`);
    process.stdout.write(`THUMBNAILS_VALID=${report.gate?.THUMBNAILS_VALID || `${EXPECTED_STYLES}/${EXPECTED_STYLES}`}\n`);
    process.stdout.write("MANIFEST_INTEGRITY=PASS\n");
  } catch (error) {
    process.stderr.write(`MANIFEST_INTEGRITY=FAIL\n${error.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = {
  EXPECTED_STYLES,
  PRODUCT_MANIFEST,
  exportBundle,
  installFrom,
  seal,
  verify,
};
