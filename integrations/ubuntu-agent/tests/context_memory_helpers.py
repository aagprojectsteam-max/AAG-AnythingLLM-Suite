"""Test helpers for isolated Context & Memory stores."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from aag_agent.context_memory.config import load_config
from aag_agent.context_memory.ingestion import IngestionPipeline
from aag_agent.context_memory.models import stable_id, utc_now
from aag_agent.context_memory.service import ContextMemoryService
from aag_agent.context_memory.store import ContextMemoryStore


def isolated(root: Path):
    config = replace(
        load_config(),
        database_path=root / "context.sqlite3",
        allowed_ingestion_roots=(root,),
        sources=(),
    )
    store = ContextMemoryStore(
        config.database_path,
        busy_timeout_ms=config.busy_timeout_ms,
        journal_mode=config.journal_mode,
        synchronous=config.synchronous,
    )
    store.migrate()
    pipeline = IngestionPipeline(store, config, allowed_roots=(root,))
    return config, store, pipeline


def begin_run(store: ContextMemoryStore, name: str = "test") -> str:
    run_id = stable_id("ingestion-run", name, utc_now())
    with store.transaction() as connection:
        connection.execute(
            """INSERT INTO ingestion_runs
               (run_id,started_at,completed_at,mode,parser_version,status,stats_json)
               VALUES (?,?,NULL,'APPLY','test-v1','RUNNING','{}')""",
            (run_id, utc_now()),
        )
    return run_id


def spec(path: Path, *, source_id: str = "test-source", parser: str = "markdown-v1", adapter=None):
    item = {
        "source_id": source_id,
        "source_type": "imported_document",
        "path": str(path),
        "verification_level": "DOCUMENTED",
        "temporal_scope": "HISTORICAL",
        "lifecycle_state": "ACTIVE",
        "parser_version": parser,
    }
    if adapter:
        item["adapter"] = adapter
    return item


def seeded(root: Path, *, diagnostic_runner=None):
    config = replace(load_config(), database_path=root / "seeded.sqlite3")
    store = ContextMemoryStore(
        config.database_path,
        busy_timeout_ms=config.busy_timeout_ms,
        journal_mode=config.journal_mode,
        synchronous=config.synchronous,
    )
    pipeline = IngestionPipeline(store, config)
    pipeline.run_configured(apply=True)
    kwargs = {}
    if diagnostic_runner is not None:
        kwargs["diagnostic_runner"] = diagnostic_runner
    return ContextMemoryService(config, store, **kwargs)
