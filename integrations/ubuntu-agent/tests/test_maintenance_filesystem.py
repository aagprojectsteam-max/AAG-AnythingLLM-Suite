from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_agent.maintenance.filesystem import ScanLimits, display_path, scan_tree
from aag_agent.maintenance.policy import PolicyError
from tests.maintenance_helpers import make_policy, mount_record


class FilesystemScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _, self.policy = make_policy(self.root)
        self.limits = ScanLimits(5.0, 8, 1000, 100, 1)

    def tearDown(self):
        self.temporary.cleanup()

    def _scan(self, **kwargs):
        return scan_tree(self.root, self.policy, kwargs.pop("limits", self.limits), mount_records=kwargs.pop("mount_records", [mount_record(self.root)]), **kwargs)

    def test_ordinary_nested_tree_and_allocated_logical_sizes(self):
        (self.root / "a/b").mkdir(parents=True)
        (self.root / "a/b/file").write_bytes(b"x" * 123)
        (self.root / "top").write_bytes(b"z" * 17)
        result = self._scan()
        self.assertGreaterEqual(result.total.logical_bytes, 140)
        self.assertGreaterEqual(result.total.allocated_bytes, 0)
        self.assertIn(str(self.root / "a"), result.top)
        self.assertIn(str(self.root / "top"), result.top)

    def test_symlink_escape_and_cycle_are_not_followed(self):
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir()
        try:
            (outside / "secret").write_bytes(b"secret")
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            (self.root / "cycle").symlink_to(self.root, target_is_directory=True)
            result = self._scan()
            self.assertEqual(result.total.symlinks, 2)
            self.assertFalse(any(record.path.endswith("secret") for record in result.largest))
        finally:
            (outside / "secret").unlink(); outside.rmdir()

    def test_hardlinks_not_double_counted_and_sparse_file(self):
        original = self.root / "original"
        original.write_bytes(b"a" * 8192)
        os.link(original, self.root / "linked")
        sparse = self.root / "sparse"
        with sparse.open("wb") as stream:
            stream.seek(2 * 1024 * 1024)
            stream.write(b"x")
        result = self._scan(collect_candidates=True)
        self.assertEqual(result.hardlink_entries_skipped, 1)
        self.assertGreater(result.hardlink_allocated_bytes_skipped, 0)
        sparse_record = next(record for record in result.candidates if record.path == str(sparse))
        self.assertTrue(sparse_record.sparse)
        self.assertGreater(sparse_record.logical_bytes, sparse_record.allocated_bytes)

    def test_simulated_nested_mount_boundary(self):
        nested = self.root / "nested"
        nested.mkdir(); (nested / "hidden").write_bytes(b"x" * 100)
        mounts = [mount_record(self.root), mount_record(nested, mount_id=2, source="/dev/other", major_minor="0:100")]
        result = self._scan(mount_records=mounts)
        self.assertIn(str(nested), result.mount_boundaries_skipped)
        self.assertFalse(any(record.path.endswith("hidden") for record in result.largest))

    def test_control_and_non_utf8_names_have_safe_display_identity(self):
        newline = self.root / "line\n\x1bname"
        newline.write_bytes(b"x")
        raw_root = os.fsencode(self.root)
        raw_path = raw_root + b"/bad-\xff"
        fd = os.open(raw_path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(fd, b"y"); os.close(fd)
        result = self._scan(collect_candidates=True)
        records = {record.path: record for record in result.candidates}
        self.assertIn("\\u000a", records[str(newline)].display_path)
        self.assertIn("\\u001b", records[str(newline)].display_path)
        decoded = os.fsdecode(raw_path)
        self.assertIn(decoded, records)
        self.assertNotIn("\n", records[str(newline)].display_path)

    def test_depth_entry_timeout_and_cancellation_limits(self):
        (self.root / "a/b/c").mkdir(parents=True)
        (self.root / "a/b/c/file").write_bytes(b"x")
        depth = self._scan(limits=ScanLimits(5, 1, 100, 10, 1))
        self.assertIn("max_depth", depth.limits_reached)
        for index in range(20):
            (self.root / f"f{index}").write_bytes(b"x")
        entries = self._scan(limits=ScanLimits(5, 8, 5, 10, 1))
        self.assertIn("max_entries", entries.limits_reached)
        timed = self._scan(limits=ScanLimits(0.000001, 8, 1000, 10, 1))
        self.assertIn("max_duration_seconds", timed.limits_reached)
        event = threading.Event(); event.set()
        cancelled = self._scan(cancel_event=event)
        self.assertIn("cancelled", cancelled.limits_reached)

    def test_permission_and_disappearing_entry_are_structured(self):
        target = self.root / "vanish"
        target.write_bytes(b"x")
        real_lstat = Path.lstat
        def race(path):
            if path == target:
                raise FileNotFoundError
            return real_lstat(path)
        with patch.object(Path, "lstat", race):
            result = self._scan()
        self.assertTrue(any(error.code == "entry_vanished" for error in result.errors))
        denied = self.root / "denied"; denied.mkdir(); (denied / "x").write_bytes(b"x")
        denied.chmod(0)
        try:
            permission = self._scan()
            if os.access(denied, os.R_OK):
                self.skipTest("environment bypasses fixture directory permissions")
            self.assertTrue(any(error.code == "permission_denied" for error in permission.errors))
        finally:
            denied.chmod(0o700)

    def test_path_traversal_and_outside_scope_fail_closed(self):
        with self.assertRaisesRegex(PolicyError, "without_traversal"):
            scan_tree(str(self.root / ".." / self.root.name), self.policy, self.limits)
        with self.assertRaisesRegex(PolicyError, "outside_allowed_scope"):
            scan_tree("/etc", self.policy, self.limits)


if __name__ == "__main__": unittest.main()
