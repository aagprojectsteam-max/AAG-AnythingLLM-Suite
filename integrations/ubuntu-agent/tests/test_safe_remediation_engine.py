from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from aag_agent.audit import verify_chain
from aag_agent.remediation.engine import RemediationEngineError, evaluate_backup_policy
from tests.remediation_helpers import CONTEXT_PLAN_ID, Harness, supported_evidence


class RemediationLifecycleTests(unittest.TestCase):
    def harness(self, **kwargs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Harness(Path(temporary.name), **kwargs)

    def test_healthy_state_creates_no_plan_or_incident(self):
        harness = self.harness(
            evidence=supported_evidence(
                health_ready=True,
                health_error=None,
                classification="HEALTHY",
                supported_failure_class=None,
            )
        )
        result = harness.plan()
        self.assertEqual(result["status"], "not_needed")
        self.assertEqual(harness.store.stats()["plans"], 0)
        with harness.context_store.read() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM incidents").fetchone()[0], 0)

    def test_unobservable_missing_and_stale_evidence_fail_closed(self):
        cases = (
            supported_evidence(classification="UNOBSERVABLE", health_error="permission_denied", supported_failure_class=None),
            {key: value for key, value in supported_evidence().items() if key != "main_pid"},
            supported_evidence(observed_at=969.999),
        )
        for evidence in cases:
            with self.subTest(evidence=evidence.get("classification")):
                harness = self.harness(evidence=evidence)
                result = harness.plan()
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(harness.store.stats()["plans"], 0)

    def test_evidence_ttl_boundary_is_inclusive(self):
        harness = self.harness(evidence=supported_evidence(observed_at=970.0))
        self.assertEqual(harness.plan()["status"], "VALIDATED")

    def test_plan_is_immutable_evidence_bound_and_authority_none(self):
        harness = self.harness()
        result = harness.plan()
        stored = harness.store.get_plan(result["plan_id"])
        self.assertEqual(stored["state"], "VALIDATED")
        self.assertEqual(stored["plan_hash"], result["plan_hash"])
        self.assertEqual(stored["plan"]["execution_authority"], "NONE")
        self.assertEqual(stored["plan"]["target_identity"], "aag-ubuntu-agent-bridge.service")
        self.assertEqual(stored["plan"]["context_plan_id"], CONTEXT_PLAN_ID)
        self.assertEqual(len(stored["evidence"]), 2)
        with harness.context_store.read() as connection:
            incident = connection.execute("SELECT * FROM incidents").fetchone()
        self.assertEqual(incident["status"], "OPEN")

    def test_context_plan_missing_or_malformed_is_rejected(self):
        harness = self.harness()
        for plan_id in ("bad", "remediation-plan:" + "0" * 24):
            with self.subTest(plan_id=plan_id):
                with self.assertRaises(Exception):
                    harness.engine.prepare_plan(
                        operation_id="bridge.restart.readiness_failure",
                        operation_version=1,
                        context_plan_id=plan_id,
                    )

    def test_approval_binds_every_security_dimension_and_stores_no_plain_token(self):
        harness = self.harness()
        plan = harness.plan()
        request = harness.engine.request_approval(plan["plan_id"])
        with harness.store.read() as connection:
            approval = dict(connection.execute("SELECT * FROM approvals").fetchone())
        self.assertNotIn(request["approval_token"], json.dumps(approval))
        for field in (
            "plan_hash", "registry_hash", "operation_id", "operation_version",
            "target_identity", "evidence_set_hash", "precondition_spec_hash",
            "backup_policy_hash", "risk_class", "approval_class", "expires_at",
        ):
            self.assertTrue(approval[field])

    def test_wrong_token_and_invalid_decision_do_not_approve(self):
        harness = self.harness()
        plan = harness.plan()
        request = harness.engine.request_approval(plan["plan_id"])
        with self.assertRaisesRegex(RemediationEngineError, "token_mismatch"):
            harness.engine.record_approval(
                request["approval_id"], token="wrong-token-" + "x" * 32,
                operator_id="operator", decision="APPROVE",
            )
        with self.assertRaisesRegex(RemediationEngineError, "invalid_approval_decision"):
            harness.engine.record_approval(
                request["approval_id"], token=request["approval_token"],
                operator_id="operator", decision="MAYBE",
            )
        self.assertEqual(harness.store.get_plan(plan["plan_id"])["state"], "AWAITING_APPROVAL")

    def test_rejection_is_terminal_and_non_mutating(self):
        harness = self.harness()
        plan = harness.plan()
        request = harness.engine.request_approval(plan["plan_id"])
        result = harness.engine.record_approval(
            request["approval_id"], token=request["approval_token"],
            operator_id="operator", decision="REJECT",
        )
        self.assertEqual(result["state"], "REJECTED")
        self.assertEqual(harness.store.get_plan(plan["plan_id"])["state"], "ABORTED_APPROVAL")
        self.assertEqual(harness.executor.calls, 0)

    def test_approval_expiry_before_record_and_before_execute(self):
        first = self.harness()
        plan = first.plan(); request = first.engine.request_approval(plan["plan_id"], ttl_seconds=30)
        first.clock.value += 31
        with self.assertRaisesRegex(RemediationEngineError, "approval_expired"):
            first.engine.record_approval(
                request["approval_id"], token=request["approval_token"],
                operator_id="operator", decision="APPROVE",
            )
        self.assertEqual(first.executor.calls, 0)

        second = self.harness()
        plan = second.plan(); request, _ = second.approve(plan["plan_id"])
        second.clock.value += 601
        with self.assertRaisesRegex(RemediationEngineError, "approval_expired"):
            second.engine.execute(
                plan["plan_id"], request["approval_id"],
                token=request["approval_token"], operator_id="stage17-operator",
            )
        self.assertEqual(second.executor.calls, 0)

    def test_wrong_operator_and_replay_are_rejected(self):
        harness = self.harness()
        plan = harness.plan(); request, _ = harness.approve(plan["plan_id"])
        with self.assertRaisesRegex(RemediationEngineError, "operator_mismatch"):
            harness.engine.execute(
                plan["plan_id"], request["approval_id"],
                token=request["approval_token"], operator_id="other-operator",
            )
        result = harness.engine.execute(
            plan["plan_id"], request["approval_id"],
            token=request["approval_token"], operator_id="stage17-operator",
        )
        self.assertEqual(result["status"], "SUCCEEDED_VERIFIED")
        with self.assertRaisesRegex(RemediationEngineError, "replay"):
            harness.engine.execute(
                plan["plan_id"], request["approval_id"],
                token=request["approval_token"], operator_id="stage17-operator",
            )
        self.assertEqual(harness.executor.calls, 1)

    def test_changed_live_state_invalidates_approval_before_executor(self):
        harness = self.harness()
        plan = harness.plan(); request, _ = harness.approve(plan["plan_id"])
        harness.observer.evidence = supported_evidence(main_pid="99999")
        result = harness.engine.execute(
            plan["plan_id"], request["approval_id"],
            token=request["approval_token"], operator_id="stage17-operator",
        )
        self.assertEqual(result["status"], "ABORTED_STALE_STATE")
        self.assertEqual(harness.executor.calls, 0)
        self.assertFalse(result["mutated"])

    def test_changed_plan_and_approval_binding_fail_before_executor(self):
        harness = self.harness()
        plan = harness.plan(); request, _ = harness.approve(plan["plan_id"])
        with harness.store.transaction() as connection:
            row = connection.execute("SELECT plan_json FROM plans WHERE plan_id=?", (plan["plan_id"],)).fetchone()
            value = json.loads(row["plan_json"]); value["risk_class"] = "R0"
            connection.execute("UPDATE plans SET plan_json=? WHERE plan_id=?", (json.dumps(value), plan["plan_id"]))
        with self.assertRaisesRegex(RemediationEngineError, "stored_plan_hash_mismatch"):
            harness.engine.execute(
                plan["plan_id"], request["approval_id"],
                token=request["approval_token"], operator_id="stage17-operator",
            )
        self.assertEqual(harness.executor.calls, 0)

        second = self.harness()
        plan = second.plan(); request, _ = second.approve(plan["plan_id"])
        with second.store.transaction() as connection:
            connection.execute("UPDATE approvals SET evidence_set_hash=? WHERE approval_id=?", ("0" * 64, request["approval_id"]))
        with self.assertRaisesRegex(RemediationEngineError, "approval_binding_invalid"):
            second.engine.execute(
                plan["plan_id"], request["approval_id"],
                token=request["approval_token"], operator_id="stage17-operator",
            )
        self.assertEqual(second.executor.calls, 0)

    def test_pre_execution_audit_failure_blocks_executor_and_consumes_approval(self):
        def fail(*args):
            raise OSError("audit unavailable")
        harness = self.harness(host_audit=fail)
        plan = harness.plan(); request, _ = harness.approve(plan["plan_id"])
        result = harness.engine.execute(
            plan["plan_id"], request["approval_id"],
            token=request["approval_token"], operator_id="stage17-operator",
        )
        self.assertEqual(result["status"], "ABORTED_AUDIT")
        self.assertEqual(harness.executor.calls, 0)
        self.assertEqual(harness.engine._approval_row(request["approval_id"])["state"], "CONSUMED")

    def test_verified_success_updates_incident_task_and_candidate_without_promotion(self):
        harness = self.harness(with_task=True)
        plan, request, result = harness.run()
        self.assertEqual(result["status"], "SUCCEEDED_VERIFIED")
        self.assertTrue(result["post_verified"])
        self.assertEqual(harness.executor.calls, 1)
        self.assertEqual(verify_chain(harness.host_audit_path)["record_count"], 2)
        with harness.context_store.read() as connection:
            incident = connection.execute("SELECT status FROM incidents").fetchone()
            action = connection.execute("SELECT lifecycle_state,result FROM historical_actions").fetchone()
            candidate = connection.execute("SELECT status FROM memory_candidates").fetchone()
            promotions = connection.execute("SELECT count(*) FROM canonical_promotion_candidates").fetchone()[0]
        self.assertEqual(incident["status"], "RESOLVED")
        self.assertEqual(action["lifecycle_state"], "VERIFIED_SUCCESS")
        self.assertEqual(candidate["status"], "PENDING")
        self.assertEqual(promotions, 0)
        task = harness.context_service.tasks.show(harness.task_id)
        self.assertIn("safe-remediation-v1:bridge.restart.readiness_failure", task["tools_used"])

    def test_execution_failure_is_historical_and_not_a_memory_candidate(self):
        harness = self.harness(executor_result={
            "status": "FAILED_EXECUTION", "error": "executor_nonzero", "returncode": 1,
            "stdout": "", "stderr": "failed", "executed": True, "mutated": True,
        })
        _, _, result = harness.run()
        self.assertEqual(result["status"], "FAILED_EXECUTION")
        with harness.context_store.read() as connection:
            action = connection.execute("SELECT lifecycle_state,result FROM historical_actions").fetchone()
            candidates = connection.execute("SELECT count(*) FROM memory_candidates").fetchone()[0]
            current = connection.execute("SELECT count(*) FROM current_canonical_facts WHERE fact_key='bridge.last_verified_remediation'").fetchone()[0]
        self.assertEqual(action["lifecycle_state"], "FAILED_ATTEMPT")
        self.assertEqual(candidates, 0)
        self.assertEqual(current, 0)

    def test_post_verification_failed_and_indeterminate_are_distinct(self):
        failed = self.harness(verification={"status": "FAILED", "reason": "health_not_ready"})
        self.assertEqual(failed.run()[2]["status"], "FAILED_VERIFICATION")
        indeterminate = self.harness(verification={"status": "INDETERMINATE", "reason": "UNOBSERVABLE"})
        self.assertEqual(indeterminate.run()[2]["status"], "INDETERMINATE")

    def test_post_audit_failure_preserves_real_outcome_but_skips_candidate(self):
        calls = []
        def audit(contract_id, event, details):
            calls.append(event)
            if len(calls) == 2:
                raise OSError("checkpoint failed")
            return {"record_hash": "sha256:" + "f" * 64}
        harness = self.harness(host_audit=audit)
        _, _, result = harness.run()
        self.assertEqual(result["status"], "SUCCEEDED_VERIFIED")
        self.assertEqual(result["audit"]["status"], "POST_WRITE_FAILED")
        self.assertIsNone(result["context_reconciliation"])
        with harness.context_store.read() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM memory_candidates").fetchone()[0], 0)

    def test_backup_policy_is_typed_and_unknown_backup_blocks(self):
        harness = self.harness()
        plan = harness.plan()
        status = harness.engine.backup_status(plan["plan_id"])
        self.assertEqual(status["status"], "VERIFIED")
        self.assertEqual(status["policy"]["class"], "NO_BACKUP_REQUIRED_WITH_JUSTIFICATION")
        self.assertFalse(status["backup_created"])
        self.assertEqual(
            evaluate_backup_policy({"class": "BACKUP_REQUIRED", "justification": "required", "restore_test_required": True})["error"],
            "typed_backup_primitive_unavailable",
        )
        self.assertEqual(evaluate_backup_policy({})["error"], "backup_policy_invalid")

    def test_rollback_is_separate_and_unavailable_for_restart(self):
        harness = self.harness(executor_result={
            "status": "FAILED_EXECUTION", "error": "executor_nonzero", "returncode": 1,
            "stdout": "", "stderr": "failed", "executed": True, "mutated": True,
        })
        _, _, result = harness.run()
        proposal = harness.engine.rollback_proposal(result["attempt_id"])
        self.assertEqual(proposal["status"], "UNAVAILABLE")
        self.assertEqual(proposal["approval_class"], "SEPARATE_OPERATION_AUTHORIZATION")
        self.assertFalse(proposal["executed"])
        self.assertEqual(harness.executor.calls, 1)

    def test_event_chain_detects_tampering(self):
        harness = self.harness()
        plan = harness.plan()
        self.assertEqual(harness.store.verify_event_chains()["status"], "PASS")
        with harness.store.transaction() as connection:
            connection.execute(
                "UPDATE plan_events SET details_json='{}' WHERE plan_id=? AND sequence=1",
                (plan["plan_id"],),
            )
        with self.assertRaisesRegex(Exception, "event_chain_hash_mismatch"):
            harness.store.verify_event_chains()

    def test_event_chain_checkpoint_detects_truncated_tail(self):
        harness = self.harness()
        plan = harness.plan()
        with harness.store.transaction() as connection:
            connection.execute(
                "DELETE FROM plan_events WHERE plan_id=? AND sequence=(SELECT max(sequence) FROM plan_events WHERE plan_id=?)",
                (plan["plan_id"], plan["plan_id"]),
            )
        with self.assertRaisesRegex(Exception, "event_chain_checkpoint_mismatch"):
            harness.store.verify_event_chains()

    def test_complete_offline_replay_uses_only_injected_executor(self):
        harness = self.harness()
        _, _, result = harness.run()
        self.assertEqual(result["status"], "SUCCEEDED_VERIFIED")
        self.assertEqual(harness.executor.calls, 1)
        self.assertEqual(harness.observer.observe_calls, 2)
        self.assertEqual(harness.observer.verify_calls, 1)
        self.assertEqual(harness.store.integrity()["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
