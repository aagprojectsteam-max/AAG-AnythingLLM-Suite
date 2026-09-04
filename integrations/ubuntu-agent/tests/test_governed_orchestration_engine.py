from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aag_agent.orchestration.engine import GovernedOrchestrator
from tests.investigation_helpers import InvestigationHarness, performance_session, storage_session


class GovernedOrchestrationEngineTests(unittest.TestCase):
    def orchestrator(self, **kwargs):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        harness = InvestigationHarness(Path(directory.name), **kwargs)
        return GovernedOrchestrator(context=harness.context, investigations=harness.engine), harness

    def test_preview_has_no_state_write_or_execution(self):
        orchestrator, harness = self.orchestrator()
        before = harness.store.stats()
        result = orchestrator.preview("למה המחשב איטי?")
        self.assertEqual(result["status"], "ROUTED")
        self.assertFalse(result["project_state_updated"])
        self.assertEqual(harness.store.stats(), before)
        self.assertEqual(result["execution_authority"], "NONE")

    def test_performance_request_creates_task_and_investigation(self):
        orchestrator, _ = self.orchestrator()
        result = orchestrator.handle("Why is the computer slow now?")
        self.assertEqual(result["status"], "INVESTIGATION_COMPLETE")
        self.assertTrue(result["task"]["task_id"].startswith("task:"))
        self.assertEqual(result["investigation"]["playbook_id"], "system.performance_investigation")

    def test_storage_request_uses_only_fixed_root(self):
        orchestrator, harness = self.orchestrator(payload=storage_session())
        result = orchestrator.handle("Check disk space under /etc")
        self.assertEqual(result["investigation"]["target_identity"], "/")
        self.assertEqual(harness.diagnostics.calls[0][0]["inputs"], {"path": "/"})

    def test_healthy_bridge_request_does_not_propose_restart(self):
        orchestrator, _ = self.orchestrator(bridge="HEALTHY")
        result = orchestrator.handle("Investigate the Bridge")
        self.assertFalse(result["investigation"]["remediation_handoff"]["eligible"])
        self.assertEqual(result["approval_status"], "NOT_REQUESTED")

    def test_generic_remediation_without_supported_hypothesis_fails_grounding(self):
        orchestrator, _ = self.orchestrator()
        result = orchestrator.handle("Prepare an evidence-based remediation plan but change nothing")
        self.assertEqual(result["status"], "CLARIFICATION_REQUIRED")
        self.assertIsNone(result["remediation_proposal"])
        self.assertEqual(result["commands"], [])

    def test_supported_performance_hypothesis_yields_nonexecuted_proposal(self):
        orchestrator, _ = self.orchestrator(payload=performance_session(disk="96%"))
        result = orchestrator.handle("Prepare a repair plan for the slow computer")
        plan = result["remediation_proposal"]
        self.assertEqual(result["status"], "GROUNDED_PROPOSAL_NOT_EXECUTED")
        self.assertEqual(plan["execution_authority"], "NONE")
        self.assertEqual(plan["execution_status"], "not_executed")
        self.assertTrue(plan["zero_mutations"])
        self.assertIn("system.root_filesystem_pressure", plan["supported_hypotheses"])

    def test_bridge_failure_only_reports_governed_eligibility(self):
        orchestrator, _ = self.orchestrator(bridge="SUPPORTED_FAILURE")
        result = orchestrator.handle("Fix the Bridge readiness issue")
        eligibility = result["remediation_proposal"]["governed_operation_eligibility"]
        self.assertTrue(eligibility["eligible"])
        self.assertEqual(eligibility["execution_authority"], "NONE")
        self.assertEqual(result["approval_status"], "NOT_REQUESTED")

    def test_ambiguous_domain_runs_nothing(self):
        orchestrator, harness = self.orchestrator()
        result = orchestrator.handle("Fix the slow computer and full disk")
        self.assertEqual(result["status"], "CLARIFICATION_REQUIRED")
        self.assertFalse(harness.diagnostics.calls)
        self.assertFalse(result["project_state_updated"])

    def test_task_continuation_requires_id_and_resumes_exact_task(self):
        orchestrator, _ = self.orchestrator()
        first = orchestrator.handle("Why is the computer slow now?")
        result = orchestrator.handle("Continue the task", task_id=first["task"]["task_id"])
        self.assertEqual(result["status"], "TASK_RESUMED")
        self.assertEqual(result["task"]["task_id"], first["task"]["task_id"])
        self.assertFalse(result["continuation"]["user_entry_required"])

    def test_context_and_history_do_not_create_investigation(self):
        orchestrator, harness = self.orchestrator()
        for request in ("What is the current AAG release?", "What failed previously?"):
            with self.subTest(request=request):
                result = orchestrator.handle(request)
                self.assertIn(result["status"], {"CONTEXT_ASSEMBLED", "CURRENT_STATE_OBSERVED"})
                self.assertIsNone(result["investigation"])
        self.assertEqual(harness.store.stats()["investigations"], 0)

    def test_all_envelopes_preserve_zero_host_mutations(self):
        orchestrator, _ = self.orchestrator(payload=performance_session(disk="99%"))
        result = orchestrator.handle("Fix the slow computer")
        self.assertTrue(result["zero_host_mutations"])
        self.assertFalse(result["host_resource_mutated"])
        self.assertEqual(result["commands"], [])
        self.assertEqual(result["execution_status"], "not_executed")


if __name__ == "__main__":
    unittest.main()
