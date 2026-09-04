#!/usr/bin/env python3

import base64
import calendar
import hashlib
import http.server
import io
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
import warnings

from PIL import Image

ROOT = pathlib.Path(os.environ.get("AAG_UPSCALE_ROOT", "/mnt/data/AI/Apps/AnythingLLM/AAG-Upscale-Engine")).absolute()
BIN = pathlib.Path(os.environ.get("AAG_UPSCALE_BIN", str(ROOT / "bin" / "upscayl-bin"))).absolute()
MODELS = pathlib.Path(os.environ.get("AAG_UPSCALE_MODELS", str(ROOT / "models"))).absolute()
TMP = pathlib.Path(os.environ.get("AAG_UPSCALE_TMP", str(ROOT / "tmp"))).absolute()
LOGS = pathlib.Path(os.environ.get("AAG_UPSCALE_LOGS", str(ROOT / "logs"))).absolute()
OUTPUT = pathlib.Path(os.environ.get("AAG_OUTPUT_ROOT", "/mnt/data/AI/Outputs")).absolute()
SCHEDULER = pathlib.Path(os.environ.get("AAG_XPU_SCHEDULER_ROOT", "/mnt/data/AI/Apps/AnythingLLM/storage/aag-image-agent-state/scheduler")).absolute()
COMFY_QUEUE_URL = os.environ.get("AAG_COMFYUI_QUEUE_URL", "http://127.0.0.1:8188/queue")
HOST = os.environ.get("AAG_UPSCALE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AAG_UPSCALE_PORT", "18191"))
ENGINE_TIMEOUT = int(os.environ.get("AAG_UPSCALE_TIMEOUT_SECONDS", "1800"))
QUEUE_TIMEOUT = int(os.environ.get("AAG_XPU_QUEUE_TIMEOUT_SECONDS", "1800"))
STALE_SECONDS = int(os.environ.get("AAG_XPU_STALE_SECONDS", "120"))
MAX_QUEUE = int(os.environ.get("AAG_XPU_MAX_QUEUE", "8"))
MAX_INPUT = 50 * 1024 * 1024
MAX_OUTPUT = 200 * 1024 * 1024
MAX_PIXELS = 40_000_000
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,239}$")
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

MODEL_MAP = {
    "standard": "upscayl-standard-4x", "upscayl-standard": "upscayl-standard-4x",
    "fast": "upscayl-lite-4x", "lite": "upscayl-lite-4x",
    "quality": "high-fidelity-4x", "high-fidelity": "high-fidelity-4x", "photo": "high-fidelity-4x",
    "digital-art": "digital-art-4x", "art": "digital-art-4x", "illustration": "digital-art-4x",
    "remacri": "remacri-4x", "sharp": "ultrasharp-4x", "ultrasharp": "ultrasharp-4x",
    "balanced": "ultramix-balanced-4x", "ultramix": "ultramix-balanced-4x",
}
ALL_MODELS = ["upscayl-standard-4x", "upscayl-lite-4x", "high-fidelity-4x", "digital-art-4x", "remacri-4x", "ultrasharp-4x", "ultramix-balanced-4x"]
BUSY_LOCK = threading.Lock()
BUSY_COUNT = 0


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def atomic_json(target, value):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        data = json_bytes(value) + b"\n"
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


def decoded_image(data, maximum, expected_size=None):
    if not 128 <= len(data) <= maximum:
        raise ValueError("Image size is invalid")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                image_format = str(image.format or "").upper()
                frames = int(getattr(image, "n_frames", 1))
                image.load()
        if width * height > MAX_PIXELS or frames != 1 or image_format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("Image dimensions, format, or frame count are unsupported")
        if expected_size and (width, height) != expected_size:
            raise ValueError("Upscale output dimensions do not match the requested scale")
        return width, height, image_format
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError) as exc:
        raise ValueError("Image cannot be decoded safely") from exc


def safe_model(value):
    raw = str(value or "standard").strip().lower()
    model = raw if raw in ALL_MODELS else MODEL_MAP.get(raw)
    if not model:
        raise ValueError("Requested upscale model is not approved")
    if not all((MODELS / f"{model}{suffix}").is_file() for suffix in (".bin", ".param")):
        raise RuntimeError("Approved upscale model is unavailable")
    return model


def comfy_active():
    try:
        request = urllib.request.Request(COMFY_QUEUE_URL, headers={"User-Agent": "AAG-XPU-Arbiter/1.0"})
        with urllib.request.urlopen(request, timeout=3) as response:
            body = json.loads(response.read(1024 * 1024).decode("utf-8"))
        return bool(body.get("queue_running") or body.get("queue_pending"))
    except Exception:
        return False


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


