import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

import lifecycle  # noqa: E402  (same import style as test_lifecycle.py)


class FakeHost:
    def __init__(self, stuck_polls=0):
        self.calls = []; self.stuck_polls = stuck_polls
    def run(self, args, timeout=90): self.calls.append(list(args))
    def start(self): self.calls.append(["start"])
    def measure(self):
        return {k: {"state": "READY"} for k in ("comfy", "hub", "upscale", "reference")}


class ReleaseTests(unittest.TestCase):
    def test_gentle_release_keeps_engine_ready_without_restart(self):
        host = FakeHost()
        with mock.patch.object(lifecycle, "get", lambda url: {"system": {}} if "system_stats" in url else {"queue_running": [], "queue_pending": []}), \
             mock.patch.object(lifecycle, "post", lambda url, payload, timeout=5: 200), \
             mock.patch.object(lifecycle, "mem_available_mb", side_effect=[10_000, 30_000]):
            report = lifecycle.host_release(host)
        self.assertTrue(report["interrupted"] and report["freed"])
        self.assertFalse(report["restarted"])
        self.assertEqual(report["freed_mb"], 20_000)
        self.assertEqual(host.calls, [["start"]])

    def test_stuck_work_escalates_to_owned_session_restart(self):
        host = FakeHost()
        with mock.patch.object(lifecycle, "get", lambda url: {"system": {}} if "system_stats" in url else {"queue_running": [["job"]], "queue_pending": []}), \
             mock.patch.object(lifecycle, "post", lambda url, payload, timeout=5: 200), \
             mock.patch.object(lifecycle.time, "sleep", lambda s: None), \
             mock.patch.object(lifecycle.time, "monotonic", side_effect=[0, 0, 0, 0, 0, 0, 20, 21, 22, 23, 24, 25, 26, 27]), \
             mock.patch.object(lifecycle, "mem_available_mb", side_effect=[10_000, 12_000]):
            report = lifecycle.host_release(host)
        self.assertTrue(report["restarted"])
        self.assertEqual(host.calls[0][:3], ["systemctl", "--user", "stop"])
        self.assertEqual(host.calls[-1], ["start"])

    def test_release_when_engine_is_off_only_starts(self):
        host = FakeHost()
        with mock.patch.object(lifecycle, "get", lambda url: None), mock.patch.object(lifecycle, "mem_available_mb", side_effect=[5, 5]):
            report = lifecycle.host_release(host)
        self.assertFalse(report["interrupted"] or report["freed"] or report["restarted"])
        self.assertEqual(host.calls, [["start"]])

    def test_manager_records_last_release_and_rejects_unknown_actions(self):
        with tempfile.TemporaryDirectory() as root:
            manager = lifecycle.Manager(root=root, host=FakeHost())
            with mock.patch.object(lifecycle, "host_release", lambda host: {"schema": "aag.image.release.v1", "restarted": False, "freed_mb": 1, "seconds": 0.1}):
                result = manager.operate("release")
            self.assertEqual(result["state"], "READY")
            self.assertEqual(result["last_release"]["freed_mb"], 1)
            self.assertEqual(json.loads((Path(root) / "operation.json").read_text())["state"], "READY")
            with self.assertRaises(lifecycle.Failure):
                manager.operate("purge")


if __name__ == "__main__":
    unittest.main()


class ForcedStopTests(unittest.TestCase):
    def test_stop_drains_owned_work_instead_of_refusing(self):
        host = lifecycle.Host(); host.calls = []
        host.run = lambda args, timeout=90: host.calls.append(list(args))
        host.units = lambda user, names: {n: "inactive" for n in names}
        polls = [{"queue_running": [["job"]], "queue_pending": [["next"]]}]
        def fake_get(url):
            if "queue" not in url: return None
            return polls.pop(0) if polls else {"queue_running": [], "queue_pending": []}
        with mock.patch.object(lifecycle, "get", fake_get), \
             mock.patch.object(lifecycle, "post", lambda url, payload, timeout=5: 200), \
             mock.patch.object(lifecycle.time, "sleep", lambda s: None), \
             mock.patch("pathlib.Path.glob", lambda self, pattern: iter(())):
            host.stop()
        self.assertTrue(host.last_stop["forced"] and host.last_stop["interrupted"] and host.last_stop["cleared_pending"])
        self.assertFalse(host.last_stop["still_running_at_stop"])
        self.assertTrue(any(c[:3] == ["systemctl", "--user", "stop"] for c in host.calls))

    def test_stop_still_refuses_unowned_reference_identity_work(self):
        host = lifecycle.Host()
        host.units = lambda user, names: {n: "active" for n in names}
        with mock.patch("pathlib.Path.glob", lambda self, pattern: iter(())):
            with self.assertRaises(lifecycle.Failure) as ctx:
                host.stop()
        self.assertEqual(ctx.exception.code, "IMAGE_WORK_ACTIVE")
