import json
import time
import unittest
from pathlib import Path

from aag_agent.diagnostics import MAX_OBSERVATIONS, PROFILES, _requests, diagnose, diagnose_many
from aag_agent.observations import build_spec


class DiagnosticTests(unittest.TestCase):
    def test_realistic_fixture_matrix_is_bounded_and_read_only(self):
        scenarios = json.loads((Path(__file__).parent / "fixtures/diagnostics/scenarios.json").read_text())
        self.assertGreaterEqual(len(scenarios), 20)
        names = {item["name"] for item in scenarios}
        self.assertIn("inactive_successful_oneshot", names)
        self.assertIn("active_application_health_unknown", names)
        self.assertIn("wrong_source_at_mountpoint", names)
        for scenario in scenarios:
            calls = []
            def observer(domain, query, **kwargs):
                calls.append((domain, query, kwargs))
                status = scenario["statuses"].get(domain, "completed")
                return {"schema":"aag-observation-v1", "domain":domain, "target":query, "status":status, "facts":{"fixture":scenario["name"]} if status == "completed" else None, "normalization_error":None, "provenance":{"collector":"fixture", "binary":"/fixed", "argv":[]}, "read_only":True, "mutated":False}
            bundle = diagnose(scenario["profile"], scenario["inputs"], observer=observer)
            self.assertTrue(bundle["read_only"], scenario["name"])
            self.assertFalse(bundle["mutated"], scenario["name"])
            self.assertLessEqual(len(calls), MAX_OBSERVATIONS)
            self.assertNotIn("stdout", json.dumps(bundle))
            self.assertEqual({domain for domain, _, _ in calls}, set(scenario["statuses"]))

    def test_profile_input_schemas_are_strict(self):
        invalid = [
            ("evil", {}), ("general_system", {"command":"id"}),
            ("service", {"service":"x.service"}), ("storage_mount", {}),
            ("package", {"package":None}), ("network", {"interface":"eth0", "extra":1}),
        ]
        for profile, inputs in invalid:
            result = diagnose(profile, inputs, observer=lambda *a, **k: self.fail("collector called"))
            self.assertIn(result["status"], {"ERROR", "UNSUPPORTED"})
            self.assertFalse(result["mutated"])

    def test_collector_validation_blocks_injection_and_sensitive_paths(self):
        attacks = ["x;id", "x\nid", "x`id`", "x$(id)", "'\"", "\x00", "א" * 10000]
        for value in attacks:
            result = diagnose("service", {"service":value, "manager":"system"})
            self.assertEqual(result["status"], "INDETERMINATE")
            self.assertEqual(result["errors"][0]["code"], "invalid_input")
        for path in ("/home/aag-linux/.ssh", "/etc/shadow", "/proc/self/environ", "/mnt/data/../etc"):
            result = diagnose("storage_mount", {"path":path})
            self.assertEqual(result["status"], "INDETERMINATE")
            self.assertTrue(result["errors"])

    def test_partial_failure_distinguishes_unobservable_from_error(self):
        def observer(domain, query, **kwargs):
            status = "permission_denied" if domain == "memory" else "completed"
            return {"domain":domain, "status":status, "facts":{} if status == "completed" else None, "read_only":True, "mutated":False}
        bundle = diagnose("performance", {}, observer=observer)
        self.assertEqual(bundle["status"], "INDETERMINATE")
        self.assertEqual(bundle["facts"]["memory"]["state"], "UNOBSERVABLE")
        self.assertEqual(bundle["facts"]["uptime"]["state"], "OBSERVED")

    def test_total_timeout_stops_further_collection(self):
        calls = []
        def observer(domain, query, **kwargs):
            calls.append(domain); time.sleep(0.002)
            return {"domain":domain, "status":"completed", "facts":{}, "read_only":True, "mutated":False}
        bundle = diagnose("general_system", {}, observer=observer, max_total_seconds=0.001)
        self.assertLess(len(calls), 6)
        self.assertEqual(bundle["errors"][-1]["code"], "total_timeout")

    def test_only_small_curated_profile_set_exists(self):
        self.assertEqual(set(PROFILES), {"general_system", "performance", "service", "application_start", "network", "storage_mount", "docker", "package", "boot_health"})

    def test_every_profile_command_family_is_fixed_and_nonmutating(self):
        examples = {
            "general_system": {}, "performance": {},
            "service": {"service":"demo.service", "manager":"system"},
            "application_start": {"service":"demo.service", "manager":"user", "pid":1},
            "network": {"interface":"eth0"}, "storage_mount": {"path":"/mnt/data"},
            "docker": {"container":"demo"}, "package": {"package":"python3"}, "boot_health": {},
        }
        forbidden = {"restart", "start", "stop", "kill", "exec", "rm", "install", "remove", "upgrade"}
        for profile, inputs in examples.items():
            for domain, query in _requests(profile, inputs):
                spec = build_spec(domain, query)
                self.assertTrue(spec.binary.startswith("/"))
                self.assertTrue(forbidden.isdisjoint(spec.argv), (profile, spec))

    def test_multi_profile_global_limit_is_preflighted(self):
        calls = []
        result = diagnose_many(
            [{"profile":"general_system", "inputs":{}}, {"profile":"performance", "inputs":{}}],
            observer=lambda *args, **kwargs: calls.append(args),
        )
        self.assertEqual(result["errors"], [{"code":"global_observation_limit_exceeded"}])
        self.assertEqual(calls, [])


if __name__ == "__main__": unittest.main()
