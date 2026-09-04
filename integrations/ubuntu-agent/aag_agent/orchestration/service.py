"""Composition root for Governed Orchestration V1."""

from __future__ import annotations

from pathlib import Path

from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.investigation.engine import InvestigationEngine
from aag_agent.investigation.registry import DEFAULT_REGISTRY, PlaybookRegistry
from aag_agent.investigation.store import DEFAULT_DATABASE, InvestigationStore

from .engine import GovernedOrchestrator
from .contracts import ValidatedRequest, validate_request, validate_response


def build_orchestrator(
    *,
    context: ContextMemoryService | None = None,
    investigation_database: Path = DEFAULT_DATABASE,
    playbook_registry: Path = DEFAULT_REGISTRY,
    diagnostic_runner=None,
    bridge_observer=None,
) -> GovernedOrchestrator:
    context = context or ContextMemoryService()
    context.store.migrate()
    store = InvestigationStore(investigation_database)
    store.migrate()
    kwargs = {}
    if diagnostic_runner is not None:
        kwargs["diagnostic_runner"] = diagnostic_runner
    if bridge_observer is not None:
        kwargs["bridge_observer"] = bridge_observer
    investigations = InvestigationEngine(
        registry=PlaybookRegistry(playbook_registry), store=store, context=context, **kwargs
    )
    return GovernedOrchestrator(context=context, investigations=investigations)


def dispatch_orchestration(payload, *, orchestrator: GovernedOrchestrator | None = None) -> dict:
    """Validate the sole model-facing contract and dispatch to trusted code."""
    validated: ValidatedRequest = validate_request(payload)
    instance = orchestrator or build_orchestrator()
    return validate_response(instance.handle(validated.request, task_id=validated.task_id))
