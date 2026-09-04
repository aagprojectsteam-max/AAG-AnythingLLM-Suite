"""Deterministic exact, entity, FTS5, relationship, and status-aware retrieval."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .config import ContextMemoryConfig
from .ingestion import redact_text
from .models import canonical_json, historical_intent, normalize_text, sha256_bytes, stable_id, utc_now
from .store import ContextMemoryStore, ContextMemoryStoreError

WORD_PATTERN = re.compile(r"[\w\u0590-\u05FF./:@-]{2,}", re.UNICODE)
LEXICAL_EXPANSIONS = {
    "נכשל": ("failed", "failure"),
    "כשל": ("failed", "failure"),
    "תוכנית": ("plan",),
    "תכנית": ("plan",),
    "ניקוי": ("cleanup",),
    "תיקן": ("fixed", "remediation"),
    "פתר": ("fix", "remediation"),
    "פתרון": ("fix", "solution"),
    "ניסינו": ("attempt", "tried"),
    "דחינו": ("rejected",),
    "עצות": ("advice",),
    "מבוססות": ("grounded", "evidence"),
    "מסוכנות": ("unsafe", "risk"),
}
EXACT_PATTERNS = (
    re.compile(r"/(?:[^\s,;]+(?: [^\s,;]+)*)"),
    re.compile(r"\b[A-Za-z0-9_.@-]+\.service\b"),
    re.compile(r"\bINC-\d{4}\b", re.I),
    re.compile(r"\b[a-fA-F0-9]{64}\b"),
    re.compile(r"\b(?:PART)?UUID[=: ]+[A-Za-z0-9-]+\b", re.I),
    re.compile(r"\b\d+\.\d+\.\d+(?:[-+._A-Za-z0-9]*)?\b"),
)
VERIFICATION_WEIGHT = {
    "DIRECT_LIVE": 100,
    "TEST_VERIFIED": 90,
    "ARTIFACT_VERIFIED": 80,
    "DOCUMENTED": 55,
    "USER_CONFIRMED": 35,
    "INFERRED": 15,
    "UNVERIFIED": 5,
}


class RetrievalError(ValueError):
    pass


def fts_query(query: str) -> str:
    tokens = []
    for token in WORD_PATTERN.findall(query):
        token = token.strip("./:@-").replace('"', "")
        if len(token) >= 2 and token.casefold() not in {item.casefold() for item in tokens}:
            tokens.append(token)
        if len(tokens) >= 12:
            break
    normalized_query = normalize_text(query)
    for source_term, expansions in LEXICAL_EXPANSIONS.items():
        if source_term not in normalized_query:
            continue
        for expansion in expansions:
            if expansion.casefold() not in {item.casefold() for item in tokens}:
                tokens.append(expansion)
            if len(tokens) >= 18:
                break
    return " OR ".join(f'"{token}"' for token in tokens)


def exact_identifiers(query: str) -> list[str]:
    values: list[str] = []
    for pattern in EXACT_PATTERNS:
        for match in pattern.findall(query):
            value = match.strip().rstrip("?.!:)")
            if value and value not in values:
                values.append(value)
    return values[:12]


def freshness_state(expires_at: str | None, *, now: datetime | None = None) -> str:
    if expires_at is None:
        return "NOT_APPLICABLE"
    current = now or datetime.now(timezone.utc)
    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    delta = (expiry - current).total_seconds()
    if delta >= 0:
        return "FRESH"
    if delta >= -300:
        return "STALE"
    return "EXPIRED"


class Retriever:
    def __init__(self, store: ContextMemoryStore, config: ContextMemoryConfig) -> None:
        self.store = store
        self.config = config

    @staticmethod
    def _source_ids(connection, subject_type: str, subject_id: str) -> list[str]:
        return [
            row["artifact_id"] for row in connection.execute(
                """SELECT DISTINCT artifact_id FROM evidence_links
                   WHERE subject_type=? AND subject_id=? ORDER BY artifact_id""",
                (subject_type, subject_id),
            )
        ]

    @staticmethod
    def _claim_item(connection, row, *, reason: str, exact: bool = False) -> dict[str, Any]:
        source_ids = Retriever._source_ids(connection, "claim_version", row["version_id"])
        score = VERIFICATION_WEIGHT.get(row["verification_level"], 0)
        score += 50 if row["temporal_scope"] == "CURRENT" else 0
        score += 30 if row["lifecycle_state"] == "ACTIVE" else -25
        score += 30 if row["canonical"] else 0
        score += 100 if exact else 0
        if reason in {"failed_attempt_requested", "rejected_approach_requested"}:
            score += 100
        return {
            "item_id": row["version_id"],
            "kind": "claim",
            "entity_id": row["entity_id"],
            "entity_name": row["canonical_name"],
            "fact_key": row["fact_key"],
            "content": json.loads(row["value_json"]),
            "epistemic_state": row["epistemic_state"],
            "temporal_scope": row["temporal_scope"],
            "lifecycle_state": row["lifecycle_state"],
            "verification_level": row["verification_level"],
            "freshness": "NOT_APPLICABLE",
            "source_ids": source_ids,
            "selection_reason": reason,
            "score": float(score),
            "untrusted_evidence": False,
        }

    @staticmethod
    def _query_relevance(query: str, item: dict[str, Any]) -> float:
        normalized = normalize_text(query)
        fact_key = str(item.get("fact_key") or "")
        boosts = (
            (("status", "state", "maturity", "מצב"), "maturity", 45.0),
            (("version", "גרסה"), "version", 45.0),
            (("authority", "הרשאה", "סמכות"), "authority", 45.0),
            (("tool", "כלים"), "tool_count", 45.0),
            (("pid", "מזהה תהליך"), "pid", 55.0),
            (("mutation", "mutations", "שינויים"), "host_resource_mutations", 55.0),
            (("failed", "failure", "נכשל"), "failure", 45.0),
            (("fix", "fixed", "תיקן", "פתר"), "remediation", 45.0),
            (("why", "failed", "failure", "למה", "נכשל", "לא פתחה"), "final_root_cause", 120.0),
            (("broken", "usb-full-01", "תקלה"), "usb_full_01_observation", 85.0),
            (("tried", "before", "ניסינו", "לפני"), "failed_attempt", 120.0),
            (("final fix", "final solution", "פתרון הסופי", "הפתרון הסופי"), "final_fix", 110.0),
            (("automatically", "started", "אוטומטית", "הופעל"), "start_attribution", 110.0),
            (("stale", "unhealthy", "תקוע", "לא תקין"), "ordered_lifecycle_first", 100.0),
            (("permanent", "identity", "קבוע", "זהות"), "usb_full_01_observation", 100.0),
            (("shutdown", "כיבוי"), "shutdown_ownership", 100.0),
            (("before preparation", "לפני ההכנה"), "launcher_preparation_policy", 100.0),
        )
        score = sum(
            value for terms, key, value in boosts
            if key in fact_key and any(term in normalized for term in terms)
        )
        content = item.get("content")
        if isinstance(content, (str, int, float)):
            normalized_content = normalize_text(str(content))
            if normalized_content and (
                normalized_content == normalized
                or normalized_content in normalized
                or normalized in normalized_content
            ):
                score += 80.0
        return score

    def resolve_entities(self, query: str) -> list[dict[str, Any]]:
        normalized = normalize_text(query)
        with self.store.read() as connection:
            aliases = connection.execute(
                """SELECT a.entity_id,a.alias,a.normalized_alias,e.canonical_name,e.entity_type
                   FROM entity_aliases a JOIN entities e ON e.entity_id=a.entity_id
                   ORDER BY length(a.normalized_alias) DESC"""
            ).fetchall()
        selected: dict[str, dict[str, Any]] = {}
        for row in aliases:
            alias = row["normalized_alias"]
            if alias and (normalized == alias or alias in normalized):
                selected.setdefault(row["entity_id"], {
                    "entity_id": row["entity_id"],
                    "canonical_name": row["canonical_name"],
                    "entity_type": row["entity_type"],
                    "matched_alias": row["alias"],
                    "selection_reason": "exact_entity_match",
                })
        return list(selected.values())[:12]

    def search(
        self,
        query: str,
        *,
        include_historical: bool | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise RetrievalError("query_required")
        if len(query) > self.config.limits["max_query_chars"]:
            raise RetrievalError("query_too_large")
        limit = limit or self.config.limits["max_results"]
        if not 1 <= limit <= self.config.limits["max_results"]:
            raise RetrievalError("invalid_result_limit")
        historical = historical_intent(query) if include_historical is None else bool(include_historical)
        entities = self.resolve_entities(query)
        entity_ids = [item["entity_id"] for item in entities]
        query_redacted, redactions = redact_text(query)
        run_id = stable_id("retrieval-run", utc_now(), sha256_bytes(query.encode("utf-8")))
        results: dict[str, dict[str, Any]] = {}
        fts = fts_query(query_redacted)
        identifiers = exact_identifiers(query)
        with self.store.transaction() as connection:
            if entity_ids:
                placeholders = ",".join("?" for _ in entity_ids)
                rows = connection.execute(
                    f"""SELECT c.entity_id,c.fact_key,v.*,e.canonical_name
                        FROM claims c JOIN claim_versions v ON v.claim_id=c.claim_id
                        JOIN entities e ON e.entity_id=c.entity_id
                        WHERE c.entity_id IN ({placeholders})
                        ORDER BY v.created_at DESC""",
                    entity_ids,
                ).fetchall()
                for row in rows:
                    if not historical and (
                        row["temporal_scope"] != "CURRENT"
                        or row["lifecycle_state"] in {"FAILED_ATTEMPT","REJECTED","SUPERSEDED","RETIRED"}
                    ):
                        continue
                    if row["lifecycle_state"] == "FAILED_ATTEMPT":
                        reason = "failed_attempt_requested"
                    elif row["lifecycle_state"] == "REJECTED":
                        reason = "rejected_approach_requested"
                    else:
                        reason = (
                            "current_verified_priority"
                            if row["temporal_scope"] == "CURRENT"
                            else "historical_incident_match"
                        )
                    item = self._claim_item(
                        connection, row,
                        reason=reason,
                        exact=True,
                    )
                    if historical and row["temporal_scope"] == "HISTORICAL":
                        item["score"] += 100.0
                    results[item["item_id"]] = item
            if fts:
                claim_rows = connection.execute(
                    """SELECT f.version_id,bm25(claims_fts) AS rank,
                              c.entity_id,c.fact_key,v.*,e.canonical_name
                       FROM claims_fts f
                       JOIN claim_versions v ON v.version_id=f.version_id
                       JOIN claims c ON c.claim_id=v.claim_id
                       JOIN entities e ON e.entity_id=c.entity_id
                       WHERE claims_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (fts, limit * 3),
                ).fetchall()
                for row in claim_rows:
                    if not historical and (
                        row["temporal_scope"] != "CURRENT"
                        or row["lifecycle_state"] in {"FAILED_ATTEMPT","REJECTED","SUPERSEDED","RETIRED"}
                    ):
                        continue
                    reason = "failed_attempt_requested" if row["lifecycle_state"] == "FAILED_ATTEMPT" else "fts_match"
                    item = self._claim_item(connection, row, reason=reason)
                    if historical and row["temporal_scope"] == "HISTORICAL":
                        item["score"] += 100.0
                    item["score"] += max(0.0, 30.0 + float(row["rank"]))
                    previous = results.get(item["item_id"])
                    if previous is None or item["score"] > previous["score"]:
                        results[item["item_id"]] = item
                chunk_rows = connection.execute(
                    """SELECT f.chunk_id,f.title,f.text,bm25(document_chunks_fts) AS rank,
                              d.document_id,d.untrusted,a.artifact_id,a.temporal_scope,
                              a.lifecycle_state,a.verification_level,s.source_id
                       FROM document_chunks_fts f
                       JOIN document_chunks c ON c.chunk_id=f.chunk_id
                       JOIN documents d ON d.document_id=c.document_id
                       JOIN source_artifacts a ON a.artifact_id=d.artifact_id
                       JOIN sources s ON s.source_id=a.source_id
                       WHERE document_chunks_fts MATCH ? AND a.status='ACTIVE'
                       ORDER BY rank LIMIT ?""",
                    (fts, limit * 3),
                ).fetchall()
                for row in chunk_rows:
                    if not historical and row["lifecycle_state"] in {
                        "FAILED_ATTEMPT", "REJECTED", "SUPERSEDED", "RETIRED"
                    }:
                        continue
                    score = VERIFICATION_WEIGHT.get(row["verification_level"], 0)
                    score += 20 if row["temporal_scope"] == "CURRENT" else 0
                    score += max(0.0, 20.0 + float(row["rank"]))
                    results.setdefault(row["chunk_id"], {
                        "item_id": row["chunk_id"],
                        "kind": "document_chunk",
                        "entity_id": None,
                        "entity_name": None,
                        "fact_key": None,
                        "content": row["text"],
                        "title": row["title"],
                        "epistemic_state": "UNVERIFIED",
                        "temporal_scope": row["temporal_scope"],
                        "lifecycle_state": row["lifecycle_state"],
                        "verification_level": row["verification_level"],
                        "freshness": "NOT_APPLICABLE",
                        "source_ids": [row["artifact_id"]],
                        "selection_reason": "historical_incident_match" if historical else "fts_match",
                        "score": float(score),
                        "untrusted_evidence": True,
                    })
            for identifier in identifiers:
                chunk_rows = connection.execute(
                    """SELECT c.chunk_id,c.text,d.title,a.artifact_id,a.temporal_scope,
                              a.lifecycle_state,a.verification_level
                       FROM document_chunks c
                       JOIN documents d ON d.document_id=c.document_id
                       JOIN source_artifacts a ON a.artifact_id=d.artifact_id
                       WHERE instr(c.text,?)>0 AND a.status='ACTIVE' LIMIT ?""",
                    (identifier, limit),
                ).fetchall()
                for row in chunk_rows:
                    if not historical and row["lifecycle_state"] == "FAILED_ATTEMPT":
                        continue
                    results[row["chunk_id"]] = {
                        "item_id": row["chunk_id"],
                        "kind": "document_chunk",
                        "entity_id": None,
                        "entity_name": None,
                        "fact_key": None,
                        "content": row["text"],
                        "title": row["title"],
                        "epistemic_state": "UNVERIFIED",
                        "temporal_scope": row["temporal_scope"],
                        "lifecycle_state": row["lifecycle_state"],
                        "verification_level": row["verification_level"],
                        "freshness": "NOT_APPLICABLE",
                        "source_ids": [row["artifact_id"]],
                        "selection_reason": "exact_identifier_match",
                        "score": 200.0 + VERIFICATION_WEIGHT.get(row["verification_level"], 0),
                        "untrusted_evidence": True,
                    }
            if entity_ids:
                placeholders = ",".join("?" for _ in entity_ids)
                rel_rows = connection.execute(
                    f"""SELECT r.*,se.canonical_name AS subject_name,
                               oe.canonical_name AS object_name
                        FROM relationships r
                        JOIN entities se ON se.entity_id=r.subject_entity_id
                        JOIN entities oe ON oe.entity_id=r.object_entity_id
                        WHERE r.subject_entity_id IN ({placeholders})
                           OR r.object_entity_id IN ({placeholders})
                        LIMIT 30""",
                    entity_ids + entity_ids,
                ).fetchall()
                for row in rel_rows:
                    source_ids = self._source_ids(connection, "relationship", row["relationship_id"])
                    results.setdefault(row["relationship_id"], {
                        "item_id": row["relationship_id"],
                        "kind": "relationship",
                        "entity_id": row["subject_entity_id"],
                        "entity_name": row["subject_name"],
                        "fact_key": row["predicate"],
                        "content": {
                            "subject": row["subject_name"],
                            "predicate": row["predicate"],
                            "object": row["object_name"],
                            "object_entity_id": row["object_entity_id"],
                        },
                        "epistemic_state": row["epistemic_state"],
                        "temporal_scope": row["temporal_scope"],
                        "lifecycle_state": row["lifecycle_state"],
                        "verification_level": row["verification_level"],
                        "freshness": "NOT_APPLICABLE",
                        "source_ids": source_ids,
                        "selection_reason": "relationship_expansion",
                        # A relationship attached to an exact entity resolution is
                        # structured evidence, not a vague lexical candidate. Keep
                        # it ahead of unrelated chunks selected by broad FTS terms.
                        "score": 250.0,
                        "untrusted_evidence": False,
                    })
            for item in results.values():
                item["score"] += self._query_relevance(query, item)
            ordered = sorted(results.values(), key=lambda item: (-item["score"], item["item_id"]))[:limit]
            diagnostics = {
                "historical_intent": historical,
                "resolved_entities": len(entities),
                "exact_identifiers": identifiers,
                "fts_query_used": bool(fts),
                "redactions": redactions,
                "candidate_count": len(results),
                "selected_count": len(ordered),
                "semantic_candidates": 0,
            }
            now = utc_now()
            connection.execute(
                """INSERT INTO retrieval_runs
                   (retrieval_run_id,query_redacted,query_sha256,intent_json,
                    started_at,completed_at,result_count,diagnostics_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    run_id, query_redacted, sha256_bytes(query.encode("utf-8")),
                    canonical_json({"historical": historical}), now, now,
                    len(ordered), canonical_json(diagnostics),
                ),
            )
            for ordinal, item in enumerate(ordered):
                connection.execute(
                    """INSERT INTO retrieval_results
                       (retrieval_result_id,retrieval_run_id,ordinal,result_type,
                        record_id,score,selection_reason,source_ids_json)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        stable_id("retrieval-result", run_id, ordinal),
                        run_id, ordinal, item["kind"], item["item_id"],
                        item["score"], item["selection_reason"],
                        canonical_json(item["source_ids"]),
                    ),
                )
        return {
            "schema": "aag-retrieval-result-set-v1",
            "retrieval_run_id": run_id,
            "query": query_redacted,
            "entities": entities,
            "results": ordered,
            "diagnostics": diagnostics,
        }

    def source_catalog(self, artifact_ids: list[str]) -> list[dict[str, Any]]:
        if not artifact_ids:
            return []
        artifact_ids = sorted(set(artifact_ids))
        placeholders = ",".join("?" for _ in artifact_ids)
        with self.store.read() as connection:
            rows = connection.execute(
                f"""SELECT a.artifact_id,a.source_id,a.uri,a.original_sha256,
                           a.parser_version,a.verification_level,a.temporal_scope,
                           a.lifecycle_state,s.source_type,s.title
                    FROM source_artifacts a JOIN sources s ON s.source_id=a.source_id
                    WHERE a.artifact_id IN ({placeholders}) ORDER BY a.artifact_id""",
                artifact_ids,
            ).fetchall()
        if len(rows) != len(artifact_ids):
            raise ContextMemoryStoreError("retrieval_referenced_missing_source")
        return [dict(row) for row in rows]
