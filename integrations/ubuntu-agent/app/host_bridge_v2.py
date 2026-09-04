#!/usr/bin/env python3

import json
import os
import subprocess
import socket
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn, UnixStreamServer
from pathlib import Path

ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aag_agent.diagnostics import PROFILES, diagnose_many
from aag_agent.maintenance import MAINTENANCE_TOOLS, dispatch as dispatch_maintenance
from aag_agent.context_memory.service import dispatch_context
from aag_agent.orchestration.contracts import (
    ContractError,
    MAX_RESPONSE_BYTES as MAX_ORCHESTRATION_RESPONSE_BYTES,
    error_response as orchestration_error_response,
)
from aag_agent.orchestration.service import build_orchestrator, dispatch_orchestration
from aag_agent.endpoints import (
    BRIDGE_API_VERSION,
    BRIDGE_CONTRACT_FILE_HOST,
    BRIDGE_CONTEXT_PATH,
    BRIDGE_DIAGNOSE_PATH,
    BRIDGE_HEALTH_PATH,
    BRIDGE_MAINTENANCE_PATH,
    BRIDGE_ORCHESTRATION_PATH,
    BRIDGE_SOCKET_HOST,
    public_contract,
)


SOCKET = BRIDGE_SOCKET_HOST
LIVE_TOOL = ROOT / "tools/live_audit.py"

ALLOWED_PROFILES = {
    "overview",
    "storage",
    "services",
    "docker",
    "network",
    "otzar",
}

MAX_RESPONSE_BYTES = 256_000
MAX_REQUEST_BYTES = 32_000
DIAGNOSTIC_SLOTS = threading.BoundedSemaphore(2)
MAINTENANCE_SLOTS = threading.BoundedSemaphore(1)
CONTEXT_SLOTS = threading.BoundedSemaphore(1)
ORCHESTRATION_SLOTS = threading.BoundedSemaphore(2)
ORCHESTRATION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aag-orchestrate")
ORCHESTRATION_TIMEOUT_SECONDS = 35.0
MAX_ORCHESTRATION_REQUEST_BYTES = 8192
_ORCHESTRATOR = None
_ORCHESTRATOR_LOCK = threading.Lock()


def dispatch_live_orchestration(payload):
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        with _ORCHESTRATOR_LOCK:
            if _ORCHESTRATOR is None:
                _ORCHESTRATOR = build_orchestrator()
    return dispatch_orchestration(payload, orchestrator=_ORCHESTRATOR)


