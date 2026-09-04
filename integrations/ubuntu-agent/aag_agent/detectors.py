"""Normalized, deterministic failure evidence for accepted domains."""

from __future__ import annotations

import time
from typing import Any, Mapping

BRIDGE_TARGET = "aag-ubuntu-agent-bridge.service"
SUPPORTED_FAILURE = "systemd_active_running_but_health_endpoint_unready"
UNOBSERVABLE_ERRORS = {"permission_denied", "socket_permission_denied", "systemd_unavailable", "observer_unavailable", "target_not_allowlisted", "socket_not_allowlisted"}


def normalize_bridge_evidence(snapshot: Mapping[str, Any] | None, readiness: Mapping[str, Any] | None, *, observed_at: float | None = None, target: str = BRIDGE_TARGET) -> dict[str, Any]:
    """Convert raw service/readiness observations into one stable schema."""
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    readiness = readiness if isinstance(readiness, Mapping) else {}
    health_ready = readiness.get("ready")
    health_error = readiness.get("error")
    service_known = snapshot.get("status") == "completed"
    if target != BRIDGE_TARGET or snapshot.get("target", target) != target:
        classification = "WRONG_TARGET"
    elif health_ready is True:
        classification = "HEALTHY"
    elif health_error in UNOBSERVABLE_ERRORS:
        classification = "UNOBSERVABLE"
    elif not service_known:
        classification = "MISSING"
    elif snapshot.get("load_state") != "loaded" or snapshot.get("active_state") != "active" or snapshot.get("sub_state") != "running":
        classification = "UNSUPPORTED_SERVICE_STATE"
    elif health_ready is False and health_error == "readiness_timeout":
        classification = "SUPPORTED_FAILURE"
    else:
        classification = "INDETERMINATE"
    return {
        "schema": "aag-bridge-detector-evidence-v1",
        "observed_at": time.time() if observed_at is None else observed_at,
        "target": target,
        "load_state": snapshot.get("load_state"),
        "active_state": snapshot.get("active_state"),
        "sub_state": snapshot.get("sub_state"),
        "main_pid": snapshot.get("main_pid", snapshot.get("id")),
        "health_ready": health_ready if isinstance(health_ready, bool) else None,
        "health_error": health_error,
        "classification": classification,
        "supported_failure_class": SUPPORTED_FAILURE if classification == "SUPPORTED_FAILURE" else None,
        "provenance": {"service_snapshot_schema": snapshot.get("snapshot_schema"), "readiness_attempts": readiness.get("attempts_used"), "read_only": True},
    }
