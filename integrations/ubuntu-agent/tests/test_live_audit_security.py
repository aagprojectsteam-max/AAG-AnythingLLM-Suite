import unittest

from tools import live_audit


class LiveAuditSecurityTests(unittest.TestCase):
    def test_all_profile_commands_are_fixed_absolute_and_nonmutating(self):
        prohibited = {"restart", "start", "stop", "kill", "rm", "mount", "umount", "exec"}
        for profile, commands in live_audit.CHECKS.items():
            for command in commands:
                self.assertTrue(command[0].startswith("/"), (profile, command))
                self.assertFalse(prohibited.intersection(command[1:]), (profile, command))

    def test_runner_uses_shell_false_minimal_environment_and_bounds(self):
        seen = {}
        original = live_audit.subprocess.run
        class Result:
            returncode = 0; stdout = "x" * 40000; stderr = "y" * 20000
        def runner(*args, **kwargs): seen.update(kwargs); return Result()
        live_audit.subprocess.run = runner
        try:
            result = live_audit.run_command(["/usr/bin/uname", "-a"])
        finally:
            live_audit.subprocess.run = original
        self.assertIs(seen["shell"], False)
        self.assertEqual(set(seen["env"]), {"PATH", "LC_ALL", "LANG"})
        self.assertEqual(len(result["stdout"]), 30000)
        self.assertEqual(len(result["stderr"]), 10000)


if __name__ == "__main__": unittest.main()
