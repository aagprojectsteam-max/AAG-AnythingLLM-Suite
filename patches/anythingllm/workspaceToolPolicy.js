"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const PROFILE_SCHEMA = "aag.anythingllm.workspace-capability-profiles.v1";
const DEFAULT_PROFILE_PATH = path.resolve(
  __dirname,
  "../workspace-capability-profiles/profiles.v1.json"
);
const INTEGRATION_ROOT = path.resolve(__dirname, "..");
const DEFAULT_AUDIT_PATH = path.resolve(
  INTEGRATION_ROOT,
  "workspace-capability-profiles/audit.ndjson"
);
const INSTALL_MARKER = Symbol.for("aag.workspaceToolPolicy.installed.v1");
const HANDLER_MARKER = Symbol.for("aag.workspaceToolPolicy.handler.v1");
const PROVIDER_MARKER = Symbol.for("aag.workspaceToolPolicy.provider.v1");
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function profilePath() {
  return process.env.AAG_WORKSPACE_CAPABILITY_PROFILES_PATH || DEFAULT_PROFILE_PATH;
}

function canonicalCreatedAt(value) {
  if (value instanceof Date) return value.getTime();
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return Math.trunc(numeric);
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function assertStringArray(value, label) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item))
    throw new Error(`${label} must be an array of non-empty strings`);
  if (new Set(value).size !== value.length)
    throw new Error(`${label} contains duplicates`);
}

function validateContract(contract) {
  if (!contract || contract.schema !== PROFILE_SCHEMA)
    throw new Error(`unsupported profile schema`);
  if (typeof contract.profile_version !== "string" || !contract.profile_version.startsWith("1."))
    throw new Error(`unsupported profile version`);
  if (contract.unknown_workspace_policy !== "deny")
    throw new Error(`unknown workspace policy must be deny`);
  if (!contract.tool_families || typeof contract.tool_families !== "object")
    throw new Error(`tool_families is required`);

  const knownTools = new Set();
  const toolFamilies = new Map();
  for (const [family, tools] of Object.entries(contract.tool_families)) {
    assertStringArray(tools, `tool_families.${family}`);
    for (const tool of tools) {
      if (knownTools.has(tool)) throw new Error(`tool ${tool} belongs to multiple families`);
      knownTools.add(tool);
      toolFamilies.set(tool, family);
    }
  }
  assertStringArray(contract.known_write_tools, "known_write_tools");
  for (const tool of contract.known_write_tools)
    if (!knownTools.has(tool)) throw new Error(`unknown write tool ${tool}`);

  if (!Array.isArray(contract.profiles) || contract.profiles.length === 0)
    throw new Error(`profiles is required`);
  const uuids = new Set();
  const workspaceIds = new Set();
  for (const profile of contract.profiles) {
    if (typeof profile.profile_version !== "string" || !profile.profile_version.startsWith("1."))
      throw new Error(`invalid profile_version`);
    if (!UUID_PATTERN.test(profile.workspace_uuid || ""))
      throw new Error(`invalid workspace_uuid`);
    if (uuids.has(profile.workspace_uuid)) throw new Error(`duplicate workspace_uuid`);
    uuids.add(profile.workspace_uuid);
    if (!profile.profile_name || typeof profile.profile_name !== "string")
      throw new Error(`profile_name is required`);

    const binding = profile.workspace_binding;
    const workspaceId = Number(binding?.anythingllm_workspace_id);
    if (!Number.isInteger(workspaceId) || workspaceId < 1)
      throw new Error(`invalid AnythingLLM workspace id`);
    if (workspaceIds.has(workspaceId)) throw new Error(`duplicate AnythingLLM workspace id`);
    workspaceIds.add(workspaceId);
    if (!binding.anythingllm_workspace_slug || typeof binding.anythingllm_workspace_slug !== "string")
      throw new Error(`workspace slug assertion is required`);
    if (!canonicalCreatedAt(binding.anythingllm_workspace_created_at))
      throw new Error(`workspace creation assertion is required`);

    for (const field of ["allow_tools", "allow_families", "deny_tools", "deny_families"])
      assertStringArray(profile[field], `${profile.profile_name}.${field}`);
    for (const family of [...profile.allow_families, ...profile.deny_families])
      if (!Object.prototype.hasOwnProperty.call(contract.tool_families, family))
        throw new Error(`profile ${profile.profile_name} references unknown family ${family}`);
    for (const tool of [...profile.allow_tools, ...profile.deny_tools])
      if (!knownTools.has(tool))
        throw new Error(`profile ${profile.profile_name} references unknown tool ${tool}`);
    if (profile.unknown_tool_policy !== "deny" || profile.enforcement_mode !== "strict")
      throw new Error(`profile ${profile.profile_name} is not fail closed`);
    if (!["deny", "profile_explicit", "allowed_by_profile"].includes(profile.write_tool_policy))
      throw new Error(`invalid write_tool_policy`);
    if (!["native_tool_contract", "deny"].includes(profile.approval_policy))
      throw new Error(`invalid approval_policy`);
    if (profile.audit_logging?.redact_payloads !== true)
      throw new Error(`audit payload redaction must be enabled`);
  }

  return { contract, knownTools, toolFamilies };
}

