#!/usr/bin/env python3
"""On-demand local Production bridge for the frozen Human Identity contract."""
from __future__ import annotations

import calendar
import contextlib
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

PROJECT = Path("/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System")
RELEASE = "0.9.0-preview.3"
CONTRACT_SHA = "d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4"
DEFAULT_RUNTIME = PROJECT / "image-agent/releases/0.9.0-preview.3/human-identity"
RUNTIME = Path(os.environ.get("AAG_HUMAN_IDENTITY_RUNTIME", DEFAULT_RUNTIME))
CONFIG_PATH = RUNTIME / "config/PRODUCTION-CONFIG.json"
CONTRACT_PATH = RUNTIME / "config/CONTRACT-B-FREEZE.json"
SEALED_CONTRACT = PROJECT / "image-agent/phase-7r3/acceptance/CONTRACT-B-FREEZE.json"
STATE = Path(os.environ.get("AAG_HUMAN_IDENTITY_STATE_ROOT", "/mnt/data/AI/Apps/AnythingLLM/storage/aag-human-identity-state"))
AGENT_STATE = Path(os.environ.get("AAG_IMAGE_AGENT_STATE_ROOT", "/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-state"))
PRIVATE_OUTPUT = Path(os.environ.get("AAG_HUMAN_IDENTITY_PRIVATE_OUTPUT", "/mnt/data/AI/Outputs/.aag-human-identity-private"))
OUTPUT = Path(os.environ.get("AAG_OUTPUT_ROOT", "/mnt/data/AI/Outputs"))
PYTHON = "/mnt/data/AI/ComfyUI/venv/bin/python"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
JOB_RE = re.compile(r"^aag-[0-9a-f-]{36}$", re.I)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUEST_KEYS = {
    "schema_version", "request_id", "parent_job_id", "child_job_id", "reference_kind",
    "fixture_id", "identity_domain", "prompt", "reference_sha256", "original_sha256",
    "reference_width", "reference_height", "source_index", "seed", "width", "height", "contract_id",
    "contract_b_sha256", "release", "candidate_release", "route", "lease_token",
    "caller", "submitted_at",
}


