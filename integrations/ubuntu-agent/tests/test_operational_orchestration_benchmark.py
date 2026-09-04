from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aag_agent.orchestration.engine import GovernedOrchestrator
from aag_agent.orchestration.practical import PracticalWorkflows
from tests.investigation_helpers import InvestigationHarness
from tests.test_operational_orchestration_practical import FakeMaintenance


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/orchestration/practical_workflows.json"


def maturity_fixture():
    names = (
        "release_manifest", "immutable_stage_manifests", "context_database",
        "investigation_database", "remediation_database", "host_mutation_audit",
        "operation_registry", "investigation_registry", "installed_skills", "live_bridge",
    )
    return {
        "status": "PASS_WITH_EXPLICIT_BOUNDARIES", "execution_authority": "NONE",
        "checks": {name: {"status": "PASS"} for name in names},
    }


class OperationalPracticalBenchmarkTests(unittest.TestCase):
    def test_forty_real_workflow_queries_route_safely_with_hebrew_majority(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cases = fixture["cases"]
        self.assertGreaterEqual(len(cases), 40)
        self.assertGreaterEqual(sum(item["language"] == "he" for item in cases), 24)
        results = []
        for case in cases:
            with self.subTest(case=case["id"]), tempfile.TemporaryDirectory() as directory:
                harness = InvestigationHarness(Path(directory))
                practical = PracticalWorkflows(
                    harness.context,
                    maintenance_dispatch=FakeMaintenance(),
                    maturity_runner=maturity_fixture,
                    anythingllm_health=lambda: {"status": "PASS", "online": True, "read_only": True},
                    sleeper=lambda _seconds: None,
                )
                orchestrator = GovernedOrchestrator(
                    context=harness.context, investigations=harness.engine, practical=practical,
                )
                result = orchestrator.handle(case["request"])
                self.assertEqual(result["intent"]["intent"], case["intent"])
                self.assertEqual(result["intent"].get("playbook_id"), case["playbook"])
                self.assertEqual(result["commands"], [])
                self.assertEqual(result["approval_status"], "NOT_REQUESTED")
                self.assertEqual(result["execution_status"], "not_executed")
                self.assertEqual(result["execution_authority"], "NONE")
                self.assertFalse(result["host_resource_mutated"])
                catalog = {item["artifact_id"] for item in result["source_catalog"]}
                self.assertTrue(set(result["evidence_ids"]).issubset(catalog))
                if result["evidence_ids"]:
                    self.assertTrue(harness.context.store.evidence_exists(result["evidence_ids"]))
                results.append({
                    "id": case["id"], "intent": result["intent"]["intent"],
                    "status": result["status"], "evidence_count": len(result["evidence_ids"]),
                    "safe": True,
                })
        self.assertEqual(len(results), len(cases))


if __name__ == "__main__":
    unittest.main()
