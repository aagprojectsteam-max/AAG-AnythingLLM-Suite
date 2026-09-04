"""Typed Context/Memory service boundary used by CLI and additive Bridge route."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from aag_agent.diagnostics import diagnose_many

from .config import ContextMemoryConfig, load_config
from .context import ContextAssembler
from .ingestion import IngestionPipeline, redact_text
from .memory import MemoryPipeline
from .models import canonical_json, sha256_bytes, stable_id, utc_now
from .remediation import RemediationPlanner
from .retrieval import Retriever
from .store import ContextMemoryStore
from .tasks import TaskStore

OPERATIONS = {
    "status", "context", "historical", "current_bridge",
    "current_performance", "task", "remediation_plan",
}
ALLOWED_FIELDS = {"operation", "query", "task_id", "budget_tier"}


class ContextServiceError(ValueError):
    pass


class ContextMemoryService:
    def __init__(
        self,
        config: ContextMemoryConfig | None = None,
        store: ContextMemoryStore | None = None,
        *,
        diagnostic_runner=diagnose_many,
    ) -> None:
        self.config = config or load_config()
        self.store = store or ContextMemoryStore(
            self.config.database_path,
            busy_timeout_ms=self.config.busy_timeout_ms,
            journal_mode=self.config.journal_mode,
            synchronous=self.config.synchronous,
        )
        self.diagnostic_runner = diagnostic_runner
        self.retriever = Retriever(self.store, self.config)
        self.tasks = TaskStore(
            self.store,
            max_json_bytes=self.config.limits["max_task_json_bytes"],
        )
        self.assembler = ContextAssembler(self.store, self.config, self.retriever, self.tasks)
        self.memory = MemoryPipeline(self.store, self.config)
        self.remediation = RemediationPlanner(self.store, self.retriever)
        self.ingestion = IngestionPipeline(self.store, self.config)

    def initialize(self, *, ingest: bool = False) -> dict[str, Any]:
        self.store.migrate()
        result: dict[str, Any] = {
            "schema": "aag-context-memory-initialization-v1",
            "integrity": self.store.integrity(),
        }
        if ingest:
            result["ingestion"] = self.ingestion.run_configured(apply=True)
        result["stats"] = self.store.stats()
        return result

    def _live_artifact(self, profile: str, payload: Mapping[str, Any]) -> str:
        encoded = canonical_json(payload)
        digest = sha256_bytes(encoded.encode("utf-8"))
        captured = payload.get("captured_at", utc_now())
        uri = f"live://diagnose/{profile}/{captured}"
        artifact_id = stable_id("artifact", "live-tool-context", uri, digest, "diagnostic-v1")
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO sources
                   (source_id,source_type,title,authority_rank,read_only,created_at)
                   VALUES ('live-tool-context','live_tool','AAG typed live diagnostics',100,1,?)""",
                (utc_now(),),
            )
            connection.execute(
                """INSERT OR IGNORE INTO source_artifacts
                   (artifact_id,source_id,uri,original_sha256,normalized_sha256,
                    parser_version,byte_size,modified_at,ingested_at,temporal_scope,
                    lifecycle_state,verification_level,status)
                   VALUES (?,'live-tool-context',?,?,?,'diagnostic-v1',?,NULL,?,
                           'LIVE_OBSERVATION','ACTIVE','DIRECT_LIVE','ACTIVE')""",
                (
                    artifact_id, uri, digest, digest, len(encoded.encode("utf-8")), utc_now(),
                ),
            )
        return artifact_id

    @staticmethod
    def _iso_from_epoch(value: Any) -> str:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
        return utc_now()

    def _run_live(
        self,
        *,
        profile: str,
        inputs: Mapping[str, Any],
        entity_id: str | None,
        fact_key: str,
        freshness_class: str,
    ) -> tuple[dict[str, Any], str]:
        session = self.diagnostic_runner([{"profile": profile, "inputs": dict(inputs)}])
        if (
            session.get("schema") != "aag-diagnostic-session-v1"
            or session.get("read_only") is not True
            or session.get("mutated") is not False
            or session.get("status") == "ERROR"
        ):
            raise ContextServiceError("typed_live_refresh_failed")
        artifact_id = self._live_artifact(profile, session)
        observed_at = self._iso_from_epoch(session.get("captured_at"))
        ttl = self.config.freshness_ttl_seconds[freshness_class]
        expires_at = None
        if ttl is not None:
            expires_at = (
                datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                + timedelta(seconds=ttl)
            ).isoformat().replace("+00:00", "Z")
        value: Any = session
        if profile == "service":
            value = {}
            for bundle in session.get("bundles", []):
                systemd = bundle.get("facts", {}).get("systemd", {})
                if systemd.get("state") == "OBSERVED":
                    value = systemd.get("value", {})
                    break
        elif profile == "performance":
            value = {
                "status": session.get("status"),
                "facts": [
                    {
                        "profile": bundle.get("profile"),
                        "status": bundle.get("status"),
                        "facts": bundle.get("facts", {}),
                    }
                    for bundle in session.get("bundles", [])
                ],
            }
        observation_id = self.store.add_observation(
            entity_id=entity_id,
            fact_key=fact_key,
            value=value,
            observed_at=observed_at,
            expires_at=expires_at,
            freshness_class=freshness_class,
            source_id="live-tool-context",
            artifact_id=artifact_id,
            read_only=True,
            mutated=False,
        )
        return {
            "item_id": observation_id,
            "kind": "observation",
            "entity_id": entity_id,
            "entity_name": "AAG Ubuntu Agent Host Bridge" if entity_id == "entity:bridge" else "Current Ubuntu performance",
            "fact_key": fact_key,
            "content": value,
            "epistemic_state": "VERIFIED",
            "temporal_scope": "LIVE_OBSERVATION",
            "lifecycle_state": "ACTIVE",
            "verification_level": "DIRECT_LIVE",
            "freshness": "FRESH",
            "observed_at": observed_at,
            "expires_at": expires_at,
            "source_ids": [artifact_id],
            "selection_reason": "current_verified_priority",
            "score": 300.0,
            "untrusted_evidence": False,
            "read_only": True,
            "mutated": False,
        }, artifact_id

    def dispatch(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) - ALLOWED_FIELDS:
            raise ContextServiceError("invalid_context_request_schema")
        operation = payload.get("operation")
        if operation not in OPERATIONS:
            raise ContextServiceError("context_operation_not_allowlisted")
        query = payload.get("query")
        task_id = payload.get("task_id")
        budget_tier = payload.get("budget_tier")
        if query is not None and (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > self.config.limits["max_query_chars"]
        ):
            raise ContextServiceError("invalid_context_query")
        if task_id is not None and not isinstance(task_id, str):
            raise ContextServiceError("invalid_context_task_id")
        if budget_tier is not None and budget_tier not in self.config.context_budget_tokens:
            raise ContextServiceError("invalid_context_budget_tier")
        if operation == "status":
            if set(payload) != {"operation"}:
                raise ContextServiceError("status_accepts_no_arguments")
            result = {
                "schema": "aag-context-memory-status-v1",
                "maturity": "CONTEXT_MEMORY_V1_IMPLEMENTED",
                "database_path": str(self.store.path),
                "integrity": self.store.integrity(),
                "stats": self.store.stats(),
                "fts5": True,
                "embeddings": False,
                "execution_authority": "NONE",
            }
        elif operation in {"context", "historical"}:
            if query is None:
                raise ContextServiceError("context_query_required")
            result = self.assembler.assemble(
                query,
                task_id=task_id,
                budget_tier=budget_tier,
                include_historical=True if operation == "historical" else None,
            )
        elif operation == "current_bridge":
            live, _artifact = self._run_live(
                profile="service",
                inputs={"service": "aag-ubuntu-agent-bridge.service", "manager": "user"},
                entity_id="entity:bridge",
                fact_key="bridge.pid_and_service_state",
                freshness_class="OPERATIONAL",
            )
            result = self.assembler.assemble(
                query or "current aag-ubuntu-agent-bridge.service PID and service state",
                task_id=task_id,
                budget_tier=budget_tier or "exact",
                supplied_live_observations=[live],
            )
        elif operation == "current_performance":
            live, _artifact = self._run_live(
                profile="performance",
                inputs={},
                entity_id="entity:aag-agent",
                fact_key="performance.current",
                freshness_class="HIGHLY_VOLATILE",
            )
            result = self.assembler.assemble(
                query or "current Ubuntu performance and why the computer is slow now",
                task_id=task_id,
                budget_tier=budget_tier or "normal",
                supplied_live_observations=[live],
            )
        elif operation == "task":
            if task_id is None or query is not None or budget_tier is not None:
                raise ContextServiceError("task_requires_only_task_id")
            result = self.tasks.resume(task_id)
        else:
            effective_query = query or "current Ubuntu performance remediation proposal"
            additional_evidence: list[str] = []
            additional_entities: list[str] = []
            if query is None or any(term in effective_query.casefold() for term in ("slow", "performance", "איטי", "ביצועים", "עכשיו", "current")):
                _live, artifact_id = self._run_live(
                    profile="performance",
                    inputs={},
                    entity_id="entity:aag-agent",
                    fact_key="performance.current",
                    freshness_class="HIGHLY_VOLATILE",
                )
                additional_evidence.append(artifact_id)
                additional_entities.append("entity:aag-agent")
            result = self.remediation.plan(
                effective_query,
                additional_evidence_ids=additional_evidence,
                additional_entities=additional_entities,
            )
        return {
            "schema": "aag-context-service-response-v1",
            "status": "ok",
            "operation": operation,
            "read_only": True,
            "mutated": False,
            "execution_authority": "NONE",
            "result": result,
        }


def dispatch_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    return ContextMemoryService().dispatch(payload)
