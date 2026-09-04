"""Canonical types for Diagnostic Reasoning V1."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
RECORD_ID = re.compile(r"^[a-z][a-z0-9-]*:[a-f0-9]{24}$")
HASH = re.compile(r"^[a-f0-9]{64}$")
HYPOTHESIS_STATES = {"PENDING", "SUPPORTED", "FALSIFIED", "UNKNOWN", "CONTRADICTED"}
INVESTIGATION_STATES = {"OPEN", "COLLECTING", "ANALYZED", "INDETERMINATE", "FAILED", "CLOSED"}


class InvestigationValidationError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvestigationValidationError("value_not_canonical_json") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(kind: str, *parts: Any) -> str:
    digest = hashlib.sha256(canonical_json([kind, *parts]).encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:24]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def bounded_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    text = "".join(char if char in "\n\t" or ord(char) >= 32 else "�" for char in text)
    return text[:limit]
