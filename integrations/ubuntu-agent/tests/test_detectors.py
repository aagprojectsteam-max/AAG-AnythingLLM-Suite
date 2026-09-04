import json
import unittest
from pathlib import Path

from aag_agent.contracts import ContractRegistry
from aag_agent.detectors import normalize_bridge_evidence
from aag_agent.policy import evaluate

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/bridge_detector"


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.contract = ContractRegistry(ROOT / "contracts").get("bridge.readiness_failure", execution=True)

    def fixture(self, name):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    def test_durable_positive_and_negative_fixtures(self):
        expected = {
            "supported_readiness_failure": True, "healthy_bridge": False,
            "inactive_service": False, "wrong_target": False,
            "stale_evidence": False, "missing_evidence": False,
            "indeterminate_health": False,
            "unobservable_health": False,
        }
        for name, allowed in expected.items():
            evidence = self.fixture(name)
            now = 1000.0 if name != "stale_evidence" else 1000.0
            self.assertEqual(evaluate(self.contract, evidence, now=now)["allowed"], allowed, name)

    def test_normalizer_classifies_only_exact_supported_failure(self):
        snapshot = {"status": "completed", "target": "aag-ubuntu-agent-bridge.service", "load_state": "loaded", "active_state": "active", "sub_state": "running", "main_pid": "5"}
        supported = normalize_bridge_evidence(snapshot, {"ready": False, "error": "readiness_timeout"}, observed_at=1)
        self.assertEqual(supported["classification"], "SUPPORTED_FAILURE")
        indeterminate = normalize_bridge_evidence(snapshot, {"ready": False, "error": "synthetic_verifier_error"}, observed_at=1)
        self.assertEqual(indeterminate["classification"], "INDETERMINATE")
        self.assertIsNone(indeterminate["supported_failure_class"])
        unobservable = normalize_bridge_evidence(snapshot, {"ready": False, "error": "permission_denied"}, observed_at=1)
        self.assertEqual(unobservable["classification"], "UNOBSERVABLE")

    def test_wrong_target_fails_closed_before_failure_class(self):
        evidence = normalize_bridge_evidence({"status": "completed", "target": "evil.service", "load_state": "loaded", "active_state": "active", "sub_state": "running"}, {"ready": False, "error": "readiness_timeout"}, target="evil.service", observed_at=1)
        self.assertEqual(evidence["classification"], "WRONG_TARGET")


if __name__ == "__main__": unittest.main()
