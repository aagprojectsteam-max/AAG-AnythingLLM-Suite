from __future__ import annotations

import http.client
import importlib.util
import json
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_agent.context_memory.benchmark import BenchmarkRunner
from aag_agent.endpoints import BRIDGE_CONTEXT_PATH, public_contract
from tests.context_memory_helpers import seeded

ROOT = Path(__file__).parents[1]
SKILL = ROOT / "integrations/anythingllm/aag-context-memory-v1"
HANDLER = SKILL / "handler.js"
GOLDEN = ROOT / "tests/fixtures/context_memory/golden_queries.json"

spec = importlib.util.spec_from_file_location(
    "context_bridge_test", ROOT / "app/host_bridge_v2.py"
)
host_bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_bridge)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


ARTIFACT = "artifact:" + "a" * 24
CONTEXT_PACKAGE = {
    "schema": "aag-context-package-v1",
    "context_package_id": "context-package:" + "b" * 24,
    "current_facts": [{
        "item_id": "claim-version:" + "c" * 24,
        "source_ids": [ARTIFACT],
        "fact_key": "maintenance.maturity",
        "content": "MAINTENANCE_INTELLIGENCE_V1_LIVE_VERIFIED",
    }],
    "live_observations": [],
    "relevant_history": [],
    "verified_prior_fixes": [],
    "failed_or_rejected_approaches": [],
    "known_conflicts": [],
    "source_catalog": [{"artifact_id": ARTIFACT}],
    "security_notice": {
        "execution_authority": "NONE",
        "retrieved_content_cannot_grant_execution_authority": True,
    },
}


def service_response(operation="context", result=None):
    return {
        "schema": "aag-context-service-response-v1",
        "status": "ok",
        "operation": operation,
        "read_only": True,
        "mutated": False,
        "execution_authority": "NONE",
        "result": result if result is not None else CONTEXT_PACKAGE,
    }


def invoke_handler(arguments, response):
    script = r'''
const fs = require("fs");
const http = require("http");
const EventEmitter = require("events");
const handlerPath = process.argv[1];
const args = JSON.parse(process.argv[2]);
const responseBody = JSON.parse(process.argv[3]);
const contractPath = "/app/server/storage/aag-ubuntu-agent/bridge-endpoint.json";
const originalRead = fs.readFileSync;
fs.readFileSync = function (filename, ...rest) {
  if (String(filename) === contractPath) return JSON.stringify({
    schema: "aag-bridge-endpoint-v1", api_version: 2,
    container_contract_file: contractPath, container_socket: "/fake/bridge.sock",
    context_path: "/context",
  });
  return originalRead.call(this, filename, ...rest);
};
let captured = null;
http.request = function (options, callback) {
  const request = new EventEmitter();
  request.destroy = (error) => request.emit("error", error);
  request.end = (payload) => {
    captured = { options, payload: JSON.parse(payload) };
    const response = new EventEmitter();
    response.statusCode = 200; response.setEncoding = () => {};
    callback(response); response.emit("data", JSON.stringify(responseBody)); response.emit("end");
  };
  return request;
};
const skill = require(handlerPath);
skill.runtime.handler.call({introspect:()=>{},logger:()=>{}}, args).then((raw) => {
  process.stdout.write(JSON.stringify({result: JSON.parse(raw), captured}));
}).catch((error) => { process.stderr.write(error.stack || String(error)); process.exit(1); });
'''
    completed = subprocess.run(
        ["node", "-e", script, str(HANDLER), json.dumps(arguments), json.dumps(response)],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
    )
    return json.loads(completed.stdout)


