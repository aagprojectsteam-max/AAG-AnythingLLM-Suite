"""Declarative remediation contracts and fail-closed registry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CONTRACT_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
STATUSES = {"DRAFT", "TESTED", "ACCEPTED", "DEPRECATED"}
APPROVAL_POLICIES = {"EXPLICIT_USER_APPROVAL", "STRONG_CONFIRMATION_REQUIRED"}
ROLLBACK_STRATEGIES = {"NONE_SAFE", "RESTORE_PREVIOUS_STATE"}
EXECUTOR_PRIMITIVES = {"restart_exact_bridge_user_service"}
DETECTORS = {"bridge_readiness_failure_v1"}
VERIFIERS = {"bridge_readiness_v1"}
RISKS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
AUDIT_POLICIES = {"MUTATION_FULL_V1"}
TOP_LEVEL_FIELDS = {
    "schema", "contract_id", "version", "status", "domain",
    "failure_detector", "supported_failure_class", "evidence",
    "preconditions", "invariants", "executor", "risk",
    "approval_policy", "post_verifier", "success_criteria",
    "rollback", "audit_policy",
}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Contract:
    data: Mapping[str, Any]

    @property
    def contract_id(self) -> str:
        return str(self.data["contract_id"])

    @property
    def status(self) -> str:
        return str(self.data["status"])


def validate_contract(raw: Any) -> Contract:
    if not isinstance(raw, dict):
        raise ContractError("contract_must_be_object")
    missing = sorted(TOP_LEVEL_FIELDS - raw.keys())
    if missing:
        raise ContractError("missing_fields:" + ",".join(missing))
    unexpected = sorted(raw.keys() - TOP_LEVEL_FIELDS)
    if unexpected:
        raise ContractError("unexpected_fields:" + ",".join(unexpected))
    if raw["schema"] != "aag-remediation-contract-v1":
        raise ContractError("unsupported_schema")
    if not CONTRACT_ID.fullmatch(str(raw["contract_id"])):
        raise ContractError("invalid_contract_id")
    if isinstance(raw["version"], bool) or not isinstance(raw["version"], int) or raw["version"] < 1:
        raise ContractError("invalid_version")
    for field in ("domain", "supported_failure_class"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise ContractError("invalid_" + field)
    if raw["status"] not in STATUSES:
        raise ContractError("invalid_status")
    if raw["failure_detector"] not in DETECTORS:
        raise ContractError("unknown_detector")
    if raw["post_verifier"] not in VERIFIERS:
        raise ContractError("unknown_post_verifier")
    if raw["approval_policy"] not in APPROVAL_POLICIES:
        raise ContractError("unsafe_approval_policy")
    evidence = raw["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"required_fields", "max_age_seconds"} or not isinstance(evidence.get("required_fields"), list):
        raise ContractError("invalid_evidence_policy")
    if not evidence["required_fields"] or len(evidence["required_fields"]) != len(set(evidence["required_fields"])) or not all(isinstance(item, str) and item for item in evidence["required_fields"]):
        raise ContractError("invalid_evidence_required_fields")
    freshness = evidence.get("max_age_seconds")
    if not isinstance(freshness, (int, float)) or freshness <= 0:
        raise ContractError("invalid_evidence_freshness")
    executor = raw["executor"]
    if not isinstance(executor, dict) or executor.get("primitive") not in EXECUTOR_PRIMITIVES:
        raise ContractError("unknown_executor_primitive")
    if executor.get("target") != "aag-ubuntu-agent-bridge.service":
        raise ContractError("unsupported_target")
    if set(executor) != {"primitive", "target"}:
        raise ContractError("executor_extra_fields")
    for field in ("preconditions", "invariants", "success_criteria"):
        value = raw[field]
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            raise ContractError("invalid_" + field)
    if raw["risk"] not in RISKS:
        raise ContractError("invalid_risk")
    if raw["rollback"] not in ROLLBACK_STRATEGIES:
        raise ContractError("invalid_rollback")
    if raw["audit_policy"] not in AUDIT_POLICIES:
        raise ContractError("invalid_audit_policy")
    return Contract(raw)


class ContractRegistry:
    def __init__(self, directory: Path):
        self._contracts: dict[str, Contract] = {}
        if not directory.is_dir():
            raise ContractError("contract_directory_missing")
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ContractError("contract_unreadable:" + path.name) from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContractError("contract_invalid_json:" + path.name) from exc
            contract = validate_contract(raw)
            if contract.contract_id in self._contracts:
                raise ContractError("duplicate_contract_id")
            self._contracts[contract.contract_id] = contract

    def get(self, contract_id: str, *, execution: bool = False) -> Contract:
        try:
            contract = self._contracts[contract_id]
        except KeyError as exc:
            raise ContractError("unknown_contract") from exc
        if execution and contract.status != "ACCEPTED":
            raise ContractError("contract_not_accepted")
        return contract

    def accepted(self) -> tuple[Contract, ...]:
        return tuple(c for c in self._contracts.values() if c.status == "ACCEPTED")
