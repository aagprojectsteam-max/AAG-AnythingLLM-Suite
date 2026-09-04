#!/usr/bin/env python3

import base64
import hashlib
import http.server
import json
import os
import pathlib
import subprocess
import threading
import time
import urllib.parse
import uuid

ROOT = pathlib.Path(
    "/mnt/data/AI/Apps/AnythingLLM/AAG-Upscale-Engine"
).resolve()

BIN = ROOT / "bin" / "upscayl-bin"
MODELS = ROOT / "models"
TMP = ROOT / "tmp"
LOGS = ROOT / "logs"

OUTPUT = pathlib.Path(
    "/mnt/data/AI/Outputs"
).resolve()

COMFY_OUTPUT = pathlib.Path(
    "/mnt/data/AI/Outputs"
).resolve()

HOST = "127.0.0.1"
PORT = 18190

MAX_INPUT = 50 * 1024 * 1024

MODEL_MAP = {
    "standard": "upscayl-standard-4x",
    "upscayl-standard": "upscayl-standard-4x",

    "fast": "upscayl-lite-4x",
    "lite": "upscayl-lite-4x",

    "quality": "high-fidelity-4x",
    "high-fidelity": "high-fidelity-4x",
    "photo": "high-fidelity-4x",

    "digital-art": "digital-art-4x",
    "art": "digital-art-4x",
    "illustration": "digital-art-4x",

    "remacri": "remacri-4x",

    "sharp": "ultrasharp-4x",
    "ultrasharp": "ultrasharp-4x",

    "balanced": "ultramix-balanced-4x",
    "ultramix": "ultramix-balanced-4x",
}

ALL_MODELS = [
    "upscayl-standard-4x",
    "upscayl-lite-4x",
    "high-fidelity-4x",
    "digital-art-4x",
    "remacri-4x",
    "ultrasharp-4x",
    "ultramix-balanced-4x",
]

LOCK = threading.Lock()


