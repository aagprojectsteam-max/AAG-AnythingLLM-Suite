"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { verify: verifyVisualAtlasProduct } = require("./visual-atlas-product");

const root = path.resolve(__dirname, "..");
const version = fs.readFileSync(path.join(root, "VERSION"), "utf8").trim();
const out = path.join(root, "releases", "staged", version);
const deployedSkills = "/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills";
const modules = ["runtime.js", "batch.js", "errors.js", "util.js", "image.js", "store.js", "scheduler.js", "identity.js", "human-identity.js", "identity-routing.js", "scene-identity.js", "ordinary-policy.js", "prompt-quality.js", "visual-atlas.js", "selective-knowledge.js", "comfy.js", "adapters.js", "recovery.js"];
const humanIdentityPaths = [
  "human-identity/runtime/composition_contract.py",
  "human-identity/runtime/evaluator_core.py",
  "human-identity/runtime/pulid_worker.py",
  "human-identity/runtime/quality_gate.py",
  "human-identity/runtime/reference_validator.py",
  "human-identity/runtime/validate_reference_cli.py",
  "human-identity/bin/process_inbox.py",
  "human-identity/config/CONTRACT-B-FREEZE.json",
  "human-identity/config/CANDIDATE-ASSET-MANIFEST.json",
  "human-identity/config/CANDIDATE-SBOM.json",
  "human-identity/config/PRODUCTION-CONFIG.json",
];
const sceneIdentityPaths = [
  "human-identity-scene/runtime/evaluator_core.py",
  "human-identity-scene/runtime/quality_gate.py",
  "human-identity-scene/runtime/reference_validator.py",
  "human-identity-scene/runtime/scene_worker.py",
  "human-identity-scene/runtime/validate_reference_cli.py",
  "human-identity-scene/bin/process_inbox.py",
  "human-identity-scene/config/SCENE-CONTRACT.json",
  "human-identity-scene/config/PRODUCTION-CONFIG.json",
];
const integrations = [
  {
    source: "integrations/anythingllm/aagArtifactPresentation.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/runtime-context-bridge/aagArtifactPresentation.js"],
  },
  {
    source: "integrations/anythingllm/aagComposerHistory.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/runtime-context-bridge/aagComposerHistory.js"],
  },
  {
    source: "integrations/anythingllm/aagComposerProxy.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/aagComposerProxy.js"],
  },
  {
    source: "integrations/anythingllm/aagImageProgress.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/aagImageProgress.js"],
  },
  {
    source: "integrations/anythingllm/aagPublicToolSchema.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/runtime-context-bridge/aagPublicToolSchema.js"],
  },
  {
    source: "integrations/anythingllm/aagPdfAssembler.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/aagPdfAssembler.js"],
  },
  {
    source: "integrations/anythingllm/aagArtifactExport.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/aagArtifactExport.js"],
  },
  {
    source: "integrations/anythingllm/server-index.js",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/index.js"],
  },
  {
    source: "integrations/upscale-engine/upscale-server.py",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/AAG-Upscale-Engine/service/upscale-server.py"],
  },
  {
    source: "integrations/upscale-engine/image-hub.py",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/AAG-Upscale-Engine/service/image-hub.py"],
  },
  {
    source: "integrations/comfyui-bridge/proxy.py",
    targets: [
      "/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/bridges/comfyui-docker-bridge/proxy.py",
      "/mnt/data/AI/Apps/AnythingLLM/storage/comfyui-docker-bridge/proxy.py",
    ],
  },
  {
    source: "integrations/launchers/aag-ai-start",
    targets: ["/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/bin/aag-ai-start"],
  },
];

// A release is not buildable unless its mandatory Visual Atlas 1.0.0 product
// asset is present and byte-identical to the sealed product manifest.
const visualAtlas = verifyVisualAtlasProduct(path.resolve(root, ".."));

