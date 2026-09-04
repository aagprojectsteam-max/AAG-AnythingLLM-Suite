from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_agent.maintenance.command import (
    CommandRunner,
    CommandSpec,
    CommandValidationError,
)


class CommandRunnerTests(unittest.TestCase):
    def _runner(self, script: Path, *, timeout=1.0, maximum=1024):
        return CommandRunner(
            timeout_seconds=timeout,
            max_output_bytes=maximum,
            commands={"fixture": CommandSpec(Path(sys.executable), lambda params: (str(script), *tuple(params.get("args", ()))))}
        )

    def test_allowed_command_locale_and_sanitized_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "show.py"
            script.write_text("import os; print(os.environ.get('LC_ALL')); print('SECRET' in os.environ)", encoding="utf-8")
            with patch.dict(os.environ, {"SECRET": "must-not-leak"}, clear=False):
                result = self._runner(script).run("fixture")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.stdout.splitlines(), ["C", "False"])
            self.assertTrue(result.read_only); self.assertFalse(result.mutated)

    def test_blocked_executable_and_suspicious_path(self):
        runner = CommandRunner()
        with self.assertRaisesRegex(CommandValidationError, "not_allowlisted"):
            runner.run("evil")
        with self.assertRaisesRegex(CommandValidationError, "invalid_path"):
            runner.run("df_bytes", {"path": "-rf"})

    def test_shell_false_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "ok.py"; script.write_text("print('ok')", encoding="utf-8")
            real = __import__("subprocess").Popen
            calls = []
            def capture(*args, **kwargs):
                calls.append((args, kwargs)); return real(*args, **kwargs)
            with patch("aag_agent.maintenance.command.subprocess.Popen", side_effect=capture):
                self._runner(script).run("fixture")
            self.assertIs(calls[0][1]["shell"], False)
            self.assertIsInstance(calls[0][0][0], list)

    def test_timeout_nonzero_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sleep_script = root / "sleep.py"; sleep_script.write_text("import time; time.sleep(2)", encoding="utf-8")
            self.assertEqual(self._runner(sleep_script, timeout=0.05).run("fixture").status, "timeout")
            fail_script = root / "fail.py"; fail_script.write_text("import sys; print('bad', file=sys.stderr); raise SystemExit(7)", encoding="utf-8")
            failed = self._runner(fail_script).run("fixture")
            self.assertEqual(failed.status, "nonzero_exit"); self.assertEqual(failed.returncode, 7)
            large_script = root / "large.py"; large_script.write_text("import sys; print('x'*500); print('y'*500,file=sys.stderr)", encoding="utf-8")
            large = self._runner(large_script, maximum=40).run("fixture")
            self.assertTrue(large.stdout_truncated); self.assertTrue(large.stderr_truncated)
            self.assertIn("AAG_OUTPUT_TRUNCATED", large.stdout)

    def test_missing_and_permission_denied(self):
        missing = CommandRunner(commands={"fixture": CommandSpec(Path("/definitely/missing/aag"), lambda p: ())}).run("fixture")
        self.assertEqual(missing.status, "missing_command")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "denied"
            target.write_text("not executable", encoding="utf-8")
            target.chmod(stat.S_IRUSR | stat.S_IWUSR)
            denied = CommandRunner(commands={"fixture": CommandSpec(target, lambda p: ())}).run("fixture")
            self.assertEqual(denied.status, "permission_denied")

    def test_malformed_json_is_not_silently_successful(self):
        class Result:
            status = "completed"; stdout = "not-json"; stderr = ""; returncode = 0
            def provenance(self): return {"status": self.status}
        class Runner:
            def run(self, name, parameters=None): return Result()
        with tempfile.TemporaryDirectory() as directory:
            from tests.maintenance_helpers import make_policy, mount_record
            from aag_agent.maintenance.mounts import storage_overview
            _, policy = make_policy(Path(directory))
            result = storage_overview(policy, runner=Runner(), mount_records=[mount_record(Path(directory))], statvfs=os.statvfs)
            self.assertTrue(any(error["code"] == "lsblk_malformed_json" for error in result["errors"]))
            self.assertEqual(result["completeness"]["status"], "partial")


if __name__ == "__main__": unittest.main()
