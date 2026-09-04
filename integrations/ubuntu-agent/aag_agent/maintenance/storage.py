"""Storage snapshot, growth, and 'where did the space go?' analysis."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .adapters import docker_summary, registered_storage_assets
from .command import CommandRunner
from .config import MaintenanceConfig, ScanBudget
from .filesystem import ScanLimits, scan_tree
from .history import HistoryError, HistoryStore
from .models import Envelope, StructuredError
from .mounts import read_mountinfo, select_backing_mount
from .policy import PolicyError, ProtectedResourcePolicy


def storage_snapshot(
    path: str | Path,
    config: MaintenanceConfig,
    policy: ProtectedResourcePolicy,
    budget: ScanBudget,
    *,
    history: HistoryStore | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limits = ScanLimits.from_budget(budget, overrides)
    envelope = Envelope("storage.snapshot", scope={"path": str(path), "size_dimension": "allocated"}, policy_fingerprint=policy.fingerprint)
    try:
        outcome = scan_tree(path, policy, limits, scan_mode="summary")
    except (PolicyError, ValueError, RuntimeError) as exc:
        envelope.error(StructuredError(str(exc), "Snapshot scan rejected or unavailable", path=str(path), operation="snapshot_scan", recoverable=False))
        return envelope.finish(failed=True)
    envelope.data["completeness"]["entries_examined"] = outcome.entries_examined
    for error in outcome.errors:
        envelope.error(error)
    for limit in outcome.limits_reached:
        envelope.limit(limit)
    preliminary_status = "partial" if outcome.errors or outcome.limits_reached else "complete"
    scan_id = envelope.data["scan_id"]
    store = history or HistoryStore(config.history_path)
    try:
        snapshot_id = store.save(
            scan_id,
            outcome,
            config_fingerprint=config.fingerprint,
            policy_fingerprint=policy.fingerprint,
            completeness=preliminary_status,
            error_count=len(outcome.errors),
            retention=config.history_retention,
        )
    except HistoryError as exc:
        envelope.error(StructuredError(str(exc), "History snapshot could not be persisted", path=str(store.path), operation="history_write", recoverable=False))
        snapshot_id = None
    envelope.data["result"] = {
        "snapshot_id": snapshot_id,
        "history_written": snapshot_id is not None,
        "history_path": str(store.path),
        "root": str(outcome.root),
        "mount_identity": outcome.mount_identity,
        "size_dimension": "allocated",
        "totals": outcome.total.to_dict(),
        "children": [
            {"path": child, **aggregate.to_dict()}
            for child, aggregate in sorted(outcome.top.items(), key=lambda item: (-item[1].allocated_bytes, item[0]))
        ][:limits.result_limit],
        "scan_config_fingerprint": config.fingerprint,
        "protected_policy_fingerprint": policy.fingerprint,
        "completeness_at_write": preliminary_status,
        "host_resources_mutated": False,
    }
    return envelope.finish()


def storage_growth(
    path: str | Path,
    config: MaintenanceConfig,
    policy: ProtectedResourcePolicy,
    *,
    history: HistoryStore | None = None,
) -> dict[str, Any]:
    envelope = Envelope("storage.growth", scope={"path": str(path)}, policy_fingerprint=policy.fingerprint)
    try:
        canonical = policy.validate_scope(path)
        result = (history or HistoryStore(config.history_path)).latest_growth(
            str(canonical),
            anomaly_mad_multiplier=config.thresholds["anomaly_mad_multiplier"],
        )
    except (PolicyError, HistoryError) as exc:
        envelope.error(StructuredError(str(exc), "Growth history unavailable", path=str(path), operation="history_read", recoverable=False))
        return envelope.finish(failed=True)
    if not result.get("comparable"):
        envelope.error(StructuredError(result.get("reason", "incompatible_history"), "No compatible previous snapshot is available", path=str(path), operation="growth_compare"))
    envelope.data["result"] = result
    return envelope.finish()


def space_discrepancy(
    path: str | Path,
    policy: ProtectedResourcePolicy,
    budget: ScanBudget,
    *,
    config: MaintenanceConfig | None = None,
    runner: CommandRunner | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limits = ScanLimits.from_budget(budget, overrides)
    envelope = Envelope("storage.space_discrepancy", scope={"path": str(path)}, policy_fingerprint=policy.fingerprint)
    try:
        outcome = scan_tree(path, policy, limits, scan_mode="metadata")
        mount = select_backing_mount(outcome.root, read_mountinfo())
    except (PolicyError, ValueError, RuntimeError) as exc:
        envelope.error(StructuredError(str(exc), "Discrepancy scan rejected or unavailable", path=str(path), operation="discrepancy_scan", recoverable=False))
        return envelope.finish(failed=True)
    envelope.data["completeness"]["entries_examined"] = outcome.entries_examined
    for error in outcome.errors:
        envelope.error(error)
    for limit in outcome.limits_reached:
        envelope.limit(limit)
    checks: list[dict[str, Any]] = []
    try:
        usage = os.statvfs(outcome.root)
        fs_total = usage.f_blocks * usage.f_frsize
        fs_used = (usage.f_blocks - usage.f_bfree) * usage.f_frsize
        fs_available = usage.f_bavail * usage.f_frsize
        reserved = max(0, usage.f_bfree - usage.f_bavail) * usage.f_frsize
        scope_is_mount_root = mount is not None and Path(mount.mount_point).resolve(strict=False) == outcome.root
        difference = fs_used - outcome.total.allocated_bytes if scope_is_mount_root else None
        checks.append({
            "check": "df_vs_scanned_allocated",
            "filesystem_total_bytes": fs_total,
            "filesystem_used_bytes": fs_used,
            "filesystem_available_bytes": fs_available,
            "scanned_allocated_bytes": outcome.total.allocated_bytes,
            "difference_bytes": difference,
            "reserved_or_root_only_bytes": reserved,
            "comparable": scope_is_mount_root and not outcome.errors and not outcome.limits_reached,
            "reason": None if scope_is_mount_root else "scope_is_not_mount_root",
        })
    except OSError as exc:
        envelope.error(StructuredError("statvfs_failed", str(exc), path=str(path), operation="statvfs"))
    runner = runner or CommandRunner(
        timeout_seconds=budget.command_timeout_seconds,
        max_output_bytes=budget.max_command_output_bytes,
    )
    lsof = runner.run("lsof_deleted")
    if lsof.status in {"completed", "nonzero_exit"}:
        deleted_count = 0
        deleted_bytes = 0
        current_size = 0
        for token in lsof.stdout.split("\x00"):
            if token.startswith("s") and token[1:].isdigit():
                current_size = int(token[1:])
            elif token.startswith("n"):
                deleted_count += 1
                deleted_bytes += current_size
                current_size = 0
        deleted_check = {
            "check": "deleted_open_files",
            "status": "observed",
            "file_count": deleted_count,
            "reported_size_bytes": deleted_bytes,
            "paths_withheld": True,
            "provenance": lsof.provenance(),
        }
    else:
        deleted_check = {
            "check": "deleted_open_files",
            "status": "unavailable",
            "reason": lsof.status,
            "provenance": lsof.provenance(),
        }
    checks.extend([
        {
            "check": "sparse_files",
            "status": "observed",
            "logical_minus_allocated_bytes": max(0, outcome.total.logical_bytes - outcome.total.allocated_bytes),
            "explanation": "Sparse allocation can make apparent size differ from physical use.",
        },
        {
            "check": "hardlinks",
            "status": "observed",
            "entries_not_double_counted": outcome.hardlink_entries_skipped,
            "allocated_bytes_not_double_counted": outcome.hardlink_allocated_bytes_skipped,
        },
        {
            "check": "nested_filesystems",
            "status": "observed",
            "mount_boundaries_skipped": sorted(outcome.mount_boundaries_skipped),
        },
        {
            "check": "inaccessible_or_incomplete",
            "status": "incomplete" if outcome.errors or outcome.limits_reached else "complete",
            "error_count": len(outcome.errors),
            "limits_reached": list(outcome.limits_reached),
        },
        deleted_check,
        {
            "check": "snapshots_docker_vm_reserved_metadata",
            "status": "requires_dedicated_adapters_or_review",
            "reason": "directory totals alone cannot prove these causes",
        },
    ])
    if config is not None:
        checks.append({
            "check": "docker_storage",
            "status": "observed_via_adapter",
            "result": docker_summary(config, runner),
        })
        checks.append({
            "check": "registered_vm_snapshot_backup_assets",
            "status": "observed_via_policy",
            "result": registered_storage_assets(policy),
        })
    causes: list[dict[str, Any]] = []
    if outcome.errors or outcome.limits_reached:
        causes.append({"cause": "incomplete_scan_coverage", "confidence": "confirmed"})
    if outcome.mount_boundaries_skipped:
        causes.append({"cause": "separate_nested_filesystems", "confidence": "confirmed"})
    if outcome.total.logical_bytes > outcome.total.allocated_bytes:
        causes.append({"cause": "sparse_or_block_allocation_difference", "confidence": "high"})
    envelope.data["result"] = {
        "root": str(outcome.root),
        "mount_identity": outcome.mount_identity,
        "checks": checks,
        "possible_causes": causes,
        "conclusion": "insufficient_coverage_for_exact_cause" if outcome.errors or outcome.limits_reached else "bounded_checks_complete_no_single_cause_assumed",
    }
    return envelope.finish()
