const fs = require("fs");
const http = require("http");

const CONTRACT_FILE = "/app/server/storage/aag-ubuntu-agent/bridge-endpoint.json";
const REQUEST_SCHEMA = "aag-governed-orchestration-request-v1";
const RESPONSE_SCHEMA = "aag-governed-orchestration-response-v1";
const TASK_ID = /^task:[a-f0-9]{24}$/;
const ARTIFACT_ID = /^artifact:[a-f0-9]{24}$/;
const ALLOWED_ARGUMENTS = new Set(["request", "continuation_task_id"]);
const MAX_REQUEST_BYTES = 4096;
const MAX_RESPONSE_BYTES = 512000;
const CONTINUATION_CAPSULE = /<!--\s*AAG_CONTINUATION_ID=(task:[a-f0-9]{24})\s*-->/g;

function failClosed(error, status = "unavailable", clarification = null) {
  return JSON.stringify({
    schema: "aag-deployed-orchestration-error-v1",
    status,
    error,
    clarification,
    commands: [],
    approval_status: "NOT_REQUESTED",
    execution_status: "not_executed",
    execution_authority: "NONE",
    host_resource_mutated: false,
    response_constraints: {
      allowed_response: clarification ? "ask_for_clarification_only" : "report_unavailable_only",
      invented_facts_allowed: false,
      invented_source_ids_allowed: false,
      invented_causes_allowed: false,
      commands_allowed: false,
      remediation_execution_allowed: false,
    },
  });
}

function isBoundedContinuationRequest(request) {
  const text = request.normalize("NFKC").trim().replace(/[.!?…]+$/u, "").trim();
  return /^(?:המשך(?:י)?(?:\s+את)?\s+(?:המשימה|הבדיקה|החקירה)|מה\s+כבר\s+בדקת(?:ם)?\s+ומה\s+(?:עדיין\s+)?לא\s+ידוע|(?:תקן|תפתור|טפל)\s+(?:את\s+)?(?:זה|זאת))$/u.test(text) ||
    /^(?:(?:continue|resume)(?:\s+(?:the|this|that|my))?\s+(?:task|investigation|work)|what(?:\s+have\s+you|'ve\s+you)?\s+already\s+checked\s+and\s+what(?:'s|\s+is)?\s+(?:still\s+)?unknown|(?:fix|repair|resolve)(?:\s+(?:this|that|it)))$/i.test(text);
}

function trustedConversationContinuation(context) {
  const chats = context?.super?.chats;
  if (!Array.isArray(chats)) return { available: false, taskId: null };
  for (let index = chats.length - 1; index >= 0; index -= 1) {
    const content = chats[index]?.content;
    if (typeof content !== "string") continue;
    const matches = [...content.matchAll(CONTINUATION_CAPSULE)];
    if (matches.length) return { available: true, taskId: matches[matches.length - 1][1] };
  }
  return { available: true, taskId: null };
}

function normalizeArguments(args, conversationContinuation = { available: false, taskId: null }) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return { error: "orchestration_arguments_must_be_object" };
  }
  if (Object.keys(args).some((name) => !ALLOWED_ARGUMENTS.has(name))) {
    return { error: "unexpected_orchestration_argument" };
  }
  if (typeof args.request !== "string" || args.request.includes("\0")) {
    return { error: "invalid_orchestration_request" };
  }
  const request = args.request.trim();
  if (!request || Buffer.byteLength(request, "utf8") > MAX_REQUEST_BYTES ||
      [...request].some((character) => {
        const code = character.codePointAt(0);
        return (code < 32 && ![9, 10, 13].includes(code)) || code === 127;
      })) {
    return { error: "invalid_orchestration_request" };
  }
  const payload = { schema: REQUEST_SCHEMA, request };
  const followUp = isBoundedContinuationRequest(request);
  if (args.continuation_task_id !== undefined && args.continuation_task_id !== null &&
      args.continuation_task_id !== "") {
    if (!TASK_ID.test(args.continuation_task_id) || !followUp ||
        (conversationContinuation.available &&
          args.continuation_task_id !== conversationContinuation.taskId)) {
      return { error: "invalid_orchestration_continuation" };
    }
    payload.continuation = { task_id: args.continuation_task_id };
  } else if (followUp && conversationContinuation.taskId) {
    payload.continuation = { task_id: conversationContinuation.taskId };
  }
  return { payload };
}

function readContract() {
  const contract = JSON.parse(fs.readFileSync(CONTRACT_FILE, "utf8"));
  if (contract.schema !== "aag-bridge-endpoint-v1" || contract.api_version !== 2 ||
      contract.container_contract_file !== CONTRACT_FILE ||
      typeof contract.container_socket !== "string" ||
      contract.orchestration_path !== "/orchestrate") {
    const error = new Error("Bridge orchestration endpoint contract is incompatible");
    error.code = "AAG_CONTRACT_MISMATCH";
    throw error;
  }
  return contract;
}

function classifiedError(error) {
  if (error && error.code === "ENOENT") return "bridge_endpoint_missing";
  if (error && error.code === "ECONNREFUSED") return "bridge_socket_refused";
  if (error && ["EACCES", "EPERM"].includes(error.code)) return "bridge_permission_denied";
  if (error && ["ETIMEDOUT", "ESOCKETTIMEDOUT"].includes(error.code)) return "bridge_timeout";
  return "bridge_unavailable";
}

