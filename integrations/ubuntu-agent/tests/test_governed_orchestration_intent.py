from __future__ import annotations

import json
import unittest
from pathlib import Path

from aag_agent.orchestration.intent import classify

ROOT = Path(__file__).parents[1]
GOLDEN = ROOT / "tests/fixtures/orchestration/golden_intents.json"


class GovernedIntentTests(unittest.TestCase):
    def test_golden_bilingual_intents(self):
        fixture = json.loads(GOLDEN.read_text())
        self.assertGreaterEqual(len(fixture["cases"]), 100)
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                decision = classify(case["request"])
                self.assertEqual(decision.intent, case["intent"])
                self.assertEqual(decision.playbook_id, case["playbook"])
                if "clarification_required" in case:
                    self.assertEqual(decision.clarification_required, case["clarification_required"])

    def test_generic_repair_requires_a_trusted_target(self):
        decision = classify("Prepare a repair plan but do not execute anything")
        self.assertEqual(decision.intent, "REMEDIATION_CLARIFICATION")
        self.assertIsNone(decision.playbook_id)
        self.assertTrue(decision.clarification_required)

    def test_multi_domain_repair_requires_clarification(self):
        decision = classify("Fix the slow computer and full storage")
        self.assertTrue(decision.clarification_required)
        self.assertIsNone(decision.playbook_id)

    def test_empty_and_oversized_request_rejected(self):
        for request in ("", "x" * 4097):
            with self.subTest(size=len(request)), self.assertRaises(ValueError):
                classify(request)

    def test_classifier_exposes_no_authority(self):
        result = classify("Fix the Bridge").public()
        self.assertFalse(result["classifier_is_authority"])
        self.assertNotIn("command", result)
        self.assertNotIn("approval", result)


if __name__ == "__main__":
    unittest.main()
