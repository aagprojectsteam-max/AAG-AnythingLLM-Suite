"""Versioned domain models shared by every maintenance collector."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

SCHEMA_VERSION = "1.0"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ActionRisk(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class RecommendationClass(StrEnum):
    LOW_RISK_CANDIDATE = "LOW_RISK_CANDIDATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PROTECTED = "PROTECTED"
    NOT_RECLAIMABLE = "NOT_RECLAIMABLE"
    UNKNOWN = "UNKNOWN"


class ProtectionClass(StrEnum):
    CRITICAL = "critical"
    PROTECTED = "protected"
    REVIEW_REQUIRED = "review_required"
    GENERATED_OUTPUT = "generated_output"
    CACHE_CANDIDATE = "cache_candidate"
    UNKNOWN = "unknown"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def stable_fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Observation:
    observation_id: str
    kind: str
    value: Any
    source: str
    unit: str | None = None
    path: str | None = None
    confidence: str = Confidence.CONFIRMED
    observed_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Inference:
    inference_id: str
    summary: str
    evidence_refs: tuple[str, ...]
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_refs"] = list(self.evidence_refs)
        return data


@dataclass(frozen=True)
class Finding:
    finding_id: str
    category: str
    summary: str
    severity: str
    confidence: str
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_refs"] = list(self.evidence_refs)
        return data


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    category: str
    summary: str
    rationale: str
    classification: str
    risk: str
    confidence: str
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_refs"] = list(self.evidence_refs)
        return data


@dataclass(frozen=True)
class StructuredError:
    code: str
    message: str
    path: str | None = None
    operation: str | None = None
    recoverable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Envelope:
    """Mutable builder with a stable, versioned public representation."""

    def __init__(
        self,
        tool: str,
        *,
        scope: Mapping[str, Any] | None = None,
        policy_fingerprint: str = "unknown",
    ) -> None:
        self._monotonic_started = time.monotonic()
        self.data: dict[str, Any] = {
            "schema": "aag-maintenance-scan-envelope-v1",
            "schema_version": SCHEMA_VERSION,
            "tool": tool,
            "scan_id": str(uuid.uuid4()),
            "started_at": utc_now(),
            "finished_at": None,
            "duration_ms": None,
            "scope": dict(scope or {}),
            "policy_fingerprint": policy_fingerprint,
            "completeness": {
                "status": Completeness.COMPLETE,
                "entries_examined": 0,
                "errors": 0,
                "truncated": False,
                "limits_reached": [],
            },
            "observations": [],
            "inferences": [],
            "findings": [],
            "recommendations": [],
            "errors": [],
            "result": {},
            "read_only": True,
            "mutated": False,
        }

    def error(self, error: StructuredError) -> None:
        self.data["errors"].append(error.to_dict())
        self.data["completeness"]["errors"] += 1

    def limit(self, name: str) -> None:
        limits = self.data["completeness"]["limits_reached"]
        if name not in limits:
            limits.append(name)
        self.data["completeness"]["truncated"] = True

    def finish(self, *, failed: bool = False) -> dict[str, Any]:
        completeness = self.data["completeness"]
        if failed:
            completeness["status"] = Completeness.FAILED
        elif completeness["errors"] or completeness["truncated"]:
            completeness["status"] = Completeness.PARTIAL
        else:
            completeness["status"] = Completeness.COMPLETE
        self.data["finished_at"] = utc_now()
        self.data["duration_ms"] = round(
            (time.monotonic() - self._monotonic_started) * 1000,
            3,
        )
        return self.data

