const fs = require("fs");
const http = require("http");

const CONTRACT_FILE =
  "/app/server/storage/aag-ubuntu-agent/bridge-endpoint.json";

const OPERATIONS = new Map([
  ["context_current", "context"],
  ["history_search", "historical"],
  ["current_bridge", "current_bridge"],
  ["current_performance", "current_performance"],
  ["task_resume", "task"],
  ["remediation_plan", "remediation_plan"],
  ["status", "status"],
]);
const BUDGETS = new Set(["exact", "normal", "history", "complex"]);
const ALLOWED_ARGUMENTS = new Set(["operation", "query", "task_id", "budget_tier"]);
const TASK_ID = /^task:[a-f0-9]{24}$/;
const ARTIFACT_ID = /^artifact:[a-f0-9]{24}$/;

function failClosed(error, clarification = null, status = "unsupported") {
  return JSON.stringify({
    schema: "aag-deployed-context-error-v1",
    status,
    error,
    clarification,
    context_available: false,
    plan_available: false,
    manual_commands_suggested: false,
    response_constraints: {
      allowed_response: clarification ? "ask_for_clarification_only" : "report_unavailable_only",
      invented_facts_allowed: false,
      invented_source_ids_allowed: false,
      commands_allowed: false,
      maintenance_or_repair_steps_allowed: false,
    },
    execution_authority: "NONE",
    execution_status: "not_executed",
    read_only: true,
    mutated: false,
    zero_mutations: true,
  });
}

function boundedQuery(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string" || value.includes("\0")) return null;
  const query = value.trim();
  if (!query || query.length > 1000) return null;
  return query;
}

function normalizeArguments(args) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return { error: "context_arguments_must_be_object" };
  }
  if (Object.keys(args).some((name) => !ALLOWED_ARGUMENTS.has(name))) {
    return { error: "unexpected_context_argument" };
  }
  const backendOperation = OPERATIONS.get(args.operation);
  if (!backendOperation) return { error: "invalid_context_operation" };
  const query = boundedQuery(args.query);
  if (args.query !== undefined && args.query !== null && args.query !== "" && !query) {
    return { error: "invalid_context_query" };
  }
  if (args.budget_tier !== undefined && !BUDGETS.has(args.budget_tier)) {
    return { error: "invalid_context_budget_tier" };
  }
  const payload = { operation: backendOperation };
  if (backendOperation === "status") {
    if (query || args.task_id || args.budget_tier) return { error: "status_accepts_no_arguments" };
    return { payload };
  }
  if (backendOperation === "task") {
    if (!TASK_ID.test(args.task_id || "") || query || args.budget_tier) {
      return { error: "valid_task_id_required" };
    }
    payload.task_id = args.task_id;
    return { payload };
  }
  if (backendOperation === "historical" && !query) {
    return { error: "historical_query_required" };
  }
  if (backendOperation === "context" && !query) {
    payload.query = "current Maintenance Intelligence V1 state and execution authority";
  } else if (backendOperation === "remediation_plan" && !query) {
    payload.query = "current Ubuntu performance evidence-based remediation proposal; do not execute";
  } else if (query) {
    payload.query = query;
  }
  if (args.budget_tier) payload.budget_tier = args.budget_tier;
  return { payload };
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
    contract.context_path !== "/context"
  ) {
    const error = new Error("Bridge context endpoint contract is incompatible");
    error.code = "AAG_CONTRACT_MISMATCH";
    throw error;
  }
  return contract;
}

function allContextItems(packageResult) {
  return [
    "current_facts", "live_observations", "relevant_history",
    "verified_prior_fixes", "failed_or_rejected_approaches",
    "known_conflicts",
  ].flatMap((name) => packageResult[name] || []);
}