function hash(file) { return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"); }
function record(relative) { return { path: relative, sha256: hash(path.join(root, relative)) }; }

fs.mkdirSync(out, { recursive: true, mode: 0o700 });
const providerFiles = [];
for (const name of ["aag-image-task", "aag-image-batch", "aag-image-job"]) {
  const dest = path.join(out, name);
  fs.mkdirSync(dest, { recursive: true, mode: 0o700 });
  const obsoletePromptWriter = path.join(dest, "prompt-enrichment.js");
  if (fs.existsSync(obsoletePromptWriter)) fs.unlinkSync(obsoletePromptWriter);
  const copies = [
    { file: "plugin.json", source: `skills/${name}/plugin.json` },
    { file: "handler.js", source: `skills/${name}/handler.js` },
    { file: "visual-taxonomy.json", source: "integrations/model-neutral-compatibility/composer/visual-taxonomy.json" },
    ...(name === "aag-image-task" ? [{ file: "provider-task.schema.json", source: "schemas/provider-task.schema.json" }] : []),
    ...(name === "aag-image-batch" ? [{ file: "provider-batch.schema.json", source: "schemas/provider-batch.schema.json" }] : []),
    ...(name === "aag-image-job" ? [{ file: "provider-job.schema.json", source: "schemas/provider-job.schema.json" }] : []),
    ...modules.map(file => ({ file, source: `src/${file}` })),
  ];
  for (const item of copies) {
    const destination = path.join(dest, item.file);
    fs.copyFileSync(path.join(root, item.source), destination);
    fs.chmodSync(destination, 0o600);
    providerFiles.push({
      path: `${name}/${item.file}`,
      source: item.source,
      deploy_target: `${deployedSkills}/${name}/${item.file}`,
      mode: "0600",
      sha256: hash(destination),
    });
  }
}

const sourcePaths = [
  "VERSION", "package.json", "README.md", "CHANGELOG.md",
  "docs/MIGRATION.md", "docs/ROLLBACK.md", "docs/SECURITY.md", "docs/PROMPT-QUALITY-ARCHITECTURE.md", "docs/PROVIDER-NEUTRAL-BOUNDARY.md", "docs/AAG-ARTIFACT-EXPORT.md", "docs/VISUAL-ATLAS-COMPOSER.md", "docs/VISUAL-ATLAS-PACKAGING.md",
  "registry/workflows.json", "schemas/provider-task.schema.json", "schemas/provider-batch.schema.json", "schemas/provider-job.schema.json",
  "skills/aag-image-task/plugin.json", "skills/aag-image-task/handler.js",
  "skills/aag-image-batch/plugin.json", "skills/aag-image-batch/handler.js",
  "skills/aag-image-job/plugin.json", "skills/aag-image-job/handler.js",
  ...modules.map(file => `src/${file}`),
  "tests/runtime.test.js", "tests/scheduler.test.js", "tests/security.test.js", "tests/prompt-quality.test.js", "tests/public-tool-schema.test.js", "tests/integration_security_test.py", "tests/composer-native-chat.test.js", "tests/inline-composer-integration.test.js", "tests/progress-persistence.test.js", "tests/stall-recovery.test.js",
  "tests/visual-atlas.test.js", "tests/governed-update.test.js",
  "tests/test_human_identity_bridge.py", "tests/test_scene_identity_bridge.py", "tests/ordinary-policy.test.js", "tests/artifact-presentation.test.js", "tests/batch.test.js", "tests/artifact-export.test.js", "tests/frontend-export-contract.test.js",
  "tools/build.js", "tools/governed-update.js", "tools/build-anythingllm-frontend.js", "tools/doctor.js", "tools/atlas-token-accounting.js", "tools/atlas-integrity-evidence.js", "tools/atlas-final-evidence.js", "tools/atlas-browser-acceptance.py", "tools/atlas-endpoint-regression.js", "tools/visual-atlas-product.js", "tools/atlas-live-acceptance.js", "tools/candidate-e2e.js", "tools/dynamic-identity-live-acceptance.js", "tools/scene-identity-live-acceptance.js", "tools/quality-semantics-live.js", "tools/multi-image-registration-audit.js", "tools/multi-image-provider-validation.js",
  "ordinary-generation/config/ORDINARY-RECIPE.json",
  "integrations/launchers/aag-image-start",
  "integrations/anythingllm/frontend/ImageGenerationCard/index.jsx",
  "integrations/anythingllm/frontend/HistoricalOutputs/index.jsx",
  "integrations/anythingllm/frontend/AagImageCollection.jsx",
  "integrations/anythingllm/frontend/aagArtifactExport.js",
  "integrations/anythingllm/frontend/AagImageComposerPanel/index.jsx",
  "integrations/anythingllm/frontend/AagImageComposerPanel/styles.css",
  "integrations/anythingllm/frontend/AagImageComposerPanel/localization.js",
  "integrations/anythingllm/frontend/AagImageComposerPanel/heTaxonomyLabels.js",
  "integrations/anythingllm/frontend/AagImageProgress/index.jsx",
  "integrations/anythingllm/frontend/AagImageProgress/styles.css",
  "integrations/model-neutral-compatibility/compatibility.py",
  "integrations/model-neutral-compatibility/composer_canonical.py",
  "integrations/model-neutral-compatibility/visual_atlas.py",
  "integrations/model-neutral-compatibility/server.py",
  "integrations/model-neutral-compatibility/test_compatibility.py",
  "integrations/model-neutral-compatibility/test_composer_canonical.py",
  "integrations/model-neutral-compatibility/test_http_boundary.py",
  "integrations/model-neutral-compatibility/test_visual_atlas.py",
  "integrations/model-neutral-compatibility/aag-model-compatibility.service",
  "integrations/model-neutral-compatibility/composer/index.html",
  "integrations/model-neutral-compatibility/composer/app.css",
  "integrations/model-neutral-compatibility/composer/app.js",
  "integrations/model-neutral-compatibility/composer/visual-taxonomy.json",
  ...humanIdentityPaths,
  ...sceneIdentityPaths,
  "scene-identity-v1/DEPLOYED-ACCEPTANCE-EVIDENCE.json",
  "../docs/AAG-IMAGE-SYSTEM-CANONICAL-MAP.md",
  "../visual-atlas/README.md",
  "../visual-atlas/manifest/atlas-manifest.json",
  "../visual-atlas/manifest/preview-index.json",
  "../visual-atlas/manifest/retrieval-aliases.json",
  "../visual-atlas/manifest/product-assets.json",
  "../visual-atlas/state/atlas-state.json",
  "../visual-atlas/reports/completeness-audit.json",
  "../visual-atlas/test/atlas.test.js",
  "../systemd/user/aag-human-identity-scene-bridge.path",
  "../systemd/user/aag-human-identity-scene-bridge.service",
  ...integrations.map(item => item.source),
];
const sourceFiles = sourcePaths.map(record);
const integrationFiles = integrations.map(item => ({ ...item, mode: "0755", sha256: hash(path.join(root, item.source)) }));
const active = ["aag-image-task", "aag-image-batch", "aag-image-job"].every(name => JSON.parse(fs.readFileSync(path.join(out, name, "plugin.json"))).active === true);
const visualAtlasRequirement = {
  schema: "aag.visual-atlas.release-requirement.v1",
  mandatory: true,
  atlas_version: visualAtlas.atlas_version,
  families: visualAtlas.expected_families,
  styles: visualAtlas.expected_styles,
  canonical_product_path: "../../visual-atlas",
  product_manifest_path: "../../visual-atlas/manifest/product-assets.json",
  product_manifest_sha256: visualAtlas.product_manifest_sha256,
  reference_set_sha256: visualAtlas.reference_set_sha256,
  thumbnail_set_sha256: visualAtlas.thumbnail_set_sha256,
  install_gate: "node image-agent/tools/visual-atlas-product.js",
  separated_bundle_restore:
    "node image-agent/tools/visual-atlas-product.js --install-from <bundle-root>",
};
fs.writeFileSync(
  path.join(out, "VISUAL-ATLAS-ASSET-REQUIREMENT.json"),
  `${JSON.stringify(visualAtlasRequirement, null, 2)}\n`,
  { mode: 0o600 }
);
const manifest = {
  schema_version: 2,
  version,
  maturity: "Preview",
  built_at: new Date().toISOString(),
  active,
  human_identity: "active-portrait-b-and-bounded-scene-c",
  contract_b_sha256: "d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4",
  scene_contract_sha256: "09c8869e0f9d7099ee4a8b2bce6c8c041e449becb5924240a950352a14b18de6",
  modules,
  human_identity_files: humanIdentityPaths.map(record),
  scene_identity_files: sceneIdentityPaths.map(record),
  visual_atlas: {
    version: visualAtlas.atlas_version,
    mandatory: true,
    packaging: "canonical-product-root-or-versioned-asset-bundle",
    canonical_root: "../../visual-atlas",
    product_manifest: "../../visual-atlas/manifest/product-assets.json",
    product_manifest_sha256: visualAtlas.product_manifest_sha256,
    families: visualAtlas.expected_families,
    styles: visualAtlas.expected_styles,
    references_valid: visualAtlas.gate.REFERENCES_VALID,
    thumbnails_valid: visualAtlas.gate.THUMBNAILS_VALID,
    reference_set_sha256: visualAtlas.reference_set_sha256,
    thumbnail_set_sha256: visualAtlas.thumbnail_set_sha256,
  },
  provider_files: providerFiles,
  integration_files: integrationFiles,
  source_files: sourceFiles,
  workflow_registry_sha256: hash(path.join(root, "registry/workflows.json")),
  provider_schema_sha256: {
    task: hash(path.join(root, "schemas/provider-task.schema.json")),
    batch: hash(path.join(root, "schemas/provider-batch.schema.json")),
    job: hash(path.join(root, "schemas/provider-job.schema.json")),
  },
};
fs.writeFileSync(path.join(out, "STAGED-MANIFEST.json"), JSON.stringify(manifest, null, 2) + "\n", { mode: 0o600 });
console.log(out);
