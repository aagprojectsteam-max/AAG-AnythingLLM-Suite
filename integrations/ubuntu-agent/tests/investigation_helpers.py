"""Isolated fixtures for Diagnostic Reasoning V1."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from aag_agent.context_memory.config import load_config
from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.context_memory.store import ContextMemoryStore
from aag_agent.investigation.engine import InvestigationEngine
from aag_agent.investigation.registry import PlaybookRegistry
from aag_agent.investigation.store import InvestigationStore


class StringClock:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        return f"2026-08-27T17:00:{self.index:02d}.000000Z"


def performance_session(*, disk="66%", available=40, total=100, cpu=20, failed=None, status="OBSERVED"):
    facts = {
        "filesystem": {"state": "OBSERVED", "value": {"target": "/", "used_percent": disk}},
        "memory": {"state": "OBSERVED", "value": {"Mem": {"available": available, "total": total}}},
        "processes": {"state": "OBSERVED", "value": [{"command": "example", "cpu_percent": cpu}]},
        "failed_units": {"state": "OBSERVED", "value": failed or []},
    }
    return {
        "schema": "aag-diagnostic-session-v1", "status": status,
        "captured_at": 1787846400.0, "read_only": True, "mutated": False,
        "bundles": [{"profile": "performance", "status": status, "facts": facts, "errors": []}],
        "errors": [],
    }


def storage_session(*, disk="66%", mounted=True):
    facts = {
        "filesystem": {"state": "OBSERVED", "value": {"target": "/", "used_percent": disk}},
        "mount": {"state": "OBSERVED", "value": {"filesystems": ([{"target": "/", "options": "rw"}] if mounted else [])}},
    }
    return {
        "schema": "aag-diagnostic-session-v1", "status": "OBSERVED",
        "captured_at": 1787846400.0, "read_only": True, "mutated": False,
        "bundles": [{"profile": "storage_mount", "status": "OBSERVED", "facts": facts, "errors": []}],
        "errors": [],
    }


class FakeDiagnostics:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or performance_session()
        self.calls = []

    def __call__(self, requests):
        self.calls.append(requests)
        return self.payload


class FakeBridge:
    def __init__(self, classification="HEALTHY") -> None:
        self.classification = classification
        self.calls = 0

    def observe(self):
        self.calls += 1
        return {
            "schema": "aag-bridge-detector-evidence-v1",
            "observed_at": 1787846400.0,
            "target": "aag-ubuntu-agent-bridge.service",
            "classification": self.classification,
            "main_pid": "5555",
            "active_state": "active",
            "health_ready": self.classification == "HEALTHY",
            "read_only": True,
            "mutated": False,
        }


class InvestigationHarness:
    def __init__(self, root: Path, *, payload=None, bridge="HEALTHY", with_task=False) -> None:
        config = replace(load_config(), database_path=root / "context.sqlite3", sources=())
        context_store = ContextMemoryStore(config.database_path)
        context_store.migrate()
        self.context = ContextMemoryService(config, context_store)
        self.store = InvestigationStore(root / "investigations.sqlite3")
        self.store.migrate()
        self.diagnostics = FakeDiagnostics(payload)
        self.bridge = FakeBridge(bridge)
        self.clock = StringClock()
        self.engine = InvestigationEngine(
            registry=PlaybookRegistry(), store=self.store, context=self.context,
            diagnostic_runner=self.diagnostics, bridge_observer=self.bridge, clock=self.clock,
        )
        self.task_id = None
        if with_task:
            self.task_id = self.context.tasks.start("Investigate current slowness")["task_id"]

    def run(self, playbook="system.performance_investigation", request="Why is the computer slow?"):
        created = self.engine.create(playbook, request_summary=request, task_id=self.task_id)
        return self.engine.run(created["investigation_id"])