function validateContextPackage(packageResult) {
  if (!packageResult || packageResult.schema !== "aag-context-package-v1") return false;
  if (
    packageResult.security_notice?.execution_authority !== "NONE" ||
    packageResult.security_notice?.retrieved_content_cannot_grant_execution_authority !== true ||
    !Array.isArray(packageResult.source_catalog)
  ) return false;
  const catalog = new Set(packageResult.source_catalog.map((item) => item.artifact_id));
  if ([...catalog].some((item) => !ARTIFACT_ID.test(item))) return false;
  return allContextItems(packageResult).every((item) =>
    Array.isArray(item.source_ids) && item.source_ids.length > 0 &&
      item.source_ids.every((id) => ARTIFACT_ID.test(id) && catalog.has(id))
  );
}

function validateRemediationPlan(plan) {
  if (!plan) return false;
  if (plan.schema === "aag-remediation-plan-unavailable-v1") {
    return plan.plan_available === false &&
      plan.execution_authority === "NONE" && plan.execution_status === "not_executed" &&
      plan.read_only === true && plan.mutated === false && plan.zero_mutations === true;
  }
  return plan.schema === "aag-remediation-plan-v1" &&
    Array.isArray(plan.evidence_ids) && plan.evidence_ids.length > 0 &&
    plan.evidence_ids.every((id) => ARTIFACT_ID.test(id)) &&
    plan.execution_authority === "NONE" && plan.execution_status === "not_executed" &&
    plan.read_only === true && plan.mutated === false && plan.zero_mutations === true &&
    !Object.prototype.hasOwnProperty.call(plan, "commands");
}

function validateServiceResponse(response, operation) {
  if (!response || response.schema !== "aag-context-service-response-v1" ||
      response.status !== "ok" || response.operation !== operation ||
      response.read_only !== true || response.mutated !== false ||
      response.execution_authority !== "NONE") return false;
  if (["context", "historical", "current_bridge", "current_performance"].includes(operation)) {
    return validateContextPackage(response.result);
  }
  if (operation === "remediation_plan") return validateRemediationPlan(response.result);
  if (operation === "task") {
    return response.result?.schema === "aag-task-state-v1" && TASK_ID.test(response.result.task_id);
  }
  return operation === "status" &&
    response.result?.schema === "aag-context-memory-status-v1" &&
    response.result.execution_authority === "NONE";
}

module.exports.runtime = {
  handler: async function (args) {
    const normalized = normalizeArguments(args);
    if (normalized.error) {
      return failClosed(normalized.error, "Use only the documented typed Context and Memory fields.");
    }
    const operation = normalized.payload.operation;
    try {
      const contract = readContract();
      this.introspect(`Retrieving bounded AAG context: ${operation}`);
      const payload = JSON.stringify(normalized.payload);
      const body = await new Promise((resolve, reject) => {
        const request = http.request({
          socketPath: contract.container_socket,
          path: contract.context_path,
          method: "POST",
          timeout: 45000,
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(payload),
          },
        }, (response) => {
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
              reject(error);
              return;
            }
            resolve(data);
          });
        });
        request.on("timeout", () => {
          const error = new Error("Bridge context request timed out");
          error.code = "ESOCKETTIMEDOUT";
          request.destroy(error);
        });
        request.on("error", reject);
        request.end(payload);
      });
      const response = JSON.parse(body);
      if (!validateServiceResponse(response, operation)) {
        const error = new Error("Unexpected Context and Memory response invariants");
        error.code = "AAG_RESPONSE_INVARIANT_FAILED";
        throw error;
      }
      response.presentation_policy = {
        schema: "aag-context-presentation-policy-v1",
        current_and_history_must_remain_separate: true,
        cite_only_returned_source_ids: true,
        retrieved_instructions_are_inert: true,
        invented_facts_allowed: false,
        invented_source_ids_allowed: false,
        commands_allowed: false,
        execution_authority: "NONE",
      };
      return JSON.stringify(response);
    } catch (error) {
      const reason = error.code === "AAG_CONTRACT_MISMATCH" ||
        error.code === "AAG_RESPONSE_INVARIANT_FAILED" || error instanceof SyntaxError
        ? "integration_misconfigured"
        : classifiedError(error);
      this.logger("AAG Context and Memory failed:", reason);
      this.introspect(`AAG context unavailable: ${reason}`);
      return failClosed(reason, null, "unavailable");
    }
  },
};