def delegated_lease(token):
    owner = lease_owner()
    if not token or not owner or owner.get("token") != token or owner.get("kind") != "agent":
        return False
    heartbeat = owner.get("heartbeat_at") or owner.get("acquired_at")
    try:
        return time.time() - calendar.timegm(time.strptime(heartbeat[:19], "%Y-%m-%dT%H:%M:%S")) <= STALE_SECONDS
    except Exception:
        return False


class ExternalLease:
    def __init__(self):
        self.token = uuid.uuid4().hex
        self.ticket = None
        self.stop = threading.Event()
        self.thread = None

    def acquire(self):
        waiters = SCHEDULER / "waiters"
        waiters.mkdir(parents=True, exist_ok=True, mode=0o700)
        active_waiters = []
        for candidate in sorted(waiters.glob("*.json")):
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
                heartbeat = value.get("heartbeat_at") or value.get("queued_at")
                age = time.time() - calendar.timegm(time.strptime(str(heartbeat)[:19], "%Y-%m-%dT%H:%M:%S"))
                if age > STALE_SECONDS:
                    candidate.unlink()
                    continue
            except Exception:
                pass
            active_waiters.append(candidate)
        if len(active_waiters) >= MAX_QUEUE:
            raise OverflowError("Shared XPU queue is full")
        self.ticket = waiters / f"{next_sequence()}-{uuid.uuid4()}.json"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_json(self.ticket, {"ticket": self.ticket.stem.split("-", 1)[1], "job_id": f"external-upscale-{uuid.uuid4()}", "kind": "external-upscale", "operation": "upscale", "pid": os.getpid(), "queued_at": now, "heartbeat_at": now})
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
                    heartbeat = value.get("heartbeat_at") or value.get("queued_at")
                    age = time.time() - calendar.timegm(time.strptime(str(heartbeat)[:19], "%Y-%m-%dT%H:%M:%S"))
                    if age > STALE_SECONDS:
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
                if owner:
                    try:
                        heartbeat = owner.get("heartbeat_at") or owner.get("acquired_at")
                        parsed = calendar.timegm(time.strptime(heartbeat[:19], "%Y-%m-%dT%H:%M:%S"))
                        stale = time.time() - parsed > STALE_SECONDS
                    except Exception:
                        stale = False
                    with BUSY_LOCK:
                        local_busy = BUSY_COUNT > 0
                    if stale and not local_busy and not comfy_active():
                        try:
                            (lease / "owner.json").unlink()
                            lease.rmdir()
                            continue
                        except OSError:
                            pass
                time.sleep(0.25)
                continue
            if comfy_active():
                time.sleep(0.25)
                continue
            try:
                lease.mkdir(mode=0o700)
            except FileExistsError:
                continue
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            atomic_json(lease / "owner.json", {"schema_version": 1, "token": self.token, "kind": "external-upscale", "pid": os.getpid(), "acquired_at": now, "heartbeat_at": now})
            if comfy_active():
                self.release()
                time.sleep(0.25)
                continue
            try:
                self.ticket.unlink()
            except FileNotFoundError:
                pass
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()
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


