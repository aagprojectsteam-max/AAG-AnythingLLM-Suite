from __future__ import annotations
import json,time,unittest,urllib.request
from unittest.mock import patch
import test_http_boundary as base
import server as boundary

class ToolKeepaliveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.BoundaryHttpTests.setUpClass()
        cls.http = base.BoundaryHttpTests()
    @classmethod
    def tearDownClass(cls):
        base.BoundaryHttpTests.tearDownClass()
    def payload(self, text="slow-valid", stream=True):
        return {"model":"behavioral-http-test","messages":[{"role":"user","content":text}],
                "stream":stream,"tools":[{"type":"function","function":{"name":"safe_tool",
                "description":"test","parameters":{"type":"object","properties":{"value":{"type":"string"}},
                "required":["value"],"additionalProperties":False}}}]}
    def delayed(self, original):
        def call(payload, **kwargs):
            text = payload["messages"][-1].get("content","")
            if text in {"slow-valid","FORCE_UNLISTED_TOOL","slow-upstream-error"}:
                time.sleep(0.30)
                if text == "slow-upstream-error":
                    raise boundary.BoundaryError(502,"UPSTREAM_TEST_ERROR","Delayed inference failed.")
            return original(payload, **kwargs)
        return call
    def test_slow_tool_headers_arrive_before_validated_completion(self):
        original=boundary.APP.upstream.json
        with patch.object(boundary,"TOOL_KEEPALIVE_SECONDS",0.03), patch.object(boundary.APP.upstream,"json",side_effect=self.delayed(original)):
            req=urllib.request.Request(self.http.base+"/v1/chat/completions",
                data=json.dumps(self.payload()).encode(),
                headers={"Content-Type":"application/json","Authorization":"Bearer "+"a"*64})
            started=time.monotonic()
            with urllib.request.urlopen(req,timeout=2) as response:
                self.assertEqual(response.status,200)
                first=response.readline()
                self.assertTrue(first.startswith(b": processing"))
                self.assertLess(time.monotonic()-started,0.25)
                self.assertNotIn(b"data:",first)
                body=first+response.read()
            self.assertGreaterEqual(time.monotonic()-started,0.30)
        self.assertIn(b"safe_tool",body)
        self.assertIn(b"data: [DONE]",body)
        self.assertEqual(body.count(b"HTTP/1.1"),0)
    def test_slow_unauthorized_tool_never_escapes_validation(self):
        original=boundary.APP.upstream.json
        with patch.object(boundary,"TOOL_KEEPALIVE_SECONDS",0.03), patch.object(boundary.APP.upstream,"json",side_effect=self.delayed(original)):
            status,headers,body=self.http.request("/v1/chat/completions",payload=self.payload("FORCE_UNLISTED_TOOL"))
        self.assertEqual(status,200)
        self.assertIn(b": processing",body)
        self.assertIn(b'"error"',body)
        self.assertNotIn(b"unlisted-destruction-tool",body)
        self.assertNotIn(b'"tool_calls"',body)
        self.assertNotIn(b"[DONE]",body)
    def test_delayed_upstream_error_is_valid_sse_error(self):
        original=boundary.APP.upstream.json
        with patch.object(boundary,"TOOL_KEEPALIVE_SECONDS",0.03), patch.object(boundary.APP.upstream,"json",side_effect=self.delayed(original)):
            status,headers,body=self.http.request("/v1/chat/completions",payload=self.payload("slow-upstream-error"))
        self.assertEqual(status,200)
        self.assertIn(b"UPSTREAM_TEST_ERROR",body)
        self.assertNotIn(b'"tool_calls"',body)
        self.assertNotIn(b"HTTP/1.1",body)
    def test_nonstream_preserves_json_and_errors(self):
        original=boundary.APP.upstream.json
        with patch.object(boundary,"TOOL_KEEPALIVE_SECONDS",0.03), patch.object(boundary.APP.upstream,"json",side_effect=self.delayed(original)):
            status,headers,body=self.http.request("/v1/chat/completions",payload=self.payload(stream=False))
            self.assertEqual(status,200)
            self.assertIn("application/json",headers["Content-Type"])
            self.assertIn("choices",json.loads(body))
            status,_,body=self.http.request("/v1/chat/completions",payload=self.payload("slow-upstream-error",False))
            self.assertEqual(status,502)
            self.assertEqual(json.loads(body)["error"]["code"],"UPSTREAM_TEST_ERROR")
    def test_auth_is_rejected_before_any_heartbeat(self):
        with patch.object(boundary,"TOOL_KEEPALIVE_SECONDS",0.001):
            status,_,body=self.http.request("/v1/chat/completions",payload=self.payload(),token=False)
        self.assertEqual(status,401)
        self.assertNotIn(b": processing",body)
        self.assertEqual(json.loads(body)["error"]["code"],"AUTHENTICATION_REQUIRED")
