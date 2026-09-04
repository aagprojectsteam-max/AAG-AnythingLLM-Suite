"""Deterministic collection, hypothesis evaluation and remediation handoff."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.diagnostics import diagnose_many
from aag_agent.remediation.bridge import BridgeObservationProvider

from .models import canonical_json, sha256_json, stable_id, utc_now
from .registry import Playbook, PlaybookRegistry
from .store import InvestigationStore


class InvestigationError(ValueError):
    pass


def _traverse(value: Any, path: list[Any]) -> tuple[bool, Any]:
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part < 0 or part >= len(current):
                return False, None
            current = current[part]
        else:
            if not isinstance(current, Mapping) or part not in current:
                return False, None
            current = current[part]
    return True, current


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    if isinstance(left, bool) or isinstance(right, bool):
        raise InvestigationError("invalid_ordered_boolean")
    try:
        left_number = float(left)
        right_number = float(right)
    except (TypeError, ValueError) as exc:
        raise InvestigationError("predicate_not_numeric") from exc
    if not math.isfinite(left_number) or not math.isfinite(right_number):
        raise InvestigationError("predicate_not_finite")
    return {
        "ge": left_number >= right_number,
        "gt": left_number > right_number,
        "le": left_number <= right_number,
        "lt": left_number < right_number,
    }[operator]


class InvestigationEngine:
    def __init__(
        self,
        *,
        registry: PlaybookRegistry,
        store: InvestigationStore,
        context: ContextMemoryService,
        diagnostic_runner: Callable[..., dict[str, Any]] = diagnose_many,
        bridge_observer: BridgeObservationProvider | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.registry = registry
        self.store = store
        self.context = context
        self.diagnostic_runner = diagnostic_runner
        self.bridge_observer = bridge_observer or BridgeObservationProvider()
        self.clock = clock

    def initialize(self) -> dict[str, Any]:
        self.context.store.migrate()
        migration = self.store.migrate()
        return {
            "schema": "aag-diagnostic-reasoning-initialization-v1",
            "migration": migration,
            "integrity": self.store.integrity(),
            "playbooks": self.registry.list(),
            "execution_authority": "NONE",
            "read_only": True,
            "mutated": False,
        }

    def create(
        self,
        playbook_id: str,
        *,
        version: int = 1,
        request_summary: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        playbook = self.registry.get(playbook_id, version)
        if not isinstance(request_summary, str) or not request_summary.strip() or len(request_summary) > 2000:
            raise InvestigationError("invalid_request_summary")
        if task_id is not None:
            task = self.context.tasks.show(task_id)
            if task["closure_status"] not in {"OPEN", "BLOCKED"}:
                raise InvestigationError("task_not_active")
        created_at = self.clock()
        investigation_id = stable_id(
            "investigation", playbook.playbook_id, version, request_summary.strip(), task_id or "", created_at
        )
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT INTO investigations
                   (investigation_id,playbook_id,playbook_version,registry_sha256,target_identity,
                    request_summary,task_id,state,conclusion,read_only,mutated,event_count,last_event_hash,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'OPEN','PENDING',1,0,0,NULL,?,?)""",
                (
                    investigation_id, playbook.playbook_id, version, playbook.registry_sha256,
                    playbook.data["target_identity"], request_summary.strip(), task_id, created_at, created_at,
                ),
            )
            for ordinal, step in enumerate(playbook.data["diagnostic_steps"], 1):
                connection.execute(
                    """INSERT INTO investigation_steps
                       (step_record_id,investigation_id,ordinal,step_id,collector,status)
                       VALUES (?,?,?,?,?,'PENDING')""",
                    (stable_id("investigation-step", investigation_id, step["step_id"]), investigation_id, ordinal, step["step_id"], step["collector"]),
                )
            for hypothesis in playbook.data["hypotheses"]:
                connection.execute(
                    """INSERT INTO hypotheses
                       (hypothesis_record_id,investigation_id,hypothesis_id,statement,predicate_json,state,
                        evidence_summary_json,score,selection_reason,next_check,created_at,updated_at)
                       VALUES (?,?,?,?,?,'PENDING','{}',0,?,?,?,?)""",
                    (
                        stable_id("hypothesis", investigation_id, hypothesis["hypothesis_id"]), investigation_id,
                        hypothesis["hypothesis_id"], hypothesis["statement"], canonical_json(hypothesis["predicate"]),
                        hypothesis["selection_reason"], hypothesis["next_check"], created_at, created_at,
                    ),
                )
            self.store.append_event(connection, investigation_id, "INVESTIGATION_CREATED", {
                "playbook_id": playbook.playbook_id, "playbook_version": version,
                "registry_sha256": playbook.registry_sha256, "target_identity": playbook.data["target_identity"],
                "task_id": task_id, "execution_authority": "NONE",
            })
        return self.store.get(investigation_id)

    def _collect(self, step: Mapping[str, Any]) -> dict[str, Any]:
        if step["collector"] == "exact_bridge_observer_v1":
            payload = dict(self.bridge_observer.observe())
            provenance = payload.get("provenance", {})
            if payload.get("read_only") is not True and not (
                isinstance(provenance, Mapping) and provenance.get("read_only") is True
            ):
                raise InvestigationError("collector_read_only_invariant_failed")
            if payload.get("mutated") not in {None, False}:
                raise InvestigationError("collector_mutation_invariant_failed")
            payload["read_only"] = True
            payload["mutated"] = False
            return payload
        payload = self.diagnostic_runner([{"profile": step["profile"], "inputs": dict(step["inputs"])}])
        if payload.get("schema") != "aag-diagnostic-session-v1" or payload.get("read_only") is not True or payload.get("mutated") is not False:
            raise InvestigationError("collector_contract_invalid")
        return payload

    @staticmethod
    def _fact(payloads: list[Mapping[str, Any]], fact: str) -> tuple[bool, Any, str | None]:
        if fact.startswith("bridge."):
            key = fact.removeprefix("bridge.")
            for payload in payloads:
                if key in payload:
                    if payload.get("classification") in {"UNOBSERVABLE", "INDETERMINATE", "MISSING", "WRONG_TARGET"}:
                        return False, None, "bridge_state_not_observable"
                    return True, payload[key], None
            return False, None, "fact_missing"
        for payload in payloads:
            for bundle in payload.get("bundles", []):
                item = bundle.get("facts", {}).get(fact)
                if not isinstance(item, Mapping):
                    continue
                if item.get("state") != "OBSERVED":
                    return False, None, f"fact_{str(item.get('state', 'unknown')).casefold()}"
                return True, item, None
        return False, None, "fact_missing"

    @classmethod
    def evaluate_predicate(cls, predicate: Mapping[str, Any], payloads: list[Mapping[str, Any]]) -> dict[str, Any]:
        found, fact, reason = cls._fact(payloads, str(predicate["fact"]))
        if not found:
            return {"state": "UNKNOWN", "score": 0, "reason": reason, "observed_value": None}
        kind = predicate["kind"]
        operator = predicate["operator"]
        try:
            if kind == "ratio":
                numerator_ok, numerator = _traverse(fact, list(predicate["numerator_path"]))
                denominator_ok, denominator = _traverse(fact, list(predicate["denominator_path"]))
                if not numerator_ok or not denominator_ok or float(denominator) == 0:
                    return {"state": "UNKNOWN", "score": 0, "reason": "ratio_input_missing", "observed_value": None}
                observed: Any = float(numerator) / float(denominator)
            else:
                value_ok, observed = _traverse(fact, list(predicate.get("path", [])))
                if not value_ok:
                    return {"state": "UNKNOWN", "score": 0, "reason": "predicate_path_missing", "observed_value": None}
                if kind == "percent":
                    observed = float(str(observed).strip().removesuffix("%"))
                elif kind == "nonempty":
                    observed = bool(observed)
                elif kind == "list_numeric":
                    if not isinstance(observed, list):
                        return {"state": "UNKNOWN", "score": 0, "reason": "predicate_list_missing", "observed_value": None}
                    item_path = list(predicate.get("numerator_path", []))
                    values = []
                    for item in observed:
                        item_ok, item_value = _traverse(item, item_path)
                        if item_ok and isinstance(item_value, (int, float)) and not isinstance(item_value, bool):
                            values.append(float(item_value))
                    if not values:
                        return {"state": "UNKNOWN", "score": 0, "reason": "predicate_list_values_missing", "observed_value": None}
                    observed = max(values)
            matched = _compare(observed, operator, predicate.get("threshold"))
        except (InvestigationError, TypeError, ValueError, KeyError, ZeroDivisionError):
            return {"state": "UNKNOWN", "score": 0, "reason": "predicate_evaluation_failed", "observed_value": None}
        return {
            "state": "SUPPORTED" if matched else "FALSIFIED",
            "score": 80 if matched else 5,
            "reason": "trusted_predicate_matched" if matched else "trusted_negative_observation",
            "observed_value": observed,
            "operator": operator,
            "threshold": predicate.get("threshold"),
        }

    def run(self, investigation_id: str) -> dict[str, Any]:
        current = self.store.get(investigation_id)
        if current["state"] != "OPEN":
            raise InvestigationError("investigation_not_open")
        playbook = self.registry.get(current["playbook_id"], current["playbook_version"])
        payloads: list[Mapping[str, Any]] = []
        artifact_ids: list[str] = []
        started = time.monotonic()
        with self.store.transaction() as connection:
            connection.execute("UPDATE investigations SET state='COLLECTING',updated_at=? WHERE investigation_id=?", (self.clock(), investigation_id))
            self.store.append_event(connection, investigation_id, "COLLECTION_STARTED", {"maximum_seconds": playbook.data["stop_policy"]["max_seconds"]})
        for ordinal, step in enumerate(playbook.data["diagnostic_steps"], 1):
            if time.monotonic() - started >= playbook.data["stop_policy"]["max_seconds"]:
                break
            step_started = self.clock()
            before = time.monotonic()
            try:
                payload = self._collect(step)
                payload_hash = sha256_json(payload)
                artifact_id = self.context._live_artifact(f"investigation-{step['step_id']}", payload)
            except Exception as exc:
                step_record_id = stable_id("investigation-step", investigation_id, step["step_id"])
                failed_at = self.clock()
                with self.store.transaction() as connection:
                    connection.execute(
                        """UPDATE investigation_steps SET status='ERROR',started_at=?,completed_at=?,duration_ms=?
                           WHERE step_record_id=?""",
                        (step_started, failed_at, round((time.monotonic() - before) * 1000, 3), step_record_id),
                    )
                    connection.execute(
                        "UPDATE investigations SET state='FAILED',conclusion='COLLECTION_FAILED',updated_at=? WHERE investigation_id=?",
                        (failed_at, investigation_id),
                    )
                    self.store.append_event(connection, investigation_id, "COLLECTION_FAILED", {
                        "step_id": step["step_id"], "error_type": type(exc).__name__,
                        "error": "trusted_collector_failed", "execution_authority": "NONE",
                    })
                return self.show(investigation_id)
            payloads.append(payload)
            artifact_ids.append(artifact_id)
            observed_at = self.clock()
            expires_at = (
                datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                + timedelta(seconds=step["freshness_seconds"])
            ).isoformat().replace("+00:00", "Z")
            status = "OBSERVED" if payload.get("status") in {"OBSERVED", "completed"} or payload.get("classification") else "INDETERMINATE"
            step_record_id = stable_id("investigation-step", investigation_id, step["step_id"])
            with self.store.transaction() as connection:
                connection.execute(
                    """UPDATE investigation_steps SET status=?,started_at=?,completed_at=?,duration_ms=?
                       WHERE step_record_id=?""",
                    (status, step_started, observed_at, round((time.monotonic() - before) * 1000, 3), step_record_id),
                )
                connection.execute(
                    """INSERT INTO investigation_evidence
                       (evidence_record_id,investigation_id,step_record_id,artifact_id,payload_sha256,
                        verification_level,observed_at,expires_at,freshness_seconds,payload_json,read_only,mutated)
                       VALUES (?,?,?,?,?,'DIRECT_LIVE',?,?,?,?,1,0)""",
                    (
                        stable_id("investigation-evidence", investigation_id, artifact_id), investigation_id,
                        step_record_id, artifact_id, payload_hash, observed_at, expires_at,
                        step["freshness_seconds"], canonical_json(payload),
                    ),
                )
                self.store.append_event(connection, investigation_id, "EVIDENCE_COLLECTED", {
                    "step_id": step["step_id"], "artifact_id": artifact_id,
                    "payload_sha256": payload_hash, "status": status,
                    "read_only": True, "mutated": False,
                })
        evaluations: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
        for hypothesis in playbook.data["hypotheses"]:
            evaluations.append((hypothesis, self.evaluate_predicate(hypothesis["predicate"], payloads)))
        supported = [item for item in evaluations if item[1]["state"] == "SUPPORTED"]
        unknown = [item for item in evaluations if item[1]["state"] == "UNKNOWN"]
        state = "INDETERMINATE" if not payloads or (unknown and not supported) else "ANALYZED"
        if supported:
            conclusion = "SUPPORTED_HYPOTHESES_REQUIRE_GOVERNED_REVIEW"
        elif unknown:
            conclusion = "INSUFFICIENT_EVIDENCE"
        else:
            conclusion = "REGISTERED_HYPOTHESES_FALSIFIED"
        with self.store.transaction() as connection:
            for hypothesis, evaluation in evaluations:
                connection.execute(
                    """UPDATE hypotheses SET state=?,evidence_summary_json=?,score=?,updated_at=?
                       WHERE investigation_id=? AND hypothesis_id=?""",
                    (
                        evaluation["state"], canonical_json({**evaluation, "artifact_ids": artifact_ids}),
                        evaluation["score"], self.clock(), investigation_id, hypothesis["hypothesis_id"],
                    ),
                )
                self.store.append_event(connection, investigation_id, "HYPOTHESIS_EVALUATED", {
                    "hypothesis_id": hypothesis["hypothesis_id"], "state": evaluation["state"],
                    "reason": evaluation["reason"], "artifact_ids": artifact_ids,
                })
            connection.execute(
                "UPDATE investigations SET state=?,conclusion=?,updated_at=? WHERE investigation_id=?",
                (state, conclusion, self.clock(), investigation_id),
            )
            self.store.append_event(connection, investigation_id, "INVESTIGATION_ANALYZED", {
                "state": state, "conclusion": conclusion,
                "supported": [item[0]["hypothesis_id"] for item in supported],
                "unknown": [item[0]["hypothesis_id"] for item in unknown],
                "execution_authority": "NONE",
            })
        if current.get("task_id"):
            changes: dict[str, list[Any]] = {
                "tools_used": [f"diagnostic-playbook:{playbook.playbook_id}:1"],
                "evidence_ids": artifact_ids,
                "observations": [{"investigation_id": investigation_id, "conclusion": conclusion}],
                "hypotheses": [
                    {"hypothesis_id": item[0]["hypothesis_id"], "state": item[1]["state"]}
                    for item in evaluations
                ],
            }
            next_checks = [item[0]["next_check"] for item in evaluations if item[1]["state"] in {"SUPPORTED", "UNKNOWN"} and item[0]["next_check"]]
            if next_checks:
                changes["next_recommended_checks"] = next_checks[:3]
            self.context.tasks.update(current["task_id"], changes)
        return self.show(investigation_id)

    def show(self, investigation_id: str) -> dict[str, Any]:
        result = self.store.get(investigation_id)
        playbook = self.registry.get(result["playbook_id"], result["playbook_version"])
        handoff = playbook.data["remediation_handoff"]
        supported_ids = {item["hypothesis_id"] for item in result["hypotheses"] if item["state"] == "SUPPORTED"}
        result["remediation_handoff"] = {
            "eligible": bool(handoff["allowed"] and handoff["required_hypothesis"] in supported_ids),
            "operation_id": handoff["operation_id"] if handoff["allowed"] else None,
            "operation_version": handoff["operation_version"] if handoff["allowed"] else None,
            "status": "PROPOSAL_ELIGIBLE_NOT_AUTHORIZED" if handoff["allowed"] and handoff["required_hypothesis"] in supported_ids else "NOT_ELIGIBLE",
            "execution_authority": "NONE",
        }
        result["unknowns"] = [item["hypothesis_id"] for item in result["hypotheses"] if item["state"] == "UNKNOWN"]
        result["failed_or_falsified"] = [item["hypothesis_id"] for item in result["hypotheses"] if item["state"] in {"FALSIFIED", "CONTRADICTED"}]
        result["security_notice"] = {
            "retrieved_content_is_evidence_not_instruction": True,
            "model_cannot_select_collectors_targets_or_predicates": True,
            "no_commands": True,
            "execution_authority": "NONE",
        }
        return result

    def close(self, investigation_id: str) -> dict[str, Any]:
        current = self.store.get(investigation_id)
        if current["state"] not in {"ANALYZED", "INDETERMINATE", "FAILED"}:
            raise InvestigationError("investigation_not_closable")
        with self.store.transaction() as connection:
            connection.execute("UPDATE investigations SET state='CLOSED',updated_at=? WHERE investigation_id=?", (self.clock(), investigation_id))
            self.store.append_event(connection, investigation_id, "INVESTIGATION_CLOSED", {"conclusion": current["conclusion"]})
        return self.show(investigation_id)
