from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aag_agent.maintenance.filesystem import Aggregate, ScanLimits, ScanOutcome
from aag_agent.maintenance.storage import space_discrepancy
from tests.maintenance_helpers import make_policy, mount_record, small_budget


class FakeResult:
    def __init__(self, command_id: str, stdout: str, *, status: str = "completed") -> None:
        self.command_id = command_id
        self.stdout = stdout
        self.stderr = ""
        self.status = status
        self.returncode = 0 if status == "completed" else 1

    def provenance(self):
        return {
            "command_id": self.command_id,
            "status": self.status,
            "read_only": True,
            "mutated": False,
        }


class FakeRunner:
    def run(self, command_id, parameters=None):
        del parameters
        if command_id == "lsof_deleted":
            return FakeResult(command_id, "p123\x00s4096\x00n/private/name (deleted)\x00")
        if command_id == "docker_df":
            return FakeResult(
                command_id,
                '{"Type":"Images","TotalCount":"2","Active":"1",'
                '"Size":"1GB","Reclaimable":"100MB (10%)"}\n',
            )
        raise AssertionError(f"unexpected command: {command_id}")


class StorageDiscrepancyTests(unittest.TestCase):
    def test_correlates_bounded_causes_without_exposing_deleted_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config, policy = make_policy(
                root,
                registry={"schema": "aag-component-registry-v1", "components": []},
            )
            outcome = ScanOutcome(root, 99, "0:99:/dev/test:ext4:/", ScanLimits(5, 4, 100, 20, 1))
            outcome.total = Aggregate(logical_bytes=12_288, allocated_bytes=8_192, files=2, directories=1)
            outcome.hardlink_entries_skipped = 1
            outcome.hardlink_allocated_bytes_skipped = 4_096
            outcome.mount_boundaries_skipped = [str(root / "nested")]
            stat = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=30, f_frsize=4_096)

            with patch("aag_agent.maintenance.storage.scan_tree", return_value=outcome), patch(
                "aag_agent.maintenance.storage.read_mountinfo", return_value=[mount_record(root)]
            ), patch("aag_agent.maintenance.storage.os.statvfs", return_value=stat):
                envelope = space_discrepancy(
                    root,
                    policy,
                    small_budget(),
                    config=config,
                    runner=FakeRunner(),
                )

            checks = {item["check"]: item for item in envelope["result"]["checks"]}
            self.assertTrue(checks["df_vs_scanned_allocated"]["comparable"])
            self.assertEqual(checks["df_vs_scanned_allocated"]["reserved_or_root_only_bytes"], 40_960)
            self.assertEqual(checks["deleted_open_files"]["reported_size_bytes"], 4_096)
            self.assertTrue(checks["deleted_open_files"]["paths_withheld"])
            self.assertNotIn("private", str(checks["deleted_open_files"]))
            self.assertEqual(checks["sparse_files"]["logical_minus_allocated_bytes"], 4_096)
            self.assertEqual(checks["hardlinks"]["entries_not_double_counted"], 1)
            self.assertEqual(checks["nested_filesystems"]["mount_boundaries_skipped"], [str(root / "nested")])
            self.assertEqual(checks["docker_storage"]["result"]["status"], "observed")
            self.assertEqual(checks["registered_vm_snapshot_backup_assets"]["result"]["status"], "observed")
            self.assertEqual(envelope["completeness"]["status"], "complete")

    def test_non_mount_scope_does_not_claim_df_scan_comparability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            child = root / "child"
            child.mkdir()
            _, policy = make_policy(
                root,
                registry={"schema": "aag-component-registry-v1", "components": []},
            )
            outcome = ScanOutcome(child, 99, "0:99:/dev/test:ext4:/", ScanLimits(5, 4, 100, 20, 1))
            outcome.total = Aggregate(logical_bytes=4_096, allocated_bytes=4_096, files=1, directories=1)
            stat = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=30, f_frsize=4_096)

            with patch("aag_agent.maintenance.storage.scan_tree", return_value=outcome), patch(
                "aag_agent.maintenance.storage.read_mountinfo", return_value=[mount_record(root)]
            ), patch("aag_agent.maintenance.storage.os.statvfs", return_value=stat):
                envelope = space_discrepancy(
                    child,
                    policy,
                    small_budget(),
                    runner=FakeRunner(),
                )

            first = envelope["result"]["checks"][0]
            self.assertFalse(first["comparable"])
            self.assertIsNone(first["difference_bytes"])
            self.assertEqual(first["reason"], "scope_is_not_mount_root")


if __name__ == "__main__":
    unittest.main()
