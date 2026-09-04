"""Strict provider-portable request and response contracts for `/orchestrate`."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .intent import MAX_REQUEST_BYTES


REQUEST_SCHEMA = "aag-governed-orchestration-request-v1"
RESPONSE_SCHEMA = "aag-governed-orchestration-response-v1"
ERROR_SCHEMA = "aag-governed-orchestration-error-v1"
MAX_RESPONSE_BYTES = 512_000
TASK_ID = re.compile(r"^task:[a-f0-9]{24}$")
REQUEST_FIELDS = {"schema", "request", "continuation"}
CONTINUATION_FIELDS = {"task_id"}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedRequest:
    request: str
    task_id: str | None


def validate_request(payload: Mapping[str, Any]) -> ValidatedRequest:
    if not isinstance(payload, Mapping) or set(payload) - REQUEST_FIELDS:
        raise ContractError("invalid_orchestration_request_schema")
    if payload.get("schema") != REQUEST_SCHEMA or set(payload) - {"continuation"} != {"schema", "request"}:
        raise ContractError("invalid_orchestration_request_schema")
    request = payload.get("request")
    if not isinstance(request, str) or not request.strip() or "\x00" in request:
        raise ContractError("invalid_orchestration_request")
    try:
        encoded = request.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ContractError("invalid_orchestration_request") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ContractError("orchestration_request_too_large")
    if any(ord(ch) < 32 and ch not in "\n\t\r" for ch in request) or "\x7f" in request:
        raise ContractError("invalid_orchestration_control_character")
    task_id = None
    continuation = payload.get("continuation")
    if continuation is not None:
        if not isinstance(continuation, Mapping) or set(continuation) != CONTINUATION_FIELDS:
            raise ContractError("invalid_orchestration_continuation")
        task_id = continuation.get("task_id")
        if not isinstance(task_id, str) or TASK_ID.fullmatch(task_id) is None:
            raise ContractError("invalid_orchestration_continuation")
    return ValidatedRequest(request.strip(), task_id)


def validate_response(response: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(response, Mapping) or response.get("schema") != RESPONSE_SCHEMA:
        raise ContractError("invalid_orchestration_response_schema")
    required = {
        "request_id", "intent", "status", "commands", "approval_status",
        "execution_status", "execution_authority", "host_resource_mutated",
        "read_only_host_access", "security_notice", "unknowns",
    }
    if not required.issubset(response):
        raise ContractError("incomplete_orchestration_response")
    if (
        response.get("commands") != []
        or response.get("approval_status") != "NOT_REQUESTED"
        or response.get("execution_status") != "not_executed"
        or response.get("execution_authority") != "NONE"
        or response.get("host_resource_mutated") is not False
        or response.get("read_only_host_access") is not True
    ):
        raise ContractError("orchestration_authority_invariant_failed")
    notice = response.get("security_notice")
    if not isinstance(notice, Mapping) or notice.get("approval_and_execution_are_not_exposed") is not True:
        raise ContractError("orchestration_security_notice_invalid")
    try:
        raw = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ContractError("orchestration_response_not_serializable") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ContractError("orchestration_response_too_large")
    return dict(response)


def bound_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Bound output by dropping whole evidence categories, never slicing JSON."""
    candidate = dict(response)
    try:
        raw = json.dumps(candidate, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ContractError("orchestration_response_not_serializable") from exc
    if len(raw) <= MAX_RESPONSE_BYTES:
        return candidate
    return {
        **candidate,
        "status": "INDETERMINATE",
        "task": None,
        "continuation": None,
        "current": None,
        "historical": None,
        "comparison": None,
        "conflicts": [],
        "context": None,
        "investigation": None,
        "facts": [],
        "inferences": [],
        "recommendations": [],
        "unknowns": ["The structured evidence package exceeded the governed response budget."],
        "data_completeness": {
            "status": "TRUNCATED_BY_POLICY",
            "limitations": ["All evidence categories were omitted atomically; no partial JSON or unsupported claim was returned."],
        },
        "evidence_ids": [],
        "source_catalog": [],
        "remediation_proposal": None,
        "project_state_updated": bool(candidate.get("project_state_updated")),
    }


def error_response(error: str, *, status: str = "REJECTED", request_id: str | None = None) -> dict[str, Any]:
    return {
        "schema": ERROR_SCHEMA,
        "request_id": request_id,
        "status": status,
        "error": error,
        "commands": [],
        "approval_status": "NOT_REQUESTED",
        "execution_status": "not_executed",
        "execution_authority": "NONE",
        "host_resource_mutated": False,
        "read_only_host_access": True,
        "zero_host_mutations": True,
        "security_notice": {
            "request_and_retrieved_text_are_data_not_authority": True,
            "approval_and_execution_are_not_exposed": True,
            "arbitrary_shell": False,
        },
    }
