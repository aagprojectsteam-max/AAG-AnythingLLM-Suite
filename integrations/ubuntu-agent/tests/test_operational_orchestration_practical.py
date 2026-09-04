from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_agent.orchestration.practical import PracticalWorkflowError, PracticalWorkflows
from tests.investigation_helpers import InvestigationHarness


def envelope(tool, result, status="complete"):
    return {
        "schema": "aag-maintenance-scan-envelope-v1",
        "schema_version": "1.0",
        "tool": tool,
        "completeness": {"status": status, "errors": 0, "limits_reached": []},
        "result": result,
        "read_only": True,
        "mutated": False,
    }


class FakeMaintenance:
    def __init__(self, metrics=None):
        self.calls = []
        self.metrics = list(metrics or [
            {"cpu_utilization_percent": 90, "memory_available_percent": 50, "swap_used_percent": 2, "io_wait_percent": 1, "maximum_temperature_c": 80},
            {"cpu_utilization_percent": 91, "memory_available_percent": 48, "swap_used_percent": 2, "io_wait_percent": 1, "maximum_temperature_c": 80},
            {"cpu_utilization_percent": 20, "memory_available_percent": 49, "swap_used_percent": 2, "io_wait_percent": 1, "maximum_temperature_c": 80},
        ])

    def __call__(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        if tool == "storage.overview":
            return envelope(tool, {"mounts": [{"mount_point": "/mnt/data", "usage_percent": 44, "read_only": False}]})
        if tool == "storage.top":
            return envelope(tool, {"top": [{"path": "/mnt/data/AI", "logical_bytes": 100, "allocated_bytes": 80}], "exclusions_skipped": ["/mnt/data/WinBoat-Assets"], "mount_boundaries_skipped": [], "hardlinks": {"duplicate_entries_not_counted": 1}})
        if tool == "storage.largest_files":
            return envelope(tool, {"largest_files": [{"path": "/mnt/data/AI/file.bin", "logical_bytes": 100, "allocated_bytes": 80}], "exclusions_skipped": ["/mnt/data/WinBoat-Assets"], "mount_boundaries_skipped": []})
        if tool == "performance.snapshot":
            return envelope(tool, {"metrics": self.metrics.pop(0)})
        raise AssertionError(tool)


class OperationalPracticalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.harness = InvestigationHarness(Path(self.directory.name))

    def workflows(self, maintenance=None):
        maturity = {
            "status": "PASS_WITH_EXPLICIT_BOUNDARIES",
            "execution_authority": "NONE",
            "checks": {
                name: {"status": "PASS"}
                for name in (
                    "release_manifest", "immutable_stage_manifests", "context_database",
                    "investigation_database", "remediation_database", "host_mutation_audit",
                    "operation_registry", "investigation_registry", "installed_skills", "live_bridge",
                )
            },
        }
        return PracticalWorkflows(
            self.harness.context,
            maintenance_dispatch=maintenance or FakeMaintenance(),
            maturity_runner=lambda: maturity,
            anythingllm_health=lambda: {"status": "PASS", "online": True, "read_only": True},
            sleeper=lambda _seconds: None,
        )

    def test_self_health_is_evidence_bound_and_nonmutating(self):
        with patch("aag_agent.orchestration.practical._sqlite_integrity", return_value={"status": "PASS", "quick_check": "ok", "foreign_key_violations": 0, "schema_version": 2}), patch("aag_agent.orchestration.practical._remediation_activity", return_value={"status": "PASS", "pending_approvals": 0, "active_attempts": 0}):
            result = self.workflows().self_health()
        self.assertEqual(result["status"], "HEALTHY")
        self.assertEqual(result["pending_approvals"], 0)
        self.assertEqual(result["active_remediation_attempts"], 0)
        self.assertTrue(self.harness.context.store.evidence_exists(result["evidence_ids"]))
        self.assertEqual(result["execution_authority"], "NONE")

    def test_self_health_distinguishes_integrity_failure_and_partial_coverage(self):
        workflows = self.workflows()
        workflows.maturity_runner = lambda: {"status": "FAIL", "execution_authority": "NONE", "checks": {"release_manifest": {"status": "FAIL"}}}
        with patch("aag_agent.orchestration.practical._sqlite_integrity", return_value={"status": "PASS"}), patch("aag_agent.orchestration.practical._remediation_activity", return_value={"status": "PASS", "pending_approvals": 0, "active_attempts": 0}):
            self.assertEqual(workflows.self_health()["status"], "INTEGRITY_FAILURE")

    def test_storage_consumers_reuses_maintenance_with_fixed_trusted_root(self):
        maintenance = FakeMaintenance()
        result = self.workflows(maintenance).storage_consumers()
        self.assertEqual(result["trusted_root"], "/mnt/data")
        self.assertEqual([call[0] for call in maintenance.calls], ["storage.overview", "storage.top", "storage.largest_files"])
        for tool, arguments in maintenance.calls[1:]:
            self.assertEqual(arguments["path"], "/mnt/data")
            self.assertLessEqual(arguments["limits"]["max_duration_seconds"], 3)
        self.assertTrue(result["scan_coverage"]["hardlinks_not_double_counted"])
        self.assertIn("/mnt/data/WinBoat-Assets", result["scan_coverage"]["excluded_paths"])
        self.assertFalse(result["cleanup_executed"])
        self.assertEqual(result["commands"], [])
        self.assertTrue(self.harness.context.store.evidence_exists(result["evidence_ids"]))

    def test_protected_resources_never_gain_deletion_authority(self):
        result = self.workflows().storage_protection()
        classes = {item["resource_id"]: item["cleanup_classification"] for item in result["resources"]}
        self.assertEqual(classes["winboat-assets"], "PROTECTED")
        self.assertEqual(classes["usb-clone-assets"], "PROTECTED")
        self.assertEqual(result["deletion_authority"], "NONE")

    def test_sustained_performance_requires_repetition_and_never_claims_root_cause(self):
        maintenance = FakeMaintenance()
        result = self.workflows(maintenance).sustained_performance()
        cpu = next(item for item in result["hypothesis_evaluations"] if item["hypothesis"] == "cpu_pressure")
        self.assertEqual(cpu["classification"], "SUPPORTED_CONTRIBUTOR")
        self.assertEqual(cpu["matched_samples"], 2)
        self.assertFalse(cpu["verified_root_cause"])
        self.assertFalse(result["causal_language"]["verified_root_cause"])
        self.assertEqual(len(result["samples"]), 3)
        self.assertEqual(result["limits"]["maximum_samples"], 3)

    def test_one_spike_is_not_supported_and_unknown_input_fails_closed(self):
        metrics = [
            {"cpu_utilization_percent": 99},
            {"cpu_utilization_percent": 20},
            {"cpu_utilization_percent": 21},
        ]
        result = self.workflows(FakeMaintenance(metrics)).sustained_performance()
        cpu = next(item for item in result["hypothesis_evaluations"] if item["hypothesis"] == "cpu_pressure")
        self.assertEqual(cpu["classification"], "ONE_TIME_SPIKE")
        broken = lambda _tool, _args: {"schema": "wrong"}
        with self.assertRaises(PracticalWorkflowError):
            self.workflows(broken).storage_consumers()


if __name__ == "__main__":
    unittest.main()
