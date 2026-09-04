from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from aag_agent.maintenance.config import MaintenanceConfig, ScanBudget, load_config
from aag_agent.maintenance.filesystem import Aggregate, ScanLimits, ScanOutcome
from aag_agent.maintenance.mounts import MountRecord
from aag_agent.maintenance.policy import ProtectedResourcePolicy


def test_config(root: Path) -> MaintenanceConfig:
    base = load_config()
    return replace(
        base,
        allowed_scope_roots=(root,),
        snapshot_roots=(root,),
        exclusions=(root / "excluded",),
        history_path=root / "history.sqlite3",
    )


def policy_document(root: Path, *, protection_class: str = "generated_output", hashing: bool = True, cleanup: bool = True) -> dict[str, Any]:
    return {
        "schema": "aag-maintenance-protected-resources-v1",
        "default_class": "unknown",
        "unknown_cleanup_policy": "review_required",
        "resources": [{
            "resource_id": "fixture-root",
            "path": str(root),
            "match": "subtree",
            "component": "fixture-component",
            "description": "Test fixture",
            "protection_class": protection_class,
            "allowed_scan_modes": ["summary", "metadata", "duplicate_quick", "duplicate_full"],
            "content_hashing_allowed": hashing,
            "cleanup_may_be_proposed": cleanup,
            "expected_mount": False,
            "expected_service": None,
            "dependencies": [],
            "dependents": [],
            "source": "test_fixture",
            "confidence": "confirmed"
        }]
    }


def make_policy(root: Path, *, protection_class: str = "generated_output", hashing: bool = True, cleanup: bool = True, registry: dict[str, Any] | None = None) -> tuple[MaintenanceConfig, ProtectedResourcePolicy]:
    config = test_config(root)
    policy_path = root / "policy.json"
    policy_path.write_text(json.dumps(policy_document(root, protection_class=protection_class, hashing=hashing, cleanup=cleanup)), encoding="utf-8")
    registry_path = root / "registry.json"
    if registry is not None:
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
    policy = ProtectedResourcePolicy(config, policy_path=policy_path, registry_path=registry_path)
    return config, policy


def mount_record(root: Path, *, mount_id: int = 1, fstype: str = "ext4", source: str = "/dev/test", options: tuple[str, ...] = ("rw",), major_minor: str = "0:99") -> MountRecord:
    return MountRecord(mount_id, 0, major_minor, "/", str(root), options, (), fstype, source, ("rw",))


def small_budget(**changes: Any) -> ScanBudget:
    base = load_config().budget("deep")
    defaults = {
        "max_duration_seconds": 5.0,
        "max_depth": 8,
        "max_entries": 1000,
        "result_limit": 100,
        "minimum_file_size": 1,
        "quick_fingerprint_bytes": 8,
        "max_full_hash_bytes": 10_000_000,
        "max_hash_workers": 1,
        "max_command_output_bytes": 4096,
        "command_timeout_seconds": 1.0,
    }
    defaults.update(changes)
    return ScanBudget(**defaults)


def fake_outcome(root: Path, *, total: int, children: dict[str, int] | None = None, mount_identity: str = "8:1:/dev/test:ext4:/") -> ScanOutcome:
    outcome = ScanOutcome(root, 1, mount_identity, ScanLimits(1.0, 2, 100, 10, 1))
    outcome.total = Aggregate(logical_bytes=total, allocated_bytes=total, files=1, directories=1)
    outcome.top = {
        str(root / name): Aggregate(logical_bytes=size, allocated_bytes=size, files=1)
        for name, size in (children or {"child": total}).items()
    }
    return outcome

