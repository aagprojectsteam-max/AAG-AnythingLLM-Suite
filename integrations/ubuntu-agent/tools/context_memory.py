#!/usr/bin/env python3
"""Operator CLI for AAG Context & Memory V1; no raw SQL or database path input."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aag_agent.context_memory.benchmark import BenchmarkRunner
from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.investigation.service import build_engine as build_investigation_engine
from aag_agent.orchestration.service import build_orchestrator
from aag_agent.remediation.service import build_engine as build_remediation_engine

GOLDEN = ROOT / "tests/fixtures/context_memory/golden_queries.json"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="AAG Context & Memory V1")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--ingest", action="store_true")
    commands.add_parser("status")
    commands.add_parser("integrity-check")
    commands.add_parser("ingestion-dry-run")
    commands.add_parser("ingestion-apply")
    commands.add_parser("source-inventory")
    exact = commands.add_parser("exact-search")
    exact.add_argument("query")
    history = commands.add_parser("historical-search")
    history.add_argument("query")
    entity = commands.add_parser("entity-lookup")
    entity.add_argument("query")
    commands.add_parser("conflict-list")
    start = commands.add_parser("task-start")
    start.add_argument("goal")
    start.add_argument("--entity", action="append", default=[])
    for name in ("task-show", "task-resume"):
        command = commands.add_parser(name)
        command.add_argument("task_id")
    update = commands.add_parser("task-update")
    update.add_argument("task_id")
    update.add_argument("--field", required=True, choices=[
        "hypotheses", "observations", "evidence_ids", "tools_used", "decisions",
        "rejected_approaches", "open_questions", "pending_approvals",
        "next_recommended_checks",
    ])
    update.add_argument("--value", required=True)
    close = commands.add_parser("task-close")
    close.add_argument("task_id")
    close.add_argument("result")
    context = commands.add_parser("context-assemble")
    context.add_argument("query")
    context.add_argument("--task-id")
    context.add_argument("--budget-tier", choices=["exact", "normal", "history", "complex"])
    commands.add_parser("benchmark-run")
    commands.add_parser("remediation-init")
    commands.add_parser("remediation-operation-list")
    operation_show = commands.add_parser("remediation-operation-show")
    operation_show.add_argument("operation_id")
    operation_show.add_argument("version", type=int)
    plan_validate = commands.add_parser("remediation-plan-validate")
    plan_validate.add_argument("operation_id")
    plan_validate.add_argument("version", type=int)
    plan_validate.add_argument("context_plan_id")
    plan_validate.add_argument("--task-id")
    plan_show = commands.add_parser("remediation-plan-show")
    plan_show.add_argument("plan_id")
    attempt_status = commands.add_parser("remediation-attempt-status")
    attempt_status.add_argument("attempt_id")
    attempt_events = commands.add_parser("remediation-attempt-events")
    attempt_events.add_argument("attempt_id")
    precondition = commands.add_parser("remediation-precondition-check")
    precondition.add_argument("plan_id")
    for name in ("remediation-backup-dry-run", "remediation-backup-check"):
        backup = commands.add_parser(name)
        backup.add_argument("plan_id")
    approval_request = commands.add_parser("remediation-approval-request")
    approval_request.add_argument("plan_id")
    approval_request.add_argument("--ttl-seconds", type=int, default=600)
    approval_record = commands.add_parser("remediation-approval-record")
    approval_record.add_argument("approval_id")
    approval_record.add_argument("operator_id")
    approval_record.add_argument("decision", choices=["APPROVE", "REJECT"])
    execute = commands.add_parser("remediation-execute")
    execute.add_argument("plan_id")
    execute.add_argument("approval_id")
    execute.add_argument("operator_id")
    post_verify = commands.add_parser("remediation-post-verify")
    post_verify.add_argument("attempt_id")
    rollback_proposal = commands.add_parser("remediation-rollback-proposal")
    rollback_proposal.add_argument("attempt_id")
    rollback_status = commands.add_parser("remediation-rollback-status")
    rollback_status.add_argument("rollback_proposal_id")
    commands.add_parser("remediation-audit-verify")
    commands.add_parser("investigation-init")
    commands.add_parser("investigation-playbook-list")
    playbook_show = commands.add_parser("investigation-playbook-show")
    playbook_show.add_argument("playbook_id")
    playbook_show.add_argument("version", type=int, nargs="?", default=1)
    investigation_start = commands.add_parser("investigation-start")
    investigation_start.add_argument("playbook_id")
    investigation_start.add_argument("request_summary")
    investigation_start.add_argument("--version", type=int, default=1)
    investigation_start.add_argument("--task-id")
    for name in ("investigation-run", "investigation-show", "investigation-close"):
        investigation = commands.add_parser(name)
        investigation.add_argument("investigation_id")
    commands.add_parser("investigation-integrity-check")
    for name in ("orchestrate-preview", "orchestrate"):
        orchestrate = commands.add_parser(name)
        orchestrate.add_argument("request")
        orchestrate.add_argument("--task-id")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    service = ContextMemoryService()
    command = args.command
    remediation = None
    investigation = None
    orchestrator = None
    if command.startswith("remediation-"):
        remediation = build_remediation_engine()
        if command != "remediation-init":
            remediation.store.migrate()
    if command.startswith("investigation-"):
        investigation = build_investigation_engine(context=service)
        if command != "investigation-init":
            service.store.migrate()
            investigation.store.migrate()
    if command.startswith("orchestrate"):
        orchestrator = build_orchestrator(context=service)
    if command == "init":
        result = service.initialize(ingest=args.ingest)
    elif command == "status":
        result = service.dispatch({"operation": "status"})
    elif command == "integrity-check":
        result = service.store.integrity()
    elif command == "ingestion-dry-run":
        result = service.ingestion.run_configured(apply=False)
    elif command == "ingestion-apply":
        result = service.ingestion.run_configured(apply=True)
    elif command == "source-inventory":
        with service.store.read() as connection:
            result = {
                "schema": "aag-source-inventory-v1",
                "sources": [
                    dict(row) for row in connection.execute(
                        """SELECT s.*,count(a.artifact_id) AS artifact_count
                           FROM sources s LEFT JOIN source_artifacts a ON a.source_id=s.source_id
                           GROUP BY s.source_id ORDER BY s.source_id"""
                    )
                ],
            }
    elif command == "exact-search":
        result = service.retriever.search(args.query, include_historical=False)
    elif command == "historical-search":
        result = service.retriever.search(args.query, include_historical=True)
    elif command == "entity-lookup":
        result = {
            "schema": "aag-entity-lookup-v1",
            "entities": service.retriever.resolve_entities(args.query),
        }
    elif command == "conflict-list":
        result = {"schema": "aag-conflict-list-v1", "conflicts": service.store.list_conflicts()}
    elif command == "task-start":
        result = service.tasks.start(args.goal, entities=args.entity)
    elif command == "task-show":
        result = service.tasks.show(args.task_id)
    elif command == "task-resume":
        result = service.tasks.resume(args.task_id)
    elif command == "task-update":
        result = service.tasks.update(args.task_id, {args.field: [args.value]})
    elif command == "task-close":
        result = service.tasks.close(args.task_id, {"summary": args.result})
    elif command == "context-assemble":
        result = service.assembler.assemble(
            args.query, task_id=args.task_id, budget_tier=args.budget_tier
        )
    elif command == "benchmark-run":
        result = BenchmarkRunner(service, GOLDEN).run()
    elif command == "remediation-init":
        result = remediation.initialize()
    elif command == "remediation-operation-list":
        result = remediation.operation_list()
    elif command == "remediation-operation-show":
        result = remediation.operation_show(args.operation_id, args.version)
    elif command == "remediation-plan-validate":
        result = remediation.prepare_plan(
            operation_id=args.operation_id,
            operation_version=args.version,
            context_plan_id=args.context_plan_id,
            task_id=args.task_id,
        )
    elif command == "remediation-plan-show":
        result = remediation.store.get_plan(args.plan_id)
    elif command == "remediation-attempt-status":
        result = remediation.attempt_status(args.attempt_id)
    elif command == "remediation-attempt-events":
        attempt = remediation.attempt_status(args.attempt_id)
        result = {
            "schema": "aag-remediation-attempt-events-v1",
            "attempt_id": args.attempt_id,
            "events": remediation.store.events(attempt["plan_id"]),
            "execution_authority": "NONE",
        }
    elif command == "remediation-precondition-check":
        result = remediation.check_preconditions(args.plan_id)
    elif command in {"remediation-backup-dry-run", "remediation-backup-check"}:
        result = remediation.backup_status(
            args.plan_id,
            dry_run=command == "remediation-backup-dry-run",
        )
    elif command == "remediation-approval-request":
        result = remediation.request_approval(args.plan_id, ttl_seconds=args.ttl_seconds)
    elif command == "remediation-approval-record":
        token = sys.stdin.readline(257).strip()
        result = remediation.record_approval(
            args.approval_id,
            token=token,
            operator_id=args.operator_id,
            decision=args.decision,
        )
    elif command == "remediation-execute":
        token = sys.stdin.readline(257).strip()
        result = remediation.execute(
            args.plan_id,
            args.approval_id,
            token=token,
            operator_id=args.operator_id,
        )
    elif command == "remediation-post-verify":
        result = remediation.post_verify(args.attempt_id)
    elif command == "remediation-rollback-proposal":
        result = remediation.rollback_proposal(args.attempt_id)
    elif command == "remediation-rollback-status":
        result = remediation.rollback_status(args.rollback_proposal_id)
    elif command == "remediation-audit-verify":
        result = remediation.audit_verify()
    elif command == "investigation-init":
        result = investigation.initialize()
    elif command == "investigation-playbook-list":
        result = {
            "schema": "aag-diagnostic-playbook-list-v1",
            "playbooks": investigation.registry.list(),
            "execution_authority": "NONE",
        }
    elif command == "investigation-playbook-show":
        playbook = investigation.registry.get(args.playbook_id, args.version)
        result = {
            "schema": "aag-diagnostic-playbook-v1",
            "playbook": dict(playbook.data),
            "registry_sha256": playbook.registry_sha256,
            "execution_authority": "NONE",
        }
    elif command == "investigation-start":
        result = investigation.create(
            args.playbook_id,
            version=args.version,
            request_summary=args.request_summary,
            task_id=args.task_id,
        )
    elif command == "investigation-run":
        result = investigation.run(args.investigation_id)
    elif command == "investigation-show":
        result = investigation.show(args.investigation_id)
    elif command == "investigation-close":
        result = investigation.close(args.investigation_id)
    elif command == "investigation-integrity-check":
        result = investigation.store.integrity()
    elif command == "orchestrate-preview":
        result = orchestrator.preview(args.request)
    elif command == "orchestrate":
        result = orchestrator.handle(args.request, task_id=args.task_id)
    else:
        raise AssertionError("unreachable_context_command")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    status = result.get("status")
    return 2 if status in {"FAIL", "failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
