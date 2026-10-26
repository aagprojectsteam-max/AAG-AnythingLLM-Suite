import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402


class AuditRotationTest(unittest.TestCase):
    def test_rotates_when_size_threshold_is_reached(self):
        with tempfile.TemporaryDirectory() as root:
            log = server.AuditLog(Path(root) / "audit" / "audit.ndjson")
            original_max, original_keep = server.AUDIT_MAX_BYTES, server.AUDIT_KEEP_FILES
            server.AUDIT_MAX_BYTES, server.AUDIT_KEEP_FILES = 512, 2
            try:
                for index in range(60):
                    log.write("test_event", index=index, padding="x" * 64)
                names = sorted(p.name for p in log.path.parent.iterdir())
                self.assertIn("audit.ndjson", names)
                self.assertIn("audit.ndjson.1", names)
                self.assertNotIn("audit.ndjson.3", names)
                self.assertLessEqual(len(names), 3)
                self.assertLess(log.path.stat().st_size, 512 + 256)
                self.assertEqual(oct(log.path.parent.stat().st_mode & 0o777), oct(0o700))
            finally:
                server.AUDIT_MAX_BYTES, server.AUDIT_KEEP_FILES = original_max, original_keep

    def test_limits_are_unified_and_bounded(self):
        self.assertEqual(server.MAX_FORWARD_BODY, server.MAX_CHAT_REQUEST_BODY)
        self.assertGreaterEqual(server.MAX_CHAT_REQUEST_BODY, 6_354_214)  # historical 413 payload
        self.assertGreaterEqual(server.UPSTREAM_COMPLETION_TIMEOUT_SECONDS, 30)
        self.assertLessEqual(server.UPSTREAM_COMPLETION_TIMEOUT_SECONDS, 1800)

    def test_bounded_env_rejects_garbage_and_clamps(self):
        os.environ["AAG_TEST_BOUNDED"] = "abc"
        try:
            self.assertEqual(server._bounded_env("AAG_TEST_BOUNDED", 120, 30, 1800), 120)
            os.environ["AAG_TEST_BOUNDED"] = "999999"
            self.assertEqual(server._bounded_env("AAG_TEST_BOUNDED", 120, 30, 1800), 1800)
            os.environ["AAG_TEST_BOUNDED"] = "1"
            self.assertEqual(server._bounded_env("AAG_TEST_BOUNDED", 120, 30, 1800), 30)
        finally:
            del os.environ["AAG_TEST_BOUNDED"]


if __name__ == "__main__":
    unittest.main()
