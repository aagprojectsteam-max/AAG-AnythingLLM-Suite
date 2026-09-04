"""Strict project-local configuration for Maintenance Intelligence V1."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import stable_fingerprint

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/maintenance-v1.json"
DEFAULT_POLICY_PATH = PROJECT_ROOT / "config/maintenance-protected-resources-v1.json"


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ScanBudget:
    max_duration_seconds: float
    max_depth: int
    max_entries: int
    result_limit: int
    minimum_file_size: int
    quick_fingerprint_bytes: int
    max_full_hash_bytes: int
    max_hash_workers: int
    max_command_output_bytes: int
    command_timeout_seconds: float


@dataclass(frozen=True)
class MaintenanceConfig:
    raw: Mapping[str, Any]
    path: Path
    allowed_scope_roots: tuple[Path, ...]
    snapshot_roots: tuple[Path, ...]
    expected_services: tuple[Mapping[str, Any], ...]
    exclusions: tuple[Path, ...]
    budgets: Mapping[str, ScanBudget]
    thresholds: Mapping[str, float]
    history_path: Path
    history_retention: int
    adapters: Mapping[str, bool]
    external_registry_path: Path
    fingerprint: str

    def budget(self, profile: str) -> ScanBudget:
        try:
            return self.budgets[profile]
        except KeyError as exc:
            raise ConfigurationError("unknown_scan_profile") from exc


TOP_LEVEL = {
    "schema",
    "allowed_scope_roots",
    "snapshot_roots",
    "expected_services",
    "exclusions",
    "budgets",
    "thresholds",
    "history",
    "adapters",
    "external_registry_path",
}

BUDGET_FIELDS = {
    "max_duration_seconds",
    "max_depth",
    "max_entries",
    "result_limit",
    "minimum_file_size",
    "quick_fingerprint_bytes",
    "max_full_hash_bytes",
    "max_hash_workers",
    "max_command_output_bytes",
    "command_timeout_seconds",
}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError("maintenance_config_missing") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("maintenance_config_unreadable") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("maintenance_config_must_be_object")
    return data


def _absolute_paths(value: Any, field: str) -> tuple[Path, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError(f"invalid_{field}")
    result: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ConfigurationError(f"invalid_{field}")
        path = Path(item)
        if not path.is_absolute() or ".." in path.parts:
            raise ConfigurationError(f"invalid_{field}")
        result.append(path)
    return tuple(result)


def _positive_number(value: Any, field: str, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigurationError(f"invalid_{field}")
    if integer and not isinstance(value, int):
        raise ConfigurationError(f"invalid_{field}")
    return value


def _budget(raw: Any, profile: str) -> ScanBudget:
    if not isinstance(raw, dict) or set(raw) != BUDGET_FIELDS:
        raise ConfigurationError(f"invalid_budget_{profile}")
    integers = BUDGET_FIELDS - {"max_duration_seconds", "command_timeout_seconds"}
    values = {
        key: _positive_number(value, f"{profile}_{key}", integer=key in integers)
        for key, value in raw.items()
    }
    if values["max_hash_workers"] > 4:
        raise ConfigurationError(f"invalid_{profile}_max_hash_workers")
    if values["max_command_output_bytes"] > 4_000_000:
        raise ConfigurationError(f"invalid_{profile}_command_output")
    return ScanBudget(**values)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> MaintenanceConfig:
    data = _load_object(path)
    if set(data) != TOP_LEVEL or data.get("schema") != "aag-maintenance-config-v1":
        raise ConfigurationError("invalid_maintenance_config_schema")
    budgets_raw = data["budgets"]
    if not isinstance(budgets_raw, dict) or set(budgets_raw) != {"quick", "standard", "deep"}:
        raise ConfigurationError("invalid_scan_budgets")
    budgets = {name: _budget(value, name) for name, value in budgets_raw.items()}

    thresholds = data["thresholds"]
    expected_thresholds = {
        "filesystem_warning_percent",
        "filesystem_critical_percent",
        "inode_warning_percent",
        "memory_available_warning_percent",
        "swap_used_warning_percent",
        "load_per_cpu_warning",
        "temperature_warning_c",
        "growth_warning_bytes",
        "growth_warning_percent",
        "anomaly_mad_multiplier",
    }
    if not isinstance(thresholds, dict) or set(thresholds) != expected_thresholds:
        raise ConfigurationError("invalid_thresholds")
    checked_thresholds: dict[str, float] = {}
    for name, value in thresholds.items():
        checked_thresholds[name] = float(_positive_number(value, name))
    if checked_thresholds["filesystem_critical_percent"] <= checked_thresholds["filesystem_warning_percent"]:
        raise ConfigurationError("filesystem_threshold_order")

    history = data["history"]
    if not isinstance(history, dict) or set(history) != {"database_path", "retention_snapshots"}:
        raise ConfigurationError("invalid_history_config")
    history_path = Path(history["database_path"])
    if not history_path.is_absolute() or PROJECT_ROOT not in history_path.parents:
        raise ConfigurationError("history_path_outside_project")
    retention = int(_positive_number(history["retention_snapshots"], "retention_snapshots", integer=True))

    adapters = data["adapters"]
    if not isinstance(adapters, dict) or set(adapters) != {"docker", "smart", "nvme", "systemd", "journal", "package"}:
        raise ConfigurationError("invalid_adapters")
    if not all(isinstance(value, bool) for value in adapters.values()):
        raise ConfigurationError("invalid_adapter_flag")

    registry_path = Path(data["external_registry_path"])
    if not registry_path.is_absolute():
        raise ConfigurationError("invalid_external_registry_path")

    expected_services = data["expected_services"]
    if not isinstance(expected_services, list) or not expected_services:
        raise ConfigurationError("invalid_expected_services")
    checked_services: list[dict[str, Any]] = []
    for item in expected_services:
        if (
            not isinstance(item, dict)
            or set(item) != {"service", "manager", "critical"}
            or not isinstance(item["service"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,160}\.service", item["service"])
            or item["manager"] not in {"system", "user"}
            or not isinstance(item["critical"], bool)
        ):
            raise ConfigurationError("invalid_expected_service")
        checked_services.append(dict(item))

    return MaintenanceConfig(
        raw=data,
        path=path,
        allowed_scope_roots=_absolute_paths(data["allowed_scope_roots"], "allowed_scope_roots"),
        snapshot_roots=_absolute_paths(data["snapshot_roots"], "snapshot_roots"),
        expected_services=tuple(checked_services),
        exclusions=_absolute_paths(data["exclusions"], "exclusions"),
        budgets=budgets,
        thresholds=checked_thresholds,
        history_path=history_path,
        history_retention=retention,
        adapters=dict(adapters),
        external_registry_path=registry_path,
        fingerprint=stable_fingerprint(data),
    )
