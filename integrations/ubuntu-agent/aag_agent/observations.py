"""Typed, bounded, read-only Ubuntu observation fabric."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

MAX_OUTPUT_BYTES = 128_000
DEFAULT_TIMEOUT = 15.0
_RUNTIME_DIR = f"/run/user/{os.getuid()}"
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
    "XDG_RUNTIME_DIR": _RUNTIME_DIR,
    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={_RUNTIME_DIR}/bus",
}
SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}\.service$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}(?::[a-z0-9-]+)?$")
MANAGERS = {"system", "user"}
SENSITIVE_PREFIXES = (
    Path("/home"), Path("/root"), Path("/etc/ssh"), Path("/etc/ssl/private"),
    Path("/etc/shadow"), Path("/etc/gshadow"), Path("/proc"), Path("/sys"),
)


class ObservationError(ValueError):
    pass


@dataclass(frozen=True)
class CommandSpec:
    domain: str
    binary: str
    argv: tuple[str, ...]


def _service(value: Any) -> str:
    value = str(value or "")
    if not SERVICE.fullmatch(value):
        raise ObservationError("invalid_service_name")
    return value


def _name(value: Any, kind: str = "name") -> str:
    value = str(value or "")
    if not NAME.fullmatch(value):
        raise ObservationError(f"invalid_{kind}")
    return value


def _pid(value: Any) -> str:
    if isinstance(value, bool):
        raise ObservationError("invalid_pid")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ObservationError("invalid_pid") from exc
    if number < 1 or number > 4_194_304:
        raise ObservationError("invalid_pid")
    return str(number)


def _path(value: Any) -> str:
    candidate = Path(str(value or ""))
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ObservationError("invalid_path")
    resolved = candidate.resolve(strict=False)
    if any(resolved == denied or denied in resolved.parents for denied in SENSITIVE_PREFIXES):
        raise ObservationError("sensitive_path_blocked")
    allowed = (Path("/mnt/data"), Path("/run/media"), Path("/var/log"))
    if resolved != Path("/") and not any(resolved == root or root in resolved.parents for root in allowed):
        raise ObservationError("path_not_allowed")
    return str(resolved)


def _manager(value: Any) -> str:
    value = str(value or "system")
    if value not in MANAGERS:
        raise ObservationError("invalid_systemd_manager")
    return value


def _manager_args(value: Any) -> tuple[str, ...]:
    return ("--user",) if _manager(value) == "user" else ("--system",)


def build_spec(domain: str, query: Mapping[str, Any] | None = None) -> CommandSpec:
    if query is not None and not isinstance(query, Mapping):
        raise ObservationError("query_must_be_object")
    query = dict(query or {})
    builders: dict[str, Callable[[], CommandSpec]] = {
        "systemd": lambda: CommandSpec(domain, "/usr/bin/systemctl", (*_manager_args(query.get("manager")), "show", _service(query.get("service")), "--no-pager", "--property=Id,LoadState,ActiveState,SubState,MainPID,ExecMainStatus,NRestarts,FragmentPath")),
        "journal": lambda: CommandSpec(domain, "/usr/bin/journalctl", (*_manager_args(query.get("manager")), "--no-pager", "--output=json", "--output-fields=MESSAGE,PRIORITY,SYSLOG_IDENTIFIER,_SYSTEMD_UNIT,_SYSTEMD_USER_UNIT", "--lines", str(_bounded_int(query.get("lines", 100), 1, 100)), "--unit", _service(query.get("service")))),
        "process": lambda: CommandSpec(domain, "/usr/bin/ps", ("-p", _pid(query.get("pid")), "-o", "pid=,ppid=,stat=,etimes=,comm=")),
        "network": lambda: CommandSpec(domain, "/usr/sbin/ip", ("-json", "address", "show", "dev", _name(query.get("interface"), "interface"))),
        "mount": lambda: CommandSpec(domain, "/usr/bin/findmnt", ("--json", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS", "--target", _path(query.get("path")))),
        "filesystem": lambda: CommandSpec(domain, "/usr/bin/df", ("--output=source,fstype,size,used,avail,pcent,target", "--block-size=1", _path(query.get("path")))),
        "docker": lambda: CommandSpec(domain, "/usr/bin/docker", ("inspect", "--type", "container", "--format", "{\"name\":{{json .Name}},\"image\":{{json .Config.Image}},\"status\":{{json .State.Status}},\"running\":{{json .State.Running}},\"exit_code\":{{json .State.ExitCode}},\"restart_count\":{{json .RestartCount}},\"health\":{{json .State.Health.Status}}}", _name(query.get("container"), "container"))),
        "package": lambda: CommandSpec(domain, "/usr/bin/dpkg-query", ("--show", "--showformat=${binary:Package}\t${db:Status-Status}\t${Version}\n", _package(query.get("package")))),
        "kernel": lambda: CommandSpec(domain, "/usr/bin/uname", ("-a",)),
        "uptime": lambda: CommandSpec(domain, "/usr/bin/uptime", ()),
        "memory": lambda: CommandSpec(domain, "/usr/bin/free", ("--bytes",)),
        "processes": lambda: CommandSpec(domain, "/usr/bin/ps", ("-eo", "pid=,ppid=,stat=,pcpu=,pmem=,comm=", "--sort=-pcpu")),
        "failed_units": lambda: CommandSpec(domain, "/usr/bin/systemctl", (*_manager_args(query.get("manager")), "--failed", "--no-legend", "--plain", "--no-pager")),
        "network_overview": lambda: CommandSpec(domain, "/usr/sbin/ip", ("-json", "address", "show")),
        "routes": lambda: CommandSpec(domain, "/usr/sbin/ip", ("-json", "route", "show")),
        "block_devices": lambda: CommandSpec(domain, "/usr/bin/lsblk", ("--json", "--bytes", "--output", "NAME,TYPE,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS,RO")),
        "docker_overview": lambda: CommandSpec(domain, "/usr/bin/docker", ("ps", "--all", "--format", "{\"name\":{{json .Names}},\"image\":{{json .Image}},\"state\":{{json .State}},\"status\":{{json .Status}}}")),
        "boot_events": lambda: CommandSpec(domain, "/usr/bin/journalctl", ("--system", "--boot", "--priority", "0..3", "--no-pager", "--output=json", "--output-fields=MESSAGE,PRIORITY,SYSLOG_IDENTIFIER,_SYSTEMD_UNIT", "--lines", "50")),
    }
    if domain not in builders:
        raise ObservationError("unknown_observation_domain")
    expected = {
        "systemd": {"service", "manager"}, "journal": {"service", "manager", "lines"},
        "process": {"pid"}, "network": {"interface"}, "mount": {"path"},
        "filesystem": {"path"}, "docker": {"container"},
        "package": {"package"}, "kernel": set(), "uptime": set(),
        "memory": set(), "processes": set(), "failed_units": {"manager"},
        "network_overview": set(), "routes": set(), "block_devices": set(),
        "docker_overview": set(), "boot_events": set(),
    }[domain]
    if set(query) - expected:
        raise ObservationError("unexpected_query_fields")
    return builders[domain]()


def _package(value: Any) -> str:
    value = str(value or "")
    if not PACKAGE.fullmatch(value):
        raise ObservationError("invalid_package")
    return value


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ObservationError("invalid_integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ObservationError("invalid_integer") from exc
    if not minimum <= number <= maximum:
        raise ObservationError("integer_out_of_range")
    return number


def _normalize(domain: str, stdout: str) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    try:
        if domain == "systemd":
            return {key: value for line in stdout.splitlines() if "=" in line for key, value in [line.split("=", 1)]}, None
        if domain in {"network", "mount", "docker", "block_devices"}:
            return json.loads(stdout), None
        if domain == "network_overview":
            items = json.loads(stdout)
            return [{"ifindex": item.get("ifindex"), "ifname": item.get("ifname"), "operstate": item.get("operstate"), "mtu": item.get("mtu"), "addresses": [{key: address.get(key) for key in ("family", "local", "prefixlen", "scope") if key in address} for address in item.get("addr_info", [])]} for item in items], None
        if domain == "routes":
            items = json.loads(stdout)
            return [{key: item.get(key) for key in ("dst", "gateway", "dev", "protocol", "metric", "prefsrc") if key in item} for item in items], None
        if domain == "docker_overview":
            return [json.loads(line) for line in stdout.splitlines() if line.strip()], None
        if domain == "package":
            package, status, version = stdout.rstrip("\n").split("\t", 2)
            return {"package": package, "status": status, "version": version}, None
        if domain == "kernel":
            return {"uname": stdout.strip()}, None
        if domain == "uptime":
            match = re.search(r"load average: ([0-9.,]+), ([0-9.,]+), ([0-9.,]+)", stdout)
            return {"summary": stdout.strip(), "load_average": [float(item.replace(",", ".")) for item in match.groups()] if match else None}, None
        if domain == "memory":
            rows = {parts[0].rstrip(":"): parts[1:] for line in stdout.splitlines()[1:] if len(parts := line.split()) >= 2}
            columns = stdout.splitlines()[0].split()
            return {name: {key: int(value) for key, value in zip(columns, values)} for name, values in rows.items()}, None
        if domain == "processes":
            facts = []
            for line in stdout.splitlines()[:10]:
                values = line.split(None, 5)
                if len(values) == 6:
                    facts.append({"pid": int(values[0]), "parent_pid": int(values[1]), "state": values[2], "cpu_percent": float(values[3]), "memory_percent": float(values[4]), "command": values[5]})
            return facts, None
        if domain == "failed_units":
            return [{"unit": values[0], "load": values[1], "active": values[2], "sub": values[3], "description": " ".join(values[4:])} for line in stdout.splitlines() if len(values := line.split()) >= 4], None
        if domain == "filesystem":
            lines = stdout.splitlines()
            if len(lines) < 2:
                raise ValueError("missing_data_row")
            values = lines[-1].split()
            if len(values) < 7:
                raise ValueError("invalid_data_row")
            return {"source": values[0], "filesystem_type": values[1], "size_bytes": int(values[2]), "used_bytes": int(values[3]), "available_bytes": int(values[4]), "used_percent": values[5], "target": " ".join(values[6:])}, None
        if domain == "process":
            values = stdout.strip().split(None, 4)
            if len(values) < 5:
                raise ValueError("invalid_process_row")
            return {"pid": int(values[0]), "parent_pid": int(values[1]), "state": values[2], "elapsed_seconds": int(values[3]), "command": values[4]}, None
        if domain in {"journal", "boot_events"}:
            safe_fields = ("__REALTIME_TIMESTAMP", "PRIORITY", "SYSLOG_IDENTIFIER", "_SYSTEMD_UNIT", "_SYSTEMD_USER_UNIT", "MESSAGE")
            return [{key: item[key] for key in safe_fields if key in item} for line in stdout.splitlines() if line.strip() for item in [json.loads(line)]], None
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, "malformed_output:" + type(exc).__name__
    return None, "normalizer_unavailable"


def observe(domain: str, query: Mapping[str, Any] | None = None, *, timeout: float = DEFAULT_TIMEOUT, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Run one validated read-only command. Callers cannot supply binaries/argv."""
    if not 0 < timeout <= DEFAULT_TIMEOUT:
        raise ObservationError("invalid_timeout")
    spec = build_spec(domain, query)
    started = time.monotonic()
    observed_at = time.time()
    try:
        result = runner(
            [spec.binary, *spec.argv], shell=False, capture_output=True,
            timeout=timeout, env=SAFE_ENV, check=False,
        )
        stdout = bytes(result.stdout) if isinstance(result.stdout, (bytes, bytearray)) else str(result.stdout).encode()
        stderr = bytes(result.stderr) if isinstance(result.stderr, (bytes, bytearray)) else str(result.stderr).encode()
        truncated = len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES
        decoded_stdout = stdout[:MAX_OUTPUT_BYTES].decode("utf-8", "replace")
        if domain == "package" and result.returncode == 1 and not truncated:
            facts, normalization_error = {"package": str((query or {}).get("package")), "installed": False}, None
            effective_status = "completed"
        else:
            facts, normalization_error = _normalize(domain, decoded_stdout) if result.returncode == 0 and not truncated else (None, "output_truncated" if truncated else None)
            if domain == "package" and isinstance(facts, dict):
                facts["installed"] = facts.get("status") == "installed"
            effective_status = "completed" if result.returncode == 0 else "command_failed"
        return {
            "schema": "aag-observation-v1", "domain": domain,
            "observed_at": observed_at, "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "target": dict(query or {}),
            "provenance": {"collector": "aag.observations", "collector_version": 1, "binary": spec.binary, "argv": list(spec.argv), "shell": False},
            "status": effective_status,
            "returncode": result.returncode,
            "stdout": decoded_stdout,
            "stderr": stderr[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
            "facts": facts, "normalization_error": normalization_error,
            "truncated": truncated, "read_only": True, "mutated": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "schema": "aag-observation-v1", "domain": domain,
            "observed_at": observed_at, "provenance": {"binary": spec.binary, "argv": list(spec.argv), "shell": False},
            "status": "timeout", "timeout_seconds": timeout,
            "read_only": True, "mutated": False,
        }
    except FileNotFoundError:
        return {"schema": "aag-observation-v1", "domain": domain, "observed_at": observed_at, "status": "missing_binary", "error": "collector_binary_missing", "provenance": {"collector": "aag.observations", "collector_version": 1, "binary": spec.binary, "argv": list(spec.argv), "shell": False}, "read_only": True, "mutated": False}
    except PermissionError:
        return {"schema": "aag-observation-v1", "domain": domain, "observed_at": observed_at, "status": "permission_denied", "error": "collector_permission_denied", "provenance": {"collector": "aag.observations", "collector_version": 1, "binary": spec.binary, "argv": list(spec.argv), "shell": False}, "read_only": True, "mutated": False}
    except OSError as exc:
        return {"schema": "aag-observation-v1", "domain": domain, "observed_at": observed_at, "status": "collector_error", "error": type(exc).__name__, "provenance": {"collector": "aag.observations", "collector_version": 1, "binary": spec.binary, "argv": list(spec.argv), "shell": False}, "read_only": True, "mutated": False}
