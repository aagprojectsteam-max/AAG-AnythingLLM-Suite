#!/usr/bin/env python3

import base64
import contextlib
import hashlib
import http.client
import http.server
import importlib.util
import io
import json
import os
import pathlib
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error
import uuid
import zlib

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name, source, environment):
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update({key: str(value) for key, value in environment.items()})
    try:
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def png_bytes(size=(32, 32), color=(20, 40, 60)):
    stream = io.BytesIO()
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = ((color[0] + x * 13 + y * 7) % 256, (color[1] + x * 3 + y * 17) % 256, (color[2] + x * 19 + y * 5) % 256)
    image.save(stream, format="PNG")
    return stream.getvalue()


def bomb_header_fixture(width=10000, height=5000):
    data = bytearray(png_bytes())
    data[16:20] = width.to_bytes(4, "big")
    data[20:24] = height.to_bytes(4, "big")
    data[29:33] = (zlib.crc32(bytes(data[12:29])) & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(data)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(port, path="/health", timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=0.3) as response:
                return response.read()
        except Exception:
            time.sleep(0.05)
    raise TimeoutError(f"server {port} did not become ready")


class ImageHubSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aag-hub-test-")
        self.output = pathlib.Path(self.temp.name) / "output"
        self.scheduler = pathlib.Path(self.temp.name) / "scheduler"
        self.output.mkdir(mode=0o775)
        self.hub = load_module(
            f"aag_hub_{uuid.uuid4().hex}",
            ROOT / "integrations/upscale-engine/image-hub.py",
            {"AAG_OUTPUT_ROOT": self.output, "AAG_XPU_SCHEDULER_ROOT": self.scheduler, "AAG_IMAGE_HUB_PORT": free_port()},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_safe_decode_and_bounded_decompression_fixture(self):
        self.assertEqual(self.hub.validate_image(png_bytes()), "image/png")
        with self.assertRaises(ValueError):
            self.hub.validate_image(b"\x89PNG\r\n\x1a\n" + b"x" * 120)
        with self.assertRaises(ValueError):
            self.hub.validate_image(bomb_header_fixture())

    def test_exclusive_publish_never_overwrites_and_rejects_symlink_read(self):
        first = png_bytes(color=(1, 2, 3))
        second = png_bytes(color=(4, 5, 6))
        name = self.hub.publish_exclusive("GEN-test.png", first)
        self.assertEqual(name, "GEN-test.png")
        self.assertEqual(self.hub.read_output(name), first)
        same = self.hub.publish_exclusive("GEN-test.png", first)
        self.assertEqual(same, name)
        different = self.hub.publish_exclusive("GEN-test.png", second)
        self.assertNotEqual(different, name)
        self.assertEqual((self.output / name).read_bytes(), first)
        self.assertEqual(stat.S_IMODE((self.output / different).stat().st_mode), 0o664)
        outside = pathlib.Path(self.temp.name) / "outside.png"
        outside.write_bytes(first)
        (self.output / "GEN-link.png").symlink_to(outside)
        with self.assertRaises(OSError):
            self.hub.read_output("GEN-link.png")
        replacement = self.hub.publish_exclusive("GEN-link.png", second)
        self.assertNotEqual(replacement, "GEN-link.png")
        self.assertEqual(outside.read_bytes(), first)

    def test_path_traversal_and_deceptive_name_rejected(self):
        for value in ("../secret.png", "/etc/passwd", "x.png/other", "x.svg", "x%2f.png", "..png"):
            with self.assertRaises(ValueError, msg=value):
                self.hub.safe_name(value)

    def test_http_file_boundary_refuses_symlink_and_health_leaks_no_path(self):
        good = png_bytes()
        (self.output / "good.png").write_bytes(good)
        outside = pathlib.Path(self.temp.name) / "outside.png"
        outside.write_bytes(good)
        (self.output / "link.png").symlink_to(outside)
        port = free_port()
        env = os.environ.copy()
        env.update({"AAG_OUTPUT_ROOT": str(self.output), "AAG_XPU_SCHEDULER_ROOT": str(self.scheduler), "AAG_IMAGE_HUB_PORT": str(port)})
        process = subprocess.Popen([sys.executable, str(ROOT / "integrations/upscale-engine/image-hub.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            health = json.loads(wait_http(port).decode("utf-8"))
            self.assertNotIn(str(self.output), json.dumps(health))
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/files/good.png", timeout=2) as response:
                self.assertEqual(response.read(), good)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/files/link.png", timeout=2)
            caught.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/files/..%2Foutside.png", timeout=2)
            caught.exception.close()
        finally:
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()

    def test_import_requires_a_fresh_agent_lease_token(self):
        self.assertFalse(self.hub.valid_agent_lease("missing"))
        lease = self.scheduler / "lease"
        lease.mkdir(parents=True)
        token = uuid.uuid4().hex
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (lease / "owner.json").write_text(json.dumps({"token": token, "kind": "agent", "acquired_at": now, "heartbeat_at": now}), encoding="utf-8")
        self.assertTrue(self.hub.valid_agent_lease(token))
        self.assertFalse(self.hub.valid_agent_lease("wrong"))
        (lease / "owner.json").unlink()
        outside = pathlib.Path(self.temp.name) / "outside-owner.json"
        outside.write_text(json.dumps({"token": token, "kind": "agent", "heartbeat_at": now}), encoding="utf-8")
        (lease / "owner.json").symlink_to(outside)
        self.assertFalse(self.hub.valid_agent_lease(token))


class UpscaleBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aag-upscale-test-")
        self.base = pathlib.Path(self.temp.name)
        for name in ("bin", "models", "tmp", "logs", "output", "state"):
            (self.base / name).mkdir()
        for model in ("upscayl-standard-4x", "upscayl-lite-4x", "high-fidelity-4x", "digital-art-4x", "remacri-4x", "ultrasharp-4x", "ultramix-balanced-4x"):
            for suffix in (".bin", ".param"):
                (self.base / "models" / f"{model}{suffix}").write_bytes(b"fixture")
        self.fake_bin = self.base / "bin" / "upscayl-bin"
        self.fake_bin.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\nfrom PIL import Image\n"
            "a=sys.argv\ni=a[a.index('-i')+1];o=a[a.index('-o')+1];s=int(a[a.index('-s')+1])\n"
            "im=Image.open(i);im.resize((im.width*s,im.height*s)).save(o,'PNG')\n",
            encoding="utf-8",
        )
        self.fake_bin.chmod(0o755)
        self.port = free_port()
        self.env = os.environ.copy()
        self.env.update({
            "AAG_UPSCALE_ROOT": str(self.base), "AAG_UPSCALE_BIN": str(self.fake_bin),
            "AAG_UPSCALE_MODELS": str(self.base / "models"), "AAG_UPSCALE_TMP": str(self.base / "tmp"),
            "AAG_UPSCALE_LOGS": str(self.base / "logs"), "AAG_OUTPUT_ROOT": str(self.base / "output"),
            "AAG_XPU_SCHEDULER_ROOT": str(self.base / "state" / "scheduler"), "AAG_UPSCALE_PORT": str(self.port),
            "AAG_XPU_QUEUE_TIMEOUT_SECONDS": "1", "AAG_XPU_STALE_SECONDS": "2", "AAG_UPSCALE_TIMEOUT_SECONDS": "5",
            "AAG_COMFYUI_QUEUE_URL": "http://127.0.0.1:9/queue",
        })
        self.process = subprocess.Popen([sys.executable, str(ROOT / "integrations/upscale-engine/upscale-server.py")], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        wait_http(self.port)

    def tearDown(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        self.process.stdout.close()
        self.temp.cleanup()

    def post(self, body, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        payload = json.dumps(body)
        connection.request("POST", "/upscale", body=payload, headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        status = response.status
        connection.close()
        return status, data

    def test_external_request_acquires_and_releases_shared_lease(self):
        source = png_bytes()
        status, body = self.post({"image_base64": base64.b64encode(source).decode(), "scale": 2, "model": "standard"})
        self.assertEqual(status, 200)
        self.assertEqual(body["lease_mode"], "external")
        output = self.base / "output" / body["filename"]
        self.assertTrue(output.is_file())
        with Image.open(output) as result:
            self.assertEqual(result.size, (64, 64))
        self.assertFalse((self.base / "state" / "scheduler" / "lease").exists())
        self.assertEqual(list((self.base / "tmp").iterdir()), [])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o664)

    def test_delegated_agent_token_is_validated_and_not_released_by_service(self):
        lease = self.base / "state" / "scheduler" / "lease"
        lease.mkdir(parents=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        token = uuid.uuid4().hex
        (lease / "owner.json").write_text(json.dumps({"token": token, "kind": "agent", "acquired_at": now, "heartbeat_at": now}), encoding="utf-8")
        status, body = self.post({"image_base64": base64.b64encode(png_bytes()).decode(), "scale": 2, "model": "standard", "lease_token": token}, {"X-AAG-Lease-Token": token})
        self.assertEqual(status, 200)
        self.assertEqual(body["lease_mode"], "delegated")
        self.assertTrue(lease.exists())
        (lease / "owner.json").unlink(); lease.rmdir()
        status, body = self.post({"image_base64": base64.b64encode(png_bytes()).decode(), "scale": 2, "model": "standard", "lease_token": "wrong"})
        self.assertEqual(status, 409)

    def test_delegated_token_rejects_symlink_owner_record(self):
        lease = self.base / "state" / "scheduler" / "lease"
        lease.mkdir(parents=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        token = uuid.uuid4().hex
        outside = self.base / "outside-owner.json"
        outside.write_text(json.dumps({"token": token, "kind": "agent", "heartbeat_at": now}), encoding="utf-8")
        (lease / "owner.json").symlink_to(outside)
        status, _ = self.post({"image_base64": base64.b64encode(png_bytes()).decode(), "scale": 2, "model": "standard", "lease_token": token})
        self.assertEqual(status, 409)

    def test_malformed_decode_unknown_model_and_extra_fields_are_rejected(self):
        status, _ = self.post({"image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 120).decode(), "scale": 2, "model": "standard"})
        self.assertEqual(status, 400)
        status, _ = self.post({"image_base64": base64.b64encode(png_bytes()).decode(), "scale": 2, "model": "../../evil"})
        self.assertEqual(status, 400)
        status, _ = self.post({"image_base64": base64.b64encode(png_bytes()).decode(), "scale": 2, "model": "standard", "command": "$(id)"})
        self.assertEqual(status, 400)

    def test_external_queue_depth_is_bounded(self):
        waiters = self.base / "state" / "scheduler" / "waiters"
        waiters.mkdir(parents=True, exist_ok=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for index in range(8):
            (waiters / f"{index + 1:020d}-{uuid.uuid4()}.json").write_text(json.dumps({"queued_at": now, "heartbeat_at": now}), encoding="utf-8")
        status, body = self.post({"image_base64": base64.b64encode(png_bytes()).decode(), "scale": 2, "model": "standard"})
        self.assertEqual(status, 429)
        self.assertIn("queue", body["error"].lower())


class FakeComfyHandler(http.server.BaseHTTPRequestHandler):
    prompt_id = "11111111-1111-4111-8111-111111111111"
    running = False
    completed = False
    interrupts = 0
    auto_complete = True

    def log_message(self, *_):
        pass

    def send_json(self, status, value):
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/queue":
            running = [[0, self.prompt_id, {}, {}, []]] if type(self).running else []
            self.send_json(200, {"queue_running": running, "queue_pending": []})
        elif self.path.startswith("/history/"):
            self.send_json(200, {self.prompt_id: {"status": {"completed": True}, "outputs": {}}} if type(self).completed else {})
        else:
            self.send_json(200, {"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/interrupt":
            request = json.loads(body.decode("utf-8"))
            if request.get("prompt_id") == self.prompt_id:
                type(self).interrupts += 1
                type(self).running = False
                self.send_json(200, {})
            else:
                self.send_json(409, {})
            return
        if self.path != "/prompt":
            self.send_json(404, {})
            return
        type(self).running = True
        type(self).completed = False
        if type(self).auto_complete:
            def complete():
                time.sleep(0.25)
                type(self).running = False
                type(self).completed = True
            __import__("threading").Thread(target=complete, daemon=True).start()
        self.send_json(200, {"prompt_id": self.prompt_id})


class ComfyBridgeArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aag-bridge-test-")
        self.state = pathlib.Path(self.temp.name) / "scheduler"
        self.target_port = free_port()
        self.listen_port = free_port()
        FakeComfyHandler.running = False
        FakeComfyHandler.completed = False
        FakeComfyHandler.interrupts = 0
        FakeComfyHandler.auto_complete = True
        self.target = http.server.ThreadingHTTPServer(("127.0.0.1", self.target_port), FakeComfyHandler)
        self.target_thread = __import__("threading").Thread(target=self.target.serve_forever, daemon=True)
        self.target_thread.start()
        env = os.environ.copy()
        env.update({"AAG_XPU_SCHEDULER_ROOT": str(self.state), "AAG_XPU_QUEUE_TIMEOUT_SECONDS": "1", "AAG_XPU_STALE_SECONDS": "2"})
        self.process = subprocess.Popen([
            sys.executable, str(ROOT / "integrations/comfyui-bridge/proxy.py"),
            "--listen-host", "127.0.0.1", "--listen-port", str(self.listen_port),
            "--target-host", "127.0.0.1", "--target-port", str(self.target_port),
        ], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        wait_http(self.listen_port, "/system_stats")

    def tearDown(self):
        self.process.terminate(); self.process.wait(timeout=5); self.process.stdout.close()
        self.target.shutdown(); self.target.server_close(); self.target_thread.join(timeout=2)
        self.temp.cleanup()

    def post_prompt(self, token=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.listen_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-AAG-Lease-Token"] = token
        connection.request("POST", "/prompt", body=json.dumps({
            "prompt": {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux-2-klein-4b-fp8.safetensors"}},
                "15": {"class_type": "SamplerCustomAdvanced", "inputs": {}},
            },
            "client_id": "aag-image-test-client",
        }), headers=headers)
        response = connection.getresponse()
        body = response.read()
        result = response.status, response.getheader("X-AAG-XPU-Arbitrated"), body
        connection.close()
        return result

    def test_external_comfy_submission_owns_then_releases_shared_lease(self):
        status, mode, body = self.post_prompt()
        self.assertEqual(status, 200); self.assertEqual(mode, "external")
        owner_file = self.state / "lease" / "owner.json"
        owner = json.loads(owner_file.read_text(encoding="utf-8"))
        self.assertEqual(owner["kind"], "external-comfyui")
        self.assertEqual(owner["prompt_id"], FakeComfyHandler.prompt_id)
        deadline = time.monotonic() + 4
        while owner_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(owner_file.exists())

    def test_delegated_agent_token_is_validated_and_bridge_does_not_release_it(self):
        lease = self.state / "lease"; lease.mkdir(parents=True)
        token = uuid.uuid4().hex; now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (lease / "owner.json").write_text(json.dumps({"token": token, "kind": "agent", "acquired_at": now, "heartbeat_at": now}), encoding="utf-8")
        status, mode, _ = self.post_prompt(token)
        self.assertEqual(status, 200); self.assertEqual(mode, "delegated"); self.assertTrue(lease.exists())
        status, _, _ = self.post_prompt("wrong-token")
        self.assertEqual(status, 409)

    def test_progress_and_exact_interrupt_require_the_bound_agent_lease(self):
        lease = self.state / "lease"; lease.mkdir(parents=True)
        token = uuid.uuid4().hex; now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        job_id = f"aag-{uuid.uuid4()}"
        (lease / "owner.json").write_text(json.dumps({
            "token": token, "kind": "agent", "job_id": job_id,
            "acquired_at": now, "heartbeat_at": now,
        }), encoding="utf-8")
        FakeComfyHandler.auto_complete = False
        status, mode, _ = self.post_prompt(token)
        self.assertEqual((status, mode), (200, "delegated"))

        route = f"/aag/engine-progress/{FakeComfyHandler.prompt_id}"
        connection = http.client.HTTPConnection("127.0.0.1", self.listen_port, timeout=5)
        connection.request("GET", route, headers={"X-AAG-Lease-Token": token})
        response = connection.getresponse(); progress = json.loads(response.read())
        self.assertEqual(response.status, 200); connection.close()
        self.assertEqual(progress["job_id"], job_id)
        self.assertEqual(progress["last_engine_progress_event"], "prompt_submitted")

        connection = http.client.HTTPConnection("127.0.0.1", self.listen_port, timeout=5)
        connection.request("POST", "/aag/interrupt", body=json.dumps({"prompt_id": FakeComfyHandler.prompt_id}), headers={"Content-Type": "application/json", "X-AAG-Lease-Token": "wrong"})
        response = connection.getresponse(); response.read()
        self.assertEqual(response.status, 403); connection.close()
        self.assertEqual(FakeComfyHandler.interrupts, 0)

        # A valid lease and exact prompt are insufficient: the bridge must
        # independently prove old, real engine progress before interrupting.
        connection = http.client.HTTPConnection("127.0.0.1", self.listen_port, timeout=5)
        connection.request("POST", "/aag/interrupt", body=json.dumps({"prompt_id": FakeComfyHandler.prompt_id}), headers={"Content-Type": "application/json", "X-AAG-Lease-Token": token})
        response = connection.getresponse(); withheld = json.loads(response.read())
        self.assertEqual(response.status, 200); connection.close()
        self.assertEqual(withheld["action"], "INTERRUPT_WITHHELD_PROGRESS_CHANGED")
        self.assertEqual(FakeComfyHandler.interrupts, 0)

        progress_file = self.state / "engine-progress" / f"{FakeComfyHandler.prompt_id}.json"
        current = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 400))
        progress_file.write_text(json.dumps({
            "schema_version": "aag.comfy-engine-progress.v1",
            "prompt_id": FakeComfyHandler.prompt_id,
            "job_id": job_id,
            "sequence": 3,
            "last_engine_progress_at": old,
            "last_engine_progress_event": "sampler_step",
            "current_engine_node": "15",
            "current_engine_node_class": "SamplerCustomAdvanced",
            "current_engine_step": 0,
            "current_engine_step_max": 4,
            "observer_status": "connected",
            "observer_heartbeat_at": current,
        }), encoding="utf-8")

        connection = http.client.HTTPConnection("127.0.0.1", self.listen_port, timeout=5)
        connection.request("POST", "/aag/interrupt", body=json.dumps({"prompt_id": FakeComfyHandler.prompt_id}), headers={"Content-Type": "application/json", "X-AAG-Lease-Token": token})
        response = connection.getresponse(); result = json.loads(response.read())
        self.assertEqual(response.status, 200); connection.close()
        self.assertEqual(result["prompt_id"], FakeComfyHandler.prompt_id)
        self.assertEqual(FakeComfyHandler.interrupts, 1)
        self.assertFalse(FakeComfyHandler.running)

    def test_delegated_token_rejects_symlink_owner_record(self):
        lease = self.state / "lease"; lease.mkdir(parents=True)
        token = uuid.uuid4().hex; now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        outside = pathlib.Path(self.temp.name) / "outside-owner.json"
        outside.write_text(json.dumps({"token": token, "kind": "agent", "heartbeat_at": now}), encoding="utf-8")
        (lease / "owner.json").symlink_to(outside)
        status, _, _ = self.post_prompt(token)
        self.assertEqual(status, 409)

    def test_external_comfy_queue_depth_is_bounded(self):
        waiters = self.state / "waiters"
        waiters.mkdir(parents=True, exist_ok=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for index in range(8):
            (waiters / f"{index + 1:020d}-{uuid.uuid4()}.json").write_text(json.dumps({"queued_at": now, "heartbeat_at": now}), encoding="utf-8")
        status, _, _ = self.post_prompt()
        self.assertEqual(status, 429)


if __name__ == "__main__":
    unittest.main(verbosity=2)
