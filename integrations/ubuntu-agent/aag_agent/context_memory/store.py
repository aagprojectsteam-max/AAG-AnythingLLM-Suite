"""Transactional SQLite store for AAG Context & Memory V1."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import canonical_json, stable_id, utc_now

SCHEMA_VERSION = 1
MIGRATION_NAME = "context_memory_v1_initial"

SCHEMA_SQL = r"""
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    authority_rank INTEGER NOT NULL CHECK(authority_rank BETWEEN 0 AND 100),
    read_only INTEGER NOT NULL CHECK(read_only IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE TABLE source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
    uri TEXT NOT NULL,
    original_sha256 TEXT NOT NULL CHECK(length(original_sha256)=64),
    normalized_sha256 TEXT NOT NULL CHECK(length(normalized_sha256)=64),
    parser_version TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
    modified_at TEXT,
    ingested_at TEXT NOT NULL,
    temporal_scope TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    verification_level TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','SUPERSEDED','MISSING')),
    UNIQUE(source_id, uri, original_sha256, parser_version)
);
CREATE INDEX idx_source_artifacts_uri ON source_artifacts(uri);
CREATE INDEX idx_source_artifacts_hash ON source_artifacts(original_sha256, parser_version);
CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL UNIQUE REFERENCES source_artifacts(artifact_id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    language TEXT NOT NULL,
    duplicate_of_document_id TEXT REFERENCES documents(document_id) ON DELETE RESTRICT,
    untrusted INTEGER NOT NULL CHECK(untrusted IN (0,1)),
    instruction_flags_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    start_line INTEGER,
    end_line INTEGER,
    text TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL CHECK(length(normalized_sha256)=64),
    token_estimate INTEGER NOT NULL CHECK(token_estimate >= 0),
    redacted INTEGER NOT NULL CHECK(redacted IN (0,1)),
    UNIQUE(document_id, ordinal)
);
CREATE VIRTUAL TABLE document_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    title,
    text,
    identifiers,
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE entity_aliases (
    alias_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_entity_alias_entity ON entity_aliases(entity_id);
CREATE TABLE relationships (
    relationship_id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
    predicate TEXT NOT NULL,
    object_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
    epistemic_state TEXT NOT NULL,
    temporal_scope TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    verification_level TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    CHECK(subject_entity_id <> object_entity_id)
);
CREATE INDEX idx_relationship_subject ON relationships(subject_entity_id, predicate);
CREATE INDEX idx_relationship_object ON relationships(object_entity_id, predicate);
CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
    fact_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, fact_key, claim_id)
);
CREATE TABLE claim_versions (
    version_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL CHECK(version_number >= 1),
    value_json TEXT NOT NULL,
    epistemic_state TEXT NOT NULL,
    temporal_scope TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    verification_level TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    supersedes_version_id TEXT REFERENCES claim_versions(version_id) ON DELETE RESTRICT,
    canonical INTEGER NOT NULL CHECK(canonical IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(claim_id, version_number)
);
CREATE UNIQUE INDEX idx_one_active_canonical_claim
ON claim_versions(claim_id)
WHERE canonical=1 AND lifecycle_state='ACTIVE' AND valid_to IS NULL;
CREATE VIRTUAL TABLE claims_fts USING fts5(
    version_id UNINDEXED,
    entity_name,
    fact_key,
    value_text,
    aliases,
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE evidence_links (
    evidence_link_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES source_artifacts(artifact_id) ON DELETE RESTRICT,
    chunk_id TEXT REFERENCES document_chunks(chunk_id) ON DELETE RESTRICT,
    evidence_role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(subject_type, subject_id, artifact_id, chunk_id, evidence_role)
);
CREATE INDEX idx_evidence_subject ON evidence_links(subject_type, subject_id);
CREATE TABLE observations (
    observation_id TEXT PRIMARY KEY,
    entity_id TEXT REFERENCES entities(entity_id) ON DELETE RESTRICT,
    fact_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    freshness_class TEXT NOT NULL,
    verification_level TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
    read_only INTEGER NOT NULL CHECK(read_only IN (0,1)),
    mutated INTEGER NOT NULL CHECK(mutated IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_observations_entity_time ON observations(entity_id, fact_key, observed_at DESC);
CREATE TABLE incidents (
    incident_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    entity_id TEXT REFERENCES entities(entity_id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    opened_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE incident_events (
    event_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE TABLE historical_actions (
    action_id TEXT PRIMARY KEY,
    incident_id TEXT REFERENCES incidents(incident_id) ON DELETE RESTRICT,
    action_type TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    result TEXT NOT NULL,
    executed_at TEXT,
    details_json TEXT NOT NULL
);
CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY,
    incident_id TEXT REFERENCES incidents(incident_id) ON DELETE RESTRICT,
    task_id TEXT,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    operator_governed INTEGER NOT NULL CHECK(operator_governed IN (0,1))
);
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    user_goal TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    state_json TEXT NOT NULL,
    result_json TEXT,
    closure_status TEXT NOT NULL CHECK(closure_status IN ('OPEN','BLOCKED','COMPLETE','CANCELLED'))
);
CREATE TABLE task_events (
    task_event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, sequence)
);
CREATE TABLE memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
    fact_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    origin TEXT NOT NULL,
    epistemic_state TEXT NOT NULL,
    verification_level TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','REJECTED','CONFLICTED','PROMOTED')),
    rejection_reason TEXT,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, fact_key, content_sha256, origin)
);
CREATE TABLE canonical_promotion_candidates (
    promotion_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES memory_candidates(candidate_id) ON DELETE RESTRICT,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','REJECTED','CONFLICTED','PROMOTED')),
    decision_reason TEXT,
    promoted_version_id TEXT REFERENCES claim_versions(version_id) ON DELETE RESTRICT
);
CREATE TABLE conflicts (
    conflict_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
    fact_key TEXT NOT NULL,
    canonical_version_id TEXT REFERENCES claim_versions(version_id) ON DELETE RESTRICT,
    observation_id TEXT REFERENCES observations(observation_id) ON DELETE RESTRICT,
    candidate_id TEXT REFERENCES memory_candidates(candidate_id) ON DELETE RESTRICT,
    canonical_value_json TEXT NOT NULL,
    observed_value_json TEXT NOT NULL,
    possible_explanations_json TEXT NOT NULL,
    required_verification_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('OPEN','RESOLVED','DISMISSED')),
    resolution_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_conflicts_open ON conflicts(status, entity_id, fact_key);
CREATE TABLE ingestion_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    mode TEXT NOT NULL CHECK(mode IN ('DRY_RUN','APPLY')),
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL,
    stats_json TEXT NOT NULL
);
CREATE TABLE ingestion_items (
    ingestion_item_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL,
    uri TEXT NOT NULL,
    artifact_id TEXT,
    result TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE TABLE retrieval_runs (
    retrieval_run_id TEXT PRIMARY KEY,
    query_redacted TEXT NOT NULL,
    query_sha256 TEXT NOT NULL,
    intent_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    result_count INTEGER NOT NULL,
    diagnostics_json TEXT NOT NULL
);
CREATE TABLE retrieval_results (
    retrieval_result_id TEXT PRIMARY KEY,
    retrieval_run_id TEXT NOT NULL REFERENCES retrieval_runs(retrieval_run_id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    result_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    score REAL NOT NULL,
    selection_reason TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    UNIQUE(retrieval_run_id, ordinal)
);
CREATE TABLE context_packages (
    context_package_id TEXT PRIMARY KEY,
    retrieval_run_id TEXT REFERENCES retrieval_runs(retrieval_run_id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
    schema_version TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    package_json TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE redaction_records (
    redaction_id TEXT PRIMARY KEY,
    artifact_id TEXT REFERENCES source_artifacts(artifact_id) ON DELETE RESTRICT,
    record_scope TEXT NOT NULL,
    pattern_class TEXT NOT NULL,
    replacement_count INTEGER NOT NULL CHECK(replacement_count >= 0),
    created_at TEXT NOT NULL
);
CREATE TABLE remediation_candidates (
    remediation_candidate_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    diagnosis_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE remediation_plans (
    plan_id TEXT PRIMARY KEY,
    remediation_candidate_id TEXT NOT NULL REFERENCES remediation_candidates(remediation_candidate_id) ON DELETE RESTRICT,
    plan_json TEXT NOT NULL,
    execution_authority TEXT NOT NULL CHECK(execution_authority='NONE'),
    execution_status TEXT NOT NULL CHECK(execution_status='not_executed'),
    created_at TEXT NOT NULL
);
CREATE TABLE remediation_plan_evidence (
    plan_id TEXT NOT NULL REFERENCES remediation_plans(plan_id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL REFERENCES source_artifacts(artifact_id) ON DELETE RESTRICT,
    PRIMARY KEY(plan_id, artifact_id)
);
CREATE TABLE memory_audit (
    sequence INTEGER PRIMARY KEY,
    event TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    record_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE VIEW current_canonical_facts AS
SELECT c.claim_id, c.entity_id, c.fact_key, v.version_id, v.value_json,
       v.epistemic_state, v.temporal_scope, v.lifecycle_state,
       v.verification_level, v.valid_from, v.created_at
FROM claims c
JOIN claim_versions v ON v.claim_id=c.claim_id
WHERE v.canonical=1 AND v.temporal_scope='CURRENT'
  AND v.lifecycle_state='ACTIVE' AND v.valid_to IS NULL;
CREATE VIEW active_tasks AS
SELECT * FROM tasks WHERE closure_status IN ('OPEN','BLOCKED');
"""

MIGRATION_CHECKSUM = hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest()


class ContextMemoryStoreError(RuntimeError):
    pass


class ContextMemoryStore:
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 3000,
        journal_mode: str = "WAL",
        synchronous: str = "FULL",
    ) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.journal_mode = journal_mode
        self.synchronous = synchronous

    def _connect(self, *, writable: bool) -> sqlite3.Connection:
        if writable:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        else:
            if not self.path.exists():
                raise ContextMemoryStoreError("context_database_missing")
            connection = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True,
                timeout=self.busy_timeout_ms / 1000,
            )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        connection.execute("PRAGMA foreign_keys=ON")
        if writable:
            actual = connection.execute(f"PRAGMA journal_mode={self.journal_mode}").fetchone()[0]
            if str(actual).upper() != self.journal_mode:
                connection.close()
                raise ContextMemoryStoreError("sqlite_journal_mode_mismatch")
            connection.execute(f"PRAGMA synchronous={self.synchronous}")
        return connection

    def migrate(self, *, fault_sql: str | None = None) -> None:
        connection = self._connect(writable=True)
        try:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise ContextMemoryStoreError("context_schema_too_new")
            if current == SCHEMA_VERSION:
                row = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version=?",
                    (SCHEMA_VERSION,),
                ).fetchone()
                if row is None or row["checksum"] != MIGRATION_CHECKSUM:
                    raise ContextMemoryStoreError("migration_checksum_mismatch")
                return
            if current != 0:
                raise ContextMemoryStoreError("unsupported_context_schema_upgrade")
            applied = utc_now().replace("'", "''")
            fault = fault_sql or ""
            script = (
                "BEGIN IMMEDIATE;\n"
                + SCHEMA_SQL
                + "\nINSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES "
                + f"({SCHEMA_VERSION},'{MIGRATION_NAME}','{MIGRATION_CHECKSUM}','{applied}');\n"
                + f"PRAGMA user_version={SCHEMA_VERSION};\n"
                + fault
                + "\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.DatabaseError as exc:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                raise ContextMemoryStoreError("context_migration_failed") from exc
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

    def integrity(self) -> dict[str, Any]:
        with self.read() as connection:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            foreign = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            migration = connection.execute(
                "SELECT name,checksum,applied_at FROM schema_migrations WHERE version=?",
                (version,),
            ).fetchone()
        return {
            "schema": "aag-context-memory-integrity-v1",
            "status": "PASS" if quick == "ok" and not foreign and version == SCHEMA_VERSION else "FAIL",
            "quick_check": quick,
            "foreign_key_violations": foreign,
            "schema_version": version,
            "migration": dict(migration) if migration else None,
            "database_mode": oct(self.path.stat().st_mode & 0o777),
        }

    def stats(self) -> dict[str, int]:
        tables = (
            "sources", "source_artifacts", "documents", "document_chunks",
            "entities", "entity_aliases", "relationships", "claims",
            "claim_versions", "observations", "incidents", "tasks",
            "memory_candidates", "conflicts", "retrieval_runs", "context_packages",
            "remediation_plans",
        )
        with self.read() as connection:
            return {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in tables
            }

    def artifact_for_uri(self, uri: str, connection: sqlite3.Connection | None = None) -> str | None:
        owns = connection is None
        connection = connection or self._connect(writable=False)
        try:
            row = connection.execute(
                """SELECT artifact_id FROM source_artifacts
                   WHERE uri=? AND status='ACTIVE'
                   ORDER BY ingested_at DESC, artifact_id DESC LIMIT 1""",
                (uri,),
            ).fetchone()
            return row["artifact_id"] if row else None
        finally:
            if owns:
                connection.close()

    def evidence_exists(self, evidence_ids: list[str], connection: sqlite3.Connection | None = None) -> bool:
        if not evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
            return False
        owns = connection is None
        connection = connection or self._connect(writable=False)
        try:
            placeholders = ",".join("?" for _ in evidence_ids)
            count = connection.execute(
                f"SELECT count(*) FROM source_artifacts WHERE artifact_id IN ({placeholders})",
                evidence_ids,
            ).fetchone()[0]
            return count == len(evidence_ids)
        finally:
            if owns:
                connection.close()

    @staticmethod
    def append_memory_audit(
        connection: sqlite3.Connection,
        event: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        previous = connection.execute(
            "SELECT sequence,record_hash FROM memory_audit ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = 1 if previous is None else int(previous["sequence"]) + 1
        previous_hash = None if previous is None else previous["record_hash"]
        created_at = utc_now()
        body = {
            "sequence": sequence,
            "event": event,
            "details": dict(details),
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        record_hash = "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT INTO memory_audit
               (sequence,event,details_json,previous_hash,record_hash,created_at)
               VALUES (?,?,?,?,?,?)""",
            (sequence, event, canonical_json(details), previous_hash, record_hash, created_at),
        )
        return {**body, "record_hash": record_hash}

    def add_observation(
        self,
        *,
        entity_id: str | None,
        fact_key: str,
        value: Any,
        observed_at: str,
        expires_at: str | None,
        freshness_class: str,
        source_id: str,
        artifact_id: str | None = None,
        verification_level: str = "DIRECT_LIVE",
        read_only: bool = True,
        mutated: bool = False,
    ) -> str:
        observation_id = stable_id("observation", entity_id or "", fact_key, observed_at, value)
        with self.transaction() as connection:
            if artifact_id is not None:
                artifact = connection.execute(
                    "SELECT source_id FROM source_artifacts WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                if artifact is None or artifact["source_id"] != source_id:
                    raise ContextMemoryStoreError("observation_artifact_source_mismatch")
            connection.execute(
                """INSERT OR IGNORE INTO observations
                   (observation_id,entity_id,fact_key,value_json,observed_at,expires_at,
                    freshness_class,verification_level,source_id,read_only,mutated,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    observation_id, entity_id, fact_key, canonical_json(value),
                    observed_at, expires_at, freshness_class, verification_level,
                    source_id, int(read_only), int(mutated), utc_now(),
                ),
            )
            if artifact_id is not None:
                connection.execute(
                    """INSERT OR IGNORE INTO evidence_links
                       (evidence_link_id,subject_type,subject_id,artifact_id,chunk_id,
                        evidence_role,created_at)
                       VALUES (?,?,?,?,NULL,'observed_by',?)""",
                    (
                        stable_id("evidence", "observation", observation_id, artifact_id),
                        "observation", observation_id, artifact_id, utc_now(),
                    ),
                )
            if entity_id is not None:
                current = connection.execute(
                    """SELECT v.version_id,v.value_json
                       FROM claims c JOIN claim_versions v ON v.claim_id=c.claim_id
                       WHERE c.entity_id=? AND c.fact_key=? AND v.canonical=1
                         AND v.lifecycle_state='ACTIVE' AND v.valid_to IS NULL""",
                    (entity_id, fact_key),
                ).fetchone()
                observed_json = canonical_json(value)
                if current is not None and current["value_json"] != observed_json:
                    conflict_id = stable_id(
                        "conflict", entity_id, fact_key,
                        current["version_id"], observation_id,
                    )
                    now = utc_now()
                    connection.execute(
                        """INSERT OR IGNORE INTO conflicts
                           (conflict_id,entity_id,fact_key,canonical_version_id,
                            observation_id,candidate_id,canonical_value_json,
                            observed_value_json,possible_explanations_json,
                            required_verification_json,status,resolution_json,
                            created_at,updated_at)
                           VALUES (?,?,?,?,?,NULL,?,?,?,?,'OPEN',NULL,?,?)""",
                        (
                            conflict_id, entity_id, fact_key,
                            current["version_id"], observation_id,
                            current["value_json"], observed_json,
                            canonical_json([
                                "intentional_transition", "stale_canonical_fact",
                                "stale_observation", "temporary_fallback",
                                "misconfiguration",
                            ]),
                            canonical_json([
                                "refresh_direct_live_observation",
                                "verify_authoritative_configuration",
                                "passing_test_or_governed_operator_decision",
                            ]),
                            now, now,
                        ),
                    )
                    self.append_memory_audit(
                        connection, "LIVE_DRIFT_CONFLICT_RECORDED",
                        {"conflict_id": conflict_id, "observation_id": observation_id},
                    )
        return observation_id

    def list_conflicts(self, *, status: str = "OPEN", limit: int = 100) -> list[dict[str, Any]]:
        if status not in {"OPEN", "RESOLVED", "DISMISSED"}:
            raise ContextMemoryStoreError("invalid_conflict_status")
        if not 1 <= limit <= 100:
            raise ContextMemoryStoreError("invalid_conflict_limit")
        with self.read() as connection:
            rows = connection.execute(
                """SELECT * FROM conflicts WHERE status=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        return [dict(row) for row in rows]
