"""Optional read-only adapters for machine-specific and health coverage."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .command import CommandResult, CommandRunner
from .config import MaintenanceConfig
from .policy import ProtectedResourcePolicy


def _safe_text(value: Any, maximum: int = 500) -> str:
    rendered: list[str] = []
    for character in str(value)[:maximum]:
        if unicodedata.category(character).startswith("C") or character == "\x1b":
            rendered.append(f"\\u{ord(character):04x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _adapter_error(name: str, result: CommandResult) -> dict[str, Any]:
    return {
        "adapter": name,
        "status": "unavailable",
        "reason": result.status,
        "coverage": "partial",
        "provenance": result.provenance(),
    }


def docker_summary(config: MaintenanceConfig, runner: CommandRunner) -> dict[str, Any]:
    if not config.adapters["docker"]:
        return {"adapter": "docker", "status": "disabled", "coverage": "not_requested"}
    result = runner.run("docker_df")
    if result.status != "completed":
        return _adapter_error("docker", result)
    rows: list[dict[str, Any]] = []
    try:
        for line in result.stdout.splitlines():
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise TypeError("docker_row_not_object")
                rows.append({
                    key: item.get(key)
                    for key in ("Type", "TotalCount", "Active", "Size", "Reclaimable")
                    if key in item
                })
    except (json.JSONDecodeError, TypeError):
        return {"adapter": "docker", "status": "malformed_output", "coverage": "partial", "provenance": result.provenance()}
    return {
        "adapter": "docker",
        "status": "observed",
        "coverage": "complete",
        "categories": rows,
        "limitations": ["docker_reported_reclaimable_is_not_cleanup_authority", "volumes_are_review_required", "docker_socket_access_is_privileged_boundary"],
        "provenance": result.provenance(),
    }


def failed_services(config: MaintenanceConfig, runner: CommandRunner) -> dict[str, Any]:
    if not config.adapters["systemd"]:
        return {"adapter": "systemd_failed", "status": "disabled", "coverage": "not_requested"}
    result = runner.run("systemd_failed")
    if result.status != "completed":
        return _adapter_error("systemd_failed", result)
    services: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service") and len(parts) >= 4:
            services.append({"unit": parts[0], "load": parts[1], "active": parts[2], "sub": parts[3]})
    return {"adapter": "systemd_failed", "status": "observed", "coverage": "complete", "failed_services": services[:100], "truncated": len(services) > 100, "provenance": result.provenance()}


def expected_services(config: MaintenanceConfig, runner: CommandRunner) -> dict[str, Any]:
    services: list[dict[str, Any]] = []
    for expected in config.expected_services:
        result = runner.run("systemd_service", {"service": expected["service"], "manager": expected["manager"]})
        item: dict[str, Any] = {**expected, "provenance": result.provenance()}
        if result.status != "completed":
            item.update({"status": "unavailable", "reason": result.status})
        else:
            fields = {}
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key in {"LoadState", "ActiveState", "SubState", "NRestarts"}:
                        fields[key] = value
            item.update({
                "status": "healthy" if fields.get("LoadState") == "loaded" and fields.get("ActiveState") == "active" else "warning",
                "load_state": fields.get("LoadState"),
                "active_state": fields.get("ActiveState"),
                "sub_state": fields.get("SubState"),
                "restart_count": int(fields["NRestarts"]) if fields.get("NRestarts", "").isdigit() else None,
            })
        services.append(item)
    return {
        "adapter": "expected_services",
        "status": "observed" if all(item["status"] != "unavailable" for item in services) else "partial",
        "coverage": "complete" if all(item["status"] != "unavailable" for item in services) else "partial",
        "services": services,
    }


def _duration_seconds(value: str) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|min)", value.strip())
    if not match:
        return None
    number = float(match.group(1))
    return number / 1000 if match.group(2) == "ms" else (number * 60 if match.group(2) == "min" else number)


def boot_timing(config: MaintenanceConfig, runner: CommandRunner) -> dict[str, Any]:
    if not config.adapters["systemd"]:
        return {"adapter": "boot_timing", "status": "disabled", "coverage": "not_requested"}
    result = runner.run("systemd_boot_time")
    if result.status != "completed":
        return _adapter_error("boot_timing", result)
    total: float | None = None
    if "=" in result.stdout:
        after = result.stdout.split("=", 1)[1].strip().split()[0]
        total = _duration_seconds(after)
    return {
        "adapter": "boot_timing",
        "status": "observed" if total is not None else "partial",
        "coverage": "complete" if total is not None else "partial",
        "boot_duration_seconds": total,
        "provenance": result.provenance(),
    }


def critical_kernel_logs(config: MaintenanceConfig, runner: CommandRunner) -> dict[str, Any]:
    if not config.adapters["journal"]:
        return {"adapter": "journal_critical", "status": "disabled", "coverage": "not_requested"}
    result = runner.run("journal_critical")
    if result.status != "completed":
        return _adapter_error("journal_critical", result)
    events: list[dict[str, Any]] = []
    malformed = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            events.append({
                "priority": item.get("PRIORITY"),
                "transport": item.get("_TRANSPORT"),
                "timestamp_us": item.get("__REALTIME_TIMESTAMP"),
                "message": _safe_text(item.get("MESSAGE", "")),
            })
        except (json.JSONDecodeError, AttributeError):
            malformed += 1
    return {
        "adapter": "journal_critical",
        "status": "observed" if not malformed else "partial",
        "coverage": "complete" if not malformed else "partial",
        "events": events[:100],
        "malformed_records": malformed,
        "provenance": result.provenance(),
    }


def package_health(config: MaintenanceConfig, runner: CommandRunner) -> dict[str, Any]:
    if not config.adapters["package"]:
        return {"adapter": "package", "status": "disabled", "coverage": "not_requested"}
    result = runner.run("dpkg_audit")
    if result.status not in {"completed", "nonzero_exit"}:
        return _adapter_error("package", result)
    audit_lines = [_safe_text(line) for line in result.stdout.splitlines() if line.strip()][:100]
    apt_lists = Path("/var/lib/apt/lists")
    metadata_mtime: float | None = None
    try:
        metadata_mtime = max((entry.stat().st_mtime for entry in apt_lists.iterdir() if entry.is_file()), default=None)
    except OSError:
        pass
    return {
        "adapter": "package",
        "status": "warning" if audit_lines or result.returncode else "observed",
        "coverage": "partial",
        "dpkg_audit_issues": audit_lines,
        "local_update_metadata_age_seconds": round(time.time() - metadata_mtime, 1) if metadata_mtime else None,
        "pending_update_count": "unknown",
        "limitations": ["network_was_not_used", "pending_updates_not_simulated"],
        "provenance": result.provenance(),
    }


def device_health(
    config: MaintenanceConfig,
    runner: CommandRunner,
    sources: Iterable[str],
) -> dict[str, Any]:
    devices: list[str] = []
    for source in sources:
        if re.fullmatch(r"/dev/sd[a-z]+\d+", source):
            source = re.sub(r"\d+$", "", source)
        elif re.fullmatch(r"/dev/nvme\d+n\d+p\d+", source):
            source = re.sub(r"p\d+$", "", source)
        if re.fullmatch(r"/dev/(?:sd[a-z]+|nvme\d+n\d+|vd[a-z]+)", source):
            devices.append(source)
    devices = sorted(set(devices))
    results: list[dict[str, Any]] = []
    for device in devices[:16]:
        command = "nvme_health" if device.startswith("/dev/nvme") and config.adapters["nvme"] else "smart_health"
        if command == "smart_health" and not config.adapters["smart"]:
            continue
        result = runner.run(command, {"device": device})
        item: dict[str, Any] = {"device": device, "adapter": command, "provenance": result.provenance()}
        if result.status in {"completed", "nonzero_exit"} and result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
                if command == "smart_health":
                    passed = (parsed.get("smart_status") or {}).get("passed")
                    item.update({"status": "healthy" if passed is True else ("warning" if passed is False else "unknown"), "smart_passed": passed})
                else:
                    warning = parsed.get("critical_warning")
                    item.update({"status": "healthy" if warning in {0, "0"} else ("warning" if warning is not None else "unknown"), "critical_warning": warning})
            except (json.JSONDecodeError, AttributeError):
                item.update({"status": "malformed_output"})
        else:
            item.update({"status": "unavailable", "reason": result.status})
        results.append(item)
    return {
        "adapter": "device_health",
        "status": "observed" if results else "unavailable",
        "coverage": "partial" if any(item["status"] in {"unavailable", "unknown", "malformed_output"} for item in results) or not results else "complete",
        "devices": results,
    }


def registered_storage_assets(policy: ProtectedResourcePolicy) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    for rule in policy.rules:
        if rule.protection_class not in {"critical", "protected", "review_required", "generated_output"}:
            continue
        try:
            st = rule.path.lstat()
            present = True
            allocated = int(getattr(st, "st_blocks", 0)) * 512
        except FileNotFoundError:
            present = False
            allocated = None
        except OSError:
            present = None
            allocated = None
        assets.append({
            "resource_id": rule.resource_id,
            "path": str(rule.path),
            "component": rule.component,
            "protection_class": rule.protection_class,
            "present": present,
            "root_entry_allocated_bytes": allocated,
            "latest_verification": "unknown",
            "retention_policy": "unknown",
            "content_traversed": False,
        })
    return {"adapter": "registered_assets", "status": "observed", "coverage": "partial", "assets": assets, "limitations": ["presence_does_not_prove_backup_validity", "no_recursive_traversal"]}
