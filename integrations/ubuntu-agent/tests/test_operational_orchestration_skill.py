from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from aag_agent.orchestration.contracts import REQUEST_SCHEMA, RESPONSE_SCHEMA

ROOT = Path(__file__).parents[1]
SKILL = ROOT / "integrations/anythingllm/aag-governed-orchestration-v1"
HANDLER = SKILL / "handler.js"


def response():
    return {
        "schema": RESPONSE_SCHEMA,
        "request_id": "orchestration-request:" + "a" * 24,
        "intent": {"schema": "aag-orchestration-intent-v2", "intent": "CONTEXT_QUERY"},
        "status": "CONTEXT_ASSEMBLED",
        "task": None, "continuation": None, "current": None, "historical": None,
        "context": None, "investigation": None, "facts": [], "inferences": [],
        "data_completeness": {"status": "COMPLETE", "limitations": []},
        "recommendations": [], "risk": {"class": "R0", "host_mutation": False},
        "unknowns": [], "evidence_ids": [], "source_catalog": [],
        "remediation_proposal": None, "timing": {"duration_ms": 1},
        "commands": [], "approval_status": "NOT_REQUESTED",
        "execution_status": "not_executed", "execution_authority": "NONE",
        "read_only_host_access": True, "host_resource_mutated": False,
        "zero_host_mutations": True, "project_state_updated": False,
        "security_notice": {"approval_and_execution_are_not_exposed": True},
    }


def invoke(arguments, body=None, status=200, malformed=False, previous_assistant=None):
    script = r'''
const fs = require("fs");
const http = require("http");
const EventEmitter = require("events");
const handlerPath = process.argv[1];
const args = JSON.parse(process.argv[2]);
const responseBody = JSON.parse(process.argv[3]);
const responseStatus = Number(process.argv[4]);
const malformed = process.argv[5] === "true";
const previousAssistant = JSON.parse(process.argv[6]);
const contractPath = "/app/server/storage/aag-ubuntu-agent/bridge-endpoint.json";
const originalRead = fs.readFileSync;
fs.readFileSync = function (filename, ...rest) {
  if (String(filename) === contractPath) return JSON.stringify({
    schema: "aag-bridge-endpoint-v1", api_version: 2,
    container_contract_file: contractPath, container_socket: "/fake/bridge.sock",
    orchestration_path: "/orchestrate",
  });
  return originalRead.call(this, filename, ...rest);
};
let captured = null;
http.request = function (options, callback) {
  const request = new EventEmitter();
  request.destroy = (error) => request.emit("error", error);
  request.end = (payload) => {
    captured = {options, payload: JSON.parse(payload)};
    const response = new EventEmitter();
    response.statusCode = responseStatus;
    response.setEncoding = () => {};
    callback(response);
    response.emit("data", malformed ? "{bad" : JSON.stringify(responseBody));
    response.emit("end");
  };
  return request;
};
const skill = require(handlerPath);
const chats = previousAssistant === null ? [] : [
  {from: "@user", to: "@agent", content: "prior request", state: "success"},
  {from: "@agent", to: "@user", content: previousAssistant, state: "success"},
];
skill.runtime.handler.call({introspect:()=>{},logger:()=>{},super:{chats}}, args).then((raw) => {
  process.stdout.write(JSON.stringify({result: JSON.parse(raw), captured}));
}).catch((error) => { process.stderr.write(error.stack || String(error)); process.exit(1); });
'''
    completed = subprocess.run(
        ["node", "-e", script, str(HANDLER), json.dumps(arguments), json.dumps(body or response()), str(status), str(malformed).lower(), json.dumps(previous_assistant)],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
    )
    return json.loads(completed.stdout)


