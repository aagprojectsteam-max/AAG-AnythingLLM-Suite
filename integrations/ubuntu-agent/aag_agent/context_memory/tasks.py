"""Structured, bounded, task-isolated continuity."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import canonical_json, stable_id, utc_now
from .store import ContextMemoryStore

TASK_ID = re.compile(r"^task:[a-f0-9]{24}$")
STATE_FIELDS = {
    "hypotheses", "observations", "evidence_ids", "tools_used", "decisions",
    "rejected_approaches", "open_questions", "pending_approvals",
    "next_recommended_checks",
}


class TaskError(ValueError):
    pass


class TaskStore:
    def __init__(self, store: ContextMemoryStore, *, max_json_bytes: int = 32000) -> None:
        self.store = store
        self.max_json_bytes = max_json_bytes

    @staticmethod
    def _initial_state() -> dict[str, list[Any]]:
        return {field: [] for field in sorted(STATE_FIELDS)}

    def _validate_id(self, task_id: str) -> None:
        if not isinstance(task_id, str) or TASK_ID.fullmatch(task_id) is None:
            raise TaskError("invalid_task_id")

    def _event(self, connection, task_id: str, event_type: str, details: Mapping[str, Any]) -> None:
        sequence = int(connection.execute(
            "SELECT count(*) FROM task_events WHERE task_id=?", (task_id,)
        ).fetchone()[0]) + 1
        connection.execute(
            """INSERT INTO task_events
               (task_event_id,task_id,sequence,event_type,details_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                stable_id("task-event", task_id, sequence, event_type),
                task_id, sequence, event_type, canonical_json(details), utc_now(),
            ),
        )

    def start(
        self,
        user_goal: str,
        *,
        scope: Mapping[str, Any] | None = None,
        entities: list[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user_goal, str) or not user_goal.strip() or len(user_goal) > 2000:
            raise TaskError("invalid_task_goal")
        scope = dict(scope or {})
        entities = list(entities or [])
        state = self._initial_state()
        task_id = stable_id("task", utc_now(), user_goal, scope, entities)
        now = utc_now()
        payloads = [canonical_json(scope), canonical_json(entities), canonical_json(state)]
        if any(len(item.encode("utf-8")) > self.max_json_bytes for item in payloads):
            raise TaskError("task_payload_too_large")
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT INTO tasks
                   (task_id,created_at,updated_at,user_goal,scope_json,entities_json,
                    state_json,result_json,closure_status)
                   VALUES (?,?,?,?,?,?,?,NULL,'OPEN')""",
                (
                    task_id, now, now, user_goal.strip(), payloads[0],
                    payloads[1], payloads[2],
                ),
            )
            self._event(connection, task_id, "STARTED", {"user_goal": user_goal.strip()})
        return self.show(task_id)

    @staticmethod
    def _timestamp(value: str) -> float:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (AttributeError, TypeError, ValueError):
            return 0.0

    def active(self, *, orchestrator: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Return bounded active task states, optionally for one trusted owner."""
        if orchestrator is not None and (not isinstance(orchestrator, str) or not orchestrator):
            raise TaskError("invalid_task_owner")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise TaskError("invalid_task_limit")
        with self.store.read() as connection:
            rows = connection.execute(
                """SELECT task_id,scope_json FROM tasks
                   WHERE closure_status IN ('OPEN','BLOCKED')
                   ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        task_ids = []
        for row in rows:
            try:
                scope = json.loads(row["scope_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if orchestrator is None or scope.get("orchestrator") == orchestrator:
                task_ids.append(row["task_id"])
        return [self.show(task_id) for task_id in task_ids]

    def start_or_reuse(
        self,
        user_goal: str,
        *,
        scope: Mapping[str, Any],
        entities: list[str],
        request_fingerprint: str,
        window_seconds: float = 30.0,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically reuse one recent identical active task or start it once."""
        if (
            not isinstance(user_goal, str)
            or not user_goal.strip()
            or len(user_goal) > 2000
            or not isinstance(scope, Mapping)
            or not isinstance(entities, list)
            or re.fullmatch(r"[a-f0-9]{64}", request_fingerprint or "") is None
            or not isinstance(window_seconds, (int, float))
            or isinstance(window_seconds, bool)
            or not 1 <= float(window_seconds) <= 120
        ):
            raise TaskError("invalid_idempotent_task_request")
        checked_scope = dict(scope)
        checked_scope["request_fingerprint"] = request_fingerprint
        state = self._initial_state()
        payloads = [canonical_json(checked_scope), canonical_json(entities), canonical_json(state)]
        if any(len(item.encode("utf-8")) > self.max_json_bytes for item in payloads):
            raise TaskError("task_payload_too_large")
        now = utc_now()
        current_epoch = datetime.now(timezone.utc).timestamp()
        reused_id = None
        reused = False
        with self.store.transaction() as connection:
            rows = connection.execute(
                """SELECT task_id,scope_json,updated_at FROM tasks
                   WHERE closure_status IN ('OPEN','BLOCKED')
                   ORDER BY updated_at DESC LIMIT 100"""
            ).fetchall()
            for row in rows:
                try:
                    candidate_scope = json.loads(row["scope_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                age = current_epoch - self._timestamp(row["updated_at"])
                if candidate_scope.get("request_fingerprint") == request_fingerprint and 0 <= age <= float(window_seconds):
                    reused_id = row["task_id"]
                    reused = True
                    break
            if reused_id is None:
                task_id = stable_id("task", now, user_goal, checked_scope, entities)
                connection.execute(
                    """INSERT INTO tasks
                       (task_id,created_at,updated_at,user_goal,scope_json,entities_json,
                        state_json,result_json,closure_status)
                       VALUES (?,?,?,?,?,?,?,NULL,'OPEN')""",
                    (task_id, now, now, user_goal.strip(), payloads[0], payloads[1], payloads[2]),
                )
                self._event(connection, task_id, "STARTED", {
                    "user_goal": user_goal.strip(),
                    "idempotency": "bounded_request_fingerprint",
                })
                reused_id = task_id
        return self.show(reused_id), reused

    def update(self, task_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_id(task_id)
        if not isinstance(changes, Mapping) or not changes or set(changes) - STATE_FIELDS:
            raise TaskError("invalid_task_update")
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise TaskError("task_not_found")
            if row["closure_status"] not in {"OPEN", "BLOCKED"}:
                raise TaskError("task_is_closed")
            state = json.loads(row["state_json"])
            for field, values in changes.items():
                if not isinstance(values, list):
                    raise TaskError("task_update_values_must_be_lists")
                if field == "evidence_ids" and values:
                    if not all(isinstance(item, str) for item in values) or not self.store.evidence_exists(values, connection):
                        raise TaskError("task_evidence_missing")
                current = state[field]
                for value in values:
                    if value not in current:
                        current.append(value)
            encoded = canonical_json(state)
            if len(encoded.encode("utf-8")) > self.max_json_bytes:
                raise TaskError("task_payload_too_large")
            connection.execute(
                "UPDATE tasks SET state_json=?,updated_at=? WHERE task_id=?",
                (encoded, utc_now(), task_id),
            )
            self._event(connection, task_id, "UPDATED", dict(changes))
        return self.show(task_id)

    def show(self, task_id: str) -> dict[str, Any]:
        self._validate_id(task_id)
        with self.store.read() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise TaskError("task_not_found")
            events = connection.execute(
                """SELECT sequence,event_type,details_json,created_at FROM task_events
                   WHERE task_id=? ORDER BY sequence""",
                (task_id,),
            ).fetchall()
        return {
            "schema": "aag-task-state-v1",
            "task_id": row["task_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "user_goal": row["user_goal"],
            "scope": json.loads(row["scope_json"]),
            "entities": json.loads(row["entities_json"]),
            **json.loads(row["state_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "closure_status": row["closure_status"],
            "events": [
                {
                    "sequence": item["sequence"],
                    "event_type": item["event_type"],
                    "details": json.loads(item["details_json"]),
                    "created_at": item["created_at"],
                }
                for item in events
            ],
        }

    def resume(self, task_id: str) -> dict[str, Any]:
        task = self.show(task_id)
        if task["closure_status"] not in {"OPEN", "BLOCKED"}:
            raise TaskError("task_not_resumable")
        with self.store.transaction() as connection:
            self._event(connection, task_id, "RESUMED", {})
            connection.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (utc_now(), task_id))
        return self.show(task_id)

    def close(self, task_id: str, result: Mapping[str, Any], *, status: str = "COMPLETE") -> dict[str, Any]:
        self._validate_id(task_id)
        if status not in {"COMPLETE", "CANCELLED"} or not isinstance(result, Mapping):
            raise TaskError("invalid_task_closure")
        encoded = canonical_json(result)
        if len(encoded.encode("utf-8")) > self.max_json_bytes:
            raise TaskError("task_result_too_large")
        with self.store.transaction() as connection:
            row = connection.execute("SELECT closure_status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise TaskError("task_not_found")
            if row["closure_status"] not in {"OPEN", "BLOCKED"}:
                raise TaskError("task_is_closed")
            connection.execute(
                """UPDATE tasks SET result_json=?,closure_status=?,updated_at=?
                   WHERE task_id=?""",
                (encoded, status, utc_now(), task_id),
            )
            self._event(connection, task_id, "CLOSED", {"status": status})
        return self.show(task_id)
