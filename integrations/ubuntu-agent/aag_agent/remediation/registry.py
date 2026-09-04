"""Strict fixed operation registry; registry data, never model text, owns execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import OPERATION_ID, OperationSpec

PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
DEFAULT_REGISTRY = PROJECT_ROOT / "config/remediation-operations-v1.json"

RISK_APPROVAL = {
    "R0": "NO_MUTATION",
    "R1": "PREAUTHORIZED_EXACT_CONTRACT",
    "R2": "USER_CONFIRMATION",
    "R3": "STRONG_EXPLICIT_CONFIRMATION",
    "R4": "SEPARATE_OPERATION_AUTHORIZATION",
    "R5": "FORBIDDEN",
}
BRIDGE_REQUIRED_EVIDENCE_FIELDS = {
    "schema", "observed_at", "target", "load_state", "active_state", "sub_state",
    "main_pid", "health_ready", "health_error", "classification",
    "supported_failure_class", "provenance",
}

TOP_FIELDS = {"schema", "registry_version", "operations"}
OPERATION_FIELDS = {
    "operation_id", "operation_version", "lifecycle_state", "description",
    "contract_id", "contract_version", "target_type", "target_identity",
    "risk_class", "approval_class", "required_evidence", "preconditions",
    "backup_policy", "executor", "post_verifier", "success_criteria",
    "failure_criteria", "indeterminate_criteria", "rollback", "audit_policy",
    "idempotency",
}


class OperationRegistryError(ValueError):
    pass


def _nonempty_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _validate_operation(raw: Any) -> None:
    if not isinstance(raw, dict) or set(raw) != OPERATION_FIELDS:
        raise OperationRegistryError("invalid_operation_fields")
    if not OPERATION_ID.fullmatch(str(raw["operation_id"])):
        raise OperationRegistryError("invalid_operation_id")
    if isinstance(raw["operation_version"], bool) or not isinstance(raw["operation_version"], int) or raw["operation_version"] < 1:
        raise OperationRegistryError("invalid_operation_version")
    if raw["lifecycle_state"] not in {"DRAFT", "TESTED", "ACCEPTED", "DEPRECATED"}:
        raise OperationRegistryError("invalid_operation_lifecycle")
    if raw["risk_class"] not in RISK_APPROVAL or raw["approval_class"] != RISK_APPROVAL[raw["risk_class"]]:
        raise OperationRegistryError("risk_approval_mismatch")
    if not all(isinstance(raw[field], str) and raw[field] for field in ("description", "contract_id", "target_type", "target_identity", "audit_policy")):
        raise OperationRegistryError("invalid_operation_metadata")
    if isinstance(raw["contract_version"], bool) or not isinstance(raw["contract_version"], int) or raw["contract_version"] < 1:
        raise OperationRegistryError("invalid_contract_version")
    evidence = raw["required_evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"schema", "classification", "max_age_seconds", "required_fields"}:
        raise OperationRegistryError("invalid_evidence_contract")
    if not isinstance(evidence["max_age_seconds"], (int, float)) or isinstance(evidence["max_age_seconds"], bool) or evidence["max_age_seconds"] <= 0:
        raise OperationRegistryError("invalid_evidence_ttl")
    if not _nonempty_strings(evidence["required_fields"]) or len(evidence["required_fields"]) != len(set(evidence["required_fields"])):
        raise OperationRegistryError("invalid_required_evidence_fields")
    if not isinstance(raw["preconditions"], list) or not raw["preconditions"]:
        raise OperationRegistryError("preconditions_required")
    allowed_check_fields = {"check_id", "check_version", "kind", "field", "expected", "invalidation"}
    for check in raw["preconditions"]:
        if not isinstance(check, dict) or set(check) - allowed_check_fields or not {"check_id", "check_version", "kind", "expected", "invalidation"}.issubset(check):
            raise OperationRegistryError("invalid_precondition")
        if check["kind"] not in {"EXACT_VALUE", "FIELD_SET"} or check["invalidation"] != "ABORT_PRECONDITION_CHANGED":
            raise OperationRegistryError("unsafe_precondition")
    backup = raw["backup_policy"]
    if not isinstance(backup, dict) or set(backup) != {"class", "justification", "restore_test_required"}:
        raise OperationRegistryError("invalid_backup_policy")
    if backup["class"] not in {"NO_BACKUP_REQUIRED_WITH_JUSTIFICATION", "BACKUP_REQUIRED"} or not isinstance(backup["justification"], str) or not backup["justification"]:
        raise OperationRegistryError("unsafe_backup_policy")
    if not isinstance(backup["restore_test_required"], bool):
        raise OperationRegistryError("invalid_restore_test_policy")
    executor = raw["executor"]
    if not isinstance(executor, dict) or set(executor) != {"primitive", "fixed_executable", "fixed_argv", "timeout_seconds", "concurrency_policy"}:
        raise OperationRegistryError("invalid_executor_contract")
    if executor["primitive"] != "restart_exact_bridge_user_service" or executor["fixed_executable"] != "/usr/bin/systemctl":
        raise OperationRegistryError("unknown_executor_primitive")
    expected_argv = ["/usr/bin/systemctl", "--user", "restart", "aag-ubuntu-agent-bridge.service"]
    if executor["fixed_argv"] != expected_argv or raw["target_identity"] != expected_argv[-1]:
        raise OperationRegistryError("executor_target_binding_mismatch")
    if not isinstance(executor["timeout_seconds"], int) or not 1 <= executor["timeout_seconds"] <= 30:
        raise OperationRegistryError("unsafe_executor_timeout")
    if executor["concurrency_policy"] != "EXCLUSIVE_EXACT_TARGET":
        raise OperationRegistryError("unsafe_concurrency_policy")
    verifier = raw["post_verifier"]
    if not isinstance(verifier, dict) or set(verifier) != {"primitive", "timeout_seconds", "required"} or verifier["primitive"] != "bridge_readiness_v1" or not _nonempty_strings(verifier["required"]):
        raise OperationRegistryError("invalid_post_verifier")
    for field in ("success_criteria", "failure_criteria", "indeterminate_criteria"):
        if not _nonempty_strings(raw[field]):
            raise OperationRegistryError("invalid_" + field)
    rollback = raw["rollback"]
    if not isinstance(rollback, dict) or set(rollback) != {"capability", "approval_class", "justification"}:
        raise OperationRegistryError("invalid_rollback_policy")
    if rollback["capability"] != "NONE" or rollback["approval_class"] != "SEPARATE_OPERATION_AUTHORIZATION" or not rollback["justification"]:
        raise OperationRegistryError("unsafe_rollback_policy")
    if raw["audit_policy"] != "MUTATION_FULL_V1" or raw["idempotency"] != {"replay": "FORBIDDEN", "retry": "NEW_PLAN_AND_APPROVAL_REQUIRED"}:
        raise OperationRegistryError("unsafe_audit_or_idempotency_policy")
    if raw["operation_id"] != "bridge.restart.readiness_failure" or raw["target_type"] != "user_systemd_service":
        raise OperationRegistryError("unsupported_operation_domain")
    if (
        raw["contract_id"] != "bridge.readiness_failure"
        or raw["contract_version"] != 1
        or raw["risk_class"] != "R2"
        or raw["approval_class"] != "USER_CONFIRMATION"
        or evidence["schema"] != "aag-bridge-detector-evidence-v1"
        or evidence["classification"] != "SUPPORTED_FAILURE"
        or evidence["max_age_seconds"] != 30
        or set(evidence["required_fields"]) != BRIDGE_REQUIRED_EVIDENCE_FIELDS
        or backup["class"] != "NO_BACKUP_REQUIRED_WITH_JUSTIFICATION"
        or backup["restore_test_required"] is not False
    ):
        raise OperationRegistryError("bridge_operation_contract_drift")


class OperationRegistry:
    def __init__(self, path: Path = DEFAULT_REGISTRY) -> None:
        self.path = path
        try:
            encoded = path.read_bytes()
            raw = json.loads(encoded)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationRegistryError("operation_registry_unreadable") from exc
        if not isinstance(raw, dict) or set(raw) != TOP_FIELDS or raw.get("schema") != "aag-remediation-operation-registry-v1" or raw.get("registry_version") != 1:
            raise OperationRegistryError("invalid_operation_registry")
        if not isinstance(raw["operations"], list) or not raw["operations"]:
            raise OperationRegistryError("empty_operation_registry")
        self.sha256 = hashlib.sha256(encoded).hexdigest()
        self._operations: dict[tuple[str, int], OperationSpec] = {}
        for item in raw["operations"]:
            _validate_operation(item)
            key = (item["operation_id"], item["operation_version"])
            if key in self._operations:
                raise OperationRegistryError("duplicate_operation_version")
            self._operations[key] = OperationSpec(dict(item), self.sha256)

    def get(self, operation_id: str, version: int, *, execution: bool = False) -> OperationSpec:
        if not isinstance(operation_id, str) or not isinstance(version, int) or isinstance(version, bool):
            raise OperationRegistryError("invalid_operation_selector")
        try:
            operation = self._operations[(operation_id, version)]
        except KeyError as exc:
            raise OperationRegistryError("unknown_operation_or_version") from exc
        if execution and not operation.accepted:
            raise OperationRegistryError("operation_not_accepted")
        return operation

    def list(self) -> list[dict[str, Any]]:
        return [dict(item.data) for item in sorted(self._operations.values(), key=lambda item: (item.operation_id, item.version))]
