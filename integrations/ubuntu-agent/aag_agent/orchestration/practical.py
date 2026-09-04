"""Bounded read-only practical workflows built from verified AAG subsystems."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.maintenance.config import load_config
from aag_agent.maintenance.orchestrator import dispatch as dispatch_maintenance
from aag_agent.maintenance.policy import ProtectedResourcePolicy
from aag_agent.maturity import MaturityVerifier


PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
MAINTENANCE_DATABASE = PROJECT_ROOT / "memory/maintenance-intelligence-v1.sqlite3"
REMEDIATION_DATABASE = PROJECT_ROOT / "memory/safe-remediation-v1.sqlite3"
ANYTHINGLLM_PING = "http://127.0.0.1:3000/api/ping"
SAMPLE_COUNT = 3
SAMPLE_INTERVAL_SECONDS = 1.0
MAX_SAMPLING_WINDOW_SECONDS = 15.0
MAX_WORKFLOW_SECONDS = 30.0


class PracticalWorkflowError(RuntimeError):
    pass


def _sqlite_integrity(path: Path) -> dict[str, Any]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        return {"status": "UNAVAILABLE", "error": type(exc).__name__}
    return {
        "status": "PASS" if quick == "ok" and not foreign else "FAIL",
        "quick_check": quick,
        "foreign_key_violations": len(foreign),
        "schema_version": version,
    }


def _anythingllm_health() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(ANYTHINGLLM_PING, timeout=3) as response:
            raw = response.read(4096)
            payload = json.loads(raw)
            healthy = response.status == 200 and payload == {"online": True}
    except Exception as exc:
        return {"status": "UNAVAILABLE", "error": type(exc).__name__, "read_only": True}
    return {"status": "PASS" if healthy else "DEGRADED", "http_status": response.status, "online": bool(payload.get("online")), "read_only": True}


def _remediation_activity() -> dict[str, int | str]:
    try:
        connection = sqlite3.connect(f"file:{REMEDIATION_DATABASE}?mode=ro", uri=True, timeout=3)
        try:
            pending = int(connection.execute(
                "SELECT count(*) FROM approvals WHERE state IN ('PENDING','RECORDED')"
            ).fetchone()[0])
            active = int(connection.execute(
                """SELECT count(*) FROM plans WHERE state IN
                   ('AWAITING_APPROVAL','APPROVED','PRECONDITION_VERIFIED','BACKUP_VERIFIED','EXECUTING','VERIFYING')"""
            ).fetchone()[0])
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError):
        return {"status": "UNAVAILABLE", "pending_approvals": -1, "active_attempts": -1}
    return {"status": "PASS", "pending_approvals": pending, "active_attempts": active}


class PracticalWorkflows:
    def __init__(
        self,
        context: ContextMemoryService,
        *,
        maintenance_dispatch: Callable[[str, Mapping[str, Any]], dict[str, Any]] = dispatch_maintenance,
        maturity_runner: Callable[[], dict[str, Any]] | None = None,
        anythingllm_health: Callable[[], dict[str, Any]] = _anythingllm_health,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.context = context
        self.maintenance_dispatch = maintenance_dispatch
        self.maturity_runner = maturity_runner or (lambda: MaturityVerifier().run(live=True))
        self.anythingllm_health = anythingllm_health
        self.sleeper = sleeper
        self.monotonic = monotonic

    def _artifact(self, name: str, payload: Mapping[str, Any]) -> str:
        return self.context._live_artifact(f"operational-{name}", payload)

    def current_release(self) -> dict[str, Any]:
        try:
            version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
            status = json.loads((PROJECT_ROOT / "release/status.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PracticalWorkflowError("release_state_unavailable") from exc
        if status.get("version") != version:
            raise PracticalWorkflowError("release_state_conflicted")
        result = {
            "schema": "aag-current-release-v1",
            "status": "OBSERVED",
            "version": version,
            "top_level_maturity": status.get("top_level_maturity") or status.get("maturity_verification", {}).get("status"),
            "subsystem_maturities": {
                "maintenance": status.get("maturity"),
                "context_memory": status.get("context_memory_maturity"),
                "diagnostic_reasoning": status.get("diagnostic_reasoning_maturity"),
                "governed_orchestration": status.get("governed_orchestration_maturity"),
                "safe_remediation": status.get("safe_remediation_maturity"),
            },
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }
        result["evidence_ids"] = [self._artifact("current-release", result)]
        return result

    @staticmethod
    def _validated_maintenance(tool: str, result: Mapping[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(result, Mapping)
            or result.get("schema") != "aag-maintenance-scan-envelope-v1"
            or result.get("read_only") is not True
            or result.get("mutated") is not False
            or result.get("completeness", {}).get("status") == "failed"
        ):
            raise PracticalWorkflowError(f"maintenance_{tool}_unavailable")
        return dict(result)

    def self_health(self) -> dict[str, Any]:
        started = self.monotonic()
        try:
            maturity = self.maturity_runner()
        except Exception as exc:
            raise PracticalWorkflowError("maturity_verifier_unavailable") from exc
        if not isinstance(maturity, Mapping) or maturity.get("execution_authority") != "NONE":
            raise PracticalWorkflowError("maturity_verifier_contract_invalid")
        maintenance_db = _sqlite_integrity(MAINTENANCE_DATABASE)
        anythingllm = self.anythingllm_health()
        remediation_activity = _remediation_activity()
        active_tasks = self.context.tasks.active(orchestrator="governed-orchestration-v1", limit=20)
        checks = dict(maturity.get("checks", {}))
        checks["maintenance_database"] = maintenance_db
        checks["anythingllm"] = anythingllm
        checks["remediation_activity"] = remediation_activity
        checks["active_orchestration_tasks"] = {
            "status": "PASS",
            "count": len(active_tasks),
            "stale_or_incomplete": sum(1 for item in active_tasks if item.get("closure_status") == "BLOCKED"),
        }
        integrity_names = {
            "release_manifest", "immutable_stage_manifests", "context_database",
            "investigation_database", "remediation_database", "maintenance_database",
            "host_mutation_audit", "operation_registry", "investigation_registry",
        }
        integrity_failure = any(checks.get(name, {}).get("status") == "FAIL" for name in integrity_names)
        unavailable = [name for name, value in checks.items() if isinstance(value, Mapping) and value.get("status") == "UNAVAILABLE"]
        degraded = [name for name, value in checks.items() if isinstance(value, Mapping) and value.get("status") not in {"PASS", None}]
        if integrity_failure:
            status = "INTEGRITY_FAILURE"
        elif maturity.get("status") not in {"PASS", "PASS_WITH_EXPLICIT_BOUNDARIES"}:
            status = "DEGRADED"
        elif unavailable:
            status = "PARTIAL_COVERAGE"
        elif degraded:
            status = "DEGRADED"
        else:
            status = "HEALTHY"
        result = {
            "schema": "aag-agent-self-health-v1",
            "status": status,
            "checks": checks,
            "unavailable_checks": unavailable,
            "degraded_checks": degraded,
            "active_tasks": [{"task_id": item["task_id"], "closure_status": item["closure_status"], "updated_at": item["updated_at"]} for item in active_tasks],
            "pending_approvals": remediation_activity["pending_approvals"],
            "active_remediation_attempts": remediation_activity["active_attempts"],
            "duration_ms": round((self.monotonic() - started) * 1000, 3),
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }
        artifact_id = self._artifact("self-health", result)
        result["evidence_ids"] = [artifact_id]
        return result

    def storage_protection(self) -> dict[str, Any]:
        config = load_config()
        policy = ProtectedResourcePolicy(config)
        roots = [
            "/mnt/data/WinBoat-Assets", "/mnt/data/USB Clone", "/mnt/data/AI/Backups",
            "/mnt/data/AAG-Backups", "/mnt/data/timeshift", "/mnt/data/AI/Models",
        ]
        decisions = [policy.classify(path).to_dict() for path in roots]
        result = {
            "schema": "aag-storage-protection-context-v1",
            "status": "OBSERVED",
            "resources": decisions,
            "deletion_authority": "NONE",
            "recommendation": "Protected and review-required resources require separate evidence and governed operator review; this workflow never deletes them.",
            "coverage": "configured_protected_resource_classes",
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }
        result["evidence_ids"] = [self._artifact("storage-protection", result)]
        return result

    def storage_consumers(self) -> dict[str, Any]:
        started = self.monotonic()
        limits = {
            "max_duration_seconds": 3.0,
            "max_depth": 1,
            "max_entries": 2000,
            "result_limit": 25,
            "minimum_file_size": 104857600,
        }
        overview = self._validated_maintenance("storage.overview", self.maintenance_dispatch("storage.overview", {}))
        top = self._validated_maintenance("storage.top", self.maintenance_dispatch("storage.top", {"path": "/mnt/data", "profile": "quick", "limits": limits}))
        largest = self._validated_maintenance("storage.largest_files", self.maintenance_dispatch("storage.largest_files", {"path": "/mnt/data", "profile": "quick", "limits": limits}))
        protection = self.storage_protection()
        evidence_ids = [
            self._artifact("storage-overview", overview),
            self._artifact("storage-top", top),
            self._artifact("storage-largest", largest),
            *protection["evidence_ids"],
        ]
        result = {
            "schema": "aag-storage-consumers-v1",
            "status": "OBSERVED" if all(item.get("completeness", {}).get("status") == "complete" for item in (overview, top, largest)) else "PARTIAL",
            "trusted_root": "/mnt/data",
            "overview": overview.get("result", {}),
            "top_directories": top.get("result", {}).get("top", []),
            "largest_files": largest.get("result", {}).get("largest_files", []),
            "protection": protection["resources"],
            "scan_coverage": {
                "profile": "quick",
                "max_depth": 1,
                "max_entries": 2000,
                "sampling_not_full_inventory": True,
                "excluded_paths": sorted(set(top.get("result", {}).get("exclusions_skipped", []) + largest.get("result", {}).get("exclusions_skipped", []))),
                "nested_mounts_skipped": sorted(set(top.get("result", {}).get("mount_boundaries_skipped", []) + largest.get("result", {}).get("mount_boundaries_skipped", []))),
                "hardlinks_not_double_counted": True,
                "logical_and_allocated_bytes_reported_separately": True,
            },
            "evidence_ids": evidence_ids,
            "duration_ms": round((self.monotonic() - started) * 1000, 3),
            "cleanup_executed": False,
            "commands": [],
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }
        return result

    @staticmethod
    def _metrics(sample: Mapping[str, Any]) -> dict[str, float]:
        values = sample.get("result", {}).get("metrics", {})
        return {key: float(value) for key, value in values.items() if isinstance(value, (int, float)) and not isinstance(value, bool)}

    def sustained_performance(self) -> dict[str, Any]:
        started = self.monotonic()
        samples: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for index in range(SAMPLE_COUNT):
            if self.monotonic() - started >= MAX_SAMPLING_WINDOW_SECONDS:
                break
            sample = self._validated_maintenance("performance.snapshot", self.maintenance_dispatch("performance.snapshot", {}))
            samples.append(sample)
            evidence_ids.append(self._artifact(f"performance-sample-{index + 1}", sample))
            if index + 1 < SAMPLE_COUNT:
                self.sleeper(SAMPLE_INTERVAL_SECONDS)
        if not samples or self.monotonic() - started > MAX_WORKFLOW_SECONDS:
            raise PracticalWorkflowError("sustained_performance_budget_exceeded")
        metrics = [self._metrics(item) for item in samples]
        rules = {
            "cpu_pressure": lambda item: item.get("cpu_utilization_percent", 0) >= 85 or item.get("cpu_pressure_avg10", 0) >= 1,
            "memory_pressure": lambda item: item.get("memory_available_percent", 100) <= 10 or item.get("memory_pressure_avg10", 0) >= 1,
            "swap_pressure": lambda item: item.get("swap_used_percent", 0) >= 70 or item.get("swap_in_delta", 0) > 0,
            "io_pressure": lambda item: item.get("io_pressure_avg10", 0) >= 1 or item.get("io_wait_percent", 0) >= 20,
            "thermal_contributor": lambda item: item.get("maximum_temperature_c", 0) >= 85,
        }
        evaluations = []
        for name, rule in rules.items():
            matched = sum(1 for item in metrics if rule(item))
            classification = "SUPPORTED_CONTRIBUTOR" if matched >= 2 else ("ONE_TIME_SPIKE" if matched == 1 else "FALSIFIED_IN_BOUNDED_WINDOW")
            evaluations.append({
                "hypothesis": name,
                "classification": classification,
                "matched_samples": matched,
                "sample_count": len(samples),
                "verified_root_cause": False,
            })
        result = {
            "schema": "aag-sustained-performance-v1",
            "status": "OBSERVED" if len(samples) == SAMPLE_COUNT else "PARTIAL",
            "samples": [{"ordinal": index + 1, "metrics": item} for index, item in enumerate(metrics)],
            "hypothesis_evaluations": evaluations,
            "causal_language": {
                "strongest_allowed": "SUPPORTED_CONTRIBUTOR",
                "verified_root_cause": False,
                "single_sample_cannot_establish_cause": True,
            },
            "limits": {
                "maximum_samples": SAMPLE_COUNT,
                "maximum_sampling_window_seconds": MAX_SAMPLING_WINDOW_SECONDS,
                "maximum_total_seconds": MAX_WORKFLOW_SECONDS,
            },
            "evidence_ids": evidence_ids,
            "duration_ms": round((self.monotonic() - started) * 1000, 3),
            "commands": [],
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }
        return result
