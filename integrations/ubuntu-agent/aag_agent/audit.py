"""Validated, hash-chained mutation audit persistence."""

from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "aag-mutation-audit-v1"
FIELDS = {"schema", "sequence", "timestamp", "contract_id", "event", "details", "previous_hash", "record_hash"}
HASH_LENGTH = len("sha256:") + 64
CHECKPOINT_SCHEMA = "aag-mutation-audit-checkpoint-v1"


class AuditError(ValueError):
    pass


class AuditPersistenceError(AuditError):
    def __init__(self, message: str, *, record_persisted: bool):
        super().__init__(message)
        self.record_persisted = record_persisted


def _canonical(body: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(dict(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuditError("audit_not_json_serializable") from exc


def _digest(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()


def record(contract_id: str, event: str, details: Mapping[str, Any], *, previous_hash: str | None = None, sequence: int = 1, timestamp: float | None = None) -> dict[str, Any]:
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise AuditError("invalid_sequence")
    if not isinstance(details, Mapping):
        raise AuditError("invalid_details")
    body = {"schema": SCHEMA, "sequence": sequence, "timestamp": time.time() if timestamp is None else timestamp, "contract_id": contract_id, "event": event, "details": dict(details), "previous_hash": previous_hash}
    return {**body, "record_hash": _digest(body)}


def validate_record(item: Any, *, expected_previous_hash: str | None, expected_sequence: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise AuditError("record_must_be_object")
    if set(item) != FIELDS:
        raise AuditError("malformed_record_fields")
    if item.get("schema") != SCHEMA:
        raise AuditError("unsupported_audit_schema")
    if not isinstance(item.get("contract_id"), str) or not item["contract_id"]:
        raise AuditError("invalid_contract_id")
    if not isinstance(item.get("event"), str) or not item["event"]:
        raise AuditError("invalid_event")
    if not isinstance(item.get("details"), dict):
        raise AuditError("invalid_details")
    timestamp = item.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        raise AuditError("invalid_timestamp")
    if item.get("sequence") != expected_sequence:
        raise AuditError("sequence_mismatch")
    if item.get("previous_hash") != expected_previous_hash:
        raise AuditError("previous_hash_mismatch")
    record_hash = item.get("record_hash")
    if not isinstance(record_hash, str) or len(record_hash) != HASH_LENGTH or not record_hash.startswith("sha256:"):
        raise AuditError("malformed_record_hash")
    body = {key: item[key] for key in FIELDS if key != "record_hash"}
    if _digest(body) != record_hash:
        raise AuditError("record_hash_mismatch")
    return item


def verify_records(records: Iterable[Any]) -> dict[str, Any]:
    previous = None
    count = 0
    for count, item in enumerate(records, 1):
        validate_record(item, expected_previous_hash=previous, expected_sequence=count)
        previous = item["record_hash"]
    return {"valid": True, "record_count": count, "last_hash": previous}


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise AuditError(f"blank_record:{line_number}")
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditError(f"malformed_json:{line_number}") from exc
        records.append(item)
    return records


def checkpoint_path(path: Path) -> Path:
    return path.with_name(path.name + ".checkpoint.json")


def _checkpoint_body(state: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema": CHECKPOINT_SCHEMA, "record_count": state["record_count"], "last_hash": state["last_hash"]}


def _checkpoint(state: Mapping[str, Any]) -> dict[str, Any]:
    body = _checkpoint_body(state)
    return {**body, "checkpoint_hash": _digest(body)}


def verify_checkpoint(path: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = checkpoint_path(path)
    if not checkpoint.exists():
        if state["record_count"]:
            raise AuditError("checkpoint_missing")
        return {"valid": True, "present": False}
    try:
        item = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError("checkpoint_malformed") from exc
    if not isinstance(item, dict) or set(item) != {"schema", "record_count", "last_hash", "checkpoint_hash"}:
        raise AuditError("checkpoint_fields_invalid")
    if item.get("schema") != CHECKPOINT_SCHEMA or item.get("record_count") != state["record_count"] or item.get("last_hash") != state["last_hash"]:
        raise AuditError("checkpoint_chain_mismatch")
    if item.get("checkpoint_hash") != _digest(_checkpoint_body(item)):
        raise AuditError("checkpoint_hash_mismatch")
    return {"valid": True, "present": True, "record_count": item["record_count"], "last_hash": item["last_hash"]}


def verify_chain(path: Path) -> dict[str, Any]:
    state = verify_records(read_records(path))
    checkpoint = verify_checkpoint(path, state)
    return {**state, "checkpoint": checkpoint}


def _write_checkpoint(path: Path, state: Mapping[str, Any]) -> None:
    target = checkpoint_path(path)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(_checkpoint(state), sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def append_event(path: Path, contract_id: str, event: str, details: Mapping[str, Any], *, timestamp: float | None = None) -> dict[str, Any]:
    """Lock, verify chain/checkpoint, append, fsync, and advance checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = verify_chain(path)
        item = record(contract_id, event, details, previous_hash=state["last_hash"], sequence=state["record_count"] + 1, timestamp=timestamp)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        new_state = {"record_count": state["record_count"] + 1, "last_hash": item["record_hash"]}
        try:
            _write_checkpoint(path, new_state)
        except OSError as exc:
            raise AuditPersistenceError("checkpoint_persistence_failed", record_persisted=True) from exc
    return item


def append(path: Path, audit_record: Mapping[str, Any]) -> None:
    """Compatibility append that validates both chain and supplied record."""
    raise AuditError("compatibility_append_disabled_use_append_event")
