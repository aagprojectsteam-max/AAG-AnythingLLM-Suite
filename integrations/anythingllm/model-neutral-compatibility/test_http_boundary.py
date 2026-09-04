#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server as boundary  # noqa: E402


def completion(content="Normal readable response", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-http-test",
        "object": "chat.completion",
        "created": 1,
        "model": "behavioral-http-test",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self.send_json(200, {"object": "list", "data": [{"id": "behavioral-http-test"}]})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or "0")
        payload = json.loads(self.rfile.read(length))
        messages = payload.get("messages", [])
        last = messages[-1].get("content", "") if messages else ""
        first = messages[0].get("content", "") if messages else ""
        if "Ordinary text compatibility check" in last:
            self.send_json(200, completion("Ordinary chat works correctly."))
            return
        tools = payload.get("tools")
        if tools:
            function = tools[0]["function"]
            name = function["name"]
            if "FORCE_UNLISTED_TOOL" in last:
                self.send_json(
                    200,
                    completion(
                        None,
                        [{
                            "id": "call-unlisted",
                            "type": "function",
                            "function": {
                                "name": "unlisted-destruction-tool",
                                "arguments": json.dumps({"target": "must-not-execute"}),
                            },
                        }],
                    ),
                )
                return
            if "FORCE_CONFLICTING_CALLS" in last:
                call = {
                    "id": "call-conflict-1",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps({"value": "ok"})},
                }
                self.send_json(200, completion(None, [call, {**call, "id": "call-conflict-2"}]))
                return
            if "FORCE_COMPOSER_PROJECT" in last and name == "aag-image-task":
                arguments = {
                    "operation": "generate",
                    "prompt": "A complete professional cinematic forest prompt.",
                    "source_policy": "auto",
                    "preservation": "none",
                    "quality": "invalid",
                    "aspect_ratio": "1:1",
                    "invented": True,
                }
                self.send_json(
                    200,
                    completion(None, [{"id": "candidate-project", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]),
                )
                return
            if "FORCE_NATIVE_INVALID" in last:
                self.send_json(200, completion(None, [{"id": "call-bad", "type": "function", "function": {"name": name, "arguments": "{}"}}]))
                return
            properties = function["parameters"].get("properties", {})
            if "nonce" in properties:
                arguments = {"nonce": properties["nonce"]["const"]}
            elif name == "aag-image-task":
                arguments = {
                    "operation": "generate",
                    "prompt": "A complete professional cinematic forest prompt.",
                    "source_policy": "auto",
                    "preservation": "none",
                    "quality": "quality",
                    "aspect_ratio": "16:9",
                }
            else:
                arguments = {"value": "ok"}
            self.send_json(
                200,
                completion(
                    None,
                    [{"id": "call-http", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}],
                ),
            )
            return
        if "AAG MODEL-NEUTRAL TOOL ADAPTER V1" in first and "FORCE_NATIVE_INVALID" in last:
            self.send_json(200, completion(json.dumps({"tool_name": "safe_tool", "arguments": {"value": "ok"}})))
            return
        if "AAG MODEL-NEUTRAL TOOL ADAPTER V1" in first and "REJECTED_NATIVE_CANDIDATE_JSON=" in last:
            lines = dict(line.split("=", 1) for line in last.splitlines() if "=" in line)
            candidate = json.loads(lines["REJECTED_NATIVE_CANDIDATE_JSON"])
            required = json.loads(lines["REQUIRED_ARGUMENTS_JSON"])
            arguments = dict(candidate["arguments"])
            arguments.update(required)
            if candidate["tool_name"] == "safe_tool":
                arguments = {"value": "ok"}
            self.send_json(200, completion(json.dumps({"tool_name": candidate["tool_name"], "arguments": arguments})))
            return
        if "LEAK_TEST" in last:
            self.send_json(200, completion("<unused20>"))
            return
        self.send_json(200, completion("A normal validated response."))


class FakeAnythingLLMHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls = []

    def log_message(self, *_args):
        return

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or "0")
        payload = json.loads(self.rfile.read(length))
        type(self).calls.append((self.path, payload, self.headers.get("Authorization")))
        if self.path == "/api/v1/workspace/image-generator/thread/new":
            self.send_json(200, {"thread": {"slug": "composer-thread"}, "message": None})
            return
        if self.path == "/api/v1/workspace/image-generator/thread/composer-thread/chat":
            self.send_json(200, {"error": None, "textResponse": "AAG_IMAGE_RESULT\nstatus=completed"})
            return
        self.send_json(404, {"error": "not found"})


class BoundaryHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        token = cls.root / "proxy.token"
        token.write_text("a" * 64)
        token.chmod(0o600)
        state = cls.root / "state"
        state.mkdir()
        cls.attested_process = subprocess.Popen(["/usr/bin/sleep", "60"])
        raw = Path(f"/proc/{cls.attested_process.pid}/stat").read_text()
        starttime = raw.rsplit(") ", 1)[1].split()[19]
        for name, value in {
            "pid": str(cls.attested_process.pid),
            "starttime": starttime,
            "model": "/arbitrary/model.gguf",
            "executable": "/usr/bin/sleep",
            "context": "8192",
            "profile": "normal",
        }.items():
            (state / name).write_text(value)
        database = cls.root / "anythingllm.db"
        connection = sqlite3.connect(database)
        connection.execute("create table api_keys (id integer primary key, secret text)")
        connection.execute("insert into api_keys(secret) values ('test-api-key')")
        connection.commit()
        connection.close()

        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstreamHandler)
        cls.fake_thread = threading.Thread(target=cls.fake.serve_forever, daemon=True)
        cls.fake_thread.start()
        FakeAnythingLLMHandler.calls = []
        cls.fake_anythingllm = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnythingLLMHandler)
        cls.fake_anythingllm_thread = threading.Thread(target=cls.fake_anythingllm.serve_forever, daemon=True)
        cls.fake_anythingllm_thread.start()
        args = SimpleNamespace(
            upstream=f"http://127.0.0.1:{cls.fake.server_port}",
            upstream_timeout=5,
            storage=str(cls.root / "storage"),
            state_dir=str(state),
            expected_executable="/usr/bin/sleep",
            token_file=str(token),
            anythingllm_db=str(database),
            anythingllm_api=f"http://127.0.0.1:{cls.fake_anythingllm.server_port}/api",
            composer_timeout=5,
            port=0,
        )
        boundary.APP = boundary.Application(args)
        cls.server = boundary.ReusableThreadingHTTPServer(("127.0.0.1", 0), boundary.BoundaryHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.fake.shutdown()
        cls.fake.server_close()
        cls.fake_anythingllm.shutdown()
        cls.fake_anythingllm.server_close()
        cls.attested_process.terminate()
        cls.attested_process.wait(timeout=3)
        cls.temporary.cleanup()

    def request(self, path, *, payload=None, token=True):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + "a" * 64
        body = None
        method = "GET"
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.headers, error.read()
            finally:
                error.close()

    def test_health_is_bounded_and_public(self):
        status, _, body = self.request("/health", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_api_requires_auth(self):
        status, _, body = self.request("/v1/models", token=False)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"]["code"], "AUTHENTICATION_REQUIRED")

    def test_models_forwarded(self):
        status, _, body = self.request("/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["object"], "list")

    def test_preflight_and_ordinary_chat(self):
        status, _, body = self.request("/aag/preflight", payload={"model": "behavioral-http-test"})
        self.assertEqual(status, 200)
        preflight = json.loads(body)
        self.assertEqual(preflight["basic_model_text_sanity"], "BASIC_TEXT_OK")
        self.assertEqual(preflight["ordinary_chat_compatibility"], "CHAT_OK")
        self.assertEqual(preflight["chat_capability"], "CHAT_OK")
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={"model": "behavioral-http-test", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "A normal validated response.")

    def test_stream_is_reconstituted_after_validation(self):
        status, headers, body = self.request(
            "/v1/chat/completions",
            payload={"model": "behavioral-http-test", "messages": [{"role": "user", "content": "hello"}], "stream": True},
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["Content-Type"])
        self.assertIn(b"data: [DONE]", body)

    def test_native_tool_call_is_schema_validated(self):
        schema = {
            "type": "function",
            "function": {
                "name": "safe_tool",
                "description": "test",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={
                "model": "behavioral-http-test",
                "messages": [{"role": "user", "content": "use tool"}],
                "tools": [schema],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "safe_tool")

    def test_unlisted_native_tool_is_rejected_at_http_boundary(self):
        schema = {
            "type": "function",
            "function": {
                "name": "safe_tool",
                "description": "the only authorized test tool",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={
                "model": "behavioral-http-test",
                "messages": [{"role": "user", "content": "FORCE_UNLISTED_TOOL"}],
                "tools": [schema],
            },
        )
        self.assertEqual(status, 422)
        parsed = json.loads(body)
        self.assertNotIn("tool_calls", parsed)
        self.assertIn(
            parsed["error"]["code"],
            {"ADAPTER_OUTPUT_INVALID", "CANONICAL_JSON_INVALID", "NATIVE_TOOL_UNAUTHORIZED"},
        )

    def test_conflicting_native_calls_are_rejected_at_http_boundary(self):
        schema = {
            "type": "function",
            "function": {
                "name": "safe_tool",
                "description": "the only authorized test tool",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={
                "model": "behavioral-http-test",
                "messages": [{"role": "user", "content": "FORCE_CONFLICTING_CALLS"}],
                "tools": [schema],
            },
        )
        self.assertEqual(status, 422)
        parsed = json.loads(body)
        self.assertNotIn("tool_calls", parsed)

    def test_real_schema_native_rejection_uses_one_adapter_attempt(self):
        schema = {
            "type": "function",
            "function": {
                "name": "safe_tool",
                "description": "test",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={
                "model": "behavioral-http-test",
                "messages": [{"role": "user", "content": "FORCE_NATIVE_INVALID"}],
                "tools": [schema],
            },
        )
        self.assertEqual(status, 200)
        parsed = json.loads(body)
        self.assertEqual(parsed["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "safe_tool")
        self.assertEqual(parsed["aag_compatibility"]["mode"], "GENERIC_ADAPTER")

    def test_actual_raw_token_leak_fails_closed(self):
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={"model": "behavioral-http-test", "messages": [{"role": "user", "content": "LEAK_TEST"}]},
        )
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(body)["error"]["code"], "RAW_UNUSED_TOKEN")

    def test_composer_page_is_loopback_asset(self):
        status, headers, body = self.request("/composer/", token=False)
        self.assertEqual(status, 200)
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers["Referrer-Policy"], "same-origin")
        self.assertIn(b"Image Composer", body)

    def test_taxonomy_is_served_as_loopback_asset(self):
        status, headers, body = self.request("/composer/visual-taxonomy.json", token=False)
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        taxonomy = json.loads(body)
        self.assertEqual(len(taxonomy["families"]), 28)
        self.assertEqual(sum(len(family["subfamilies"]) for family in taxonomy["families"]), 493)
        self.assertEqual(taxonomy["schema"], "aag.visual-style-atlas.catalog.v1")
        self.assertEqual(taxonomy["total_entries"], 493)
        first = taxonomy["families"][0]["subfamilies"][0]
        self.assertIsInstance(first["atlas"]["available"], bool)
        self.assertIn("description", first)
        self.assertNotIn("thumbnail_path", json.dumps(taxonomy))

    def test_atlas_thumbnail_and_preview_are_canonical_cached_assets(self):
        status, headers, thumbnail = self.request(
            "/composer/atlas/thumbnail/fine-art-traditional-media/watercolor",
            token=False,
        )
        pixels_available = boundary.get_visual_atlas().catalog()["families"][0]["subfamilies"][0]["atlas"]["available"]
        if not pixels_available:
            self.assertEqual(status, 404)
            self.assertEqual(json.loads(thumbnail)["error"]["code"], "ATLAS_ASSET_NOT_FOUND")
            return
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/webp")
        self.assertIn("immutable", headers["Cache-Control"])
        self.assertTrue(thumbnail.startswith(b"RIFF"))
        status, headers, preview = self.request(
            "/composer/atlas/preview/fine-art-traditional-media/watercolor",
            token=False,
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(preview.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(preview), len(thumbnail))

    def test_unknown_atlas_asset_fails_closed(self):
        status, _, body = self.request(
            "/composer/atlas/thumbnail/photography/not-a-style",
            token=False,
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "ATLAS_ASSET_NOT_FOUND")

    def test_composer_uses_trusted_workspace_thread(self):
        FakeAnythingLLMHandler.calls = []
        session, _ = boundary.APP.composer_sessions.issue()
        result = boundary.APP.submit_composer({"mode": "auto", "free_text": "Create one quiet forest image."}, session)
        self.assertIsNone(result["error"])
        self.assertEqual(
            [call[0] for call in FakeAnythingLLMHandler.calls],
            [
                "/api/v1/workspace/image-generator/thread/new",
                "/api/v1/workspace/image-generator/thread/composer-thread/chat",
            ],
        )
        self.assertTrue(all(call[2] == "Bearer test-api-key" for call in FakeAnythingLLMHandler.calls))
        chat_payload = FakeAnythingLLMHandler.calls[1][1]
        self.assertEqual(chat_payload["mode"], "automatic")
        self.assertNotIn("sessionId", chat_payload)

    def test_advanced_composer_message_is_signed(self):
        FakeAnythingLLMHandler.calls = []
        session, _ = boundary.APP.composer_sessions.issue()
        boundary.APP.submit_composer(
            {
                "mode": "advanced",
                "free_text": "A cinematic fox",
                "operation": "generate",
                "aspect_ratio": "16:9",
                "count": 1,
                "quality": "quality",
                "source_policy": "auto",
                "preservation": "none",
                "scale": "none",
            },
            session,
        )
        message = FakeAnythingLLMHandler.calls[1][1]["message"]
        self.assertIn("AAG_COMPOSER_INTENT_SIGNATURE_V1=", message)
        intent = boundary.APP.trusted_composer_intent({"messages": [{"role": "user", "content": message}]})
        self.assertEqual(intent["quality"], "quality")

    def test_native_prepare_signs_without_submitting_or_creating_a_thread(self):
        FakeAnythingLLMHandler.calls = []
        result = boundary.APP.prepare_composer(
            {
                "mode": "advanced",
                "free_text": "שועל רודף אחרי חתול",
                "operation": "generate",
                "visual_family": "fantasy",
                "aspect_ratio": "16:9",
            }
        )
        self.assertEqual(FakeAnythingLLMHandler.calls, [])
        self.assertIn("AAG_COMPOSER_INTENT_SIGNATURE_V1=", result["modelMessage"])
        intent = boundary.APP.trusted_composer_intent(
            {"messages": [{"role": "user", "content": result["modelMessage"]}]}
        )
        self.assertEqual(
            intent["semantics"]["explicit_constraints"]["visual_family"],
            "fantasy",
        )

    def test_signed_composer_request_text_binding_fails_closed(self):
        result = boundary.APP.prepare_composer(
            {"mode": "advanced", "free_text": "original request"}
        )
        tampered = result["modelMessage"].replace(
            "USER_CREATIVE_DIRECTION=\noriginal request",
            "USER_CREATIVE_DIRECTION=\ntampered request",
        )
        with self.assertRaises(boundary.CompatibilityError) as caught:
            boundary.APP.trusted_composer_intent(
                {"messages": [{"role": "user", "content": tampered}]}
            )
        self.assertEqual(caught.exception.code, "COMPOSER_INTENT_SIGNATURE_INVALID")

    def test_signed_composer_tamper_matrix_remains_rejected(self):
        prepared = boundary.APP.prepare_composer(
            {
                "mode": "advanced",
                "free_text": "A cinematic fox in an old city",
                "visual_family": "cinematic-film-still",
                "visual_subfamily": "feature-film-look",
                "atlas_selection_mode": "manual_taxonomy",
            }
        )["modelMessage"]
        raw = re.search(
            r"^AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1=(.+)$",
            prepared,
            re.MULTILINE,
        ).group(1)
        original = json.loads(raw)

        def replace_intent(mutator):
            changed = json.loads(json.dumps(original))
            mutator(changed)
            return prepared.replace(raw, boundary.composer_canonical_json(changed), 1)

        cases = {
            "modified Atlas family": replace_intent(
                lambda value: value["knowledge_modules"]["visual_atlas"]["selections"][0].update(
                    {"family_id": "illustration"}
                )
            ),
            "modified Atlas subfamily": replace_intent(
                lambda value: value["knowledge_modules"]["visual_atlas"]["selections"][0].update(
                    {"subfamily_id": "editorial"}
                )
            ),
            "modified confidence": replace_intent(
                lambda value: value["knowledge_modules"]["visual_atlas"].update(
                    {"confidence": 0.95}
                )
            ),
            "missing signed field": replace_intent(lambda value: value.pop("operation")),
            "inserted unexpected signed field": replace_intent(
                lambda value: value.update({"unexpected_signed_field": True})
            ),
            "modified signature": prepared[:-1] + ("0" if prepared[-1] != "0" else "1"),
        }
        for name, tampered in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(boundary.CompatibilityError) as caught:
                    boundary.APP.trusted_composer_intent(
                        {"messages": [{"role": "user", "content": tampered}]}
                    )
                self.assertEqual(caught.exception.code, "COMPOSER_INTENT_SIGNATURE_INVALID")

        noncanonical = prepared.replace('"confidence":1', '"confidence":1.0', 1)
        with self.assertRaises(boundary.CompatibilityError) as caught:
            boundary.APP.trusted_composer_intent(
                {"messages": [{"role": "user", "content": noncanonical}]}
            )
        self.assertEqual(caught.exception.code, "COMPOSER_INTENT_INVALID")

    def test_advanced_attachment_preserves_signed_authoritative_text(self):
        FakeAnythingLLMHandler.calls = []
        session, _ = boundary.APP.composer_sessions.issue()
        boundary.APP.submit_composer(
            {
                "mode": "advanced",
                "free_text": "Enlarge this source exactly twice",
                "operation": "upscale",
                "aspect_ratio": "auto",
                "count": 1,
                "quality": "auto",
                "source_policy": "current_attachment",
                "preservation": "none",
                "scale": 2,
                "attachments": [{"name": "source.png", "mime": "image/png", "contentString": "data:image/png;base64,iVBORw0KGgo="}],
            },
            session,
        )
        message = FakeAnythingLLMHandler.calls[1][1]["message"]
        self.assertNotIn("AAG_COMPOSER_OPAQUE_V1=", message)
        self.assertIn("USER_CREATIVE_DIRECTION=\nEnlarge this source exactly twice", message)
        self.assertIn("AAG_COMPOSER_INTENT_SIGNATURE_V1=", message)
        payload = {"messages": [{"role": "user", "content": message}]}
        self.assertEqual(boundary.APP.trusted_composer_intent(payload)["scale"], 2)

    def test_composer_session_reuses_thread_for_previous_artifact(self):
        FakeAnythingLLMHandler.calls = []
        session, _ = boundary.APP.composer_sessions.issue()
        first = boundary.APP.submit_composer({"mode": "auto", "free_text": "Create one quiet forest image."}, session)
        self.assertTrue(first["previousArtifactAvailable"])
        second = boundary.APP.submit_composer(
            {
                "mode": "advanced",
                "free_text": "Upscale the latest image faithfully",
                "operation": "upscale",
                "source_policy": "previous_artifact",
                "preservation": "none",
                "scale": 2,
            },
            session,
        )
        self.assertTrue(second["previousArtifactAvailable"])
        self.assertEqual(
            [call[0] for call in FakeAnythingLLMHandler.calls],
            [
                "/api/v1/workspace/image-generator/thread/new",
                "/api/v1/workspace/image-generator/thread/composer-thread/chat",
                "/api/v1/workspace/image-generator/thread/composer-thread/chat",
            ],
        )

    def test_stale_previous_artifact_fails_before_anythingllm(self):
        FakeAnythingLLMHandler.calls = []
        session, _ = boundary.APP.composer_sessions.issue()
        with self.assertRaises(boundary.BoundaryError) as caught:
            boundary.APP.submit_composer(
                {
                    "mode": "advanced",
                    "free_text": "Upscale the latest image",
                    "operation": "upscale",
                    "source_policy": "previous_artifact",
                    "preservation": "none",
                    "scale": 2,
                },
                session,
            )
        self.assertEqual(caught.exception.code, "COMPOSER_PREVIOUS_ARTIFACT_UNAVAILABLE")
        self.assertEqual(FakeAnythingLLMHandler.calls, [])

    def test_signed_composer_intent_is_enforced(self):
        preview, _ = boundary.compose_request(
            {
                "mode": "advanced",
                "free_text": "A cinematic fox",
                "operation": "generate",
                "aspect_ratio": "16:9",
                "count": 1,
                "quality": "quality",
                "source_policy": "auto",
                "preservation": "none",
                "scale": "none",
            }
        )
        signed = boundary.APP._sign_composer_message(preview)
        schema = {
            "type": "function",
            "function": {
                "name": "aag-image-task",
                "description": "test",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string"},
                        "prompt": {"type": "string"},
                        "source_policy": {"type": "string"},
                        "preservation": {"type": "string"},
                        "quality": {"type": "string"},
                        "aspect_ratio": {"type": "string"},
                    },
                    "required": ["operation", "prompt", "source_policy", "preservation", "quality", "aspect_ratio"],
                    "additionalProperties": False,
                },
            },
        }
        status, _, _ = self.request(
            "/v1/chat/completions",
            payload={"model": "behavioral-http-test", "messages": [{"role": "user", "content": signed}], "tools": [schema]},
        )
        self.assertEqual(status, 200)
        tampered = signed.replace('"aspect_ratio":"16:9"', '"aspect_ratio":"1:1"')
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={"model": "behavioral-http-test", "messages": [{"role": "user", "content": tampered}], "tools": [schema]},
        )
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(body)["error"]["code"], "COMPOSER_INTENT_SIGNATURE_INVALID")

    def test_signed_composer_candidate_is_deterministically_projected(self):
        preview, _ = boundary.compose_request(
            {
                "mode": "advanced",
                "free_text": "FORCE_COMPOSER_PROJECT",
                "operation": "generate",
                "aspect_ratio": "16:9",
                "count": 1,
                "quality": "quality",
                "source_policy": "auto",
                "preservation": "none",
                "scale": "none",
            }
        )
        signed = boundary.APP._sign_composer_message(preview)
        schema = {
            "type": "function",
            "function": {
                "name": "aag-image-task",
                "description": "test",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["generate", "transform", "upscale"]},
                        "prompt": {"type": "string", "minLength": 10},
                        "source_policy": {"type": "string", "enum": ["auto", "current_attachment"]},
                        "preservation": {"type": "string", "enum": ["none", "subject"]},
                        "quality": {"type": "string", "enum": ["auto", "quality"]},
                        "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9"]},
                        "count": {"type": "integer", "minimum": 1, "maximum": 2},
                    },
                    "required": ["operation", "prompt", "source_policy", "preservation"],
                    "additionalProperties": False,
                },
            },
        }
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={"model": "behavioral-http-test", "messages": [{"role": "user", "content": signed}], "tools": [schema]},
        )
        self.assertEqual(status, 200)
        parsed = json.loads(body)
        arguments = json.loads(parsed["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(arguments["prompt"], "A complete professional cinematic forest prompt.")
        self.assertEqual(arguments["aspect_ratio"], "16:9")
        self.assertEqual(arguments["quality"], "quality")
        self.assertNotIn("invented", arguments)
        self.assertNotIn("count", arguments)
        self.assertEqual(parsed["aag_compatibility"]["mode"], "GENERIC_ADAPTER")

    def test_signed_intent_is_enforced_after_native_adapter_fallback(self):
        preview, _ = boundary.compose_request(
            {
                "mode": "advanced",
                "free_text": "FORCE_NATIVE_INVALID",
                "operation": "generate",
                "aspect_ratio": "16:9",
                "count": 1,
                "quality": "quality",
                "source_policy": "auto",
                "preservation": "none",
                "scale": "none",
            }
        )
        signed = boundary.APP._sign_composer_message(preview)
        schema = {
            "type": "function",
            "function": {
                "name": "safe_tool",
                "description": "test",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        status, _, body = self.request(
            "/v1/chat/completions",
            payload={"model": "behavioral-http-test", "messages": [{"role": "user", "content": signed}], "tools": [schema]},
        )
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(body)["error"]["code"], "COMPOSER_INTENT_MISMATCH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
