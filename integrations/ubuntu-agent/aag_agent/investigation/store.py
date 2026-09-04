"""Append-oriented investigation state with per-investigation hash chains."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import HYPOTHESIS_STATES, INVESTIGATION_STATES, canonical_json, stable_id, utc_now

PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
DEFAULT_DATABASE = PROJECT_ROOT / "memory/diagnostic-investigations-v1.sqlite3"
SCHEMA_VERSION = 1
MIGRATION_NAME = "diagnostic_investigations_v1_initial"
_hypothesis_states = ",".join(f"'{item}'" for item in sorted(HYPOTHESIS_STATES))
_investigation_states = ",".join(f"'{item}'" for item in sorted(INVESTIGATION_STATES))
SCHEMA_SQL = f"""
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE investigations (
    investigation_id TEXT PRIMARY KEY,
    playbook_id TEXT NOT NULL,
    playbook_version INTEGER NOT NULL CHECK(playbook_version=1),
    registry_sha256 TEXT NOT NULL CHECK(length(registry_sha256)=64),
    target_identity TEXT NOT NULL,
    request_summary TEXT NOT NULL,
    task_id TEXT,
    state TEXT NOT NULL CHECK(state IN ({_investigation_states})),
    conclusion TEXT NOT NULL,
    read_only INTEGER NOT NULL CHECK(read_only=1),
    mutated INTEGER NOT NULL CHECK(mutated=0),
    event_count INTEGER NOT NULL DEFAULT 0 CHECK(event_count>=0),
    last_event_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE hypotheses (
    hypothesis_record_id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(investigation_id) ON DELETE RESTRICT,
    hypothesis_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    predicate_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ({_hypothesis_states})),
    evidence_summary_json TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    selection_reason TEXT NOT NULL,
    next_check TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(investigation_id,hypothesis_id)
);
CREATE TABLE investigation_steps (
    step_record_id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(investigation_id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK(ordinal>=1),
    step_id TEXT NOT NULL,
    collector TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','OBSERVED','INDETERMINATE','ERROR')),
    started_at TEXT,
    completed_at TEXT,
    duration_ms REAL,
    UNIQUE(investigation_id,ordinal),
    UNIQUE(investigation_id,step_id)
);
CREATE TABLE investigation_evidence (
    evidence_record_id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(investigation_id) ON DELETE RESTRICT,
    step_record_id TEXT NOT NULL REFERENCES investigation_steps(step_record_id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    verification_level TEXT NOT NULL CHECK(verification_level='DIRECT_LIVE'),
    observed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    freshness_seconds INTEGER NOT NULL CHECK(freshness_seconds BETWEEN 5 AND 600),
    payload_json TEXT NOT NULL,
    read_only INTEGER NOT NULL CHECK(read_only=1),
    mutated INTEGER NOT NULL CHECK(mutated=0),
    UNIQUE(investigation_id,artifact_id)
);
CREATE TABLE investigation_events (
    event_id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(investigation_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK(sequence>=1),
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    record_hash TEXT NOT NULL UNIQUE CHECK(length(record_hash)=64),
    created_at TEXT NOT NULL,
    UNIQUE(investigation_id,sequence)
);
CREATE INDEX idx_investigation_target ON investigations(target_identity,created_at);
CREATE INDEX idx_hypothesis_state ON hypotheses(state,investigation_id);
CREATE INDEX idx_investigation_evidence_artifact ON investigation_evidence(artifact_id);
"""
MIGRATION_CHECKSUM = hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest()


class InvestigationStoreError(RuntimeError):
    pass


class InvestigationStore:
    def __init__(self, path: Path = DEFAULT_DATABASE, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        if self.path.is_symlink():
            raise InvestigationStoreError("database_symlink_forbidden")
        if not isinstance(busy_timeout_ms, int) or not 100 <= busy_timeout_ms <= 10000:
            raise InvestigationStoreError("invalid_busy_timeout")

    def _connect(self, *, writable: bool) -> sqlite3.Connection:
        if not writable and not self.path.exists():
            raise InvestigationStoreError("investigation_database_missing")
        if writable:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise InvestigationStoreError("database_symlink_forbidden")
        target = str(self.path) if writable else f"file:{self.path}?mode=ro"
        connection = sqlite3.connect(target, uri=not writable, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        if writable:
            journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(journal).casefold() != "wal":
                connection.close()
                raise InvestigationStoreError("unsafe_journal_mode")
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    def migrate(self, *, fault_sql: str | None = None) -> dict[str, Any]:
        existed = self.path.exists()
        connection = self._connect(writable=True)
        try:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise InvestigationStoreError("database_schema_newer_than_code")
            if current == 0:
                applied = utc_now().replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n" + SCHEMA_SQL
                    + f"\nINSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES ({SCHEMA_VERSION},'{MIGRATION_NAME}','{MIGRATION_CHECKSUM}','{applied}');"
                    + f"\nPRAGMA user_version={SCHEMA_VERSION};\n"
                    + (fault_sql or "") + "\nCOMMIT;"
                )
                try:
                    connection.executescript(script)
                except sqlite3.DatabaseError as exc:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.DatabaseError:
                        pass
                    raise InvestigationStoreError("migration_failed") from exc
            row = connection.execute("SELECT name,checksum FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone()
            if row is None or row["name"] != MIGRATION_NAME or row["checksum"] != MIGRATION_CHECKSUM:
                raise InvestigationStoreError("migration_checksum_mismatch")
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
    def append_event(connection: sqlite3.Connection, investigation_id: str, event_type: str, details: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event_type, str) or not event_type or not isinstance(details, Mapping):
            raise InvestigationStoreError("invalid_event")
        details_json = canonical_json(details)
        if len(details_json.encode("utf-8")) > 65536:
            raise InvestigationStoreError("event_too_large")
        previous = connection.execute(
            "SELECT sequence,record_hash FROM investigation_events WHERE investigation_id=? ORDER BY sequence DESC LIMIT 1",
            (investigation_id,),
        ).fetchone()
        sequence = 1 if previous is None else int(previous["sequence"]) + 1
        previous_hash = None if previous is None else previous["record_hash"]
        created_at = utc_now()
        body = {
            "investigation_id": investigation_id,
            "sequence": sequence,
            "event_type": event_type,
            "details": dict(details),
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        record_hash = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        event_id = stable_id("investigation-event", investigation_id, sequence, record_hash)
        connection.execute(
            "INSERT INTO investigation_events VALUES (?,?,?,?,?,?,?,?)",
            (event_id, investigation_id, sequence, event_type, details_json, previous_hash, record_hash, created_at),
        )
        connection.execute(
            "UPDATE investigations SET event_count=?,last_event_hash=?,updated_at=? WHERE investigation_id=?",
            (sequence, record_hash, created_at, investigation_id),
        )
        return {**body, "event_id": event_id, "record_hash": record_hash}

    def get(self, investigation_id: str) -> dict[str, Any]:
        if not isinstance(investigation_id, str):
            raise InvestigationStoreError("invalid_investigation_id")
        with self.read() as connection:
            row = connection.execute("SELECT * FROM investigations WHERE investigation_id=?", (investigation_id,)).fetchone()
            if row is None:
                raise InvestigationStoreError("investigation_not_found")
            hypotheses = connection.execute(
                "SELECT * FROM hypotheses WHERE investigation_id=? ORDER BY score DESC,hypothesis_id",
                (investigation_id,),
            ).fetchall()
            steps = connection.execute(
                "SELECT * FROM investigation_steps WHERE investigation_id=? ORDER BY ordinal", (investigation_id,)
            ).fetchall()
            evidence = connection.execute(
                "SELECT evidence_record_id,step_record_id,artifact_id,payload_sha256,verification_level,observed_at,expires_at,freshness_seconds,read_only,mutated FROM investigation_evidence WHERE investigation_id=? ORDER BY observed_at",
                (investigation_id,),
            ).fetchall()
        return {
            "schema": "aag-diagnostic-investigation-v1",
            "investigation_id": row["investigation_id"],
            "playbook_id": row["playbook_id"],
            "playbook_version": row["playbook_version"],
            "target_identity": row["target_identity"],
            "task_id": row["task_id"],
            "state": row["state"],
            "conclusion": row["conclusion"],
            "hypotheses": [
                {
                    "hypothesis_id": item["hypothesis_id"], "statement": item["statement"],
                    "state": item["state"], "score": item["score"],
                    "evidence_summary": json.loads(item["evidence_summary_json"]),
                    "selection_reason": item["selection_reason"], "next_check": item["next_check"],
                }
                for item in hypotheses
            ],
            "steps": [dict(item) for item in steps],
            "evidence": [dict(item) for item in evidence],
            "event_count": row["event_count"],
            "last_event_hash": row["last_event_hash"],
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
        }

    def integrity(self) -> dict[str, Any]:
        errors: list[str] = []
        with self.read() as connection:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            foreign = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            investigation_ids = [row[0] for row in connection.execute("SELECT investigation_id FROM investigations")]
            for investigation_id in investigation_ids:
                events = connection.execute(
                    "SELECT * FROM investigation_events WHERE investigation_id=? ORDER BY sequence", (investigation_id,)
                ).fetchall()
                previous = None
                for expected, event in enumerate(events, 1):
                    details = json.loads(event["details_json"])
                    body = {
                        "investigation_id": investigation_id, "sequence": expected,
                        "event_type": event["event_type"], "details": details,
                        "previous_hash": previous, "created_at": event["created_at"],
                    }
                    expected_hash = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
                    if event["sequence"] != expected or event["previous_hash"] != previous or event["record_hash"] != expected_hash:
                        errors.append(f"event_chain_invalid:{investigation_id}")
                        break
                    previous = expected_hash
                head = connection.execute(
                    "SELECT event_count,last_event_hash FROM investigations WHERE investigation_id=?", (investigation_id,)
                ).fetchone()
                if head["event_count"] != len(events) or head["last_event_hash"] != previous:
                    errors.append(f"event_checkpoint_invalid:{investigation_id}")
        status = "PASS" if quick == "ok" and not foreign and version == SCHEMA_VERSION and not errors else "FAIL"
        return {
            "schema": "aag-investigation-integrity-v1", "status": status,
            "quick_check": quick, "foreign_key_violations": foreign,
            "schema_version": version, "event_chain_errors": errors,
            "database_mode": oct(self.path.stat().st_mode & 0o777),
        }

    def stats(self) -> dict[str, int]:
        with self.read() as connection:
            return {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in ("investigations", "hypotheses", "investigation_steps", "investigation_evidence", "investigation_events")
            }
