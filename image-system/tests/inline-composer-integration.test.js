"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const frontend = path.join(
  root,
  "integrations/anythingllm/frontend/AagImageComposerPanel/index.jsx"
);
const localization = path.join(
  root,
  "integrations/anythingllm/frontend/AagImageComposerPanel/localization.js"
);
const hebrewTaxonomy = path.join(
  root,
  "integrations/anythingllm/frontend/AagImageComposerPanel/heTaxonomyLabels.js"
);
const proxy = path.join(root, "integrations/anythingllm/aagComposerProxy.js");
const server = path.join(root, "integrations/anythingllm/server-index.js");
const build = path.join(root, "tools/build-anythingllm-frontend.js");
const externalComposer = path.join(
  root,
  "integrations/model-neutral-compatibility/composer/index.html"
);
const taxonomy = path.join(
  root,
  "integrations/model-neutral-compatibility/composer/visual-taxonomy.json"
);

test("inline Composer exposes the full canonical taxonomy and dynamic selectors", () => {
  const catalog = JSON.parse(fs.readFileSync(taxonomy, "utf8"));
  const source = fs.readFileSync(frontend, "utf8");
  assert.equal(catalog.families.length, 28);
  assert.equal(
    catalog.families.reduce((total, family) => total + family.subfamilies.length, 0),
    493
  );
  assert.doesNotMatch(source, /data-testid="aag-family-search"/);
  assert.doesNotMatch(source, /data-testid="aag-subfamily-search"/);
  assert.doesNotMatch(source, /Find a visual family|Find a subfamily|Type to filter/);
  assert.match(source, /data-testid="aag-visual-family"/);
  assert.match(source, /data-testid="aag-visual-subfamily"/);
  assert.match(source, /\(taxonomy\?\.families \|\| \[\]\)\.map/);
  assert.match(source, /\(family\?\.subfamilies \|\| \[\]\)\.map/);
  assert.doesNotMatch(
    source,
    /Promise\.all\(\[api\("taxonomy"\), establishSession\(\)\]\)/,
    "taxonomy rendering must not depend on session establishment"
  );
  assert.match(source, /const catalog = await api\("taxonomy"\)/);
  assert.match(source, /establishSession\(\)\.catch\(\(error\)/);
  assert.match(source, /window\.setTimeout\(loadTaxonomy, retryDelay\)/);
  assert.match(source, /window\.clearTimeout\(retryTimer\)/);
  assert.doesNotMatch(source, /taxonomyReload|taxonomyLoading/);
  assert.doesNotMatch(source, /Taxonomy unavailable|retryFamilies/);
  assert.match(source, /"X-AAG-Workspace-Path": window\.location\.pathname/);
  assert.match(source, /"X-AAG-Workspace-Slug": "image-generator"/);
  assert.match(source, /disabled=\{!taxonomy\}/);
  assert.match(source, /taxonomy\.families\.length/);
  assert.doesNotMatch(source, /help=\{tr\("familyCount", "28 families available"\)\}/);
  assert.match(source, /visualSubfamily: "auto"/);
  assert.match(source, /Choose an existing visual family from the list/);
  assert.match(source, /Choose an existing subfamily for the selected family/);
  assert.match(source, /RECENTS_KEY = "aag\.image-composer\.v1\.1\.recent-styles"/);
});

test("inline Composer localizes labels without changing canonical taxonomy values", () => {
  const catalog = JSON.parse(fs.readFileSync(taxonomy, "utf8"));
  const source = fs.readFileSync(frontend, "utf8");
  const localeSource = fs.readFileSync(localization, "utf8");
  const hebrewSource = fs.readFileSync(hebrewTaxonomy, "utf8");
  const match = hebrewSource.match(/Object\.freeze\(\n([\s\S]*?)\n\);/);
  assert.ok(match, "Hebrew taxonomy label dictionary must be parseable");
  const labels = JSON.parse(match[1]);
  assert.equal(Object.keys(labels).length, 28 + 493);
  for (const family of catalog.families) {
    assert.equal(typeof labels[`family/${family.id}`], "string");
    for (const subfamily of family.subfamilies)
      assert.equal(typeof labels[`${family.id}/${subfamily.id}`], "string");
  }
  assert.equal(labels["family/photography"], "צילום");
  assert.equal(labels["photography/editorial"], "דיוקן מערכתי");
  assert.match(source, /data-testid="aag-ui-language"/);
  assert.match(source, /dir=\{language === "he" \? "rtl" : "ltr"\}/);
  assert.match(localeSource, /DEFAULT_UI_LANGUAGE = "en"/);
  assert.match(localeSource, /localStorage\.setItem\(UI_LANGUAGE_KEY/);
  assert.match(hebrewSource, /Keys reference canonical Composer IDs; values are never submitted/);
});

test("Visual Atlas browser uses canonical catalog thumbnails and bounded lazy rendering", () => {
  const source = fs.readFileSync(frontend, "utf8");
  const styles = fs.readFileSync(
    path.join(root, "integrations/anythingllm/frontend/AagImageComposerPanel/styles.css"),
    "utf8"
  );
  const localeSource = fs.readFileSync(localization, "utf8");
  assert.match(source, /data-testid="aag-browse-visual-atlas"/);
  assert.match(source, /data-testid="aag-visual-atlas-browser"/);
  assert.match(source, /data-testid="aag-atlas-search"/);
  assert.match(source, /data-testid="aag-atlas-family-filter"/);
  assert.match(source, /data-testid="aag-atlas-grid"/);
  assert.match(source, /matches\.slice\(0, limit\)/);
  assert.match(source, /setAtlasLimit\(48\)/);
  assert.match(source, /function AtlasImage/);
  assert.match(source, /IntersectionObserver/);
  assert.match(source, /rootMargin: "240px"/);
  assert.match(source, /fetch\(protectedUrl/);
  assert.match(source, /\.\.\.baseHeaders\(\)/);
  assert.match(source, /URL\.createObjectURL\(blob\)/);
  assert.match(source, /URL\.revokeObjectURL\(localBlobUrl\)/);
  assert.match(source, /data-atlas-image-state/);
  assert.match(source, /data-atlas-asset-url/);
  assert.match(source, /webp192-v1/);
  assert.match(source, /png512-v1/);
  assert.match(source, /atlas\?\.thumbnail_sha256/);
  assert.doesNotMatch(source, /<img\s+src=\{atlasAssetUrl/);
  assert.match(source, /decoding="async"/);
  assert.match(source, /atlas-thumbnail/);
  assert.match(source, /atlas-preview/);
  assert.doesNotMatch(source, /output_path|thumbnail_path|images\//);
  assert.match(source, /\.\.\.\(style\.aliases \|\| \[\]\)/);
  assert.match(source, /taxonomyLabel\(language/);
  assert.match(styles, /\.aag-atlas-grid/);
  assert.match(styles, /grid-template-columns: repeat\(auto-fill/);
  assert.match(styles, /grid-auto-rows: max-content/);
  assert.match(styles, /align-content: start/);
  assert.match(localeSource, /עיון באטלס החזותי/);
  assert.match(localeSource, /חיפוש סגנונות/);
});

test("manual Atlas selection is visible, clearable, scoped, and authoritative", () => {
  const source = fs.readFileSync(frontend, "utf8");
  assert.match(source, /atlasSelectionMode: "auto"/);
  assert.match(source, /"manual_taxonomy"/);
  assert.match(source, /"manual_browse"/);
  assert.match(source, /atlas_selection_mode:/);
  assert.match(source, /data-testid="aag-selected-atlas-style"/);
  assert.match(source, /data-testid="aag-clear-atlas-style"/);
  assert.match(source, /selectedStyle\.description/);
  assert.match(source, /threadScopeRef/);
  assert.match(source, /previous && previous !== threadSlug/);
  assert.match(source, /visualFamily: "auto"/);
  assert.match(source, /visualSubfamily: "auto"/);
});

test("Atlas UX v2 synchronizes canonical selection from every visual result", () => {
  const source = fs.readFileSync(frontend, "utf8");
  assert.match(source, /function chooseAtlasStyle\(familyId, subfamilyId\)/);
  assert.match(source, /visualFamily: familyId/);
  assert.match(source, /visualSubfamily: subfamilyId/);
  assert.match(source, /atlasSelectionMode: "manual_browse"/);
  assert.match(source, /onSelect=\{chooseAtlasStyle\}/);
  assert.match(source, /onClick=\{\(\) => onSelect\(family\.id, style\.id\)\}/);
  assert.match(source, /visual_family:\s*showsCreativeStyle \? settings\.visualFamily/);
  assert.match(source, /visual_subfamily:\s*showsCreativeStyle \? settings\.visualSubfamily/);
  assert.match(source, /atlas_selection_mode:/);
  assert.doesNotMatch(source, /family\.label.*visualFamily|style\.label.*visualSubfamily/);
});

test("Atlas UX v2 separates reference inspection from explicit style selection", () => {
  const source = fs.readFileSync(frontend, "utf8");
  assert.match(source, /className="aag-atlas-image-button"/);
  assert.match(source, /onClick=\{\(\) => onInspect\(family, style\)\}/);
  assert.match(source, /function AtlasLightbox/);
  assert.match(source, /data-testid="aag-atlas-large-preview"/);
  assert.match(source, /kind="atlas-preview"/);
  assert.match(source, /assetSha256=\{style\.atlas\?\.sha256\}/);
  assert.match(source, /selectThisStyle/);
  assert.match(source, /chooseAtlasStyle\(previewTarget\.family\.id, previewTarget\.style\.id\)/);
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /previouslyFocused\?\.focus\?\.\(\)/);
  assert.match(source, /event\.target === event\.currentTarget/);
  assert.doesNotMatch(source, /onClick=\{\(\) => onInspect\(family, style\)\}[\s\S]{0,80}onSelect/);
});

test("Atlas UX v2 provides persistent presentation-only responsive sizing", () => {
  const source = fs.readFileSync(frontend, "utf8");
  const styles = fs.readFileSync(
    path.join(root, "integrations/anythingllm/frontend/AagImageComposerPanel/styles.css"),
    "utf8"
  );
  assert.match(source, /ATLAS_SIZES = Object\.freeze\(\["small", "medium", "large"\]\)/);
  assert.match(source, /aag\.image-composer\.v1\.2\.atlas-thumbnail-size/);
  assert.match(source, /data-testid="aag-atlas-size-control"/);
  assert.match(source, /data-thumbnail-size=\{size\}/);
  assert.match(source, /localStorage\.getItem\(ATLAS_SIZE_KEY\)/);
  assert.match(source, /localStorage\.setItem\(ATLAS_SIZE_KEY, value\)/);
  assert.match(source, /return "medium"/);
  assert.match(styles, /--aag-atlas-card-min: 112px/);
  assert.match(styles, /--aag-atlas-card-min: 148px/);
  assert.match(styles, /--aag-atlas-card-min: 200px/);
  assert.match(styles, /repeat\(auto-fill/);
  assert.match(source, /matches\.slice\(0, limit\)/);
  assert.match(source, /<AtlasImage[\s\S]*?lazy/);
  const sizeHandler = source.match(
    /function chooseAtlasSize\(value\) \{([\s\S]*?)\n  \}/
  );
  assert.ok(sizeHandler, "thumbnail-size handler must remain inspectable");
  assert.doesNotMatch(sizeHandler[1], /chooseAtlasStyle|setSettings|prepare\(|request\(/);
});

test("inline Composer keeps all accepted operation and upload controls", () => {
  const source = fs.readFileSync(frontend, "utf8");
  for (const marker of [
    'value="create"',
    'value="batch"',
    'value="reference"',
    'value="transform"',
    'value="upscale"',
    'data-testid="aag-source-upload"',
    'data-testid="aag-source-index"',
    'data-testid="aag-preservation"',
    'dataTestId="aag-upscale-factor"',
  ])
    assert.ok(source.includes(marker), `missing ${marker}`);
  assert.match(source, /mode: "advanced"/);
  assert.match(source, /source_policy:/);
  assert.match(source, /attachments,/);
  assert.match(source, /request\("prepare", payload\)/);
  assert.match(source, /modelMessage: data\.modelMessage/);
  assert.doesNotMatch(source, /request\("submit", payload\)/);
  assert.doesNotMatch(source, /request\("preview", payload\)/);
  assert.doesNotMatch(source, /Preview request|previewRequest/);
  assert.doesNotMatch(source, /window\.location\.assign/);
  assert.doesNotMatch(source, /Submitting through Image Generator/);
  assert.doesNotMatch(source, /AAG_COMPOSER_AUTH_V1|AAG_COMPOSER_INTENT_SIGNATURE_V1/);
});

test("Edit and Upscale expose only governed current-turn or latest-thread sources", () => {
  const source = fs.readFileSync(frontend, "utf8");
  const localeSource = fs.readFileSync(localization, "utf8");
  assert.match(source, /const usesSource = isReference \|\| isEdit \|\| isUpscale/);
  assert.match(source, /findLatestThreadArtifact\(history\)/);
  assert.match(source, /output\?\.type !== "imageGenerationCard"/);
  assert.match(source, /payload\?\.artifactSha256/);
  assert.match(source, /data-testid="aag-source-policy"/);
  assert.match(source, /value="previous_artifact"/);
  assert.match(source, /value="current_attachment"/);
  assert.match(source, /latestThreadArtifact\s*\? "previous_artifact"\s*: "current_attachment"/);
  assert.match(source, /data-testid="aag-previous-artifact-summary"/);
  assert.match(source, /StorageFiles\.image\(latestThreadArtifact\.storageFilename\)/);
  assert.match(source, /!latestThreadArtifact/);
  assert.doesNotMatch(source, /thread_reference|artifact_id.*option/i);
  assert.match(localeSource, /התמונה האחרונה שנוצרה בשרשור זה/);
  assert.match(localeSource, /תמונה שהועלתה/);
});

test("operation-specific UI distinguishes Create, preserve Edit, Restyle, and Upscale", () => {
  const source = fs.readFileSync(frontend, "utf8");
  const localeSource = fs.readFileSync(localization, "utf8");
  assert.match(source, /const isGeneration = !isEdit && !isUpscale/);
  assert.match(source, /const isRestyle = isEdit && settings\.editMode === "restyle"/);
  assert.match(source, /dataTestId="aag-edit-mode"/);
  assert.match(source, /\["preserve", "Preserve current appearance"\]/);
  assert.match(source, /\["restyle", "Restyle image"\]/);
  assert.match(source, /const isIdentityReference =/);
  assert.match(source, /const showsCreativeStyle =/);
  assert.match(source, /\{\(isGeneration \|\| isRestyle\) && showsCreativeStyle && \(/);
  assert.match(source, /\{isGeneration && \(\s*<ComposerGroup title=\{tr\("sizeQuantity"/);
  assert.match(source, /\{\(isGeneration \|\| isEdit \|\| isUpscale\) && \(/);
  assert.match(source, /\{settings\.operation === "create" && \(/);
  assert.match(source, /edit_mode: isEdit \? settings\.editMode : "not_applicable"/);
  assert.match(source, /source_instruction: ""/);
  assert.doesNotMatch(source, /settings\.sourceInstruction|What may change\?|mayChangePlaceholder/);
  assert.match(localeSource, /מצב עריכה/);
  assert.match(localeSource, /שימור המראה הנוכחי/);
  assert.match(localeSource, /שינוי סגנון התמונה/);
});

test("Create from reference reuses governed sources and signed native-chat intent", () => {
  const source = fs.readFileSync(frontend, "utf8");
  const localeSource = fs.readFileSync(localization, "utf8");
  assert.match(source, /value="reference"/);
  assert.match(source, /Create from reference/);
  assert.match(source, /dataTestId="aag-reference-purpose"/);
  assert.match(source, /\["identity", "Preserve person identity"\]/);
  assert.match(source, /\["general_visual", "Preserve general visual reference"\]/);
  assert.match(source, /const usesSource = isReference \|\| isEdit \|\| isUpscale/);
  assert.match(source, /quality:\s*isGeneration && !isIdentityReference/);
  assert.match(source, /quality: referencePurpose === "identity" \? "auto"/);
  assert.match(source, /dataTestId="aag-technical-quality"/);
  assert.match(source, /data-testid="aag-identity-generation-quality"/);
  assert.match(source, /The locked validated identity recipe remains unchanged/);
  assert.match(source, /dataTestId="aag-final-output-quality"/);
  assert.match(source, /\["standard", "Standard"\]/);
  assert.match(source, /\["enhanced_2x", "Enhanced 2×"\]/);
  assert.match(localeSource, /איכות זהות מאומתת/);
  assert.match(localeSource, /מתכון הזהות המאומת והנעול נשאר ללא שינוי/);
  assert.match(source, /StorageFiles\.image\(latestThreadArtifact\.storageFilename\)/);
  assert.match(source, /sha256Blob\(previousArtifactBlob\)/);
  assert.match(source, /artifactSha256 !== latestThreadArtifact\.artifactSha256/);
  assert.match(source, /reference_purpose: isReference/);
  assert.match(source, /reference_source: isReference/);
  assert.match(source, /reference_artifact_sha256: materializesPreviousReference/);
  assert.match(source, /isReference\s*\? "transform"/);
  assert.match(source, /settings\.referencePurpose === "identity"\s*\? "identity"\s*: "subject"/);
  assert.match(source, /data-testid="aag-identity-realistic-capability"/);
  assert.match(source, /Identity preservation currently uses validated realistic rendering/);
  assert.match(source, /UNVALIDATED_IDENTITY_STYLE_CUE\.test\(text\)/);
  assert.match(source, /Person identity preservation currently supports validated realistic rendering only/);
  assert.match(source, /showsCreativeStyle \? settings\.visualFamily : "auto"/);
  assert.match(source, /showsCreativeStyle \? settings\.visualSubfamily : "auto"/);
  assert.match(source, /referencePurpose === "identity" \? "auto" : current\.visualFamily/);
  assert.match(localeSource, /שמירת זהות משתמשת כרגע בעיבוד ריאליסטי שעבר אימות/);
  assert.match(localeSource, /לסגנונות חופשיים יותר/);
  assert.doesNotMatch(source, /filesystem_path|reference_path/);
  assert.match(localeSource, /יצירה מתמונת ייחוס/);
  assert.match(localeSource, /שימור זהות האדם/);
  assert.match(localeSource, /שימור ייחוס חזותי כללי/);
});

test("inline copy is current, explains pixel text, and names native Send as the only submit", () => {
  const source = fs.readFileSync(frontend, "utf8");
  const localeSource = fs.readFileSync(localization, "utf8");
  const externalSource = fs.readFileSync(externalComposer, "utf8");
  const visibleSources = `${source.replace(
    /const RECENTS_KEY = .*?;\n/,
    ""
  )}\n${localeSource}`;
  assert.doesNotMatch(visibleSources, /V1\.1|v1\.1/);
  assert.doesNotMatch(externalSource, /V1\.1|v1\.1/);
  assert.match(source, /Controls text intended to appear inside the image/);
  assert.match(source, /Exact rendered spelling is not guaranteed/);
  assert.match(source, /Current image-engine limits/);
  assert.match(source, /there is no separate Composer submission/);
  assert.match(source, /Composer selections ready/);
  assert.match(localeSource, /שולט בטקסט שאמור להופיע בתוך התמונה/);
  assert.match(localeSource, /אין שליחה נפרדת של ה־Composer/);
  assert.match(externalSource, /COMPOSER UI V1\.2/);
  assert.match(externalSource, /Controls text intended to appear inside the image/);
});

test("proxy is authenticated, Image-Generator-only, fixed-route, and secret-free", () => {
  const source = fs.readFileSync(proxy, "utf8");
  assert.match(source, /validatedRequest/);
  assert.match(source, /IMAGE_WORKSPACE = "image-generator"/);
  assert.match(source, /validWorkspaceSlug/);
  assert.match(source, /sameOriginImageWorkspaceOnly/);
  assert.match(source, /function browserOrigin\(request\)/);
  assert.match(source, /request\.get\("x-forwarded-proto"\)/);
  assert.match(source, /\["http", "https"\]\.includes\(forwardedProtocol\)/);
  assert.match(source, /const expectedOrigin = browserOrigin\(request\)/);
  assert.match(source, /const explicitWorkspacePath = request\.get\("x-aag-workspace-path"\)/);
  assert.match(source, /const authenticatedWorkspaceFetch/);
  assert.match(source, /request\.method === "GET" && !referer && !authenticatedWorkspaceFetch/);
  assert.match(source, /workspacePath === "\/" && workspaceSlug === IMAGE_WORKSPACE/);
  assert.match(source, /\/workspace\\\/image-generator/);
  assert.match(source, /\(\?:t\|thread\)/);
  assert.match(source, /socketPath: SOCKET_PATH/);
  for (const route of [
    "/composer/session",
    "/composer/visual-taxonomy.json",
    "/composer/preview",
    "/composer/prepare",
    "/composer/submit",
  ])
    assert.ok(source.includes(route), `missing fixed route ${route}`);
  assert.match(source, /atlas-thumbnail\/\:family\/\:subfamily/);
  assert.match(source, /atlas-preview\/\:family\/\:subfamily/);
  assert.match(source, /ATLAS_ID_PATTERN/);
  assert.match(source, /private, max-age=31536000, immutable/);
  assert.match(source, /Cross-Origin-Resource-Policy/);
  assert.doesNotMatch(source, /proxy\.token|Authorization.*Bearer|AAG_COMPOSER_AUTH_V1/);
});

test("same-origin guard accepts authenticated image fetch without Referer and rejects missing context", () => {
  const vm = require("node:vm");
  const proxySource = fs.readFileSync(proxy, "utf8");
  const boundary = proxySource.slice(
    proxySource.indexOf("function browserOrigin"),
    proxySource.indexOf("function composerSessionCookie")
  );
  const sandbox = { URL, IMAGE_WORKSPACE: "image-generator", result: null };
  vm.runInNewContext(
    `${boundary}\nresult = { sameOriginImageWorkspaceOnly };`,
    sandbox
  );
  const { sameOriginImageWorkspaceOnly } = sandbox.result;
  function invoke(headers, method = "GET") {
    let passed = false;
    const response = {
      statusCode: null,
      status(code) {
        this.statusCode = code;
        return this;
      },
      json(body) {
        this.body = body;
        return this;
      },
    };
    const request = {
      method,
      protocol: "https",
      get(name) {
        const values = { host: "anythingllm.localhost", ...headers };
        return values[name.toLowerCase()];
      },
    };
    sameOriginImageWorkspaceOnly(request, response, () => {
      passed = true;
    });
    return { passed, response };
  }
  const accepted = invoke({
    "x-aag-workspace-path": "/workspace/image-generator/t/thread-1",
    "x-aag-workspace-slug": "image-generator",
  });
  assert.equal(accepted.passed, true);
  const missing = invoke({});
  assert.equal(missing.passed, false);
  assert.equal(missing.response.statusCode, 403);
  const wrong = invoke({
    "x-aag-workspace-path": "/workspace/other",
    "x-aag-workspace-slug": "image-generator",
  });
  assert.equal(wrong.passed, false);
  assert.equal(wrong.response.statusCode, 403);
});

test("production Atlas endpoint regression validates browser-consumable bytes", () => {
  const source = fs.readFileSync(path.join(root, "tools/atlas-endpoint-regression.js"), "utf8");
  assert.match(source, /authenticated_fetch_without_referer_200/);
  assert.match(source, /thumbnail_hash_matches_seal/);
  assert.match(source, /preview_hash_matches_manifest/);
  assert.match(source, /changed_style_returns_different_bytes/);
  assert.match(source, /missing_workspace_context_rejected/);
  assert.match(source, /wrong_workspace_rejected/);
  assert.match(source, /private, max-age=31536000, immutable/);
});

test("pinned frontend build augments the native chat submit without replacing it", () => {
  const buildSource = fs.readFileSync(build, "utf8");
  const serverSource = fs.readFileSync(server, "utf8");
  assert.match(buildSource, /07bd65f80b3d9ba3031ed7afb8786627326bd134/);
  assert.match(buildSource, /patchPromptInput\(checkout\)/);
  assert.match(buildSource, /patchChatContainer\(checkout\)/);
  assert.match(buildSource, /composerMode === \\"advanced\\"/);
  assert.match(buildSource, /composerPanelRef\.current\?\.prepare\(\)/);
  assert.match(buildSource, /return submit\(e, prepared\)/);
  assert.match(buildSource, /content: currentMessage/);
  assert.match(buildSource, /userMessage: modelMessage/);
  assert.match(buildSource, /modelMessage: pending\.modelMessage \|\| pending\.message/);
  assert.match(buildSource, /let aagPendingNativeSubmission = null/);
  assert.match(buildSource, /const memoryPending = aagPendingNativeSubmission/);
  assert.match(buildSource, /hasInMemoryAttachments: outgoingAttachments\.length > 0/);
  assert.match(buildSource, /pending\.hasInMemoryAttachments && !memoryPending/);
  assert.doesNotMatch(
    buildSource,
    /JSON\.stringify\(\{ message: text, modelMessage: executionMessage, attachments \}\)/
  );
  assert.match(buildSource, /history=\{history\}/);
  assert.match(buildSource, /threadSlug=\{threadSlug\}/);
  assert.match(buildSource, /history=\{chatHistory\}/);
  assert.match(buildSource, /<AagImageProgress/);
  assert.equal(
    (buildSource.match(/threadSlug=\{activeThreadSlug\}/g) || []).length,
    2
  );
  assert.doesNotMatch(buildSource, /composerPanelRef\.current\?\.submit\(\)/);
  assert.match(buildSource, /isImageGenerator/);
  assert.match(serverSource, /aagComposerProxyEndpoints\(apiRouter\)/);
});
