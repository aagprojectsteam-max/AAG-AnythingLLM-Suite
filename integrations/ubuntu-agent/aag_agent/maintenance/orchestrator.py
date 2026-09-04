"""Strict typed dispatch for all Maintenance Intelligence V1 tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .command import CommandRunner
from .adapters import critical_kernel_logs, failed_services
from .config import MaintenanceConfig, load_config
from .duplicates import duplicate_analysis
from .filesystem import ScanLimits, scan_envelope
from .health import system_health
from .history import HistoryError, HistoryStore
from .models import Envelope, StructuredError
from .mounts import storage_overview
from .performance import performance_snapshot
from .policy import PolicyError, ProtectedResourcePolicy
from .recommendations import explain_plan_item, maintenance_plan
from .render import render_hebrew
from .storage import space_discrepancy, storage_growth, storage_snapshot

MAINTENANCE_TOOLS = {
    "system.health",
    "performance.snapshot",
    "storage.overview",
    "storage.top",
    "storage.inspect",
    "storage.largest_files",
    "storage.snapshot",
    "storage.growth",
    "storage.duplicate_candidates",
    "storage.duplicate_verify",
    "storage.space_discrepancy",
    "maintenance.plan",
    "maintenance.explain",
}

TOOL_CATALOG: dict[str, dict[str, Any]] = {
    name: {
        "risk": "R0",
        "side_effect": "durable_summary_history_write" if name in {"storage.snapshot", "performance.snapshot", "system.health"} else "none",
        "host_mutation": False,
        "cleanup_execution": False,
    }
    for name in MAINTENANCE_TOOLS
}

PATH_TOOLS = {
    "storage.top", "storage.inspect", "storage.largest_files", "storage.snapshot",
    "storage.growth", "storage.duplicate_candidates", "storage.duplicate_verify",
    "storage.space_discrepancy", "maintenance.plan", "maintenance.explain",
}
LIMIT_TOOLS = {
    "storage.top", "storage.inspect", "storage.largest_files", "storage.snapshot",
    "storage.duplicate_candidates", "storage.duplicate_verify",
    "storage.space_discrepancy", "maintenance.plan",
}


@dataclass(frozen=True)
class MaintenanceContext:
    config: MaintenanceConfig
    policy: ProtectedResourcePolicy
    history: HistoryStore


def default_context() -> MaintenanceContext:
    config = load_config()
    policy = ProtectedResourcePolicy(config)
    return MaintenanceContext(config, policy, HistoryStore(config.history_path))


def _validate(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if tool not in MAINTENANCE_TOOLS:
        raise ValueError("maintenance_tool_not_allowlisted")
    if not isinstance(arguments, Mapping):
        raise ValueError("maintenance_arguments_must_be_object")
    allowed: set[str] = set()
    required: set[str] = set()
    if tool in PATH_TOOLS:
        allowed.add("path")
        required.add("path")
    if tool in LIMIT_TOOLS:
        allowed.update({"profile", "limits"})
    if tool == "maintenance.explain":
        allowed.add("item_id")
        required.add("item_id")
    if set(arguments) - allowed or required - set(arguments):
        raise ValueError("invalid_maintenance_argument_schema")
    validated = dict(arguments)
    if "path" in validated and (not isinstance(validated["path"], str) or not validated["path"]):
        raise ValueError("invalid_maintenance_path")
    if "profile" in validated:
        if validated["profile"] not in {"quick", "standard", "deep"}:
            raise ValueError("invalid_maintenance_profile")
    else:
        validated["profile"] = "standard"
    if "limits" in validated and not isinstance(validated["limits"], dict):
        raise ValueError("invalid_maintenance_limits")
    if "item_id" in validated and (not isinstance(validated["item_id"], str) or not validated["item_id"].startswith("maint-v1-")):
        raise ValueError("invalid_maintenance_item_id")
    return validated


def _attach_public_metadata(result: dict[str, Any], tool: str) -> dict[str, Any]:
    result["tool_contract"] = {"tool": tool, **TOOL_CATALOG[tool]}
    result["hebrew"] = render_hebrew(result)
    return result


def dispatch(
    tool: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    context: MaintenanceContext | None = None,
) -> dict[str, Any]:
    try:
        args = _validate(tool, arguments or {})
        ctx = context or default_context()
    except (ValueError, PolicyError) as exc:
        envelope = Envelope(tool if isinstance(tool, str) else "invalid", scope={}, policy_fingerprint="unknown")
        envelope.error(StructuredError(str(exc), "Maintenance request rejected", operation="dispatch", recoverable=False))
        result = envelope.finish(failed=True)
        result["hebrew"] = render_hebrew(result)
        return result
    budget = ctx.config.budget(args.get("profile", "standard"))
    runner = CommandRunner(timeout_seconds=budget.command_timeout_seconds, max_output_bytes=budget.max_command_output_bytes)
    limits = args.get("limits")
    if tool in LIMIT_TOOLS:
        try:
            ScanLimits.from_budget(budget, limits)
        except ValueError as exc:
            envelope = Envelope(
                tool,
                scope={"path": args.get("path")},
                policy_fingerprint=ctx.policy.fingerprint,
            )
            envelope.error(
                StructuredError(
                    str(exc),
                    "Requested scan limits are invalid or exceed the selected profile",
                    path=args.get("path"),
                    operation="dispatch_budget_validation",
                    recoverable=False,
                )
            )
            return _attach_public_metadata(envelope.finish(failed=True), tool)
    if tool == "system.health":
        result = system_health(ctx.config, ctx.policy, runner=runner, history=ctx.history)
        health_result = result.get("result", {})
        metrics = health_result.get("performance", {}).get("metrics", {})
        numeric = dict(metrics)
        numeric.update({
            "coverage_percent": health_result.get("coverage_percent"),
            "failed_services": len(health_result.get("adapters", {}).get("failed_services", {}).get("failed_services", [])),
            "unhealthy_expected_services": sum(1 for item in health_result.get("adapters", {}).get("expected_services", {}).get("services", []) if item.get("status") == "warning"),
            "boot_duration_seconds": health_result.get("adapters", {}).get("boot_timing", {}).get("boot_duration_seconds"),
            "docker_categories": len(health_result.get("adapters", {}).get("docker", {}).get("categories", [])),
        })
        real_filesystems = health_result.get("storage", {}).get("real_filesystems", [])
        available_values = [item.get("available_bytes") for item in real_filesystems if isinstance(item.get("available_bytes"), (int, float))]
        if available_values:
            numeric["minimum_filesystem_available_bytes"] = min(available_values)
        try:
            metric_id = ctx.history.save_metrics("health", numeric, config_fingerprint=ctx.config.fingerprint, completeness=result.get("completeness", {}).get("status", "failed"))
            health_result["history_written"] = True
            health_result["metric_history_id"] = metric_id
            health_result["baseline"] = ctx.history.metric_baseline("health", config_fingerprint=ctx.config.fingerprint)
        except HistoryError as exc:
            result["errors"].append(StructuredError(str(exc), "Health baseline could not be saved", operation="metric_history").to_dict())
            result["completeness"]["errors"] += 1
            result["completeness"]["status"] = "partial"
            health_result["history_written"] = False
    elif tool == "performance.snapshot":
        result = performance_snapshot(ctx.policy)
        optional = {
            "failed_services": failed_services(ctx.config, runner),
            "critical_kernel_logs": critical_kernel_logs(ctx.config, runner),
        }
        result["result"]["optional_adapters"] = optional
        areas = result["result"].get("coverage", {}).get("areas", {})
        areas["failed_services"] = optional["failed_services"].get("status") == "observed"
        areas["critical_kernel_logs"] = optional["critical_kernel_logs"].get("status") in {"observed", "partial"}
        if areas:
            result["result"]["coverage"]["percent"] = round(sum(bool(value) for value in areas.values()) / len(areas) * 100, 1)
            result["result"]["coverage"]["unknown_areas"] = [name for name, value in areas.items() if not value]
        metrics = result.get("result", {}).get("metrics", {})
        try:
            metric_id = ctx.history.save_metrics("performance", metrics, config_fingerprint=ctx.config.fingerprint, completeness=result.get("completeness", {}).get("status", "failed"))
            result["result"]["history_written"] = True
            result["result"]["metric_history_id"] = metric_id
            try:
                result["result"]["baseline"] = ctx.history.metric_baseline("performance", config_fingerprint=ctx.config.fingerprint)
            except HistoryError:
                result["result"]["baseline"] = {"samples": 0, "status": "unavailable"}
        except HistoryError as exc:
            result["errors"].append(StructuredError(str(exc), "Performance baseline could not be saved", operation="metric_history").to_dict())
            result["completeness"]["errors"] += 1
            result["completeness"]["status"] = "partial"
            result["result"]["history_written"] = False
    elif tool == "storage.overview":
        result = storage_overview(ctx.policy, runner=runner)
    elif tool in {"storage.top", "storage.inspect", "storage.largest_files"}:
        scan_limits = ScanLimits.from_budget(budget, limits)
        view = {"storage.top": "top", "storage.inspect": "inspect", "storage.largest_files": "largest"}[tool]
        result = scan_envelope(tool, args["path"], ctx.policy, scan_limits, view=view)
    elif tool == "storage.snapshot":
        result = storage_snapshot(args["path"], ctx.config, ctx.policy, budget, history=ctx.history, overrides=limits)
    elif tool == "storage.growth":
        result = storage_growth(args["path"], ctx.config, ctx.policy, history=ctx.history)
    elif tool in {"storage.duplicate_candidates", "storage.duplicate_verify"}:
        if args.get("profile") != "deep":
            envelope = Envelope(tool, scope={"path": args["path"]}, policy_fingerprint=ctx.policy.fingerprint)
            envelope.error(StructuredError("deep_profile_required", "Duplicate analysis requires an explicit deep profile", path=args["path"], operation="dispatch", recoverable=False))
            result = envelope.finish(failed=True)
        else:
            result = duplicate_analysis(args["path"], ctx.policy, budget, verify_full=tool.endswith("verify"), overrides=limits)
    elif tool == "storage.space_discrepancy":
        if args.get("profile") != "deep":
            envelope = Envelope(tool, scope={"path": args["path"]}, policy_fingerprint=ctx.policy.fingerprint)
            envelope.error(StructuredError("deep_profile_required", "Discrepancy analysis requires an explicit deep profile", path=args["path"], operation="dispatch", recoverable=False))
            result = envelope.finish(failed=True)
        else:
            result = space_discrepancy(
                args["path"],
                ctx.policy,
                budget,
                config=ctx.config,
                runner=runner,
                overrides=limits,
            )
    elif tool == "maintenance.plan":
        result = maintenance_plan(args["path"], ctx.config, ctx.policy, budget, history=ctx.history, overrides=limits)
    elif tool == "maintenance.explain":
        plan = maintenance_plan(args["path"], ctx.config, ctx.policy, ctx.config.budget("standard"), history=ctx.history)
        result = explain_plan_item(plan, args["item_id"])
    else:
        raise AssertionError("unreachable_maintenance_tool")
    return _attach_public_metadata(result, tool)