def json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def detect_image(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"

    if (
        len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        return ".webp"

    raise ValueError(
        "Input is not a valid JPG, PNG, or WEBP image"
    )


def safe_model(value):
    raw = str(
        value or "standard"
    ).strip().lower()

    if raw in ALL_MODELS:
        model = raw
    else:
        model = MODEL_MAP.get(
            raw,
            "upscayl-standard-4x",
        )

    for suffix in (".bin", ".param"):
        if not (
            MODELS / f"{model}{suffix}"
        ).is_file():
            raise ValueError(
                f"Model file missing: {model}{suffix}"
            )

    return model


def safe_comfy_source(filename, subfolder):
    filename = str(filename or "").strip()
    subfolder = str(subfolder or "").strip()

    if (
        not filename
        or "\0" in filename
        or "/" in filename
        or "\\" in filename
    ):
        raise ValueError(
            "Invalid ComfyUI filename"
        )

    candidate = (
        COMFY_OUTPUT /
        subfolder /
        filename
    ).resolve()

    root = COMFY_OUTPUT.resolve()

    if (
        candidate != root
        and root not in candidate.parents
    ):
        raise ValueError(
            "ComfyUI source resolves outside output root"
        )

    if not candidate.is_file():
        raise FileNotFoundError(
            f"ComfyUI output not found: {candidate}"
        )

    return candidate


def cleanup_empty_parents(start):
    root = COMFY_OUTPUT.resolve()
    current = start.resolve()

    while (
        current != root
        and root in current.parents
    ):
        try:
            current.rmdir()
        except OSError:
            break

        current = current.parent


def unique_destination(name):
    target = OUTPUT / name

    if not target.exists():
        return target

    stem = pathlib.Path(name).stem
    suffix = pathlib.Path(name).suffix

    token = uuid.uuid4().hex[:8]

    return OUTPUT / (
        f"{stem}-{token}{suffix}"
    )


class Handler(
    http.server.BaseHTTPRequestHandler
):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(
            "[AAG-IMAGE-HUB] "
            + (fmt % args),
            flush=True,
        )

    def send_json(self, status, body):
        payload = json_bytes(body)

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(payload)),
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(
            self.path
        )

        if parsed.path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "engine":
                        "AAG Central Image Hub + Upscale Engine",
                    "output":
                        str(OUTPUT),
                    "gpu": 0,
                    "models": ALL_MODELS,
                },
            )
            return

        if parsed.path == "/models":
            self.send_json(
                200,
                {
                    "models": ALL_MODELS,
                },
            )
            return

        if parsed.path.startswith("/files/"):
            name = urllib.parse.unquote(
                parsed.path[
                    len("/files/"):
                ]
            )

            if (
                not name
                or "/" in name
                or "\\" in name
                or ".." in name
            ):
                self.send_error(400)
                return

            file_path = (
                OUTPUT / name
            ).resolve()

            if (
                file_path.parent
                != OUTPUT.resolve()
                or not file_path.is_file()
            ):
                self.send_error(404)
                return

            ext = file_path.suffix.lower()

            types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }

            ctype = types.get(ext)

            if not ctype:
                self.send_error(404)
                return

            data = file_path.read_bytes()

            self.send_response(200)
            self.send_header(
                "Content-Type",
                ctype,
            )
            self.send_header(
                "Content-Length",
                str(len(data)),
            )
            self.send_header(
                "Cache-Control",
                "private, max-age=86400",
            )
            self.send_header(
                "X-Content-Type-Options",
                "nosniff",
            )
            self.end_headers()

            self.wfile.write(data)
            return

        self.send_error(404)

    def do_POST(self):

        # -------------------------------------------------
        # Import a completed ComfyUI image into the
        # one real central output directory.
        # -------------------------------------------------

        if self.path == "/import-comfyui":

            try:
                length = int(
                    self.headers.get(
                        "Content-Length",
                        "0",
                    )
                )

                if (
                    length <= 0
                    or length > 1024 * 1024
                ):
                    raise ValueError(
                        "Invalid request size"
                    )

                body = json.loads(
                    self.rfile
                    .read(length)
                    .decode("utf-8")
                )

                filename = body.get(
                    "filename"
                )

                subfolder = body.get(
                    "subfolder",
                    "",
                )

                image_type = str(
                    body.get(
                        "type",
                        "output",
                    )
                )

                kind = str(
                    body.get(
                        "kind",
                        "GEN",
                    )
                ).upper()

                if image_type != "output":
                    raise ValueError(
                        "Only ComfyUI output images "
                        "may be imported"
                    )

                if kind not in {
                    "GEN",
                    "REF",
                }:
                    raise ValueError(
                        "kind must be GEN or REF"
                    )

                source = safe_comfy_source(
                    filename,
                    subfolder,
                )

                original_name = (
                    source.name
                )

                if not original_name.startswith(
                    f"{kind}-"
                ):
                    final_name = (
                        f"{kind}-"
                        f"{original_name}"
                    )
                else:
                    final_name = (
                        original_name
                    )

                destination = (
                    unique_destination(
                        final_name
                    )
                )

                source_parent = (
                    source.parent
                )

                source.replace(
                    destination
                )

                cleanup_empty_parents(
                    source_parent
                )

                digest = hashlib.sha256(
                    destination.read_bytes()
                ).hexdigest()

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "filename":
                            destination.name,
                        "sha256":
                            digest,
                        "public_path":
                            "/files/"
                            + urllib.parse.quote(
                                destination.name
                            ),
                    },
                )

            except Exception as exc:
                self.send_json(
                    400,
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                )

            return

        # -------------------------------------------------
        # Native AAG Upscale
        # -------------------------------------------------

        if self.path != "/upscale":
            self.send_error(404)
            return

        source = None

        try:
            length = int(
                self.headers.get(
                    "Content-Length",
                    "0",
                )
            )

            if length <= 0:
                raise ValueError(
                    "Empty request body"
                )

            if length > MAX_INPUT * 2:
                raise ValueError(
                    "Request is too large"
                )

            raw = self.rfile.read(
                length
            )

            body = json.loads(
                raw.decode("utf-8")
            )

            encoded = str(
                body.get(
                    "image_base64",
                    "",
                )
            ).strip()

            if encoded.startswith(
                "data:"
            ):
                encoded = encoded.split(
                    ",",
                    1,
                )[-1]

            try:
                image = base64.b64decode(
                    encoded,
                    validate=True,
                )
            except Exception:
                raise ValueError(
                    "Invalid image_base64"
                )

            if len(image) < 128:
                raise ValueError(
                    "Image is empty or invalid"
                )

            if len(image) > MAX_INPUT:
                raise ValueError(
                    "Image exceeds 50 MB limit"
                )

            extension = detect_image(
                image
            )

            scale = int(
                body.get(
                    "scale",
                    4,
                )
            )

            if scale not in {
                2,
                3,
                4,
            }:
                raise ValueError(
                    "scale must be 2, 3, or 4"
                )

            model = safe_model(
                body.get("model")
            )

            token = (
                time.strftime(
                    "%Y%m%d-%H%M%S"
                )
                + "-"
                + uuid.uuid4().hex[:10]
            )

            source = TMP / (
                f"input-{token}"
                f"{extension}"
            )

            output = OUTPUT / (
                f"UPSCALE-{token}.png"
            )

            source.write_bytes(
                image
            )

            command = [
                str(BIN),
                "-i",
                str(source),
                "-o",
                str(output),
                "-m",
                str(MODELS),
                "-n",
                model,
                "-g",
                "0",
                "-s",
                str(scale),
                "-f",
                "png",
                "-v",
            ]

            started = (
                time.monotonic()
            )

            with LOCK:
                process = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=1800,
                    check=False,
                )

            elapsed = round(
                time.monotonic()
                - started,
                3,
            )

            log_file = LOGS / (
                f"job-{token}.log"
            )

            log_file.write_text(
                process.stdout or "",
                encoding="utf-8",
            )

            if source.exists():
                source.unlink()

            if process.returncode != 0:
                raise RuntimeError(
                    "Upscale engine failed; "
                    f"exit={process.returncode}"
                )

            if not output.is_file():
                raise RuntimeError(
                    "Upscale completed "
                    "without output file"
                )

            digest = hashlib.sha256(
                output.read_bytes()
            ).hexdigest()

            self.send_json(
                200,
                {
                    "ok": True,
                    "filename":
                        output.name,
                    "model":
                        model,
                    "scale":
                        scale,
                    "gpu":
                        0,
                    "elapsed_seconds":
                        elapsed,
                    "sha256":
                        digest,
                    "public_path":
                        "/files/"
                        + urllib.parse.quote(
                            output.name
                        ),
                },
            )

        except subprocess.TimeoutExpired:
            self.send_json(
                504,
                {
                    "ok": False,
                    "error":
                        "Upscale job exceeded "
                        "30 minutes",
                },
            )

        except Exception as exc:

            if (
                source is not None
                and source.exists()
            ):
                try:
                    source.unlink()
                except Exception:
                    pass

            self.send_json(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                },
            )


class Server(
    http.server.ThreadingHTTPServer
):
    daemon_threads = True
    allow_reuse_address = True


OUTPUT.mkdir(
    parents=True,
    exist_ok=True,
)

TMP.mkdir(
    parents=True,
    exist_ok=True,
)

LOGS.mkdir(
    parents=True,
    exist_ok=True,
)

print(
    "[AAG-IMAGE-HUB] "
    f"listening on "
    f"http://{HOST}:{PORT}; "
    f"central={OUTPUT}",
    flush=True,
)

with Server(
    (HOST, PORT),
    Handler,
) as server:
    server.serve_forever()
