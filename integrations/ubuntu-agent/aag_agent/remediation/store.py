"""Durable append-oriented state for Safe Remediation V1."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import PLAN_STATES, canonical_json, stable_id, utc_now, validate_transition

PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
DEFAULT_DATABASE = PROJECT_ROOT / "memory/safe-remediation-v1.sqlite3"
SCHEMA_VERSION = 1
MIGRATION_NAME = "safe_remediation_v1_initial"

_states = ",".join("'" + item + "'" for item in sorted(PLAN_STATES))
SCHEMA_SQL = f"""
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE plans (
    plan_id TEXT PRIMARY KEY,
    plan_hash TEXT NOT NULL UNIQUE CHECK(length(plan_hash)=64),
    registry_hash TEXT NOT NULL CHECK(length(registry_hash)=64),
    operation_id TEXT NOT NULL,
    operation_version INTEGER NOT NULL CHECK(operation_version>=1),
    target_identity TEXT NOT NULL,
    context_plan_id TEXT NOT NULL,
    task_id TEXT,
    incident_id TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL CHECK(length(evidence_set_hash)=64),
    precondition_spec_hash TEXT NOT NULL CHECK(length(precondition_spec_hash)=64),
    backup_policy_hash TEXT NOT NULL CHECK(length(backup_policy_hash)=64),
    plan_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ({_states})),
    event_count INTEGER NOT NULL DEFAULT 0 CHECK(event_count>=0),
    last_event_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE plan_evidence (
    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),
    verification_level TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    PRIMARY KEY(plan_id,artifact_id)
);
CREATE TABLE plan_events (
    event_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK(sequence>=1),
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    record_hash TEXT NOT NULL UNIQUE CHECK(length(record_hash)=64),
    created_at TEXT NOT NULL,
    UNIQUE(plan_id,sequence)
);
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE RESTRICT,
    nonce_hash TEXT NOT NULL UNIQUE CHECK(length(nonce_hash)=64),
    operator_id TEXT,
    decision TEXT,
    plan_hash TEXT NOT NULL CHECK(length(plan_hash)=64),
    registry_hash TEXT NOT NULL CHECK(length(registry_hash)=64),
    operation_id TEXT NOT NULL,
    operation_version INTEGER NOT NULL,
    target_identity TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL CHECK(length(evidence_set_hash)=64),
    precondition_spec_hash TEXT NOT NULL CHECK(length(precondition_spec_hash)=64),
    backup_policy_hash TEXT NOT NULL CHECK(length(backup_policy_hash)=64),
    risk_class TEXT NOT NULL,
    approval_class TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    recorded_at TEXT,
    consumed_at TEXT,
    state TEXT NOT NULL CHECK(state IN ('PENDING','APPROVED','REJECTED','CONSUMED','EXPIRED','INVALIDATED')),
    UNIQUE(plan_id) ON CONFLICT ABORT
);
CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE RESTRICT,
    approval_id TEXT NOT NULL REFERENCES approvals(approval_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK(attempt_number>=1),
    precondition_hash TEXT NOT NULL CHECK(length(precondition_hash)=64),
    backup_record_json TEXT NOT NULL,
    execution_result_json TEXT,
    verification_result_json TEXT,
    host_audit_start_hash TEXT,
    host_audit_finish_hash TEXT,
    audit_status TEXT NOT NULL,
    outcome TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(plan_id,attempt_number)
);
CREATE TABLE rollback_proposals (
    rollback_proposal_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    capability TEXT NOT NULL,
    approval_class TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('NOT_REQUIRED','UNAVAILABLE','AWAITING_SEPARATE_AUTHORIZATION','APPROVED','COMPLETED','FAILED')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_unfinished_attempt_per_target
ON plans(target_identity)
WHERE state IN ('APPROVED','PRECONDITION_VERIFIED','BACKUP_VERIFIED','EXECUTING','VERIFYING');
CREATE INDEX idx_plan_operation ON plans(operation_id,operation_version,created_at);
CREATE INDEX idx_event_plan ON plan_events(plan_id,sequence);
CREATE INDEX idx_attempt_plan ON attempts(plan_id,attempt_number);
"""
MIGRATION_CHECKSUM = hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest()


class RemediationStoreError(ValueError):
    pass


class RemediationStore:
    def __init__(self, path: Path = DEFAULT_DATABASE, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        if self.path.is_symlink():
            raise RemediationStoreError("database_symlink_forbidden")
        if not isinstance(busy_timeout_ms, int) or not 100 <= busy_timeout_ms <= 10000:
            raise RemediationStoreError("invalid_busy_timeout")

    def _connect(self, *, writable: bool) -> sqlite3.Connection:
        if not writable and not self.path.exists():
            raise RemediationStoreError("remediation_database_missing")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RemediationStoreError("database_symlink_forbidden")
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        if writable:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).casefold() != "wal":
                connection.close()
                raise RemediationStoreError("unsafe_journal_mode")
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    def migrate(self) -> dict[str, Any]:
        existed = self.path.exists()
        connection = self._connect(writable=True)
        try:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RemediationStoreError("database_schema_newer_than_code")
            if current == 0:
                try:
                    connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL)
                    connection.execute(
                        "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES (?,?,?,?)",
                        (SCHEMA_VERSION, MIGRATION_NAME, MIGRATION_CHECKSUM, utc_now()),
                    )
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    connection.commit()
                except Exception as exc:
                    connection.rollback()
                    raise RemediationStoreError("migration_failed") from exc
            row = connection.execute(
                "SELECT name,checksum FROM schema_migrations WHERE version=?",
                (SCHEMA_VERSION,),
            ).fetchone()
            if row is None or row["name"] != MIGRATION_NAME or row["checksum"] != MIGRATION_CHECKSUM:
                raise RemediationStoreError("migration_checksum_mismatch")
        finally:
            connection.close()
        os.chmod(self.path, 0o600)
        return {"schema_version": SCHEMA_VERSION, "created": not existed, "database_mode": "0o600"}

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect(writable=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        os.chmod(self.path, 0o600)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect(writable=False)
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def append_event(connection: sqlite3.Connection, plan_id: str, event_type: str, details: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event_type, str) or not event_type or not isinstance(details, Mapping):
            raise RemediationStoreError("invalid_event")
        encoded = canonical_json(details)
        if len(encoded.encode("utf-8")) > 65536:
            raise RemediationStoreError("event_too_large")
        previous = connection.execute(
            "SELECT sequence,record_hash FROM plan_events WHERE plan_id=? ORDER BY sequence DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        sequence = 1 if previous is None else int(previous["sequence"]) + 1
        previous_hash = None if previous is None else previous["record_hash"]
        created_at = utc_now()
        body = {
            "plan_id": plan_id,
            "sequence": sequence,
            "event_type": event_type,
            "details": dict(details),
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        record_hash = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        event_id = stable_id("remediation-event", plan_id, sequence, record_hash)
        connection.execute(
            """INSERT INTO plan_events
               (event_id,plan_id,sequence,event_type,details_json,previous_hash,record_hash,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (event_id, plan_id, sequence, event_type, encoded, previous_hash, record_hash, created_at),
        )
        connection.execute(
            "UPDATE plans SET event_count=?,last_event_hash=? WHERE plan_id=?",
            (sequence, record_hash, plan_id),
        )
        return {**body, "event_id": event_id, "record_hash": record_hash}

    @classmethod
    def transition(
        cls,
        connection: sqlite3.Connection,
        plan_id: str,
        target_state: str,
        event_type: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        row = connection.execute("SELECT state FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise RemediationStoreError("plan_not_found")
        validate_transition(row["state"], target_state)
        connection.execute(
            "UPDATE plans SET state=?,updated_at=? WHERE plan_id=?",
            (target_state, utc_now(), plan_id),
        )
        return cls.append_event(
            connection,
            plan_id,
            event_type,
            {"from": row["state"], "to": target_state, **dict(details)},
        )

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
            if row is None:
                raise RemediationStoreError("plan_not_found")
            evidence = [dict(item) for item in connection.execute(
                "SELECT artifact_id,artifact_sha256,verification_level,evidence_role FROM plan_evidence WHERE plan_id=? ORDER BY artifact_id",
                (plan_id,),
            )]
        result = dict(row)
        result["plan"] = json.loads(result.pop("plan_json"))
        result["evidence"] = evidence
        return result

    def events(self, plan_id: str) -> list[dict[str, Any]]:
        with self.read() as connection:
            if connection.execute("SELECT 1 FROM plans WHERE plan_id=?", (plan_id,)).fetchone() is None:
                raise RemediationStoreError("plan_not_found")
            rows = connection.execute(
                "SELECT * FROM plan_events WHERE plan_id=? ORDER BY sequence", (plan_id,)
            ).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    def verify_event_chains(self) -> dict[str, Any]:
        with self.read() as connection:
            plans = [dict(row) for row in connection.execute("SELECT plan_id,event_count,last_event_hash FROM plans ORDER BY plan_id")]
            count = 0
            for plan_record in plans:
                plan_id = plan_record["plan_id"]
                previous_hash = None
                expected_sequence = 1
                rows = connection.execute(
                    "SELECT * FROM plan_events WHERE plan_id=? ORDER BY sequence", (plan_id,)
                ).fetchall()
                for row in rows:
                    if row["sequence"] != expected_sequence or row["previous_hash"] != previous_hash:
                        raise RemediationStoreError("event_chain_sequence_or_link_mismatch")
                    body = {
                        "plan_id": plan_id,
                        "sequence": row["sequence"],
                        "event_type": row["event_type"],
                        "details": json.loads(row["details_json"]),
                        "previous_hash": row["previous_hash"],
                        "created_at": row["created_at"],
                    }
                    actual = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
                    if actual != row["record_hash"]:
                        raise RemediationStoreError("event_chain_hash_mismatch")
                    previous_hash = actual
                    expected_sequence += 1
                    count += 1
                if plan_record["event_count"] != len(rows) or plan_record["last_event_hash"] != previous_hash:
                    raise RemediationStoreError("event_chain_checkpoint_mismatch")
        return {"status": "PASS", "plan_count": len(plans), "event_count": count}

    def integrity(self) -> dict[str, Any]:
        with self.read() as connection:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            foreign = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            migration = connection.execute(
                "SELECT name,checksum,applied_at FROM schema_migrations WHERE version=?", (version,)
            ).fetchone()
        try:
            chains = self.verify_event_chains()
        except RemediationStoreError as exc:
            chains = {"status": "FAIL", "error": str(exc)}
        passed = quick == "ok" and not foreign and version == SCHEMA_VERSION and chains["status"] == "PASS"
        return {
            "schema": "aag-safe-remediation-integrity-v1",
            "status": "PASS" if passed else "FAIL",
            "quick_check": quick,
            "foreign_key_violations": foreign,
            "schema_version": version,
            "migration": dict(migration) if migration else None,
            "event_chains": chains,
            "database_mode": oct(self.path.stat().st_mode & 0o777),
        }

    def stats(self) -> dict[str, int]:
        with self.read() as connection:
            return {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in ("plans", "plan_events", "approvals", "attempts", "rollback_proposals")
            }