class BridgeFailure(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False,
                 classification: str | None = None, evidence_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.classification = classification
        self.evidence_id = evidence_id


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def atomic_text(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.write(descriptor, value.encode("utf-8", errors="replace"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def secure_json(path: Path, maximum: int = 128 * 1024) -> dict:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 1 or info.st_size > maximum:
            raise BridgeFailure("REQUEST_INVALID", "The local bridge record is unsafe.")
        payload = os.read(descriptor, info.st_size + 1)
        if len(payload) != info.st_size:
            raise BridgeFailure("REQUEST_INVALID", "The local bridge record changed during read.")
        value = json.loads(payload)
    finally:
        os.close(descriptor)
    if not isinstance(value, dict):
        raise BridgeFailure("REQUEST_INVALID", "The local bridge record must be an object.")
    return value


def load_config() -> dict:
    config = secure_json(CONFIG_PATH)
    if config.get("release") != RELEASE or config.get("contract_b_sha256") != CONTRACT_SHA:
        raise BridgeFailure("CONTRACT_INTEGRITY_FAILURE", "Production identity configuration drifted.")
    if sha256(CONTRACT_PATH) != CONTRACT_SHA or sha256(SEALED_CONTRACT) != CONTRACT_SHA:
        raise BridgeFailure("CONTRACT_INTEGRITY_FAILURE", "Frozen Contract B hash mismatch.")
    return config


def validate_message(value: dict, config: dict) -> dict:
    if set(value) != REQUEST_KEYS or value.get("schema_version") != "aag.human-identity.bridge-request.v2":
        raise BridgeFailure("REQUEST_INVALID", "The local identity request schema is invalid.")
    request_id = str(value.get("request_id", ""))
    if not UUID_RE.fullmatch(request_id):
        raise BridgeFailure("REQUEST_INVALID", "The identity request ID is invalid.")
    if not JOB_RE.fullmatch(str(value.get("parent_job_id", ""))) or not JOB_RE.fullmatch(str(value.get("child_job_id", ""))):
        raise BridgeFailure("REQUEST_INVALID", "The parent or child job ID is invalid.")
    digest_re = re.compile(r"^[0-9a-f]{64}$")
    if not digest_re.fullmatch(str(value.get("reference_sha256", ""))) or not digest_re.fullmatch(str(value.get("original_sha256", ""))):
        raise BridgeFailure("REQUEST_INVALID", "Trusted reference hashes are missing or invalid.")
    reference_kind = value.get("reference_kind")
    fixture = None
    if reference_kind == "historical_validation_fixture":
        fixture = config["historical_validation_fixtures"].get(str(value.get("fixture_id", "")))
        if not fixture or value.get("original_sha256") != fixture["sha256"] or value.get("identity_domain") != fixture["domain"]:
            raise BridgeFailure("IDENTITY_DOMAIN_UNSUPPORTED", "The named historical validation fixture is inconsistent.")
    elif reference_kind == "trusted_runtime_reference":
        runtime_contract = config.get("trusted_runtime_reference", {})
        if runtime_contract.get("enabled") is not True or value.get("fixture_id") is not None or value.get("identity_domain") not in runtime_contract.get("contract_prompt_domains", []):
            raise BridgeFailure("IDENTITY_DOMAIN_UNSUPPORTED", "The trusted runtime reference contract is unavailable or inconsistent.")
    else:
        raise BridgeFailure("IDENTITY_DOMAIN_UNSUPPORTED", "The reference kind is outside the Production-v1 eligibility contract.")
    if value.get("prompt") != config["prompts"][value["identity_domain"]]:
        raise BridgeFailure("CONTRACT_INTEGRITY_FAILURE", "The request does not contain exact frozen Contract B wording.")
    if value.get("contract_id") != "structured-close-b" or value.get("contract_b_sha256") != CONTRACT_SHA or value.get("release") != RELEASE:
        raise BridgeFailure("CONTRACT_INTEGRITY_FAILURE", "The request does not match the active release contract.")
    if value.get("width") != 896 or value.get("height") != 1152 or not isinstance(value.get("seed"), int) or not 0 <= value["seed"] <= 0xFFFFFFFF:
        raise BridgeFailure("IDENTITY_CONTRACT_REQUIRED", "The request recipe differs from frozen Contract B.")
    if not all(isinstance(value.get(key), int) and value[key] > 0 for key in ("reference_width", "reference_height", "source_index")):
        raise BridgeFailure("REQUEST_INVALID", "Trusted current-attachment dimensions or index are invalid.")
    if not isinstance(value.get("caller"), dict) or set(value["caller"]) != {"workspace_id", "thread_id", "user_id", "invocation_id"}:
        raise BridgeFailure("REQUEST_INVALID", "Trusted caller scope is missing.")
    if any(not isinstance(item, str) or not item for item in value["caller"].values()) or any(value["caller"][key] == "unknown" for key in ("workspace_id", "thread_id", "invocation_id")):
        raise BridgeFailure("REQUEST_INVALID", "Trusted caller scope is incomplete.")
    if not UUID_RE.fullmatch(str(value.get("lease_token", ""))):
        raise BridgeFailure("XPU_LEASE_LOST", "The delegated XPU lease token is invalid.", True)
    return {"kind": reference_kind, "fixture": fixture}


def timestamp_age(value: str) -> float:
    parsed = time.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S")
    return time.time() - calendar.timegm(parsed)


def verify_lease(message: dict) -> None:
    owner = secure_json(AGENT_STATE / "scheduler/lease/owner.json", 64 * 1024)
    age = timestamp_age(owner.get("heartbeat_at") or owner.get("acquired_at") or "")
    if owner.get("kind") != "agent" or owner.get("job_id") != message["parent_job_id"] or not 0 <= age <= 120:
        raise BridgeFailure("XPU_LEASE_LOST", "The delegated XPU lease owner is inconsistent.", True)
    if not hmac.compare_digest(str(owner.get("token", "")), message["lease_token"]):
        raise BridgeFailure("XPU_LEASE_LOST", "The delegated XPU lease token does not match.", True)


def verify_fixture(fixture: dict) -> Path:
    target = Path(fixture["path"])
    if target.is_symlink() or not target.is_file() or target.stat().st_nlink != 1 or sha256(target) != fixture["sha256"]:
        raise BridgeFailure("REFERENCE_CHANGED", "The authorized frozen reference fixture failed integrity validation.")
    return target


def verify_staged_reference(message: dict) -> Path:
    target = STATE / "references" / f"{message['request_id']}.png"
    provenance_path = STATE / "references" / f"{message['request_id']}.provenance.json"
    try:
        info = target.lstat()
    except FileNotFoundError as error:
        raise BridgeFailure("REFERENCE_NOT_FOUND", "The request-bound staged current attachment is missing.") from error
    if target.is_symlink() or not target.is_file() or info.st_nlink != 1:
        raise BridgeFailure("REFERENCE_NOT_REGULAR", "The request-bound staged current attachment is unsafe.")
    if info.st_size <= 0 or info.st_size > 50 * 1024 * 1024 or sha256(target) != message["reference_sha256"]:
        raise BridgeFailure("REFERENCE_CHANGED", "The request-bound staged current attachment failed integrity validation.")
    provenance = secure_json(provenance_path, 64 * 1024)
    if set(provenance) != {"schema_version", "request_id", "caller", "source"} or provenance.get("schema_version") != "aag.human-identity.staged-reference-provenance.v1" or provenance.get("request_id") != message["request_id"]:
        raise BridgeFailure("REQUEST_INVALID", "The staged current-attachment provenance record is invalid.")
    expected_source = {
        "kind": "current_attachment", "index": message["source_index"],
        "original_sha256": message["original_sha256"], "normalized_sha256": message["reference_sha256"],
        "width": message["reference_width"], "height": message["reference_height"], "format": "png",
    }
    if provenance.get("caller") != message["caller"] or provenance.get("source") != expected_source:
        raise BridgeFailure("SOURCE_UNAUTHORIZED", "The staged current attachment belongs to a different trusted invocation scope.")
    return target


def offline_env(runtime: Path) -> dict:
    p7 = PROJECT / "image-agent/phase-7/candidate"
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "ORT_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONPATH": ":".join([
            str(runtime / "runtime"), str(p7 / "runtime"), str(p7 / "runtime-deps/phase6"),
            str(p7 / "runtime-deps/phase5"), str(p7 / "assets/source"),
        ]),
    }
    return env


def sandbox(command: list[str]) -> list[str]:
    return [
        "bwrap", "--ro-bind", "/", "/", "--bind", str(STATE), str(STATE),
        "--bind", str(PRIVATE_OUTPUT), str(PRIVATE_OUTPUT), "--tmpfs", "/tmp",
        "--proc", "/proc", "--dev-bind", "/dev", "/dev", "--unshare-net",
        "--die-with-parent", "--chdir", str(PROJECT), "--", *command,
    ]


SAFE_ENV_KEYS = (
    "PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TMPDIR",
    "PYTHONDONTWRITEBYTECODE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
    "ORT_DISABLE_TELEMETRY", "HF_HUB_DISABLE_TELEMETRY",
    "TOKENIZERS_PARALLELISM", "ONEAPI_DEVICE_SELECTOR", "ZE_AFFINITY_MASK",
    "SYCL_CACHE_PERSISTENT", "AAG_HUMAN_IDENTITY_RUNTIME",
    "AAG_HUMAN_IDENTITY_STATE_ROOT", "AAG_IMAGE_AGENT_STATE_ROOT",
    "AAG_HUMAN_IDENTITY_PRIVATE_OUTPUT", "AAG_OUTPUT_ROOT",
)
SENSITIVE = re.compile(
    r"(?i)(secret|token|password|authorization|api[_-]?key|"
    r"reference(?:_image)?_?base64|raw_?embedding|face_?crop|raw_?reference)"
    r"\s*[:=]\s*[^\s,;]+"
)


def sanitize_text(value: str, maximum: int = 512 * 1024) -> str:
    cleaned = SENSITIVE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    if len(cleaned) > maximum:
        cleaned = cleaned[:maximum] + "\n<truncated>\n"
    return cleaned


def safe_environment(env: dict) -> dict:
    values = {key: str(env[key]) for key in SAFE_ENV_KEYS if key in env}
    payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return {"values": values, "sha256": hashlib.sha256(payload).hexdigest()}


def namespace_snapshot() -> dict:
    namespaces = {}
    for name in ("cgroup", "ipc", "mnt", "net", "pid", "time", "user", "uts"):
        with contextlib.suppress(OSError):
            namespaces[name] = os.readlink(f"/proc/self/ns/{name}")
    descriptors = []
    for number in (0, 1, 2):
        try:
            info = os.fstat(number)
            descriptors.append({"fd": number, "mode": stat.S_IFMT(info.st_mode), "isatty": os.isatty(number)})
        except OSError:
            descriptors.append({"fd": number, "closed": True})
    umask = None
    with contextlib.suppress(OSError):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("Umask:"):
                umask = line.split(":", 1)[1].strip()
                break
    return {
        "pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid(),
        "groups": os.getgroups(), "umask": umask, "namespaces": namespaces,
        "file_descriptors": descriptors,
    }


def lease_snapshot() -> dict | None:
    try:
        owner = secure_json(AGENT_STATE / "scheduler/lease/owner.json", 64 * 1024)
    except Exception:
        return None
    return {
        "kind": owner.get("kind"), "job_id": owner.get("job_id"),
        "acquired_at": owner.get("acquired_at"), "heartbeat_at": owner.get("heartbeat_at"),
        "token_sha256": hashlib.sha256(str(owner.get("token", "")).encode()).hexdigest(),
    }


def classify_process_failure(stage: str, stderr: str, stdout: str, returncode: int | None,
                             spawn_exception: BaseException | None = None,
                             timed_out: bool = False) -> str:
    text = f"{stderr}\n{stdout}".lower()
    if spawn_exception is not None:
        return "SPAWN_FAILED"
    if timed_out:
        return "WORKER_TIMEOUT"
    if returncode is not None and returncode < 0:
        return "WORKER_SIGNALLED"
    if "no permissions to create a new namespace" in text or "operation not permitted" in text:
        return "CONFIG_FAILED"
    if "modulenotfounderror" in text or "importerror" in text or "no module named" in text:
        return "IMPORT_FAILED"
    if "asset mismatch" in text or "hash mismatch" in text:
        return "ASSET_HASH_MISMATCH"
    if "no such file or directory" in text or "filenotfounderror" in text:
        return "ASSET_MISSING"
    if "xpu" in text and ("init" in text or "device" in text or "driver" in text):
        return "XPU_INIT_FAILED"
    if "xpu" in text:
        return "XPU_RUNTIME_FAILED"
    if "model" in text and ("load" in text or "checkpoint" in text):
        return "MODEL_LOAD_FAILED"
    if stage == "REFERENCE_PREFLIGHT":
        return "REFERENCE_PREFLIGHT_FAILED"
    if returncode not in (None, 0):
        return "ENGINE_NONZERO_EXIT"
    return "ENGINE_CRASH_UNKNOWN"


def run_checked(command: list[str], env: dict, timeout: int, expected: set[int] = {0},
                *, stage: str, request_id: str, cancel_file: Path | None = None):
    evidence_id = f"{request_id}/{stage.lower()}"
    evidence = STATE / "process" / request_id
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence.chmod(0o700)
    started_wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    started = time.monotonic()
    process = None
    stdout = ""
    stderr = ""
    timed_out = False
    spawn_exception = None
    try:
        process = subprocess.Popen(
            command, cwd=PROJECT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            timed_out = True
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            process.terminate()
            try:
                tail_out, tail_err = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                tail_out, tail_err = process.communicate(timeout=10)
            stdout += tail_out or ""
            stderr += tail_err or ""
    except (OSError, ValueError) as error:
        spawn_exception = error
    elapsed = time.monotonic() - started
    returncode = None if process is None else process.returncode
    classification = classify_process_failure(stage, stderr, stdout, returncode, spawn_exception, timed_out)
    record = {
        "schema_version": "aag.human-identity.process-evidence.v1",
        "request_id": request_id, "stage": stage, "evidence_id": evidence_id,
        "executable": command[0], "resolved_executable": shutil.which(command[0]),
        "argv": command, "cwd": str(PROJECT),
        "child_pid": None if process is None else process.pid,
        "exit_code": returncode if returncode is not None and returncode >= 0 else None,
        "terminating_signal": -returncode if returncode is not None and returncode < 0 else None,
        "started_at": started_wall,
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": elapsed, "timeout_seconds": timeout,
        "timed_out": timed_out,
        "cancelled_at_end": bool(cancel_file and cancel_file.exists()),
        "spawn_exception_type": None if spawn_exception is None else type(spawn_exception).__name__,
        "spawn_exception": None if spawn_exception is None else sanitize_text(str(spawn_exception), 4096),
        "failure_classification": None if returncode in expected and not timed_out and spawn_exception is None else classification,
        "environment": safe_environment(env), "bridge_process": namespace_snapshot(),
        "xpu_lease_owner": lease_snapshot(),
        "stdout_sha256": hashlib.sha256(stdout.encode(errors="replace")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode(errors="replace")).hexdigest(),
    }
    atomic_text(evidence / f"{stage.lower()}.stdout.log", sanitize_text(stdout))
    atomic_text(evidence / f"{stage.lower()}.stderr.log", sanitize_text(stderr))
    atomic_json(evidence / f"{stage.lower()}.json", record)
    if spawn_exception is not None or timed_out or returncode not in expected:
        raise BridgeFailure(
            "ENGINE_CRASH", "The isolated local identity stage failed.", True,
            classification=classification, evidence_id=evidence_id,
        )
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def require_process_json(path: Path, maximum: int, request_id: str, stage: str,
                         classification: str) -> dict:
    evidence_id = f"{request_id}/{stage.lower()}"
    if not path.exists():
        raise BridgeFailure(
            "ENGINE_CRASH", f"The {stage.lower()} stage returned no result.", True,
            classification=classification, evidence_id=evidence_id,
        )
    try:
        return secure_json(path, maximum)
    except (OSError, ValueError, json.JSONDecodeError, BridgeFailure) as error:
        evidence = STATE / "process" / request_id
        evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_json(evidence / f"{stage.lower()}-protocol-error.json", {
            "schema_version": "aag.human-identity.protocol-error.v1",
            "request_id": request_id, "stage": stage,
            "exception_type": type(error).__name__,
            "exception": sanitize_text(str(error), 4096),
            "result_path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
        })
        raise BridgeFailure(
            "ENGINE_CRASH", f"The {stage.lower()} stage returned an invalid result.", True,
            classification=classification, evidence_id=evidence_id,
        ) from error


def external_network_events(trace_prefix: Path) -> tuple[int, int]:
    syscalls = 0
    external = 0
    for trace in trace_prefix.parent.glob(trace_prefix.name + "*"):
        text = trace.read_text(errors="replace")
        syscalls += len([line for line in text.splitlines() if any(token in line for token in ("socket(", "connect(", "sendto(", "sendmsg("))])
        for line in text.splitlines():
            if "connect(" not in line and "sendto(" not in line and "sendmsg(" not in line:
                continue
            if "AF_INET" in line and not any(loopback in line for loopback in ("127.0.0.1", "0.0.0.0", "::1")):
                external += 1
    return syscalls, external


def health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200 and b'AAG Central Image Hub' in response.read(4096)
    except Exception:
        return False


def docker_gateway() -> str:
    result = subprocess.run(["docker", "inspect", "anythingllm", "--format", "{{range .NetworkSettings.Networks}}{{println .Gateway}}{{end}}"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    gateway = next((line.strip() for line in result.stdout.splitlines() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", line.strip())), "")
    if result.returncode or not gateway:
        raise BridgeFailure("ENGINE_UNAVAILABLE", "The local AnythingLLM bridge gateway is unavailable.", True)
    return gateway


@contextlib.contextmanager
def publisher(private: Path):
    gateway = docker_gateway()
    local_url = "http://127.0.0.1:18190/health"
    bridge_url = f"http://{gateway}:18190/health"
    owned: list[subprocess.Popen] = []
    log = (private / "publisher.log").open("ab", buffering=0)
    try:
        if not health(local_url):
            env = {**os.environ, "AAG_IMAGE_HUB_HOST": "127.0.0.1", "AAG_IMAGE_HUB_PORT": "18190", "AAG_OUTPUT_ROOT": str(OUTPUT), "AAG_XPU_SCHEDULER_ROOT": str(AGENT_STATE / "scheduler")}
            owned.append(subprocess.Popen([sys.executable, "/mnt/data/AI/Apps/AnythingLLM/AAG-Upscale-Engine/service/image-hub.py"], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            for _ in range(60):
                if health(local_url):
                    break
                if owned[-1].poll() is not None:
                    break
                time.sleep(0.25)
            if not health(local_url):
                raise BridgeFailure("ENGINE_UNAVAILABLE", "The local trusted image publisher failed readiness.", True)
        if not health(bridge_url):
            owned.append(subprocess.Popen([
                sys.executable, "/mnt/data/AI/Apps/AnythingLLM/AAG-Upscale-Engine/service/docker-bridge.py",
                "--listen-host", gateway, "--listen-port", "18190", "--target-host", "127.0.0.1", "--target-port", "18190",
            ], stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            for _ in range(60):
                if health(bridge_url):
                    break
                if owned[-1].poll() is not None:
                    break
                time.sleep(0.25)
            if not health(bridge_url):
                raise BridgeFailure("ENGINE_UNAVAILABLE", "The container-local trusted publisher relay failed readiness.", True)
        yield {"gateway": gateway, "owned_processes": len(owned)}
    finally:
        for process in reversed(owned):
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        log.close()


def publish_exclusive(source: Path, request_id: str) -> tuple[str, str]:
    name = f"REF-{request_id}.png"
    if not NAME_RE.fullmatch(name):
        raise BridgeFailure("OUTPUT_POLICY_VIOLATION", "The managed output name is invalid.")
    target = OUTPUT / name
    if target.exists() or target.is_symlink():
        raise BridgeFailure("OUTPUT_COLLISION", "The managed output name already exists.")
    os.chmod(source, 0o664)
    os.link(source, target, follow_symlinks=False)
    directory = os.open(OUTPUT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    digest = sha256(target)
    source.unlink()
    return name, digest


def error_response(request_id: str, code: str, message: str, retryable: bool = False,
                   classification: str | None = None, evidence_id: str | None = None) -> dict:
    response = {
        "schema_version": "aag.human-identity.response.v1", "request_id": request_id,
        "release": RELEASE, "contract_b_sha256": CONTRACT_SHA, "status": "CANCELLED" if code == "JOB_CANCELLED" else "FAIL",
        "error_code": code, "message": message, "retryable": retryable,
        "external_network_events": 0, "cleanup_result": "PASS", "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if classification:
        response["process_failure_classification"] = classification
    if evidence_id:
        response["process_evidence_id"] = evidence_id
    return response


def process_request(request_file: Path) -> None:
    request_id = request_file.stem
    response_path = STATE / "responses" / f"{request_id}.json"
    started = time.monotonic()
    private = STATE / "private" / request_id
    cancel = AGENT_STATE / "cancel" / "unknown"
    message = None
    try:
        if not UUID_RE.fullmatch(request_id) or request_file.is_symlink():
            raise BridgeFailure("REQUEST_INVALID", "The bridge inbox filename is invalid.")
        config = load_config()
        message = secure_json(request_file)
        reference_contract = validate_message(message, config)
        if message["request_id"] != request_id:
            raise BridgeFailure("REQUEST_INVALID", "The bridge request ID and filename differ.")
        request_file.unlink()
        verify_lease(message)
        reference_path = verify_staged_reference(message)
        cancel = AGENT_STATE / "cancel" / message["parent_job_id"]
        private.mkdir(parents=True, exist_ok=False, mode=0o700)
        PRIVATE_OUTPUT.mkdir(parents=True, exist_ok=True, mode=0o700)
        PRIVATE_OUTPUT.chmod(0o700)
        worker_request = private / "request.json"
        reference_validation = private / "reference-validation.json"
        worker_report = private / "worker.json"
        stage_file = private / "worker-stage.json"
        evaluation_file = private / "evaluation.json"
        worker_output = PRIVATE_OUTPUT / f".{request_id}.candidate.png"
        trace_prefix = private / "network.strace"
        atomic_json(worker_request, {
            "schema_version": "aag.human-identity.request.v1", "request_id": request_id,
            "operation": "transform", "prompt": message["prompt"], "reference_path": str(reference_path),
            "reference_kind": message["reference_kind"], "fixture_id": message["fixture_id"],
            "reference_sha256": message["reference_sha256"], "original_sha256": message["original_sha256"],
            "identity_domain": message["identity_domain"], "source_index": message["source_index"],
            "seed": message["seed"], "width": 896, "height": 1152,
        })
        env = offline_env(RUNTIME)
        validator = sandbox([PYTHON, str(RUNTIME / "runtime/validate_reference_cli.py"), "--request", str(worker_request), "--config", str(CONFIG_PATH), "--result", str(reference_validation)])
        run_checked(
            validator, env, 300, stage="REFERENCE_PREFLIGHT",
            request_id=request_id, cancel_file=cancel,
        )
        verify_lease(message)
        worker = sandbox([PYTHON, str(RUNTIME / "runtime/pulid_worker.py"), "--request", str(worker_request), "--report", str(worker_report), "--output", str(worker_output), "--cancel-file", str(cancel), "--stage-file", str(stage_file)])
        traced = ["strace", "-ff", "-qq", "-e", "trace=network", "-o", str(trace_prefix), *worker]
        result = run_checked(
            traced, env, 2700, {0, 1, 2}, stage="IDENTITY_WORKER",
            request_id=request_id, cancel_file=cancel,
        )
        report = require_process_json(
            worker_report, 2 * 1024 * 1024, request_id,
            "IDENTITY_WORKER", "ENGINE_PROTOCOL_ERROR",
        )
        network_syscalls, external_events = external_network_events(trace_prefix)
        if external_events:
            raise BridgeFailure("NETWORK_POLICY_VIOLATION", "The isolated identity runtime attempted an external connection.")
        if result.returncode == 2 or report.get("status") == "CANCELLED":
            raise BridgeFailure("JOB_CANCELLED", "The Human Identity request was cancelled.")
        if result.returncode or report.get("status") != "PASS":
            detail = f"{report.get('error_type', '')}: {report.get('error', '')}"
            classification = classify_process_failure(
                "IDENTITY_WORKER", detail, "", result.returncode,
            )
            raise BridgeFailure(
                "ENGINE_CRASH", "The local identity worker failed safely.", True,
                classification=classification, evidence_id=f"{request_id}/identity_worker",
            )
        verify_lease(message)
        gate = sandbox([PYTHON, str(RUNTIME / "runtime/quality_gate.py"), "--worker-report", str(worker_report), "--reference-validation", str(reference_validation), "--result", str(evaluation_file)])
        gate_result = run_checked(
            gate, env, 600, {0, 1}, stage="QUALITY_GATE",
            request_id=request_id, cancel_file=cancel,
        )
        evaluation = require_process_json(
            evaluation_file, 2 * 1024 * 1024, request_id,
            "QUALITY_GATE", "OUTPUT_PROTOCOL_ERROR",
        )
        if gate_result.returncode or evaluation.get("status") != "PASS":
            code = str(evaluation.get("error_code") or "IDENTITY_QUALITY_REJECTED")
            raise BridgeFailure(code, "The generated portrait was rejected by the locked Production quality contract.")
        name, digest = publish_exclusive(worker_output, request_id)
        evidence = STATE / "evidence" / request_id
        evidence.mkdir(parents=True, exist_ok=False, mode=0o700)
        for source in (reference_validation, worker_report, evaluation_file):
            shutil.move(source, evidence / source.name)
        network_dir = STATE / "network" / request_id
        network_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        for trace in private.glob("network.strace*"):
            shutil.move(trace, network_dir / trace.name)
        with contextlib.suppress(FileNotFoundError): worker_request.unlink()
        with contextlib.suppress(FileNotFoundError): stage_file.unlink()
        with contextlib.suppress(OSError): private.rmdir()
        response = {
            "schema_version": "aag.human-identity.response.v1", "request_id": request_id,
            "release": RELEASE, "contract_b_sha256": CONTRACT_SHA, "status": "PASS",
            "artifact_filename": name, "artifact_sha256": digest,
            "evaluation": {
                key: evaluation.get(key) for key in (
                    "status", "intended_cosine", "minimum_negative_margin", "face_occupancy_percent",
                    "face_blur_variance", "face_blur_variance_floor", "prompt_composition_result",
                    "prompt_adherence_proxy", "severe_artifact_result", "detection_path",
                )
            },
            "generation_latency_seconds": report.get("inference_seconds"),
            "total_latency_seconds": time.monotonic() - started,
            "peak_rss_bytes": report.get("peak_rss_bytes"), "xpu_memory": report.get("xpu_memory"),
            "network_syscalls": network_syscalls, "external_network_events": 0,
            "cleanup_result": "PASS", "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with publisher(STATE / "evidence" / request_id):
            atomic_json(response_path, response)
            ack = STATE / "acks" / f"{request_id}.json"
            for _ in range(180):
                if ack.exists():
                    secure_json(ack)
                    break
                time.sleep(0.5)
    except BridgeFailure as error:
        with contextlib.suppress(FileNotFoundError): request_file.unlink()
        atomic_json(response_path, error_response(
            request_id, error.code, str(error), error.retryable,
            error.classification, error.evidence_id,
        ))
    except Exception as error:
        with contextlib.suppress(FileNotFoundError): request_file.unlink()
        evidence = STATE / "process" / request_id
        evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_json(evidence / "bridge-exception.json", {
            "schema_version": "aag.human-identity.bridge-exception.v1",
            "request_id": request_id, "stage": "BRIDGE",
            "exception_type": type(error).__name__,
            "exception": sanitize_text(str(error), 4096),
            "traceback": sanitize_text(traceback.format_exc()),
            "bridge_process": namespace_snapshot(),
        })
        atomic_json(response_path, error_response(
            request_id, "ENGINE_CRASH", "The local Human Identity bridge failed safely.", True,
            "ENGINE_CRASH_UNKNOWN", f"{request_id}/bridge-exception",
        ))
    finally:
        with contextlib.suppress(FileNotFoundError): cancel.unlink()
        with contextlib.suppress(FileNotFoundError):
            (STATE / "references" / f"{request_id}.png").unlink()
        with contextlib.suppress(FileNotFoundError):
            (STATE / "references" / f"{request_id}.provenance.json").unlink()
        if private.exists():
            shutil.rmtree(private)
        if PRIVATE_OUTPUT.exists():
            for partial in PRIVATE_OUTPUT.glob(f"*{request_id}*"):
                with contextlib.suppress(OSError): partial.unlink()


def main() -> int:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    for directory in (STATE / "inbox", STATE / "responses", STATE / "acks", STATE / "records", STATE / "references", STATE / "evidence", STATE / "network", STATE / "process"):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    requests = sorted((STATE / "inbox").glob("*.json"))
    if not requests:
        return 0
    process_request(requests[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
