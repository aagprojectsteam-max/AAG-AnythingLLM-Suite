"""Validated Stage 16 Context/Memory links for remediation lifecycle records."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from aag_agent.context_memory.models import canonical_json, stable_id, utc_now
from aag_agent.context_memory.service import ContextMemoryService


class ContextLinkError(ValueError):
    pass


class ContextLinker:
    def __init__(self, service: ContextMemoryService) -> None:
        self.service = service
        self.store = service.store

    def validate_context_plan(self, context_plan_id: str, task_id: str | None) -> dict[str, Any]:
        if not isinstance(context_plan_id, str) or not context_plan_id.startswith("remediation-plan:"):
            raise ContextLinkError("invalid_context_plan_id")
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM remediation_plans WHERE plan_id=?", (context_plan_id,)
            ).fetchone()
            if row is None:
                raise ContextLinkError("context_plan_missing")
            if row["execution_authority"] != "NONE" or row["execution_status"] != "not_executed":
                raise ContextLinkError("context_plan_authority_invalid")
            evidence = [dict(item) for item in connection.execute(
                """SELECT a.artifact_id,a.original_sha256,a.verification_level
                   FROM remediation_plan_evidence r
                   JOIN source_artifacts a ON a.artifact_id=r.artifact_id
                   WHERE r.plan_id=? ORDER BY a.artifact_id""",
                (context_plan_id,),
            )]
            if not evidence:
                raise ContextLinkError("context_plan_evidence_missing")
            if task_id is not None:
                task = connection.execute("SELECT closure_status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if task is None:
                    raise ContextLinkError("context_task_missing")
                if task["closure_status"] not in {"OPEN", "BLOCKED"}:
                    raise ContextLinkError("context_task_closed")
        return {"context_plan": json.loads(row["plan_json"]), "evidence": evidence}

    def record_live_evidence(self, evidence: Mapping[str, Any]) -> dict[str, str]:
        encoded = canonical_json(evidence)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        observed_at = evidence.get("observed_at")
        uri = f"live://remediation/bridge/{observed_at}/{digest}"
        artifact_id = stable_id("artifact", "live-tool-remediation", uri, digest, "bridge-detector-v1")
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO sources
                   (source_id,source_type,title,authority_rank,read_only,created_at)
                   VALUES ('live-tool-remediation','live_tool','AAG governed remediation observations',100,1,?)""",
                (utc_now(),),
            )
            connection.execute(
                """INSERT OR IGNORE INTO source_artifacts
                   (artifact_id,source_id,uri,original_sha256,normalized_sha256,
                    parser_version,byte_size,modified_at,ingested_at,temporal_scope,
                    lifecycle_state,verification_level,status)
                   VALUES (?,'live-tool-remediation',?,?,?,'bridge-detector-v1',?,NULL,?,
                           'LIVE_OBSERVATION','ACTIVE','DIRECT_LIVE','ACTIVE')""",
                (artifact_id, uri, digest, digest, len(encoded.encode("utf-8")), utc_now()),
            )
        return {"artifact_id": artifact_id, "original_sha256": digest, "verification_level": "DIRECT_LIVE"}

    def ensure_incident(self, incident_id: str, title: str) -> None:
        with self.store.transaction() as connection:
            if connection.execute("SELECT 1 FROM entities WHERE entity_id='entity:bridge'").fetchone() is None:
                raise ContextLinkError("bridge_entity_missing")
            connection.execute(
                """INSERT OR IGNORE INTO incidents
                   (incident_id,title,entity_id,status,severity,opened_at,closed_at,created_at)
                   VALUES (?,?,'entity:bridge','OPEN','LOW',?,NULL,?)""",
                (incident_id, title, utc_now(), utc_now()),
            )

    def record_outcome(
        self,
        *,
        plan: Mapping[str, Any],
        attempt_id: str,
        outcome: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        encoded = canonical_json(result)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        uri = f"remediation://attempt/{attempt_id}/{digest}"
        artifact_id = stable_id("artifact", "remediation-result", uri, digest, "remediation-result-v1")
        succeeded = outcome == "SUCCEEDED_VERIFIED"
        lifecycle = "VERIFIED_SUCCESS" if succeeded else "FAILED_ATTEMPT"
        action_id = stable_id("historical-action", attempt_id, outcome)
        event_id = stable_id("incident-event", plan["incident_id"], attempt_id, outcome)
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO sources
                   (source_id,source_type,title,authority_rank,read_only,created_at)
                   VALUES ('remediation-result','live_tool','Governed remediation results',100,1,?)""",
                (utc_now(),),
            )
            connection.execute(
                """INSERT OR IGNORE INTO source_artifacts
                   (artifact_id,source_id,uri,original_sha256,normalized_sha256,
                    parser_version,byte_size,modified_at,ingested_at,temporal_scope,
                    lifecycle_state,verification_level,status)
                   VALUES (?,'remediation-result',?,?,?,'remediation-result-v1',?,NULL,?,
                           'HISTORICAL',?,'DIRECT_LIVE','ACTIVE')""",
                (artifact_id, uri, digest, digest, len(encoded.encode("utf-8")), utc_now(), lifecycle),
            )
            connection.execute(
                """INSERT OR IGNORE INTO historical_actions
                   (action_id,incident_id,action_type,lifecycle_state,result,executed_at,details_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (action_id, plan["incident_id"], plan["operation_id"], lifecycle, outcome, utc_now(), encoded),
            )
            connection.execute(
                """INSERT OR IGNORE INTO incident_events
                   (event_id,incident_id,event_type,event_at,details_json)
                   VALUES (?,?,?,?,?)""",
                (event_id, plan["incident_id"], outcome, utc_now(), encoded),
            )
            if succeeded:
                connection.execute(
                    "UPDATE incidents SET status='RESOLVED',closed_at=? WHERE incident_id=?",
                    (utc_now(), plan["incident_id"]),
                )
        task_result = None
        if plan.get("task_id"):
            task_result = self.service.tasks.update(
                plan["task_id"],
                {
                    "tools_used": ["safe-remediation-v1:" + plan["operation_id"]],
                    "observations": [{"attempt_id": attempt_id, "outcome": outcome}],
                    "evidence_ids": [artifact_id],
                },
            )
        candidate = None
        if succeeded:
            candidate = self.service.memory.submit(
                entity_id="entity:bridge",
                fact_key="bridge.last_verified_remediation",
                value={"attempt_id": attempt_id, "operation_id": plan["operation_id"], "outcome": outcome},
                origin="TOOL_RESULT",
                evidence_ids=[artifact_id],
            )
        return {
            "artifact_id": artifact_id,
            "historical_action_id": action_id,
            "incident_event_id": event_id,
            "task_updated": task_result is not None,
            "memory_candidate": candidate,
            "canonical_promoted": False,
        }
