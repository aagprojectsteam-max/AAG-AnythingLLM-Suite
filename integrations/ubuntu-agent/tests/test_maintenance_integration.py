from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app.agent as agent
from aag_agent.endpoints import BRIDGE_MAINTENANCE_PATH, public_contract
from aag_agent.maintenance.history import HistoryStore
from aag_agent.maintenance.orchestrator import MAINTENANCE_TOOLS, MaintenanceContext, dispatch
from tests.maintenance_helpers import make_policy

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("maintenance_bridge_test", ROOT / "app/host_bridge_v2.py")
bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)


class MaintenanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        registry = {"schema":"aag-component-registry-v1","components":[{"identity":"fixture-component","name":"Fixture","dependencies":[],"dependents":[],"mutation_risk":"low","confidence":"high"}]}
        self.config, self.policy = make_policy(self.root, registry=registry)
        self.context = MaintenanceContext(self.config, self.policy, HistoryStore(self.config.history_path))

    def tearDown(self): self.temporary.cleanup()

    def test_agent_exposes_strict_hebrew_maintenance_intents_and_bounded_defaults(self):
        names = {item.get("name") for item in agent.TOOLS}
        for name in ("system_health", "performance_snapshot", "storage_top", "storage_duplicate_verify", "maintenance_plan"):
            self.assertIn(name, names)
        storage_top = next(item for item in agent.TOOLS if item.get("name") == "storage_top")
        self.assertEqual(set(storage_top["parameters"]["properties"]), {"path", "profile"})
        self.assertFalse(storage_top["parameters"]["additionalProperties"])
        for phrase in ("מה מצב המחשב?", "למה המחשב איטי?", "מה תופס לי מקום?", "תכין לי תוכנית ניקוי אבל אל תמחק כלום"):
            self.assertIn(phrase, agent.SYSTEM_PROMPT)

    def test_invalid_path_and_deep_defaults_fail_closed(self):
        invalid = dispatch("storage.top", {"path":"/etc", "profile":"standard"}, context=self.context)
        self.assertEqual(invalid["completeness"]["status"], "failed")
        excessive = dispatch(
            "storage.top",
            {"path": str(self.root), "profile": "quick", "limits": {"max_entries": 2001}},
            context=self.context,
        )
        self.assertEqual(excessive["completeness"]["status"], "failed")
        self.assertEqual(excessive["errors"][0]["code"], "max_entries_exceeds_profile_budget")
        deep = dispatch("storage.duplicate_candidates", {"path":str(self.root), "profile":"standard"}, context=self.context)
        self.assertEqual(deep["errors"][0]["code"], "deep_profile_required")
        arbitrary = dispatch("run.command", {"command":"id"}, context=self.context)
        self.assertEqual(arbitrary["completeness"]["status"], "failed")

    def test_snapshot_growth_and_plan_are_nonmutating(self):
        target = self.root / "output"; target.write_bytes(b"x" * 100)
        first = dispatch("storage.snapshot", {"path":str(self.root), "profile":"standard"}, context=self.context)
        target.write_bytes(b"x" * 200)
        second = dispatch("storage.snapshot", {"path":str(self.root), "profile":"standard"}, context=self.context)
        growth = dispatch("storage.growth", {"path":str(self.root)}, context=self.context)
        plan = dispatch("maintenance.plan", {"path":str(self.root), "profile":"standard"}, context=self.context)
        self.assertTrue(first["result"]["history_written"]); self.assertTrue(second["result"]["history_written"])
        self.assertTrue(growth["result"]["comparable"])
        self.assertTrue(plan["result"]["zero_mutations"])
        self.assertEqual(plan["result"]["execution_authority"], "NONE")
        self.assertTrue(all(item["execution_status"] == "not_executed" for item in plan["result"]["items"]))
        self.assertTrue(plan["read_only"]); self.assertFalse(plan["mutated"])

    def test_hebrew_rendering_is_concise_and_honest_on_partial(self):
        result = dispatch("storage.top", {"path":str(self.root), "profile":"quick"}, context=self.context)
        self.assertEqual(result["hebrew"]["language"], "he")
        self.assertIn("לא בוצעו", result["hebrew"]["mutations"])
        if result["completeness"]["status"] != "complete":
            self.assertIn("חלקי", result["hebrew"]["summary"])

    def _handler(self, payload, path=BRIDGE_MAINTENANCE_PATH):
        raw = json.dumps(payload).encode("utf-8")
        handler = object.__new__(bridge.Handler)
        handler.path = path
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.send_json = Mock()
        return handler

    def test_bridge_maintenance_route_calls_only_typed_dispatch(self):
        expected = {"schema":"aag-maintenance-scan-envelope-v1","schema_version":"1.0","completeness":{"status":"complete"},"read_only":True,"mutated":False}
        handler = self._handler({"tool":"storage.overview","arguments":{}})
        with patch.object(bridge, "dispatch_maintenance", return_value=expected) as dispatcher:
            bridge.Handler.do_POST(handler)
        dispatcher.assert_called_once_with("storage.overview", {})
        handler.send_json.assert_called_once_with(200, expected)
        rejected = self._handler({"tool":"run.shell","arguments":{"command":"id"}})
        with patch.object(bridge, "dispatch_maintenance") as dispatcher:
            bridge.Handler.do_POST(rejected)
        dispatcher.assert_not_called()
        rejected.send_json.assert_called_once_with(400, {"error":"invalid_maintenance_request_schema"})

    def test_contracts_bridge_and_legacy_invariants(self):
        contract = public_contract()
        self.assertEqual(contract["maintenance_path"], "/maintenance")
        self.assertEqual(contract["diagnose_path"], "/diagnose")
        self.assertEqual(contract["api_version"], 2)
        self.assertEqual(hashlib.sha256((ROOT / "app/host_bridge.py").read_bytes()).hexdigest(), "a16f206849d8848314ed0f1e9013a207114a98a61095910d61d4a0fd86858fff")
        self.assertIn("diagnose", {item.get("name") for item in agent.TOOLS})
        self.assertEqual(agent.BRIDGE_CONTRACT_ID, "bridge.readiness_failure")
        source = "\n".join((ROOT / path).read_text() for path in ["aag_agent/maintenance/command.py", "app/host_bridge_v2.py"])
        self.assertNotIn("shell=True", source)
        self.assertNotIn("sudo", source)

    def test_versioned_contracts_and_staged_plugin(self):
        envelope_schema = json.loads((ROOT / "contracts/maintenance/scan-envelope.v1.schema.json").read_text())
        plan_schema = json.loads((ROOT / "contracts/maintenance/maintenance-plan.v1.schema.json").read_text())
        catalog = json.loads((ROOT / "contracts/maintenance/tool-catalog.v1.json").read_text())
        plugin = json.loads((ROOT / "integrations/anythingllm/aag-maintenance-intelligence/plugin.json").read_text())
        handler = (ROOT / "integrations/anythingllm/aag-maintenance-intelligence/handler.js").read_text()
        self.assertEqual(envelope_schema["properties"]["schema_version"]["const"], "1.0")
        self.assertEqual(plan_schema["properties"]["execution_authority"]["const"], "NONE")
        self.assertEqual({item["tool"] for item in catalog["tools"]}, MAINTENANCE_TOOLS)
        self.assertEqual(set(plugin["entrypoint"]["params"]), {"operation","path","profile","item_id"})
        self.assertIn('contract.maintenance_path', handler)
        for forbidden in ("child_process", "exec(", "spawn(", "sudo", "prune"):
            self.assertNotIn(forbidden, handler)


if __name__ == "__main__": unittest.main()
