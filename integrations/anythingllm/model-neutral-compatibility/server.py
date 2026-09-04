#!/usr/bin/env python3
"""Shared ordinary-chat and tool-call compatibility boundary.

The proxy is intentionally outside AnythingLLM core and never executes a tool.
It validates a running model/server session, returns valid native calls unchanged,
or converts one schema-validated canonical adapter call to OpenAI format.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.cookies
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from composer_canonical import composer_canonical_json
from compatibility import (
    LAYER_VERSION,
    CompatibilityError,
    adapter_instruction,
    build_adapter_payload,
    build_candidate_adapter_payload,
    canonical_to_openai_response,
    composer_intent_from_message,
    composer_preview,
    composer_user_request_from_message,
    compose_request,
    extract_native_candidate,
    normalize_tools,
    normalize_candidate_with_trusted_arguments,
    normalize_composer_candidate,
    openai_sse_events,
    parse_canonical_call,
    schema_hash,
    sha256_text,
    stable_json,
    text_sanity,
    validate_native_tool_response,
    validate_composer_intent_call,
    validate_ordinary_response,
    validate_preserved_arguments,
    validate_required_arguments,
)
from visual_atlas import AtlasError, get_visual_atlas


MAX_CHAT_BODY = 4 * 1024 * 1024
MAX_COMPOSER_BODY = 24 * 1024 * 1024
DEFAULT_UPSTREAM = "http://127.0.0.1:8080"
DEFAULT_STATE_DIR = f"/run/user/{os.getuid()}/aag-llama/server"
DEFAULT_EXPECTED_EXECUTABLE = "/mnt/data/AI/Apps/llama.cpp/build-sycl/bin/llama-server"
DEFAULT_STORAGE_DIR = "/mnt/data/AI/Apps/AnythingLLM/storage/aag-model-neutral-compatibility"
DEFAULT_DB = "/mnt/data/AI/Apps/AnythingLLM/storage/anythingllm.db"
DEFAULT_ANYTHINGLLM_API = "http://127.0.0.1:3000/api"
SAFE_FORWARD_HEADERS = {"accept", "user-agent", "x-request-id"}


class BoundaryError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class ProbeResult:
    ok: bool
    capability: str
    code: str
    latency_ms: int
    output_sha256: str | None = None
    repair_count: int = 0


@dataclass
class ToolCapability:
    capability: str
    mode: str
    native_probe: ProbeResult
    adapter_probe: ProbeResult | None
    schema_sha256: str


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise RuntimeError("Unsafe audit directory symlink.")
        os.chmod(self.path.parent, 0o700)

    def write(self, event: str, **fields: Any) -> None:
        record = {
            "schema": "aag.model-neutral-compatibility.audit.v1",
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            **fields,
        }
        encoded = (stable_json(record) + "\n").encode("utf-8")
        with self.lock:
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


class RuntimeSession:
    def __init__(self, state_dir: Path, expected_executable: str = DEFAULT_EXPECTED_EXECUTABLE):
        self.state_dir = state_dir
        self.expected_executable = os.path.realpath(expected_executable)

    def _read(self, name: str) -> str:
        path = self.state_dir / name
        try:
            info = path.lstat()
        except (FileNotFoundError, PermissionError, OSError) as error:
            raise BoundaryError(503, "MODEL_RUNTIME_STATE_MISSING", "Managed model runtime state is unavailable.") from error
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or path.is_symlink():
            raise BoundaryError(503, "MODEL_RUNTIME_STATE_UNSAFE", "Managed model runtime state is unsafe.")
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _process_starttime(pid: int) -> str:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        remainder = raw.rsplit(") ", 1)[1].split()
        return remainder[19]

    @staticmethod
    def _process_uid(pid: int) -> int:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
        raise ValueError("Process UID is missing.")

    @staticmethod
    def _process_argv0(pid: int) -> str:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        argv0 = raw.split(b"\0", 1)[0].decode("utf-8")
        if not argv0:
            raise ValueError("Process argv[0] is missing.")
        return os.path.realpath(argv0)

    @staticmethod
    def _process_cgroup(pid: int) -> list[str]:
        return Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines()

    def _verify_managed_generation(self, pid: int) -> tuple[str, str]:
        """Verify optional systemd generation state without replacing PID start ticks."""
        unit_path = self.state_dir / "unit"
        invocation_path = self.state_dir / "invocation_id"
        if not unit_path.exists() and not invocation_path.exists():
            return "", ""
        unit = self._read("unit")
        invocation_id = self._read("invocation_id")
        if not re.fullmatch(r"aag-llama-server-runtime-[0-9]+-[0-9]+\.service", unit):
            raise BoundaryError(503, "MODEL_RUNTIME_UNIT_INVALID", "Managed runtime unit state is invalid.")
        if not re.fullmatch(r"[0-9a-f]{32}", invocation_id):
            raise BoundaryError(503, "MODEL_RUNTIME_GENERATION_INVALID", "Managed runtime generation state is invalid.")
        if not any(line.endswith("/" + unit) for line in self._process_cgroup(pid)):
            raise BoundaryError(503, "MODEL_RUNTIME_CGROUP_MISMATCH", "Managed runtime cgroup does not match state.")
        return unit, invocation_id

    def fingerprint(self) -> tuple[str, dict[str, str]]:
        try:
            pid_text = self._read("pid")
            starttime = self._read("starttime")
            model = self._read("model")
            executable = self._read("executable")
            context = self._read("context")
            profile = self._read("profile")
            pid = int(pid_text)
            if os.path.realpath(executable) != self.expected_executable:
                raise BoundaryError(503, "MODEL_RUNTIME_EXECUTABLE_STATE_INVALID", "Managed runtime executable state is invalid.")
            if self._process_starttime(pid) != starttime:
                raise BoundaryError(503, "MODEL_RUNTIME_STARTTIME_MISMATCH", "Managed model runtime state is stale.")
            if self._process_uid(pid) != os.getuid():
                raise BoundaryError(503, "MODEL_RUNTIME_UID_MISMATCH", "Managed runtime owner does not match.")
            if self._process_argv0(pid) != self.expected_executable:
                raise BoundaryError(503, "MODEL_RUNTIME_EXECUTABLE_MISMATCH", "Managed runtime executable does not match state.")
            unit, invocation_id = self._verify_managed_generation(pid)
        except BoundaryError:
            raise
        except FileNotFoundError as error:
            raise BoundaryError(503, "MODEL_RUNTIME_ATTESTATION_FILE_NOT_FOUND", "No verified managed model runtime is available.") from error
        except PermissionError as error:
            raise BoundaryError(503, "MODEL_RUNTIME_ATTESTATION_PERMISSION_DENIED", "No verified managed model runtime is available.") from error
        except ValueError as error:
            raise BoundaryError(503, "MODEL_RUNTIME_ATTESTATION_VALUE_INVALID", "No verified managed model runtime is available.") from error
        except IndexError as error:
            raise BoundaryError(503, "MODEL_RUNTIME_ATTESTATION_PROC_INVALID", "No verified managed model runtime is available.") from error
        except OSError as error:
            raise BoundaryError(503, "MODEL_RUNTIME_ATTESTATION_OS_ERROR", "No verified managed model runtime is available.") from error
        material = {
            "pid": pid_text,
            "starttime": starttime,
            "model_sha256": sha256_text(os.path.realpath(model)),
            "executable_sha256": sha256_text(executable),
            "context": context,
            "profile": profile,
            "layer": LAYER_VERSION,
        }
        if unit:
            material["unit"] = unit
            material["invocation_id"] = invocation_id
        return sha256_text(stable_json(material)), material


class Upstream:
    def __init__(self, base_url: str, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        request_headers = {"User-Agent": f"AAG-Compatibility/{LAYER_VERSION}"}
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as error:
            raise BoundaryError(503, "MODEL_RUNTIME_UNAVAILABLE", "The local inference runtime is unavailable.") from error

    def json(self, payload: dict[str, Any], *, timeout: int | None = None) -> dict[str, Any]:
        status, _, raw = self.request(
            "POST",
            "/v1/chat/completions",
            body=stable_json(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=timeout,
        )
        if status < 200 or status >= 300:
            raise BoundaryError(502, "UPSTREAM_COMPLETION_FAILED", f"The inference runtime returned HTTP {status}.")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BoundaryError(502, "UPSTREAM_RESPONSE_INVALID", "The inference runtime returned malformed JSON.") from error
        if not isinstance(value, dict):
            raise BoundaryError(502, "UPSTREAM_RESPONSE_INVALID", "The inference runtime returned an invalid object.")
        return value

    def healthy(self) -> bool:
        try:
            status, _, _ = self.request("GET", "/health", timeout=3)
            return status == 200
        except BoundaryError:
            return False


class CapabilityManager:
    def __init__(self, upstream: Upstream, runtime: RuntimeSession, audit: AuditLog):
        self.upstream = upstream
        self.runtime = runtime
        self.audit = audit
        self.lock = threading.Lock()
        self.session_sha256: str | None = None
        self.session_meta: dict[str, str] = {}
        self.basic: ProbeResult | None = None
        self.chat: ProbeResult | None = None
        self.tools: dict[str, ToolCapability] = {}

    def _switch_session(self, session_sha256: str, meta: dict[str, str]) -> None:
        if session_sha256 == self.session_sha256:
            return
        previous = self.session_sha256
        self.session_sha256 = session_sha256
        self.session_meta = meta
        self.basic = None
        self.chat = None
        self.tools.clear()
        self.audit.write(
            "cache_invalidated",
            reason="server_session_changed" if previous else "layer_start_or_first_session",
            previous_session_sha256=previous,
            session_sha256=session_sha256,
        )

    @staticmethod
    def _probe_model(request_payload: dict[str, Any]) -> str:
        value = request_payload.get("model")
        return value if isinstance(value, str) and value else "local-model"

    def _basic_probe(self, model: str) -> ProbeResult:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "שלום\nBasic model text sanity check. Reply briefly with normal human-readable text.",
                }
            ],
            "temperature": 0,
            "max_tokens": 48,
            "stream": False,
        }
        started = time.monotonic()
        try:
            response = self.upstream.json(payload, timeout=120)
            validate_ordinary_response(response)
            text = response["choices"][0]["message"].get("content") or ""
            sanity = text_sanity(text)
            if not sanity.ok:
                raise CompatibilityError(sanity.code, "Basic model text sanity probe failed.")
            result = ProbeResult(True, "BASIC_TEXT_OK", "SANE_TEXT", round((time.monotonic() - started) * 1000), sanity.sha256)
        except (CompatibilityError, BoundaryError) as error:
            code = error.code
            result = ProbeResult(False, "BASIC_TEXT_INCOMPATIBLE", code, round((time.monotonic() - started) * 1000))
        self.audit.write("basic_text_sanity_probe", session_sha256=self.session_sha256, **asdict(result))
        return result

    def _chat_probe(self, model: str) -> ProbeResult:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a normal chat assistant. Return only normal user-facing text and never raw control tokens.",
                },
                {
                    "role": "user",
                    "content": "Ordinary multi-role chat compatibility check. Briefly confirm that chat works.",
                },
            ],
            "temperature": 0,
            "max_tokens": 48,
            "stream": False,
        }
        started = time.monotonic()
        try:
            response = self.upstream.json(payload, timeout=120)
            validate_ordinary_response(response)
            text = response["choices"][0]["message"].get("content") or ""
            sanity = text_sanity(text)
            if not sanity.ok:
                raise CompatibilityError(sanity.code, "Ordinary chat compatibility probe failed.")
            result = ProbeResult(True, "CHAT_OK", "ORDINARY_CHAT_VALID", round((time.monotonic() - started) * 1000), sanity.sha256)
        except (CompatibilityError, BoundaryError) as error:
            result = ProbeResult(False, "CHAT_INCOMPATIBLE", error.code, round((time.monotonic() - started) * 1000))
        self.audit.write("ordinary_chat_probe", session_sha256=self.session_sha256, **asdict(result))
        return result

    @staticmethod
    def _probe_tool(nonce: str) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "aag_compatibility_noop",
                "description": "Return the supplied nonce for a side-effect-free compatibility probe.",
                "parameters": {
                    "type": "object",
                    "properties": {"nonce": {"type": "string", "const": nonce}},
                    "required": ["nonce"],
                    "additionalProperties": False,
                },
            },
        }

    def _native_probe(self, model: str) -> ProbeResult:
        nonce = secrets.token_hex(8)
        tools = [self._probe_tool(nonce)]
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": f"Call aag_compatibility_noop with nonce {nonce}."}],
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "temperature": 0,
            "max_tokens": 128,
            "stream": False,
        }
        started = time.monotonic()
        try:
            response = self.upstream.json(payload, timeout=120)
            calls = validate_native_tool_response(response, tools)
            if calls[0].arguments != {"nonce": nonce}:
                raise CompatibilityError("PROBE_NONCE_MISMATCH", "Native probe nonce changed.")
            result = ProbeResult(True, "NATIVE_TOOLS", "NATIVE_TOOL_CALL_VALID", round((time.monotonic() - started) * 1000))
        except (CompatibilityError, BoundaryError) as error:
            result = ProbeResult(False, "NATIVE_TOOLS_UNAVAILABLE", error.code, round((time.monotonic() - started) * 1000))
        self.audit.write("native_tool_probe", session_sha256=self.session_sha256, **asdict(result))
        return result

    def _adapter_probe(self, model: str) -> ProbeResult:
        nonce = secrets.token_hex(8)
        tools = [self._probe_tool(nonce)]
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": f"Call aag_compatibility_noop with nonce {nonce}."}],
            "tools": tools,
            "temperature": 0,
            "max_tokens": 128,
            "stream": False,
        }
        started = time.monotonic()
        try:
            response = self.upstream.json(build_adapter_payload(payload, tools), timeout=120)
            validate_ordinary_response(response)
            text = response["choices"][0]["message"].get("content")
            if not isinstance(text, str):
                raise CompatibilityError("CANONICAL_JSON_INVALID", "Adapter probe did not return text.")
            call = parse_canonical_call(text, tools)
            if call.arguments != {"nonce": nonce}:
                raise CompatibilityError("PROBE_NONCE_MISMATCH", "Adapter probe nonce changed.")
            result = ProbeResult(
                True,
                "GENERIC_ADAPTER_TOOLS",
                "CANONICAL_TOOL_CALL_VALID",
                round((time.monotonic() - started) * 1000),
                sha256_text(text),
                call.repair_count,
            )
        except (CompatibilityError, BoundaryError) as error:
            result = ProbeResult(False, "GENERIC_ADAPTER_UNAVAILABLE", error.code, round((time.monotonic() - started) * 1000))
        self.audit.write("generic_adapter_probe", session_sha256=self.session_sha256, **asdict(result))
        return result

    def ensure_basic(self, request_payload: dict[str, Any]) -> ProbeResult:
        session, meta = self.runtime.fingerprint()
        with self.lock:
            self._switch_session(session, meta)
            if self.basic is None:
                self.basic = self._basic_probe(self._probe_model(request_payload))
            return self.basic

    def ensure_chat(self, request_payload: dict[str, Any]) -> ProbeResult:
        current_basic = self.ensure_basic(request_payload)
        if not current_basic.ok:
            with self.lock:
                if self.chat is None:
                    self.chat = ProbeResult(False, "CHAT_INCOMPATIBLE", "BASIC_TEXT_GATE_FAILED", 0)
                    self.audit.write(
                        "ordinary_chat_probe_blocked",
                        session_sha256=self.session_sha256,
                        basic_code=current_basic.code,
                        **asdict(self.chat),
                    )
                return self.chat
        with self.lock:
            if self.chat is None:
                self.chat = self._chat_probe(self._probe_model(request_payload))
            return self.chat

    def ensure_tools(self, request_payload: dict[str, Any], tools: list[dict[str, Any]]) -> ToolCapability:
        normalized = normalize_tools(tools)
        current_chat = self.ensure_chat(request_payload)
        if not current_chat.ok:
            raise BoundaryError(422, "CHAT_INCOMPATIBLE", "Tool capability is not evaluated for an invalid text runtime.")
        digest = schema_hash(normalized)
        with self.lock:
            if digest in self.tools:
                return self.tools[digest]
            model = self._probe_model(request_payload)
            native = self._native_probe(model)
            if native.ok:
                result = ToolCapability("NATIVE_TOOLS", "NATIVE", native, None, digest)
            else:
                adapter = self._adapter_probe(model)
                result = ToolCapability(
                    "GENERIC_ADAPTER_TOOLS" if adapter.ok else "TOOL_INCOMPATIBLE",
                    "GENERIC_ADAPTER" if adapter.ok else "INCOMPATIBLE",
                    native,
                    adapter,
                    digest,
                )
            self.tools[digest] = result
            self.audit.write(
                "tool_capability_classified",
                session_sha256=self.session_sha256,
                schema_sha256=digest,
                capability=result.capability,
                mode=result.mode,
            )
            return result

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "layer_version": LAYER_VERSION,
                "session_sha256": self.session_sha256,
                "session": self.session_meta,
                "basic_model_text_sanity": asdict(self.basic) if self.basic else None,
                "chat": asdict(self.chat) if self.chat else None,
                "tools": {key: asdict(value) for key, value in self.tools.items()},
            }


class ComposerSessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: dict[str, dict[str, Any]] = {}

    def issue(self) -> tuple[str, str]:
        session = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self.lock:
            self._prune()
            self.sessions[session] = {
                "csrf": csrf,
                "expires": time.time() + 3600,
                "thread_slug": None,
                "artifact_available": False,
            }
        return session, csrf

    def resume_or_issue(self, session: str | None) -> tuple[str, str, bool, bool]:
        with self.lock:
            self._prune()
            entry = self.sessions.get(session or "")
            if entry:
                entry["expires"] = time.time() + 3600
                return session or "", entry["csrf"], False, bool(entry["artifact_available"])
        issued, csrf = self.issue()
        return issued, csrf, True, False

    def validate(self, session: str | None, csrf: str | None) -> bool:
        if not session or not csrf:
            return False
        with self.lock:
            self._prune()
            expected = self.sessions.get(session)
            return bool(expected and hmac.compare_digest(expected["csrf"], csrf))

    def thread(self, session: str) -> str | None:
        with self.lock:
            self._prune()
            entry = self.sessions.get(session)
            return entry.get("thread_slug") if entry else None

    def bind_thread(self, session: str, thread_slug: str) -> None:
        with self.lock:
            self._prune()
            entry = self.sessions.get(session)
            if not entry:
                raise BoundaryError(403, "COMPOSER_SESSION_EXPIRED", "Composer session expired.")
            current = entry.get("thread_slug")
            if current not in {None, thread_slug}:
                raise BoundaryError(409, "COMPOSER_THREAD_CONFLICT", "Composer conversation scope changed unexpectedly.")
            entry["thread_slug"] = thread_slug

    def artifact_available(self, session: str) -> bool:
        with self.lock:
            self._prune()
            entry = self.sessions.get(session)
            return bool(entry and entry.get("artifact_available"))

    def mark_artifact(self, session: str) -> None:
        with self.lock:
            self._prune()
            entry = self.sessions.get(session)
            if entry:
                entry["artifact_available"] = True

    def _prune(self) -> None:
        now = time.time()
        self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] >= now}


class Application:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        storage = Path(args.storage)
        self.audit = AuditLog(storage / "audit.ndjson")
        self.upstream = Upstream(args.upstream, args.upstream_timeout)
        self.capabilities = CapabilityManager(
            self.upstream,
            RuntimeSession(Path(args.state_dir), args.expected_executable),
            self.audit,
        )
        self.composer_sessions = ComposerSessions()
        self.auth_token = self._read_secret(Path(args.token_file))
        self.assets = Path(__file__).resolve().parent / "composer"

    @staticmethod
    def _read_secret(path: Path) -> str:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Compatibility token file must be an owner-only regular file.")
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < 32:
            raise RuntimeError("Compatibility token is too short.")
        return value

    def authorized(self, header: str | None) -> bool:
        prefix = "Bearer "
        return bool(header and header.startswith(prefix) and hmac.compare_digest(header[len(prefix) :], self.auth_token))

    def anythingllm_key(self) -> str:
        database = Path(self.args.anythingllm_db)
        info = database.lstat()
        if not stat.S_ISREG(info.st_mode) or database.is_symlink():
            raise BoundaryError(503, "COMPOSER_AUTH_UNAVAILABLE", "AnythingLLM API authentication is unavailable.")
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=3)
        try:
            row = connection.execute("select secret from api_keys where secret is not null order by id limit 1").fetchone()
        finally:
            connection.close()
        if not row or not isinstance(row[0], str) or not row[0]:
            raise BoundaryError(503, "COMPOSER_AUTH_UNAVAILABLE", "AnythingLLM API authentication is unavailable.")
        return row[0]

    def _anythingllm_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = stable_json(payload).encode("utf-8")
        request = urllib.request.Request(
            self.args.anythingllm_api.rstrip("/") + path,
            data=body,
            headers={
                "Authorization": "Bearer " + self.anythingllm_key(),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"AAG-Composer/{LAYER_VERSION}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.args.composer_timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as error:
            raise BoundaryError(503, "COMPOSER_SUBMISSION_UNAVAILABLE", "AnythingLLM is unavailable.") from error
        if status < 200 or status >= 300:
            raise BoundaryError(502, "COMPOSER_SUBMISSION_FAILED", f"AnythingLLM returned HTTP {status}.")
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BoundaryError(502, "COMPOSER_RESPONSE_INVALID", "AnythingLLM returned invalid JSON.") from error
        if not isinstance(result, dict):
            raise BoundaryError(502, "COMPOSER_RESPONSE_INVALID", "AnythingLLM returned an invalid response object.")
        return result

    def _sign_composer_message(self, message: str) -> str:
        intent = composer_intent_from_message(message)
        user_request = composer_user_request_from_message(message)
        if intent.get("user_request_sha256") != hashlib.sha256(user_request.encode("utf-8")).hexdigest():
            raise CompatibilityError("COMPOSER_INTENT_INVALID", "Composer user request binding is invalid.")
        signature = hmac.new(
            self.auth_token.encode("utf-8"), composer_canonical_json(intent).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return message + "\nAAG_COMPOSER_INTENT_SIGNATURE_V1=" + signature

    def _pack_composer_transport(self, message: str) -> str:
        encoded = message.encode("utf-8").hex()
        signature = hmac.new(self.auth_token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return "AAG_COMPOSER_OPAQUE_V1=" + encoded + "\nAAG_COMPOSER_AUTH_V1=" + signature

    def unpack_composer_transport(self, payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return payload
        unpacked = dict(payload)
        unpacked_messages = [dict(item) if isinstance(item, dict) else item for item in messages]
        for index in range(len(unpacked_messages) - 1, -1, -1):
            item = unpacked_messages[index]
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            content = item.get("content")
            candidates: list[tuple[int | None, str]] = []
            if isinstance(content, str):
                candidates.append((None, content))
            elif isinstance(content, list):
                for part_index, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        candidates.append((part_index, part["text"]))
            marked = [
                (part_index, text)
                for part_index, text in candidates
                if "AAG_COMPOSER_OPAQUE_V1=" in text or "AAG_COMPOSER_AUTH_V1=" in text
            ]
            if not marked:
                continue
            if len(marked) != 1:
                raise CompatibilityError("COMPOSER_TRANSPORT_INVALID", "Composer opaque transport is ambiguous.")
            part_index, content_text = marked[0]
            match = re.fullmatch(
                r"AAG_COMPOSER_OPAQUE_V1=([0-9a-f]{2,48000})\nAAG_COMPOSER_AUTH_V1=([0-9a-f]{64})",
                content_text,
            )
            if not match:
                raise CompatibilityError("COMPOSER_TRANSPORT_INVALID", "Composer opaque transport is malformed.")
            encoded, supplied = match.groups()
            expected = hmac.new(self.auth_token.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, supplied):
                raise CompatibilityError("COMPOSER_TRANSPORT_INVALID", "Composer opaque transport authentication failed.")
            try:
                decoded = bytes.fromhex(encoded).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as error:
                raise CompatibilityError("COMPOSER_TRANSPORT_INVALID", "Composer opaque transport cannot be decoded.") from error
            if part_index is None:
                item["content"] = decoded
            else:
                parts = [dict(part) if isinstance(part, dict) else part for part in content]
                parts[part_index]["text"] = decoded
                item["content"] = parts
            unpacked["messages"] = unpacked_messages
            return unpacked
        return payload

    def trusted_composer_intent(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return None
        for item in reversed(messages):
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            raw_content = item.get("content")
            if isinstance(raw_content, str):
                content = raw_content
            elif isinstance(raw_content, list):
                text_parts = [
                    part["text"]
                    for part in raw_content
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                ]
                content = "\n".join(text_parts)
            else:
                continue
            marker_present = "AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1=" in content
            signature_matches = re.findall(r"^AAG_COMPOSER_INTENT_SIGNATURE_V1=([0-9a-f]{64})$", content, re.MULTILINE)
            if not marker_present and not signature_matches:
                continue
            if not marker_present or len(signature_matches) != 1:
                raise CompatibilityError("COMPOSER_INTENT_SIGNATURE_INVALID", "Composer intent signature is missing or ambiguous.")
            intent = composer_intent_from_message(content)
            expected = hmac.new(
                self.auth_token.encode("utf-8"), composer_canonical_json(intent).encode("utf-8"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, signature_matches[0]):
                raise CompatibilityError("COMPOSER_INTENT_SIGNATURE_INVALID", "Composer intent signature is invalid.")
            user_request = composer_user_request_from_message(content)
            if intent.get("user_request_sha256") != hashlib.sha256(user_request.encode("utf-8")).hexdigest():
                raise CompatibilityError(
                    "COMPOSER_INTENT_SIGNATURE_INVALID",
                    "Composer user request does not match its signed intent.",
                )
            return intent
        return None

    def prepare_composer(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate and sign Advanced intent without submitting a chat/job."""
        if data.get("mode") != "advanced":
            raise CompatibilityError("COMPOSER_INPUT_INVALID", "Native Composer prepare requires Advanced mode.")
        message, attachments = compose_request(data)
        signed = self._sign_composer_message(message)
        intent = composer_intent_from_message(signed)
        self.audit.write(
            "composer_native_prepare",
            operation=intent.get("operation"),
            attachment_count=len(attachments),
            request_sha256=intent.get("user_request_sha256"),
            explicit_fields=sorted(intent.get("semantics", {}).get("explicit_constraints", {})),
            discretion_fields=intent.get("semantics", {}).get("model_discretion_fields", []),
            atlas=intent.get("knowledge_modules", {}).get("visual_atlas", {"used": False, "mode": "auto"}),
        )
        return {
            "modelMessage": signed,
            "attachmentCount": len(attachments),
            "requestSha256": intent["user_request_sha256"],
        }

    def submit_composer(self, data: dict[str, Any], session: str) -> dict[str, Any]:
        message, attachments = compose_request(data)
        if data.get("mode") == "advanced":
            message = self._sign_composer_message(message)
            # The non-core AAG ordinary-command integration defers signed
            # Composer envelopes to this boundary. Keep the original readable
            # request intact so AAG_INVOCATION_PROMPT and the preserved
            # semantic-fidelity gate receive the same authoritative content.
        # The workspace-only public endpoint intentionally has no trusted
        # conversation object. Create a normal AnythingLLM workspace thread so
        # the unchanged AAG owner-scope gate receives the same server-owned
        # thread identity as the ordinary UI workflow.
        thread_slug = self.composer_sessions.thread(session)
        if data.get("source_policy") == "previous_artifact" and not self.composer_sessions.artifact_available(session):
            raise BoundaryError(409, "COMPOSER_PREVIOUS_ARTIFACT_UNAVAILABLE", "No completed Composer image is available in this session.")
        if thread_slug is None:
            thread_result = self._anythingllm_json(
                "/v1/workspace/image-generator/thread/new",
                {"name": "AAG Image Composer " + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())},
            )
            thread = thread_result.get("thread")
            thread_slug = thread.get("slug") if isinstance(thread, dict) else None
            if not isinstance(thread_slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_slug):
                raise BoundaryError(502, "COMPOSER_THREAD_INVALID", "AnythingLLM did not return a valid conversation scope.")
            self.composer_sessions.bind_thread(session, thread_slug)
        payload = {
            "message": message,
            "mode": "automatic",
            "attachments": attachments,
            "reset": False,
        }
        started = time.monotonic()
        result = self._anythingllm_json(
            "/v1/workspace/image-generator/thread/"
            + urllib.parse.quote(thread_slug, safe="")
            + "/chat",
            payload,
        )
        text_response = result.get("textResponse") if isinstance(result, dict) else None
        completed = (
            not bool(result.get("error"))
            and isinstance(text_response, str)
            and re.search(r"(?m)^status=completed$", text_response) is not None
        )
        self.audit.write(
            "composer_submission",
            mode=data.get("mode"),
            operation=data.get("operation", "auto"),
            attachment_count=len(attachments),
            request_sha256=sha256_text(message),
            thread_sha256=sha256_text(thread_slug),
            latency_ms=round((time.monotonic() - started) * 1000),
            success=completed,
        )
        if completed:
            self.composer_sessions.mark_artifact(session)
        result["composerWorkspaceUrl"] = "http://127.0.0.1:3000/workspace/image-generator/thread/" + thread_slug
        result["previousArtifactAvailable"] = self.composer_sessions.artifact_available(session)
        return result


