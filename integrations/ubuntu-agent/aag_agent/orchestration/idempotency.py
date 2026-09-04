"""Bounded in-process replay and concurrency control for orchestration calls."""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Iterator


class IdempotencyGuard:
    def __init__(
        self, *, window_seconds: float = 30.0, maximum_entries: int = 256,
        monotonic=time.monotonic,
    ) -> None:
        if not 1 <= window_seconds <= 120 or not 8 <= maximum_entries <= 1024:
            raise ValueError("invalid_idempotency_limits")
        self.window_seconds = float(window_seconds)
        self.maximum_entries = int(maximum_entries)
        self.monotonic = monotonic
        self._state_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    @staticmethod
    def fingerprint(request: str, task_id: str | None = None) -> str:
        body = f"aag-orchestration-idempotency-v1\x00{request.strip().casefold()}\x00{task_id or ''}"
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @staticmethod
    def request_id(fingerprint: str) -> str:
        return "orchestration-request:" + fingerprint[:24]

    def _prune(self, now: float) -> None:
        expired = [key for key, (deadline, _value) in self._cache.items() if deadline <= now]
        for key in expired:
            self._cache.pop(key, None)
        while len(self._cache) > self.maximum_entries:
            self._cache.popitem(last=False)

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        now = self.monotonic()
        with self._state_lock:
            self._prune(now)
            item = self._cache.get(fingerprint)
            if item is None:
                return None
            self._cache.move_to_end(fingerprint)
            return copy.deepcopy(item[1])

    def put(self, fingerprint: str, response: dict[str, Any]) -> None:
        now = self.monotonic()
        with self._state_lock:
            self._prune(now)
            self._cache[fingerprint] = (now + self.window_seconds, copy.deepcopy(response))
            self._cache.move_to_end(fingerprint)
            self._prune(now)

    @contextmanager
    def exclusive(self, fingerprint: str) -> Iterator[None]:
        with self._state_lock:
            lock = self._locks.setdefault(fingerprint, threading.Lock())
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._state_lock:
                if not lock.locked():
                    self._locks.pop(fingerprint, None)
