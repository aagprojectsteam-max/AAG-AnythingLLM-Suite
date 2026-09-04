"""Shared constants and deterministic helpers for Context & Memory V1."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

EPISTEMIC_STATES = {
    "VERIFIED", "CANDIDATE", "USER_REPORTED", "INFERRED",
    "UNVERIFIED", "CONFLICTED", "UNKNOWN",
}
TEMPORAL_SCOPES = {"CURRENT", "HISTORICAL", "LIVE_OBSERVATION"}
LIFECYCLE_STATES = {
    "ACTIVE", "SUPERSEDED", "FAILED_ATTEMPT", "REJECTED",
    "TEMPORARY_WORKAROUND", "RETIRED",
}
FRESHNESS_STATES = {"FRESH", "STALE", "EXPIRED", "NOT_APPLICABLE"}
VERIFICATION_LEVELS = {
    "DIRECT_LIVE", "TEST_VERIFIED", "ARTIFACT_VERIFIED", "DOCUMENTED",
    "USER_CONFIRMED", "INFERRED", "UNVERIFIED",
}
SOURCE_TYPES = {
    "live_tool", "verified_test", "stage_evidence", "release_evidence",
    "handoff", "incident", "configuration_snapshot", "registry",
    "ubuntu_manager", "user_statement", "imported_document", "llm_inference",
}
HISTORICAL_TERMS = {
    "previous", "previously", "history", "historical", "failed", "rejected",
    "prior", "past", "before", "what happened", "נכשל", "ניסינו", "דחינו",
    "בעבר", "היסטורי", "היסטוריה", "קודם", "מה קרה", "last time",
    "recent incident", "during that incident", "incident", "בפעם האחרונה",
    "האחרונה", "במהלך התקלה", "תקלה",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def stable_id(prefix: str, *parts: Any) -> str:
    body = "\x1f".join(canonical_json(part) if not isinstance(part, str) else part for part in parts)
    return f"{prefix}:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:24]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(value: str) -> str:
    value = value.replace("\x00", " ")
    value = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else canonical_json(value)
    return max(1, (len(text) + 3) // 4)


def historical_intent(query: str) -> bool:
    normalized = normalize_text(query)
    return any(term in normalized for term in HISTORICAL_TERMS)
