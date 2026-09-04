"""Pure types and canonical hashing for governed remediation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

OPERATION_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
RECORD_ID = re.compile(r"^[a-z][a-z0-9_-]*:[a-f0-9]{24}$")
HASH = re.compile(r"^[a-f0-9]{64}$")

PLAN_STATES = {
    "PROPOSED",
    "VALIDATED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "PRECONDITION_VERIFIED",
    "BACKUP_VERIFIED",
    "EXECUTING",
    "VERIFYING",
    "SUCCEEDED_VERIFIED",
    "FAILED_EXECUTION",
    "FAILED_VERIFICATION",
    "INDETERMINATE",
    "ABORTED_STALE_STATE",
    "ABORTED_APPROVAL",
    "ABORTED_AUDIT",
    "ROLLBACK_PROPOSED",
    "ROLLED_BACK_VERIFIED",
    "ROLLBACK_FAILED",
}

ALLOWED_TRANSITIONS = {
    "PROPOSED": {"VALIDATED"},
    "VALIDATED": {"AWAITING_APPROVAL"},
    "AWAITING_APPROVAL": {"APPROVED", "ABORTED_APPROVAL"},
    "APPROVED": {"PRECONDITION_VERIFIED", "ABORTED_STALE_STATE", "ABORTED_APPROVAL", "ABORTED_AUDIT"},
    "PRECONDITION_VERIFIED": {"BACKUP_VERIFIED", "ABORTED_STALE_STATE"},
    "BACKUP_VERIFIED": {"EXECUTING", "ABORTED_AUDIT"},
    "EXECUTING": {"VERIFYING", "FAILED_EXECUTION", "INDETERMINATE"},
    "VERIFYING": {"SUCCEEDED_VERIFIED", "FAILED_VERIFICATION", "INDETERMINATE"},
    "FAILED_EXECUTION": {"ROLLBACK_PROPOSED"},
    "FAILED_VERIFICATION": {"ROLLBACK_PROPOSED"},
    "INDETERMINATE": {"ROLLBACK_PROPOSED"},
    "ROLLBACK_PROPOSED": {"ROLLED_BACK_VERIFIED", "ROLLBACK_FAILED"},
}


class RemediationValidationError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RemediationValidationError("value_not_canonical_json") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(kind: str, *values: Any) -> str:
    digest = hashlib.sha256(canonical_json([kind, *values]).encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:24]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise RemediationValidationError("invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemediationValidationError("invalid_timestamp") from exc
    if result.tzinfo is None:
        raise RemediationValidationError("timestamp_must_be_utc")
    return result.astimezone(timezone.utc)


def bounded_text(value: Any, *, limit: int = 4096) -> str:
    text = str(value or "")
    text = "".join(char if char in "\n\t" or ord(char) >= 32 else "�" for char in text)
    return text[:limit]


@dataclass(frozen=True)
class OperationSpec:
    data: Mapping[str, Any]
    registry_sha256: str

    @property
    def operation_id(self) -> str:
        return str(self.data["operation_id"])

    @property
    def version(self) -> int:
        return int(self.data["operation_version"])

    @property
    def target(self) -> str:
        return str(self.data["target_identity"])

    @property
    def risk(self) -> str:
        return str(self.data["risk_class"])

    @property
    def approval_class(self) -> str:
        return str(self.data["approval_class"])

    @property
    def accepted(self) -> bool:
        return self.data["lifecycle_state"] == "ACCEPTED"


def validate_transition(current: str, target: str) -> None:
    if current not in PLAN_STATES or target not in PLAN_STATES:
        raise RemediationValidationError("unknown_lifecycle_state")
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise RemediationValidationError(f"invalid_transition:{current}:{target}")