function loadContract() {
  const raw = fs.readFileSync(profilePath(), "utf8");
  return validateContract(JSON.parse(raw));
}

function denyAllPolicy(workspace, reason) {
  return {
    valid: false,
    reason,
    workspace,
    workspaceUuid: null,
    profileName: "UNRESOLVED_DENY_ALL",
    profile: null,
    contract: null,
    knownTools: new Set(),
    toolFamilies: new Map(),
    allowedTools: new Set(),
    deniedTools: new Set(),
    writeTools: new Set(),
  };
}

function resolveWorkspacePolicy(workspace) {
  try {
    const loaded = loadContract();
    const workspaceId = Number(workspace?.id);
    if (!Number.isInteger(workspaceId) || workspaceId < 1)
      return denyAllPolicy(workspace, "WORKSPACE_ID_UNAVAILABLE");
    const profile = loaded.contract.profiles.find(
      (candidate) =>
        Number(candidate.workspace_binding.anythingllm_workspace_id) === workspaceId
    );
    if (!profile) return denyAllPolicy(workspace, "UNKNOWN_WORKSPACE");

    const binding = profile.workspace_binding;
    if (
      String(workspace?.slug || "") !== binding.anythingllm_workspace_slug ||
      canonicalCreatedAt(workspace?.createdAt) !==
        canonicalCreatedAt(binding.anythingllm_workspace_created_at)
    )
      return denyAllPolicy(workspace, "WORKSPACE_BINDING_MISMATCH");

    const allowedTools = new Set(profile.allow_tools);
    for (const family of profile.allow_families)
      for (const tool of loaded.contract.tool_families[family]) allowedTools.add(tool);
    const deniedTools = new Set(profile.deny_tools);
    for (const family of profile.deny_families)
      for (const tool of loaded.contract.tool_families[family]) deniedTools.add(tool);

    return {
      valid: true,
      reason: "PROFILE_RESOLVED",
      workspace,
      workspaceUuid: profile.workspace_uuid,
      profileName: profile.profile_name,
      profile,
      contract: loaded.contract,
      knownTools: loaded.knownTools,
      toolFamilies: loaded.toolFamilies,
      allowedTools,
      deniedTools,
      writeTools: new Set(loaded.contract.known_write_tools),
    };
  } catch (_error) {
    return denyAllPolicy(workspace, "POLICY_CONFIG_INVALID");
  }
}

function evaluateTool(policy, toolName) {
  const name = String(toolName || "");
  if (!policy?.valid)
    return { allowed: false, decision: `DENY_${policy?.reason || "NO_POLICY"}` };
  if (!policy.knownTools.has(name))
    return { allowed: false, decision: "DENY_UNKNOWN_TOOL" };
  if (policy.deniedTools.has(name))
    return { allowed: false, decision: "DENY_EXPLICIT" };
  if (!policy.allowedTools.has(name))
    return { allowed: false, decision: "DENY_NOT_ALLOWED" };
  if (policy.writeTools.has(name)) {
    if (policy.profile.write_tool_policy === "deny")
      return { allowed: false, decision: "DENY_WRITE_POLICY" };
    if (
      policy.profile.write_tool_policy === "profile_explicit" &&
      !policy.profile.allow_tools.includes(name)
    )
      return { allowed: false, decision: "DENY_WRITE_NOT_EXPLICIT" };
  }
  if (policy.profile.approval_policy === "deny")
    return { allowed: false, decision: "DENY_APPROVAL_POLICY" };
  return { allowed: true, decision: "ALLOW_PROFILE" };
}

function registeredToolName(entry) {
  const raw = typeof entry === "string" ? entry : entry?.name;
  if (!raw || typeof raw !== "string") return null;
  if (raw.startsWith("@@")) return raw.replace(/^@@/, "");
  if (raw.includes("#")) return raw.split("#").at(-1);
  return raw;
}

function formatFunctionsToTools(functions) {
  if (!Array.isArray(functions)) return [];
  return functions.map((fn) => ({
    type: "function",
    function: {
      name: fn.name,
      description: fn.description,
      parameters: fn.parameters,
    },
  }));
}