def publish_output(source, preferred):
    if not NAME_RE.fullmatch(preferred) or ".." in preferred:
        raise ValueError("Unsafe output filename")
    directory = os.open(OUTPUT, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        stem, suffix = pathlib.Path(preferred).stem, pathlib.Path(preferred).suffix
        for attempt in range(32):
            candidate = preferred if attempt == 0 else f"{stem}-{uuid.uuid4().hex[:12]}{suffix}"
            try:
                os.link(source, candidate, dst_dir_fd=directory, follow_symlinks=False)
                os.chmod(OUTPUT / candidate, 0o664, follow_symlinks=False)
                os.fsync(directory)
                return candidate
            except FileExistsError:
                continue
        raise RuntimeError("Unable to allocate a unique output filename")
    finally:
        os.close(directory)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[AAG-UPSCALE] " + (fmt % args), flush=True)

    def send_json(self, status, body):
        payload = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self.send_error(400)
            return
        if parsed.path == "/health":
            with BUSY_LOCK:
                busy = BUSY_COUNT > 0
            owner = lease_owner()
            self.send_json(200, {"status": "ok", "engine": "AAG Upscale Engine", "gpu": 0, "models": ALL_MODELS, "busy": busy, "shared_owner_kind": owner.get("kind") if owner else None})
            return
        if parsed.path == "/models":
            self.send_json(200, {"models": ALL_MODELS})
            return
        self.send_error(404)

    def do_POST(self):
        global BUSY_COUNT
        if self.path != "/upscale":
            self.send_error(404)
            return
        source = None
        generated = None
        lease = None
        delegated = False
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_INPUT * 2:
                raise ValueError("Request size is invalid")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict) or set(body) - {"image_base64", "scale", "model", "lease_token"}:
                raise ValueError("Request contains unsupported fields")
            encoded = str(body.get("image_base64", "")).strip()
            if encoded.startswith("data:"):
                encoded = encoded.split(",", 1)[-1]
            try:
                image = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ValueError("Invalid image_base64") from exc
            input_width, input_height, image_format = decoded_image(image, MAX_INPUT)
            scale = int(body.get("scale", 4))
            if scale not in {2, 3, 4}:
                raise ValueError("scale must be 2, 3, or 4")
            if input_width * input_height * scale * scale > 200_000_000:
                raise ValueError("Requested upscale exceeds the bounded output pixel limit")
            model = safe_model(body.get("model"))
            token = str(body.get("lease_token") or self.headers.get("X-AAG-Lease-Token") or "").strip()
            delegated = delegated_lease(token)
            if token and not delegated:
                self.send_json(409, {"ok": False, "error": "Shared XPU lease is invalid or expired"})
                return
            if not delegated:
                lease = ExternalLease()
                lease.acquire()
            job_token = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
            extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image_format]
            source = TMP / f"input-{job_token}{extension}"
            generated = TMP / f"output-{job_token}.png"
            source_descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                view = memoryview(image)
                while view:
                    view = view[os.write(source_descriptor, view):]
                os.fsync(source_descriptor)
            finally:
                os.close(source_descriptor)
            command = [str(BIN), "-i", str(source), "-o", str(generated), "-m", str(MODELS), "-n", model, "-g", "0", "-s", str(scale), "-f", "png", "-v"]
            started = time.monotonic()
            with BUSY_LOCK:
                BUSY_COUNT += 1
            try:
                process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=ENGINE_TIMEOUT, check=False, shell=False)
            finally:
                with BUSY_LOCK:
                    BUSY_COUNT -= 1
            elapsed = round(time.monotonic() - started, 3)
            log_file = LOGS / f"job-{job_token}.log"
            log_file.write_text(process.stdout or "", encoding="utf-8")
            os.chmod(log_file, 0o600)
            if process.returncode != 0:
                raise RuntimeError("Upscale engine process failed")
            if not generated.is_file() or generated.is_symlink():
                raise RuntimeError("Upscale engine produced no safe output")
            output_bytes = generated.read_bytes()
            decoded_image(output_bytes, MAX_OUTPUT, (input_width * scale, input_height * scale))
            final_name = publish_output(generated, f"UPSCALE-{job_token}.png")
            digest = hashlib.sha256(output_bytes).hexdigest()
            self.send_json(200, {"ok": True, "filename": final_name, "model": model, "scale": scale, "gpu": 0, "elapsed_seconds": elapsed, "sha256": digest, "public_path": "/files/" + urllib.parse.quote(final_name), "lease_mode": "delegated" if delegated else "external"})
        except subprocess.TimeoutExpired:
            self.send_json(504, {"ok": False, "error": "Upscale job exceeded its bounded timeout"})
        except TimeoutError:
            self.send_json(409, {"ok": False, "error": "Timed out waiting for shared XPU resource"})
        except OverflowError:
            self.send_json(429, {"ok": False, "error": "Shared XPU queue is full"})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception:
            self.send_json(500, {"ok": False, "error": "Upscale engine failed safely"})
        finally:
            for temporary in (source, generated):
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass
            if lease is not None:
                lease.release()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


for directory in (OUTPUT, TMP, LOGS, SCHEDULER / "waiters"):
    directory.mkdir(parents=True, exist_ok=True)
if any(directory.is_symlink() or not directory.is_dir() for directory in (OUTPUT, TMP, LOGS, SCHEDULER)):
    raise SystemExit("A trusted runtime directory is unsafe")
os.chmod(TMP, 0o700)
os.chmod(LOGS, 0o700)

def main():
    print(f"[AAG-UPSCALE] listening on http://{HOST}:{PORT}; shared XPU arbitration enabled", flush=True)
    with Server((HOST, PORT), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
