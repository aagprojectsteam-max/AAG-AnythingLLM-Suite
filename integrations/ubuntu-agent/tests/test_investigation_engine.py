from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aag_agent.investigation.engine import InvestigationError
from tests.investigation_helpers import InvestigationHarness, performance_session, storage_session


class InvestigationEngineTests(unittest.TestCase):
    def harness(self, **kwargs):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        return InvestigationHarness(Path(directory.name), **kwargs)

    def test_performance_negative_observations_falsify_hypotheses(self):
        result = self.harness().run()
        states = {item["hypothesis_id"]: item["state"] for item in result["hypotheses"]}
        self.assertEqual(set(states.values()), {"FALSIFIED"})
        self.assertEqual(result["conclusion"], "REGISTERED_HYPOTHESES_FALSIFIED")

    def test_supported_contributors_are_ranked_but_not_called_verified_root_cause(self):
        result = self.harness(payload=performance_session(disk="95%", available=2, cpu=120, failed=["x.service"])).run()
        self.assertTrue(all(item["state"] == "SUPPORTED" for item in result["hypotheses"]))
        self.assertEqual(result["conclusion"], "SUPPORTED_HYPOTHESES_REQUIRE_GOVERNED_REVIEW")
        self.assertFalse(result["remediation_handoff"]["eligible"])

    def test_partial_missing_fact_is_unknown(self):
        payload = performance_session(); del payload["bundles"][0]["facts"]["memory"]
        result = self.harness(payload=payload).run()
        self.assertIn("system.low_available_memory", result["unknowns"])
        self.assertEqual(result["state"], "INDETERMINATE")

    def test_bridge_healthy_falsifies_restart_hypothesis(self):
        result = self.harness(bridge="HEALTHY").run("bridge.readiness_investigation", "Is Bridge unhealthy?")
        self.assertEqual(result["hypotheses"][0]["state"], "FALSIFIED")
        self.assertFalse(result["remediation_handoff"]["eligible"])

    def test_bridge_supported_failure_only_proposes_handoff(self):
        result = self.harness(bridge="SUPPORTED_FAILURE").run("bridge.readiness_investigation", "Bridge health failed")
        self.assertEqual(result["hypotheses"][0]["state"], "SUPPORTED")
        self.assertTrue(result["remediation_handoff"]["eligible"])
        self.assertEqual(result["remediation_handoff"]["status"], "PROPOSAL_ELIGIBLE_NOT_AUTHORIZED")
        self.assertEqual(result["execution_authority"], "NONE")

    def test_unobservable_bridge_is_unknown_not_failure(self):
        result = self.harness(bridge="UNOBSERVABLE").run("bridge.readiness_investigation", "Bridge state")
        self.assertEqual(result["hypotheses"][0]["state"], "UNKNOWN")
        self.assertFalse(result["remediation_handoff"]["eligible"])

    def test_fixed_root_storage_never_accepts_caller_path(self):
        harness = self.harness(payload=storage_session(disk="91%"))
        result = harness.run("storage.root_pressure_investigation", "Check /etc please")
        self.assertEqual(harness.diagnostics.calls[0], [{"profile": "storage_mount", "inputs": {"path": "/"}}])
        self.assertEqual(result["target_identity"], "/")

    def test_live_artifact_ids_exist_in_context_store(self):
        harness = self.harness(); result = harness.run()
        ids = [item["artifact_id"] for item in result["evidence"]]
        self.assertTrue(harness.context.store.evidence_exists(ids))

    def test_task_continuity_receives_evidence_and_hypotheses(self):
        harness = self.harness(with_task=True); result = harness.run()
        task = harness.context.tasks.show(harness.task_id)
        self.assertEqual(task["evidence_ids"], [result["evidence"][0]["artifact_id"]])
        self.assertTrue(task["hypotheses"])
        self.assertTrue(task["observations"])

    def test_closed_task_rejected_before_collection(self):
        harness = self.harness(with_task=True)
        harness.context.tasks.close(harness.task_id, {"done": True})
        with self.assertRaises(InvestigationError):
            harness.engine.create("system.performance_investigation", request_summary="test", task_id=harness.task_id)
        self.assertFalse(harness.diagnostics.calls)

    def test_run_is_not_replayable(self):
        harness = self.harness()
        created = harness.engine.create("system.performance_investigation", request_summary="test")
        harness.engine.run(created["investigation_id"])
        with self.assertRaisesRegex(InvestigationError, "not_open"):
            harness.engine.run(created["investigation_id"])
        self.assertEqual(len(harness.diagnostics.calls), 1)

    def test_close_preserves_history_and_chain(self):
        harness = self.harness(); result = harness.run()
        closed = harness.engine.close(result["investigation_id"])
        self.assertEqual(closed["state"], "CLOSED")
        self.assertEqual(harness.store.integrity()["status"], "PASS")

    def test_every_result_preserves_read_only_authority(self):
        result = self.harness(payload=performance_session(disk="99%", cpu=200)).run()
        self.assertTrue(result["read_only"])
        self.assertFalse(result["mutated"])
        self.assertEqual(result["execution_authority"], "NONE")
        self.assertTrue(result["security_notice"]["no_commands"])


if __name__ == "__main__":
    unittest.main()
