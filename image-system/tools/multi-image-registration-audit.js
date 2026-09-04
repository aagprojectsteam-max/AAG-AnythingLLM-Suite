"use strict";

const fs = require("fs");
const crypto = require("crypto");
const AIbitat = require("/app/server/utils/agents/aibitat");
const ImportedPlugin = require("/app/server/utils/agents/imported");
const { WORKSPACE_AGENT } = require("/app/server/utils/agents/defaults");
const { Workspace } = require("/app/server/models/workspace");
const { withPublicToolSchema } = require("/app/server/storage/aag-image-agent-integration/runtime-context-bridge/aagPublicToolSchema.js");

const output = process.argv[2];
const workspaceSlug = process.argv[3] || "image-generator";
const skillRoot = "/app/server/storage/plugins/agent-skills";
const jobRoot = "/app/server/storage/aag-image-agent-state/jobs";
const definitions = [
  ["aag-image-task", "provider-task.schema.json"],
  ["aag-image-batch", "provider-batch.schema.json"],
  ["aag-image-job", "provider-job.schema.json"],
];

function sha256(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function jobNames() {
  try { return new Set(fs.readdirSync(jobRoot).filter((name) => /^aag-[0-9a-f-]+\.json$/i.test(name))); }
  catch (error) { if (error?.code === "ENOENT") return new Set(); throw error; }
}

async function main() {
  if (!output) throw new Error("Evidence output path is required.");
  const workspace = await Workspace.get({ slug: workspaceSlug });
  if (!workspace) throw new Error(`Workspace not found: ${workspaceSlug}`);
  const naturalRequest = "Create exactly three distinct cinematic watercolor lighthouse images as one coherent series, with no technical speed or quality preference.";
  const workspaceDefinition = await WORKSPACE_AGENT.getDefinition(workspace.chatProvider, workspace, null, naturalRequest);
  const aibitat = new AIbitat({ handlerProps: { log() {} } });
  const tools = {};
  for (const [hubId, schemaFile] of definitions) {
    const plugin = ImportedPlugin.loadPluginByHubId(hubId);
    if (!plugin) throw new Error(`${hubId} did not load.`);
    aibitat.use(withPublicToolSchema(plugin, plugin.plugin({
      AAG_WORKSPACE_ID: workspace.id,
      AAG_THREAD_ID: 0,
      AAG_USER_ID: null,
      AAG_INVOCATION_UUID: "preview11-registration-audit",
      AAG_TURN_ID: "preview11-registration-audit",
      AAG_INVOCATION_PROMPT: naturalRequest,
      AAG_INVOCATION_ATTACHMENTS: [],
    })));
    const registered = aibitat.functions.get(hubId);
    if (!registered) throw new Error(`${hubId} did not register.`);
    const schemaBytes = fs.readFileSync(`${skillRoot}/${hubId}/${schemaFile}`);
    tools[hubId] = {
      plugin: {
        hubId: plugin.config.hubId,
        version: plugin.config.version,
        active: plugin.config.active,
        publicSchema: plugin.config.public_schema,
        sameTurnRetry: plugin.config.same_turn_retry || null,
      },
      registered: {
        name: registered.name,
        description: registered.description,
        parameters: registered.parameters,
        schemaSha256: sha256(schemaBytes),
        additionalProperties: registered.parameters.additionalProperties,
        required: registered.parameters.required,
        topLevelStylePresent: Object.hasOwn(registered.parameters.properties || {}, "style"),
        nestedStylePresent: Object.hasOwn(registered.parameters.properties?.items?.items?.properties || {}, "style"),
        directOutputWrapperInstalled: registered.handler.toString().includes("skipHandleExecution") && registered.handler.toString().includes("validatePublicArguments"),
      },
    };
  }
  const before = jobNames();
  const invalid = await aibitat.functions.get("aag-image-batch").handler({
    operation: "multi_generate",
    collection_brief: "Exactly three coherent watercolor lighthouse images.",
    count: 3,
    quality: "auto",
    items: [
      { prompt: "First lighthouse scene", style: "watercolor" },
      { prompt: "Second lighthouse scene" },
      { prompt: "Third lighthouse scene" },
    ],
  });
  const after = jobNames();
  const evidence = {
    schema: "aag.multi-image.runtime-registration.v1",
    verifiedAt: new Date().toISOString(),
    containerId: fs.readFileSync("/etc/hostname", "utf8").trim(),
    workspace: { id: workspace.id, slug: workspace.slug, chatProvider: workspace.chatProvider, chatModel: workspace.chatModel },
    naturalRequest,
    workspaceResolvedFunctions: workspaceDefinition.functions,
    workspaceResolvesBatch: workspaceDefinition.functions.includes("@@aag-image-batch"),
    tools,
    invalidNestedUnknownFieldProbe: {
      result: invalid,
      directOutput: aibitat.skipHandleExecution,
      jobsBefore: before.size,
      jobsAfter: after.size,
      jobsCreated: [...after].filter((name) => !before.has(name)),
    },
  };
  fs.writeFileSync(output, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify({
    output,
    workspaceResolvesBatch: evidence.workspaceResolvesBatch,
    workspaceResolvedFunctions: evidence.workspaceResolvedFunctions,
    batchVersion: tools["aag-image-batch"].plugin.version,
    batchAdditionalProperties: tools["aag-image-batch"].registered.additionalProperties,
    batchRequired: tools["aag-image-batch"].registered.required,
    stylePresent: tools["aag-image-batch"].registered.topLevelStylePresent || tools["aag-image-batch"].registered.nestedStylePresent,
    invalidProbeJobsCreated: evidence.invalidNestedUnknownFieldProbe.jobsCreated.length,
  }));
}

main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
