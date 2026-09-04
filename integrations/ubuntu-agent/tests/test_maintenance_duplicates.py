from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_agent.maintenance.duplicates import duplicate_analysis
from tests.maintenance_helpers import make_policy, small_budget


class DuplicateAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _, self.policy = make_policy(self.root)
        self.budget = small_budget()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _classes(result):
        return [group["classification"] for group in result["result"]["groups"]]

    def test_same_size_different_content_is_not_confirmed(self):
        (self.root / "a").write_bytes(b"abcd")
        (self.root / "b").write_bytes(b"wxyz")
        result = duplicate_analysis(self.root, self.policy, self.budget, verify_full=True)
        self.assertNotIn("confirmed_content_match", self._classes(result))

    def test_confirmed_duplicate_and_conservative_reclaim(self):
        content = b"same-content" * 100
        (self.root / "a").write_bytes(content)
        (self.root / "b").write_bytes(content)
        result = duplicate_analysis(self.root, self.policy, self.budget, verify_full=True)
        group = next(group for group in result["result"]["groups"] if group["classification"] == "confirmed_content_match")
        self.assertEqual(group["confidence"], "confirmed")
        self.assertGreaterEqual(group["estimated_reclaimable_bytes"], 0)
        self.assertEqual(result["result"]["full_hash_bytes_read"], len(content) * 2)

    def test_quick_collision_requires_full_hash(self):
        (self.root / "a").write_bytes(b"A" * 100)
        (self.root / "b").write_bytes(b"B" * 100)
        with patch("aag_agent.maintenance.duplicates._quick_fingerprint", return_value=("collision", 8)):
            candidate = duplicate_analysis(self.root, self.policy, self.budget, verify_full=False)
            verified = duplicate_analysis(self.root, self.policy, self.budget, verify_full=True)
        self.assertIn("probable_content_match", self._classes(candidate))
        self.assertNotIn("confirmed_content_match", self._classes(verified))

    def test_same_inode_hardlink_has_zero_reclaim(self):
        source = self.root / "a"; source.write_bytes(b"same" * 100)
        os.link(source, self.root / "b")
        result = duplicate_analysis(self.root, self.policy, self.budget)
        group = next(group for group in result["result"]["groups"] if group["classification"] == "same_inode_hardlink")
        self.assertEqual(group["estimated_reclaimable_bytes"], 0)

    def test_changing_file_is_not_confirmed(self):
        (self.root / "a").write_bytes(b"same" * 100)
        (self.root / "b").write_bytes(b"same" * 100)
        with patch("aag_agent.maintenance.duplicates._stable_stat", side_effect=RuntimeError("file_changed_during_hash")):
            result = duplicate_analysis(self.root, self.policy, self.budget, verify_full=True)
        self.assertNotIn("confirmed_content_match", self._classes(result))
        self.assertTrue(any(error["code"] == "file_changed_during_hash" for error in result["errors"]))

    def test_protected_files_are_not_hashed(self):
        _, protected = make_policy(self.root, protection_class="protected", hashing=False, cleanup=False)
        (self.root / "a").write_bytes(b"same" * 100)
        (self.root / "b").write_bytes(b"same" * 100)
        with patch("aag_agent.maintenance.duplicates._quick_fingerprint") as fingerprint:
            result = duplicate_analysis(self.root, protected, self.budget)
        fingerprint.assert_not_called()
        self.assertIn("intentionally_redundant_or_protected", self._classes(result))

    def test_full_hash_budget_exhaustion_is_partial(self):
        content = b"same" * 100
        (self.root / "a").write_bytes(content)
        (self.root / "b").write_bytes(content)
        result = duplicate_analysis(self.root, self.policy, small_budget(max_full_hash_bytes=len(content)), verify_full=True)
        self.assertIn("full_hash_budget_exhausted", result["completeness"]["limits_reached"])
        self.assertEqual(result["completeness"]["status"], "partial")
        self.assertNotIn("confirmed_content_match", self._classes(result))

    def test_groups_do_not_overlap(self):
        for name in ("a", "b", "c"):
            (self.root / name).write_bytes(b"same" * 100)
        result = duplicate_analysis(self.root, self.policy, self.budget, verify_full=True)
        groups = [group for group in result["result"]["groups"] if group["classification"] == "confirmed_content_match"]
        paths = [item["path"] for group in groups for item in group["files"]]
        self.assertEqual(len(paths), len(set(paths)))


if __name__ == "__main__": unittest.main()
