"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execFileSync } = require("child_process");
const { verify: verifyVisualAtlasProduct } = require("./visual-atlas-product");

const root = path.resolve(__dirname, "..");
const version = fs.readFileSync(path.join(root, "VERSION"), "utf8").trim();
const out = path.join(root, "releases", "staged", version);
const manifest = JSON.parse(fs.readFileSync(path.join(out, "STAGED-MANIFEST.json")));
const checkDeployed = process.argv.includes("--deployed");
const postPreviewOverlaySources = new Set([
  "README.md",
  "docs/PROVIDER-NEUTRAL-BOUNDARY.md",
  "tools/build.js",
  "tools/doctor.js",
]);
let failed = 0;

function hash(file) { return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"); }
function ok(label) { console.log(`OK ${label}`); }
function fail(label, message) { console.error(`FAIL ${label}: ${message}`); failed += 1; }
function assertCheck(label, condition, message) {
  if (condition) ok(label);
  else fail(label, message);
}
function check(label, file, expected) {
  try {
    if (hash(file) !== expected) throw new Error("hash mismatch");
    console.log(`OK ${label}`);
  } catch (error) {
    console.error(`FAIL ${label}: ${error.message}`);
    failed += 1;
  }
}

function discoverImportedSkills() {
  const discoveryRoot = "/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills";
  const rows = [];
  for (const entry of fs.readdirSync(discoveryRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const manifestPath = path.join(discoveryRoot, entry.name, "plugin.json");
    if (!fs.existsSync(manifestPath)) continue;
    try {
      const plugin = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
      if (plugin.active !== true) continue;
      rows.push({
        directory: entry.name,
        hubId: String(plugin.hubId || ""),
        version: String(plugin.version || ""),
        functionName: `gtc__${String(plugin.hubId || "")}`,
      });
    } catch (error) {
      fail(`discovery manifest ${entry.name}`, error.message);
    }
  }
  return rows;
}

function duplicateValues(rows, key) {
  const counts = new Map();
  for (const row of rows) counts.set(row[key], (counts.get(row[key]) || 0) + 1);
  return [...counts.entries()].filter(([value, count]) => value && count > 1);
}

function verifyDiscoveryHygiene() {
  const rows = discoverImportedSkills();
  const duplicateHubIds = duplicateValues(rows, "hubId");
  const duplicateFunctions = duplicateValues(rows, "functionName");
  assertCheck("unique active imported-skill hubIds", duplicateHubIds.length === 0, JSON.stringify(duplicateHubIds));
  assertCheck("unique exported imported-skill function names", duplicateFunctions.length === 0, JSON.stringify(duplicateFunctions));
  for (const hubId of ["aag-image-task", "aag-image-batch", "aag-image-job"]) {
    const matches = rows.filter(row => row.hubId === hubId);
    assertCheck(`${hubId} single canonical discovery`, matches.length === 1 && matches[0].directory === hubId && matches[0].version === version, JSON.stringify(matches));
  }
  const activePhaseCopies = rows.filter(row => /^\.phase/i.test(row.directory));
  assertCheck("no active hidden phase skill directories", activePhaseCopies.length === 0, JSON.stringify(activePhaseCopies));
  for (const legacy of ["aag-comfyui-image-generator", "aag-comfyui-reference-image", "aag-upscayl-image-upscaler", "aag-comfyui-model-inventory"]) {
    const legacyPath = path.join("/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills", legacy);
    assertCheck(`legacy overlapping skill absent from discovery ${legacy}`, !fs.existsSync(legacyPath), legacyPath);
  }
}

function markerCount(contents, marker) {
  return contents.split(marker).length - 1;
}

function verifyBridgePersistence() {
  const integrationRoot = "/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration";
  const canonicalPresentation = path.join(root, "integrations/anythingllm/aagArtifactPresentation.js");
  const deployedPresentation = path.join(integrationRoot, "runtime-context-bridge/aagArtifactPresentation.js");
  const canonicalPublicSchemaAdapter = path.join(root, "integrations/anythingllm/aagPublicToolSchema.js");
  const deployedPublicSchemaAdapter = path.join(integrationRoot, "runtime-context-bridge/aagPublicToolSchema.js");
  const canonicalComposerHistory = path.join(root, "integrations/anythingllm/aagComposerHistory.js");
  const deployedComposerHistory = path.join(integrationRoot, "runtime-context-bridge/aagComposerHistory.js");
  const bridgeFiles = [
    { source: path.join(integrationRoot, "runtime-context-bridge/index.js"), destination: "/app/server/utils/agents/index.js", turnMarker: true },
    { source: path.join(integrationRoot, "runtime-context-bridge/ephemeral.js"), destination: "/app/server/utils/agents/ephemeral.js", turnMarker: false },
  ];
  const required = ["AAG_WORKSPACE_ID", "AAG_THREAD_ID", "AAG_USER_ID", "AAG_INVOCATION_UUID", "AAG_INVOCATION_PROMPT", "AAG_INVOCATION_ATTACHMENTS", "AAG_TURN_ID"];
  for (const item of bridgeFiles) {
    const contents = fs.readFileSync(item.source, "utf8");
    assertCheck(`bridge marker ${path.basename(item.source)}`, markerCount(contents, "AAG_IMAGE_RUNTIME_CONTEXT_BRIDGE_V2") === 1, "runtime bridge marker count is not one");
    if (item.turnMarker) assertCheck("turn bridge marker index.js", markerCount(contents, "AAG_IMAGE_TURN_CONTEXT_BRIDGE_V2") === 1, "turn bridge marker count is not one");
    for (const field of required) assertCheck(`bridge ${path.basename(item.source)} ${field}`, contents.includes(field), "trusted field is missing");
    assertCheck(`artifact presentation marker ${path.basename(item.source)}`, markerCount(contents, "AAG_IMAGE_ARTIFACT_PRESENTATION_V1") === 1, "artifact presentation marker count is not one");
    assertCheck(`artifact presentation import ${path.basename(item.source)}`, contents.includes("aagArtifactPresentation.js") && contents.includes("installAagArtifactPresentation"), "artifact presentation adapter is not installed");
    assertCheck(`public tool schema import ${path.basename(item.source)}`, contents.includes("aagPublicToolSchema.js") && contents.includes("withPublicToolSchema"), "canonical public schema adapter is not installed");
  }

  const presentationContents = fs.readFileSync(canonicalPresentation, "utf8");
  const publicSchemaAdapterContents = fs.readFileSync(canonicalPublicSchemaAdapter, "utf8");
  const composerHistoryContents = fs.readFileSync(canonicalComposerHistory, "utf8");
  assertCheck("artifact presentation canonical/deployed parity", hash(canonicalPresentation) === hash(deployedPresentation), `${hash(canonicalPresentation)} != ${hash(deployedPresentation)}`);
  assertCheck("public tool schema canonical/deployed parity", hash(canonicalPublicSchemaAdapter) === hash(deployedPublicSchemaAdapter), `${hash(canonicalPublicSchemaAdapter)} != ${hash(deployedPublicSchemaAdapter)}`);
  assertCheck("Composer native-history canonical/deployed parity", hash(canonicalComposerHistory) === hash(deployedComposerHistory), `${hash(canonicalComposerHistory)} != ${hash(deployedComposerHistory)}`);
  assertCheck("public tool schema validates before handler execution", publicSchemaAdapterContents.includes("validatePublicArguments(schema, args)") && publicSchemaAdapterContents.includes("PUBLIC_SCHEMA_VIOLATION"), "closed runtime validation is missing");
  assertCheck("public tool schema enforces direct final output", publicSchemaAdapterContents.includes('same_turn_retry === "forbidden"') && publicSchemaAdapterContents.includes("skipHandleExecution = true"), "same-turn retry enforcement is missing");
  assertCheck("artifact presentation capability-open policy", presentationContents.includes('AAG_IMAGE_PROVIDER_POLICY: "OPEN_BY_CAPABILITY"'), "provider capability policy marker is missing");
  assertCheck("artifact presentation native output", presentationContents.includes('type: "imageGenerationCard"') && presentationContents.includes("_pendingOutputs"), "native AnythingLLM output registration is missing");
  assertCheck("artifact presentation supports governed batch capability", presentationContents.includes('"aag-image-batch"') && presentationContents.includes("collectionComplete") && presentationContents.includes("logicalIndex"), "batch collection presentation metadata is missing");
  const websocketBridgeContents = fs.readFileSync(bridgeFiles[0].source, "utf8");
  assertCheck("Composer native history augments standard plugin", websocketBridgeContents.includes("withComposerVisibleHistory(AgentPlugins.chatHistory.plugin())") && websocketBridgeContents.includes("installComposerHistoryPersistence({ WorkspaceChats, Workspace })") && composerHistoryContents.includes("original.call(this, aibitat"), "standard chat-history persistence is not comprehensively augmented");
  assertCheck("Composer native history preserves exact bound text", composerHistoryContents.includes("user_request_sha256") && composerHistoryContents.includes("visibleComposerPrompt") && composerHistoryContents.includes("installComposerFailurePersistence"), "visible prompt binding or bounded failure persistence is missing");
  assertCheck("Composer semantic gate receives strict exact native text", markerCount(websocketBridgeContents, "AAG_INVOCATION_PROMPT = composerInvocationPrompt") === 2 && bridgeFiles.every(item => fs.readFileSync(item.source, "utf8").includes("composerInvocationPrompt")), "one runtime bridge can still promote a signed Composer envelope");
  assertCheck("Composer envelope presentation fails closed", composerHistoryContents.includes("RFC 8785/JCS") && composerHistoryContents.includes("AAG_COMPOSER_ENVELOPE_INVALID") && composerHistoryContents.includes('kind: "invalid", visiblePrompt: ""'), "cross-runtime canonicalization or fail-closed envelope handling is missing");
  assertCheck("turn context bridge covers every governed image skill", websocketBridgeContents.includes('["aag-image-task", "aag-image-batch", "aag-image-job"]') && websocketBridgeContents.includes("fn.runtimeArgs.AAG_TURN_ID = currentTurnId"), "batch tool is outside the trusted per-turn context refresh");
  assertCheck("artifact presentation authorization precedes websocket output", presentationContents.includes("await authorizeOutputs(outputs)") && websocketBridgeContents.includes("WorkspaceChats.upsert(chatId"), "owner-scoped card authorization pre-registration is missing");
  assertCheck("artifact presentation uses AnythingLLM upsert failure signal", websocketBridgeContents.includes("const { message } = await WorkspaceChats.upsert(chatId") && websocketBridgeContents.includes("if (message)"), "presentation adapter must use WorkspaceChats.upsert message rather than its undefined chat return");
  assertCheck("artifact presentation canonical URL validation", presentationContents.includes("AAG_PUBLIC_HOSTS") && presentationContents.includes("AAG_INTERNAL_ORIGIN") && presentationContents.includes("artifactSha256"), "canonical URL/hash verification is missing");
  assertCheck("artifact presentation no provider allow-list", !/MODEL_NOT_ALLOWED|PROVIDER_NOT_ALLOWED|if\s*\([^\n]*(gemini|openai|anthropic|claude|gemma|qwen)/i.test(presentationContents), "provider/model policy branch detected");

  for (const launcher of [
    path.join(root, "integrations/launchers/aag-image-start"),
    "/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/bin/aag-image-start",
  ]) {
    const contents = fs.readFileSync(launcher, "utf8");
    assertCheck(`start doctor gate ${launcher}`, markerCount(contents, "AAG_IMAGE_DOCTOR_GATE_V1") === 1 && contents.includes("tools/doctor.js") && contents.includes("--deployed"), "canonical start verification gate is missing");
  }

  const composePath = "/home/aag-linux/docker/anythingllm/compose.yaml";
  const compose = fs.readFileSync(composePath, "utf8");
  for (const item of bridgeFiles) {
    const declaration = `\"${item.source}:${item.destination}:ro\"`;
    assertCheck(`read-only compose mount ${path.basename(item.source)}`, compose.includes(declaration), declaration);
  }

  if (!checkDeployed) return;
  try {
    const mounts = JSON.parse(execFileSync("docker", ["inspect", "anythingllm", "--format", "{{json .Mounts}}"], { encoding: "utf8" }));
    for (const item of bridgeFiles) {
      const mount = mounts.find(value => value.Source === item.source && value.Destination === item.destination);
      assertCheck(`live read-only mount ${path.basename(item.source)}`, Boolean(mount) && mount.RW === false, JSON.stringify(mount || null));
      const liveHash = execFileSync("docker", ["exec", "anythingllm", "sha256sum", item.destination], { encoding: "utf8" }).trim().split(/\s+/, 1)[0];
      assertCheck(`live bridge parity ${path.basename(item.source)}`, liveHash === hash(item.source), `${liveHash} != ${hash(item.source)}`);
    }
    const livePresentationHash = execFileSync("docker", ["exec", "anythingllm", "sha256sum", "/app/server/storage/aag-image-agent-integration/runtime-context-bridge/aagArtifactPresentation.js"], { encoding: "utf8" }).trim().split(/\s+/, 1)[0];
    assertCheck("live artifact presentation parity", livePresentationHash === hash(deployedPresentation), `${livePresentationHash} != ${hash(deployedPresentation)}`);
    const liveComposerHistoryHash = execFileSync("docker", ["exec", "anythingllm", "sha256sum", "/app/server/storage/aag-image-agent-integration/runtime-context-bridge/aagComposerHistory.js"], { encoding: "utf8" }).trim().split(/\s+/, 1)[0];
    assertCheck("live Composer native-history parity", liveComposerHistoryHash === hash(deployedComposerHistory), `${liveComposerHistoryHash} != ${hash(deployedComposerHistory)}`);
  } catch (error) {
    fail("live bridge inspection", error.message);
  }
}

function verifyModelNeutralCompatibility() {
  const integrationRoot = path.join(root, "integrations/model-neutral-compatibility");
  const compatibilityPath = path.join(integrationRoot, "compatibility.py");
  const composerCanonicalPath = path.join(integrationRoot, "composer_canonical.py");
  const serverPath = path.join(integrationRoot, "server.py");
  const unitSource = path.join(integrationRoot, "aag-model-compatibility.service");
  const unitDeployed = "/home/aag-linux/.config/systemd/user/aag-model-compatibility.service";
  const controlPath = "/mnt/data/AI/Scripts/aag-llama-control";
  const compatibility = fs.readFileSync(compatibilityPath, "utf8");
  const composerCanonical = fs.readFileSync(composerCanonicalPath, "utf8");
  const server = fs.readFileSync(serverPath, "utf8");
  const unit = fs.readFileSync(unitSource, "utf8");
  const control = fs.readFileSync(controlPath, "utf8");
  const pythonPrimaryLogic = `${compatibility}\n${server}`.toLowerCase();
  const launcherPrimaryLogic = control.split("\n")
    .filter(line => !/^\s*(?:#|[A-Z_][A-Z0-9_]*=)/.test(line))
    .join("\n").toLowerCase();
  assertCheck("model-neutral compatibility has staged text and chat gates", compatibility.includes("def text_sanity") && server.includes("ensure_basic") && server.includes("ensure_chat") && server.includes("BASIC_TEXT_GATE_FAILED"), "staged basic-text and ordinary-chat gates are missing");
  assertCheck("model-neutral compatibility exposes behavioral modes", server.includes('"NATIVE"') && server.includes('"GENERIC_ADAPTER"') && server.includes('"INCOMPATIBLE"'), "NATIVE/GENERIC_ADAPTER/INCOMPATIBLE modes are missing");
  assertCheck("model-neutral compatibility validates dynamic tool schemas", compatibility.includes("def validate_json_schema") && compatibility.includes("normalize_tools(tools)"), "dynamic tool schema validation is missing");
  assertCheck("model-neutral compatibility has bounded canonical adapter", compatibility.includes("def parse_canonical_call") && compatibility.includes("repair_count = 1"), "bounded canonical adapter is missing");
  assertCheck("model-neutral compatibility never executes tools", !server.includes("subprocess") && !server.includes("execFile") && !server.includes("os.system"), "compatibility server gained an execution primitive");
  assertCheck("model-neutral compatibility has no model-family primary routing", !/(gemma|qwen|mistral|llama3|phi-)/.test(pythonPrimaryLogic) && !/(gemma|qwen|mistral|llama3|phi-)/.test(launcherPrimaryLogic), "model-family token found in executable primary compatibility routing");
  assertCheck("managed launcher removed exact-model chat template routing", !control.includes("SCHEMA_ENFORCED_GEMMA4") && !control.includes("select_chat_template") && !control.includes("--chat-template-file"), "exact-model chat template routing remains");
  assertCheck("managed launcher requires behavioral preflight", control.includes("start_compatibility_boundary") && control.includes("MODEL RUNTIME PREFLIGHT"), "launcher behavioral preflight is missing");
  assertCheck("compatibility service binds only loopback and Docker gateway", unit.includes("AAG_COMPAT_BIND=127.0.0.1,172.17.0.1") && !unit.includes("0.0.0.0"), "compatibility bind is too broad");
  assertCheck("compatibility service hardening", unit.includes("NoNewPrivileges=true") && unit.includes("ProtectSystem=strict") && unit.includes("MemoryDenyWriteExecute=true"), "compatibility service hardening is incomplete");
  assertCheck("Composer AUTO and ADVANCED surface", fs.readFileSync(path.join(integrationRoot, "composer/index.html"), "utf8").includes("Free text only") && compatibility.includes('mode == "auto"'), "Composer mode contract is missing");
  const prepareMethod = server.slice(
    server.indexOf("    def prepare_composer"),
    server.indexOf("    def submit_composer")
  );
  assertCheck("Composer native prepare signs without execution", prepareMethod.includes("_sign_composer_message") && server.includes('path.endswith("prepare")') && !prepareMethod.includes("_anythingllm_json"), "native prepare endpoint is missing or gained an execution path");
  assertCheck("Composer uses one RFC 8785 cross-runtime contract", composerCanonical.includes('COMPOSER_CANONICALIZATION = "RFC8785-JCS"') && compatibility.includes("composer_canonical_json(structured)") && compatibility.includes("composer_canonical_json(value) != raw") && markerCount(server, "composer_canonical_json(intent)") >= 2 && server.includes("composer_canonical_json(composer_intent)"), "Composer generation, signing, verification, or audit hashing bypasses the JCS contract");
  assertCheck("Composer Auto semantics are model discretion", compatibility.includes('"model_discretion_fields"') && compatibility.includes("not literal 'auto' properties") && compatibility.includes("Controls absent ") && compatibility.includes("not applicable and must not become creative requirements"), "Composer three-state semantics are missing");
  assertCheck("Composer preserves native request content", compatibility.includes("AUTHORITATIVE_CONTENT_PRESERVATION=") && compatibility.includes("requested subject, object, action") && compatibility.includes("relationship, quantity, and named attribute") && compatibility.includes("never authorize replacing or") && compatibility.includes("omitting that content"), "model-visible subject/action preservation contract is missing");
  const frontendBuilder = fs.readFileSync(path.join(root, "tools/build-anythingllm-frontend.js"), "utf8");
  const inlineComposer = fs.readFileSync(path.join(root, "integrations/anythingllm/frontend/AagImageComposerPanel/index.jsx"), "utf8");
  const inlineComposerLocalization = fs.readFileSync(path.join(root, "integrations/anythingllm/frontend/AagImageComposerPanel/localization.js"), "utf8");
  const externalComposerUi = fs.readFileSync(path.join(integrationRoot, "composer/index.html"), "utf8");
  assertCheck("Composer Advanced uses native chat submit", frontendBuilder.includes("composerPanelRef.current?.prepare()") && frontendBuilder.includes("return submit(e, prepared)") && frontendBuilder.includes("patchChatContainer(checkout)"), "native chat augmentation patch is missing");
  assertCheck("Composer inline separate submit bypassed", !frontendBuilder.includes("composerPanelRef.current?.submit()"), "inline frontend still intercepts chat with Composer submit");
  assertCheck("Composer inline source choice is governed", inlineComposer.includes('value="previous_artifact"') && inlineComposer.includes('value="current_attachment"') && inlineComposer.includes("findLatestThreadArtifact(history)") && inlineComposer.includes("artifactSha256") && !inlineComposer.includes("thread_reference"), "inline source selection exceeds or misses the governed source policies");
  assertCheck("Composer latest-thread source is visible and fail-closed", inlineComposer.includes('data-testid="aag-previous-artifact-summary"') && inlineComposer.includes("StorageFiles.image(latestThreadArtifact.storageFilename)") && inlineComposer.includes("!latestThreadArtifact"), "latest source visibility or fail-closed validation is missing");
  assertCheck("Composer inline has one normal submit action", !inlineComposer.includes('request("preview", payload)') && !inlineComposer.includes("Preview request") && inlineComposer.includes("there is no separate Composer submission"), "inline preview still resembles a second submission action");
  const visibleInlineCopy = `${inlineComposer.replace(/const RECENTS_KEY = .*?;\n/, "")}\n${inlineComposerLocalization}`;
  assertCheck("Composer visible copy has no stale V1.1 wording", !/V1\.1|v1\.1/.test(visibleInlineCopy) && !/V1\.1|v1\.1/.test(externalComposerUi) && inlineComposer.includes("Controls text intended to appear inside the image") && inlineComposerLocalization.includes("שולט בטקסט שאמור להופיע בתוך התמונה") && externalComposerUi.includes("COMPOSER UI V1.2"), "Composer copy is stale or visible-text help is unclear");
  assertCheck("Composer receives structured native history", frontendBuilder.includes("history={history}") && frontendBuilder.includes("history={chatHistory}"), "structured thread history is not available to source selection");
  assertCheck("Composer Edit defaults to source preservation", inlineComposer.includes('editMode: "preserve"') && inlineComposer.includes('dataTestId="aag-edit-mode"') && inlineComposer.includes('["preserve", "Preserve current appearance"]') && inlineComposer.includes('["restyle", "Restyle image"]'), "closed Edit-mode selector or preserve default is missing");
  assertCheck("Composer operation-specific controls are conditional", inlineComposer.includes("(isGeneration || isRestyle) &&") && inlineComposer.includes("isGeneration && !isIdentityReference") && inlineComposer.includes("!isUpscale &&") && inlineComposer.includes('settings.operation === "create" &&') && !inlineComposer.includes("settings.sourceInstruction"), "Edit/Upscale still expose or submit irrelevant creation controls");
  assertCheck("Composer signed semantics preserve source properties", compatibility.includes('"edit_mode": {"not_applicable", "preserve", "restyle"}') && compatibility.includes('"source_preservation"') && compatibility.includes('"preserve_unspecified_source_properties": True') && compatibility.includes('"style_change_authorized": restyle') && compatibility.includes("Upscale never authorizes creative redesign"), "operation-specific signed preservation semantics are missing");
  assertCheck("Composer Create-from-reference UI is governed", inlineComposer.includes('value="reference"') && inlineComposer.includes('dataTestId="aag-reference-purpose"') && inlineComposer.includes('sha256Blob(previousArtifactBlob)') && inlineComposer.includes('artifactSha256 !== latestThreadArtifact.artifactSha256') && !inlineComposer.includes("reference_path") && !inlineComposer.includes("filesystem_path"), "Create-from-reference source or integrity UI is missing");
  assertCheck("Composer Create-from-reference semantics are signed", compatibility.includes('"reference_purpose": {"not_applicable", "identity", "general_visual"}') && compatibility.includes('"reference_creation"') && compatibility.includes('"preserve_source_composition_by_default": False') && compatibility.includes('"composer_operation"] = "create_from_reference"') && compatibility.includes("never downgrade to subject preservation"), "Create-from-reference signed semantics are missing");

  try {
    const testOutput = execFileSync("python3", ["-m", "unittest", "discover", "-s", integrationRoot, "-p", "test_*.py", "-q"], { encoding: "utf8", cwd: "/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System" });
    assertCheck("model-neutral compatibility offline tests", !/FAILED|ERROR/.test(testOutput), testOutput.trim());
  } catch (error) {
    fail("model-neutral compatibility offline tests", `${error.stdout || ""}${error.stderr || error.message}`.trim());
  }

  if (!checkDeployed) return;
  try {
    assertCheck("deployed compatibility service unit exists", fs.existsSync(unitDeployed), unitDeployed);
    assertCheck("deployed compatibility service unit parity", hash(unitDeployed) === hash(unitSource), `${hash(unitDeployed)} != ${hash(unitSource)}`);
    const env = fs.readFileSync("/mnt/data/AI/Apps/AnythingLLM/storage/.env", "utf8");
    assertCheck("AnythingLLM uses shared compatibility boundary", /^GENERIC_OPEN_AI_BASE_PATH=['\"]http:\/\/host\.docker\.internal:18080\/v1['\"]$/m.test(env), "generic OpenAI base path is not the compatibility boundary");
    const tokenPath = "/mnt/data/AI/Apps/AnythingLLM/storage/aag-model-neutral-compatibility/proxy.token";
    const tokenStat = fs.statSync(tokenPath);
    assertCheck("compatibility token is owner-only", (tokenStat.mode & 0o777) === 0o600 && tokenStat.uid === process.getuid(), `${(tokenStat.mode & 0o777).toString(8)} uid=${tokenStat.uid}`);
  } catch (error) {
    fail("deployed model-neutral compatibility inspection", error.message);
  }
}

function verifyVisualAtlas() {
  const atlasRoot = path.resolve(root, "../visual-atlas");
  const manifestPath = path.join(atlasRoot, "manifest/atlas-manifest.json");
  const aliasPath = path.join(atlasRoot, "manifest/retrieval-aliases.json");
  const taxonomyPath = path.join(root, "integrations/model-neutral-compatibility/composer/visual-taxonomy.json");
  const manifestData = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const aliasData = JSON.parse(fs.readFileSync(aliasPath, "utf8"));
  const taxonomyData = JSON.parse(fs.readFileSync(taxonomyPath, "utf8"));
  const pairs = taxonomyData.families.flatMap(family => family.subfamilies.map(subfamily => `${family.id}/${subfamily.id}`));
  const manifestPairs = manifestData.entries.map(entry => `${entry.family_id}/${entry.subfamily_id}`);
  assertCheck("Visual Atlas taxonomy has 28 families and 493 styles", taxonomyData.families.length === 28 && pairs.length === 493 && new Set(pairs).size === 493, `${taxonomyData.families.length}/${pairs.length}`);
  assertCheck("Visual Atlas manifest has 493 completed entries", manifestData.entries.length === 493 && manifestData.entries.every(entry => entry.status === "COMPLETED"), String(manifestData.entries.length));
  assertCheck("Visual Atlas taxonomy/manifest bijection", JSON.stringify([...pairs].sort()) === JSON.stringify([...manifestPairs].sort()), "style sets differ");
  assertCheck("Visual Atlas canonical taxonomy hash", hash(taxonomyPath) === manifestData.taxonomy_sha256, `${hash(taxonomyPath)} != ${manifestData.taxonomy_sha256}`);
  assertCheck("Visual Atlas alias version", aliasData.atlas_version === manifestData.atlas_version, `${aliasData.atlas_version} != ${manifestData.atlas_version}`);
  assertCheck("Visual Atlas aliases target canonical styles", Object.keys(aliasData.entries || {}).every(key => pairs.includes(key)), "non-canonical alias target");
  let badAssets = [];
  for (const entry of manifestData.entries) {
    const preview = path.join(atlasRoot, entry.output_path);
    const thumbnail = path.join(atlasRoot, entry.thumbnail_path);
    try {
      if (fs.statSync(preview).size < 128 || fs.statSync(thumbnail).size < 128 || hash(preview) !== entry.sha256 || fs.readFileSync(thumbnail).subarray(0, 4).toString("ascii") !== "RIFF") badAssets.push(`${entry.family_id}/${entry.subfamily_id}`);
    } catch { badAssets.push(`${entry.family_id}/${entry.subfamily_id}`); }
  }
  assertCheck("Visual Atlas 493 previews and thumbnails valid", badAssets.length === 0, JSON.stringify(badAssets.slice(0, 8)));
  const runtimeAtlas = fs.readFileSync(path.join(root, "src/visual-atlas.js"), "utf8");
  const knowledge = fs.readFileSync(path.join(root, "src/selective-knowledge.js"), "utf8");
  const composer = fs.readFileSync(path.join(root, "integrations/anythingllm/frontend/AagImageComposerPanel/index.jsx"), "utf8");
  const proxy = fs.readFileSync(path.join(root, "integrations/anythingllm/aagComposerProxy.js"), "utf8");
  try {
    const product = verifyVisualAtlasProduct(path.resolve(root, ".."));
    assertCheck(
      "Visual Atlas mandatory product gate",
      product.result === "PASS" &&
        product.gate.REFERENCES_VALID === "493/493" &&
        product.gate.THUMBNAILS_VALID === "493/493",
      JSON.stringify(product.gate)
    );
  } catch (error) {
    fail("Visual Atlas mandatory product gate", error.message);
  }
  assertCheck("Visual Atlas retrieval is deterministic and model-neutral", runtimeAtlas.includes("deterministic_alias_match") && !/(flux|sdxl|comfy|ollama|qwen)/i.test(runtimeAtlas), "model-specific Atlas routing detected");
  assertCheck("Visual Atlas top-k and context are bounded", runtimeAtlas.includes("Math.min(2") && runtimeAtlas.includes("MAX_CONTEXT_CHARS = 720"), "retrieval bound missing");
  assertCheck("Visual Atlas protects subject anatomy and identity", runtimeAtlas.includes("do not copy the Atlas benchmark subject") && runtimeAtlas.includes("Human anatomy and identity-preservation constraints remain authoritative") && runtimeAtlas.includes("identity_reference_protected"), "style/reference separation missing");
  assertCheck("selective knowledge composition hook is module-separated", knowledge.includes("MODULES = Object.freeze([visualAtlas])") && knowledge.includes("future cultural module"), "composable module hook missing");
  assertCheck("Visual Atlas browser is user-facing and viewport-lazy", composer.includes('data-testid="aag-browse-visual-atlas"') && composer.includes('data-testid="aag-visual-atlas-browser"') && composer.includes("IntersectionObserver") && composer.includes('rootMargin: "240px"') && composer.includes("matches.slice(0, limit)") && composer.includes('data-testid="aag-selected-atlas-style"'), "Atlas browser/selection surface incomplete");
  assertCheck("Visual Atlas browser uses authenticated validated image fetch", composer.includes("fetch(protectedUrl") && composer.includes("...baseHeaders()") && composer.includes('"X-AAG-Workspace-Path"') && composer.includes("URL.createObjectURL(blob)") && composer.includes("responseType !== expectedType") && !/<img\s+src=\{atlasAssetUrl/.test(composer), "protected assets are not loaded through validated fetch/blob delivery");
  assertCheck("Visual Atlas assets use guarded same-origin proxy", proxy.includes("ATLAS_ID_PATTERN") && proxy.includes("atlas-thumbnail/:family/:subfamily") && proxy.includes("atlas-preview/:family/:subfamily") && proxy.includes("authenticatedWorkspaceFetch") && proxy.includes("private, max-age=31536000, immutable"), "Atlas asset proxy missing");
  const composePath = "/home/aag-linux/docker/anythingllm/compose.yaml";
  const mountSource = atlasRoot;
  const mountDestination = "/app/server/storage/aag-visual-atlas";
  const compose = fs.readFileSync(composePath, "utf8");
  assertCheck("Visual Atlas read-only compose mount declared", compose.includes(`\"${mountSource}:${mountDestination}:ro\"`), mountSource);
  if (checkDeployed) {
    try {
      const mounts = JSON.parse(execFileSync("docker", ["inspect", "anythingllm", "--format", "{{json .Mounts}}"], { encoding: "utf8" }));
      const mount = mounts.find(value => value.Source === mountSource && value.Destination === mountDestination);
      assertCheck("Visual Atlas live read-only mount", Boolean(mount) && mount.RW === false, JSON.stringify(mount || null));
      const liveCount = Number(execFileSync("docker", ["exec", "anythingllm", "find", `${mountDestination}/thumbs`, "-type", "f", "-name", "*.webp"], { encoding: "utf8" }).trim().split("\n").filter(Boolean).length);
      assertCheck("Visual Atlas live thumbnail count", liveCount === 493, String(liveCount));
    } catch (error) { fail("Visual Atlas live mount inspection", error.message); }
  }
}

function verifyDynamicIdentityContract() {
  const human = require(path.join(root, "src/human-identity.js"));
  const base = { kind: "current_attachment", index: 1, original_sha256: "9".repeat(64), normalized_sha256: "a".repeat(64), width: 1024, height: 768 };
  let dynamic = null;
  try { dynamic = human.classifySource({ request: "same person", _aag_source: base }); }
  catch (error) { fail("trusted dynamic identity classification", error.message); }
  assertCheck("trusted dynamic identity classification", dynamic?.reference_kind === "trusted_runtime_reference" && dynamic?.fixture_id === null, JSON.stringify(dynamic));
  const historical = human.classifySource({ _aag_source: { ...base, original_sha256: "8b131e3030a094173004ae17df02b9fa94d523cb273398b027ea6bb31e1f2c61" } });
  assertCheck("historical fixture classification retained", historical.reference_kind === "historical_validation_fixture" && historical.fixture_id === "authorized-adult-01", JSON.stringify(historical));
  const historicalBaby = human.classifySource({ _aag_source: { ...base, original_sha256: "93665635711952c6a5da892bea90cc892b7c0a4a6748416e13a69ffd124eced6" } });
  assertCheck("historical baby fixture classification retained", historicalBaby.reference_kind === "historical_validation_fixture" && historicalBaby.fixture_id === "authorized-baby-01", JSON.stringify(historicalBaby));
  let pathRejected = false;
  try { human.classifySource({ _aag_source: { ...base, path: "/tmp/person.png" } }); } catch (error) { pathRejected = error?.code === "SOURCE_UNAUTHORIZED"; }
  assertCheck("arbitrary identity filesystem path rejected", pathRejected, "path-bearing source was accepted");

  const config = JSON.parse(fs.readFileSync(path.join(root, "human-identity/config/PRODUCTION-CONFIG.json"), "utf8"));
  assertCheck("dynamic identity production config enabled", config.trusted_runtime_reference?.enabled === true && config.trusted_runtime_reference?.caller_path_allowed === false, JSON.stringify(config.trusted_runtime_reference || null));
  assertCheck("historical fixtures remain regression definitions", Object.keys(config.historical_validation_fixtures || {}).sort().join(",") === "authorized-adult-01,authorized-baby-01", JSON.stringify(Object.keys(config.historical_validation_fixtures || {})));
  const worker = fs.readFileSync(path.join(root, "human-identity/bin/process_inbox.py"), "utf8");
  assertCheck("worker bridge request v2", worker.includes("aag.human-identity.bridge-request.v2") && worker.includes("verify_staged_reference"), "dynamic worker contract is missing");
  assertCheck("worker path is request-derived", worker.includes("STATE / \"references\" / f\"{message['request_id']}.png\"") && !worker.includes("value.get(\"reference_path\")"), "worker accepts or derives an unsafe path");
  const gate = fs.readFileSync(path.join(root, "human-identity/runtime/quality_gate.py"), "utf8");
  assertCheck("quality gate supports dynamic reference-specific evaluation", gate.includes("dynamic-reference-specific") && gate.includes("intended_control"), "dynamic quality distinction is missing");

  if (checkDeployed) {
    const activeRuntime = path.join(root, "human-identity");
    for (const relative of ["bin/process_inbox.py", "config/PRODUCTION-CONFIG.json", "runtime/validate_reference_cli.py", "runtime/quality_gate.py"]) {
      assertCheck(`active identity subrelease parity ${relative}`, hash(path.join(activeRuntime, relative)) === hash(path.join(root, "human-identity", relative)), "active worker differs from canonical persistent source");
    }
    const unit = execFileSync("systemctl", ["--user", "cat", "aag-human-identity-bridge.service"], { encoding: "utf8" });
    assertCheck("active identity service uses canonical persistent runtime", unit.includes(`ExecStart=${path.join(root, "human-identity/bin/process_inbox.py")}`) && unit.includes(`AAG_HUMAN_IDENTITY_RUNTIME=${path.join(root, "human-identity")}`), "systemd identity runtime is not canonical persistent source");
  }
}

function verifySceneIdentityContract() {
  const expectedScene = "09c8869e0f9d7099ee4a8b2bce6c8c041e449becb5924240a950352a14b18de6";
  const expectedB = "d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4";
  const contractPath = path.join(root, "human-identity-scene/config/SCENE-CONTRACT.json");
  const configPath = path.join(root, "human-identity-scene/config/PRODUCTION-CONFIG.json");
  const contract = JSON.parse(fs.readFileSync(contractPath, "utf8"));
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  assertCheck("Scene Contract C frozen hash", hash(contractPath) === expectedScene && contract.status === "FROZEN_PRODUCTION_V1", hash(contractPath));
  assertCheck("Contract B frozen control unchanged", hash(path.join(root, "human-identity/config/CONTRACT-B-FREEZE.json")) === expectedB && contract.contract_b_control_sha256 === expectedB, contract.contract_b_control_sha256);
  assertCheck("Scene Contract production config", config.scene_contract_sha256 === expectedScene && config.contract_b_control_sha256 === expectedB && config.trusted_runtime_reference?.caller_path_allowed === false, JSON.stringify(config));
  assertCheck("Scene Contract reference root is private staging only", JSON.stringify(config.authorized_reference_roots) === JSON.stringify(["/mnt/data/AI/Apps/AnythingLLM/storage/aag-human-identity-scene-state/references"]), JSON.stringify(config.authorized_reference_roots));
  assertCheck("Scene Contract quality gate locked", contract.identity_gate?.cosine_floor === 0.55 && contract.identity_gate?.manual_visual_veto_required === true && contract.scene_gate?.manual_visual_veto_required === true, JSON.stringify(contract.identity_gate));
  assertCheck("Scene Contract bounded profiles", Object.keys(contract.supported_profiles || {}).sort().join(",") === "scene-c-landscape,scene-c-portrait", JSON.stringify(contract.supported_profiles));
  const runtime = require(path.join(root, "src/runtime.js"));
  assertCheck("provider policy is open by protocol capability", runtime.PROVIDER_POLICY === "OPEN_BY_CAPABILITY", String(runtime.PROVIDER_POLICY));
  const runtimeSource = fs.readFileSync(path.join(root, "src/runtime.js"), "utf8");
  assertCheck("no provider or model-name policy branch in canonical core", !/(?:provider|model)\s*(?:===|==|includes\s*\()/i.test(runtimeSource), "provider/model-name policy branch found");
  const trusted = { AAG_WORKSPACE_ID: "doctor-w", AAG_THREAD_ID: "doctor-t", AAG_USER_ID: "doctor-u", AAG_INVOCATION_UUID: "doctor-i", AAG_TURN_ID: "doctor-turn" };
  const scene = runtime.normalizeTask({ operation: "transform", request: "תעשה לי תמונה של הילדה הזו רוכבת על גמל", prompt: "Create the same young girl visibly riding one camel in a desert scene.", preservation: "identity", source_policy: "current_attachment", source_index: 1, aspect_ratio: "landscape" }, trusted);
  assertCheck("provider landscape scene routes Scene C", runtime.workflow(scene) === "transform.human.identity.scene.v1" && scene._aag_identity_profile === "scene-c-landscape" && scene._aag_internal_width === 1152 && scene._aag_internal_height === 896, JSON.stringify(scene));
  const omittedRequest = runtime.normalizeTask({ operation: "transform", prompt: "Create the same young girl visibly riding one camel in a desert scene.", preservation: "identity", source_policy: "current_attachment", source_index: 1 }, { ...trusted, AAG_INVOCATION_PROMPT: "תעשה לי תמונה של הילדה הזו רוכבת על גמל" });
  assertCheck("omitted provider request uses trusted invocation prompt", omittedRequest.request === "תעשה לי תמונה של הילדה הזו רוכבת על גמל" && runtime.workflow(omittedRequest) === "transform.human.identity.scene.v1", JSON.stringify(omittedRequest));
  const portrait = runtime.normalizeTask({ operation: "transform", request: "professional portrait of the same person", preservation: "identity", source_policy: "current_attachment", source_index: 1, width: 896, height: 1152 }, trusted);
  assertCheck("explicit portrait dimensions canonicalize to Contract B", runtime.workflow(portrait) === "transform.human.identity.portrait.v1" && portrait._aag_identity_profile === "contract-b-portrait", JSON.stringify(portrait));
  const prompt = require(path.join(root, "src/scene-identity.js")).scenePrompt(scene);
  assertCheck("scene prompt uses the validated bounded camel composition", prompt.includes("exactly one toddler girl") && prompt.includes("exactly one friendly camel") && prompt.includes("visibly riding") && prompt.includes("warm desert dunes") && prompt.includes("medium-wide landscape") && !prompt.includes("still mid shot portrait"), prompt);
  const sceneAdapter = require(path.join(root, "src/scene-identity.js"));
  assertCheck("agent and frozen Scene C release identities are distinct", sceneAdapter.RELEASE === version && sceneAdapter.CONTRACT_RELEASE === "0.9.0-preview.5", JSON.stringify({ agent: sceneAdapter.RELEASE, contract: sceneAdapter.CONTRACT_RELEASE }));
  const worker = fs.readFileSync(path.join(root, "human-identity-scene/bin/process_inbox.py"), "utf8");
  assertCheck("scene worker consumes trusted staged reference", worker.includes("verify_staged_reference(message)") && !worker.includes("value.get(\"reference_path\")"), "unsafe or missing staging boundary");
  if (checkDeployed) {
    const unit = execFileSync("systemctl", ["--user", "cat", "aag-human-identity-scene-bridge.service"], { encoding: "utf8" });
    assertCheck("active Scene Identity service uses canonical persistent runtime", unit.includes(`ExecStart=${path.join(root, "human-identity-scene/bin/process_inbox.py")}`) && unit.includes(`AAG_HUMAN_IDENTITY_RUNTIME=${path.join(root, "human-identity-scene")}`), "scene systemd runtime is not canonical persistent source");
    const activePath = path.join("/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-state", "scene-active-generation.json");
    const active = JSON.parse(fs.readFileSync(activePath, "utf8"));
    assertCheck("Scene Identity committed activation generation", active.commit_state === "COMMITTED_LIVE" && /^0\.9\.0-preview\.[0-9]+$/.test(active.release || "") && active.scene_contract_sha256 === expectedScene && active.contract_b_sha256 === expectedB, JSON.stringify(active));
  }
}

function verifyPromptQualityContract() {
  const qualityPath = path.join(root, "src/prompt-quality.js");
  const quality = require(qualityPath);
  const authoritative = "תעשה לי תמונה מצויירת של חתול ועכבר בזירת אגרוף";
  const weak = "A hand-drawn illustration of a cat and a mouse engaged in a boxing match. Dynamic action, cartoon style.";
  const detailed = "A dynamic and whimsical cartoon illustration of a humorous boxing match between an expressive cat and a clever little mouse. In the center of a brightly lit vintage boxing ring, the determined cat wearing blue boxing shorts and matching boxing gloves squares off against a tiny, agile mouse wearing red boxing shorts and oversized red boxing gloves in a classic fighter stance. Vibrant stadium arena lighting, clean character design, lively 3D animated movie style, detailed boxing ropes and canvas floor, playful atmosphere, balanced composition, rich colors and expressive faces.";
  const weakResult = quality.validate({ authoritative, proposal: weak });
  const strong = quality.validate({ authoritative, proposal: detailed });
  assertCheck("prompt quality contract identity", quality.CONTRACT_ID === "aag.prompt-quality.v1", quality.CONTRACT_ID);
  assertCheck("weak prompt measured and preserved without retry", weakResult.contract.status === "UNDER_SPECIFIED" && weakResult.prompt === weak, JSON.stringify(weakResult.contract));
  assertCheck("workspace LLM prompt preserved", strong.contract.author === "workspace-llm" && strong.contract.strategy === "preserved-llm-authored-prompt" && strong.prompt === detailed, JSON.stringify(strong.contract));
  const source = fs.readFileSync(qualityPath, "utf8");
  assertCheck("prompt quality has no creative writer or template", !/(?:boxingPrompt|genericEnrichment|basePrompt|canonical-scene-template|deterministic-structural-constraints)/i.test(source), "creative prompt writer detected");
  assertCheck("prompt quality has no provider/model allow-list", !/(?:gemini|gemma|openai|anthropic|claude|qwen)|(?:provider|model)\s*(?:===|==|includes\s*\()/i.test(source), "provider/model policy branch detected");
  const registry = JSON.parse(fs.readFileSync(path.join(root, "registry/workflows.json"), "utf8"));
  const ordinary = registry["generation.text.fast.v1"];
  assertCheck("ordinary workflow uses validation-only prompt policy", ordinary?.prompt_policy === "workspace-llm-authored-validation-only-v1", JSON.stringify(ordinary));
  assertCheck("ordinary engine recipe remains frozen", ordinary?.model === "flux-2-klein-4b-fp8.safetensors" && ordinary?.steps === 4 && ordinary?.cfg === 1 && ordinary?.sampler === "euler" && ordinary?.scheduler === "Flux2Scheduler", JSON.stringify(ordinary));
}

function directoryTreeHash(directory) {
  const files = [];
  const walk = current => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true }).sort((left, right) => left.name.localeCompare(right.name))) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) walk(absolute);
      else if (entry.isFile() && entry.name !== "AAG-BUILD-PROVENANCE.json") files.push(path.relative(directory, absolute));
    }
  };
  walk(directory);
  const digest = crypto.createHash("sha256");
  for (const relative of files) {
    digest.update(relative); digest.update("\0"); digest.update(fs.readFileSync(path.join(directory, relative))); digest.update("\0");
  }
  return { files: files.length, sha256: digest.digest("hex") };
}

function verifyMultiImageExportIntegration() {
  const stagedPublic = path.join(out, "anythingllm-public");
  const deployedPublic = "/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/public";
  const provenancePath = path.join(stagedPublic, "AAG-BUILD-PROVENANCE.json");
  assertCheck("source-built AnythingLLM frontend exists", fs.existsSync(provenancePath), provenancePath);
  if (fs.existsSync(provenancePath)) {
    const provenance = JSON.parse(fs.readFileSync(provenancePath, "utf8"));
    const actual = directoryTreeHash(stagedPublic);
    assertCheck("AnythingLLM frontend exact source revision", provenance.anythingllmRevision === "07bd65f80b3d9ba3031ed7afb8786627326bd134", JSON.stringify(provenance));
    assertCheck("AnythingLLM frontend staged tree provenance", provenance.publicTreeSha256 === actual.sha256 && provenance.publicFiles === actual.files, JSON.stringify({ provenance, actual }));
  }
  const client = fs.readFileSync(path.join(root, "integrations/anythingllm/frontend/aagArtifactExport.js"), "utf8");
  assertCheck("Save As precedes lazy server export request", client.indexOf("window.showSaveFilePicker") >= 0 && client.indexOf("requestPdf(storageFilenames, mode)", client.indexOf("window.showSaveFilePicker")) > client.indexOf("window.showSaveFilePicker"), "native picker is not invoked before server work");
  assertCheck("Save As browser fallback exists", client.includes("saveAs(blob, suggestedName)") && client.includes("ANYTHING-${stamp}.pdf"), "safe filename fallback is missing");
  const endpoint = fs.readFileSync(path.join(root, "integrations/anythingllm/aagArtifactExport.js"), "utf8");
  const assembler = fs.readFileSync(path.join(root, "integrations/anythingllm/aagPdfAssembler.js"), "utf8");
  assertCheck("export endpoint authorizes persisted chat outputs", endpoint.includes("validatedRequest") && endpoint.includes("WorkspaceChats.where") && endpoint.includes("collectionComplete !== true"), "trusted export authorization is incomplete");
  assertCheck("export rejects paths and accepts opaque storage references", endpoint.includes("GENERATED_IMAGE_FILENAME_PATTERN") && endpoint.includes("O_NOFOLLOW") && !/output_path|destination_path|directory_path/.test(endpoint), "unsafe export path surface detected");
  assertCheck("deterministic local PDF exact-count assembler", assembler.includes("one-image-per-page") || (assembler.includes("verifyPdf") && assembler.includes("expectedPages")) && !/https?:\/\//.test(assembler), "local deterministic assembler is missing");

  const composePath = "/home/aag-linux/docker/anythingllm/compose.yaml";
  const compose = fs.readFileSync(composePath, "utf8");
  const mounts = [
    ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/index.js", "/app/server/index.js"],
    ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/aagArtifactExport.js", "/app/server/endpoints/aagArtifactExport.js"],
    ["/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-integration/multi-image-export/server/aagPdfAssembler.js", "/app/server/endpoints/aagPdfAssembler.js"],
    [deployedPublic, "/app/server/public"],
  ];
  for (const [source, destination] of mounts) assertCheck(`persistent export mount ${path.basename(destination)}`, compose.includes(`"${source}:${destination}:ro"`), `${source}:${destination}:ro`);
  if (!checkDeployed) return;
  if (fs.existsSync(provenancePath) && fs.existsSync(path.join(deployedPublic, "AAG-BUILD-PROVENANCE.json"))) {
    assertCheck("deployed frontend tree parity", directoryTreeHash(stagedPublic).sha256 === directoryTreeHash(deployedPublic).sha256, "staged and deployed frontend trees differ");
  }
  try {
    const liveMounts = JSON.parse(execFileSync("docker", ["inspect", "anythingllm", "--format", "{{json .Mounts}}"], { encoding: "utf8" }));
    for (const [source, destination] of mounts) {
      const mount = liveMounts.find(value => value.Source === source && value.Destination === destination);
      assertCheck(`live export mount ${path.basename(destination)}`, Boolean(mount) && mount.RW === false, JSON.stringify(mount || null));
    }
  } catch (error) { fail("live export mount inspection", error.message); }
}

for (const item of manifest.source_files) {
  if (postPreviewOverlaySources.has(item.path)) {
    ok(`source ${item.path} governed post-Preview.12 overlay`);
    continue;
  }
  check(`source ${item.path}`, path.join(root, item.path), item.sha256);
}
for (const item of manifest.provider_files) {
  check(`staged ${item.path}`, path.join(out, item.path), item.sha256);
  check(`source-to-staged ${item.source}`, path.join(root, item.source), item.sha256);
  if (checkDeployed) check(`deployed ${item.deploy_target}`, item.deploy_target, item.sha256);
}
for (const item of manifest.integration_files) {
  check(`integration-source ${item.source}`, path.join(root, item.source), item.sha256);
  if (checkDeployed) for (const target of item.targets) check(`integration-deployed ${target}`, target, item.sha256);
}

const plugins = ["aag-image-task", "aag-image-batch", "aag-image-job"].map(name => JSON.parse(fs.readFileSync(path.join(out, name, "plugin.json"))));
const publicTaskSchema = JSON.parse(fs.readFileSync(path.join(root, "schemas/provider-task.schema.json")));
const stagedPublicTaskSchema = JSON.parse(fs.readFileSync(path.join(out, "aag-image-task/provider-task.schema.json")));
const publicBatchSchema = JSON.parse(fs.readFileSync(path.join(root, "schemas/provider-batch.schema.json")));
const stagedPublicBatchSchema = JSON.parse(fs.readFileSync(path.join(out, "aag-image-batch/provider-batch.schema.json")));
const providerBoundary = fs.readFileSync(path.join(root, "docs/PROVIDER-NEUTRAL-BOUNDARY.md"), "utf8");
assertCheck("public task schema staged parity", JSON.stringify(publicTaskSchema) === JSON.stringify(stagedPublicTaskSchema), "staged model-facing schema differs from canonical provider schema");
assertCheck("public batch schema staged parity", JSON.stringify(publicBatchSchema) === JSON.stringify(stagedPublicBatchSchema), "staged model-facing batch schema differs from canonical provider schema");
assertCheck("public batch schema exact plan boundary", JSON.stringify(publicBatchSchema.required) === JSON.stringify(["operation", "collection_brief", "count", "quality", "items"]) && publicBatchSchema.properties?.count?.minimum === 2 && publicBatchSchema.properties?.count?.maximum === 10 && publicBatchSchema.properties?.items?.items?.additionalProperties === false, JSON.stringify(publicBatchSchema.required));
assertCheck("public batch schema has no structured style", !Object.hasOwn(publicBatchSchema.properties || {}, "style") && !Object.hasOwn(publicBatchSchema.properties?.items?.items?.properties || {}, "style") && /no structured style field/i.test(publicBatchSchema.description || ""), "batch schema exposes style");
assertCheck("public batch schema forbids same-turn retry", /at most once/i.test(publicBatchSchema.description || "") && /same-turn regeneration/i.test(publicBatchSchema.description || ""), publicBatchSchema.description || "");
assertCheck("public batch quality preserves Preview 10 semantics", JSON.stringify(publicBatchSchema.properties?.quality?.enum) === JSON.stringify(["auto", "fast", "balanced", "quality"]) && /fast requires explicit/i.test(publicBatchSchema.description || "") && /Creative words[\s\S]*never select quality/i.test(publicBatchSchema.description || ""), publicBatchSchema.description || "");
assertCheck("public task schema exact required fields", JSON.stringify(publicTaskSchema.required) === JSON.stringify(["operation", "prompt", "source_policy", "preservation"]), JSON.stringify(publicTaskSchema.required));
assertCheck("public task schema forbids same-turn retries", /at most once per user turn/i.test(publicTaskSchema.properties?.operation?.description || "") && /including failure/i.test(publicTaskSchema.properties?.operation?.description || ""), publicTaskSchema.properties?.operation?.description || "");
assertCheck("public task schema keeps creative style in prompt", !Object.hasOwn(publicTaskSchema.properties || {}, "style") && /no structured style field/i.test(publicTaskSchema.description || ""), JSON.stringify(publicTaskSchema.properties?.style));
assertCheck("public task schema forbids invented enum values", Object.values(publicTaskSchema.properties).filter(value => Array.isArray(value.enum)).every(value => /do not invent|never values|listed value/i.test(value.description || "")), "an enum lacks closed-value guidance");
const qualitySemantics = `${plugins[0].description}\n${publicTaskSchema.description}\n${publicTaskSchema.properties?.quality?.description || ""}`;
assertCheck("public quality explicit speed semantics", /fast when the user explicitly prioritizes speed, quick output, low latency, or the fastest/i.test(qualitySemantics), qualitySemantics);
assertCheck("public quality explicit maximum semantics", /quality ONLY (?:for|when).*explicit.*maximum.*quality/i.test(qualitySemantics), qualitySemantics);
assertCheck("public quality explicit balance semantics", /balanced (?:ONLY )?(?:for|when).*explicit.*(?:compromise|balance) between speed and quality/i.test(qualitySemantics), qualitySemantics);
assertCheck("public quality default semantics", /(?:DEFAULT DECISION IS auto|QUALITY FIELD DECISION)[\s\S]*(?:omission is equivalent|select quality.?auto)/i.test(qualitySemantics), qualitySemantics);
assertCheck("creative words do not promote technical quality", /3D[\s\S]*cinematic[\s\S]*MUST NOT (?:alone |by themselves )?(?:imply quality|select (?:any non-auto )?quality)/i.test(qualitySemantics), qualitySemantics);
assertCheck("provider-neutral canonical validation boundary", /ANY PROVIDER \/ ANY MODEL[\s\S]*strict canonical pre-execution validation[\s\S]*governed execution/i.test(providerBoundary) && /model-neutral compatibility boundary[\s\S]*never executes a tool/i.test(providerBoundary), providerBoundary);
assertCheck("provider-neutral adapters require no Image System rewrite", /LM Studio and Ollama/i.test(providerBoundary) && /Gemini and OpenAI-compatible cloud providers/i.test(providerBoundary) && /No Image System rewrite or[\s\S]*provider\/model allow-list is required/i.test(providerBoundary), providerBoundary);
assertCheck("public source policies match resolvable source collections", JSON.stringify(publicTaskSchema.properties?.source_policy?.enum) === JSON.stringify(["auto", "current_attachment", "previous_artifact"]), JSON.stringify(publicTaskSchema.properties?.source_policy?.enum));
const publicSourceDescription = publicTaskSchema.properties?.source_policy?.description || "";
assertCheck("public current attachment semantics", /current_attachment.*CURRENT user (?:message|turn)/i.test(publicSourceDescription) && /NEVER merely because an image exists earlier/i.test(publicSourceDescription), publicSourceDescription);
assertCheck("public previous artifact semantics", /previous_artifact.*most recently returned eligible AAG image/i.test(publicSourceDescription) && /image just made/i.test(publicSourceDescription), publicSourceDescription);
assertCheck("unsupported older thread selection is explicit", /non-latest older thread artifact.*cannot currently be selected/i.test(publicSourceDescription) && /do not invent thread_reference/i.test(publicSourceDescription), publicSourceDescription);
const publicSourceIndexDescription = publicTaskSchema.properties?.source_index?.description || "";
assertCheck("public source index semantics", /ONE-BASED/i.test(publicSourceIndexDescription) && /Valid only when source_policy is current_attachment/i.test(publicSourceIndexDescription) && /never indexes prior artifacts or thread history/i.test(publicSourceIndexDescription), publicSourceIndexDescription);
const publicPreservationDescription = publicTaskSchema.properties?.preservation?.description || "";
assertCheck("public preservation semantics", /same recognizable HUMAN PERSON/i.test(publicPreservationDescription) && /Never use identity for animals, objects, products, logos/i.test(publicPreservationDescription) && /subject means preserve ordinary non-human/i.test(publicPreservationDescription), publicPreservationDescription);
const canonicalRuntimeSource = fs.readFileSync(path.join(root, "src/runtime.js"), "utf8");
assertCheck("runtime rejects every structured style field", !canonicalRuntimeSource.includes('"style"') && !canonicalRuntimeSource.includes("args.style") && !canonicalRuntimeSource.includes("_aag_upstream_style"), "runtime retains a private style input");
assertCheck("runtime rejects unsupported thread reference policy", !canonicalRuntimeSource.includes('"thread_reference"'), "runtime still accepts thread_reference");
assertCheck("runtime source index is current-only", canonicalRuntimeSource.includes('task.source_index !== undefined && task.source_policy !== "current_attachment"'), "runtime does not enforce source_index collection");
assertCheck("runtime non-retryable failures forbid same-turn retry", canonicalRuntimeSource.includes('same_turn_retry=forbidden'), "runtime failure envelope lacks explicit same-turn retry prohibition");
assertCheck("plugin same-turn retry is structurally forbidden", plugins.find(plugin => plugin.hubId === "aag-image-task")?.same_turn_retry === "forbidden", "task plugin lacks the direct-output marker");
assertCheck("batch plugin same-turn retry is structurally forbidden", plugins.find(plugin => plugin.hubId === "aag-image-batch")?.same_turn_retry === "forbidden", "batch plugin lacks the direct-output marker");
if (!plugins.every(plugin => plugin.active === manifest.active && plugin.version === version)) {
  console.error("FAIL activation/version mismatch"); failed += 1;
}
if (manifest.maturity !== "Preview" || /rc/i.test(version) || manifest.human_identity !== "active-portrait-b-and-bounded-scene-c") {
  console.error("FAIL maturity/capability label mismatch"); failed += 1;
}
if (manifest.contract_b_sha256 !== "d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4") {
  console.error("FAIL Contract B identity mismatch"); failed += 1;
}
if (manifest.scene_contract_sha256 !== "09c8869e0f9d7099ee4a8b2bce6c8c041e449becb5924240a950352a14b18de6") {
  console.error("FAIL Scene Contract C identity mismatch"); failed += 1;
}
for (const item of manifest.human_identity_files || []) check(`human-identity ${item.path}`, path.join(root, item.path), item.sha256);
for (const item of manifest.scene_identity_files || []) check(`scene-identity ${item.path}`, path.join(root, item.path), item.sha256);
if (hash(path.join(root, "registry/workflows.json")) !== manifest.workflow_registry_sha256) {
  console.error("FAIL workflow registry hash"); failed += 1;
}
for (const name of ["aag-image-task", "aag-image-batch", "aag-image-job"]) {
  for (const moduleName of manifest.modules) {
    try { require(path.join(out, name, moduleName)); }
    catch (error) { console.error(`FAIL load ${name}/${moduleName}: ${error.message}`); failed += 1; }
  }
}
verifyDiscoveryHygiene();
verifyBridgePersistence();
verifyModelNeutralCompatibility();
verifyVisualAtlas();
verifyDynamicIdentityContract();
verifySceneIdentityContract();
verifyPromptQualityContract();
verifyMultiImageExportIntegration();
process.exitCode = failed ? 1 : 0;
