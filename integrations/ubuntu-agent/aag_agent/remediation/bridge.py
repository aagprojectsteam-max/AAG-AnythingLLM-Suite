"""Exact Bridge observation and executor primitives.

No caller-controlled executable, argv, environment, target, socket or path is
accepted by this module.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from aag_agent.detectors import BRIDGE_TARGET, normalize_bridge_evidence
from aag_agent.endpoints import BRIDGE_HEALTH_PATH, BRIDGE_SOCKET_HOST

from .models import OperationSpec, bounded_text

SYSTEMCTL = "/usr/bin/systemctl"
FIXED_RESTART_ARGV = [SYSTEMCTL, "--user", "restart", BRIDGE_TARGET]
FIXED_SHOW_ARGV = [
    SYSTEMCTL,
    "--user",
    "show",
    BRIDGE_TARGET,
    "--property=Id",
    "--property=LoadState",
    "--property=ActiveState",
    "--property=SubState",
    "--property=UnitFileState",
    "--property=FragmentPath",
    "--property=MainPID",
]


def minimal_user_systemd_environment() -> dict[str, str]:
    uid = os.getuid()
    runtime = f"/run/user/{uid}"
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }


class ExactTargetLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = None

    def __enter__(self) -> "ExactTargetLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RuntimeError("resource_lock_symlink_forbidden")
        self._stream = self.path.open("a", encoding="utf-8")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._stream.close()
            self._stream = None
            raise RuntimeError("resource_lock_busy") from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None


class UnixHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(str(BRIDGE_SOCKET_HOST))


def _health_once() -> dict[str, Any]:
    try:
        connection = UnixHTTPConnection("localhost", timeout=2)
        connection.request("GET", BRIDGE_HEALTH_PATH)
        response = connection.getresponse()
        body = response.read(65536).decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        health_status = payload.get("status") if isinstance(payload, dict) else None
        return {
            "ready": response.status == 200 and health_status == "ok",
            "http_status": response.status,
            "health_status": health_status,
            "mutated": False,
        }
    except PermissionError:
        return {"ready": False, "error": "socket_permission_denied", "mutated": False}
    except Exception as exc:
        return {
            "ready": False,
            "error": "readiness_timeout",
            "detail": bounded_text(f"{type(exc).__name__}: {exc}", limit=512),
            "mutated": False,
        }


class BridgeObservationProvider:
    """Fixed, read-only current-state provider for the accepted Bridge fixture."""

    def __init__(
        self,
        *,
        runner: Callable[..., Any] = subprocess.run,
        health_probe: Callable[[], dict[str, Any]] = _health_once,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.health_probe = health_probe
        self.sleeper = sleeper

    def observe(self) -> dict[str, Any]:
        try:
            result = self.runner(
                FIXED_SHOW_ARGV,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                shell=False,
                env=minimal_user_systemd_environment(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            readiness = {"ready": False, "error": "systemd_unavailable", "detail": type(exc).__name__}
            return normalize_bridge_evidence(None, readiness)
        fields: dict[str, str] = {}
        for line in str(result.stdout or "").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        snapshot = {
            "status": "completed" if result.returncode == 0 else "error",
            "snapshot_schema": "aag-user-service-live-snapshot-v1",
            "target": BRIDGE_TARGET,
            "load_state": fields.get("LoadState", "unknown"),
            "active_state": fields.get("ActiveState", "unknown"),
            "sub_state": fields.get("SubState", "unknown"),
            "main_pid": fields.get("MainPID", "0"),
            "unit_file_state": fields.get("UnitFileState", "unknown"),
            "fragment_path": fields.get("FragmentPath", ""),
            "returncode": result.returncode,
            "read_only": True,
            "mutated": False,
        }
        readiness = self.health_probe()
        readiness = dict(readiness)
        readiness.setdefault("attempts_used", 1)
        return normalize_bridge_evidence(snapshot, readiness)

    def verify(self, pre_pid: str, *, attempts: int = 20, interval_seconds: float = 0.5) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        for index in range(attempts):
            evidence = self.observe()
            observations.append(evidence)
            current_pid = str(evidence.get("main_pid") or "0")
            if evidence.get("classification") == "HEALTHY":
                if current_pid == "0":
                    return {"status": "FAILED", "reason": "main_pid_invalid", "observations": observations}
                if current_pid == str(pre_pid):
                    return {"status": "FAILED", "reason": "main_pid_unchanged", "observations": observations}
                return {
                    "status": "PASS",
                    "pre_pid": str(pre_pid),
                    "post_pid": current_pid,
                    "evidence": evidence,
                    "observations": observations,
                }
            if evidence.get("classification") in {"UNOBSERVABLE", "INDETERMINATE", "MISSING", "WRONG_TARGET"}:
                if index + 1 == attempts:
                    return {"status": "INDETERMINATE", "reason": evidence.get("classification"), "observations": observations}
            if index + 1 < attempts:
                self.sleeper(interval_seconds)
        return {"status": "FAILED", "reason": "health_not_ready", "observations": observations}


class ExactBridgeRestartExecutor:
    """The sole Stage 17 mutating primitive."""

    def __init__(self, *, runner: Callable[..., Any] = subprocess.run) -> None:
        self.runner = runner

    def execute(self, operation: OperationSpec) -> dict[str, Any]:
        executor = operation.data["executor"]
        if (
            operation.operation_id != "bridge.restart.readiness_failure"
            or operation.version != 1
            or operation.target != BRIDGE_TARGET
            or executor["primitive"] != "restart_exact_bridge_user_service"
            or executor["fixed_executable"] != SYSTEMCTL
            or executor["fixed_argv"] != FIXED_RESTART_ARGV
        ):
            return {"status": "INDETERMINATE", "error": "executor_registry_binding_invalid", "executed": False, "mutated": False}
        try:
            result = self.runner(
                FIXED_RESTART_ARGV,
                capture_output=True,
                text=True,
                timeout=executor["timeout_seconds"],
                check=False,
                shell=False,
                env=minimal_user_systemd_environment(),
            )
        except subprocess.TimeoutExpired:
            return {"status": "INDETERMINATE", "error": "executor_timeout", "executed": True, "mutated": True}
        except PermissionError:
            return {"status": "FAILED_EXECUTION", "error": "executor_permission_denied", "executed": False, "mutated": False}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "INDETERMINATE", "error": "executor_observer_error", "error_type": type(exc).__name__, "executed": False, "mutated": False}
        try:
            returncode = int(result.returncode)
        except (AttributeError, TypeError, ValueError):
            return {"status": "INDETERMINATE", "error": "executor_output_malformed", "executed": True, "mutated": True}
        normalized = {
            "returncode": returncode,
            "stdout": bounded_text(getattr(result, "stdout", "")),
            "stderr": bounded_text(getattr(result, "stderr", "")),
        }
        if returncode != 0:
            return {"status": "FAILED_EXECUTION", "error": "executor_nonzero", **normalized, "executed": True, "mutated": True}
        return {"status": "EXECUTION_OK", **normalized, "executed": True, "mutated": True}
