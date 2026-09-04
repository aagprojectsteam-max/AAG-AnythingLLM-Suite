from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "integrations/anythingllm/aag-maintenance-intelligence"
HANDLER = SKILL / "handler.js"


SUCCESSFUL_PLAN = {
    "schema": "aag-maintenance-scan-envelope-v1",
    "schema_version": "1.0",
    "completeness": {"status": "complete", "errors": 0},
    "read_only": True,
    "mutated": False,
    "result": {
        "schema": "aag-maintenance-plan-v1",
        "execution_authority": "NONE",
        "zero_mutations": True,
        "items": [{
            "item_id": "maint-v1-00000000000000000000",
            "target": "/mnt/data/AI/Models",
            "classification": "REVIEW_REQUIRED",
            "estimated_reclaimable_bytes": "unknown",
            "execution_status": "not_executed",
        }],
        "root": "/mnt/data/AI",
        "estimated_reclaimable_bytes": 0,
        "execution_status": "not_executed",
    },
}


def invoke_handler(arguments, *, status=200, body=None):
    script = r"""
const fs = require("fs");
const http = require("http");
const EventEmitter = require("events");
const handlerPath = process.argv[1];
const args = JSON.parse(process.argv[2]);
const responseStatus = Number(process.argv[3]);
const responseBody = JSON.parse(process.argv[4]);
const contractPath = "/app/server/storage/aag-ubuntu-agent/bridge-endpoint.json";
const originalRead = fs.readFileSync;
fs.readFileSync = function (filename, ...rest) {
  if (String(filename) === contractPath) {
    return JSON.stringify({
      schema: "aag-bridge-endpoint-v1",
      api_version: 2,
      container_contract_file: contractPath,
      container_socket: "/fake/bridge.sock",
      maintenance_path: "/maintenance",
    });
  }
  return originalRead.call(this, filename, ...rest);
};
let captured = null;
http.request = function (_options, callback) {
  const request = new EventEmitter();
  request.destroy = (error) => request.emit("error", error);
  request.end = (payload) => {
    captured = JSON.parse(payload);
    const response = new EventEmitter();
    response.statusCode = responseStatus;
    response.setEncoding = () => {};
    callback(response);
    response.emit("data", JSON.stringify(responseBody));
    response.emit("end");
  };
  return request;
};
const skill = require(handlerPath);
const context = { introspect: () => {}, logger: () => {} };
skill.runtime.handler.call(context, args).then((raw) => {
  process.stdout.write(JSON.stringify({ result: JSON.parse(raw), captured }));
}).catch((error) => {
  process.stderr.write(error.stack || String(error));
  process.exit(1);
});
"""
    completed = subprocess.run(
        [
            "node",
            "-e",
            script,
            str(HANDLER),
            json.dumps(arguments),
            str(status),
            json.dumps(body or SUCCESSFUL_PLAN),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return json.loads(completed.stdout)


class MaintenanceSkillGroundingTests(unittest.TestCase):
    def test_pathless_hebrew_and_english_plan_requests_use_trusted_default(self):
        natural_requests = (
            "תכין לי תוכנית ניקוי אבל אל תמחק כלום.",
            "Prepare a cleanup plan, but do not delete or change anything.",
        )
        for request in natural_requests:
            with self.subTest(request=request):
                call = invoke_handler({"operation": "maintenance_plan", "profile": "standard"})
                self.assertEqual(call["captured"]["tool"], "maintenance.plan")
                self.assertEqual(call["captured"]["arguments"]["path"], "/mnt/data/AI")
                self.assertEqual(call["result"]["result"]["execution_authority"], "NONE")
                self.assertTrue(call["result"]["result"]["zero_mutations"])
                self.assertTrue(all(
                    item["execution_status"] == "not_executed"
                    for item in call["result"]["result"]["items"]
                ))
                self.assertEqual(call["result"]["result"]["grounded_recommendations"], [])
                policy = call["result"]["result"]["presentation_policy"]
                self.assertFalse(policy["commands_allowed"])
                self.assertFalse(policy["ungrounded_estimates_allowed"])
                self.assertFalse(policy["deletion_recommendations_for_other_items_allowed"])
                self.assertEqual(policy["evidence_backed_candidate_count"], 0)

    def test_explicit_trusted_path_is_preserved_and_normalized(self):
        call = invoke_handler({
            "operation": "maintenance_plan",
            "path": "/mnt/data/AI/Models/../Models",
            "profile": "quick",
        })
        self.assertEqual(call["captured"]["arguments"], {
            "path": "/mnt/data/AI/Models",
            "profile": "quick",
        })

    def test_untrusted_path_fails_before_bridge_with_structured_clarification(self):
        call = invoke_handler({
            "operation": "maintenance_plan",
            "path": "/etc",
            "profile": "standard",
        })
        self.assertIsNone(call["captured"])
        result = call["result"]
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["error"], "trusted_maintenance_path_required")
        self.assertFalse(result["plan_available"])
        self.assertEqual(result["result"]["execution_authority"], "NONE")
        self.assertTrue(result["result"]["zero_mutations"])
        self.assertEqual(result["result"]["items"], [])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["mutated"])

    def test_missing_and_invalid_arguments_fail_closed(self):
        cases = (
            None,
            {},
            {"operation": "maintenance_plan", "path": 7},
            {"operation": "maintenance_plan", "profile": "unbounded"},
            {"operation": "run_shell", "command": "id"},
            {"operation": "storage_top"},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                call = invoke_handler(arguments)
                self.assertIsNone(call["captured"])
                result = call["result"]
                self.assertFalse(result["plan_available"])
                self.assertEqual(result["result"]["execution_authority"], "NONE")
                self.assertTrue(result["result"]["zero_mutations"])
                self.assertTrue(result["read_only"])
                self.assertFalse(result["mutated"])

    def test_failed_core_call_cannot_become_ungrounded_executable_plan(self):
        failed_core = {
            "schema": "aag-maintenance-scan-envelope-v1",
            "schema_version": "1.0",
            "completeness": {"status": "failed"},
            "errors": [{"code": "path_outside_allowed_scope"}],
            "read_only": True,
            "mutated": False,
        }
        call = invoke_handler(
            {"operation": "maintenance_plan", "path": "/mnt/data", "profile": "quick"},
            status=400,
            body=failed_core,
        )
        result = call["result"]
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["error"], "maintenance_request_rejected")
        self.assertFalse(result["plan_available"])
        self.assertFalse(result["manual_commands_suggested"])
        self.assertEqual(result["result"], {
            "execution_authority": "NONE",
            "zero_mutations": True,
            "items": [],
        })
        serialized = json.dumps(result).lower()
        for forbidden in ("sudo ", "system prune", "apt clean", "autoremove", "vacuum-time"):
            self.assertNotIn(forbidden, serialized)

    def test_successful_plan_response_must_preserve_all_plan_invariants(self):
        invalid = dict(SUCCESSFUL_PLAN)
        invalid["result"] = dict(SUCCESSFUL_PLAN["result"], execution_authority="MUTATE")
        call = invoke_handler(
            {"operation": "maintenance_plan", "profile": "standard"},
            body=invalid,
        )
        result = call["result"]
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["result"]["execution_authority"], "NONE")
        self.assertTrue(result["result"]["zero_mutations"])
        self.assertEqual(result["result"]["items"], [])

    def test_grounded_recommendations_include_only_typed_positive_low_risk_items(self):
        response = json.loads(json.dumps(SUCCESSFUL_PLAN))
        response["result"]["items"].append({
            "item_id": "maint-v1-11111111111111111111",
            "target": "/mnt/data/AI/Outputs",
            "classification": "LOW_RISK_CANDIDATE",
            "reason": "Registered generated output",
            "estimated_reclaimable_bytes": 4096,
            "risk": "R4",
            "required_approval_level": "strong_confirmation_required",
            "required_backup_or_rollback": "verified backup required",
            "execution_status": "not_executed",
        })
        response["result"]["estimated_reclaimable_bytes"] = 4096
        call = invoke_handler(
            {"operation": "maintenance_plan", "profile": "standard"},
            body=response,
        )
        plan = call["result"]["result"]
        self.assertEqual([item["target"] for item in plan["grounded_recommendations"]], [
            "/mnt/data/AI/Outputs"
        ])
        self.assertEqual(plan["presentation_policy"]["evidence_backed_candidate_count"], 1)
        self.assertNotIn("/mnt/data/AI/Models", json.dumps(plan["grounded_recommendations"]))

    def test_manifest_and_core_policy_remain_aligned(self):
        plugin = json.loads((SKILL / "plugin.json").read_text())
        config = json.loads((ROOT / "config/maintenance-v1.json").read_text())
        catalog = json.loads((ROOT / "contracts/maintenance/tool-catalog.v1.json").read_text())
        self.assertEqual(plugin["version"], "1.0.2")
        self.assertIn("/mnt/data/AI", plugin["entrypoint"]["params"]["path"]["description"])
        self.assertEqual(config["snapshot_roots"][0], "/mnt/data/AI")
        self.assertEqual(config["allowed_scope_roots"], ["/mnt/data", "/var/log"])
        self.assertEqual(len(catalog["tools"]), 13)
        plan = next(item for item in catalog["tools"] if item["tool"] == "maintenance.plan")
        self.assertEqual(plan["required"], ["path"])
        self.assertEqual(catalog["execution_authority"], "NONE")


if __name__ == "__main__":
    unittest.main()
