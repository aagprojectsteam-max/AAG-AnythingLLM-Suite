from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_agent.maintenance.adapters import device_health
from aag_agent.maintenance.command import CommandResult
from aag_agent.maintenance.health import system_health
from aag_agent.maintenance.performance import performance_snapshot
from tests.maintenance_helpers import make_policy


def command_result(stdout: str, *, status: str = "completed", returncode: int = 0) -> CommandResult:
    return CommandResult("fixture", "/usr/bin/fixture", ("/usr/bin/fixture",), status, returncode, stdout, "", False, False, "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", 1.0, 1.0)


class FakeRunner:
    def __init__(self, results): self.results = results
    def run(self, name, parameters=None): return self.results[name]


class PerformanceAndHealthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.proc = self.root / "proc"; self.sys = self.root / "sys"
        self.proc.mkdir(); self.sys.mkdir()
        self.config, self.policy = make_policy(self.root, registry={"schema":"aag-component-registry-v1","components":[]})

    def tearDown(self): self.temporary.cleanup()

    def _write_sample(self, *, second=False):
        cpu = "cpu  200 0 200 900 100 0 0 0 0 0\n" if second else "cpu  100 0 100 800 0 0 0 0 0 0\n"
        (self.proc / "stat").write_text(cpu, encoding="utf-8")
        (self.proc / "loadavg").write_text("4.00 2.00 1.00 1/100 1\n", encoding="utf-8")
        (self.proc / "meminfo").write_text("MemTotal: 100000 kB\nMemAvailable: 5000 kB\nSwapTotal: 10000 kB\nSwapFree: 2000 kB\n", encoding="utf-8")
        (self.proc / "vmstat").write_text(f"pswpin {10 if second else 0}\npswpout {5 if second else 0}\npgmajfault {3 if second else 0}\n", encoding="utf-8")
        (self.proc / "diskstats").write_text(f"8 0 sda 1 0 {30 if second else 10} 0 1 0 {60 if second else 20} 0 0 {20 if second else 1} 0\n", encoding="utf-8")
        pressure = self.proc / "pressure"; pressure.mkdir(exist_ok=True)
        (pressure / "cpu").write_text("some avg10=1.00 avg60=0.50 avg300=0.10 total=1\n", encoding="utf-8")
        (pressure / "memory").write_text("some avg10=2.00 avg60=1.00 avg300=0.20 total=2\nfull avg10=0.10 avg60=0.10 avg300=0.10 total=1\n", encoding="utf-8")
        (pressure / "io").write_text("some avg10=10.00 avg60=5.00 avg300=1.00 total=3\nfull avg10=1.00 avg60=1.00 avg300=0.50 total=1\n", encoding="utf-8")
        process = self.proc / "123"; process.mkdir(exist_ok=True)
        fields = ["S"] + ["0"] * 40
        fields[11] = "30" if second else "10"; fields[12] = "20" if second else "10"
        (process / "stat").write_text("123 (writer process) " + " ".join(fields), encoding="utf-8")
        (process / "status").write_text("VmRSS:\t2048 kB\n", encoding="utf-8")
        (process / "io").write_text(f"read_bytes: {200 if second else 100}\nwrite_bytes: {2000 if second else 100}\n", encoding="utf-8")
        (process / "cgroup").write_text("0::/system.slice/example.service\n", encoding="utf-8")

    def _write_sys(self):
        zone = self.sys / "class/thermal/thermal_zone0"; zone.mkdir(parents=True)
        (zone / "temp").write_text("55000\n", encoding="utf-8"); (zone / "type").write_text("cpu\n", encoding="utf-8")
        battery = self.sys / "class/power_supply/BAT0"; battery.mkdir(parents=True)
        (battery / "type").write_text("Battery\n", encoding="utf-8"); (battery / "capacity").write_text("80\n", encoding="utf-8")
        (battery / "energy_full").write_text("800\n", encoding="utf-8"); (battery / "energy_full_design").write_text("1000\n", encoding="utf-8")
        card = self.sys / "class/drm/card0/device"; card.mkdir(parents=True)
        (card / "vendor").write_text("0x1002\n", encoding="utf-8"); (card / "device").write_text("0x0001\n", encoding="utf-8")

    def test_correlated_cpu_memory_swap_io_pressure_and_process_snapshot(self):
        self._write_sample(second=False); self._write_sys()
        result = performance_snapshot(self.policy, proc_root=self.proc, sys_root=self.sys, sample_seconds=0.1, sleep=lambda _: self._write_sample(second=True))
        metrics = result["result"]["metrics"]
        self.assertGreater(metrics["io_wait_percent"], 10)
        self.assertLess(metrics["memory_available_percent"], 10)
        self.assertEqual(metrics["swap_in_delta"], 10)
        self.assertEqual(result["result"]["top_processes"]["io"][0]["name"], "writer process")
        self.assertEqual(result["inferences"][0]["inference_id"], "inference:io-contention")
        self.assertTrue(result["result"]["thermal"]); self.assertTrue(result["result"]["battery"]); self.assertTrue(result["result"]["gpu"])

    def test_tool_unavailable_yields_partial_coverage_not_false_health(self):
        self._write_sample(second=False)
        result = performance_snapshot(self.policy, proc_root=self.proc, sys_root=self.sys, sample_seconds=0.1, sleep=lambda _: self._write_sample(second=True))
        self.assertEqual(result["completeness"]["status"], "partial")
        self.assertIn("battery", result["result"]["coverage"]["unknown_areas"])

    def test_smart_healthy_and_warning(self):
        healthy = FakeRunner({"smart_health": command_result(json.dumps({"smart_status":{"passed":True}}))})
        warning = FakeRunner({"smart_health": command_result(json.dumps({"smart_status":{"passed":False}}), status="nonzero_exit", returncode=8)})
        self.assertEqual(device_health(self.config, healthy, ["/dev/sda1"])["devices"][0]["status"], "healthy")
        self.assertEqual(device_health(self.config, warning, ["/dev/sda1"])["devices"][0]["status"], "warning")

    def test_health_reports_readonly_missing_mount_failed_service_and_partial(self):
        storage = {
            "completeness":{"status":"complete"},
            "result":{"mounts":[{"mount_id":1,"mount_point":"/mnt/data","source":"/dev/sda1","classification":"block","read_only":True,"usage_percent":50,"inode_usage_percent":1}],"expected_mounts":[{"resource_id":"data","path":"/mnt/data","present":False}]}
        }
        performance = {"completeness":{"status":"partial"},"result":{"metrics":{},"coverage":{}},"inferences":[]}
        adapter = {"status":"observed","coverage":"complete"}
        with patch("aag_agent.maintenance.health.storage_overview", return_value=storage), patch("aag_agent.maintenance.health.performance_snapshot", return_value=performance), patch("aag_agent.maintenance.health.failed_services", return_value={**adapter,"failed_services":[{"unit":"bad.service"}]}), patch("aag_agent.maintenance.health.expected_services", return_value={**adapter,"services":[]}), patch("aag_agent.maintenance.health.boot_timing", return_value={**adapter,"boot_duration_seconds":1.0}), patch("aag_agent.maintenance.health.critical_kernel_logs", return_value={**adapter,"events":[]}), patch("aag_agent.maintenance.health.device_health", return_value={**adapter,"devices":[]}), patch("aag_agent.maintenance.health.docker_summary", return_value=adapter), patch("aag_agent.maintenance.health.package_health", return_value=adapter), patch("aag_agent.maintenance.health.registered_storage_assets", return_value=adapter):
            result = system_health(self.config, self.policy, runner=object())
        self.assertEqual(result["result"]["overall_status"], "critical")
        categories = {item["category"] for item in result["findings"]}
        self.assertIn("read_only_filesystem", categories); self.assertIn("missing_expected_mount", categories); self.assertIn("failed_services", categories)
        self.assertNotEqual(result["result"]["overall_status"], "healthy")


if __name__ == "__main__": unittest.main()
