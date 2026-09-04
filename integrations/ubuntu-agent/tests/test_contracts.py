import copy
import json
import time
import unittest
import tempfile
from pathlib import Path

from aag_agent.contracts import ContractError, ContractRegistry, validate_contract
from aag_agent.onboarding import review_contract
from aag_agent.policy import evaluate

ROOT = Path(__file__).parents[1]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads((ROOT / "contracts/bridge.readiness_failure.v1.json").read_text())
        self.registry = ContractRegistry(ROOT / "contracts")

    def evidence(self, **changes):
        result = {
            "schema": "aag-bridge-detector-evidence-v1", "observed_at": 1000.0,
            "target": "aag-ubuntu-agent-bridge.service", "load_state": "loaded",
            "active_state": "active", "sub_state": "running", "health_ready": False,
            "health_error": "readiness_timeout", "classification": "SUPPORTED_FAILURE",
            "supported_failure_class": "systemd_active_running_but_health_endpoint_unready",
            "provenance": {"read_only": True},
        }
        result.update(changes)
        return result

    def test_valid_accepted_bridge_contract(self):
        contract = self.registry.get("bridge.readiness_failure", execution=True)
        self.assertEqual(contract.data["executor"]["target"], "aag-ubuntu-agent-bridge.service")

    def test_unknown_contract_fails_closed(self):
        with self.assertRaisesRegex(ContractError, "unknown_contract"):
            self.registry.get("service.restart_anything", execution=True)

    def test_malformed_contract_rejected(self):
        raw = copy.deepcopy(self.raw); del raw["post_verifier"]
        with self.assertRaisesRegex(ContractError, "missing_fields"):
            validate_contract(raw)

    def test_target_action_and_command_injection_rejected(self):
        for mutation, error in [
            (("executor", "target", "evil.service"), "unsupported_target"),
            (("executor", "primitive", "run_shell"), "unknown_executor_primitive"),
        ]:
            raw = copy.deepcopy(self.raw); raw[mutation[0]][mutation[1]] = mutation[2]
            with self.assertRaisesRegex(ContractError, error): validate_contract(raw)
        raw = copy.deepcopy(self.raw); raw["executor"]["command"] = "systemctl restart evil"
        with self.assertRaisesRegex(ContractError, "executor_extra_fields"): validate_contract(raw)

    def test_top_level_and_nested_schema_are_strict(self):
        raw = copy.deepcopy(self.raw); raw["command"] = "id"
        with self.assertRaisesRegex(ContractError, "unexpected_fields"): validate_contract(raw)
        raw = copy.deepcopy(self.raw); raw["evidence"]["unknown"] = True
        with self.assertRaisesRegex(ContractError, "invalid_evidence_policy"): validate_contract(raw)

    def test_version_risk_and_metadata_types_are_validated(self):
        for field, value, error in [
            ("version", 0, "invalid_version"), ("version", True, "invalid_version"),
            ("risk", "tiny", "invalid_risk"), ("preconditions", [], "invalid_preconditions"),
            ("audit_policy", "NONE", "invalid_audit_policy"),
        ]:
            raw = copy.deepcopy(self.raw); raw[field] = value
            with self.assertRaisesRegex(ContractError, error): validate_contract(raw)

    def test_nonaccepted_lifecycle_states_cannot_execute(self):
        for status in ("DRAFT", "TESTED", "DEPRECATED"):
            with tempfile.TemporaryDirectory() as directory:
                raw = copy.deepcopy(self.raw); raw["status"] = status
                Path(directory, "contract.json").write_text(json.dumps(raw))
                registry = ContractRegistry(Path(directory))
                with self.assertRaisesRegex(ContractError, "contract_not_accepted"):
                    registry.get("bridge.readiness_failure", execution=True)

    def test_registry_missing_invalid_json_and_duplicate_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ContractError, "contract_directory_missing"):
                ContractRegistry(Path(directory) / "missing")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "bad.json").write_text("{broken")
            with self.assertRaisesRegex(ContractError, "contract_invalid_json"):
                ContractRegistry(Path(directory))
        with tempfile.TemporaryDirectory() as directory:
            encoded = json.dumps(self.raw)
            Path(directory, "a.json").write_text(encoded); Path(directory, "b.json").write_text(encoded)
            with self.assertRaisesRegex(ContractError, "duplicate_contract_id"):
                ContractRegistry(Path(directory))

    def test_future_evidence_rejected(self):
        result = evaluate(validate_contract(self.raw), self.evidence(observed_at=1002), now=1000)
        self.assertIn("future_evidence", result["errors"])
        self.assertEqual(result["classification"], "STALE")

    def test_missing_stale_and_false_failure_evidence_rejected(self):
        contract = validate_contract(self.raw)
        self.assertFalse(evaluate(contract, {}, now=1000)["allowed"])
        self.assertIn("stale_evidence", evaluate(contract, self.evidence(observed_at=900), now=1000)["errors"])
        self.assertIn("supported_failure_not_detected", evaluate(contract, self.evidence(health_ready=True, classification="HEALTHY", supported_failure_class=None), now=1000)["errors"])

    def test_supported_fresh_failure_requires_approval(self):
        result = evaluate(validate_contract(self.raw), self.evidence(), now=1000)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["execution_authority"], "PENDING_EXPLICIT_APPROVAL")

    def test_onboarding_tests_but_never_promotes(self):
        result = review_contract(
            self.raw, [self.evidence()], [self.evidence(health_ready=True, classification="HEALTHY", supported_failure_class=None)],
            executor_probe=lambda: True,
            verifier_probe=lambda: True,
            rollback_probe=lambda: self.raw["rollback"] == "NONE_SAFE",
        )
        self.assertEqual(result["status"], "TESTED")
        self.assertFalse(result["promoted"])
        self.assertTrue(result["acceptance_requires_human_decision"])

    def test_onboarding_rejects_missing_execution_probes(self):
        result = review_contract(self.raw, [self.evidence()], [self.evidence(health_ready=True, classification="HEALTHY", supported_failure_class=None)])
        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(result["promoted"])


if __name__ == "__main__": unittest.main()
