const fs = require("fs");
const http = require("http");
const path = require("path");

const CONTRACT_FILE =
  "/app/server/storage/aag-ubuntu-agent/bridge-endpoint.json";

// This is the first configured snapshot root in config/maintenance-v1.json.
// It is intentionally fixed here because the AnythingLLM container cannot read
// project configuration directly. Repository tests keep it aligned with the
// trusted core configuration, which remains the final path-policy authority.
const DEFAULT_PLAN_PATH = "/mnt/data/AI";
const TRUSTED_SCOPE_ROOTS = Object.freeze(["/mnt/data", "/var/log"]);

const OPERATIONS = new Map([
  ["system_health", "system.health"],
  ["performance_snapshot", "performance.snapshot"],
  ["storage_overview", "storage.overview"],
  ["storage_top", "storage.top"],
  ["storage_inspect", "storage.inspect"],
  ["storage_largest_files", "storage.largest_files"],
  ["storage_snapshot", "storage.snapshot"],
  ["storage_growth", "storage.growth"],
  ["storage_duplicate_candidates", "storage.duplicate_candidates"],
  ["storage_duplicate_verify", "storage.duplicate_verify"],
  ["storage_space_discrepancy", "storage.space_discrepancy"],
  ["maintenance_plan", "maintenance.plan"],
  ["maintenance_explain", "maintenance.explain"],
]);

const NO_PATH = new Set([
  "system.health",
  "performance.snapshot",
  "storage.overview",
]);

const DEEP_ONLY = new Set([
  "storage.duplicate_candidates",
  "storage.duplicate_verify",
  "storage.space_discrepancy",
]);

const ALLOWED_ARGUMENTS = new Set([
  "operation",
  "path",
  "profile",
  "item_id",
]);

function failClosed(error, detail, clarification = null, status = "unsupported") {
  return JSON.stringify({
    schema: "aag-deployed-maintenance-error-v1",
    status,
    error,
    detail,
    clarification,
    plan_available: false,
    manual_commands_suggested: false,
    response_constraints: {
      grounded_plan_required: true,
      allowed_response: clarification ? "ask_for_clarification_only" : "report_unavailable_only",
      forbidden_content: [
        "maintenance_plan",
        "reclaim_estimates",
        "cleanup_candidates",
        "maintenance_commands",
      ],
    },
    result: {
      execution_authority: "NONE",
      zero_mutations: true,
      items: [],
    },
    read_only: true,
    mutated: false,
  });
}

function trustedPath(value) {
  if (typeof value !== "string" || value.includes("\0")) return null;
  const candidate = value.trim();
  if (!candidate.startsWith("/")) return null;
  const normalized = path.posix.normalize(candidate);
  if (!normalized.startsWith("/")) return null;
  if (!TRUSTED_SCOPE_ROOTS.some(
    (root) => normalized === root || normalized.startsWith(`${root}/`)
  )) return null;
  return normalized;
}

function normalizeArguments(args) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return { error: "maintenance_arguments_must_be_object" };
  }
  if (Object.keys(args).some((name) => !ALLOWED_ARGUMENTS.has(name))) {
    return { error: "unexpected_maintenance_argument" };
  }
  const tool = OPERATIONS.get(args.operation);
  if (!tool) return { error: "invalid_maintenance_operation" };
  const profile = args.profile || "standard";
  if (!["quick", "standard", "deep"].includes(profile)) {
    return { error: "invalid_maintenance_profile" };
  }
  if (DEEP_ONLY.has(tool) && profile !== "deep") {
    return { error: "deep_profile_required" };
  }

  const normalized = { operation: args.operation, profile };
  if (!NO_PATH.has(tool)) {
    const omittedPlanPath = tool === "maintenance.plan" &&
      (args.path === undefined || args.path === null || args.path === "");
    const selectedPath = omittedPlanPath ? DEFAULT_PLAN_PATH : trustedPath(args.path);
    if (!selectedPath) {
      return {
        error: "trusted_maintenance_path_required",
        clarification: "Provide a path inside the configured maintenance scope.",
      };
    }
    normalized.path = selectedPath;
  }
  if (tool === "maintenance.explain") {
    if (typeof args.item_id !== "string" || !args.item_id.startsWith("maint-v1-")) {
      return { error: "invalid_maintenance_item_id" };
    }
    normalized.item_id = args.item_id;
  }
  return { tool, args: normalized };
}

