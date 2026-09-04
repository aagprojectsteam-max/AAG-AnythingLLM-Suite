"""Narrow fixed-command runner for optional read-only collectors."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping


class CommandValidationError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    executable: str
    argv: tuple[str, ...]
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    started_at: str
    finished_at: str
    duration_ms: float
    timeout_seconds: float
    read_only: bool = True
    mutated: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["argv"] = list(self.argv)
        return data

    def provenance(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "executable": self.executable,
            "argv": list(self.argv),
            "status": self.status,
            "returncode": self.returncode,
            "stdout_bytes": len(self.stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(self.stderr.encode("utf-8", errors="replace")),
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "timeout_seconds": self.timeout_seconds,
            "read_only": True,
            "mutated": False,
        }


Builder = Callable[[Mapping[str, Any]], tuple[str, ...]]


@dataclass(frozen=True)
class CommandSpec:
    executable: Path
    builder: Builder


def _no_parameters(parameters: Mapping[str, Any]) -> tuple[str, ...]:
    if parameters:
        raise CommandValidationError("unexpected_command_parameters")
    return ()


def _absolute_path(parameters: Mapping[str, Any]) -> Path:
    if set(parameters) != {"path"} or not isinstance(parameters.get("path"), str):
        raise CommandValidationError("invalid_path_parameter")
    value = parameters["path"]
    if not value or "\x00" in value or value.startswith("-"):
        raise CommandValidationError("invalid_path_parameter")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise CommandValidationError("invalid_path_parameter")
    return path


def _df_bytes(parameters: Mapping[str, Any]) -> tuple[str, ...]:
    path = _absolute_path(parameters)
    return ("--block-size=1", "--output=source,fstype,size,used,avail,pcent,target", "--", str(path))


def _df_inodes(parameters: Mapping[str, Any]) -> tuple[str, ...]:
    path = _absolute_path(parameters)
    return ("--inodes", "--output=source,itotal,iused,iavail,ipcent,target", "--", str(path))


def _device(parameters: Mapping[str, Any]) -> Path:
    if set(parameters) != {"device"} or not isinstance(parameters.get("device"), str):
        raise CommandValidationError("invalid_device_parameter")
    value = parameters["device"]
    if not re.fullmatch(r"/dev/(?:sd[a-z]+|nvme\d+n\d+|vd[a-z]+)", value):
        raise CommandValidationError("device_not_allowlisted")
    return Path(value)


def _smart(parameters: Mapping[str, Any]) -> tuple[str, ...]:
    return ("--json=c", "--health", "--attributes", str(_device(parameters)))


def _nvme(parameters: Mapping[str, Any]) -> tuple[str, ...]:
    device = _device(parameters)
    if not re.fullmatch(r"/dev/nvme\d+n\d+", str(device)):
        raise CommandValidationError("nvme_device_required")
    return ("smart-log", "--output-format=json", str(device))


def _service(parameters: Mapping[str, Any]) -> tuple[str, ...]:
    if set(parameters) != {"service", "manager"}:
        raise CommandValidationError("invalid_service_parameters")
    service = parameters.get("service")
    manager = parameters.get("manager")
    if (
        not isinstance(service, str)
        or not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,160}\.service", service)
        or manager not in {"system", "user"}
    ):
        raise CommandValidationError("invalid_service_parameters")
    prefix = ("--user",) if manager == "user" else ()
    return (*prefix, "show", "--no-pager", "--property=LoadState,ActiveState,SubState,NRestarts", "--", service)


COMMANDS: dict[str, CommandSpec] = {
    "lsblk_json": CommandSpec(Path("/usr/bin/lsblk"), lambda p: _no_parameters(p) + ("--json", "--bytes", "--output", "NAME,KNAME,PATH,TYPE,FSTYPE,LABEL,UUID,SIZE,RO,RM,PKNAME,MOUNTPOINTS")),
    "findmnt_json": CommandSpec(Path("/usr/bin/findmnt"), lambda p: _no_parameters(p) + ("--json", "--bytes", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS,FSROOT")),
    "df_bytes": CommandSpec(Path("/usr/bin/df"), _df_bytes),
    "df_inodes": CommandSpec(Path("/usr/bin/df"), _df_inodes),
    "docker_df": CommandSpec(Path("/usr/bin/docker"), lambda p: _no_parameters(p) + ("system", "df", "--format", "{{json .}}")),
    "systemd_failed": CommandSpec(Path("/usr/bin/systemctl"), lambda p: _no_parameters(p) + ("--no-pager", "--plain", "--state=failed", "--type=service", "--all")),
    "journal_critical": CommandSpec(Path("/usr/bin/journalctl"), lambda p: _no_parameters(p) + ("--no-pager", "--output=json", "--dmesg", "--priority=0..3", "--since=-1 hour", "--lines=100")),
    "dpkg_audit": CommandSpec(Path("/usr/bin/dpkg"), lambda p: _no_parameters(p) + ("--audit",)),
    "smart_health": CommandSpec(Path("/usr/sbin/smartctl"), _smart),
    "nvme_health": CommandSpec(Path("/usr/sbin/nvme"), _nvme),
    "systemd_service": CommandSpec(Path("/usr/bin/systemctl"), _service),
    "systemd_boot_time": CommandSpec(Path("/usr/bin/systemd-analyze"), lambda p: _no_parameters(p) + ("time", "--no-pager")),
    "lsof_deleted": CommandSpec(Path("/usr/bin/lsof"), lambda p: _no_parameters(p) + ("-nP", "+L1", "-F0psn")),
}


class CommandRunner:
    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        max_output_bytes: int = 256_000,
        commands: Mapping[str, CommandSpec] = COMMANDS,
    ) -> None:
        if timeout_seconds <= 0 or max_output_bytes <= 0:
            raise ValueError("invalid_command_runner_limits")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = int(max_output_bytes)
        self.commands = dict(commands)

    @staticmethod
    def _read_bounded(stream: Any, maximum: int) -> tuple[str, bool]:
        stream.seek(0)
        raw = stream.read(maximum + 1)
        truncated = len(raw) > maximum
        if truncated:
            raw = raw[:maximum]
        text = raw.decode("utf-8", errors="replace")
        if truncated:
            text += "\n[AAG_OUTPUT_TRUNCATED]"
        return text, truncated

    def run(
        self,
        command_id: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> CommandResult:
        started_at = _utc_now()
        monotonic_started = time.monotonic()
        try:
            spec = self.commands[command_id]
        except KeyError as exc:
            raise CommandValidationError("command_not_allowlisted") from exc
        argv_tail = spec.builder(dict(parameters or {}))
        argv = (str(spec.executable), *argv_tail)
        if cancel_event is not None and cancel_event.is_set():
            return self._result(command_id, argv, "cancelled", None, "", "", False, False, started_at, monotonic_started)
        env = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LC_ALL": "C",
            "LANG": "C",
        }
        if command_id == "systemd_service" and (parameters or {}).get("manager") == "user":
            runtime = f"/run/user/{os.getuid()}"
            env["XDG_RUNTIME_DIR"] = runtime
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                process = subprocess.Popen(
                    list(argv),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=env,
                    close_fds=True,
                )
            except FileNotFoundError:
                return self._result(command_id, argv, "missing_command", None, "", "executable_not_found", False, False, started_at, monotonic_started)
            except PermissionError:
                return self._result(command_id, argv, "permission_denied", None, "", "executable_permission_denied", False, False, started_at, monotonic_started)
            deadline = monotonic_started + self.timeout_seconds
            status = "completed"
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    status = "cancelled"
                    process.terminate()
                    break
                if time.monotonic() >= deadline:
                    status = "timeout"
                    process.terminate()
                    break
                time.sleep(0.02)
            if process.poll() is None:
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            returncode = process.returncode
            if status == "completed" and returncode != 0:
                status = "nonzero_exit"
            stdout, stdout_truncated = self._read_bounded(stdout_file, self.max_output_bytes)
            stderr, stderr_truncated = self._read_bounded(stderr_file, self.max_output_bytes)
            return self._result(command_id, argv, status, returncode, stdout, stderr, stdout_truncated, stderr_truncated, started_at, monotonic_started)

    def _result(
        self,
        command_id: str,
        argv: tuple[str, ...],
        status: str,
        returncode: int | None,
        stdout: str,
        stderr: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        started_at: str,
        monotonic_started: float,
    ) -> CommandResult:
        return CommandResult(
            command_id=command_id,
            executable=argv[0],
            argv=argv,
            status=status,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=round((time.monotonic() - monotonic_started) * 1000, 3),
            timeout_seconds=self.timeout_seconds,
        )
