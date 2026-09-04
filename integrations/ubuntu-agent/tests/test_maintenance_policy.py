from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aag_agent.maintenance.policy import PolicyError, ProtectedResourcePolicy
from tests.maintenance_helpers import make_policy, policy_document, test_config


class ProtectedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _registry(self):
        return {
            "schema": "aag-component-registry-v1",
            "components": [{
                "identity": "fixture-component",
                "name": "Fixture",
                "dependencies": ["storage"],
                "dependents": ["service"],
                "mutation_risk": "high",
                "confidence": "high"
            }]
        }

    def test_protected_exact_subtree_and_prefix_confusion(self):
        protected = self.root / "protected"; protected.mkdir()
        doc = policy_document(protected, protection_class="protected", hashing=False, cleanup=False)
        exact = dict(doc["resources"][0]); exact.update({"resource_id": "exact", "path": str(self.root / "exact"), "match": "exact"})
        doc["resources"].append(exact)
        policy_path = self.root / "policy.json"; policy_path.write_text(json.dumps(doc), encoding="utf-8")
        registry_path = self.root / "registry.json"; registry_path.write_text(json.dumps(self._registry()), encoding="utf-8")
        policy = ProtectedResourcePolicy(test_config(self.root), policy_path=policy_path, registry_path=registry_path)
        self.assertEqual(policy.classify(protected / "child").protection_class, "protected")
        self.assertEqual(policy.classify(self.root / "exact").resource_id, "exact")
        self.assertEqual(policy.classify(self.root / "exact" / "child").resource_id, "unknown-resource")
        self.assertEqual(policy.classify(self.root / "protected-other").resource_id, "unknown-resource")

    def test_traversal_and_symlink_escape(self):
        _, policy = make_policy(self.root, registry=self._registry())
        with self.assertRaisesRegex(PolicyError, "without_traversal"):
            policy.validate_scope(str(self.root / ".." / self.root.name))
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir()
        try:
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(PolicyError, "outside_allowed_scope"):
                policy.validate_scope(self.root / "escape")
        finally:
            (self.root / "escape").unlink(); outside.rmdir()

    def test_unknown_resource_fails_closed(self):
        child = self.root / "other"
        protected_root = self.root / "known"; protected_root.mkdir()
        config, policy = make_policy(protected_root, registry=self._registry())
        # Build a wider allowed scope without changing the exact policy rule.
        config = test_config(self.root)
        policy = ProtectedResourcePolicy(config, policy_path=protected_root / "policy.json", registry_path=protected_root / "registry.json")
        decision = policy.classify(child)
        self.assertEqual(decision.protection_class, "unknown")
        self.assertFalse(decision.content_hashing_allowed)
        self.assertFalse(decision.cleanup_may_be_proposed)
        self.assertEqual(str(decision.cleanup_classification), "REVIEW_REQUIRED")

    def test_missing_and_malformed_registry_fail_closed_for_cleanup(self):
        _, missing = make_policy(self.root)
        self.assertEqual(missing.registry_status, "missing")
        self.assertEqual(str(missing.classify(self.root).cleanup_classification), "REVIEW_REQUIRED")
        config = test_config(self.root)
        policy_path = self.root / "policy.json"
        malformed_path = self.root / "bad-registry.json"; malformed_path.write_text("{bad", encoding="utf-8")
        malformed = ProtectedResourcePolicy(config, policy_path=policy_path, registry_path=malformed_path)
        self.assertEqual(malformed.registry_status, "malformed")
        self.assertEqual(str(malformed.classify(self.root).cleanup_classification), "REVIEW_REQUIRED")

    def test_dependency_known_and_graph_provenance(self):
        _, policy = make_policy(self.root, registry=self._registry())
        decision = policy.classify(self.root)
        self.assertEqual(decision.dependency_status, "known")
        self.assertIn("storage", decision.dependencies)
        self.assertIn("service", decision.dependents)
        graph = policy.dependency_graph()
        self.assertFalse(graph["complete"])
        self.assertTrue(any(edge["from"] == "fixture-component" and edge["to"] == "storage" for edge in graph["edges"]))

    def test_valid_registered_generated_output_can_be_low_risk_candidate(self):
        _, policy = make_policy(self.root, registry=self._registry())
        decision = policy.classify(self.root)
        self.assertEqual(str(decision.cleanup_classification), "LOW_RISK_CANDIDATE")
        self.assertTrue(decision.cleanup_may_be_proposed)


if __name__ == "__main__": unittest.main()
