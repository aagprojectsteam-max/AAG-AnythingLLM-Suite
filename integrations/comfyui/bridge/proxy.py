#!/usr/bin/env python3

import argparse
import hashlib
import hmac
import http.client
import http.server
import json
import os
import pathlib
import re
import signal
import stat
import threading
import time
import urllib.parse
import uuid

try:
    import websocket as websocket_client
except ImportError:  # Fail closed to history/queue observation if unavailable.
    websocket_client = None

MAX_BODY = 16 * 1024 * 1024
SCHEDULER = pathlib.Path(os.environ.get("AAG_XPU_SCHEDULER_ROOT", pathlib.Path.home() / ".local/share/aag-anythingllm-suite/state/image-agent/scheduler")).absolute()
QUEUE_TIMEOUT = int(os.environ.get("AAG_XPU_QUEUE_TIMEOUT_SECONDS", "1800"))
STALE_SECONDS = int(os.environ.get("AAG_XPU_STALE_SECONDS", "120"))
MAX_QUEUE = int(os.environ.get("AAG_XPU_MAX_QUEUE", "8"))
HOP_HEADERS = {"connection", "proxy-connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer"}
PROMPT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9._~-]{1,160}$")
ENGINE_PROGRESS_LOCK = threading.Lock()
STALL_SECONDS = {
    "fast": {"load": 300, "sample": 180, "output": 180, "default": 300},
    "quality": {"load": 600, "sample": 360, "output": 240, "default": 600},
}


def atomic_json(target, value):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        data = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, target)
    os.chmod(target, 0o600)


def next_sequence():
    directory = SCHEDULER / "sequence"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    while True:
        values = [int(item.name) for item in directory.iterdir() if item.is_dir() and len(item.name) == 20 and item.name.isdigit()]
        value = (max(values) if values else 0) + 1
        marker = directory / f"{value:020d}"
        try:
            marker.mkdir(mode=0o700)
            return marker.name
        except FileExistsError:
            continue


