"""Bounded orchestration across verified AAG subsystems without execution."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.investigation.engine import InvestigationEngine

from .contracts import ContractError, RESPONSE_SCHEMA, bound_response, validate_response
from .idempotency import IdempotencyGuard
from .intent import IntentDecision, classify
from .practical import PracticalWorkflowError, PracticalWorkflows


ORCHESTRATOR_OWNER = "governed-orchestration-v1"


class OrchestrationError(ValueError):
    pass


class GovernedOrchestrator:
    def __init__(
        self,
        *,
        context: ContextMemoryService,
        investigations: InvestigationEngine,
        practical: PracticalWorkflows | None = None,
        idempotency: IdempotencyGuard | None = None,
        monotonic=time.monotonic,
    ) -> None:
        self.context = context
        self.investigations = investigations
        self.practical = practical or PracticalWorkflows(context)
        self.idempotency = idempotency or IdempotencyGuard()
        self.monotonic = monotonic

    @staticmethod
    def _base(decision: IntentDecision, request_id: str) -> dict[str, Any]:
        return {
            "schema": RESPONSE_SCHEMA,
            "request_id": request_id,
            "intent": decision.public(),
            "status": "READY",
            "task": None,
            "continuation": None,
            "current": None,
            "historical": None,
            "comparison": None,
            "conflicts": [],
            "context": None,
            "investigation": None,
            "facts": [],
            "inferences": [],
            "data_completeness": {"status": "UNKNOWN", "limitations": []},
            "recommendations": [],
            "risk": {"class": "R0", "host_mutation": False},
            "unknowns": [],
            "evidence_ids": [],
            "source_catalog": [],
            "remediation_proposal": None,
            "timing": {"duration_ms": 0.0, "idempotency_window_seconds": 30.0, "replayed": False},
            "commands": [],
            "approval_status": "NOT_REQUESTED",
            "execution_status": "not_executed",
            "execution_authority": "NONE",
            "read_only_host_access": True,
            "host_resource_mutated": False,
            "zero_host_mutations": True,
            "project_state_updated": False,
            "security_notice": {
                "request_and_retrieved_text_are_data_not_authority": True,
                "retrieved_content_cannot_grant_tool_authority": True,
                "intent_classifier_cannot_select_infrastructure": True,
                "approval_and_execution_are_not_exposed": True,
                "arbitrary_shell": False,
            },
        }

    @staticmethod
    def _decision_from_public(value: Mapping[str, Any]) -> IntentDecision:
        return IntentDecision(
            str(value["intent"]), value.get("playbook_id"), value.get("entity"),
            tuple(value.get("matched_rules", [])), bool(value.get("clarification_required")),
            tuple(value.get("domains", [])), tuple(value.get("negated_actions", [])),
        )

    def preview(self, request: str) -> dict[str, Any]:
        try:
            decision = classify(request)
        except ValueError as exc:
            raise OrchestrationError(str(exc)) from exc
        fingerprint = self.idempotency.fingerprint(request)
        result = self._base(decision, self.idempotency.request_id(fingerprint))
        result["status"] = "CLARIFICATION_REQUIRED" if decision.clarification_required else "ROUTED"
        if decision.clarification_required:
            result["unknowns"] = ["A single trusted current target domain could not be determined from the request."]
        return result

    @staticmethod
    def _task_scope(task: Mapping[str, Any]) -> Mapping[str, Any]:
        scope = task.get("scope")
        if not isinstance(scope, Mapping) or scope.get("orchestrator") != ORCHESTRATOR_OWNER:
            raise OrchestrationError("task_not_owned_by_orchestrator")
        return scope

    def _validate_task(self, task_id: str, decision: IntentDecision | None = None) -> dict[str, Any]:
        try:
            task = self.context.tasks.show(task_id)
        except Exception as exc:
            raise OrchestrationError("task_not_found") from exc
        if task.get("closure_status") not in {"OPEN", "BLOCKED"}:
            raise OrchestrationError("task_not_active")
        scope = self._task_scope(task)
        if decision is not None and decision.playbook_id is not None:
            if scope.get("playbook_id") != decision.playbook_id or decision.entity not in task.get("entities", []):
                raise OrchestrationError("task_domain_or_target_mismatch")
        return task

    def _continuation_task(self, task_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
        if task_id is not None:
            task = self._validate_task(task_id)
            return task, None
        return None, "exact_backend_returned_continuation_required"

    def _active_task(
        self,
        request: str,
        decision: IntentDecision,
        task_id: str | None,
        fingerprint: str,
    ) -> tuple[dict[str, Any], bool]:
        if task_id is not None:
            return self._validate_task(task_id, decision), True
        if decision.playbook_id is None:
            raise OrchestrationError("trusted_playbook_missing")
        playbook = self.investigations.registry.get(decision.playbook_id, 1)
        scope = {
            "orchestrator": ORCHESTRATOR_OWNER,
            "intent": decision.intent,
            "playbook_id": decision.playbook_id,
            "domain": decision.domains[0] if len(decision.domains) == 1 else None,
            "entity": decision.entity,
            "target_identity": playbook.data["target_identity"],
        }
        try:
            return self.context.tasks.start_or_reuse(
                request[:2000], scope=scope,
                entities=[decision.entity] if decision.entity else [],
                request_fingerprint=fingerprint,
                window_seconds=self.idempotency.window_seconds,
            )
        except Exception as exc:
            raise OrchestrationError("task_state_unavailable") from exc

    @staticmethod
    def _parse_time(value: Any) -> float:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    def _recent_investigation(self, task_id: str, playbook_id: str) -> dict[str, Any] | None:
        try:
            with self.investigations.store.read() as connection:
                row = connection.execute(
                    """SELECT investigation_id,state,updated_at FROM investigations
                       WHERE task_id=? AND playbook_id=? ORDER BY updated_at DESC LIMIT 1""",
                    (task_id, playbook_id),
                ).fetchone()
        except Exception as exc:
            raise OrchestrationError("investigation_store_unavailable") from exc
        if row is None:
            return None
        age = datetime.now(timezone.utc).timestamp() - self._parse_time(row["updated_at"])
        if not 0 <= age <= self.idempotency.window_seconds:
            return None
        if row["state"] in {"ANALYZED", "INDETERMINATE", "FAILED"}:
            return self.investigations.show(row["investigation_id"])
        if row["state"] in {"OPEN", "COLLECTING"}:
            return {
                "schema": "aag-diagnostic-investigation-v1",
                "investigation_id": row["investigation_id"],
                "playbook_id": playbook_id,
                "task_id": task_id,
                "state": "INDETERMINATE",
                "conclusion": "DUPLICATE_IN_FLIGHT_REQUEST_NOT_REPLAYED",
                "hypotheses": [], "steps": [], "evidence": [], "unknowns": ["investigation_in_flight"],
                "failed_or_falsified": [],
                "remediation_handoff": {"eligible": False, "status": "NOT_ELIGIBLE", "execution_authority": "NONE"},
                "read_only": True, "mutated": False, "execution_authority": "NONE",
            }
        return None

    @staticmethod
    def _unavailable_plan(reason: str, evidence_ids: list[str] | None = None) -> dict[str, Any]:
        return {
            "schema": "aag-remediation-plan-unavailable-v1",
            "plan_available": False,
            "error": reason,
            "evidence_ids": list(evidence_ids or []),
            "clarification_required": True,
            "commands": [],
            "execution_authority": "NONE",
            "execution_status": "not_executed",
            "read_only": True,
            "mutated": False,
            "zero_mutations": True,
        }

    def _source_rows(self, artifact_ids: list[str]) -> list[dict[str, Any]]:
        unique = list(dict.fromkeys(artifact_ids))
        if not unique:
            return []
        placeholders = ",".join("?" for _ in unique)
        with self.context.store.read() as connection:
            rows = connection.execute(
                f"""SELECT artifact_id,source_id,uri,temporal_scope,verification_level,status
                    FROM source_artifacts WHERE artifact_id IN ({placeholders})""",
                unique,
            ).fetchall()
        by_id = {row["artifact_id"]: dict(row) for row in rows}
        if set(by_id) != set(unique):
            raise OrchestrationError("evidence_provenance_missing")
        return [by_id[item] for item in unique]

    @staticmethod
    def _package_sources(package: Any) -> tuple[list[str], list[dict[str, Any]]]:
        if not isinstance(package, Mapping):
            return [], []
        catalog = [dict(item) for item in package.get("source_catalog", []) if isinstance(item, Mapping)]
        ids = [str(item.get("artifact_id")) for item in catalog if item.get("artifact_id")]
        return ids, catalog

    def _finalize(self, result: dict[str, Any], started: float) -> dict[str, Any]:
        evidence_ids = list(result.get("evidence_ids", []))
        catalogs: list[dict[str, Any]] = list(result.get("source_catalog", []))
        for package in (result.get("context"), result.get("current"), result.get("historical")):
            ids, rows = self._package_sources(package)
            evidence_ids.extend(ids)
            catalogs.extend(rows)
        investigation = result.get("investigation")
        if isinstance(investigation, Mapping):
            evidence_ids.extend(
                str(item["artifact_id"]) for item in investigation.get("evidence", [])
                if isinstance(item, Mapping) and item.get("artifact_id")
            )
            for hypothesis in investigation.get("hypotheses", []):
                state = hypothesis.get("state")
                classification = {
                    "SUPPORTED": "SUPPORTED_CONTRIBUTOR",
                    "FALSIFIED": "FALSIFIED",
                    "CONTRADICTED": "FALSIFIED",
                    "UNKNOWN": "UNKNOWN",
                }.get(state, "UNKNOWN")
                result["inferences"].append({
                    "hypothesis_id": hypothesis.get("hypothesis_id"),
                    "classification": classification,
                    "verified_root_cause": False,
                    "score": hypothesis.get("score"),
                })
        proposal = result.get("remediation_proposal")
        if isinstance(proposal, Mapping):
            evidence_ids.extend(str(item) for item in proposal.get("evidence_ids", []) if isinstance(item, str))
        evidence_ids = list(dict.fromkeys(evidence_ids))
        known = {item.get("artifact_id"): item for item in catalogs if item.get("artifact_id")}
        missing = [item for item in evidence_ids if item not in known]
        if missing:
            for item in self._source_rows(missing):
                known[item["artifact_id"]] = item
        result["evidence_ids"] = evidence_ids
        result["source_catalog"] = [known[key] for key in evidence_ids]
        result["timing"]["duration_ms"] = round((self.monotonic() - started) * 1000, 3)
        return validate_response(bound_response(result))

    def _clarification(self, decision: IntentDecision, request_id: str, started: float, reason: str) -> dict[str, Any]:
        result = self._base(decision, request_id)
        result["status"] = "CLARIFICATION_REQUIRED"
        result["unknowns"] = [reason]
        result["data_completeness"] = {"status": "INSUFFICIENT_TARGET", "limitations": [reason]}
        return self._finalize(result, started)

    def _failed(self, decision: IntentDecision, request_id: str, started: float, error: str, status: str = "UNAVAILABLE") -> dict[str, Any]:
        result = self._base(decision, request_id)
        result["status"] = status
        result["unknowns"] = [error]
        result["data_completeness"] = {"status": "FAILED", "limitations": [error]}
        return self._finalize(result, started)

    def _handle_once(
        self,
        request: str,
        *,
        task_id: str | None,
        fingerprint: str,
        request_id: str,
        started: float,
    ) -> dict[str, Any]:
        decision = classify(request)

        if decision.intent == "DEICTIC_REMEDIATION":
            task, error = self._continuation_task(task_id)
            if task is None:
                return self._clarification(decision, request_id, started, error or "exact_active_task_required")
            scope = self._task_scope(task)
            playbook_id = scope.get("playbook_id")
            entity = scope.get("entity")
            domain = scope.get("domain")
            if not isinstance(playbook_id, str) or not isinstance(entity, str) or not isinstance(domain, str):
                return self._clarification(decision, request_id, started, "active_task_has_no_trusted_remediation_domain")
            decision = IntentDecision(
                "REMEDIATION_PROPOSAL", playbook_id, entity,
                tuple((*decision.matched_rules, "trusted_active_task_binding")), False,
                (domain,), decision.negated_actions,
            )
            task_id = task["task_id"]

        if decision.clarification_required:
            return self._clarification(
                decision, request_id, started,
                "A single trusted current target domain could not be determined; no diagnostic or proposal was run.",
            )

        result = self._base(decision, request_id)

        if decision.intent == "TASK_CONTINUATION":
            task, error = self._continuation_task(task_id)
            if task is None:
                return self._clarification(decision, request_id, started, error or "exact_active_task_required")
            task = self.context.tasks.resume(task["task_id"])
            result["task"] = task
            result["continuation"] = {"task_id": task["task_id"], "opaque": True, "user_entry_required": False}
            result["context"] = self.context.assembler.assemble(request, task_id=task["task_id"], budget_tier="history")
            result["status"] = "TASK_RESUMED"
            result["project_state_updated"] = True
            result["data_completeness"] = {"status": "COMPLETE", "limitations": []}
            return self._finalize(result, started)

        if decision.intent == "AGENT_SELF_HEALTH":
            result["current"] = self.practical.self_health()
            result["facts"] = [{"classification": "OBSERVED_FACT", "fact": "agent_self_health", "value": result["current"]["status"]}]
            result["evidence_ids"] = result["current"]["evidence_ids"]
            result["status"] = result["current"]["status"]
            result["data_completeness"] = {"status": result["current"]["status"], "limitations": result["current"].get("unavailable_checks", [])}
            result["project_state_updated"] = True
            return self._finalize(result, started)

        if decision.intent == "CURRENT_RELEASE":
            result["current"] = self.practical.current_release()
            result["facts"] = [{"classification": "OBSERVED_FACT", "fact": "release.version", "value": result["current"]["version"]}]
            result["evidence_ids"] = result["current"]["evidence_ids"]
            result["status"] = "CURRENT_STATE_OBSERVED"
            result["data_completeness"] = {"status": "COMPLETE", "limitations": []}
            result["project_state_updated"] = True
            return self._finalize(result, started)

        if decision.intent in {"STORAGE_CONSUMERS", "STORAGE_PROTECTION_CONTEXT", "SUSTAINED_PERFORMANCE"}:
            if decision.intent == "STORAGE_CONSUMERS":
                current = self.practical.storage_consumers()
            elif decision.intent == "STORAGE_PROTECTION_CONTEXT":
                current = self.practical.storage_protection()
            else:
                current = self.practical.sustained_performance()
                result["inferences"] = list(current["hypothesis_evaluations"])
            result["current"] = current
            result["evidence_ids"] = current["evidence_ids"]
            result["status"] = current["status"]
            result["data_completeness"] = {"status": current["status"], "limitations": current.get("scan_coverage", {}).get("excluded_paths", [])}
            result["project_state_updated"] = True
            return self._finalize(result, started)

        if decision.intent == "MIXED_CURRENT_HISTORY":
            historical = self.context.dispatch({"operation": "historical", "query": request})["result"]
            current = None
            if decision.domains == ("bridge",):
                current = self.context.dispatch({"operation": "current_bridge", "query": request})["result"]
            elif decision.domains == ("performance",):
                current = self.context.dispatch({"operation": "current_performance", "query": request})["result"]
            result["current"] = current
            result["historical"] = historical
            result["comparison"] = {
                "current_and_history_separate": True,
                "historical_does_not_override_current": True,
                "current_refresh_performed": current is not None,
            }
            result["status"] = "MIXED_CONTEXT_ASSEMBLED" if current is not None else "INDETERMINATE"
            if current is None:
                result["unknowns"] = ["The historical event domain was not specific enough to select a trusted live refresh."]
                result["status"] = "CLARIFICATION_REQUIRED"
            result["data_completeness"] = {"status": "COMPLETE" if current is not None else "PARTIAL", "limitations": list(result["unknowns"])}
            result["project_state_updated"] = True
            return self._finalize(result, started)

        if decision.intent in {"CONTEXT_QUERY", "HISTORICAL_CONTEXT", "CURRENT_BRIDGE_CONTEXT", "MAINTENANCE_CONTEXT"}:
            operation = {"HISTORICAL_CONTEXT": "historical", "CURRENT_BRIDGE_CONTEXT": "current_bridge"}.get(decision.intent, "context")
            payload: dict[str, Any] = {"operation": operation, "query": request}
            if task_id is not None:
                self._validate_task(task_id)
                payload["task_id"] = task_id
            package = self.context.dispatch(payload)["result"]
            result["context"] = package
            if decision.intent == "HISTORICAL_CONTEXT":
                result["historical"] = package
            else:
                result["current"] = package
            result["status"] = "CONTEXT_ASSEMBLED"
            result["data_completeness"] = {"status": "COMPLETE", "limitations": package.get("unknowns", []) if isinstance(package, Mapping) else []}
            result["project_state_updated"] = True
            return self._finalize(result, started)

        if decision.playbook_id is None:
            raise OrchestrationError("trusted_playbook_missing")
        task, reused_task = self._active_task(request, decision, task_id, fingerprint)
        investigation = self._recent_investigation(task["task_id"], decision.playbook_id) if reused_task else None
        if investigation is None:
            created = self.investigations.create(decision.playbook_id, request_summary=request[:2000], task_id=task["task_id"])
            investigation = self.investigations.run(created["investigation_id"])
        result["task"] = self.context.tasks.show(task["task_id"])
        result["continuation"] = {"task_id": task["task_id"], "opaque": True, "user_entry_required": False}
        result["investigation"] = investigation
        result["status"] = "INVESTIGATION_COMPLETE" if investigation.get("state") == "ANALYZED" else investigation.get("state", "INDETERMINATE")
        result["project_state_updated"] = True
        investigation_unknowns = list(investigation.get("unknowns", []))
        if investigation.get("state") in {"FAILED", "INDETERMINATE"} and not investigation_unknowns:
            investigation_unknowns = ["The governed investigation did not produce sufficient verified observations."]
            investigation["unknowns"] = list(investigation_unknowns)
        result["unknowns"] = list(investigation_unknowns)
        result["data_completeness"] = {"status": result["status"], "limitations": investigation_unknowns}
        if decision.intent != "REMEDIATION_PROPOSAL":
            return self._finalize(result, started)
        supported = [item for item in investigation.get("hypotheses", []) if item.get("state") == "SUPPORTED"]
        evidence_ids = [item["artifact_id"] for item in investigation.get("evidence", [])]
        if not supported:
            reason = "registered_hypotheses_not_supported" if not investigation.get("unknowns") else "diagnosis_indeterminate"
            result["remediation_proposal"] = self._unavailable_plan(reason, evidence_ids)
            result["status"] = "REMEDIATION_NOT_GROUNDED"
            result["unknowns"] = investigation.get("unknowns") or ["No registered causal hypothesis was supported by current evidence."]
            return self._finalize(result, started)
        proposal = self.context.remediation.plan(
            request,
            additional_evidence_ids=evidence_ids,
            additional_entities=[decision.entity] if decision.entity else [],
        )
        if proposal.get("execution_authority") != "NONE" or proposal.get("execution_status") != "not_executed" or proposal.get("zero_mutations") is not True:
            raise OrchestrationError("remediation_proposal_authority_violation")
        proposal["supported_hypotheses"] = [item["hypothesis_id"] for item in supported]
        proposal["investigation_id"] = investigation["investigation_id"]
        proposal["governed_operation_eligibility"] = investigation["remediation_handoff"]
        result["remediation_proposal"] = proposal
        result["status"] = "GROUNDED_PROPOSAL_NOT_EXECUTED"
        result["risk"] = {"class": "PROPOSAL_ONLY", "host_mutation": False, "approval_required_before_any_future_action": True}
        return self._finalize(result, started)

    def handle(self, request: str, *, task_id: str | None = None) -> dict[str, Any]:
        started = self.monotonic()
        try:
            decision = classify(request)
        except ValueError as exc:
            raise OrchestrationError(str(exc)) from exc
        fingerprint = self.idempotency.fingerprint(request, task_id)
        request_id = self.idempotency.request_id(fingerprint)
        cached = self.idempotency.get(fingerprint)
        if cached is not None:
            cached["timing"]["replayed"] = True
            return cached
        with self.idempotency.exclusive(fingerprint):
            cached = self.idempotency.get(fingerprint)
            if cached is not None:
                cached["timing"]["replayed"] = True
                return cached
            try:
                result = self._handle_once(
                    request, task_id=task_id, fingerprint=fingerprint,
                    request_id=request_id, started=started,
                )
            except (OrchestrationError, PracticalWorkflowError) as exc:
                result = self._failed(decision, request_id, started, str(exc), "FAILED")
            except ContractError:
                raise
            except Exception:
                result = self._failed(decision, request_id, started, "orchestration_backend_unavailable", "UNAVAILABLE")
            self.idempotency.put(fingerprint, result)
            return result