APP: Application


class BoundaryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AAGCompatibility/1"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        APP.audit.write(
            "http_access",
            client=self.client_address[0],
            method=self.command,
            path=self.path.split("?", 1)[0],
            status=args[1] if len(args) > 1 else None,
        )

    def _json(self, status: int, value: Any, *, headers: dict[str, str] | None = None) -> None:
        body = stable_json(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if headers:
            for key, header_value in headers.items():
                self.send_header(key, header_value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, error: BoundaryError | CompatibilityError) -> None:
        if isinstance(error, BoundaryError):
            status, code = error.status, error.code
        else:
            status, code = 422, error.code
        APP.audit.write("request_rejected", code=code, status=status, path=self.path.split("?", 1)[0])
        self._json(
            status,
            {
                "error": {
                    "message": str(error),
                    "type": "aag_compatibility_error",
                    "code": code,
                }
            },
        )

    def _read_json(self, limit: int) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as error:
            raise BoundaryError(400, "INVALID_CONTENT_LENGTH", "Content-Length is invalid.") from error
        if length <= 0 or length > limit:
            raise BoundaryError(413 if length > limit else 400, "REQUEST_SIZE_INVALID", "Request body size is invalid.")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BoundaryError(400, "REQUEST_JSON_INVALID", "Request body is not valid JSON.") from error
        if not isinstance(value, dict):
            raise BoundaryError(400, "REQUEST_JSON_INVALID", "Request JSON must be an object.")
        return value

    def _require_api_auth(self) -> None:
        if not APP.authorized(self.headers.get("Authorization")):
            raise BoundaryError(401, "AUTHENTICATION_REQUIRED", "A valid local compatibility token is required.")

    def _require_composer_local(self) -> None:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            raise BoundaryError(403, "COMPOSER_LOOPBACK_ONLY", "Composer is available only on loopback.")

    def _composer_origin_ok(self) -> bool:
        origin = self.headers.get("Origin") or ""
        referer = self.headers.get("Referer") or ""
        allowed = {
            f"http://127.0.0.1:{APP.args.port}",
            f"http://localhost:{APP.args.port}",
        }
        return origin in allowed and any(referer.startswith(value + "/") for value in allowed)

    def _composer_cookie(self) -> str | None:
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("aag_composer_session")
        return morsel.value if morsel else None

    def _asset(self, name: str, content_type: str) -> None:
        path = APP.assets / name
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                raise FileNotFoundError
            body = path.read_bytes()
        except (FileNotFoundError, OSError):
            raise BoundaryError(404, "NOT_FOUND", "Resource not found.")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _atlas_asset(self, family_id: str, subfamily_id: str, kind: str) -> None:
        try:
            file, content_type = get_visual_atlas().asset(family_id, subfamily_id, kind)
            info = file.lstat()
            if not stat.S_ISREG(info.st_mode) or file.is_symlink():
                raise FileNotFoundError
            body = file.read_bytes()
        except (AtlasError, FileNotFoundError, OSError) as error:
            raise BoundaryError(404, "ATLAS_ASSET_NOT_FOUND", "Visual Atlas asset not found.") from error
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                self._json(200, {"status": "ok", "layer": LAYER_VERSION})
                return
            if path == "/ready":
                status = 200 if APP.upstream.healthy() else 503
                self._json(status, {"status": "ready" if status == 200 else "upstream-unavailable", "layer": LAYER_VERSION})
                return
            if path == "/composer" or path == "/composer/":
                self._require_composer_local()
                self._asset("index.html", "text/html; charset=utf-8")
                return
            if path == "/composer/app.js":
                self._require_composer_local()
                self._asset("app.js", "application/javascript; charset=utf-8")
                return
            if path == "/composer/app.css":
                self._require_composer_local()
                self._asset("app.css", "text/css; charset=utf-8")
                return
            if path == "/composer/visual-taxonomy.json":
                self._require_composer_local()
                try:
                    catalog = get_visual_atlas().catalog()
                except AtlasError as error:
                    raise BoundaryError(503, "ATLAS_UNAVAILABLE", "Visual Atlas catalog is unavailable.") from error
                self._json(200, catalog)
                return
            atlas_match = re.fullmatch(
                r"/composer/atlas/(thumbnail|preview)/([a-z0-9]+(?:-[a-z0-9]+)*)/([a-z0-9]+(?:-[a-z0-9]+)*)",
                path,
            )
            if atlas_match:
                self._require_composer_local()
                self._atlas_asset(atlas_match.group(2), atlas_match.group(3), atlas_match.group(1))
                return
            if path == "/composer/session":
                self._require_composer_local()
                session, csrf, _, artifact_available = APP.composer_sessions.resume_or_issue(self._composer_cookie())
                cookie = f"aag_composer_session={session}; HttpOnly; SameSite=Strict; Path=/composer; Max-Age=3600"
                self._json(
                    200,
                    {"csrf": csrf, "previousArtifactAvailable": artifact_available},
                    headers={"Set-Cookie": cookie},
                )
                return
            if path == "/aag/capabilities":
                self._require_api_auth()
                self._json(200, APP.capabilities.snapshot())
                return
            if path.startswith("/v1/"):
                self._require_api_auth()
                self._forward()
                return
            raise BoundaryError(404, "NOT_FOUND", "Resource not found.")
        except (BoundaryError, CompatibilityError) as error:
            self._error(error)

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/aag/preflight":
                self._require_api_auth()
                data = self._read_json(16_384)
                if not isinstance(data.get("model"), str) or not data["model"]:
                    raise BoundaryError(400, "PREFLIGHT_REQUEST_INVALID", "A model alias is required for preflight.")
                basic = APP.capabilities.ensure_basic(data)
                if not basic.ok:
                    raise BoundaryError(
                        422,
                        "MODEL_INCOMPATIBLE_WITH_AAG_TEXT_CONTRACT",
                        f"Basic model/text sanity gate failed: {basic.code}.",
                    )
                chat = APP.capabilities.ensure_chat(data)
                if not chat.ok:
                    raise BoundaryError(
                        422,
                        "MODEL_INCOMPATIBLE_WITH_AAG_CHAT_CONTRACT",
                        f"Ordinary chat compatibility gate failed: {chat.code}.",
                    )
                self._json(
                    200,
                    {
                        "status": "PASS",
                        "basic_model_text_sanity": basic.capability,
                        "basic_code": basic.code,
                        "ordinary_chat_compatibility": chat.capability,
                        "chat_capability": chat.capability,
                        "chat_code": chat.code,
                    },
                )
                return
            if path in {"/composer/preview", "/composer/prepare", "/composer/submit"}:
                self._require_composer_local()
                if not self._composer_origin_ok():
                    raise BoundaryError(403, "COMPOSER_ORIGIN_INVALID", "Composer origin validation failed.")
                if not APP.composer_sessions.validate(self._composer_cookie(), self.headers.get("X-AAG-CSRF")):
                    raise BoundaryError(403, "COMPOSER_CSRF_INVALID", "Composer CSRF validation failed.")
                data = self._read_json(MAX_COMPOSER_BODY)
                if path.endswith("preview"):
                    preview = composer_preview(data)
                    self._json(200, {"summary": preview, "attachment_count": len(data.get("attachments", []))})
                elif path.endswith("prepare"):
                    self._json(200, APP.prepare_composer(data))
                else:
                    session = self._composer_cookie()
                    if session is None:
                        raise BoundaryError(403, "COMPOSER_SESSION_EXPIRED", "Composer session expired.")
                    self._json(200, APP.submit_composer(data, session))
                return
            if path == "/v1/chat/completions":
                self._require_api_auth()
                self._chat_completion()
                return
            if path.startswith("/v1/"):
                self._require_api_auth()
                self._forward()
                return
            raise BoundaryError(404, "NOT_FOUND", "Resource not found.")
        except (BoundaryError, CompatibilityError) as error:
            self._error(error)

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > MAX_CHAT_BODY:
            raise BoundaryError(413, "REQUEST_SIZE_INVALID", "Forwarded request is too large.")
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() in SAFE_FORWARD_HEADERS
        }
        if body is not None:
            headers["Content-Type"] = self.headers.get("Content-Type", "application/json")
        status, response_headers, response_body = APP.upstream.request(self.command, self.path, body=body, headers=headers)
        self.send_response(status)
        self.send_header("Content-Type", response_headers.get("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(response_body)

    def _chat_completion(self) -> None:
        payload = self._read_json(MAX_CHAT_BODY)
        payload = APP.unpack_composer_transport(payload)
        messages = payload.get("messages")
        if not isinstance(payload.get("model"), str) or not isinstance(messages, list) or not messages:
            raise BoundaryError(400, "CHAT_REQUEST_INVALID", "model and non-empty messages are required.")
        stream_requested = payload.get("stream") is True
        composer_intent = APP.trusted_composer_intent(payload)
        basic = APP.capabilities.ensure_basic(payload)
        if not basic.ok:
            raise BoundaryError(
                422,
                "MODEL_INCOMPATIBLE_WITH_AAG_TEXT_CONTRACT",
                f"Basic model/text sanity gate failed: {basic.code}.",
            )
        chat = APP.capabilities.ensure_chat(payload)
        if not chat.ok:
            raise BoundaryError(
                422,
                "MODEL_INCOMPATIBLE_WITH_AAG_CHAT_CONTRACT",
                f"Ordinary chat compatibility gate failed: {chat.code}.",
            )

        upstream_payload = dict(payload)
        upstream_payload["stream"] = False
        tools = payload.get("tools")
        mode = "CHAT_ONLY"
        if tools is not None:
            normalized = normalize_tools(tools)
            capability = APP.capabilities.ensure_tools(payload, normalized)
            mode = capability.capability
            if mode == "NATIVE_TOOLS":
                try:
                    response = APP.upstream.json(upstream_payload)
                    validate_ordinary_response(response)
                    message = response["choices"][0]["message"]
                    if message.get("tool_calls"):
                        if composer_intent is not None:
                            candidate = extract_native_candidate(response, normalized)
                            call = normalize_composer_candidate(candidate, normalized, composer_intent)
                            response = canonical_to_openai_response(response, call)
                            mode = "NATIVE_CREATIVE_WITH_DETERMINISTIC_TRUSTED_INTENT"
                            APP.audit.write(
                                "composer_native_creative_normalized",
                                session_sha256=APP.capabilities.session_sha256,
                                tool_name=call.tool_name,
                                prompt_count=len(call.arguments.get("items", [])) if call.tool_name == "aag-image-batch" else 1,
                            )
                        else:
                            validate_native_tool_response(response, normalized)
                    elif composer_intent is not None:
                        raise CompatibilityError("COMPOSER_INTENT_MISMATCH", "Advanced Composer request did not produce a tool call.")
                except CompatibilityError as native_error:
                    # A synthetic native probe cannot cover every real schema.
                    # Retry once through the same generic canonical adapter only
                    # for pre-execution protocol/schema rejection. A trusted
                    # Advanced mismatch is an authoritative fail-closed result.
                    if native_error.code.startswith("COMPOSER_INTENT_"):
                        raise
                    APP.audit.write(
                        "native_request_rejected",
                        session_sha256=APP.capabilities.session_sha256,
                        code=native_error.code,
                        fallback="GENERIC_ADAPTER_TOOLS",
                    )
                    candidate = None
                    preserved: dict[str, Any] = {}
                    required: dict[str, Any] = {}
                    if native_error.code == "ARGUMENT_SCHEMA_MISMATCH":
                        candidate = extract_native_candidate(response, normalized)
                        prompt = candidate.arguments.get("prompt")
                        if isinstance(prompt, str):
                            preserved["prompt"] = prompt
                        if composer_intent is not None and candidate.tool_name == "aag-image-task":
                            for field in ("operation", "quality", "source_policy", "preservation"):
                                if field in composer_intent:
                                    required[field] = composer_intent[field]
                            if composer_intent.get("aspect_ratio") != "auto":
                                required["aspect_ratio"] = composer_intent["aspect_ratio"]
                            if composer_intent.get("count") != 1:
                                required["count"] = composer_intent["count"]
                            if composer_intent.get("operation") == "upscale":
                                required["scale"] = composer_intent["scale"]
                    deterministic_call = None
                    if candidate is not None and composer_intent is not None and candidate.tool_name == "aag-image-task":
                        omitted = set()
                        if composer_intent.get("count") == 1:
                            omitted.add("count")
                        if composer_intent.get("aspect_ratio") != "auto":
                            omitted.update({"width", "height"})
                        if composer_intent.get("operation") != "upscale":
                            omitted.add("scale")
                        if composer_intent.get("operation") == "generate":
                            omitted.add("source_index")
                        try:
                            deterministic_call = normalize_candidate_with_trusted_arguments(
                                candidate,
                                normalized,
                                required,
                                omit_arguments=omitted,
                            )
                            validate_preserved_arguments(deterministic_call, preserved)
                            validate_required_arguments(deterministic_call, required)
                            validate_composer_intent_call(composer_intent, deterministic_call)
                        except CompatibilityError:
                            deterministic_call = None
                    if deterministic_call is not None:
                        APP.audit.write(
                            "deterministic_trusted_intent_normalization",
                            session_sha256=APP.capabilities.session_sha256,
                            preserved_argument_names=sorted(preserved),
                            required_argument_names=sorted(required),
                            omitted_argument_names=sorted(omitted),
                        )
                        response = canonical_to_openai_response(response, deterministic_call)
                        mode = "GENERIC_ADAPTER_TOOLS_DETERMINISTIC_TRUSTED_INTENT"
                    elif candidate is not None:
                        adapter_payload = build_candidate_adapter_payload(
                            upstream_payload,
                            normalized,
                            candidate,
                            preserve_arguments=preserved,
                            required_arguments=required,
                        )
                        adapter_input = "REJECTED_NATIVE_CANDIDATE"
                    else:
                        adapter_payload = build_adapter_payload(upstream_payload, normalized)
                        adapter_input = "ORIGINAL_REQUEST"
                    if deterministic_call is None:
                        APP.audit.write(
                            "generic_adapter_attempt",
                            session_sha256=APP.capabilities.session_sha256,
                            input=adapter_input,
                            preserved_argument_names=sorted(preserved),
                            required_argument_names=sorted(required),
                        )
                        response = APP.upstream.json(adapter_payload)
                        validate_ordinary_response(response)
                        content = response["choices"][0]["message"].get("content")
                        if not isinstance(content, str):
                            raise CompatibilityError("CANONICAL_JSON_INVALID", "Adapter did not return canonical text.")
                        call = parse_canonical_call(content, normalized)
                        validate_preserved_arguments(call, preserved)
                        validate_required_arguments(call, required)
                        if composer_intent is not None:
                            validate_composer_intent_call(composer_intent, call)
                        response = canonical_to_openai_response(response, call)
                        mode = "GENERIC_ADAPTER_TOOLS_AFTER_NATIVE_REJECTION"
            elif mode == "GENERIC_ADAPTER_TOOLS":
                response = APP.upstream.json(build_adapter_payload(upstream_payload, normalized))
                validate_ordinary_response(response)
                content = response["choices"][0]["message"].get("content")
                if not isinstance(content, str):
                    raise CompatibilityError("CANONICAL_JSON_INVALID", "Adapter did not return canonical text.")
                call = parse_canonical_call(content, normalized)
                if composer_intent is not None:
                    validate_composer_intent_call(composer_intent, call)
                response = canonical_to_openai_response(response, call)
            else:
                raise BoundaryError(
                    422,
                    "MODEL_INCOMPATIBLE_WITH_AAG_TOOL_CONTRACT",
                    "The sane chat model cannot satisfy native or generic-adapter tool contracts.",
                )
        else:
            response = APP.upstream.json(upstream_payload)
            validate_ordinary_response(response)

        if composer_intent is not None:
            APP.audit.write(
                "composer_intent_validated",
                session_sha256=APP.capabilities.session_sha256,
                intent_sha256=sha256_text(composer_canonical_json(composer_intent)),
                mode=mode,
            )
        APP.audit.write(
            "completion_validated",
            session_sha256=APP.capabilities.session_sha256,
            mode=mode,
            stream_requested=stream_requested,
            response_sha256=sha256_text(stable_json(response)),
        )
        if stream_requested:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for event in openai_sse_events(response):
                self.wfile.write(event)
                self.wfile.flush()
            self.close_connection = True
        else:
            self._json(200, response)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AAG model-neutral local compatibility boundary")
    parser.add_argument("--bind", action="append", default=None, help="Bind address; may be repeated")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AAG_COMPAT_PORT", "18080")))
    parser.add_argument("--upstream", default=os.environ.get("AAG_COMPAT_UPSTREAM", DEFAULT_UPSTREAM))
    parser.add_argument("--state-dir", default=os.environ.get("AAG_COMPAT_STATE_DIR", DEFAULT_STATE_DIR))
    parser.add_argument("--expected-executable", default=os.environ.get("AAG_COMPAT_EXPECTED_EXECUTABLE", DEFAULT_EXPECTED_EXECUTABLE))
    parser.add_argument("--storage", default=os.environ.get("AAG_COMPAT_STORAGE", DEFAULT_STORAGE_DIR))
    parser.add_argument("--token-file", default=os.environ.get("AAG_COMPAT_TOKEN_FILE", DEFAULT_STORAGE_DIR + "/proxy.token"))
    parser.add_argument("--anythingllm-db", default=os.environ.get("AAG_ANYTHINGLLM_DB", DEFAULT_DB))
    parser.add_argument("--anythingllm-api", default=os.environ.get("AAG_ANYTHINGLLM_API", DEFAULT_ANYTHINGLLM_API))
    # Full agent turns can include the preserved professional workspace prompt
    # and all three allowed Image schemas. Keep the transport bounded while
    # allowing measured long-context ingestion plus tool-call decoding.
    # Behavioral probes continue to pass their own strict 120-second timeout.
    parser.add_argument("--upstream-timeout", type=int, default=900)
    parser.add_argument("--composer-timeout", type=int, default=1800)
    return parser.parse_args()


def main() -> int:
    global APP
    args = parse_args()
    binds = args.bind or os.environ.get("AAG_COMPAT_BIND", "127.0.0.1,172.17.0.1").split(",")
    if not (1 <= args.port <= 65535):
        raise RuntimeError("Port is invalid.")
    APP = Application(args)
    servers: list[ReusableThreadingHTTPServer] = []
    for host in binds:
        host = host.strip()
        if host not in {"127.0.0.1", "::1", "172.17.0.1"}:
            raise RuntimeError(f"Unsafe compatibility bind address: {host}")
        servers.append(ReusableThreadingHTTPServer((host, args.port), BoundaryHandler))

    stopping = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        for server in servers:
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    APP.audit.write("boundary_start", layer_version=LAYER_VERSION, bind=binds, port=args.port)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers[1:]]
    for thread in threads:
        thread.start()
    try:
        servers[0].serve_forever()
    finally:
        for server in servers:
            server.server_close()
        APP.audit.write("boundary_stop", layer_version=LAYER_VERSION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
