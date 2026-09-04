"""Read-only deterministic maturity and integrity verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aag_agent.audit import verify_chain
from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.endpoints import BRIDGE_CONTRACT_FILE_HOST, public_contract
from aag_agent.investigation.registry import PlaybookRegistry
from aag_agent.investigation.store import InvestigationStore
from aag_agent.remediation.bridge import BridgeObservationProvider
from aag_agent.remediation.registry import OperationRegistry
from aag_agent.remediation.store import RemediationStore

PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
RELEASE_MANIFEST = PROJECT_ROOT / "release/MANIFEST.sha256"
STATUS_PATH = PROJECT_ROOT / "release/status.json"
HOST_AUDIT = PROJECT_ROOT / "runtime/audit/mutations.jsonl"
IMMUTABLE_STAGE_MANIFESTS = (
    PROJECT_ROOT / "stage14/maintenance-v1-activation-20260827T132919Z/STAGE14-MANIFEST.sha256",
    PROJECT_ROOT / "stage15/maintenance-v1-grounding-fix-20260827T134831Z/STAGE15-MANIFEST.sha256",
    PROJECT_ROOT / "stage16/context-memory-v1-20260827T145233Z/STAGE16-MANIFEST.sha256",
    PROJECT_ROOT / "stage17/safe-remediation-v1-20260827T162838Z/STAGE17-MANIFEST.sha256",
    PROJECT_ROOT / "stage18/diagnostic-reasoning-v1-20260827T165530Z/STAGE18-MANIFEST.sha256",
    PROJECT_ROOT / "stage19/governed-orchestration-v1-20260827T170827Z/STAGE19-MANIFEST.sha256",
)
SKILL_BINDINGS = (
    (
        PROJECT_ROOT / "integrations/anythingllm/aag-ubuntu-diagnostics",
        Path("/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-ubuntu-live-audit"),
    ),
    (
        PROJECT_ROOT / "integrations/anythingllm/aag-maintenance-intelligence",
        Path("/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-maintenance-intelligence-v1"),
    ),
    (
        PROJECT_ROOT / "integrations/anythingllm/aag-context-memory-v1",
        Path("/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-context-memory-v1"),
    ),
    (
        PROJECT_ROOT / "integrations/anythingllm/aag-governed-orchestration-v1",
        Path("/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-governed-orchestration-v1"),
    ),
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(path: Path, *, root: Path) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    checked = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": "FAIL", "checked": 0, "errors": [{"code": "manifest_unreadable", "type": type(exc).__name__}]}
    for line in lines:
        if not line or "  " not in line:
            errors.append({"code": "manifest_line_invalid", "entry": line[:120]})
            continue
        expected, relative = line.split("  ", 1)
        target = root / relative
        try:
            if target.resolve().is_relative_to(root.resolve()) is False:
                errors.append({"code": "manifest_path_escape", "path": relative})
                continue
            actual = file_sha256(target)
        except (OSError, RuntimeError):
            errors.append({"code": "manifest_file_unreadable", "path": relative})
            continue
        checked += 1
        if len(expected) != 64 or actual != expected:
            errors.append({"code": "manifest_hash_mismatch", "path": relative})
    return {"status": "PASS" if not errors and checked else "FAIL", "checked": checked, "errors": errors}


class MaturityVerifier:
    def __init__(self, *, bridge_provider: BridgeObservationProvider | None = None) -> None:
        self.bridge_provider = bridge_provider or BridgeObservationProvider()

    @staticmethod
    def _stage_manifests() -> dict[str, Any]:
        results = []
        for path in IMMUTABLE_STAGE_MANIFESTS:
            result = verify_manifest(path, root=path.parent)
            results.append({"path": str(path.relative_to(PROJECT_ROOT)), **result})
        return {"status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL", "manifests": results}

    @staticmethod
    def _installed_skills(status_document: dict[str, Any]) -> dict[str, Any]:
        orchestration_live = "LIVE_VERIFIED" in str(status_document.get("governed_orchestration_maturity", ""))
        results = []
        for index, (canonical, installed) in enumerate(SKILL_BINDINGS):
            required = index < 3 or orchestration_live
            hashes = {}
            status = "PASS"
            for name in ("README.md", "handler.js", "plugin.json"):
                try:
                    source_hash = file_sha256(canonical / name)
                    installed_hash = file_sha256(installed / name)
                except OSError:
                    source_hash = installed_hash = None
                hashes[name] = {"canonical": source_hash, "installed": installed_hash, "match": source_hash is not None and source_hash == installed_hash}
                if not hashes[name]["match"]:
                    status = "FAIL" if required else "STAGED_NOT_LIVE"
            results.append({"canonical": str(canonical), "installed": str(installed), "required": required, "status": status, "hashes": hashes})
        return {"status": "PASS" if all(item["status"] in {"PASS", "STAGED_NOT_LIVE"} for item in results) else "FAIL", "skills": results}

    @staticmethod
    def _authority(status: dict[str, Any]) -> dict[str, Any]:
        values = {
            "maintenance": status.get("maintenance_intelligence", {}).get("execution_authority"),
            "context": status.get("context_memory", {}).get("execution_authority"),
            "diagnostic_reasoning": status.get("diagnostic_reasoning", {}).get("execution_authority"),
            "governed_orchestration": status.get("governed_orchestration", {}).get("execution_authority"),
            "safe_remediation": status.get("safe_remediation", {}).get("execution_authority"),
        }
        valid = (
            values["maintenance"] == "NONE"
            and values["context"] == "NONE"
            and values["diagnostic_reasoning"] == "NONE"
            and values["governed_orchestration"] == "NONE"
            and isinstance(values["safe_remediation"], str)
            and values["safe_remediation"].startswith("NONE_")
        )
        return {
            "status": "PASS" if valid else "FAIL", "values": values,
            "arbitrary_shell_authority": False,
            "model_approval_authority": False,
            "model_execution_authority": False,
        }

    @staticmethod
    def _endpoint_contract(status_document: dict[str, Any]) -> dict[str, Any]:
        expected = public_contract()
        try:
            live = json.loads(BRIDGE_CONTRACT_FILE_HOST.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"status": "FAIL", "error": type(exc).__name__}
        orchestration_live = "LIVE_VERIFIED" in str(status_document.get("governed_orchestration_maturity", ""))
        staged_contract = dict(expected)
        staged_contract.pop("orchestration_path", None)
        exact = live == expected
        staged = not orchestration_live and live == staged_contract
        return {
            "status": "PASS" if exact or staged else "FAIL",
            "activation_state": "LIVE" if exact else ("STAGED_NOT_LIVE" if staged else "MISMATCH"),
            "contract": live,
            "orchestration_route_present": "orchestration_path" in live,
            "investigation_route_present": "investigation_path" in live,
        }

    def run(self, *, live: bool = False) -> dict[str, Any]:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        context_integrity = ContextMemoryService().store.integrity()
        investigation_integrity = InvestigationStore().integrity()
        remediation_integrity = RemediationStore().integrity()
        host_audit = verify_chain(HOST_AUDIT)
        checks: dict[str, Any] = {
            "release_manifest": verify_manifest(RELEASE_MANIFEST, root=PROJECT_ROOT),
            "immutable_stage_manifests": self._stage_manifests(),
            "context_database": context_integrity,
            "investigation_database": investigation_integrity,
            "remediation_database": remediation_integrity,
            "host_mutation_audit": {
                "status": "PASS" if host_audit.get("valid") and host_audit.get("checkpoint", {}).get("valid") else "FAIL",
                **host_audit,
            },
            "operation_registry": {"status": "PASS", "operations": OperationRegistry().list()},
            "investigation_registry": {"status": "PASS", "playbooks": PlaybookRegistry().list()},
            "authority": self._authority(status),
            "endpoint_contract": self._endpoint_contract(status),
            "installed_skills": self._installed_skills(status),
        }
        bridge = None
        if live:
            bridge = dict(self.bridge_provider.observe())
            classification = bridge.get("classification")
            checks["live_bridge"] = {
                "status": "PASS" if classification == "HEALTHY" else "FAIL",
                "classification": classification,
                "main_pid": bridge.get("main_pid"),
                "health_ready": bridge.get("health_ready"),
                "read_only": True,
                "mutated": False,
            }
        failures = [name for name, value in checks.items() if value.get("status") != "PASS"]
        boundaries = [
            {
                "id": "stage17_live_attempt",
                "status": "NOT_PROVEN",
                "reason": "No naturally valid exact Bridge readiness failure and explicit live execution approval occurred during Stage 17.",
                "required_authority": "exact plan-bound operator approval after a fresh supported failure",
            },
            {
                "id": "arbitrary_repair",
                "status": "FORBIDDEN",
                "reason": "Only the exact accepted Bridge operation exists; generic shell or mutation authority is intentionally absent.",
                "required_authority": "new separately designed typed operation contracts, tests, risk policy, and approval",
            },
            {
                "id": "external_audit_anchor",
                "status": "NOT_IMPLEMENTED",
                "reason": "The local hash chain/checkpoint is valid but has no external signed anchor.",
                "required_authority": "separate key-management and trust design",
            },
        ]
        if "LIVE_VERIFIED" not in str(status.get("governed_orchestration_maturity", "")):
            boundaries.insert(1, {
                "id": "governed_orchestration_live_integration",
                "status": "NOT_DEPLOYED",
                "reason": "The reviewed additive Bridge route and AnythingLLM skill are staged but not yet live.",
                "required_authority": "reviewed activation including the exact additive files and authorized Bridge restart",
            })
        return {
            "schema": "aag-maturity-verification-v1",
            "status": "PASS_WITH_EXPLICIT_BOUNDARIES" if not failures else "FAIL",
            "release": status.get("version"),
            "maturities": {
                "maintenance": status.get("maturity"),
                "context_memory": status.get("context_memory_maturity"),
                "safe_remediation": status.get("safe_remediation_maturity"),
                "diagnostic_reasoning": status.get("diagnostic_reasoning_maturity"),
                "governed_orchestration": status.get("governed_orchestration_maturity"),
            },
            "checks": checks,
            "failed_checks": failures,
            "open_boundaries": boundaries,
            "eligible_for_arbitrary_repair": False,
            "live_check_requested": live,
            "bridge": bridge,
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }
