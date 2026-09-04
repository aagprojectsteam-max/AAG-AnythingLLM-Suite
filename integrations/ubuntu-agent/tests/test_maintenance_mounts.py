from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from aag_agent.maintenance.mounts import parse_mountinfo, select_backing_mount, storage_overview
from tests.maintenance_helpers import make_policy


class Result:
    status = "completed"
    stdout = json.dumps({"blockdevices": []})
    stderr = ""
    returncode = 0
    def provenance(self): return {"command_id":"lsblk_json","status":"completed","read_only":True,"mutated":False}


class Runner:
    def run(self, name, parameters=None): return Result()


class MountTests(unittest.TestCase):
    def test_autofs_parent_does_not_override_rw_backing_mount(self):
        text = "\n".join([
            "10 1 0:39 / /mnt/data rw,relatime shared:1 - autofs systemd-1 rw,fd=1",
            "11 10 8:17 / /mnt/data rw,relatime shared:2 - ext4 /dev/sdb1 rw,stripe=1",
        ])
        records = parse_mountinfo(text)
        selected = select_backing_mount("/mnt/data", records)
        self.assertEqual(selected.filesystem_type, "ext4")
        self.assertFalse(selected.mount_read_only)

    def test_genuine_readonly_backing_remains_readonly(self):
        records = parse_mountinfo("11 10 8:17 / /mnt/data ro,relatime - ext4 /dev/sdb1 ro")
        self.assertTrue(select_backing_mount("/mnt/data/project", records).mount_read_only)

    def test_storage_overview_reports_capacity_inodes_and_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, policy = make_policy(root, registry={"schema":"aag-component-registry-v1","components":[]})
            escaped = str(root).replace(" ", "\\040")
            records = parse_mountinfo(f"11 1 0:99 / {escaped} rw - ext4 /dev/test rw")
            result = storage_overview(policy, runner=Runner(), mount_records=records, statvfs=os.statvfs)
            mount = result["result"]["mounts"][0]
            self.assertIsInstance(mount["total_bytes"], int)
            self.assertIn("inode_usage_percent", mount)
            self.assertFalse(mount["read_only"])


if __name__ == "__main__": unittest.main()
