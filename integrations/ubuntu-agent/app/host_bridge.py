#!/usr/bin/env python3

import json
import os
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer
from pathlib import Path


ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")
SOCKET = Path(
    "/mnt/data/AI/Apps/AnythingLLM/storage/"
    "aag-ubuntu-agent/host-bridge.sock"
)
LIVE_TOOL = ROOT / "tools/live_audit.py"

ALLOWED_PROFILES = {
    "overview",
    "storage",
    "services",
    "docker",
    "network",
    "otzar",
}

MAX_RESPONSE_BYTES = 100_000


class Handler(BaseHTTPRequestHandler):

    server_version = "AAGUbuntuBridge/1.0"

    def log_message(self, fmt, *args):
        # Keep normal HTTP noise out of stdout.
        return

    def send_json(self, code, obj):
        raw = json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        if len(raw) > MAX_RESPONSE_BYTES:
            raw = json.dumps(
                {
                    "error": "response_too_large",
                    "message": "Audit output exceeded bridge limit",
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

        if parsed.path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "service": "aag-ubuntu-agent-bridge",
                    "mode": "READ_ONLY",
                    "profiles": sorted(ALLOWED_PROFILES),
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
                env={
                    **os.environ,
                    "LC_ALL": "C",
                    "LANG": "C",
                },
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

    try:
        SOCKET.unlink()
    except FileNotFoundError:
        pass

    server = UnixStreamServer(
        str(SOCKET),
        Handler,
    )

    # The socket contains READ-ONLY diagnostic access only.
    # It must be reachable from the AnythingLLM container,
    # which sees the same bind-mounted storage directory.
    os.chmod(SOCKET, 0o666)

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