class Server(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True
    request_queue_size = 16


class Handler(BaseHTTPRequestHandler):

    server_version = "AAGUbuntuBridge/1.0"

    def log_message(self, fmt, *args):
        # Keep normal HTTP noise out of stdout.
        return

    def send_json(self, code, obj, *, maximum_bytes=MAX_RESPONSE_BYTES):
        raw = json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        if len(raw) > maximum_bytes:
            raw = json.dumps(
                {
                    "error": "response_too_large",
                    "message": "Bridge output exceeded the route limit",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            code = 500

        self.send_response(code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(raw)),
        )
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == BRIDGE_HEALTH_PATH:
            self.send_json(
                200,
                {
                    "status": "ok",
                    "service": "aag-ubuntu-agent-bridge",
                    "mode": "READ_ONLY",
                    "api_version": BRIDGE_API_VERSION,
                    "profiles": sorted(ALLOWED_PROFILES),
                    "diagnostic_profiles": sorted(PROFILES),
                    "maintenance_tools": sorted(MAINTENANCE_TOOLS),
                    "orchestration_schema": "aag-governed-orchestration-request-v1",
                    "endpoint": public_contract(),
                },
            )
            return

        if parsed.path != "/audit":
            self.send_json(
                404,
                {"error": "not_found"},
            )
            return

        qs = urllib.parse.parse_qs(parsed.query)

        profile = qs.get("profile", [""])[0]

        if profile not in ALLOWED_PROFILES:
            self.send_json(
                400,
                {
                    "error": "profile_not_allowed",
                    "allowed": sorted(ALLOWED_PROFILES),
                },
            )
            return

        try:
            proc = subprocess.run(
                [str(LIVE_TOOL), profile],
                shell=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=70,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
            )
        except subprocess.TimeoutExpired:
            self.send_json(
                504,
                {
                    "error": "audit_timeout",
                    "profile": profile,
                },
            )
            return
        except Exception as exc:
            self.send_json(
                500,
                {
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
            )
            return

        if proc.returncode != 0:
            self.send_json(
                500,
                {
                    "error": "live_audit_failed",
                    "profile": profile,
                    "returncode": proc.returncode,
                    "stderr": proc.stderr[-5000:],
                },
            )
            return

        try:
            result = json.loads(proc.stdout)
        except Exception:
            self.send_json(
                500,
                {
                    "error": "invalid_live_audit_json",
                },
            )
            return

        self.send_json(
            200,
            result,
        )

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {
            BRIDGE_DIAGNOSE_PATH, BRIDGE_MAINTENANCE_PATH, BRIDGE_CONTEXT_PATH,
            BRIDGE_ORCHESTRATION_PATH,
        }:
            self.send_json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        maximum_request = MAX_ORCHESTRATION_REQUEST_BYTES if parsed.path == BRIDGE_ORCHESTRATION_PATH else MAX_REQUEST_BYTES
        if not 0 < length <= maximum_request:
            self.send_json(400, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "malformed_json"})
            return
        if parsed.path == BRIDGE_ORCHESTRATION_PATH:
            if not ORCHESTRATION_SLOTS.acquire(blocking=False):
                self.send_json(429, orchestration_error_response("orchestration_capacity_reached", status="UNAVAILABLE"))
                return
            release_slot = True
            try:
                future = ORCHESTRATION_EXECUTOR.submit(dispatch_live_orchestration, payload)
                try:
                    result = future.result(timeout=ORCHESTRATION_TIMEOUT_SECONDS)
                except FutureTimeoutError:
                    if not future.cancel():
                        # A running request retains its capacity slot until it
                        # really ends. Provider retries therefore cannot grow
                        # an unbounded executor queue after a client timeout.
                        release_slot = False
                        future.add_done_callback(lambda _completed: ORCHESTRATION_SLOTS.release())
                    self.send_json(504, orchestration_error_response("orchestration_timeout", status="INDETERMINATE"))
                    return
                except (ContractError, ValueError) as exc:
                    self.send_json(400, orchestration_error_response(str(exc), status="REJECTED"))
                    return
                except Exception:
                    self.send_json(503, orchestration_error_response("orchestration_backend_unavailable", status="UNAVAILABLE"))
                    return
            finally:
                if release_slot:
                    ORCHESTRATION_SLOTS.release()
            self.send_json(200, result, maximum_bytes=MAX_ORCHESTRATION_RESPONSE_BYTES)
            return
        if parsed.path == BRIDGE_DIAGNOSE_PATH:
            if not isinstance(payload, dict) or set(payload) != {"requests"}:
                self.send_json(400, {"error": "invalid_request_schema"})
                return
            if not DIAGNOSTIC_SLOTS.acquire(blocking=False):
                self.send_json(429, {"error": "diagnostic_capacity_reached"})
                return
            try:
                result = diagnose_many(payload["requests"])
            finally:
                DIAGNOSTIC_SLOTS.release()
            self.send_json(200 if result.get("status") != "ERROR" else 400, result)
            return

        if parsed.path == BRIDGE_CONTEXT_PATH:
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "invalid_context_request_schema"})
                return
            if not CONTEXT_SLOTS.acquire(blocking=False):
                self.send_json(429, {"error": "context_capacity_reached"})
                return
            try:
                try:
                    result = dispatch_context(payload)
                except ValueError as exc:
                    self.send_json(400, {
                        "schema": "aag-context-service-error-v1",
                        "status": "rejected",
                        "error": str(exc),
                        "read_only": True,
                        "mutated": False,
                        "execution_authority": "NONE",
                    })
                    return
                except Exception:
                    self.send_json(500, {
                        "schema": "aag-context-service-error-v1",
                        "status": "unavailable",
                        "error": "context_backend_unavailable",
                        "read_only": True,
                        "mutated": False,
                        "execution_authority": "NONE",
                    })
                    return
            finally:
                CONTEXT_SLOTS.release()
            self.send_json(200, result)
            return

        if (
            not isinstance(payload, dict)
            or set(payload) != {"tool", "arguments"}
            or payload.get("tool") not in MAINTENANCE_TOOLS
            or not isinstance(payload.get("arguments"), dict)
        ):
            self.send_json(400, {"error": "invalid_maintenance_request_schema"})
            return
        if not MAINTENANCE_SLOTS.acquire(blocking=False):
            self.send_json(429, {"error": "maintenance_capacity_reached"})
            return
        try:
            result = dispatch_maintenance(payload["tool"], payload["arguments"])
        finally:
            MAINTENANCE_SLOTS.release()
        code = 400 if result.get("completeness", {}).get("status") == "failed" else 200
        self.send_json(code, result)


def remove_stale_socket(path=SOCKET, connector=socket.socket):
    """Never unlink a reachable or indeterminate listener."""
    if not path.exists():
        return "absent"
    probe = connector(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        probe.connect(str(path))
    except ConnectionRefusedError:
        path.unlink()
        return "stale_removed"
    except (TimeoutError, PermissionError, OSError):
        raise RuntimeError("bridge_socket_exists_but_liveness_indeterminate")
    else:
        raise RuntimeError("bridge_listener_already_active")
    finally:
        probe.close()


def publish_endpoint_contract(path=BRIDGE_CONTRACT_FILE_HOST):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(public_contract(), sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(path)


def main():
    if not Path("/mnt/data").is_mount():
        raise SystemExit(
            "ERROR: /mnt/data is not mounted"
        )

    if not LIVE_TOOL.is_file():
        raise SystemExit(
            f"ERROR: missing {LIVE_TOOL}"
        )

    SOCKET.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    remove_stale_socket()

    server = Server(
        str(SOCKET),
        Handler,
    )

    # The socket contains READ-ONLY diagnostic access only.
    # It must be reachable from the AnythingLLM container,
    # which sees the same bind-mounted storage directory.
    os.chmod(SOCKET, 0o666)
    publish_endpoint_contract()

    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            SOCKET.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
