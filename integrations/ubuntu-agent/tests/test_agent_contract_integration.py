import unittest
from unittest.mock import patch

import app.agent as agent


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        agent.PENDING_MUTATION = None
        agent.CONSUMED_APPROVAL_TOKENS.clear()

    def test_model_facing_contract_has_no_action_or_target_parameter(self):
        tool = next(item for item in agent.TOOLS if item.get("name") == "prepare_controlled_mutation")
        props = tool["parameters"]["properties"]
        self.assertEqual(set(props), {"contract_id"})

    def test_diagnose_tool_exposes_profiles_not_commands(self):
        tool = next(item for item in agent.TOOLS if item.get("name") == "diagnose")
        props = tool["parameters"]["properties"]
        self.assertEqual(set(props), {"profile", "inputs", "secondary_profile", "secondary_inputs"})
        self.assertNotIn("command", props["inputs"]["properties"])
        self.assertNotIn("binary", props["inputs"]["properties"])
        self.assertNotIn("target", props["inputs"]["properties"])

    def test_unknown_contract_blocked(self):
        result = agent.prepare_contract_remediation("evil.restart")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["execution_authority"], "NONE")

    def test_healthy_bridge_never_creates_pending_authority(self):
        with patch.object(agent, "verify_bridge_readiness", return_value={"ready": True}):
            result = agent.prepare_contract_remediation("bridge.readiness_failure")
        self.assertEqual(result["status"], "not_needed")
        self.assertIsNone(agent.PENDING_MUTATION)

    def test_wrong_token_and_replay_fail(self):
        agent.PENDING_MUTATION = {"token": "correct"}
        self.assertEqual(agent.handle_local_mutation_command("/approve wrong")["error"], "approval_token_mismatch")
        agent.PENDING_MUTATION = None; agent.CONSUMED_APPROVAL_TOKENS.add("used")
        self.assertEqual(agent.handle_local_mutation_command("/approve used")["error"], "approval_token_already_consumed")

    def test_executor_rejects_arbitrary_target_before_subprocess(self):
        result = agent.execute_controlled_mutation("restart_user_service", "evil.service", {}, {}, {})
        self.assertEqual(result["error"], "executor_target_not_allowlisted")
        self.assertFalse(result["mutated"])

    def _pending(self, token="audit-token"):
        snapshot = {
            "status": "completed", "target": "aag-ubuntu-agent-bridge.service",
            "load_state": "loaded", "active_state": "active", "sub_state": "running",
            "main_pid": "51001", "exec_main_status": "0",
        }
        plan = agent.build_bridge_restart_plan()
        agent.PENDING_MUTATION = {
            "token": token, "action": "restart_user_service",
            "target": "aag-ubuntu-agent-bridge.service", "plan": plan,
            "approved_snapshot": snapshot,
            "plan_fingerprint": agent.remediation_plan_fingerprint(plan),
            "state_fingerprint": agent.approval_state_fingerprint(snapshot),
        }
        return token, snapshot

    def test_indeterminate_health_never_creates_pending_authority(self):
        snapshot = {"status": "completed", "target": "aag-ubuntu-agent-bridge.service", "load_state": "loaded", "active_state": "active", "sub_state": "running"}
        with patch.object(agent, "verify_bridge_readiness", return_value={"ready": False, "error": "permission_denied"}), patch.object(agent, "build_live_service_snapshot", return_value=snapshot):
            result = agent.prepare_contract_remediation("bridge.readiness_failure")
        self.assertEqual(result["error"], "unsupported_bridge_failure_evidence")
        self.assertEqual(result["detector_evidence"]["classification"], "UNOBSERVABLE")
        self.assertIsNone(agent.PENDING_MUTATION)

    def test_supported_failure_creates_pending_without_authority(self):
        snapshot = {"status": "completed", "target": "aag-ubuntu-agent-bridge.service", "load_state": "loaded", "active_state": "active", "sub_state": "running", "main_pid": "51001"}
        with patch.object(agent, "verify_bridge_readiness", return_value={"ready": False, "error": "readiness_timeout"}), patch.object(agent, "build_live_service_snapshot", return_value=snapshot):
            result = agent.prepare_contract_remediation("bridge.readiness_failure")
        self.assertEqual(result["status"], "awaiting_explicit_user_approval")
        self.assertEqual(result["execution_authority"], "NONE")
        self.assertFalse(result["executed"]); self.assertFalse(result["mutated"])

    def test_state_and_plan_changes_invalidate_pending_request(self):
        token, snapshot = self._pending("audit-token-plan-change")
        changed = dict(snapshot); changed["main_pid"] = "51002"
        with patch.object(agent, "verify_bridge_readiness", return_value={"ready": False, "error": "readiness_timeout"}), patch.object(agent, "build_live_service_snapshot", return_value=changed):
            stale = agent.handle_local_mutation_command(f"/approve {token}")
        self.assertEqual(stale["error"], "approved_state_became_stale")
        self.assertFalse(stale["executed"])

        token, snapshot = self._pending()
        agent.PENDING_MUTATION["plan"]["action_reason"] += " changed"
        with patch.object(agent, "verify_bridge_readiness", return_value={"ready": False, "error": "readiness_timeout"}), patch.object(agent, "build_live_service_snapshot", return_value=snapshot):
            changed_plan = agent.handle_local_mutation_command(f"/approve {token}")
        self.assertEqual(changed_plan["error"], "approved_plan_changed")
        self.assertFalse(changed_plan["executed"])

    def test_pre_execution_audit_failure_blocks_executor(self):
        token, snapshot = self._pending()
        with patch.object(agent, "verify_bridge_readiness", return_value={"ready": False, "error": "readiness_timeout"}), patch.object(agent, "build_live_service_snapshot", return_value=snapshot), patch.object(agent, "persist_mutation_audit_event", side_effect=OSError("disk unavailable")), patch.object(agent, "execute_controlled_mutation") as executor:
            result = agent.handle_local_mutation_command(f"/approve {token}")
        self.assertEqual(result["error"], "pre_execution_audit_persistence_failed")
        self.assertFalse(result["mutated"])
        executor.assert_not_called()

    def test_post_execution_audit_failure_preserves_real_result(self):
        token, snapshot = self._pending()
        executor_result = {"status": "completed", "executed": True, "mutated": True, "post_verified": True}
        first_audit = {"record_hash": "sha256:" + "1" * 64}
        with patch.object(agent, "verify_bridge_readiness", return_value={"ready": False, "error": "readiness_timeout"}), patch.object(agent, "build_live_service_snapshot", return_value=snapshot), patch.object(agent, "persist_mutation_audit_event", side_effect=[first_audit, OSError("disk full")]), patch.object(agent, "execute_controlled_mutation", return_value=executor_result):
            result = agent.handle_local_mutation_command(f"/approve {token}")
        self.assertIs(result["result"], executor_result)
        self.assertTrue(result["executed"]); self.assertTrue(result["mutated"])
        self.assertFalse(result["audit"]["persisted"])
        self.assertTrue(result["audit"]["started_persisted"])
        self.assertFalse(result["audit"]["finished_persisted"])
        self.assertEqual(result["audit"]["error"], "post_execution_audit_persistence_failed")


if __name__ == "__main__": unittest.main()
