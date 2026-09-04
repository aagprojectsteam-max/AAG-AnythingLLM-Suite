"""Generic evidence and execution policy for remediation contracts."""

from __future__ import annotations

import time
from typing import Any, Mapping

from .contracts import Contract
from .detectors import BRIDGE_TARGET, SUPPORTED_FAILURE


def evaluate(contract: Contract, evidence: Mapping[str, Any] | None, *, now: float | None = None) -> dict[str, Any]:
    errors: list[str] = []
    evidence = evidence if isinstance(evidence, Mapping) else {}
    policy = contract.data["evidence"]
    for field in policy["required_fields"]:
        if field not in evidence:
            errors.append("missing_evidence:" + field)
    observed_at = evidence.get("observed_at")
    current = time.time() if now is None else now
    if not isinstance(observed_at, (int, float)):
        errors.append("invalid_evidence_timestamp")
    elif observed_at > current + 1:
        errors.append("future_evidence")
    elif current - observed_at > policy["max_age_seconds"]:
        errors.append("stale_evidence")
    if contract.contract_id == "bridge.readiness_failure":
        if evidence.get("target") != BRIDGE_TARGET:
            errors.append("wrong_target")
        if evidence.get("classification") != "SUPPORTED_FAILURE" or evidence.get("supported_failure_class") != SUPPORTED_FAILURE:
            errors.append("supported_failure_not_detected")
    classification = evidence.get("classification")
    if any(error.startswith("missing_evidence:") or error == "invalid_evidence_timestamp" for error in errors):
        classification = "MISSING"
    elif "future_evidence" in errors or "stale_evidence" in errors:
        classification = "STALE"
    return {
        "allowed": not errors and contract.status == "ACCEPTED",
        "contract_id": contract.contract_id, "contract_version": contract.data["version"], "contract_status": contract.status, "classification": classification, "errors": sorted(set(errors)),
        "approval_policy": contract.data["approval_policy"],
        "requires_explicit_approval": contract.data["approval_policy"] in {"EXPLICIT_USER_APPROVAL", "STRONG_CONFIRMATION_REQUIRED"},
        "execution_authority": "PENDING_EXPLICIT_APPROVAL" if not errors and contract.status == "ACCEPTED" else "NONE",
        "executed": False,
        "mutated": False,
    }
