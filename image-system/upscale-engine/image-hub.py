#!/usr/bin/env python3

import errno
import calendar
import hashlib
import http.server
import io
import json
import os
import pathlib
import re
import stat
import urllib.parse
import urllib.request
import uuid
import warnings
import time

from PIL import Image

DATA_HOME = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local/share"))
OUTPUT = pathlib.Path(os.environ.get("AAG_OUTPUT_ROOT", DATA_HOME / "aag-anythingllm-suite/outputs")).absolute()
SCHEDULER = pathlib.Path(os.environ.get("AAG_XPU_SCHEDULER_ROOT", DATA_HOME / "aag-anythingllm-suite/state/image-agent/scheduler")).absolute()
STALE_SECONDS = int(os.environ.get("AAG_XPU_STALE_SECONDS", "120"))
HOST = os.environ.get("AAG_IMAGE_HUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("AAG_IMAGE_HUB_PORT", "18190"))
COMFY_BASE = os.environ.get("AAG_COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/")
MAX_IMAGE_BYTES = 200 * 1024 * 1024
MAX_PIXELS = 40_000_000
ALLOWED_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,239}$")
SUBFOLDER_RE = re.compile(r"^(?:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)?$")
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def safe_name(value):
    name = str(value or "").strip()
    if not NAME_RE.fullmatch(name) or ".." in name or pathlib.Path(name).suffix.lower() not in ALLOWED_TYPES:
        raise ValueError("Invalid image filename")
    return name


def valid_agent_lease(token):
    if not token:
        return False
    owner_path = SCHEDULER / "lease" / "owner.json"
    descriptor = None
    try:
        descriptor = os.open(owner_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
            return False
        chunks = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        owner = json.loads(b"".join(chunks).decode("utf-8"))
        heartbeat = str(owner.get("heartbeat_at") or owner.get("acquired_at") or "")
        age = time.time() - calendar.timegm(time.strptime(heartbeat[:19], "%Y-%m-%dT%H:%M:%S"))
        return owner.get("kind") == "agent" and owner.get("token") == token and 0 <= age <= STALE_SECONDS
    except Exception:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validate_image(data, expected_content_type=None):
    if not 128 <= len(data) <= MAX_IMAGE_BYTES:
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
        if not width or not height or width * height > MAX_PIXELS or frames != 1:
            raise ValueError("Image dimensions or frame count are unsupported")
        content_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image_format)
        if not content_type or (expected_content_type and expected_content_type != content_type):
            raise ValueError("Image content type does not match decoded content")
        return content_type
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError) as exc:
        raise ValueError("Image cannot be decoded safely") from exc


def output_dir_fd():
    return os.open(OUTPUT, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))


def read_output(name):
    name = safe_name(name)
    directory = output_dir_fd()
    descriptor = None
    try:
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 128 or info.st_size > MAX_IMAGE_BYTES:
            raise ValueError("Output is not a safe regular image")
        chunks = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        validate_image(data, ALLOWED_TYPES[pathlib.Path(name).suffix.lower()])
        return data
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def publish_exclusive(preferred, data):
    preferred = safe_name(preferred)
    content_type = validate_image(data)
    decoded_suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[content_type]
    if pathlib.Path(preferred).suffix.lower() == ".jpeg" and decoded_suffix == ".jpg":
        decoded_suffix = ".jpeg"
    if ALLOWED_TYPES[pathlib.Path(preferred).suffix.lower()] != content_type:
        preferred = f"{pathlib.Path(preferred).stem}{decoded_suffix}"
    directory = output_dir_fd()
    temporary = f".aag-write-{uuid.uuid4().hex}.tmp"
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o664, dir_fd=directory)
        os.fchmod(descriptor, 0o664)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        stem, suffix = pathlib.Path(preferred).stem, pathlib.Path(preferred).suffix
        for attempt in range(32):
            candidate = preferred if attempt == 0 else f"{stem}-{uuid.uuid4().hex[:12]}{suffix}"
            try:
                os.link(temporary, candidate, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.fsync(directory)
                return candidate
            except FileExistsError:
                try:
                    if read_output(candidate) == data:
                        return candidate
                except (OSError, ValueError):
                    pass
        raise RuntimeError("Unable to allocate a unique output filename")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def fetch_comfy_image(filename, subfolder):
    filename = safe_name(filename)
    subfolder = str(subfolder or "").strip().strip("/")
    if not SUBFOLDER_RE.fullmatch(subfolder):
        raise ValueError("Invalid ComfyUI image subfolder")
    query = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": "output"})
    request = urllib.request.Request(f"{COMFY_BASE}/view?{query}", method="GET", headers={"User-Agent": "AAG-Image-Hub/2.1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            declared = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if declared not in set(ALLOWED_TYPES.values()):
                raise ValueError("ComfyUI returned an unsupported image type")
            data = response.read(MAX_IMAGE_BYTES + 1)
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError("Unable to fetch the completed ComfyUI artifact") from exc
    validate_image(data, declared)
    return data


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[AAG-IMAGE-HUB] " + (fmt % args), flush=True)

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
            self.send_json(200, {"status": "ok", "service": "AAG Central Image Hub", "upscale": False, "write_policy": "exclusive-nofollow"})
            return
        if parsed.path.startswith("/files/"):
            try:
                name = safe_name(urllib.parse.unquote(parsed.path[len("/files/"):]))
                data = read_output(name)
                content_type = ALLOWED_TYPES[pathlib.Path(name).suffix.lower()]
            except (OSError, ValueError):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/import-comfyui":
            self.send_error(404)
            return
        if not valid_agent_lease(self.headers.get("X-AAG-Lease-Token", "").strip()):
            self.send_json(403, {"ok": False, "error": "A valid shared XPU lease is required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict) or set(body) - {"filename", "subfolder", "type", "kind"}:
                raise ValueError("Invalid request fields")
            filename = safe_name(body.get("filename"))
            if str(body.get("type", "output")) != "output":
                raise ValueError("Only ComfyUI output images may be imported")
            kind = str(body.get("kind", "GEN")).upper()
            if kind not in {"GEN", "REF"}:
                raise ValueError("kind must be GEN or REF")
            data = fetch_comfy_image(filename, body.get("subfolder", ""))
            preferred = filename if filename.startswith(f"{kind}-") else f"{kind}-{filename}"
            final_name = publish_exclusive(preferred, data)
            self.send_json(200, {"ok": True, "filename": final_name, "sha256": hashlib.sha256(data).hexdigest(), "public_path": "/files/" + urllib.parse.quote(final_name)})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception:
            self.send_json(502, {"ok": False, "error": "Trusted image import failed safely"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


OUTPUT.mkdir(parents=True, exist_ok=True)
if OUTPUT.is_symlink() or not OUTPUT.is_dir():
    raise SystemExit("Output root is not a safe directory")

def main():
    print(f"[AAG-IMAGE-HUB] listening on http://{HOST}:{PORT}; exclusive no-follow output policy enabled", flush=True)
    with Server((HOST, PORT), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
