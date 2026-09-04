"""Governed Safe Remediation and Verification Engine V1."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from aag_agent.audit import append_event as append_host_audit
from aag_agent.contracts import ContractRegistry
from aag_agent.detectors import BRIDGE_TARGET
from aag_agent.policy import evaluate as evaluate_contract_policy

from .bridge import BridgeObservationProvider, ExactBridgeRestartExecutor, ExactTargetLock
from .context_link import ContextLinker
from .models import canonical_json, parse_utc, sha256_json, stable_id, utc_now
from .registry import OperationRegistry, OperationRegistryError
from .store import RemediationStore, RemediationStoreError

PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
DEFAULT_HOST_AUDIT = PROJECT_ROOT / "runtime/audit/mutations.jsonl"
DEFAULT_LOCK = PROJECT_ROOT / "runtime/remediation/bridge-restart.lock"

PLAN_FIELDS = {
    "schema", "plan_nonce_hash", "operation_id", "operation_version",
    "registry_hash", "contract_id", "contract_version", "target_type",
    "target_identity", "context_plan_id", "task_id", "incident_id",
    "evidence", "evidence_set_hash", "live_evidence",
    "precondition_spec", "precondition_spec_hash", "precondition_fingerprint",
    "backup_policy", "backup_policy_hash", "executor_primitive",
    "post_verifier", "risk_class", "approval_class", "rollback",
    "created_at", "execution_authority",
}


class RemediationEngineError(ValueError):
    pass


def _token_hash(token: str) -> str:
    if not isinstance(token, str) or len(token) < 24 or len(token) > 256:
        raise RemediationEngineError("invalid_approval_token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _operator(operator_id: str) -> str:
    if not isinstance(operator_id, str) or not 2 <= len(operator_id) <= 128:
        raise RemediationEngineError("invalid_operator_identity")
    if any(ord(char) < 32 for char in operator_id):
        raise RemediationEngineError("invalid_operator_identity")
    return operator_id


def _precondition_view(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: evidence.get(key)
        for key in (
            "target", "load_state", "active_state", "sub_state", "main_pid",
            "health_ready", "health_error", "classification", "supported_failure_class",
        )
    }


def evaluate_backup_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(policy, Mapping) or set(policy) != {"class", "justification", "restore_test_required"}:
        return {"status": "BLOCKED", "error": "backup_policy_invalid", "backup_created": False}
    if policy["class"] == "NO_BACKUP_REQUIRED_WITH_JUSTIFICATION":
        if isinstance(policy["justification"], str) and policy["justification"] and policy["restore_test_required"] is False:
            return {"status": "VERIFIED", "error": None, "backup_created": False}
        return {"status": "BLOCKED", "error": "backup_justification_missing", "backup_created": False}
    if policy["class"] == "BACKUP_REQUIRED":
        return {"status": "BLOCKED", "error": "typed_backup_primitive_unavailable", "backup_created": False}
    return {"status": "BLOCKED", "error": "backup_policy_unknown", "backup_created": False}


class RemediationEngine:
    def __init__(
        self,
        *,
        registry: OperationRegistry,
        store: RemediationStore,
        context_linker: ContextLinker,
        observer: Any | None = None,
        executor: Any | None = None,
        contracts: ContractRegistry | None = None,
        host_audit: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        lock_path: Path = DEFAULT_LOCK,
        now: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.context = context_linker
        self.observer = observer or BridgeObservationProvider()
        self.executor = executor or ExactBridgeRestartExecutor()
        self.contracts = contracts or ContractRegistry(PROJECT_ROOT / "contracts")
        self.host_audit = host_audit or (
            lambda contract_id, event, details: append_host_audit(
                DEFAULT_HOST_AUDIT, contract_id, event, details
            )
        )
        self.lock_path = Path(lock_path)
        self.now = now
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    def initialize(self) -> dict[str, Any]:
        migration = self.store.migrate()
        return {
            "schema": "aag-safe-remediation-initialization-v1",
            "migration": migration,
            "integrity": self.store.integrity(),
            "registry_sha256": self.registry.sha256,
            "operation_count": len(self.registry.list()),
            "execution_authority": "NONE",
        }

    def operation_list(self) -> dict[str, Any]:
        return {
            "schema": "aag-remediation-operation-list-v1",
            "registry_sha256": self.registry.sha256,
            "operations": self.registry.list(),
            "execution_authority": "NONE",
        }

    def operation_show(self, operation_id: str, version: int) -> dict[str, Any]:
        operation = self.registry.get(operation_id, version)
        return {
            "schema": "aag-remediation-operation-v1",
            "registry_sha256": self.registry.sha256,
            "operation": dict(operation.data),
            "execution_authority": "NONE",
        }

    def _evaluate_evidence(self, operation, evidence: Mapping[str, Any]) -> dict[str, Any]:
        required = operation.data["required_evidence"]
        missing = sorted(set(required["required_fields"]) - set(evidence))
        if missing:
            return {"allowed": False, "errors": ["missing_evidence:" + item for item in missing], "classification": "MISSING", "execution_authority": "NONE"}
        if evidence.get("schema") != required["schema"] or evidence.get("classification") != required["classification"]:
            return {"allowed": False, "errors": ["required_evidence_classification_not_met"], "classification": evidence.get("classification"), "execution_authority": "NONE"}
        try:
            contract = self.contracts.get(operation.data["contract_id"], execution=True)
        except Exception as exc:
            return {"allowed": False, "errors": ["accepted_contract_unavailable", type(exc).__name__], "execution_authority": "NONE"}
        if contract.data["version"] != operation.data["contract_version"]:
            return {"allowed": False, "errors": ["contract_version_binding_mismatch"], "execution_authority": "NONE"}
        result = evaluate_contract_policy(contract, evidence, now=self.now())
        if contract.data["evidence"]["max_age_seconds"] != required["max_age_seconds"]:
            result = {**result, "allowed": False, "errors": sorted(set(result.get("errors", [])) | {"evidence_ttl_binding_mismatch"}), "execution_authority": "NONE"}
        return result

    @staticmethod
    def _validate_plan_object(plan: Mapping[str, Any]) -> None:
        if not isinstance(plan, Mapping) or set(plan) != PLAN_FIELDS:
            raise RemediationEngineError("stored_plan_fields_invalid")
        if plan.get("schema") != "aag-governed-remediation-plan-v1":
            raise RemediationEngineError("stored_plan_schema_invalid")
        if plan.get("execution_authority") != "NONE":
            raise RemediationEngineError("stored_plan_authority_invalid")

    def _stored_plan(self, plan_id: str) -> dict[str, Any]:
        record = self.store.get_plan(plan_id)
        plan = record["plan"]
        self._validate_plan_object(plan)
        if sha256_json(plan) != record["plan_hash"]:
            raise RemediationEngineError("stored_plan_hash_mismatch")
        if plan["registry_hash"] != record["registry_hash"] or plan["registry_hash"] != self.registry.sha256:
            raise RemediationEngineError("registry_changed_since_plan")
        operation = self.registry.get(plan["operation_id"], plan["operation_version"], execution=True)
        registry_bindings = {
            "contract_id": operation.data["contract_id"],
            "contract_version": operation.data["contract_version"],
            "target_type": operation.data["target_type"],
            "target_identity": operation.target,
            "precondition_spec": operation.data["preconditions"],
            "precondition_spec_hash": sha256_json(operation.data["preconditions"]),
            "backup_policy": operation.data["backup_policy"],
            "backup_policy_hash": sha256_json(operation.data["backup_policy"]),
            "executor_primitive": operation.data["executor"]["primitive"],
            "post_verifier": operation.data["post_verifier"],
            "risk_class": operation.risk,
            "approval_class": operation.approval_class,
            "rollback": operation.data["rollback"],
        }
        if any(plan.get(key) != value for key, value in registry_bindings.items()):
            raise RemediationEngineError("stored_plan_registry_binding_mismatch")
        if plan.get("precondition_fingerprint") != sha256_json(_precondition_view(plan["live_evidence"])):
            raise RemediationEngineError("stored_plan_precondition_binding_mismatch")
        if plan.get("evidence_set_hash") != sha256_json(plan["evidence"]):
            raise RemediationEngineError("stored_plan_evidence_hash_mismatch")
        stored_evidence = [
            {
                "artifact_id": item["artifact_id"],
                "original_sha256": item["artifact_sha256"],
                "verification_level": item["verification_level"],
                "evidence_role": item["evidence_role"],
            }
            for item in record["evidence"]
        ]
        if plan["evidence"] != stored_evidence:
            raise RemediationEngineError("stored_plan_evidence_rows_mismatch")
        fields = {
            "operation_id": plan["operation_id"],
            "operation_version": plan["operation_version"],
            "target_identity": plan["target_identity"],
            "context_plan_id": plan["context_plan_id"],
            "task_id": plan["task_id"],
            "incident_id": plan["incident_id"],
            "evidence_set_hash": plan["evidence_set_hash"],
            "precondition_spec_hash": plan["precondition_spec_hash"],
            "backup_policy_hash": plan["backup_policy_hash"],
        }
        if any(record[key] != value for key, value in fields.items()):
            raise RemediationEngineError("stored_plan_columns_mismatch")
        return record

    def prepare_plan(
        self,
        *,
        operation_id: str,
        operation_version: int,
        context_plan_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        operation = self.registry.get(operation_id, operation_version, execution=True)
        context_record = self.context.validate_context_plan(context_plan_id, task_id)
        evidence = self.observer.observe()
        policy = self._evaluate_evidence(operation, evidence)
        if evidence.get("classification") == "HEALTHY":
            return {
                "schema": "aag-governed-remediation-plan-unavailable-v1",
                "status": "not_needed",
                "error": "accepted_failure_not_present",
                "evidence": evidence,
                "execution_authority": "NONE",
                "executed": False,
                "mutated": False,
            }
        if not policy.get("allowed"):
            return {
                "schema": "aag-governed-remediation-plan-unavailable-v1",
                "status": "blocked",
                "error": "fresh_supported_failure_evidence_required",
                "evidence": evidence,
                "policy": policy,
                "execution_authority": "NONE",
                "executed": False,
                "mutated": False,
            }
        live_artifact = self.context.record_live_evidence(evidence)
        all_artifacts: dict[str, dict[str, str]] = {
            item["artifact_id"]: {
                "artifact_id": item["artifact_id"],
                "original_sha256": item["original_sha256"],
                "verification_level": item["verification_level"],
                "evidence_role": "context_plan",
            }
            for item in context_record["evidence"]
        }
        all_artifacts[live_artifact["artifact_id"]] = {
            **live_artifact,
            "evidence_role": "fresh_precondition",
        }
        evidence_bindings = [all_artifacts[key] for key in sorted(all_artifacts)]
        evidence_set_hash = sha256_json(evidence_bindings)
        precondition_spec_hash = sha256_json(operation.data["preconditions"])
        backup_policy_hash = sha256_json(operation.data["backup_policy"])
        plan_nonce = self.token_factory()
        plan_nonce_hash = _token_hash(plan_nonce)
        incident_id = stable_id("incident", operation_id, context_plan_id, plan_nonce_hash)
        created_at = utc_now()
        plan = {
            "schema": "aag-governed-remediation-plan-v1",
            "plan_nonce_hash": plan_nonce_hash,
            "operation_id": operation.operation_id,
            "operation_version": operation.version,
            "registry_hash": operation.registry_sha256,
            "contract_id": operation.data["contract_id"],
            "contract_version": operation.data["contract_version"],
            "target_type": operation.data["target_type"],
            "target_identity": operation.target,
            "context_plan_id": context_plan_id,
            "task_id": task_id,
            "incident_id": incident_id,
            "evidence": evidence_bindings,
            "evidence_set_hash": evidence_set_hash,
            "live_evidence": dict(evidence),
            "precondition_spec": operation.data["preconditions"],
            "precondition_spec_hash": precondition_spec_hash,
            "precondition_fingerprint": sha256_json(_precondition_view(evidence)),
            "backup_policy": operation.data["backup_policy"],
            "backup_policy_hash": backup_policy_hash,
            "executor_primitive": operation.data["executor"]["primitive"],
            "post_verifier": operation.data["post_verifier"],
            "risk_class": operation.risk,
            "approval_class": operation.approval_class,
            "rollback": operation.data["rollback"],
            "created_at": created_at,
            "execution_authority": "NONE",
        }
        self._validate_plan_object(plan)
        plan_hash = sha256_json(plan)
        plan_id = stable_id("governed-plan", plan_hash)
        self.context.ensure_incident(
            incident_id,
            "AAG Bridge active/running but fixed health endpoint unready",
        )
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT INTO plans
                   (plan_id,plan_hash,registry_hash,operation_id,operation_version,
                    target_identity,context_plan_id,task_id,incident_id,evidence_set_hash,
                    precondition_spec_hash,backup_policy_hash,plan_json,state,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'PROPOSED',?,?)""",
                (
                    plan_id, plan_hash, operation.registry_sha256, operation.operation_id,
                    operation.version, operation.target, context_plan_id, task_id, incident_id,
                    evidence_set_hash, precondition_spec_hash, backup_policy_hash,
                    canonical_json(plan), created_at, created_at,
                ),
            )
            for item in evidence_bindings:
                connection.execute(
                    """INSERT INTO plan_evidence
                       (plan_id,artifact_id,artifact_sha256,verification_level,evidence_role)
                       VALUES (?,?,?,?,?)""",
                    (plan_id, item["artifact_id"], item["original_sha256"], item["verification_level"], item["evidence_role"]),
                )
            self.store.append_event(
                connection,
                plan_id,
                "PLAN_PROPOSED",
                {"plan_hash": plan_hash, "operation_id": operation.operation_id, "operation_version": operation.version, "target_identity": operation.target},
            )
            self.store.transition(
                connection,
                plan_id,
                "VALIDATED",
                "PLAN_VALIDATED",
                {"registry_hash": operation.registry_sha256, "evidence_set_hash": evidence_set_hash, "policy": policy},
            )
        return {
            "schema": "aag-governed-remediation-plan-result-v1",
            "status": "VALIDATED",
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "plan": plan,
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    def request_approval(self, plan_id: str, *, ttl_seconds: int = 600) -> dict[str, Any]:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 30 <= ttl_seconds <= 900:
            raise RemediationEngineError("invalid_approval_ttl")
        record = self._stored_plan(plan_id)
        if record["state"] != "VALIDATED":
            raise RemediationEngineError("plan_not_validated_for_approval")
        operation = self.registry.get(record["operation_id"], record["operation_version"], execution=True)
        token = self.token_factory()
        nonce_hash = _token_hash(token)
        requested = datetime.fromtimestamp(self.now(), timezone.utc)
        expires = requested + timedelta(seconds=ttl_seconds)
        approval_id = stable_id("approval", plan_id, nonce_hash)
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT INTO approvals
                   (approval_id,plan_id,nonce_hash,operator_id,decision,plan_hash,registry_hash,
                   operation_id,operation_version,target_identity,evidence_set_hash,
                    precondition_spec_hash,backup_policy_hash,risk_class,approval_class,
                    requested_at,expires_at,recorded_at,consumed_at,state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    approval_id, plan_id, nonce_hash, None, None,
                    record["plan_hash"], record["registry_hash"],
                    operation.operation_id, operation.version, operation.target,
                    record["evidence_set_hash"], record["precondition_spec_hash"], record["backup_policy_hash"],
                    operation.risk, operation.approval_class,
                    requested.isoformat().replace("+00:00", "Z"), expires.isoformat().replace("+00:00", "Z"),
                    None, None, "PENDING",
                ),
            )
            self.store.transition(
                connection,
                plan_id,
                "AWAITING_APPROVAL",
                "APPROVAL_REQUESTED",
                {
                    "approval_id": approval_id,
                    "nonce_hash": nonce_hash,
                    "expires_at": expires.isoformat().replace("+00:00", "Z"),
                    "risk_class": operation.risk,
                    "approval_class": operation.approval_class,
                },
            )
        return {
            "schema": "aag-remediation-approval-request-v1",
            "approval_id": approval_id,
            "plan_id": plan_id,
            "approval_token": token,
            "token_displayed_once": True,
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
            "risk_class": operation.risk,
            "approval_class": operation.approval_class,
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    def _approval_row(self, approval_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            raise RemediationEngineError("approval_not_found")
        return dict(row)

    def record_approval(
        self,
        approval_id: str,
        *,
        token: str,
        operator_id: str,
        decision: str,
    ) -> dict[str, Any]:
        operator_id = _operator(operator_id)
        if decision not in {"APPROVE", "REJECT"}:
            raise RemediationEngineError("invalid_approval_decision")
        approval = self._approval_row(approval_id)
        record = self._stored_plan(approval["plan_id"])
        if approval["state"] != "PENDING" or record["state"] != "AWAITING_APPROVAL":
            raise RemediationEngineError("approval_not_pending")
        if _token_hash(token) != approval["nonce_hash"]:
            raise RemediationEngineError("approval_token_mismatch")
        now = datetime.fromtimestamp(self.now(), timezone.utc)
        if now > parse_utc(approval["expires_at"]):
            with self.store.transaction() as connection:
                connection.execute("UPDATE approvals SET state='EXPIRED',recorded_at=? WHERE approval_id=?", (utc_now(), approval_id))
                self.store.transition(connection, record["plan_id"], "ABORTED_APPROVAL", "APPROVAL_EXPIRED", {"approval_id": approval_id})
            raise RemediationEngineError("approval_expired")
        state = "APPROVED" if decision == "APPROVE" else "REJECTED"
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE approvals SET operator_id=?,decision=?,recorded_at=?,state=? WHERE approval_id=?",
                (operator_id, decision, utc_now(), state, approval_id),
            )
            if decision == "APPROVE":
                self.store.transition(connection, record["plan_id"], "APPROVED", "APPROVAL_RECORDED", {"approval_id": approval_id, "operator_id": operator_id, "decision": decision})
            else:
                self.store.transition(connection, record["plan_id"], "ABORTED_APPROVAL", "APPROVAL_REJECTED", {"approval_id": approval_id, "operator_id": operator_id, "decision": decision})
        return {
            "schema": "aag-remediation-approval-record-v1",
            "approval_id": approval_id,
            "plan_id": record["plan_id"],
            "state": state,
            "operator_id": operator_id,
            "execution_authority": "EXACT_PLAN_BOUND_APPROVAL" if state == "APPROVED" else "NONE",
            "executed": False,
            "mutated": False,
        }

    def _binding_errors(self, record: Mapping[str, Any], approval: Mapping[str, Any], operation) -> list[str]:
        expected = {
            "plan_hash": record["plan_hash"],
            "registry_hash": self.registry.sha256,
            "operation_id": operation.operation_id,
            "operation_version": operation.version,
            "target_identity": operation.target,
            "evidence_set_hash": record["evidence_set_hash"],
            "precondition_spec_hash": record["precondition_spec_hash"],
            "backup_policy_hash": record["backup_policy_hash"],
            "risk_class": operation.risk,
            "approval_class": operation.approval_class,
        }
        return sorted(key + "_binding_mismatch" for key, value in expected.items() if approval.get(key) != value)

    def check_preconditions(self, plan_id: str) -> dict[str, Any]:
        record = self._stored_plan(plan_id)
        operation = self.registry.get(record["operation_id"], record["operation_version"], execution=True)
        evidence = self.observer.observe()
        policy = self._evaluate_evidence(operation, evidence)
        fingerprint = sha256_json(_precondition_view(evidence))
        expected = record["plan"]["precondition_fingerprint"]
        passed = bool(policy.get("allowed")) and fingerprint == expected
        return {
            "schema": "aag-remediation-precondition-result-v1",
            "status": "PASS" if passed else "FAIL",
            "plan_id": plan_id,
            "precondition_hash": fingerprint,
            "expected_precondition_hash": expected,
            "evidence": evidence,
            "policy": policy,
            "error": None if passed else "ABORT_PRECONDITION_CHANGED",
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }

    def backup_status(self, plan_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        record = self._stored_plan(plan_id)
        policy = record["plan"]["backup_policy"]
        evaluation = evaluate_backup_policy(policy)
        return {
            "schema": "aag-remediation-backup-result-v1",
            "plan_id": plan_id,
            "status": evaluation["status"],
            "policy": policy,
            "backup_policy_hash": record["backup_policy_hash"],
            "dry_run": bool(dry_run),
            "backup_created": evaluation["backup_created"],
            "error": evaluation["error"],
            "mutated": False,
            "execution_authority": "NONE",
        }

    def _abort_stale(self, record: Mapping[str, Any], approval_id: str, precondition: Mapping[str, Any]) -> dict[str, Any]:
        with self.store.transaction() as connection:
            connection.execute("UPDATE approvals SET state='INVALIDATED',consumed_at=? WHERE approval_id=?", (utc_now(), approval_id))
            self.store.transition(connection, record["plan_id"], "ABORTED_STALE_STATE", "PRECONDITION_CHANGED", {"approval_id": approval_id, "precondition": dict(precondition)})
        return {
            "schema": "aag-remediation-attempt-result-v1",
            "status": "ABORTED_STALE_STATE",
            "error": "ABORT_PRECONDITION_CHANGED",
            "plan_id": record["plan_id"],
            "approval_id": approval_id,
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    def execute(self, plan_id: str, approval_id: str, *, token: str, operator_id: str) -> dict[str, Any]:
        operator_id = _operator(operator_id)
        record = self._stored_plan(plan_id)
        approval = self._approval_row(approval_id)
        if approval["plan_id"] != plan_id or approval["state"] == "CONSUMED":
            raise RemediationEngineError("approval_replay_or_plan_mismatch")
        if approval["state"] != "APPROVED" or record["state"] != "APPROVED":
            raise RemediationEngineError("approved_state_required")
        if approval["operator_id"] != operator_id:
            raise RemediationEngineError("approval_operator_mismatch")
        if _token_hash(token) != approval["nonce_hash"]:
            raise RemediationEngineError("approval_token_mismatch")
        if datetime.fromtimestamp(self.now(), timezone.utc) > parse_utc(approval["expires_at"]):
            with self.store.transaction() as connection:
                connection.execute("UPDATE approvals SET state='EXPIRED',consumed_at=? WHERE approval_id=?", (utc_now(), approval_id))
                self.store.transition(connection, plan_id, "ABORTED_APPROVAL", "APPROVAL_EXPIRED", {"approval_id": approval_id})
            raise RemediationEngineError("approval_expired")
        operation = self.registry.get(record["operation_id"], record["operation_version"], execution=True)
        binding_errors = self._binding_errors(record, approval, operation)
        if binding_errors:
            with self.store.transaction() as connection:
                connection.execute("UPDATE approvals SET state='INVALIDATED',consumed_at=? WHERE approval_id=?", (utc_now(), approval_id))
                self.store.transition(connection, plan_id, "ABORTED_APPROVAL", "APPROVAL_BINDING_INVALID", {"errors": binding_errors})
            raise RemediationEngineError("approval_binding_invalid:" + ",".join(binding_errors))

        try:
            lock = ExactTargetLock(self.lock_path)
            lock.__enter__()
        except RuntimeError as exc:
            return {"schema": "aag-remediation-attempt-result-v1", "status": "blocked", "error": str(exc), "executed": False, "mutated": False, "execution_authority": "NONE"}
        try:
            precondition = self.check_preconditions(plan_id)
            if precondition["status"] != "PASS":
                return self._abort_stale(record, approval_id, precondition)
            backup = self.backup_status(plan_id, dry_run=False)
            if backup["status"] != "VERIFIED":
                return self._abort_stale(record, approval_id, {"error": "backup_not_verified", "backup": backup})
            attempt_number = 1
            attempt_id = stable_id("remediation-attempt", plan_id, attempt_number, approval_id)
            with self.store.transaction() as connection:
                self.store.transition(connection, plan_id, "PRECONDITION_VERIFIED", "PRECONDITION_VERIFIED", {"precondition_hash": precondition["precondition_hash"]})
                self.store.transition(connection, plan_id, "BACKUP_VERIFIED", "BACKUP_VERIFIED", {"backup": backup})
                connection.execute("UPDATE approvals SET state='CONSUMED',consumed_at=? WHERE approval_id=?", (utc_now(), approval_id))
                connection.execute(
                    """INSERT INTO attempts
                       (attempt_id,plan_id,approval_id,attempt_number,precondition_hash,
                        backup_record_json,execution_result_json,verification_result_json,
                        host_audit_start_hash,host_audit_finish_hash,audit_status,outcome,
                        started_at,completed_at)
                       VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,'PENDING','PENDING',?,NULL)""",
                    (attempt_id, plan_id, approval_id, attempt_number, precondition["precondition_hash"], canonical_json(backup), utc_now()),
                )
                self.store.append_event(connection, plan_id, "APPROVAL_CONSUMED", {"approval_id": approval_id, "attempt_id": attempt_id, "operator_id": operator_id})
            try:
                audit_start = self.host_audit(
                    operation.data["contract_id"],
                    "remediation_execution_started",
                    {
                        "plan_id": plan_id,
                        "attempt_id": attempt_id,
                        "plan_hash": record["plan_hash"],
                        "registry_hash": record["registry_hash"],
                        "operation_id": operation.operation_id,
                        "operation_version": operation.version,
                        "target_identity": operation.target,
                        "approval_id": approval_id,
                        "approval_token_sha256": approval["nonce_hash"],
                        "operator_id": operator_id,
                        "precondition_hash": precondition["precondition_hash"],
                        "backup_policy_hash": record["backup_policy_hash"],
                    },
                )
            except Exception as exc:
                with self.store.transaction() as connection:
                    connection.execute("UPDATE attempts SET audit_status='PRE_WRITE_FAILED',outcome='ABORTED_AUDIT',completed_at=? WHERE attempt_id=?", (utc_now(), attempt_id))
                    self.store.transition(connection, plan_id, "ABORTED_AUDIT", "AUDIT_PREWRITE_FAILED", {"attempt_id": attempt_id, "error_type": type(exc).__name__})
                return {"schema": "aag-remediation-attempt-result-v1", "status": "ABORTED_AUDIT", "error": "pre_execution_audit_persistence_failed", "attempt_id": attempt_id, "executed": False, "mutated": False, "execution_authority": "NONE"}
            with self.store.transaction() as connection:
                connection.execute("UPDATE attempts SET host_audit_start_hash=?,audit_status='STARTED_PERSISTED' WHERE attempt_id=?", (audit_start["record_hash"], attempt_id))
                self.store.transition(connection, plan_id, "EXECUTING", "EXECUTION_STARTED", {"attempt_id": attempt_id, "host_audit_start_hash": audit_start["record_hash"]})
            execution = self.executor.execute(operation)
            outcome: str
            verification: dict[str, Any] | None = None
            if execution["status"] == "EXECUTION_OK":
                with self.store.transaction() as connection:
                    self.store.transition(connection, plan_id, "VERIFYING", "EXECUTION_FINISHED_VERIFYING", {"attempt_id": attempt_id, "execution": execution})
                verification = self.observer.verify(str(precondition["evidence"].get("main_pid") or "0"))
                if verification.get("status") == "PASS":
                    outcome = "SUCCEEDED_VERIFIED"
                elif verification.get("status") == "FAILED":
                    outcome = "FAILED_VERIFICATION"
                else:
                    outcome = "INDETERMINATE"
            elif execution["status"] == "FAILED_EXECUTION":
                outcome = "FAILED_EXECUTION"
            else:
                outcome = "INDETERMINATE"
            post_audit_error = None
            try:
                audit_finish = self.host_audit(
                    operation.data["contract_id"],
                    "remediation_execution_finished",
                    {
                        "plan_id": plan_id,
                        "attempt_id": attempt_id,
                        "operation_id": operation.operation_id,
                        "operation_version": operation.version,
                        "target_identity": operation.target,
                        "outcome": outcome,
                        "executed": bool(execution.get("executed")),
                        "mutated": bool(execution.get("mutated")),
                        "post_verified": outcome == "SUCCEEDED_VERIFIED",
                        "verification": verification,
                        "rollback_capability": operation.data["rollback"]["capability"],
                    },
                )
            except Exception as exc:
                audit_finish = None
                post_audit_error = {"error": "post_execution_audit_persistence_failed", "error_type": type(exc).__name__}
            final_state = outcome
            with self.store.transaction() as connection:
                current = connection.execute("SELECT state FROM plans WHERE plan_id=?", (plan_id,)).fetchone()["state"]
                if current == "EXECUTING" and outcome in {"FAILED_EXECUTION", "INDETERMINATE"}:
                    self.store.transition(connection, plan_id, outcome, "EXECUTION_OUTCOME", {"attempt_id": attempt_id, "execution": execution})
                elif current == "VERIFYING" and outcome in {"SUCCEEDED_VERIFIED", "FAILED_VERIFICATION", "INDETERMINATE"}:
                    self.store.transition(connection, plan_id, outcome, "VERIFICATION_OUTCOME", {"attempt_id": attempt_id, "verification": verification})
                connection.execute(
                    """UPDATE attempts SET execution_result_json=?,verification_result_json=?,
                       host_audit_finish_hash=?,audit_status=?,outcome=?,completed_at=? WHERE attempt_id=?""",
                    (
                        canonical_json(execution), canonical_json(verification) if verification is not None else None,
                        audit_finish["record_hash"] if audit_finish else None,
                        "COMPLETE" if audit_finish else "POST_WRITE_FAILED",
                        outcome, utc_now(), attempt_id,
                    ),
                )
                self.store.append_event(
                    connection,
                    plan_id,
                    "ATTEMPT_FINALIZED",
                    {"attempt_id": attempt_id, "outcome": outcome, "host_audit_finish_hash": audit_finish["record_hash"] if audit_finish else None, "post_audit_error": post_audit_error},
                )
            reconciliation = None
            reconciliation_error = None
            if audit_finish is not None:
                try:
                    reconciliation = self.context.record_outcome(
                        plan={**record["plan"], "plan_id": plan_id},
                        attempt_id=attempt_id,
                        outcome=outcome,
                        result={"execution": execution, "verification": verification, "outcome": outcome},
                    )
                except Exception as exc:
                    reconciliation_error = {"error": "context_reconciliation_failed", "error_type": type(exc).__name__}
            return {
                "schema": "aag-remediation-attempt-result-v1",
                "status": final_state,
                "plan_id": plan_id,
                "attempt_id": attempt_id,
                "approval_id": approval_id,
                "execution": execution,
                "verification": verification,
                "audit": {
                    "status": "COMPLETE" if audit_finish else "POST_WRITE_FAILED",
                    "start_hash": audit_start["record_hash"],
                    "finish_hash": audit_finish["record_hash"] if audit_finish else None,
                    "error": post_audit_error,
                },
                "context_reconciliation": reconciliation,
                "context_reconciliation_error": reconciliation_error,
                "execution_authority": "CONSUMED_EXACT_PLAN_BOUND_APPROVAL",
                "executed": bool(execution.get("executed")),
                "mutated": bool(execution.get("mutated")),
                "post_verified": outcome == "SUCCEEDED_VERIFIED",
                "approval_consumed": True,
            }
        finally:
            lock.__exit__(None, None, None)

    def attempt_status(self, attempt_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise RemediationEngineError("attempt_not_found")
        result = dict(row)
        for field in ("backup_record_json", "execution_result_json", "verification_result_json"):
            result[field.removesuffix("_json")] = json.loads(result.pop(field)) if result[field] else None
        return {"schema": "aag-remediation-attempt-status-v1", **result, "execution_authority": "NONE"}

    def post_verify(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.attempt_status(attempt_id)
        execution = attempt.get("execution_result") or {}
        pre_pid = str((attempt.get("verification_result") or {}).get("pre_pid") or execution.get("pre_pid") or "0")
        result = self.observer.verify(pre_pid, attempts=3, interval_seconds=0.25)
        return {"schema": "aag-remediation-post-verification-v1", "attempt_id": attempt_id, "result": result, "read_only": True, "mutated": False, "execution_authority": "NONE"}

    def rollback_proposal(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.attempt_status(attempt_id)
        record = self._stored_plan(attempt["plan_id"])
        rollback = record["plan"]["rollback"]
        if attempt["outcome"] == "SUCCEEDED_VERIFIED":
            status = "NOT_REQUIRED"
            reason = "verified_success_has_no_rollback_need"
        elif rollback["capability"] == "NONE":
            status = "UNAVAILABLE"
            reason = rollback["justification"]
        else:
            status = "AWAITING_SEPARATE_AUTHORIZATION"
            reason = "rollback_requires_separate_exact_operation_authorization"
        proposal_id = stable_id("rollback-proposal", attempt_id, status)
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO rollback_proposals
                   (rollback_proposal_id,attempt_id,capability,approval_class,status,reason,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (proposal_id, attempt_id, rollback["capability"], rollback["approval_class"], status, reason, utc_now(), utc_now()),
            )
            current = connection.execute("SELECT state FROM plans WHERE plan_id=?", (record["plan_id"],)).fetchone()["state"]
            if status != "NOT_REQUIRED" and current in {"FAILED_EXECUTION", "FAILED_VERIFICATION", "INDETERMINATE"}:
                self.store.transition(connection, record["plan_id"], "ROLLBACK_PROPOSED", "ROLLBACK_PROPOSAL_RECORDED", {"rollback_proposal_id": proposal_id, "status": status})
        return {
            "schema": "aag-remediation-rollback-proposal-v1",
            "rollback_proposal_id": proposal_id,
            "attempt_id": attempt_id,
            "capability": rollback["capability"],
            "approval_class": rollback["approval_class"],
            "status": status,
            "reason": reason,
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    def rollback_status(self, rollback_proposal_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute("SELECT * FROM rollback_proposals WHERE rollback_proposal_id=?", (rollback_proposal_id,)).fetchone()
        if row is None:
            raise RemediationEngineError("rollback_proposal_not_found")
        return {"schema": "aag-remediation-rollback-status-v1", **dict(row), "execution_authority": "NONE"}

    def audit_verify(self) -> dict[str, Any]:
        return {
            "schema": "aag-remediation-audit-verification-v1",
            "remediation_store": self.store.integrity(),
            "execution_authority": "NONE",
        }
