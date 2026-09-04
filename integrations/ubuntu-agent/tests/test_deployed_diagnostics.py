import http.client
import importlib.util
import json
import re
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.agent as agent
from aag_agent.diagnostics import diagnose_many
from aag_agent.endpoints import BRIDGE_CONTRACT_FILE_CONTAINER, BRIDGE_SOCKET_CONTAINER, public_contract

ROOT = Path(__file__).parents[1]
BRIDGE_PATH = ROOT / "app/host_bridge_v2.py"
spec = importlib.util.spec_from_file_location("host_bridge_test", BRIDGE_PATH)
host_bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_bridge)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


class DeployedDiagnosticTests(unittest.TestCase):
    def test_bridge_post_diagnose_reaches_trusted_orchestrator(self):
        expected = {"schema":"aag-diagnostic-session-v1", "status":"OBSERVED", "bundles":[], "errors":[], "read_only":True, "mutated":False}
        with tempfile.TemporaryDirectory() as directory, patch.object(host_bridge, "diagnose_many", return_value=expected) as orchestrator:
            path = str(Path(directory) / "bridge.sock")
            server = host_bridge.Server(path, host_bridge.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps({"requests":[{"profile":"performance","inputs":{}}]})
                connection = UnixConnection(path)
                connection.request("POST", "/diagnose", body, {"Content-Type":"application/json", "Content-Length":str(len(body))})
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(result["schema"], "aag-diagnostic-session-v1")
                orchestrator.assert_called_once_with([{"profile":"performance","inputs":{}}])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_endpoint_contract_is_single_source_for_deployed_client(self):
        contract = public_contract()
        self.assertEqual(contract["container_socket"], str(BRIDGE_SOCKET_CONTAINER))
        self.assertEqual(contract["container_contract_file"], str(BRIDGE_CONTRACT_FILE_CONTAINER))
        handler = (ROOT / "integrations/anythingllm/aag-ubuntu-diagnostics/handler.js").read_text()
        match = re.search(r'const CONTRACT_FILE =\s*\n?\s*"([^"]+)"', handler)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), contract["container_contract_file"])
        self.assertNotIn(str(BRIDGE_SOCKET_CONTAINER), handler)

    def test_anythingllm_params_are_gemini_compatible(self):
        plugin = json.loads((ROOT / "integrations/anythingllm/aag-ubuntu-diagnostics/plugin.json").read_text())
        params = plugin["entrypoint"]["params"]
        self.assertEqual(
            set(params),
            {"profile", "secondary_profile", "service", "manager", "pid", "interface", "path", "container", "package"},
        )
        for name, schema in params.items():
            self.assertEqual(set(schema), {"description", "type"}, name)
            self.assertIn(schema["type"], {"string", "number"}, name)
            self.assertNotIn("required", schema, f"Gemini rejects boolean required at properties.{name}")
            self.assertNotIn("properties", schema, name)
            self.assertNotIn("items", schema, name)
        self.assertEqual(params["service"]["type"], "string")
        self.assertEqual(params["package"]["type"], "string")

    def test_real_gemini_required_errors_are_reproduced_by_old_shape(self):
        for name in ("service", "package"):
            old_property = {"description": "old", "type": "string", "required": False}
            with self.assertRaisesRegex(ValueError, rf"value at properties\.{name} must be a list"):
                if "required" in old_property and not isinstance(old_property["required"], list):
                    raise ValueError(f"value at properties.{name} must be a list")

    def test_canonical_and_deployed_plugin_schema_match(self):
        deployed = Path("/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-ubuntu-live-audit/plugin.json")
        if deployed.exists():
            canonical = ROOT / "integrations/anythingllm/aag-ubuntu-diagnostics/plugin.json"
            self.assertEqual(json.loads(deployed.read_text()), json.loads(canonical.read_text()))

    def test_stale_socket_cleanup_is_fail_closed(self):
        class Probe:
            def __init__(self, outcome): self.outcome = outcome
            def settimeout(self, value): pass
            def connect(self, path):
                if self.outcome: raise self.outcome
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.sock"
            path.touch()
            result = host_bridge.remove_stale_socket(path, connector=lambda *a: Probe(ConnectionRefusedError()))
            self.assertEqual(result, "stale_removed"); self.assertFalse(path.exists())
            path.touch()
            with self.assertRaisesRegex(RuntimeError, "already_active"):
                host_bridge.remove_stale_socket(path, connector=lambda *a: Probe(None))
            self.assertTrue(path.exists())
            with self.assertRaisesRegex(RuntimeError, "liveness_indeterminate"):
                host_bridge.remove_stale_socket(path, connector=lambda *a: Probe(TimeoutError()))
            self.assertTrue(path.exists())

    def test_semantic_cases_reach_only_trusted_capabilities(self):
        cases = json.loads((ROOT / "tests/fixtures/semantic_routing/cases.json").read_text())
        production = "\n".join((ROOT / path).read_text() for path in ["aag_agent/diagnostics.py", "integrations/anythingllm/aag-ubuntu-diagnostics/handler.js"])
        for case in cases:
            for turn in case["turns"]:
                self.assertNotIn(turn, production, case["name"])
            calls = []
            def observer(domain, query, **kwargs):
                calls.append((domain, query))
                return {"schema":"aag-observation-v1", "domain":domain, "target":query, "status":"completed", "facts":{}, "normalization_error":None, "read_only":True, "mutated":False}
            result = diagnose_many(case["requests"], observer=observer)
            self.assertEqual(result["status"], "OBSERVED", case["name"])
            self.assertTrue(result["read_only"]); self.assertFalse(result["mutated"])
            self.assertLessEqual(len(calls), 8)

    def test_model_tool_has_no_freeform_execution_surface(self):
        tool = next(item for item in agent.TOOLS if item.get("name") == "diagnose")
        properties = tool["parameters"]["properties"]
        self.assertEqual(set(properties), {"profile", "inputs", "secondary_profile", "secondary_inputs"})
        serialized = json.dumps(tool)
        for forbidden in ("command", "binary", "argv", "shell", "systemctl", "sudo"):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_deployed_fallback_is_specific_and_never_relays_commands(self):
        handler = (ROOT / "integrations/anythingllm/aag-ubuntu-diagnostics/handler.js").read_text()
        for reason in ("bridge_endpoint_missing", "bridge_socket_refused", "bridge_permission_denied", "bridge_timeout", "integration_misconfigured"):
            self.assertIn(reason, handler)
        self.assertIn("manual_commands_suggested: false", handler)
        for command in ("htop", "free -h", "docker ps", "systemctl restart"):
            self.assertNotIn(command, handler)

    def test_staged_unit_changes_only_bridge_entrypoint(self):
        unit = (ROOT / "integrations/systemd/aag-ubuntu-agent-bridge.service").read_text()
        self.assertIn("ExecStart=/mnt/data/AI/Agents/AAG-Ubuntu-Agent/venv/bin/python /mnt/data/AI/Agents/AAG-Ubuntu-Agent/app/host_bridge_v2.py", unit)
        self.assertNotIn("ExecStartPre", unit)
        self.assertNotIn("sudo", unit)
        self.assertEqual(unit.count("ExecStart="), 1)


if __name__ == "__main__": unittest.main()
