"""Isolated fixtures for Safe Remediation V1."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from aag_agent.audit import append_event
from aag_agent.context_memory.config import load_config
from aag_agent.context_memory.models import canonical_json, utc_now
from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.context_memory.store import ContextMemoryStore
from aag_agent.remediation.context_link import ContextLinker
from aag_agent.remediation.engine import RemediationEngine
from aag_agent.remediation.registry import OperationRegistry
from aag_agent.remediation.store import RemediationStore

CONTEXT_ARTIFACT_ID = "artifact:" + "a" * 24
CONTEXT_CANDIDATE_ID = "remediation-candidate:" + "b" * 24
CONTEXT_PLAN_ID = "remediation-plan:" + "c" * 24


def supported_evidence(**changes: Any) -> dict[str, Any]:
    value = {
        "schema": "aag-bridge-detector-evidence-v1",
        "observed_at": 1000.0,
        "target": "aag-ubuntu-agent-bridge.service",
        "load_state": "loaded",
        "active_state": "active",
        "sub_state": "running",
        "main_pid": "51001",
        "health_ready": False,
        "health_error": "readiness_timeout",
        "classification": "SUPPORTED_FAILURE",
        "supported_failure_class": "systemd_active_running_but_health_endpoint_unready",
        "provenance": {"read_only": True},
    }
    value.update(changes)
    return value


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class Tokens:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"stage17-token-{self.count:03d}-" + "x" * 40


class FakeObserver:
    def __init__(self, evidence: dict[str, Any] | None = None, verification: dict[str, Any] | None = None) -> None:
        self.evidence = evidence or supported_evidence()
        self.verification = verification or {
            "status": "PASS",
            "pre_pid": "51001",
            "post_pid": "51002",
            "evidence": supported_evidence(
                main_pid="51002",
                health_ready=True,
                health_error=None,
                classification="HEALTHY",
                supported_failure_class=None,
            ),
        }
        self.observe_calls = 0
        self.verify_calls = 0

    def observe(self) -> dict[str, Any]:
        self.observe_calls += 1
        return dict(self.evidence)

    def verify(self, pre_pid: str, **kwargs: Any) -> dict[str, Any]:
        self.verify_calls += 1
        result = dict(self.verification)
        result.setdefault("pre_pid", pre_pid)
        return result


class FakeExecutor:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {
            "status": "EXECUTION_OK",
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "executed": True,
            "mutated": True,
        }
        self.calls = 0
        self.operations = []

    def execute(self, operation):
        self.calls += 1
        self.operations.append(operation)
        return dict(self.result)


class Harness:
    def __init__(
        self,
        root: Path,
        *,
        evidence: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        executor_result: dict[str, Any] | None = None,
        context_plan_json: dict[str, Any] | None = None,
        with_task: bool = False,
        host_audit=None,
    ) -> None:
        self.root = root
        config = replace(load_config(), database_path=root / "context.sqlite3", sources=())
        self.context_store = ContextMemoryStore(config.database_path)
        self.context_store.migrate()
        self.context_service = ContextMemoryService(config, self.context_store)
        with self.context_store.transaction() as connection:
            connection.execute(
                "INSERT INTO entities VALUES ('entity:bridge','service','AAG Host Bridge','ACTIVE',?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT INTO sources VALUES ('stage17-test','verified_test','Stage 17 test evidence',90,1,?)",
                (utc_now(),),
            )
            connection.execute(
                """INSERT INTO source_artifacts
                   VALUES (?,?,?,?,?,'stage17-test-v1',1,NULL,?,'CURRENT','ACTIVE','TEST_VERIFIED','ACTIVE')""",
                (
                    CONTEXT_ARTIFACT_ID,
                    "stage17-test",
                    "test://stage17/context-plan",
                    "a" * 64,
                    "a" * 64,
                    utc_now(),
                ),
            )
            connection.execute(
                "INSERT INTO remediation_candidates VALUES (?,?,?,'PROPOSED',?)",
                (CONTEXT_CANDIDATE_ID, "Bridge readiness failure", "{}", utc_now()),
            )
            connection.execute(
                "INSERT INTO remediation_plans VALUES (?,?,?,'NONE','not_executed',?)",
                (
                    CONTEXT_PLAN_ID,
                    CONTEXT_CANDIDATE_ID,
                    canonical_json(context_plan_json or {"schema": "aag-remediation-plan-v1"}),
                    utc_now(),
                ),
            )
            connection.execute(
                "INSERT INTO remediation_plan_evidence VALUES (?,?)",
                (CONTEXT_PLAN_ID, CONTEXT_ARTIFACT_ID),
            )
        self.task_id = None
        if with_task:
            self.task_id = self.context_service.tasks.start(
                "Repair the exact Bridge readiness failure",
                entities=["entity:bridge"],
            )["task_id"]
        self.store = RemediationStore(root / "remediation.sqlite3")
        self.store.migrate()
        self.registry = OperationRegistry()
        self.observer = FakeObserver(evidence, verification)
        self.executor = FakeExecutor(executor_result)
        self.clock = Clock()
        self.tokens = Tokens()
        self.host_audit_path = root / "host-audit.jsonl"
        if host_audit is None:
            host_audit = lambda contract_id, event, details: append_event(
                self.host_audit_path, contract_id, event, details, timestamp=self.clock()
            )
        self.engine = RemediationEngine(
            registry=self.registry,
            store=self.store,
            context_linker=ContextLinker(self.context_service),
            observer=self.observer,
            executor=self.executor,
            host_audit=host_audit,
            lock_path=root / "bridge-restart.lock",
            now=self.clock,
            token_factory=self.tokens,
        )

    def plan(self) -> dict[str, Any]:
        return self.engine.prepare_plan(
            operation_id="bridge.restart.readiness_failure",
            operation_version=1,
            context_plan_id=CONTEXT_PLAN_ID,
            task_id=self.task_id,
        )

    def approve(self, plan_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        request = self.engine.request_approval(plan_id)
        record = self.engine.record_approval(
            request["approval_id"],
            token=request["approval_token"],
            operator_id="stage17-operator",
            decision="APPROVE",
        )
        return request, record

    def run(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        plan = self.plan()
        request, _ = self.approve(plan["plan_id"])
        result = self.engine.execute(
            plan["plan_id"],
            request["approval_id"],
            token=request["approval_token"],
            operator_id="stage17-operator",
        )
        return plan, request, result