function classifiedError(error) {
  const code = error && error.code;
  if (code === "ENOENT") return "bridge_endpoint_missing";
  if (code === "ECONNREFUSED") return "bridge_socket_refused";
  if (code === "EACCES" || code === "EPERM") return "bridge_permission_denied";
  if (code === "ETIMEDOUT" || code === "ESOCKETTIMEDOUT") return "bridge_timeout";
  return "bridge_unavailable";
}

function readContract() {
  const contract = JSON.parse(fs.readFileSync(CONTRACT_FILE, "utf8"));
  if (
    contract.schema !== "aag-bridge-endpoint-v1" ||
    contract.api_version !== 2 ||
    contract.container_contract_file !== CONTRACT_FILE ||
    typeof contract.container_socket !== "string" ||
    contract.maintenance_path !== "/maintenance"
  ) {
    const error = new Error("Bridge maintenance endpoint contract is incompatible");
    error.code = "AAG_CONTRACT_MISMATCH";
    throw error;
  }
  return contract;
}

function requestArguments(tool, args) {
  const result = {};
  if (!NO_PATH.has(tool)) result.path = args.path;
  if ([
    "storage.top",
    "storage.inspect",
    "storage.largest_files",
    "storage.snapshot",
    "storage.duplicate_candidates",
    "storage.duplicate_verify",
    "storage.space_discrepancy",
    "maintenance.plan",
  ].includes(tool)) result.profile = args.profile || "standard";
  if (tool === "maintenance.explain") result.item_id = args.item_id;
  return result;
}

function validEnvelope(result) {
  return result &&
    result.schema === "aag-maintenance-scan-envelope-v1" &&
    result.schema_version === "1.0" &&
    result.read_only === true &&
    result.mutated === false;
}

function validPlanEnvelope(result) {
  if (!validEnvelope(result)) return false;
  const plan = result.result;
  return plan &&
    plan.schema === "aag-maintenance-plan-v1" &&
    plan.execution_authority === "NONE" &&
    plan.zero_mutations === true &&
    Array.isArray(plan.items) &&
    plan.items.every((item) => item.execution_status === "not_executed");
}

function groundPlanForConversation(result) {
  const plan = result.result;
  const grounded = plan.items
    .filter((item) =>
      item.classification === "LOW_RISK_CANDIDATE" &&
      Number.isInteger(item.estimated_reclaimable_bytes) &&
      item.estimated_reclaimable_bytes > 0
    )
    .map((item) => ({
      item_id: item.item_id,
      target: item.target,
      reason: item.reason,
      estimated_reclaimable_bytes: item.estimated_reclaimable_bytes,
      risk: item.risk,
      required_approval_level: item.required_approval_level,
      required_backup_or_rollback: item.required_backup_or_rollback,
      execution_status: item.execution_status,
    }));
  plan.grounded_recommendations = grounded;
  plan.presentation_policy = {
    schema: "aag-maintenance-presentation-policy-v1",
    grounding_source: "typed_plan_only",
    evidence_backed_candidate_count: grounded.length,
    commands_allowed: false,
    ungrounded_estimates_allowed: false,
    deletion_recommendations_for_other_items_allowed: false,
    no_candidate_message: grounded.length === 0
      ? "No evidence-backed cleanup candidate exists in this plan. Report sizes only as observations and do not recommend deletion."
      : null,
    required_plan_facts: {
      root: plan.root,
      completeness: result.completeness?.status || "unknown",
      estimated_reclaimable_bytes: plan.estimated_reclaimable_bytes,
      execution_authority: plan.execution_authority,
      zero_mutations: plan.zero_mutations,
      execution_status: plan.execution_status,
    },
    forbidden_derivations: [
      "logical_or_allocated_bytes_as_reclaimable",
      "deletion_recommendation_for_non_low_risk_items",
      "commands_or_execution_steps",
    ],
  };
  return result;
}

