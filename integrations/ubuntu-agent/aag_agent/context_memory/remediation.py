"""Evidence-bound remediation proposals with no execution surface."""

from __future__ import annotations

from typing import Any

from .models import canonical_json, stable_id, utc_now
from .retrieval import Retriever
from .store import ContextMemoryStore


class RemediationError(ValueError):
    pass


class RemediationPlanner:
    def __init__(self, store: ContextMemoryStore, retriever: Retriever) -> None:
        self.store = store
        self.retriever = retriever

    def plan(
        self,
        query: str,
        *,
        additional_evidence_ids: list[str] | None = None,
        additional_entities: list[str] | None = None,
    ) -> dict[str, Any]:
        retrieval = self.retriever.search(query, include_historical=True)
        # A remediation proposal is an execution-adjacent contract. Raw imported
        # text is intentionally excluded from its diagnosis fields even when the
        # same text is useful in a conversational context package. Only typed,
        # structured backend records may ground the proposal itself.
        structured_results = [
            item for item in retrieval["results"]
            if item.get("kind") in {"claim", "relationship", "observation"}
            and not item.get("untrusted_evidence", False)
            # Historical failed/rejected/superseded approaches are valuable in
            # conversational diagnosis, but must never ground a fresh
            # remediation proposal as if they were current recommendations.
            and item.get("lifecycle_state") not in {
                "FAILED_ATTEMPT", "REJECTED", "SUPERSEDED", "RETIRED"
            }
        ]
        evidence_ids = sorted({
            source_id
            for item in structured_results
            for source_id in item.get("source_ids", [])
        } | set(additional_evidence_ids or []))
        if not evidence_ids or not self.store.evidence_exists(evidence_ids):
            return {
                "schema": "aag-remediation-plan-unavailable-v1",
                "plan_available": False,
                "error": "grounding_evidence_required",
                "clarification_required": True,
                "execution_authority": "NONE",
                "execution_status": "not_executed",
                "read_only": True,
                "mutated": False,
                "zero_mutations": True,
                "commands": [],
            }
        entities = sorted({
            item["entity_id"] for item in structured_results if item.get("entity_id")
        } | set(additional_entities or []))
        known = [
            {
                "item_id": item["item_id"],
                "fact_key": item.get("fact_key"),
                "content": item["content"],
                "verification_level": item["verification_level"],
                "temporal_scope": item["temporal_scope"],
            }
            for item in structured_results[:8]
        ]
        candidate_id = stable_id("remediation-candidate", query, evidence_ids)
        plan_id = stable_id("remediation-plan", candidate_id)
        plan = {
            "schema": "aag-remediation-plan-v1",
            "plan_id": plan_id,
            "diagnosis": {
                "query": query,
                "state": "EVIDENCE_BOUND_PROPOSAL",
                "knowns": known,
                "unknowns": [
                    "No operation is authorized until preconditions are refreshed and an operator approves a governed executor."
                ],
            },
            "target_entities": entities,
            "evidence_ids": evidence_ids,
            "operation_types": ["OBSERVE_ONLY", "MANUAL_GOVERNED_CHANGE"],
            "risk": "R1",
            "required_approval": "SEPARATE_GOVERNED_STAGE",
            "backup_requirement": {
                "required_before_change": True,
                "scope": "exact files/resources selected by a future typed executor",
            },
            "rollback_plan": {
                "required": True,
                "status": "proposal_only",
                "rule": "rollback must be defined and validated before execution authority is issued",
            },
            "verification_plan": {
                "required": True,
                "checks": [
                    "refresh the relevant typed live observation",
                    "verify the diagnosed signature still exists",
                    "run operation-specific post-checks",
                    "run regression and integrity checks",
                ],
            },
            "success_criteria": [
                "target symptom no longer reproduces",
                "typed post-checks pass",
                "no protected invariant regresses",
            ],
            "failure_criteria": [
                "precondition changed",
                "post-check failed or became indeterminate",
                "integrity or safety invariant failed",
            ],
            "execution_authority": "NONE",
            "execution_status": "not_executed",
            "read_only": True,
            "mutated": False,
            "zero_mutations": True,
        }
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO remediation_candidates
                   (remediation_candidate_id,query,diagnosis_json,status,created_at)
                   VALUES (?,?,?,'PROPOSED',?)""",
                (candidate_id, query, canonical_json(plan["diagnosis"]), utc_now()),
            )
            connection.execute(
                """INSERT OR IGNORE INTO remediation_plans
                   (plan_id,remediation_candidate_id,plan_json,execution_authority,
                    execution_status,created_at)
                   VALUES (?,?,?,'NONE','not_executed',?)""",
                (plan_id, candidate_id, canonical_json(plan), utc_now()),
            )
            for artifact_id in evidence_ids:
                connection.execute(
                    "INSERT OR IGNORE INTO remediation_plan_evidence(plan_id,artifact_id) VALUES (?,?)",
                    (plan_id, artifact_id),
                )
        return plan
