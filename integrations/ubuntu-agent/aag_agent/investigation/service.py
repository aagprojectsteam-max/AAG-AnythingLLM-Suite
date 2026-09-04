"""Composition root for Diagnostic Reasoning V1."""

from __future__ import annotations

from pathlib import Path

from aag_agent.context_memory.service import ContextMemoryService

from .engine import InvestigationEngine
from .registry import DEFAULT_REGISTRY, PlaybookRegistry
from .store import DEFAULT_DATABASE, InvestigationStore


def build_engine(
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    database_path: Path = DEFAULT_DATABASE,
    context: ContextMemoryService | None = None,
    diagnostic_runner=None,
    bridge_observer=None,
) -> InvestigationEngine:
    kwargs = {}
    if diagnostic_runner is not None:
        kwargs["diagnostic_runner"] = diagnostic_runner
    if bridge_observer is not None:
        kwargs["bridge_observer"] = bridge_observer
    return InvestigationEngine(
        registry=PlaybookRegistry(registry_path),
        store=InvestigationStore(database_path),
        context=context or ContextMemoryService(),
        **kwargs,
    )