function schemaTokenCount(functions) {
  try {
    const { TokenManager } = require("/app/server/utils/helpers/tiktoken");
    return new TokenManager().countFromString(
      JSON.stringify(formatFunctionsToTools(functions))
    );
  } catch (_error) {
    return null;
  }
}

function promptTokenCount(usage) {
  const candidates = [
    usage?.prompt_tokens,
    usage?.input_tokens,
    usage?.promptTokens,
    usage?.inputTokens,
  ];
  for (const value of candidates) {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric >= 0) return numeric;
  }
  return null;
}

function safeAuditPath(policy) {
  const destination = policy?.profile?.audit_logging?.destination;
  if (!destination) return DEFAULT_AUDIT_PATH;
  const resolved = path.resolve(INTEGRATION_ROOT, destination);
  if (resolved !== INTEGRATION_ROOT && !resolved.startsWith(`${INTEGRATION_ROOT}${path.sep}`))
    return DEFAULT_AUDIT_PATH;
  return resolved;
}

function installWorkspaceToolPolicy({ aibitat, workspace, log = console.log }) {
  if (!aibitat || aibitat[INSTALL_MARKER]) return aibitat?.[INSTALL_MARKER] || null;

  const context = {
    workspace,
    turnSequence: 0,
    currentTurn: null,
    globalRegistryTools: null,
  };

  function currentPolicy() {
    return resolveWorkspacePolicy(workspace);
  }

  function baseTrace(policy = currentPolicy()) {
    const turn = context.currentTurn || {};
    return {
      schema: "aag.anythingllm.workspace-tool-policy.trace.v1",
      timestamp: new Date().toISOString(),
      turn_id: turn.turnId || null,
      workspace_uuid: policy.workspaceUuid,
      profile_name: policy.profileName,
      global_tool_count: context.globalRegistryTools?.length ?? null,
      policy_eligible_tool_count: turn.policyEligibleTools?.length ?? null,
      reranker_input_count: turn.rerankerInputCount ?? null,
      reranker_selected_count: turn.rerankerSelectedCount ?? null,
      model_visible_tools: turn.modelVisibleTools || [],
      actual_tool_call: turn.actualToolCall || null,
      actual_tool_calls: turn.actualToolCalls || [],
      policy_pre_rerank_decision: turn.preRerankDecision || null,
      policy_pre_execution_decision: turn.preExecutionDecision || null,
      execution_result: turn.executionResult || null,
      tool_schema_tokens: turn.toolSchemaTokens ?? null,
      total_model_input_tokens: turn.totalModelInputTokens ?? null,
      rerank_ms: turn.rerankMs ?? null,
      time_to_first_tool_call: turn.timeToFirstToolCallMs ?? null,
    };
  }

  function audit(event, additions = {}, policy = currentPolicy()) {
    const record = { ...baseTrace(policy), event, ...additions };
    const line = JSON.stringify(record);
    try {
      log(`[WorkspaceToolPolicy] ${line}`);
    } catch (_error) {
      console.log(`[WorkspaceToolPolicy] ${line}`);
    }
    if (policy?.profile?.audit_logging?.enabled !== true) return record;
    try {
      const target = safeAuditPath(policy);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.appendFileSync(target, `${line}\n`, { encoding: "utf8", mode: 0o600 });
    } catch (_error) {
      console.error("[WorkspaceToolPolicy] audit write unavailable");
    }
    return record;
  }

  const originalFunction = aibitat.function.bind(aibitat);
  aibitat.function = function registerPolicyGuardedFunction(functionConfig) {
    if (
      functionConfig &&
      typeof functionConfig.handler === "function" &&
      !functionConfig[HANDLER_MARKER]
    ) {
      const originalHandler = functionConfig.handler;
      const toolName = String(functionConfig.name || "");
      functionConfig.handler = async function workspacePolicyGuardedHandler(args) {
        const policy = currentPolicy();
        const decision = evaluateTool(policy, toolName);
        const turn = context.currentTurn || {};
        turn.actualToolCall = toolName;
        turn.actualToolCalls = Array.isArray(turn.actualToolCalls)
          ? [...turn.actualToolCalls, toolName]
          : [toolName];
        turn.preExecutionDecision = decision.decision;
        if (turn.firstProviderStartedAt && turn.timeToFirstToolCallMs == null)
          turn.timeToFirstToolCallMs = Date.now() - turn.firstProviderStartedAt;
        context.currentTurn = turn;
        audit("pre_execution", {}, policy);
        if (!decision.allowed) {
          turn.executionResult = "denied";
          audit("execution_result", {}, policy);
          const denied = new Error("WORKSPACE_TOOL_POLICY_DENIED");
          denied.code = "WORKSPACE_TOOL_POLICY_DENIED";
          throw denied;
        }
        try {
          const result = await originalHandler.call(this, args);
          turn.executionResult = "success";
          const usage = aibitat.providerInstance?.getUsage?.() || null;
          turn.totalModelInputTokens =
            turn.totalModelInputTokens ?? promptTokenCount(usage);
          audit("execution_result", {}, policy);
          return result;
        } catch (error) {
          turn.executionResult = "error";
          audit("execution_result", { error_code: error?.code || "TOOL_ERROR" }, policy);
          throw error;
        }
      };
      Object.defineProperty(functionConfig, HANDLER_MARKER, {
        value: true,
        enumerable: false,
      });
    }
    return originalFunction(functionConfig);
  };

  const originalGetProvider = aibitat.getProviderForConfig.bind(aibitat);
  aibitat.getProviderForConfig = function getPolicyObservedProvider(config) {
    const provider = originalGetProvider(config);
    if (!provider || provider[PROVIDER_MARKER]) return provider;
    for (const method of ["stream", "complete"]) {
      if (typeof provider[method] !== "function") continue;
      const originalCall = provider[method].bind(provider);
      provider[method] = async function policyObservedProviderCall(
        messages,
        functions,
        ...rest
      ) {
        const turn = context.currentTurn;
        const isToolTurn = turn && Array.isArray(functions);
        const callNumber = isToolTurn ? (turn.providerCallCount || 0) + 1 : null;
        if (isToolTurn) {
          turn.providerCallCount = callNumber;
          if (callNumber === 1) {
            turn.firstProviderStartedAt = Date.now();
            turn.rerankerSelectedCount = functions.length;
            turn.modelVisibleTools = functions.map((fn) => fn.name);
            turn.toolSchemaTokens = schemaTokenCount(functions);
            if (turn.rerankerInputCount <= Number(process.env.AGENT_SKILL_RERANKER_TOP_N || 15))
              turn.rerankMs = 0;
            audit("model_visible");
          }
        }
        const result = await originalCall(messages, functions, ...rest);
        if (isToolTurn && callNumber === 1) {
          turn.totalModelInputTokens = promptTokenCount(
            result?.usage || provider.getUsage?.() || null
          );
          if (result?.functionCall?.name) turn.actualToolCall = result.functionCall.name;
          else turn.executionResult = "no_tool_call";
          audit("provider_result");
        }
        return result;
      };
    }
    Object.defineProperty(provider, PROVIDER_MARKER, {
      value: true,
      enumerable: false,
    });
    return provider;
  };

  const originalReply = aibitat.reply.bind(aibitat);
  aibitat.reply = async function workspacePolicyFilteredReply(route) {
    const policy = currentPolicy();
    const agent = aibitat.agents.get(route.from);
    const configuredEntries = Array.isArray(agent?.functions) ? agent.functions : [];
    if (!context.globalRegistryTools)
      context.globalRegistryTools = configuredEntries
        .map(registeredToolName)
        .filter(Boolean);

    const eligibleEntries = configuredEntries.filter((entry) => {
      const name = registeredToolName(entry);
      return name && evaluateTool(policy, name).allowed;
    });
    if (agent) agent.functions = eligibleEntries;

    context.turnSequence += 1;
    const turnId = crypto
      .createHash("sha256")
      .update(
        JSON.stringify([
          "AAG_WORKSPACE_TOOL_POLICY_TURN_V1",
          policy.workspaceUuid,
          context.turnSequence,
        ])
      )
      .digest("hex")
      .slice(0, 24);
    context.currentTurn = {
      turnId,
      policyEligibleTools: eligibleEntries.map(registeredToolName).filter(Boolean),
      rerankerInputCount: eligibleEntries.length,
      preRerankDecision: policy.valid
        ? "ALLOWLIST_APPLIED"
        : `FAIL_CLOSED_${policy.reason}`,
      rerankMs: null,
    };
    audit("pre_rerank", {}, policy);
    return originalReply(route);
  };

  const installedPolicy = currentPolicy();
  const installed = {
    workspaceUuid: installedPolicy.workspaceUuid,
    profileName: installedPolicy.profileName,
    valid: installedPolicy.valid,
    reason: installedPolicy.reason,
    evaluate: (toolName) => evaluateTool(currentPolicy(), toolName),
    trace: () => baseTrace(currentPolicy()),
  };
  Object.defineProperty(aibitat, INSTALL_MARKER, {
    value: installed,
    enumerable: false,
  });
  audit("policy_installed", { policy_resolution: installedPolicy.reason }, installedPolicy);
  return installed;
}

module.exports = {
  PROFILE_SCHEMA,
  validateContract,
  loadContract,
  resolveWorkspacePolicy,
  evaluateTool,
  registeredToolName,
  formatFunctionsToTools,
  installWorkspaceToolPolicy,
};
