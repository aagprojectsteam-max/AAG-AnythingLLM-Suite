"""Bounded, source-hashed, non-executing ingestion for Context & Memory V1."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import ContextMemoryConfig, WINBOAT_SEED_PATH, load_seed
from .models import (
    EPISTEMIC_STATES,
    LIFECYCLE_STATES,
    TEMPORAL_SCOPES,
    VERIFICATION_LEVELS,
    canonical_json,
    estimate_tokens,
    normalize_text,
    sha256_bytes,
    stable_id,
    utc_now,
)
from .store import ContextMemoryStore

SECRET_PATTERNS = (
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("named_secret", re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([^\s'\"},;]{8,})")),
    ("provider_key", re.compile(r"\b(?:sk|AIza)[-_A-Za-z0-9]{16,}\b")),
)
INSTRUCTION_PATTERNS = {
    "ignore_instructions": re.compile(r"(?i)ignore (?:all |the )?(?:previous|prior|system) instructions"),
    "privileged_command": re.compile(r"(?i)\b(?:sudo|docker\s+system\s+prune|apt\s+(?:clean|autoremove)|journalctl\s+--vacuum)\b"),
    "tool_authority_claim": re.compile(r"(?i)\b(?:you are authorized|execute this command|call the tool|system message)\b"),
}
IDENTIFIER_PATTERN = re.compile(
    r"(?:/[A-Za-z0-9_.+@%:/ -]{3,}|[A-Za-z0-9_.@-]+\.service\b|"
    r"\bINC-\d{4}\b|\b[a-fA-F0-9]{64}\b|\b(?:PART)?UUID\b[^\s,;]*)"
)
AUTHORITY_RANK = {
    "DIRECT_LIVE": 100,
    "TEST_VERIFIED": 90,
    "ARTIFACT_VERIFIED": 80,
    "DOCUMENTED": 55,
    "USER_CONFIRMED": 35,
    "INFERRED": 15,
    "UNVERIFIED": 5,
}


class IngestionError(ValueError):
    pass


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    text = text.replace("\x00", " ")
    text = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in text)
    counts: dict[str, int] = {}
    for name, pattern in SECRET_PATTERNS:
        def replacement(match: re.Match[str]) -> str:
            if name == "named_secret":
                return f"{match.group(1)}=[REDACTED]"
            return "[REDACTED]"
        text, count = pattern.subn(replacement, text)
        if count:
            counts[name] = count
    return text, counts


def instruction_flags(text: str) -> list[str]:
    return sorted(name for name, pattern in INSTRUCTION_PATTERNS.items() if pattern.search(text))


def compress_repetition(text: str) -> tuple[str, int]:
    output: list[str] = []
    previous: str | None = None
    repeats = 0
    compressed = 0
    for line in text.splitlines():
        normalized = normalize_text(line)
        if normalized and normalized == previous:
            repeats += 1
            continue
        if repeats:
            output.append(f"[previous line repeated {repeats} additional times]")
            compressed += repeats
            repeats = 0
        output.append(line)
        previous = normalized
    if repeats:
        output.append(f"[previous line repeated {repeats} additional times]")
        compressed += repeats
    return "\n".join(output), compressed


def chunk_text(text: str, *, max_chars: int, max_chunks: int) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    lines = text.splitlines()
    start = 1
    buffer: list[str] = []
    size = 0
    for number, line in enumerate(lines, 1):
        heading_boundary = line.startswith("#") and buffer
        projected = size + len(line) + 1
        if heading_boundary or projected > max_chars:
            body = "\n".join(buffer).strip()
            if body:
                chunks.append((start, number - 1, body))
            if len(chunks) >= max_chunks:
                break
            buffer = []
            size = 0
            start = number
        buffer.append(line)
        size += len(line) + 1
    if buffer and len(chunks) < max_chunks:
        body = "\n".join(buffer).strip()
        if body:
            chunks.append((start, len(lines), body))
    return chunks


class IngestionPipeline:
    def __init__(
        self,
        store: ContextMemoryStore,
        config: ContextMemoryConfig,
        *,
        allowed_roots: Iterable[Path] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.allowed_roots = tuple(Path(root).resolve() for root in (allowed_roots or config.allowed_ingestion_roots))

    def _trusted_path(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise IngestionError("ingestion_path_must_be_absolute")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise IngestionError("ingestion_source_missing") from exc
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            raise IngestionError("ingestion_path_outside_allowlist")
        current = Path("/")
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise IngestionError("ingestion_symlink_rejected")
        if not resolved.is_file():
            raise IngestionError("ingestion_source_not_file")
        return resolved

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = __import__("hashlib").sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _ubuntu_manager_payload(self, path: Path) -> str:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise IngestionError("ubuntu_manager_integrity_failed")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"documents", "scans", "smart_analysis", "content_equivalence"}
            if not required.issubset(tables):
                raise IngestionError("ubuntu_manager_schema_mismatch")
            rows = connection.execute(
                """SELECT d.path,d.filename,d.extension,d.size,d.sha256,
                          s.document_type,s.evidence_status,s.primary_topic,s.document_date
                   FROM documents d LEFT JOIN smart_analysis s ON s.path=d.path
                   ORDER BY d.path LIMIT 1000"""
            ).fetchall()
            payload = {
                "schema": "aag-ubuntu-manager-readonly-adapter-v1",
                "integrity": "PASS",
                "documents": [dict(row) for row in rows],
                "content_equivalence": [
                    dict(row) for row in connection.execute(
                        "SELECT source,target,relation FROM content_equivalence ORDER BY source,target LIMIT 1000"
                    )
                ],
            }
            return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        finally:
            connection.close()

    def _maintenance_history_payload(self, path: Path) -> str:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise IngestionError("maintenance_history_integrity_failed")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"maintenance_snapshots", "maintenance_metrics"}.issubset(tables):
                raise IngestionError("maintenance_history_schema_mismatch")
            payload = {
                "schema": "aag-maintenance-history-readonly-adapter-v1",
                "snapshots": [
                    dict(row) for row in connection.execute(
                        """SELECT snapshot_id,scan_id,created_at,root,mount_identity,
                                  config_fingerprint,policy_fingerprint,size_dimension,
                                  completeness,error_count
                           FROM maintenance_snapshots ORDER BY snapshot_id DESC LIMIT 100"""
                    )
                ],
                "metrics": [
                    dict(row) for row in connection.execute(
                        """SELECT metric_id,captured_at,profile,config_fingerprint,
                                  completeness,metrics_json
                           FROM maintenance_metrics ORDER BY metric_id DESC LIMIT 100"""
                    )
                ],
            }
            return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        finally:
            connection.close()

    def _load(self, spec: Mapping[str, Any]) -> tuple[Path, str, str]:
        path = self._trusted_path(spec["path"])
        original_sha = self._hash_file(path)
        adapter = spec.get("adapter")
        if adapter == "ubuntu_manager":
            text = self._ubuntu_manager_payload(path)
        elif adapter == "maintenance_history":
            text = self._maintenance_history_payload(path)
        else:
            raw = path.read_bytes()
            if len(raw) > self.config.limits["max_document_bytes"]:
                raise IngestionError("ingestion_document_too_large")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise IngestionError("ingestion_source_not_utf8") from exc
            if spec["parser_version"].startswith(("json-", "registry-")):
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True, indent=2)
                except json.JSONDecodeError as exc:
                    raise IngestionError("ingestion_json_malformed") from exc
        if len(text.encode("utf-8")) > self.config.limits["max_document_bytes"]:
            raise IngestionError("normalized_document_too_large")
        return path, original_sha, text

    def preview(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        path, original_sha, text = self._load(spec)
        redacted, redactions = redact_text(text)
        compressed, repetitions = compress_repetition(redacted)
        chunks = chunk_text(
            compressed,
            max_chars=self.config.limits["max_chunk_chars"],
            max_chunks=self.config.limits["max_chunks_per_document"],
        )
        return {
            "source_id": spec["source_id"],
            "uri": str(path),
            "original_sha256": original_sha,
            "normalized_sha256": sha256_bytes(normalize_text(compressed).encode("utf-8")),
            "parser_version": spec["parser_version"],
            "chunks": len(chunks),
            "redactions": redactions,
            "repetitions_compressed": repetitions,
            "instruction_flags": instruction_flags(compressed),
            "would_apply": False,
        }

    def ingest(self, spec: Mapping[str, Any], *, run_id: str) -> dict[str, Any]:
        path, original_sha, text = self._load(spec)
        redacted, redactions = redact_text(text)
        compressed, repetitions = compress_repetition(redacted)
        normalized_sha = sha256_bytes(normalize_text(compressed).encode("utf-8"))
        artifact_id = stable_id(
            "artifact", spec["source_id"], str(path), original_sha, spec["parser_version"]
        )
        document_id = stable_id("document", artifact_id)
        chunks = chunk_text(
            compressed,
            max_chars=self.config.limits["max_chunk_chars"],
            max_chunks=self.config.limits["max_chunks_per_document"],
        )
        now = utc_now()
        stat = path.stat()
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO sources
                   (source_id,source_type,title,authority_rank,read_only,created_at)
                   VALUES (?,?,?,?,1,?)""",
                (
                    spec["source_id"], spec["source_type"], path.name,
                    AUTHORITY_RANK[spec["verification_level"]], now,
                ),
            )
            existing = connection.execute(
                "SELECT artifact_id FROM source_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if existing:
                result = "UNCHANGED"
            else:
                connection.execute(
                    """UPDATE source_artifacts SET status='SUPERSEDED'
                       WHERE source_id=? AND uri=? AND status='ACTIVE'""",
                    (spec["source_id"], str(path)),
                )
                connection.execute(
                    """INSERT INTO source_artifacts
                       (artifact_id,source_id,uri,original_sha256,normalized_sha256,
                        parser_version,byte_size,modified_at,ingested_at,temporal_scope,
                        lifecycle_state,verification_level,status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE')""",
                    (
                        artifact_id, spec["source_id"], str(path), original_sha,
                        normalized_sha, spec["parser_version"], stat.st_size,
                        str(stat.st_mtime_ns), now, spec["temporal_scope"],
                        spec["lifecycle_state"], spec["verification_level"],
                    ),
                )
                duplicate = connection.execute(
                    """SELECT d.document_id FROM documents d
                       JOIN source_artifacts a ON a.artifact_id=d.artifact_id
                       WHERE a.normalized_sha256=? AND a.parser_version=?
                         AND d.duplicate_of_document_id IS NULL
                       ORDER BY d.created_at,d.document_id LIMIT 1""",
                    (normalized_sha, spec["parser_version"]),
                ).fetchone()
                duplicate_id = duplicate["document_id"] if duplicate else None
                flags = instruction_flags(compressed)
                connection.execute(
                    """INSERT INTO documents
                       (document_id,artifact_id,title,media_type,language,
                        duplicate_of_document_id,untrusted,instruction_flags_json,created_at)
                       VALUES (?,?,?,?,?,?,1,?,?)""",
                    (
                        document_id, artifact_id, path.name,
                        "application/json" if spec["parser_version"].startswith(("json-", "registry-")) else "text/markdown",
                        "mixed", duplicate_id, canonical_json(flags), now,
                    ),
                )
                if duplicate_id is None:
                    for ordinal, (start_line, end_line, body) in enumerate(chunks):
                        chunk_id = stable_id("chunk", document_id, ordinal, body)
                        chunk_hash = sha256_bytes(normalize_text(body).encode("utf-8"))
                        was_redacted = bool(redactions)
                        connection.execute(
                            """INSERT INTO document_chunks
                               (chunk_id,document_id,ordinal,start_line,end_line,text,
                                normalized_sha256,token_estimate,redacted)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (
                                chunk_id, document_id, ordinal, start_line, end_line,
                                body, chunk_hash, estimate_tokens(body), int(was_redacted),
                            ),
                        )
                        identifiers = " ".join(sorted(set(IDENTIFIER_PATTERN.findall(body))))
                        connection.execute(
                            "INSERT INTO document_chunks_fts(chunk_id,title,text,identifiers) VALUES (?,?,?,?)",
                            (chunk_id, path.name, body, identifiers),
                        )
                for pattern_class, count in sorted(redactions.items()):
                    connection.execute(
                        """INSERT INTO redaction_records
                           (redaction_id,artifact_id,record_scope,pattern_class,
                            replacement_count,created_at) VALUES (?,?,?,?,?,?)""",
                        (
                            stable_id("redaction", artifact_id, pattern_class),
                            artifact_id, "document", pattern_class, count, now,
                        ),
                    )
                if repetitions:
                    connection.execute(
                        """INSERT INTO redaction_records
                           (redaction_id,artifact_id,record_scope,pattern_class,
                            replacement_count,created_at) VALUES (?,?,?,?,?,?)""",
                        (
                            stable_id("redaction", artifact_id, "repetition"),
                            artifact_id, "document", "repetition_compression",
                            repetitions, now,
                        ),
                    )
                result = "RENAMED_DUPLICATE" if duplicate_id else "INGESTED"
            connection.execute(
                """INSERT OR REPLACE INTO ingestion_items
                   (ingestion_item_id,run_id,source_id,uri,artifact_id,result,details_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    stable_id("ingestion-item", run_id, spec["source_id"]),
                    run_id, spec["source_id"], str(path), artifact_id, result,
                    canonical_json({
                        "chunks": 0 if result in {"UNCHANGED", "RENAMED_DUPLICATE"} else len(chunks),
                        "redactions": redactions,
                        "repetitions_compressed": repetitions,
                    }),
                ),
            )
        return {
            "source_id": spec["source_id"],
            "artifact_id": artifact_id,
            "result": result,
            "chunks": 0 if result in {"UNCHANGED", "RENAMED_DUPLICATE"} else len(chunks),
            "redactions": sum(redactions.values()),
        }

    @staticmethod
    def _evidence_link(
        connection: sqlite3.Connection,
        *,
        subject_type: str,
        subject_id: str,
        artifact_id: str,
        role: str,
    ) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO evidence_links
               (evidence_link_id,subject_type,subject_id,artifact_id,chunk_id,
                evidence_role,created_at) VALUES (?,?,?,?,NULL,?,?)""",
            (
                stable_id("evidence", subject_type, subject_id, artifact_id, role),
                subject_type, subject_id, artifact_id, role, utc_now(),
            ),
        )

    @staticmethod
    def _entity_id_for_registry(identity: str) -> str:
        return {
            "aag-ubuntu-agent": "entity:aag-agent",
            "aag-host-bridge": "entity:bridge",
            "anythingllm": "entity:anythingllm",
        }.get(identity, f"entity:registry:{identity}")

    def apply_registry_structure(self, spec: Mapping[str, Any]) -> dict[str, int]:
        path = self._trusted_path(spec["path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != "aag-component-registry-v1" or not isinstance(data.get("components"), list):
            raise IngestionError("registry_schema_mismatch")
        artifact_id = self.store.artifact_for_uri(str(path))
        if artifact_id is None:
            raise IngestionError("registry_artifact_missing")
        now = utc_now()
        identities = {
            str(component["identity"]): self._entity_id_for_registry(str(component["identity"]))
            for component in data["components"]
        }
        relationships = 0
        with self.store.transaction() as connection:
            for component in data["components"]:
                identity = str(component["identity"])
                entity_id = identities[identity]
                name = str(component["name"])
                connection.execute(
                    """INSERT INTO entities(entity_id,entity_type,canonical_name,lifecycle_state,created_at)
                       VALUES (?,?,?,'ACTIVE',?)
                       ON CONFLICT(entity_id) DO UPDATE SET canonical_name=excluded.canonical_name""",
                    (entity_id, str(component.get("category", "component")), name, now),
                )
                for alias in {identity, name}:
                    connection.execute(
                        """INSERT OR IGNORE INTO entity_aliases
                           (alias_id,entity_id,alias,normalized_alias,language,created_at)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            stable_id("alias", normalize_text(alias)), entity_id, alias,
                            normalize_text(alias), "mixed", now,
                        ),
                    )
            for component in data["components"]:
                subject = identities[str(component["identity"])]
                for dependency in component.get("dependencies", []):
                    if dependency not in identities:
                        continue
                    relationship_id = stable_id("relationship", subject, "depends_on", identities[dependency])
                    connection.execute(
                        """INSERT OR IGNORE INTO relationships
                           (relationship_id,subject_entity_id,predicate,object_entity_id,
                            epistemic_state,temporal_scope,lifecycle_state,verification_level,
                            valid_from,valid_to,created_at)
                           VALUES (?,?, 'depends_on',?,'VERIFIED','CURRENT','ACTIVE',
                                   'ARTIFACT_VERIFIED',NULL,NULL,?)""",
                        (relationship_id, subject, identities[dependency], now),
                    )
                    self._evidence_link(
                        connection, subject_type="relationship",
                        subject_id=relationship_id, artifact_id=artifact_id, role="supports",
                    )
                    relationships += 1
        return {"entities": len(identities), "relationships": relationships}

    def apply_seed(self, seed: Mapping[str, Any]) -> dict[str, int]:
        now = utc_now()
        counts = {"entities": 0, "relationships": 0, "claims": 0, "incidents": 0}
        with self.store.transaction() as connection:
            for item in seed["entities"]:
                connection.execute(
                    """INSERT OR IGNORE INTO entities
                       (entity_id,entity_type,canonical_name,lifecycle_state,created_at)
                       VALUES (?,?,?,'ACTIVE',?)""",
                    (item["entity_id"], item["entity_type"], item["canonical_name"], now),
                )
                for alias in {item["canonical_name"], *item.get("aliases", [])}:
                    connection.execute(
                        """INSERT OR IGNORE INTO entity_aliases
                           (alias_id,entity_id,alias,normalized_alias,language,created_at)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            stable_id("alias", normalize_text(alias)), item["entity_id"],
                            alias, normalize_text(alias), "mixed", now,
                        ),
                    )
                counts["entities"] += 1
            for item in seed["relationships"]:
                artifact_id = self.store.artifact_for_uri(item["evidence_uri"], connection)
                if artifact_id is None:
                    raise IngestionError("seed_relationship_evidence_missing")
                connection.execute(
                    """INSERT OR IGNORE INTO relationships
                       (relationship_id,subject_entity_id,predicate,object_entity_id,
                        epistemic_state,temporal_scope,lifecycle_state,verification_level,
                        valid_from,valid_to,created_at)
                       VALUES (?,?,?,?,'VERIFIED',?,?,?,?,NULL,?)""",
                    (
                        item["relationship_id"], item["subject_entity_id"], item["predicate"],
                        item["object_entity_id"], item["temporal_scope"],
                        item["lifecycle_state"], item["verification_level"],
                        item.get("valid_from"), now,
                    ),
                )
                self._evidence_link(
                    connection, subject_type="relationship",
                    subject_id=item["relationship_id"], artifact_id=artifact_id, role="supports",
                )
                counts["relationships"] += 1
            for item in seed["claims"]:
                if item["epistemic_state"] not in EPISTEMIC_STATES:
                    raise IngestionError("seed_epistemic_state_invalid")
                if item["temporal_scope"] not in TEMPORAL_SCOPES:
                    raise IngestionError("seed_temporal_scope_invalid")
                if item["lifecycle_state"] not in LIFECYCLE_STATES:
                    raise IngestionError("seed_lifecycle_state_invalid")
                if item["verification_level"] not in VERIFICATION_LEVELS:
                    raise IngestionError("seed_verification_level_invalid")
                artifact_id = self.store.artifact_for_uri(item["evidence_uri"], connection)
                if artifact_id is None:
                    raise IngestionError("seed_claim_evidence_missing")
                connection.execute(
                    "INSERT OR IGNORE INTO claims(claim_id,entity_id,fact_key,created_at) VALUES (?,?,?,?)",
                    (item["claim_id"], item["entity_id"], item["fact_key"], now),
                )
                version_id = stable_id("claim-version", item["claim_id"], 1, item["value"])
                connection.execute(
                    """INSERT OR IGNORE INTO claim_versions
                       (version_id,claim_id,version_number,value_json,epistemic_state,
                        temporal_scope,lifecycle_state,verification_level,valid_from,
                        valid_to,supersedes_version_id,canonical,created_at)
                       VALUES (?,?,1,?,?,?,?,?,?,NULL,NULL,?,?)""",
                    (
                        version_id, item["claim_id"], canonical_json(item["value"]),
                        item["epistemic_state"], item["temporal_scope"],
                        item["lifecycle_state"], item["verification_level"],
                        item.get("valid_from"), int(bool(item["canonical"])), now,
                    ),
                )
                entity = connection.execute(
                    "SELECT canonical_name FROM entities WHERE entity_id=?",
                    (item["entity_id"],),
                ).fetchone()
                aliases = " ".join(
                    row["alias"] for row in connection.execute(
                        "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                        (item["entity_id"],),
                    )
                )
                if connection.execute(
                    "SELECT count(*) FROM claims_fts WHERE version_id=?", (version_id,)
                ).fetchone()[0] == 0:
                    connection.execute(
                        """INSERT INTO claims_fts
                           (version_id,entity_name,fact_key,value_text,aliases)
                           VALUES (?,?,?,?,?)""",
                        (
                            version_id, entity["canonical_name"], item["fact_key"],
                            canonical_json(item["value"]), aliases,
                        ),
                    )
                self._evidence_link(
                    connection, subject_type="claim_version",
                    subject_id=version_id, artifact_id=artifact_id, role="supports",
                )
                counts["claims"] += 1
            for item in seed["incidents"]:
                artifact_id = self.store.artifact_for_uri(item["evidence_uri"], connection)
                if artifact_id is None:
                    raise IngestionError("seed_incident_evidence_missing")
                connection.execute(
                    """INSERT OR IGNORE INTO incidents
                       (incident_id,title,entity_id,status,severity,opened_at,closed_at,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        item["incident_id"], item["title"], item.get("entity_id"),
                        item["status"], item["severity"], item.get("opened_at"),
                        item.get("closed_at"), now,
                    ),
                )
                self._evidence_link(
                    connection, subject_type="incident", subject_id=item["incident_id"],
                    artifact_id=artifact_id, role="documents",
                )
                counts["incidents"] += 1
        return counts

    def run_configured(self, *, apply: bool) -> dict[str, Any]:
        run_id = stable_id("ingestion-run", utc_now(), "APPLY" if apply else "DRY_RUN")
        results: list[dict[str, Any]] = []
        if not apply:
            for spec in self.config.sources:
                results.append(self.preview(spec))
            return {
                "schema": "aag-context-ingestion-run-v1",
                "run_id": run_id,
                "mode": "DRY_RUN",
                "status": "PASS",
                "items": results,
            }
        self.store.migrate()
        with self.store.transaction() as connection:
            now = utc_now()
            connection.execute(
                """INSERT OR IGNORE INTO sources
                   (source_id,source_type,title,authority_rank,read_only,created_at)
                   VALUES ('live-tool-context','live_tool','AAG typed live diagnostics',100,1,?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO ingestion_runs
                   (run_id,started_at,completed_at,mode,parser_version,status,stats_json)
                   VALUES (?,?,NULL,'APPLY','context-ingestion-v1','RUNNING','{}')""",
                (run_id, now),
            )
        try:
            for spec in self.config.sources:
                results.append(self.ingest(spec, run_id=run_id))
            registry_spec = next(item for item in self.config.sources if item.get("adapter") == "registry")
            registry = self.apply_registry_structure(registry_spec)
            seeded = self.apply_seed(load_seed())
            winboat_seeded = self.apply_seed(load_seed(WINBOAT_SEED_PATH))
            seeded = {key: seeded[key] + winboat_seeded[key] for key in seeded}
            stats = {
                "items": len(results),
                "ingested": sum(item["result"] == "INGESTED" for item in results),
                "unchanged": sum(item["result"] == "UNCHANGED" for item in results),
                "renamed_duplicates": sum(item["result"] == "RENAMED_DUPLICATE" for item in results),
                "chunks": sum(item["chunks"] for item in results),
                "redactions": sum(item["redactions"] for item in results),
                "registry": registry,
                "seeded": seeded,
            }
            with self.store.transaction() as connection:
                connection.execute(
                    """UPDATE ingestion_runs SET completed_at=?,status='PASS',stats_json=?
                       WHERE run_id=?""",
                    (utc_now(), canonical_json(stats), run_id),
                )
            status = "PASS"
        except Exception as exc:
            with self.store.transaction() as connection:
                connection.execute(
                    """UPDATE ingestion_runs SET completed_at=?,status='FAIL',stats_json=?
                       WHERE run_id=?""",
                    (utc_now(), canonical_json({"error": type(exc).__name__}), run_id),
                )
            raise
        return {
            "schema": "aag-context-ingestion-run-v1",
            "run_id": run_id,
            "mode": "APPLY",
            "status": status,
            "stats": stats,
            "items": results,
        }
