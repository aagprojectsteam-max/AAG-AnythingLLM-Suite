import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import BoundaryError, RuntimeSession


class RuntimeAttestationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.process = subprocess.Popen(["/usr/bin/sleep", "30"])
        self.addCleanup(self._cleanup_process)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        raw = Path(f"/proc/{self.process.pid}/stat").read_text(encoding="utf-8")
        self.starttime = raw.rsplit(") ", 1)[1].split()[19]
        self.values = {
            "pid": str(self.process.pid),
            "starttime": self.starttime,
            "model": "/models/current.gguf",
            "executable": "/usr/bin/sleep",
            "context": "8192",
            "profile": "normal",
        }
        self._publish(self.values)

    def _cleanup_process(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=3)

    def _publish(self, values):
        for name, value in values.items():
            (self.state / name).write_text(value, encoding="utf-8")

    def _assert_code(self, code):
        with self.assertRaises(BoundaryError) as caught:
            RuntimeSession(self.state, "/usr/bin/sleep").fingerprint()
        self.assertEqual(caught.exception.code, code)

    def test_correct_starttime(self):
        _, material = RuntimeSession(self.state, "/usr/bin/sleep").fingerprint()
        self.assertEqual(material["starttime"], self.starttime)

    def test_stale_starttime(self):
        (self.state / "starttime").write_text(str(int(self.starttime) - 1))
        self._assert_code("MODEL_RUNTIME_STARTTIME_MISMATCH")

    def test_wrong_pid(self):
        (self.state / "pid").write_text("999999999")
        self._assert_code("MODEL_RUNTIME_ATTESTATION_FILE_NOT_FOUND")

    def test_wrong_executable(self):
        (self.state / "executable").write_text("/usr/bin/false")
        self._assert_code("MODEL_RUNTIME_EXECUTABLE_STATE_INVALID")

    def test_wrong_uid(self):
        with mock.patch.object(RuntimeSession, "_process_uid", return_value=os.getuid() + 1):
            self._assert_code("MODEL_RUNTIME_UID_MISMATCH")

    def test_stale_generation_cgroup(self):
        self._publish({
            "unit": "aag-llama-server-runtime-1-2.service",
            "invocation_id": "0" * 32,
        })
        self._assert_code("MODEL_RUNTIME_CGROUP_MISMATCH")

    def test_partially_published_state(self):
        (self.state / "context").unlink()
        self._assert_code("MODEL_RUNTIME_STATE_MISSING")


if __name__ == "__main__":
    unittest.main()