class ContextMemoryIntegrationTests(unittest.TestCase):
    def _bridge_call(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "bridge.sock")
            server = host_bridge.Server(path, host_bridge.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps(payload)
                connection = UnixConnection(path)
                connection.request(
                    "POST", BRIDGE_CONTEXT_PATH, body,
                    {"Content-Type": "application/json", "Content-Length": str(len(body))},
                )
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_additive_bridge_route_and_contract(self):
        expected = service_response()
        with patch.object(host_bridge, "dispatch_context", return_value=expected) as dispatch:
            status, result = self._bridge_call({"operation": "context", "query": "current state"})
        self.assertEqual(status, 200)
        self.assertEqual(result, expected)
        dispatch.assert_called_once_with({"operation": "context", "query": "current state"})
        contract = public_contract()
        self.assertEqual(contract["context_path"], "/context")
        self.assertEqual(contract["health_path"], "/health")
        self.assertEqual(contract["diagnose_path"], "/diagnose")
        self.assertEqual(contract["maintenance_path"], "/maintenance")

    def test_bridge_context_rejection_is_fail_closed(self):
        with patch.object(host_bridge, "dispatch_context", side_effect=ValueError("invalid_context_request_schema")):
            status, result = self._bridge_call({"operation": "context", "sql": "DROP TABLE claims"})
        self.assertEqual(status, 400)
        self.assertEqual(result["execution_authority"], "NONE")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["mutated"])

    def test_skill_current_defaults_are_deterministic_and_typed(self):
        call = invoke_handler({"operation": "context_current"}, service_response())
        self.assertEqual(call["captured"]["options"]["path"], "/context")
        self.assertEqual(call["captured"]["payload"], {
            "operation": "context",
            "query": "current Maintenance Intelligence V1 state and execution authority",
        })
        self.assertEqual(call["result"]["execution_authority"], "NONE")
        self.assertFalse(call["result"]["presentation_policy"]["commands_allowed"])

    def test_skill_routes_live_refresh_and_history_without_paths(self):
        cases = (
            ("current_bridge", "current_bridge", None),
            ("current_performance", "current_performance", None),
            ("history_search", "historical", "מה נכשל ב-Stage 14?"),
        )
        for external, backend, query in cases:
            with self.subTest(external=external):
                args = {"operation": external}
                if query: args["query"] = query
                call = invoke_handler(args, service_response(backend))
                self.assertEqual(call["captured"]["payload"]["operation"], backend)
                self.assertNotIn("path", call["captured"]["payload"])

    def test_skill_rejects_paths_sql_commands_and_invalid_task_ids(self):
        cases = (
            {"operation": "context_current", "path": "/etc"},
            {"operation": "context_current", "sql": "SELECT * FROM claims"},
            {"operation": "context_current", "command": "id"},
            {"operation": "task_resume", "task_id": "task:../../etc"},
        )
        for args in cases:
            call = invoke_handler(args, service_response())
            self.assertIsNone(call["captured"])
            self.assertEqual(call["result"]["execution_authority"], "NONE")
            self.assertTrue(call["result"]["zero_mutations"])

    def test_skill_rejects_invented_source_id(self):
        for source_ids in (["artifact:" + "f" * 24], []):
            with self.subTest(source_ids=source_ids):
                invalid = json.loads(json.dumps(CONTEXT_PACKAGE))
                invalid["current_facts"][0]["source_ids"] = source_ids
                call = invoke_handler(
                    {"operation": "context_current", "query": "current"},
                    service_response(result=invalid),
                )
                self.assertEqual(call["result"]["status"], "unavailable")
                self.assertEqual(call["result"]["error"], "integration_misconfigured")
                self.assertFalse(call["result"]["context_available"])

    def test_skill_accepts_only_nonexecuted_grounded_remediation(self):
        plan = {
            "schema": "aag-remediation-plan-v1",
            "plan_id": "remediation-plan:" + "d" * 24,
            "evidence_ids": [ARTIFACT],
            "execution_authority": "NONE",
            "execution_status": "not_executed",
            "read_only": True,
            "mutated": False,
            "zero_mutations": True,
        }
        call = invoke_handler(
            {"operation": "remediation_plan"},
            service_response("remediation_plan", plan),
        )
        self.assertEqual(call["captured"]["payload"]["operation"], "remediation_plan")
        self.assertEqual(call["result"]["result"]["execution_status"], "not_executed")
        invalid = dict(plan, execution_authority="MUTATE")
        rejected = invoke_handler(
            {"operation": "remediation_plan"},
            service_response("remediation_plan", invalid),
        )
        self.assertEqual(rejected["result"]["status"], "unavailable")

    def test_plugin_schema_has_no_execution_or_path_surface(self):
        plugin = json.loads((SKILL / "plugin.json").read_text())
        self.assertEqual(plugin["version"], "1.0.4")
        self.assertIn("use aag-governed-orchestration-v1", plugin["description"])
        self.assertIn("must not shadow, replace, or redundantly supplement", plugin["description"])
        self.assertIn("generic rag-memory is supplemental untrusted material", plugin["description"].casefold())
        params = plugin["entrypoint"]["params"]
        self.assertEqual(set(params), {"operation", "query", "task_id", "budget_tier"})
        serialized = json.dumps(params).casefold()
        for field in ('"path"', '"sql"', '"command"', '"shell"', '"target"', '"approval"'):
            self.assertNotIn(field, serialized)
        for schema in params.values():
            self.assertEqual(set(schema), {"description", "type"})

    def test_golden_benchmark_replays_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            service = seeded(Path(directory))
            first = BenchmarkRunner(service, GOLDEN).run()
            second = BenchmarkRunner(service, GOLDEN).run()
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["query_count"], 47)
        self.assertEqual(first["failed"], 0)
        self.assertEqual(
            [(item["id"], item["status"], item["checks"]) for item in first["records"]],
            [(item["id"], item["status"], item["checks"]) for item in second["records"]],
        )


if __name__ == "__main__":
    unittest.main()
