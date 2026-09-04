import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aag_agent.observations import MAX_OUTPUT_BYTES, ObservationError, build_spec, observe


class ObservationTests(unittest.TestCase):
    def test_domains_have_absolute_fixed_binaries_and_no_shell(self):
        cases = {
            "systemd": {"service": "example.service"}, "journal": {"service": "example.service"},
            "process": {"pid": 1}, "network": {"interface": "eth0"},
            "mount": {"path": "/mnt/data"}, "filesystem": {"path": "/"},
            "docker": {"container": "anythingllm"}, "package": {"package": "python3"}, "kernel": {},
            "uptime": {}, "memory": {}, "processes": {},
            "failed_units": {"manager": "system"}, "network_overview": {},
            "routes": {}, "block_devices": {}, "docker_overview": {}, "boot_events": {},
        }
        for domain, query in cases.items():
            spec = build_spec(domain, query)
            self.assertTrue(spec.binary.startswith("/"))
            seen = {}
            def runner(command, **kwargs):
                seen.update(kwargs); return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")
            result = observe(domain, query, runner=runner)
            self.assertIs(seen["shell"], False)
            self.assertTrue(result["read_only"]); self.assertFalse(result["mutated"])

    def test_injection_and_invalid_inputs_rejected(self):
        bad = [
            ("systemd", {"service": "x.service;id"}), ("process", {"pid": "1;id"}),
            ("network", {"interface": "eth0 --help"}), ("docker", {"container": "$(id)"}),
            ("package", {"package": "../evil"}), ("mount", {"path": "/mnt/data/../etc"}),
            ("filesystem", {"path": "/home/aag-linux/.ssh"}), ("kernel", {"binary": "/tmp/uname"}),
        ]
        for domain, query in bad:
            with self.assertRaises(ObservationError, msg=(domain, query)): build_spec(domain, query)

    def test_output_is_bounded(self):
        huge = b"x" * (MAX_OUTPUT_BYTES + 100)
        result = observe("kernel", {}, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=huge, stderr=huge))
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["stdout"]), MAX_OUTPUT_BYTES)

    def test_timeout_is_structured(self):
        def runner(*args, **kwargs): raise subprocess.TimeoutExpired(args[0], 1)
        result = observe("kernel", {}, timeout=1, runner=runner)
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["mutated"])

    def test_systemd_manager_is_typed_not_arbitrary_flags(self):
        user = build_spec("systemd", {"service": "example.service", "manager": "user"})
        system = build_spec("systemd", {"service": "example.service", "manager": "system"})
        self.assertEqual(user.argv[0], "--user"); self.assertEqual(system.argv[0], "--system")
        with self.assertRaisesRegex(ObservationError, "invalid_systemd_manager"):
            build_spec("systemd", {"service": "example.service", "manager": "--host=evil"})

    def test_sensitive_and_symlink_paths_are_blocked(self):
        for path in ("/home/aag-linux/.ssh", "/etc/shadow", "/proc/self/environ", "/sys/kernel"):
            with self.assertRaises(ObservationError): build_spec("filesystem", {"path": path})
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory(dir=root) as directory:
            link = Path(directory) / "escape"; link.symlink_to("/home/aag-linux/.ssh")
            with self.assertRaisesRegex(ObservationError, "sensitive_path_blocked"):
                build_spec("filesystem", {"path": str(link)})

    def test_newline_backticks_dollar_and_null_are_rejected(self):
        for value in ("x.service\nid", "x`id`.service", "x$(id).service", "x;$HOME.service", None):
            with self.assertRaises(ObservationError): build_spec("systemd", {"service": value})
        with self.assertRaisesRegex(ObservationError, "query_must_be_object"):
            build_spec("kernel", "not-an-object")

    def test_missing_binary_permission_nonzero_malformed_and_unicode(self):
        def missing(*args, **kwargs): raise FileNotFoundError()
        def denied(*args, **kwargs): raise PermissionError()
        self.assertEqual(observe("kernel", runner=missing)["status"], "missing_binary")
        self.assertEqual(observe("kernel", runner=denied)["status"], "permission_denied")
        failed = observe("kernel", runner=lambda *a, **k: SimpleNamespace(returncode=2, stdout=b"", stderr=b"denied"))
        self.assertEqual(failed["status"], "command_failed")
        malformed = observe("network", {"interface": "eth0"}, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"not-json", stderr=b""))
        self.assertTrue(malformed["normalization_error"].startswith("malformed_output"))
        unicode_result = observe("kernel", runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="לינוקס\n", stderr=""))
        self.assertEqual(unicode_result["facts"]["uname"], "לינוקס")

    def test_docker_inspection_does_not_request_environment_or_exec(self):
        spec = build_spec("docker", {"container": "anythingllm"})
        joined = " ".join(spec.argv)
        self.assertIn("inspect", spec.argv)
        self.assertNotIn("exec", spec.argv)
        self.assertNotIn(".Config.Env", joined)
        self.assertNotIn("restart", spec.argv)
        overview = build_spec("docker_overview", {})
        joined = " ".join(overview.argv)
        self.assertNotIn("Labels", joined)
        self.assertNotIn("Mounts", joined)
        self.assertNotIn("Command", joined)

    def test_package_absence_is_a_normalized_observation(self):
        result = observe("package", {"package":"missing-demo"}, runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b"no packages found"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["facts"], {"package":"missing-demo", "installed":False})

    def test_user_manager_environment_is_fixed_not_inherited(self):
        seen = {}
        def runner(command, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(returncode=0, stdout=b"Id=x.service\n", stderr=b"")
        observe("systemd", {"service":"x.service", "manager":"user"}, runner=runner)
        self.assertEqual(set(seen["env"]), {"PATH", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"})
        self.assertTrue(seen["env"]["DBUS_SESSION_BUS_ADDRESS"].startswith("unix:path=/run/user/"))


if __name__ == "__main__": unittest.main()