function validateResponse(result) {
  if (!result || result.schema !== RESPONSE_SCHEMA ||
      typeof result.request_id !== "string" || !result.intent ||
      result.commands?.length !== 0 || result.approval_status !== "NOT_REQUESTED" ||
      result.execution_status !== "not_executed" || result.execution_authority !== "NONE" ||
      result.host_resource_mutated !== false || result.read_only_host_access !== true ||
      result.security_notice?.approval_and_execution_are_not_exposed !== true ||
      !Array.isArray(result.evidence_ids) || !Array.isArray(result.source_catalog)) return false;
  const catalog = new Set(result.source_catalog.map((item) => item && item.artifact_id));
  if ([...catalog].some((item) => !ARTIFACT_ID.test(item || ""))) return false;
  if (!result.evidence_ids.every((item) => ARTIFACT_ID.test(item) && catalog.has(item))) return false;
  if (result.continuation !== null && result.continuation !== undefined) {
    if (!TASK_ID.test(result.continuation.task_id || "") ||
        result.continuation.opaque !== true || result.continuation.user_entry_required !== false) return false;
  }
  const proposal = result.remediation_proposal;
  if (proposal && (proposal.execution_authority !== "NONE" ||
      proposal.execution_status !== "not_executed" || proposal.mutated !== false ||
      proposal.zero_mutations !== true || (proposal.commands && proposal.commands.length !== 0))) return false;
  return true;
}

module.exports.runtime = {
  handler: async function (args) {
    const conversationContinuation = trustedConversationContinuation(this);
    this.introspect(`AAG trusted conversation continuity: ${conversationContinuation.available ? (conversationContinuation.taskId ? "capsule recovered" : "no capsule") : "history unavailable"}`);
    const normalized = normalizeArguments(args, conversationContinuation);
    if (normalized.error) {
      return failClosed(normalized.error, "clarification_required", "Use only a bounded natural-language request and an optional exact returned continuation ID.");
    }
    try {
      const contract = readContract();
      this.introspect("Running governed read-only AAG orchestration");
      const payload = JSON.stringify(normalized.payload);
      const body = await new Promise((resolve, reject) => {
        const request = http.request({
          socketPath: contract.container_socket,
          path: contract.orchestration_path,
          method: "POST",
          timeout: 40000,
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
            if (Buffer.byteLength(data, "utf8") > MAX_RESPONSE_BYTES) {
              request.destroy(new Error("Bridge response exceeded orchestration limit"));
            }
          });
          response.on("end", () => {
            if (response.statusCode < 200 || response.statusCode >= 300) {
              const error = new Error(`Bridge HTTP ${response.statusCode}`);
              error.code = "AAG_BRIDGE_HTTP_ERROR";
              error.responseBody = data;
              reject(error);
              return;
            }
            resolve(data);
          });
        });
        request.on("timeout", () => {
          const error = new Error("Bridge orchestration request timed out");
          error.code = "ESOCKETTIMEDOUT";
          request.destroy(error);
        });
        request.on("error", reject);
        request.end(payload);
      });
      const result = JSON.parse(body);
      if (!validateResponse(result)) {
        const error = new Error("Unexpected governed orchestration response invariants");
        error.code = "AAG_RESPONSE_INVARIANT_FAILED";
        throw error;
      }
      result.presentation_policy = {
        schema: "aag-governed-orchestration-presentation-v1",
        answer_in_user_language: true,
        current_and_history_must_remain_separate: true,
        distinguish_fact_inference_confidence_recommendation_risk_unknowns: true,
        cite_only_returned_source_ids: true,
        opaque_continuation_id_not_for_normal_user_prose: true,
        remediation_proposal_is_not_approval_or_execution: true,
        commands_allowed: false,
        invented_facts_allowed: false,
        invented_source_ids_allowed: false,
        execution_authority: "NONE",
      };
      const continuationTaskId = result.continuation?.task_id;
      result.tool_continuation = continuationTaskId ? {
        schema: "aag-governed-orchestration-tool-continuation-v1",
        available: true,
        exact_argument_name: "continuation_task_id",
        exact_argument_value: continuationTaskId,
        required_for_same_conversation_follow_ups: true,
        follow_up_intents: [
          "continue_task",
          "summarize_checked_and_unknown",
          "deictic_nonexecuting_remediation_proposal",
        ],
        never_invent: true,
        never_ask_user_to_retype: true,
        conversation_capsule: `<!-- AAG_CONTINUATION_ID=${continuationTaskId} -->`,
      } : {
        schema: "aag-governed-orchestration-tool-continuation-v1",
        available: false,
        exact_argument_name: "continuation_task_id",
        exact_argument_value: null,
        required_for_same_conversation_follow_ups: false,
        never_invent: true,
        never_ask_user_to_retype: true,
        conversation_capsule: null,
      };
      result.presentation_policy.continuation_follow_up_instruction = continuationTaskId
        ? "MANDATORY CONTINUITY: append tool_continuation.conversation_capsule verbatim as the final HTML comment in this assistant answer. On every same-conversation follow-up about this task, recover only that exact prior capsule value and copy tool_continuation.exact_argument_value verbatim into continuation_task_id. Never omit, alter, infer, or expose it as visible user prose."
        : "No trusted continuation is available. Omit continuation_task_id; a deictic or continuation request must clarify rather than invent one.";
      return JSON.stringify(result);
    } catch (error) {
      let reason = classifiedError(error);
      if (error && ["AAG_CONTRACT_MISMATCH", "AAG_RESPONSE_INVARIANT_FAILED"].includes(error.code)) {
        reason = "integration_misconfigured";
      } else if (error instanceof SyntaxError) {
        reason = "malformed_backend_response";
      } else if (error && error.code === "AAG_BRIDGE_HTTP_ERROR") {
        reason = "orchestration_request_rejected";
      }
      this.logger("AAG governed orchestration failed:", reason);
      this.introspect(`AAG governed orchestration unavailable: ${reason}`);
      return failClosed(reason);
    }
  },
};