def lease_owner():
    descriptor = None
    try:
        descriptor = os.open(SCHEDULER / "lease" / "owner.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 64 * 1024:
            return None
        chunks = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = json.loads(b"".join(chunks).decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def regular_json(target, maximum=256 * 1024):
    descriptor = None
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            return None
        data = b""
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            data += chunk
            remaining -= len(chunk)
        value = json.loads(data.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def safe_prompt_id(value):
    value = str(value or "").lower()
    return value if PROMPT_ID.fullmatch(value) else None


def binding_file(prompt_id):
    prompt_id = safe_prompt_id(prompt_id)
    return SCHEDULER / "engine-bindings" / f"{prompt_id}.json" if prompt_id else None


def progress_file(prompt_id):
    prompt_id = safe_prompt_id(prompt_id)
    return SCHEDULER / "engine-progress" / f"{prompt_id}.json" if prompt_id else None


def token_sha256(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def workflow_class(graph):
    unets = {
        str(node.get("inputs", {}).get("unet_name", ""))
        for node in (graph.values() if isinstance(graph, dict) else [])
        if isinstance(node, dict) and node.get("class_type") == "UNETLoader"
    }
    if unets == {"flux-2-klein-9b-fp8.safetensors"}:
        return "quality"
    if unets == {"flux-2-klein-4b-fp8.safetensors"}:
        return "fast"
    return "unknown"


def write_binding(prompt_id, client_id, token, owner, graph):
    prompt_id = safe_prompt_id(prompt_id)
    client_id = str(client_id or "")
    if not prompt_id or not CLIENT_ID.fullmatch(client_id) or not owner or owner.get("kind") != "agent":
        raise ValueError("Unsafe delegated ComfyUI binding")
    node_classes = {}
    for node_id, node in (graph.items() if isinstance(graph, dict) else []):
        node_id = str(node_id)
        class_type = str(node.get("class_type", "")) if isinstance(node, dict) else ""
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", node_id) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", class_type):
            node_classes[node_id] = class_type
    value = {
        "schema_version": "aag.comfy-engine-binding.v1",
        "prompt_id": prompt_id,
        "client_id": client_id,
        "job_id": str(owner.get("job_id", ""))[:80],
        "lease_token_sha256": token_sha256(token),
        "workflow_class": workflow_class(graph),
        "node_classes": node_classes,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    atomic_json(binding_file(prompt_id), value)
    return value


def authorized_binding(prompt_id, token):
    target = binding_file(prompt_id)
    binding = regular_json(target) if target else None
    owner = lease_owner()
    if not binding or not owner or not valid_delegated(token):
        return None
    if owner.get("kind") != "agent" or str(owner.get("job_id", "")) != str(binding.get("job_id", "")):
        return None
    if not hmac.compare_digest(token_sha256(token), str(binding.get("lease_token_sha256", ""))):
        return None
    return binding


def engine_phase(progress):
    class_name = str(progress.get("current_engine_node_class", ""))
    if re.search(r"Sampler|KSampler", class_name, re.I):
        return "sample"
    if re.search(r"VAEDecode|SaveImage|PreviewImage|Output", class_name, re.I):
        return "output"
    if re.search(r"Loader|LoadImage|Scale|Encode|Conditioning|ReferenceLatent|Scheduler|Noise|Latent", class_name, re.I):
        return "load"
    return "default"


def stalled_progress_evidence(binding, progress):
    workflow = str(binding.get("workflow_class", ""))
    profile = STALL_SECONDS.get(workflow)
    if not profile or not isinstance(progress, dict):
        return False
    if progress.get("schema_version") != "aag.comfy-engine-progress.v1":
        return False
    if str(progress.get("prompt_id", "")) != str(binding.get("prompt_id", "")):
        return False
    if progress.get("terminal_event"):
        return False
    if str(progress.get("last_engine_progress_event", "")) not in {"node_started", "sampler_step", "node_completed"}:
        return False
    if not progress.get("current_engine_node") or not progress.get("current_engine_node_class"):
        return False
    # A live observer heartbeat proves that silence means no engine event, not
    # merely a broken monitoring connection. It never counts as progress.
    if progress.get("observer_status") != "connected":
        return False
    observer_age = iso_age(progress.get("observer_heartbeat_at"))
    progress_age = iso_age(progress.get("last_engine_progress_at"))
    if observer_age is None or observer_age > 15 or progress_age is None:
        return False
    threshold = profile.get(engine_phase(progress), profile["default"])
    return progress_age > threshold


def update_engine_progress(prompt_id, fields=None, real_progress=False):
    target = progress_file(prompt_id)
    if target is None:
        return None
    with ENGINE_PROGRESS_LOCK:
        current = regular_json(target) or {
            "schema_version": "aag.comfy-engine-progress.v1",
            "prompt_id": safe_prompt_id(prompt_id),
            "sequence": 0,
        }
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        current.update(fields or {})
        current["observer_heartbeat_at"] = now
        if real_progress:
            current["sequence"] = int(current.get("sequence", 0)) + 1
            current["last_engine_progress_at"] = now
        current["updated_at"] = now
        atomic_json(target, current)
        return current


def target_json(host, port, method, route, body=None, timeout=10):
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    connection.request(method, route, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read(MAX_BODY)
    status = response.status
    connection.close()
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        value = {}
    return status, value


def prompt_in_history(host, port, prompt_id):
    try:
        status, value = target_json(host, port, "GET", f"/history/{urllib.parse.quote(prompt_id)}")
        return value.get(prompt_id) if status == 200 and isinstance(value, dict) else None
    except Exception:
        return None


def apply_engine_event(binding, message):
    if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
        return False
    event_type = str(message.get("type", ""))
    data = message["data"]
    prompt_id = binding["prompt_id"]
    event_prompt = str(data.get("prompt_id", ""))
    if event_prompt and event_prompt != prompt_id:
        return False
    current = regular_json(progress_file(prompt_id)) or {}
    node = str(data.get("node") or data.get("node_id") or "")
    node_class = str(binding.get("node_classes", {}).get(node, ""))
    fields = {"last_engine_event_type": event_type[:80]}
    progress = False
    if event_type == "execution_start" and current.get("last_engine_progress_event") != "execution_start":
        fields.update({"last_engine_progress_event": "execution_start", "execution_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        progress = True
    elif event_type == "executing":
        if data.get("node") is None:
            fields.update({"last_engine_progress_event": "execution_complete", "terminal_event": "execution_complete"})
            progress = True
        elif node and node != str(current.get("current_engine_node", "")):
            fields.update({"last_engine_progress_event": "node_started", "current_engine_node": node, "current_engine_node_class": node_class, "current_engine_step": 0, "current_engine_step_max": 0})
            progress = True
    elif event_type in {"progress", "progress_state"}:
        value = int(data.get("value", 0) or 0)
        maximum = int(data.get("max", 0) or 0)
        prior_node = str(current.get("current_engine_node", ""))
        prior_value = int(current.get("current_engine_step", 0) or 0)
        if node and (node != prior_node or value > prior_value):
            fields.update({"last_engine_progress_event": "sampler_step", "current_engine_node": node, "current_engine_node_class": node_class, "current_engine_step": value, "current_engine_step_max": maximum})
            progress = True
    elif event_type in {"executed", "execution_cached"}:
        completed_node = node or str(data.get("display_node", ""))
        if completed_node and completed_node != str(current.get("last_completed_node", "")):
            fields.update({"last_engine_progress_event": "node_completed", "last_completed_node": completed_node, "current_engine_node": completed_node, "current_engine_node_class": str(binding.get("node_classes", {}).get(completed_node, ""))})
            progress = True
    elif event_type in {"execution_success", "execution_error", "execution_interrupted"}:
        fields.update({"last_engine_progress_event": event_type, "terminal_event": event_type})
        progress = True
    update_engine_progress(prompt_id, fields, progress)
    return bool(fields.get("terminal_event"))


def monitor_engine_events(binding, target_host, target_port):
    prompt_id = binding["prompt_id"]
    deadline = time.monotonic() + 30 * 60
    last_history_check = 0.0
    while time.monotonic() < deadline:
        if prompt_in_history(target_host, target_port, prompt_id):
            update_engine_progress(prompt_id, {"last_engine_progress_event": "history_appeared", "terminal_event": "history_appeared"}, True)
            return
        if websocket_client is None:
            update_engine_progress(prompt_id, {"observer_status": "websocket_unavailable"})
            return
        connection = None
        try:
            route = f"ws://{target_host}:{target_port}/ws?clientId={urllib.parse.quote(binding['client_id'])}"
            connection = websocket_client.create_connection(route, timeout=5)
            connection.settimeout(5)
            update_engine_progress(prompt_id, {"observer_status": "connected", "observer_connected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            while time.monotonic() < deadline:
                try:
                    raw = connection.recv()
                    if isinstance(raw, bytes):
                        continue
                    message = json.loads(raw)
                    if apply_engine_event(binding, message):
                        return
                except websocket_client.WebSocketTimeoutException:
                    update_engine_progress(prompt_id, {"observer_status": "connected"})
                if time.monotonic() - last_history_check >= 10:
                    last_history_check = time.monotonic()
                    if prompt_in_history(target_host, target_port, prompt_id):
                        update_engine_progress(prompt_id, {"last_engine_progress_event": "history_appeared", "terminal_event": "history_appeared"}, True)
                        return
        except Exception as error:
            update_engine_progress(prompt_id, {"observer_status": "reconnecting", "observer_error": type(error).__name__[:80]})
            time.sleep(1)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
    update_engine_progress(prompt_id, {"observer_status": "deadline_reached"})


def iso_age(value):
    try:
        parsed = time.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S")
        return time.time() - __import__("calendar").timegm(parsed)
    except Exception:
        return None


def valid_delegated(token):
    owner = lease_owner()
    age = iso_age(owner.get("heartbeat_at") or owner.get("acquired_at")) if owner else None
    return bool(token and owner and owner.get("kind") == "agent" and owner.get("token") == token and age is not None and age <= STALE_SECONDS)


class ExternalComfyLease:
    def __init__(self, target_host, target_port):
        self.target_host = target_host
        self.target_port = target_port
        self.token = uuid.uuid4().hex
        self.ticket = None
        self.stop = threading.Event()

    def engine_active(self):
        try:
            connection = http.client.HTTPConnection(self.target_host, self.target_port, timeout=3)
            connection.request("GET", "/queue", headers={"User-Agent": "AAG-XPU-Arbiter/1.0"})
            response = connection.getresponse()
            body = json.loads(response.read(MAX_BODY).decode("utf-8"))
            connection.close()
            return bool(body.get("queue_running") or body.get("queue_pending"))
        except Exception:
            return False

    def acquire(self):
        waiters = SCHEDULER / "waiters"
        waiters.mkdir(parents=True, exist_ok=True, mode=0o700)
        active_waiters = []
        for candidate in sorted(waiters.glob("*.json")):
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
                age = iso_age(value.get("heartbeat_at") or value.get("queued_at"))
                if age is not None and age > STALE_SECONDS:
                    candidate.unlink()
                    continue
            except Exception:
                pass
            active_waiters.append(candidate)
        if len(active_waiters) >= MAX_QUEUE:
            raise OverflowError("Shared XPU queue is full")
        self.ticket = waiters / f"{next_sequence()}-{uuid.uuid4()}.json"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_json(self.ticket, {"ticket": self.ticket.stem.split("-", 1)[1], "job_id": f"external-comfy-{uuid.uuid4()}", "kind": "external-comfyui", "pid": os.getpid(), "queued_at": now, "heartbeat_at": now})
        deadline = time.monotonic() + QUEUE_TIMEOUT
        lease = SCHEDULER / "lease"
        while time.monotonic() < deadline:
            ticket_value = json.loads(self.ticket.read_text(encoding="utf-8"))
            ticket_value["heartbeat_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            atomic_json(self.ticket, ticket_value)
            ordered = []
            for candidate in sorted(waiters.glob("*.json")):
                try:
                    value = json.loads(candidate.read_text(encoding="utf-8"))
                    age = iso_age(value.get("heartbeat_at") or value.get("queued_at"))
                    if age is not None and age > STALE_SECONDS:
                        candidate.unlink()
                        continue
                except Exception:
                    pass
                ordered.append(candidate)
            if not ordered or ordered[0] != self.ticket:
                time.sleep(0.25)
                continue
            if lease.exists():
                owner = lease_owner()
                age = iso_age(owner.get("heartbeat_at") or owner.get("acquired_at")) if owner else None
                if owner and age is not None and age > STALE_SECONDS and not self.engine_active():
                    try:
                        (lease / "owner.json").unlink()
                        lease.rmdir()
                        continue
                    except OSError:
                        pass
                time.sleep(0.25)
                continue
            if self.engine_active():
                time.sleep(0.25)
                continue
            try:
                lease.mkdir(mode=0o700)
            except FileExistsError:
                continue
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            atomic_json(lease / "owner.json", {"schema_version": 1, "token": self.token, "kind": "external-comfyui", "pid": os.getpid(), "acquired_at": now, "heartbeat_at": now})
            if self.engine_active():
                self.release()
                time.sleep(0.25)
                continue
            try:
                self.ticket.unlink()
            except FileNotFoundError:
                pass
            threading.Thread(target=self._heartbeat, daemon=True).start()
            return
        raise TimeoutError("Timed out waiting for shared XPU resource")

    def _heartbeat(self):
        while not self.stop.wait(max(1, STALE_SECONDS // 3)):
            owner = lease_owner()
            if not owner or owner.get("token") != self.token:
                return
            owner["heartbeat_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            atomic_json(SCHEDULER / "lease" / "owner.json", owner)

    def release(self):
        self.stop.set()
        owner = lease_owner()
        if owner and owner.get("token") == self.token:
            try:
                (SCHEDULER / "lease" / "owner.json").unlink()
                (SCHEDULER / "lease").rmdir()
            except FileNotFoundError:
                pass
        if self.ticket:
            try:
                self.ticket.unlink()
            except FileNotFoundError:
                pass


def monitor_prompt(lease, target_host, target_port, prompt_id):
    deadline = time.monotonic() + 30 * 60
    seen_in_queue = False
    missing_after_seen = 0
    try:
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection(target_host, target_port, timeout=10)
            connection.request("GET", f"/history/{urllib.parse.quote(prompt_id)}")
            response = connection.getresponse()
            data = response.read(MAX_BODY)
            connection.close()
            if response.status == 200:
                body = json.loads(data.decode("utf-8"))
                if prompt_id in body:
                    return
            connection = http.client.HTTPConnection(target_host, target_port, timeout=10)
            connection.request("GET", "/queue")
            response = connection.getresponse()
            queue = json.loads(response.read(MAX_BODY).decode("utf-8"))
            connection.close()
            identifiers = []
            for item in list(queue.get("queue_running") or []) + list(queue.get("queue_pending") or []):
                if isinstance(item, list) and len(item) > 1:
                    identifiers.append(str(item[1]))
            if prompt_id in identifiers:
                seen_in_queue = True
                missing_after_seen = 0
            elif seen_in_queue:
                missing_after_seen += 1
                if missing_after_seen >= 3:
                    return
            time.sleep(1)
    except Exception:
        pass
    finally:
        lease.release()


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    target_host = None
    target_port = None

    def log_message(self, fmt, *args):
        print("[AAG-COMFYUI-BRIDGE] " + (fmt % args), flush=True)

    def send_json(self, status, value):
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def governed_progress(self, parsed):
        prompt_id = safe_prompt_id(parsed.path.rsplit("/", 1)[-1])
        token = self.headers.get("X-AAG-Lease-Token", "").strip()
        binding = authorized_binding(prompt_id, token) if prompt_id else None
        if not binding:
            self.send_json(403, {"ok": False, "error": "authorized engine binding required"})
            return
        progress = regular_json(progress_file(prompt_id))
        if not progress:
            self.send_json(404, {"ok": False, "error": "engine progress unavailable"})
            return
        allowed = {
            key: progress[key]
            for key in (
                "schema_version", "prompt_id", "job_id", "sequence",
                "last_engine_progress_at", "last_engine_progress_event",
                "last_engine_event_type", "current_engine_node",
                "current_engine_node_class", "current_engine_step",
                "current_engine_step_max", "last_completed_node",
                "execution_started_at", "terminal_event", "observer_status",
                "observer_heartbeat_at", "updated_at", "stall_detected_at",
                "recovery_action", "recovery_started_at", "recovery_completed_at",
                "recovery_outcome",
            )
            if key in progress
        }
        allowed["job_id"] = binding.get("job_id")
        allowed["ok"] = True
        self.send_json(200, allowed)

    def governed_interrupt(self, parsed, body):
        token = self.headers.get("X-AAG-Lease-Token", "").strip()
        try:
            request = json.loads((body or b"{}").decode("utf-8"))
        except Exception:
            self.send_json(400, {"ok": False, "error": "invalid recovery request"})
            return
        if not isinstance(request, dict) or set(request) != {"prompt_id"}:
            self.send_json(400, {"ok": False, "error": "exact prompt identifier required"})
            return
        prompt_id = safe_prompt_id(request.get("prompt_id"))
        binding = authorized_binding(prompt_id, token) if prompt_id else None
        if not binding:
            self.send_json(403, {"ok": False, "error": "authorized engine binding required"})
            return
        progress = regular_json(progress_file(prompt_id))
        if not stalled_progress_evidence(binding, progress):
            self.send_json(200, {
                "ok": False,
                "prompt_id": prompt_id,
                "action": "INTERRUPT_WITHHELD_PROGRESS_CHANGED",
            })
            return
        status, queue = target_json(self.target_host, self.target_port, "GET", "/queue")
        running = [
            str(item[1]) for item in (queue.get("queue_running") or [])
            if isinstance(item, list) and len(item) > 1
        ] if status == 200 and isinstance(queue, dict) else []
        if running != [prompt_id] or prompt_in_history(self.target_host, self.target_port, prompt_id):
            self.send_json(409, {"ok": False, "error": "exclusive running prompt ownership not proven"})
            return
        # Close the race between the original watchdog observation and the
        # destructive interrupt boundary. Any new real event withholds cancel.
        confirmed = regular_json(progress_file(prompt_id))
        if (
            not stalled_progress_evidence(binding, confirmed)
            or int(confirmed.get("sequence", -1)) != int(progress.get("sequence", -2))
            or confirmed.get("last_engine_progress_at") != progress.get("last_engine_progress_at")
        ):
            self.send_json(200, {
                "ok": False,
                "prompt_id": prompt_id,
                "action": "INTERRUPT_WITHHELD_PROGRESS_CHANGED",
            })
            return
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        update_engine_progress(prompt_id, {
            "stall_detected_at": started,
            "recovery_action": "INTERRUPT_REQUESTED",
            "recovery_started_at": started,
        })
        interrupt_status, _ = target_json(
            self.target_host,
            self.target_port,
            "POST",
            "/interrupt",
            {"prompt_id": prompt_id},
        )
        if not 200 <= interrupt_status < 300:
            update_engine_progress(prompt_id, {"recovery_outcome": "INTERRUPT_REJECTED"})
            self.send_json(502, {"ok": False, "error": "exact prompt interrupt rejected"})
            return
        self.send_json(200, {"ok": True, "prompt_id": prompt_id, "action": "INTERRUPT_REQUESTED"})

    def proxy(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            self.send_error(400)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        if self.command == "GET" and parsed.path.startswith("/aag/engine-progress/"):
            self.governed_progress(parsed)
            return
        if self.command == "POST" and parsed.path == "/aag/interrupt":
            self.governed_interrupt(parsed, body)
            return
        lease = None
        delegated = False
        delegated_token = ""
        delegated_binding = None
        delegated_request = None
        if self.command == "POST" and parsed.path == "/prompt":
            token = self.headers.get("X-AAG-Lease-Token", "").strip()
            delegated = valid_delegated(token)
            if token and not delegated:
                self.send_error(409, "Invalid shared XPU lease")
                return
            if not delegated:
                lease = ExternalComfyLease(self.target_host, self.target_port)
                try:
                    lease.acquire()
                except OverflowError:
                    self.send_error(429, "Shared XPU queue is full")
                    return
                except TimeoutError:
                    self.send_error(409, "Shared XPU resource timeout")
                    return
            else:
                delegated_token = token
                try:
                    delegated_request = json.loads((body or b"{}").decode("utf-8"))
                except Exception:
                    delegated_request = None
                if (
                    not isinstance(delegated_request, dict)
                    or not isinstance(delegated_request.get("prompt"), dict)
                    or not CLIENT_ID.fullmatch(str(delegated_request.get("client_id", "")))
                ):
                    self.send_error(400, "Bound client and workflow are required")
                    return
        headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS | {"host", "content-length", "x-aag-lease-token"}}
        try:
            connection = http.client.HTTPConnection(self.target_host, self.target_port, timeout=180)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read(MAX_BODY + 1)
            response_headers = list(response.getheaders())
            connection.close()
            if len(response_body) > MAX_BODY:
                raise RuntimeError("ComfyUI response exceeds bridge limit")
            prompt_id = None
            if (lease is not None or delegated) and 200 <= response.status < 300:
                try:
                    prompt_id = json.loads(response_body.decode("utf-8")).get("prompt_id")
                except Exception:
                    prompt_id = None
                if not prompt_id:
                    raise RuntimeError("ComfyUI did not return prompt_id")
                owner = lease_owner()
                if lease is not None and owner and owner.get("token") == lease.token:
                    owner["prompt_id"] = prompt_id
                    owner["heartbeat_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    atomic_json(SCHEDULER / "lease" / "owner.json", owner)
            if delegated and prompt_id:
                try:
                    owner = lease_owner()
                    delegated_binding = write_binding(
                        prompt_id,
                        delegated_request.get("client_id"),
                        delegated_token,
                        owner,
                        delegated_request.get("prompt"),
                    )
                    update_engine_progress(prompt_id, {
                        "job_id": delegated_binding.get("job_id"),
                        "last_engine_progress_event": "prompt_submitted",
                        "observer_status": "starting",
                    }, True)
                except Exception as error:
                    try:
                        _, queue = target_json(self.target_host, self.target_port, "GET", "/queue")
                        running = [
                            str(item[1]) for item in (queue.get("queue_running") or [])
                            if isinstance(item, list) and len(item) > 1
                        ]
                        if running == [str(prompt_id)]:
                            target_json(self.target_host, self.target_port, "POST", "/interrupt", {"prompt_id": prompt_id})
                    except Exception:
                        pass
                    raise RuntimeError("Delegated engine event binding failed") from error
            self.send_response(response.status, response.reason)
            for key, value in response_headers:
                if key.lower() not in HOP_HEADERS | {"content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("X-AAG-XPU-Arbitrated", "delegated" if delegated else "external" if lease else "read-only")
            self.end_headers()
            self.wfile.write(response_body)
            if lease is not None:
                threading.Thread(target=monitor_prompt, args=(lease, self.target_host, self.target_port, prompt_id), daemon=True).start()
                lease = None
            elif delegated_binding is not None:
                threading.Thread(
                    target=monitor_engine_events,
                    args=(delegated_binding, self.target_host, self.target_port),
                    daemon=True,
                ).start()
        except Exception:
            if not self.wfile.closed:
                try:
                    self.send_error(502, "Trusted ComfyUI bridge failed safely")
                except Exception:
                    pass
        finally:
            if lease is not None:
                lease.release()

    do_GET = proxy
    do_POST = proxy


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    args = parser.parse_args()
    SCHEDULER.mkdir(parents=True, exist_ok=True, mode=0o700)
    if SCHEDULER.is_symlink() or not SCHEDULER.is_dir():
        raise SystemExit("Shared scheduler root is unsafe")
    ProxyHandler.target_host = args.target_host
    ProxyHandler.target_port = args.target_port
    server = Server((args.listen_host, args.listen_port), ProxyHandler)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"[AAG-COMFYUI-BRIDGE] listening on {args.listen_host}:{args.listen_port}; forwarding to {args.target_host}:{args.target_port}; shared XPU arbitration enabled", flush=True)
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