module.exports.runtime = {
  handler: async function (args) {
    const normalized = normalizeArguments(args);
    if (normalized.error) {
      return failClosed(
        normalized.error,
        "Maintenance request rejected before Bridge dispatch.",
        normalized.clarification || "Use only the documented typed maintenance fields."
      );
    }
    const { tool } = normalized;
    try {
      const contract = readContract();
      this.introspect(`Running bounded read-only maintenance intelligence: ${tool}`);
      const payload = JSON.stringify({
        tool,
        arguments: requestArguments(tool, normalized.args),
      });
      const body = await new Promise((resolve, reject) => {
        const request = http.request(
          {
            socketPath: contract.container_socket,
            path: contract.maintenance_path,
            method: "POST",
            timeout: 55000,
            headers: {
              Accept: "application/json",
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payload),
            },
          },
          (response) => {
            let data = "";
            response.setEncoding("utf8");
            response.on("data", (chunk) => {
              data += chunk;
              if (Buffer.byteLength(data) > 256000) {
                request.destroy(new Error("Bridge response exceeded limit"));
              }
            });
            response.on("end", () => {
              if (response.statusCode < 200 || response.statusCode >= 300) {
                const error = new Error(`Bridge HTTP ${response.statusCode}`);
                error.code = "AAG_BRIDGE_HTTP_ERROR";
                error.responseStatus = response.statusCode;
                error.responseBody = data;
                reject(error);
                return;
              }
              resolve(data);
            });
          }
        );
        request.on("timeout", () => {
          const error = new Error("Bridge maintenance request timed out");
          error.code = "ESOCKETTIMEDOUT";
          request.destroy(error);
        });
        request.on("error", reject);
        request.end(payload);
      });
      const result = JSON.parse(body);
      if (!validEnvelope(result)) {
        const error = new Error("Unexpected maintenance response schema");
        error.code = "AAG_RESPONSE_INVARIANT_FAILED";
        throw error;
      }
      if (tool === "maintenance.plan" && !validPlanEnvelope(result)) {
        const error = new Error("Unexpected maintenance plan invariants");
        error.code = "AAG_PLAN_INVARIANT_FAILED";
        throw error;
      }
      return JSON.stringify(
        tool === "maintenance.plan" ? groundPlanForConversation(result) : result
      );
    } catch (error) {
      if (error.code === "AAG_BRIDGE_HTTP_ERROR") {
        let coreError = "maintenance_request_rejected";
        try {
          const response = JSON.parse(error.responseBody || "{}");
          coreError = response.errors?.[0]?.code || response.error || coreError;
        } catch (_) {
          // The stable fail-closed wrapper below intentionally ignores malformed detail.
        }
        this.logger("AAG maintenance intelligence rejected by trusted core:", coreError);
        this.introspect("Maintenance request rejected by trusted policy; no plan is available");
        return failClosed(
          "maintenance_request_rejected",
          `Trusted core rejected the request: ${coreError}`,
          "Ask for a path inside the configured maintenance scope or explain that no grounded plan is available.",
          "clarification_required"
        );
      }
      const reason = error.code === "AAG_CONTRACT_MISMATCH" || error instanceof SyntaxError
        ? "integration_misconfigured"
        : classifiedError(error);
      this.logger("AAG maintenance intelligence failed:", reason, error.message);
      this.introspect(`Maintenance intelligence unavailable: ${reason}`);
      return failClosed(
        reason,
        "Maintenance intelligence is unavailable; no grounded plan was produced.",
        null,
        "unavailable"
      );
    }
  },
};
