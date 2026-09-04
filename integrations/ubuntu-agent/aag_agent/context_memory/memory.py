"""Governed memory-candidate validation and canonical promotion."""

from __future__ import annotations

import json
from typing import Any

from .config import ContextMemoryConfig
from .models import canonical_json, normalize_text, sha256_bytes, stable_id, utc_now
from .store import ContextMemoryStore

ORIGIN_STATES = {
    "LLM_OUTPUT": ("INFERRED", "INFERRED"),
    "COMPLETED_TASK": ("CANDIDATE", "ARTIFACT_VERIFIED"),
    "USER_STATEMENT": ("USER_REPORTED", "USER_CONFIRMED"),
    "TOOL_RESULT": ("CANDIDATE", "DIRECT_LIVE"),
}
PROMOTABLE_VERIFICATION = {"DIRECT_LIVE", "TEST_VERIFIED", "ARTIFACT_VERIFIED"}


class MemoryValidationError(ValueError):
    pass


class MemoryPipeline:
    def __init__(self, store: ContextMemoryStore, config: ContextMemoryConfig) -> None:
        self.store = store
        self.config = config

    def submit(
        self,
        *,
        entity_id: str,
        fact_key: str,
        value: Any,
        origin: str,
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        if origin not in ORIGIN_STATES:
            raise MemoryValidationError("invalid_memory_origin")
        if not isinstance(entity_id, str) or not isinstance(fact_key, str) or not fact_key:
            raise MemoryValidationError("invalid_memory_identity")
        if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
            raise MemoryValidationError("invalid_memory_evidence")
        encoded = canonical_json(value)
        if len(encoded.encode("utf-8")) > 16000:
            raise MemoryValidationError("memory_candidate_too_large")
        content_hash = sha256_bytes(
            canonical_json({"entity_id": entity_id, "fact_key": fact_key, "value": value}).encode("utf-8")
        )
        candidate_id = stable_id("memory-candidate", entity_id, fact_key, content_hash, origin)
        epistemic, verification = ORIGIN_STATES[origin]
        with self.store.transaction() as connection:
            if connection.execute("SELECT 1 FROM entities WHERE entity_id=?", (entity_id,)).fetchone() is None:
                raise MemoryValidationError("memory_entity_missing")
            if not self.store.evidence_exists(evidence_ids, connection):
                raise MemoryValidationError("memory_evidence_missing")
            connection.execute(
                """INSERT OR IGNORE INTO memory_candidates
                   (candidate_id,entity_id,fact_key,value_json,origin,epistemic_state,
                    verification_level,status,rejection_reason,content_sha256,created_at)
                   VALUES (?,?,?,?,?,?,?,'PENDING',NULL,?,?)""",
                (
                    candidate_id, entity_id, fact_key, encoded, origin,
                    epistemic, verification, content_hash, utc_now(),
                ),
            )
            for artifact_id in evidence_ids:
                connection.execute(
                    """INSERT OR IGNORE INTO evidence_links
                       (evidence_link_id,subject_type,subject_id,artifact_id,chunk_id,
                        evidence_role,created_at) VALUES (?,?,?,?,NULL,'supports',?)""",
                    (
                        stable_id("evidence", "memory_candidate", candidate_id, artifact_id),
                        "memory_candidate", candidate_id, artifact_id, utc_now(),
                    ),
                )
            self.store.append_memory_audit(
                connection, "MEMORY_CANDIDATE_ACCEPTED",
                {"candidate_id": candidate_id, "origin": origin, "epistemic_state": epistemic},
            )
        return self.get(candidate_id)

    def get(self, candidate_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise MemoryValidationError("memory_candidate_missing")
            evidence = [
                item["artifact_id"] for item in connection.execute(
                    """SELECT artifact_id FROM evidence_links
                       WHERE subject_type='memory_candidate' AND subject_id=?
                       ORDER BY artifact_id""",
                    (candidate_id,),
                )
            ]
        return {
            "schema": "aag-memory-candidate-v1",
            "candidate_id": row["candidate_id"],
            "entity_id": row["entity_id"],
            "fact_key": row["fact_key"],
            "value": json.loads(row["value_json"]),
            "origin": row["origin"],
            "epistemic_state": row["epistemic_state"],
            "verification_level": row["verification_level"],
            "evidence_ids": evidence,
            "status": row["status"],
            "rejection_reason": row["rejection_reason"],
        }

    def promote(self, candidate_id: str) -> dict[str, Any]:
        promotion_id = stable_id("promotion", candidate_id)
        with self.store.transaction() as connection:
            candidate = connection.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise MemoryValidationError("memory_candidate_missing")
            if candidate["status"] == "PROMOTED":
                row = connection.execute(
                    "SELECT * FROM canonical_promotion_candidates WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()
                return dict(row)
            evidence = [
                row["artifact_id"] for row in connection.execute(
                    """SELECT artifact_id FROM evidence_links
                       WHERE subject_type='memory_candidate' AND subject_id=?""",
                    (candidate_id,),
                )
            ]
            reason: str | None = None
            if candidate["fact_key"] not in self.config.canonical_fact_keys:
                reason = "fact_type_not_allowlisted"
            elif candidate["origin"] in {"LLM_OUTPUT", "USER_STATEMENT"}:
                reason = "origin_cannot_self_promote"
            elif candidate["verification_level"] not in PROMOTABLE_VERIFICATION:
                reason = "verification_level_not_promotable"
            elif not self.store.evidence_exists(evidence, connection):
                reason = "evidence_missing"
            if reason:
                connection.execute(
                    "UPDATE memory_candidates SET status='REJECTED',rejection_reason=? WHERE candidate_id=?",
                    (reason, candidate_id),
                )
                connection.execute(
                    """INSERT OR REPLACE INTO canonical_promotion_candidates
                       (promotion_id,candidate_id,requested_at,status,decision_reason,promoted_version_id)
                       VALUES (?, ?, ?, 'REJECTED', ?, NULL)""",
                    (promotion_id, candidate_id, utc_now(), reason),
                )
                self.store.append_memory_audit(
                    connection, "CANONICAL_PROMOTION_REJECTED",
                    {"candidate_id": candidate_id, "reason": reason},
                )
                return {
                    "promotion_id": promotion_id,
                    "candidate_id": candidate_id,
                    "status": "REJECTED",
                    "decision_reason": reason,
                    "promoted_version_id": None,
                }
            current = connection.execute(
                """SELECT c.claim_id,v.version_id,v.value_json
                   FROM claims c JOIN claim_versions v ON v.claim_id=c.claim_id
                   WHERE c.entity_id=? AND c.fact_key=? AND v.canonical=1
                     AND v.lifecycle_state='ACTIVE' AND v.valid_to IS NULL""",
                (candidate["entity_id"], candidate["fact_key"]),
            ).fetchone()
            if current is not None and current["value_json"] != candidate["value_json"]:
                conflict_id = stable_id(
                    "conflict", candidate["entity_id"], candidate["fact_key"],
                    current["version_id"], candidate_id,
                )
                now = utc_now()
                connection.execute(
                    """INSERT OR IGNORE INTO conflicts
                       (conflict_id,entity_id,fact_key,canonical_version_id,observation_id,
                        candidate_id,canonical_value_json,observed_value_json,
                        possible_explanations_json,required_verification_json,status,
                        resolution_json,created_at,updated_at)
                       VALUES (?,?,?,?,NULL,?,?,?,
                               ?,?,'OPEN',NULL,?,?)""",
                    (
                        conflict_id, candidate["entity_id"], candidate["fact_key"],
                        current["version_id"], candidate_id, current["value_json"],
                        candidate["value_json"],
                        canonical_json(["intentional_transition","stale_canonical_fact","incorrect_candidate"]),
                        canonical_json(["direct_live_verification","passing_test","governed_operator_decision"]),
                        now, now,
                    ),
                )
                connection.execute(
                    "UPDATE memory_candidates SET status='CONFLICTED',rejection_reason='canonical_conflict' WHERE candidate_id=?",
                    (candidate_id,),
                )
                connection.execute(
                    """INSERT OR REPLACE INTO canonical_promotion_candidates
                       (promotion_id,candidate_id,requested_at,status,decision_reason,promoted_version_id)
                       VALUES (?, ?, ?, 'CONFLICTED', 'canonical_conflict', NULL)""",
                    (promotion_id, candidate_id, now),
                )
                self.store.append_memory_audit(
                    connection, "CANONICAL_PROMOTION_CONFLICT",
                    {"candidate_id": candidate_id, "conflict_id": conflict_id},
                )
                return {
                    "promotion_id": promotion_id,
                    "candidate_id": candidate_id,
                    "status": "CONFLICTED",
                    "decision_reason": "canonical_conflict",
                    "conflict_id": conflict_id,
                    "promoted_version_id": None,
                }
            if current is not None:
                version_id = current["version_id"]
            else:
                claim_id = stable_id("claim", candidate["entity_id"], candidate["fact_key"])
                connection.execute(
                    "INSERT OR IGNORE INTO claims(claim_id,entity_id,fact_key,created_at) VALUES (?,?,?,?)",
                    (claim_id, candidate["entity_id"], candidate["fact_key"], utc_now()),
                )
                version_number = int(connection.execute(
                    "SELECT count(*) FROM claim_versions WHERE claim_id=?", (claim_id,)
                ).fetchone()[0]) + 1
                version_id = stable_id("claim-version", claim_id, version_number, candidate["value_json"])
                connection.execute(
                    """INSERT INTO claim_versions
                       (version_id,claim_id,version_number,value_json,epistemic_state,
                        temporal_scope,lifecycle_state,verification_level,valid_from,
                        valid_to,supersedes_version_id,canonical,created_at)
                       VALUES (?,?,?,?,?,'CURRENT','ACTIVE',?,?,NULL,NULL,1,?)""",
                    (
                        version_id, claim_id, version_number, candidate["value_json"],
                        "VERIFIED", candidate["verification_level"], utc_now(), utc_now(),
                    ),
                )
                entity = connection.execute(
                    "SELECT canonical_name FROM entities WHERE entity_id=?",
                    (candidate["entity_id"],),
                ).fetchone()
                aliases = " ".join(row["alias"] for row in connection.execute(
                    "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                    (candidate["entity_id"],),
                ))
                connection.execute(
                    "INSERT INTO claims_fts(version_id,entity_name,fact_key,value_text,aliases) VALUES (?,?,?,?,?)",
                    (
                        version_id, entity["canonical_name"], candidate["fact_key"],
                        candidate["value_json"], aliases,
                    ),
                )
                for artifact_id in evidence:
                    connection.execute(
                        """INSERT OR IGNORE INTO evidence_links
                           (evidence_link_id,subject_type,subject_id,artifact_id,chunk_id,
                            evidence_role,created_at)
                           VALUES (?,?,?,?,NULL,'supports',?)""",
                        (
                            stable_id("evidence", "claim_version", version_id, artifact_id),
                            "claim_version", version_id, artifact_id, utc_now(),
                        ),
                    )
            connection.execute(
                "UPDATE memory_candidates SET status='PROMOTED',rejection_reason=NULL WHERE candidate_id=?",
                (candidate_id,),
            )
            connection.execute(
                """INSERT OR REPLACE INTO canonical_promotion_candidates
                   (promotion_id,candidate_id,requested_at,status,decision_reason,promoted_version_id)
                   VALUES (?, ?, ?, 'PROMOTED', 'deterministic_gate_passed', ?)""",
                (promotion_id, candidate_id, utc_now(), version_id),
            )
            self.store.append_memory_audit(
                connection, "CANONICAL_PROMOTION_COMMITTED",
                {"candidate_id": candidate_id, "version_id": version_id},
            )
        return {
            "promotion_id": promotion_id,
            "candidate_id": candidate_id,
            "status": "PROMOTED",
            "decision_reason": "deterministic_gate_passed",
            "promoted_version_id": version_id,
        }

    def reconcile_conflict(
        self,
        conflict_id: str,
        *,
        decision: str,
        evidence_ids: list[str],
        operator_governed: bool,
    ) -> dict[str, Any]:
        """Resolve a promotion conflict through an explicit deterministic gate.

        This method is deliberately absent from the LLM/Bridge service contract.
        It exists for a future governed operator workflow and proves that version
        supersession can be transactional without silently overwriting history.
        """
        if decision not in {"KEEP_CANONICAL", "PROMOTE_CANDIDATE"}:
            raise MemoryValidationError("invalid_conflict_decision")
        if operator_governed is not True:
            raise MemoryValidationError("operator_governance_required")
        if not isinstance(evidence_ids, list) or not all(
            isinstance(item, str) for item in evidence_ids
        ):
            raise MemoryValidationError("invalid_resolution_evidence")
        with self.store.transaction() as connection:
            if not self.store.evidence_exists(evidence_ids, connection):
                raise MemoryValidationError("resolution_evidence_missing")
            conflict = connection.execute(
                "SELECT * FROM conflicts WHERE conflict_id=?",
                (conflict_id,),
            ).fetchone()
            if conflict is None:
                raise MemoryValidationError("conflict_missing")
            if conflict["status"] != "OPEN":
                raise MemoryValidationError("conflict_not_open")
            candidate_id = conflict["candidate_id"]
            candidate = None
            if candidate_id is not None:
                candidate = connection.execute(
                    "SELECT * FROM memory_candidates WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()
            now = utc_now()
            resolution = {
                "decision": decision,
                "evidence_ids": sorted(evidence_ids),
                "operator_governed": True,
                "resolved_at": now,
            }
            if decision == "KEEP_CANONICAL":
                if candidate is not None:
                    connection.execute(
                        """UPDATE memory_candidates
                           SET status='REJECTED',rejection_reason='conflict_resolved_keep_canonical'
                           WHERE candidate_id=?""",
                        (candidate_id,),
                    )
                    connection.execute(
                        """UPDATE canonical_promotion_candidates
                           SET status='REJECTED',decision_reason='conflict_resolved_keep_canonical'
                           WHERE candidate_id=?""",
                        (candidate_id,),
                    )
                promoted_version_id = conflict["canonical_version_id"]
            else:
                if candidate is None:
                    raise MemoryValidationError("conflict_has_no_promotable_candidate")
                if candidate["fact_key"] not in self.config.canonical_fact_keys:
                    raise MemoryValidationError("fact_type_not_allowlisted")
                if (
                    candidate["origin"] in {"LLM_OUTPUT", "USER_STATEMENT"}
                    or candidate["verification_level"] not in PROMOTABLE_VERIFICATION
                ):
                    raise MemoryValidationError("candidate_not_promotable")
                current = connection.execute(
                    """SELECT c.claim_id,v.* FROM claims c
                       JOIN claim_versions v ON v.claim_id=c.claim_id
                       WHERE v.version_id=? AND v.canonical=1
                         AND v.lifecycle_state='ACTIVE' AND v.valid_to IS NULL""",
                    (conflict["canonical_version_id"],),
                ).fetchone()
                if current is None:
                    raise MemoryValidationError("canonical_changed_since_conflict")
                version_number = int(connection.execute(
                    "SELECT max(version_number) FROM claim_versions WHERE claim_id=?",
                    (current["claim_id"],),
                ).fetchone()[0]) + 1
                promoted_version_id = stable_id(
                    "claim-version", current["claim_id"], version_number,
                    candidate["value_json"],
                )
                connection.execute(
                    """UPDATE claim_versions
                       SET canonical=0,lifecycle_state='SUPERSEDED',valid_to=?
                       WHERE version_id=?""",
                    (now, current["version_id"]),
                )
                connection.execute(
                    """INSERT INTO claim_versions
                       (version_id,claim_id,version_number,value_json,epistemic_state,
                        temporal_scope,lifecycle_state,verification_level,valid_from,
                        valid_to,supersedes_version_id,canonical,created_at)
                       VALUES (?,?,?,?,?,'CURRENT','ACTIVE',?,?,NULL,?,1,?)""",
                    (
                        promoted_version_id, current["claim_id"], version_number,
                        candidate["value_json"], "VERIFIED",
                        candidate["verification_level"], now,
                        current["version_id"], now,
                    ),
                )
                entity = connection.execute(
                    "SELECT canonical_name FROM entities WHERE entity_id=?",
                    (candidate["entity_id"],),
                ).fetchone()
                aliases = " ".join(row["alias"] for row in connection.execute(
                    "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                    (candidate["entity_id"],),
                ))
                connection.execute(
                    """INSERT INTO claims_fts
                       (version_id,entity_name,fact_key,value_text,aliases)
                       VALUES (?,?,?,?,?)""",
                    (
                        promoted_version_id, entity["canonical_name"],
                        candidate["fact_key"], candidate["value_json"], aliases,
                    ),
                )
                candidate_evidence = [
                    row["artifact_id"] for row in connection.execute(
                        """SELECT artifact_id FROM evidence_links
                           WHERE subject_type='memory_candidate' AND subject_id=?""",
                        (candidate_id,),
                    )
                ]
                for artifact_id in sorted(set(candidate_evidence + evidence_ids)):
                    connection.execute(
                        """INSERT OR IGNORE INTO evidence_links
                           (evidence_link_id,subject_type,subject_id,artifact_id,chunk_id,
                            evidence_role,created_at)
                           VALUES (?,?,?,?,NULL,'supports',?)""",
                        (
                            stable_id(
                                "evidence", "claim_version",
                                promoted_version_id, artifact_id,
                            ),
                            "claim_version", promoted_version_id,
                            artifact_id, now,
                        ),
                    )
                connection.execute(
                    """UPDATE memory_candidates
                       SET status='PROMOTED',rejection_reason=NULL WHERE candidate_id=?""",
                    (candidate_id,),
                )
                connection.execute(
                    """UPDATE canonical_promotion_candidates
                       SET status='PROMOTED',decision_reason='governed_conflict_reconciliation',
                           promoted_version_id=? WHERE candidate_id=?""",
                    (promoted_version_id, candidate_id),
                )
            connection.execute(
                """UPDATE conflicts SET status='RESOLVED',resolution_json=?,updated_at=?
                   WHERE conflict_id=?""",
                (canonical_json(resolution), now, conflict_id),
            )
            self.store.append_memory_audit(
                connection, "CANONICAL_CONFLICT_RECONCILED",
                {
                    "conflict_id": conflict_id, "decision": decision,
                    "promoted_version_id": promoted_version_id,
                },
            )
        return {
            "conflict_id": conflict_id,
            "status": "RESOLVED",
            "decision": decision,
            "promoted_version_id": promoted_version_id,
            "operator_governed": True,
        }
