"""Actionable system-health aggregation with honest coverage."""

from __future__ import annotations

from typing import Any

from .adapters import (
    boot_timing,
    critical_kernel_logs,
    device_health,
    docker_summary,
    failed_services,
    expected_services,
    package_health,
    registered_storage_assets,
)
from .command import CommandRunner
from .config import MaintenanceConfig
from .history import HistoryError, HistoryStore
from .models import Envelope, Finding, Severity, StructuredError
from .mounts import storage_overview
from .performance import performance_snapshot
from .policy import ProtectedResourcePolicy


def system_health(
    config: MaintenanceConfig,
    policy: ProtectedResourcePolicy,
    *,
    runner: CommandRunner | None = None,
    history: HistoryStore | None = None,
) -> dict[str, Any]:
    budget = config.budget("quick")
    runner = runner or CommandRunner(timeout_seconds=budget.command_timeout_seconds, max_output_bytes=budget.max_command_output_bytes)
    envelope = Envelope("system.health", scope={"profile": "quick"}, policy_fingerprint=policy.fingerprint)
    storage = storage_overview(policy, runner=runner)
    performance = performance_snapshot(policy)
    real_mounts = [item for item in storage.get("result", {}).get("mounts", []) if item.get("classification") in {"block", "removable", "device_mapper", "nbd"}]
    sources = [item.get("source", "") for item in real_mounts]
    adapters = {
        "failed_services": failed_services(config, runner),
        "expected_services": expected_services(config, runner),
        "boot_timing": boot_timing(config, runner),
        "critical_kernel_logs": critical_kernel_logs(config, runner),
        "device_health": device_health(config, runner, sources),
        "docker": docker_summary(config, runner),
        "package": package_health(config, runner),
        "registered_assets": registered_storage_assets(policy),
    }
    findings: list[dict[str, Any]] = []
    for mount in real_mounts:
        usage = mount.get("usage_percent")
        inode_usage = mount.get("inode_usage_percent")
        if mount.get("read_only"):
            findings.append(Finding(f"health:readonly:{mount.get('mount_id')}", "read_only_filesystem", f"Filesystem {mount.get('mount_point')} is read-only", Severity.CRITICAL, "confirmed").to_dict())
        if isinstance(usage, (int, float)) and usage >= config.thresholds["filesystem_critical_percent"]:
            findings.append(Finding(f"health:space-critical:{mount.get('mount_id')}", "filesystem_capacity", f"Filesystem {mount.get('mount_point')} is {usage}% used", Severity.CRITICAL, "confirmed").to_dict())
        elif isinstance(usage, (int, float)) and usage >= config.thresholds["filesystem_warning_percent"]:
            findings.append(Finding(f"health:space-warning:{mount.get('mount_id')}", "filesystem_capacity", f"Filesystem {mount.get('mount_point')} is {usage}% used", Severity.WARNING, "confirmed").to_dict())
        if isinstance(inode_usage, (int, float)) and inode_usage >= config.thresholds["inode_warning_percent"]:
            findings.append(Finding(f"health:inode-warning:{mount.get('mount_id')}", "inode_capacity", f"Filesystem {mount.get('mount_point')} inode use is {inode_usage}%", Severity.WARNING, "confirmed").to_dict())
    for expected in storage.get("result", {}).get("expected_mounts", []):
        if not expected.get("present"):
            findings.append(Finding(f"health:missing:{expected['resource_id']}", "missing_expected_mount", f"Expected mount {expected['path']} is missing", Severity.CRITICAL, "high").to_dict())
    failed = adapters["failed_services"].get("failed_services", [])
    if failed:
        findings.append(Finding("health:failed-services", "failed_services", f"{len(failed)} failed services were observed", Severity.WARNING, "high").to_dict())
    unhealthy_expected = [item for item in adapters["expected_services"].get("services", []) if item.get("status") == "warning"]
    for item in unhealthy_expected:
        findings.append(Finding(
            f"health:expected-service:{item['service']}",
            "expected_service",
            f"Expected service {item['service']} is not active",
            Severity.CRITICAL if item.get("critical") else Severity.WARNING,
            "high",
        ).to_dict())
    device_warnings = [item for item in adapters["device_health"].get("devices", []) if item.get("status") == "warning"]
    if device_warnings:
        findings.append(Finding("health:device-warning", "media_health", "A storage device reported a health warning", Severity.CRITICAL, "high").to_dict())
    metrics = performance.get("result", {}).get("metrics", {})
    if isinstance(metrics.get("memory_available_percent"), (int, float)) and metrics["memory_available_percent"] < config.thresholds["memory_available_warning_percent"]:
        findings.append(Finding("health:memory", "memory_pressure", "Available memory is below the configured warning threshold", Severity.WARNING, "high").to_dict())
    if isinstance(metrics.get("maximum_temperature_c"), (int, float)) and metrics["maximum_temperature_c"] >= config.thresholds["temperature_warning_c"]:
        findings.append(Finding("health:thermal", "thermal", "Temperature is above the configured warning threshold", Severity.WARNING, "high").to_dict())

    growth: dict[str, Any]
    try:
        growth = (history or HistoryStore(config.history_path)).latest_growth(
            str(config.snapshot_roots[0].resolve(strict=False)),
            anomaly_mad_multiplier=config.thresholds["anomaly_mad_multiplier"],
        )
    except HistoryError as exc:
        growth = {"comparable": False, "reason": str(exc)}

    coverage_areas = {
        "storage": storage.get("completeness", {}).get("status") != "failed",
        "performance": performance.get("completeness", {}).get("status") != "failed",
        "failed_services": adapters["failed_services"].get("status") == "observed",
        "expected_services": adapters["expected_services"].get("coverage") == "complete",
        "boot_timing": adapters["boot_timing"].get("coverage") == "complete",
        "critical_logs": adapters["critical_kernel_logs"].get("status") in {"observed", "partial"},
        "device_health": adapters["device_health"].get("coverage") == "complete",
        "docker": adapters["docker"].get("status") == "observed",
        "package": adapters["package"].get("status") in {"observed", "warning"},
        "backup_snapshot_registration": adapters["registered_assets"].get("status") == "observed",
        "recent_growth": bool(growth.get("comparable")),
    }
    unknown = [name for name, present in coverage_areas.items() if not present]
    coverage_percent = round(sum(coverage_areas.values()) / len(coverage_areas) * 100, 1)
    if any(item["severity"] == "critical" for item in findings):
        overall = "critical"
    elif findings or unknown:
        overall = "warning"
    else:
        overall = "healthy"
    if storage.get("completeness", {}).get("status") == "failed" or performance.get("completeness", {}).get("status") == "failed":
        overall = "unknown" if not findings else overall
    envelope.data["findings"] = findings
    envelope.data["result"] = {
        "overall_status": overall,
        "coverage_percent": coverage_percent,
        "unknown_areas": unknown,
        "coverage": coverage_areas,
        "storage": {
            "real_filesystems": real_mounts,
            "expected_mounts": storage.get("result", {}).get("expected_mounts", []),
            "collector_status": storage.get("completeness", {}).get("status"),
        },
        "performance": {
            "metrics": metrics,
            "inferences": performance.get("inferences", []),
            "coverage": performance.get("result", {}).get("coverage", {}),
        },
        "adapters": adapters,
        "recent_growth": growth,
        "thresholds": dict(config.thresholds),
    }
    for subresult, name in ((storage, "storage"), (performance, "performance")):
        if subresult.get("completeness", {}).get("status") != "complete":
            envelope.error(StructuredError(f"{name}_coverage_partial", f"{name} collector coverage is incomplete", operation=name))
    if unknown:
        envelope.limit("partial_health_coverage")
    return envelope.finish()
