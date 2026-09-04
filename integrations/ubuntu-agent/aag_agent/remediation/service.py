"""Fixed production composition root for the Stage 17 operator interface."""

from __future__ import annotations

from aag_agent.context_memory.service import ContextMemoryService

from .context_link import ContextLinker
from .engine import RemediationEngine
from .registry import OperationRegistry
from .store import RemediationStore


def build_engine() -> RemediationEngine:
    context_service = ContextMemoryService()
    return RemediationEngine(
        registry=OperationRegistry(),
        store=RemediationStore(),
        context_linker=ContextLinker(context_service),
    )
