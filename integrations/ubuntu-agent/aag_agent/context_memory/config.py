"""Strict fixed-path configuration for Context & Memory V1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/context-memory-v1.json"
DEFAULT_SEED_PATH = PROJECT_ROOT / "config/context-memory-seed-v1.json"
WINBOAT_SEED_PATH = PROJECT_ROOT / "config/context-memory-winboat-seed-v1.json"


class ContextMemoryConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ContextMemoryConfig:
    path: Path
    database_path: Path
    allowed_ingestion_roots: tuple[Path, ...]
    sources: tuple[dict[str, Any], ...]
    freshness_ttl_seconds: dict[str, int | None]
    limits: dict[str, int]
    context_budget_tokens: dict[str, int]
    canonical_fact_keys: frozenset[str]
    journal_mode: str
    synchronous: str
    busy_timeout_ms: int
    fingerprint: str


def _absolute(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ContextMemoryConfigurationError(f"invalid_absolute_path:{field}")
    return Path(value)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ContextMemoryConfig:
    if path != DEFAULT_CONFIG_PATH:
        raise ContextMemoryConfigurationError("alternate_production_config_not_allowed")
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextMemoryConfigurationError("context_memory_config_unreadable") from exc
    required = {
        "schema", "schema_version", "database_path", "journal_mode",
        "synchronous", "busy_timeout_ms", "allowed_ingestion_roots", "sources",
        "freshness_ttl_seconds", "limits", "context_budget_tokens",
        "canonical_fact_keys",
    }
    if set(data) != required or data.get("schema") != "aag-context-memory-config-v1":
        raise ContextMemoryConfigurationError("invalid_context_memory_config_schema")
    if data.get("schema_version") != 1:
        raise ContextMemoryConfigurationError("unsupported_context_memory_config_version")
    database_path = _absolute(data["database_path"], "database_path")
    if database_path != PROJECT_ROOT / "memory/context-memory-v1.sqlite3":
        raise ContextMemoryConfigurationError("database_path_not_fixed")
    roots = tuple(_absolute(item, "allowed_ingestion_roots") for item in data["allowed_ingestion_roots"])
    if not roots or len(set(roots)) != len(roots):
        raise ContextMemoryConfigurationError("invalid_ingestion_roots")
    if data["journal_mode"] != "WAL" or data["synchronous"] != "FULL":
        raise ContextMemoryConfigurationError("unsafe_sqlite_durability")
    if not isinstance(data["busy_timeout_ms"], int) or not 100 <= data["busy_timeout_ms"] <= 10000:
        raise ContextMemoryConfigurationError("invalid_busy_timeout")
    if not isinstance(data["sources"], list) or not data["sources"]:
        raise ContextMemoryConfigurationError("missing_sources")
    source_ids: set[str] = set()
    for source in data["sources"]:
        if not isinstance(source, dict) or not {
            "source_id", "source_type", "path", "verification_level",
            "temporal_scope", "lifecycle_state", "parser_version",
        }.issubset(source):
            raise ContextMemoryConfigurationError("invalid_source_spec")
        _absolute(source["path"], f"source:{source.get('source_id')}")
        if source["source_id"] in source_ids:
            raise ContextMemoryConfigurationError("duplicate_source_id")
        source_ids.add(source["source_id"])
    for mapping_name in ("freshness_ttl_seconds", "limits", "context_budget_tokens"):
        if not isinstance(data[mapping_name], dict):
            raise ContextMemoryConfigurationError(f"invalid_{mapping_name}")
    budgets = data["context_budget_tokens"]
    if set(budgets) != {"exact", "normal", "history", "complex"}:
        raise ContextMemoryConfigurationError("invalid_budget_tiers")
    if max(budgets.values()) > data["limits"]["max_context_tokens"]:
        raise ContextMemoryConfigurationError("budget_above_hard_ceiling")
    return ContextMemoryConfig(
        path=path,
        database_path=database_path,
        allowed_ingestion_roots=roots,
        sources=tuple(dict(item) for item in data["sources"]),
        freshness_ttl_seconds=dict(data["freshness_ttl_seconds"]),
        limits={key: int(value) for key, value in data["limits"].items()},
        context_budget_tokens={key: int(value) for key, value in budgets.items()},
        canonical_fact_keys=frozenset(data["canonical_fact_keys"]),
        journal_mode=data["journal_mode"],
        synchronous=data["synchronous"],
        busy_timeout_ms=data["busy_timeout_ms"],
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def load_seed(path: Path = DEFAULT_SEED_PATH) -> dict[str, Any]:
    if path not in {DEFAULT_SEED_PATH, WINBOAT_SEED_PATH}:
        raise ContextMemoryConfigurationError("alternate_seed_not_allowed")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextMemoryConfigurationError("context_memory_seed_unreadable") from exc
    if not isinstance(data, dict) or data.get("schema") != "aag-context-memory-seed-v1":
        raise ContextMemoryConfigurationError("invalid_context_memory_seed")
    if set(data) != {"schema", "entities", "relationships", "claims", "incidents"}:
        raise ContextMemoryConfigurationError("invalid_context_memory_seed_fields")
    return data