class OperationalOrchestrationSkillTests(unittest.TestCase):
    def test_natural_request_maps_to_exact_bridge_contract(self):
        call = invoke({"request": "הסוכן עצמו תקין?"})
        self.assertEqual(call["captured"]["options"]["path"], "/orchestrate")
        self.assertEqual(call["captured"]["payload"], {"schema": REQUEST_SCHEMA, "request": "הסוכן עצמו תקין?"})
        self.assertEqual(call["result"]["execution_authority"], "NONE")
        self.assertFalse(call["result"]["presentation_policy"]["commands_allowed"])

    def test_opaque_continuation_is_nested_and_never_required_from_user(self):
        task_id = "task:" + "b" * 24
        body = response()
        body["continuation"] = {"task_id": task_id, "opaque": True, "user_entry_required": False}
        prior = f"Prior result.\n\n<!-- AAG_CONTINUATION_ID={task_id} -->"
        call = invoke({"request": "המשך את המשימה", "continuation_task_id": task_id}, body, previous_assistant=prior)
        self.assertEqual(call["captured"]["payload"]["continuation"], {"task_id": task_id})
        self.assertTrue(call["result"]["presentation_policy"]["opaque_continuation_id_not_for_normal_user_prose"])
        self.assertEqual(call["result"]["tool_continuation"], {
            "schema": "aag-governed-orchestration-tool-continuation-v1",
            "available": True,
            "exact_argument_name": "continuation_task_id",
            "exact_argument_value": task_id,
            "required_for_same_conversation_follow_ups": True,
            "follow_up_intents": [
                "continue_task",
                "summarize_checked_and_unknown",
                "deictic_nonexecuting_remediation_proposal",
            ],
            "never_invent": True,
            "never_ask_user_to_retype": True,
            "conversation_capsule": f"<!-- AAG_CONTINUATION_ID={task_id} -->",
        })
        self.assertIn("append tool_continuation.conversation_capsule verbatim", call["result"]["presentation_policy"]["continuation_follow_up_instruction"])

    def test_fresh_result_explicitly_forbids_continuation_invention(self):
        call = invoke({"request": "תקן את זה"})
        self.assertFalse(call["result"]["tool_continuation"]["available"])
        self.assertIsNone(call["result"]["tool_continuation"]["exact_argument_value"])
        self.assertIsNone(call["result"]["tool_continuation"]["conversation_capsule"])
        self.assertIn("must clarify", call["result"]["presentation_policy"]["continuation_follow_up_instruction"])

    def test_handler_recovers_exact_same_conversation_capsule_for_bounded_followups(self):
        task_id = "task:" + "c" * 24
        prior = f"Measured facts.\n\n<!-- AAG_CONTINUATION_ID={task_id} -->"
        for request in ("המשך את המשימה.", "מה כבר בדקת ומה עדיין לא ידוע?", "תקן את זה.", "Continue the task.", "Fix it."):
            with self.subTest(request=request):
                call = invoke({"request": request}, previous_assistant=prior)
                self.assertEqual(call["captured"]["payload"]["continuation"], {"task_id": task_id})

    def test_capsule_is_not_applied_to_unrelated_request_and_invented_id_is_rejected(self):
        task_id = "task:" + "d" * 24
        prior = f"Measured facts.\n\n<!-- AAG_CONTINUATION_ID={task_id} -->"
        unrelated = invoke({"request": "מה תופס מקום בדיסק?"}, previous_assistant=prior)
        self.assertNotIn("continuation", unrelated["captured"]["payload"])
        invented = invoke({"request": "המשך את המשימה", "continuation_task_id": "task:" + "e" * 24}, previous_assistant=prior)
        self.assertIsNone(invented["captured"])
        self.assertEqual(invented["result"]["status"], "clarification_required")

    def test_infrastructure_and_unknown_arguments_fail_before_bridge(self):
        cases = (
            {"request": "status", "path": "/etc"},
            {"request": "status", "service": "evil.service"},
            {"request": "status", "operation_id": "evil.restart"},
            {"request": "status", "command": "id"},
            {"request": "status", "continuation_task_id": "invented"},
            {"request": "x" * 4097},
            None,
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                call = invoke(arguments)
                self.assertIsNone(call["captured"])
                self.assertEqual(call["result"]["execution_authority"], "NONE")
                self.assertEqual(call["result"]["commands"], [])

    def test_authority_violating_and_malformed_backend_output_fail_closed(self):
        changed = response()
        changed["approval_status"] = "APPROVED"
        for body, malformed in ((changed, False), (response(), True)):
            with self.subTest(malformed=malformed):
                call = invoke({"request": "status"}, body, malformed=malformed)
                self.assertEqual(call["result"]["status"], "unavailable")
                self.assertEqual(call["result"]["execution_authority"], "NONE")
                self.assertFalse(call["result"]["host_resource_mutated"])

    def test_invented_source_id_is_rejected(self):
        body = response()
        body["evidence_ids"] = ["artifact:" + "f" * 24]
        call = invoke({"request": "current status"}, body)
        self.assertEqual(call["result"]["error"], "integration_misconfigured")
        self.assertFalse(call["result"]["response_constraints"]["invented_source_ids_allowed"])

    def test_plugin_schema_is_one_portable_nonexecuting_front_door(self):
        plugin = json.loads((SKILL / "plugin.json").read_text())
        self.assertEqual(plugin["version"], "1.0.4")
        self.assertEqual(set(plugin["entrypoint"]["params"]), {"request", "continuation_task_id"})
        serialized = json.dumps(plugin["entrypoint"]["params"]).casefold()
        for field in ('"command"', '"shell"', '"argv"', '"sql"', '"path"', '"service"', '"operation_id"', '"approval"', '"token"'):
            self.assertNotIn(field, serialized)
        self.assertIn("preferred governed front door", plugin["description"].casefold())
        self.assertIn("append tool_continuation.conversation_capsule verbatim", plugin["description"].casefold())
        self.assertIn("supported_contributor is only a possible contributor", plugin["description"].casefold())


if __name__ == "__main__":
    unittest.main()
