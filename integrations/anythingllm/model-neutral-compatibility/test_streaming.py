import io
import http.client
import json
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import server as boundary
from compatibility import CompatibilityError, openai_sse_events
from text_stream import encode_event, read_events, validated_text_events


def chunk(text=None, finish=None, **extra):
    return {'id': 'test-stream', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test', 'choices': [{'index': 0, 'delta': {} if text is None else {'content': text}, 'finish_reason': finish}], **extra}


def contents(events):
    return ''.join((event.get('choices') or [{}])[0].get('delta', {}).get('content', '') for event in events)


class StreamValidationTests(unittest.TestCase):
    def test_unicode_markdown_and_exact_content(self):
        pieces = ['#', ' Hello', '\n\n', 'שלום ', '**bold', '** [1]', '\n', '<b', '>HTML</b>']
        events = list(validated_text_events([*(chunk(text) for text in pieces), chunk(finish='stop')]))
        self.assertEqual(contents(events), ''.join(pieces))
        self.assertEqual(sum(bool(e['choices'][0]['finish_reason']) for e in events), 1)

    def test_prefix_is_delivered_before_generator_finishes(self):
        reached = []
        def upstream():
            yield chunk('First word')
            reached.append('later')
            yield chunk(' second word')
            yield chunk(finish='stop')
        stream = validated_text_events(upstream())
        self.assertEqual(contents([next(stream)]), 'First word')
        self.assertEqual(reached, [])
        self.assertEqual(contents(list(stream)), ' second word')

    def test_split_internal_tokens_never_released(self):
        for token in ('<unused20>', '<|tool_call|>', '<|im_start|>'):
            for split in range(1, len(token)):
                with self.subTest(token=token, split=split):
                    released = []
                    with self.assertRaises(CompatibilityError):
                        for event in validated_text_events([chunk('Safe text ' + token[:split]), chunk(token[split:]), chunk(finish='stop')]):
                            released.append(event)
                    self.assertNotIn('<', contents(released))

    def test_final_checks_retain_repetition_and_empty_rejection(self):
        for text in ('', '   ', '!!!', 'hello ' * 30):
            with self.subTest(text=text[:20]), self.assertRaises(CompatibilityError):
                list(validated_text_events([chunk(text), chunk(finish='stop')]))

    def test_usage_and_timings_available_at_finish(self):
        usage = {'prompt_tokens': 20, 'completion_tokens': 3, 'total_tokens': 23}
        timings = {'predicted_n': 3, 'predicted_ms': 300}
        events = list(validated_text_events([chunk('Done'), chunk(finish='length', timings=timings), {'choices': [], 'usage': usage}]))
        terminal = next(e for e in events if e.get('choices') and e['choices'][0]['finish_reason'])
        self.assertEqual(terminal['usage'], usage)
        self.assertEqual(terminal['timings'], timings)
        self.assertEqual(terminal['choices'][0]['finish_reason'], 'length')

    def test_reasoning_is_preserved(self):
        first = chunk(); first['choices'][0]['delta'] = {'reasoning_content': 'Reasoning text'}
        events = list(validated_text_events([first, chunk('Answer'), chunk(finish='stop')]))
        self.assertEqual(events[0]['choices'][0]['delta']['reasoning_content'], 'Reasoning text')
        self.assertEqual(contents(events), 'Answer')

    def test_malformed_choice_delta_and_unexpected_tool(self):
        bad = [{'choices': None}, {'choices': [None]}, {'choices': [{'index': 1, 'delta': {}}]}, {'choices': [{'index': 0, 'delta': []}]}, {'choices': [{'index': 0, 'delta': {'content': 1}}]}, {'choices': [{'index': 0, 'delta': {'tool_calls': [{'id': 'bad'}]}}]}]
        for event in bad:
            with self.subTest(event=event), self.assertRaises(CompatibilityError):
                list(validated_text_events([event]))

    def test_missing_finish_and_duplicate_finish_rejected(self):
        for events in ([chunk('Hello')], [chunk('Hello'), chunk(finish='stop'), chunk('Late')]):
            with self.assertRaises(CompatibilityError):
                list(validated_text_events(events))

    def test_sse_crlf_comments_multiline_and_unicode(self):
        data = ': heartbeat\r\n\r\ndata: {"choices": [],\r\ndata: "value": "שלום"}\r\n\r\ndata: [DONE]\r\n\r\n'.encode()
        self.assertEqual(list(read_events(io.BytesIO(data)))[0]['value'], 'שלום')

    def test_sse_invalid_truncated_error_and_event_limit(self):
        for data in (b'data: nope\n\n', b'data: {"error":{"code":"bad"}}\n\n', b'data: {}\n\n', b'data: ' + b'x' * 2_100_001):
            with self.subTest(length=len(data)), self.assertRaises(CompatibilityError):
                list(read_events(io.BytesIO(data)))

    def test_buffered_tool_response_keeps_usage_and_finish(self):
        response = {'choices': [{'message': {'tool_calls': [{'id': 'call', 'function': {'name': 'safe_tool', 'arguments': '{"value":"ok"}'}}]}, 'finish_reason': 'tool_calls'}], 'usage': {'completion_tokens': 12}, 'timings': {'predicted_ms': 1000}}
        events = list(openai_sse_events(response))
        parsed = [json.loads(e[6:]) for e in events if e != b'data: [DONE]\n\n']
        terminal = next(e for e in parsed if e.get('choices') and e['choices'][0]['finish_reason'])
        self.assertEqual(terminal['choices'][0]['finish_reason'], 'tool_calls')
        self.assertEqual(terminal['usage']['completion_tokens'], 12)
        self.assertEqual(sum(e == b'data: [DONE]\n\n' for e in events), 1)


class MockUpstream(BaseHTTPRequestHandler):
    requests = []
    completed = 0
    disconnected = threading.Event()
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        data = json.dumps({'data': [{'id': 'test', 'owned_by': 'llamacpp', 'meta': {'n_ctx': 32768}}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(payload)
        mode = payload['messages'][-1]['content']
        if mode == 'http-error':
            self.send_error(503); return
        if not payload.get('stream') or mode == 'json-fallback':
            message = {'role': 'assistant', 'content': 'A complete validated answer.'}
            finish = 'stop'
            if payload.get('tools'):
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'safe-call', 'type': 'function', 'function': {'name': 'safe_tool', 'arguments': '{"value":"ok"}'}}]}
                finish = 'tool_calls'
            data = json.dumps({'id': 'json-test', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 4}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Connection', 'close'); self.end_headers()
        self.close_connection = True
        try:
            if mode == 'prefill-delay':
                time.sleep(.15)
            self.wfile.write(encode_event(chunk('First visible words'))); self.wfile.flush()
            time.sleep(.15)
            if mode == 'disconnect':
                for i in range(150):
                    self.wfile.write(encode_event(chunk(' ' + str(i)))); self.wfile.flush(); time.sleep(.01)
                type(self).completed += 1
            elif mode == 'truncated':
                return
            elif mode == 'bad-json':
                self.wfile.write(b'data: not-json\n\n'); self.wfile.flush(); return
            else:
                self.wfile.write(encode_event(chunk(' then final words.')))
                self.wfile.write(encode_event(chunk(finish='stop', timings={'predicted_n': 8, 'predicted_ms': 150})))
                self.wfile.write(encode_event({'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18}}))
                self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.disconnected.set()


class StreamingHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_app = boundary.APP if hasattr(boundary, 'APP') else None
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), MockUpstream)
        cls.upthread = threading.Thread(target=lambda: cls.upstream.serve_forever(poll_interval=.02), daemon=True); cls.upthread.start()
        cls.logs = []
        caps = SimpleNamespace(session_sha256='test-session', ensure_basic=lambda p: SimpleNamespace(ok=True), ensure_chat=lambda p: SimpleNamespace(ok=True), ensure_tools=lambda p, t: SimpleNamespace(capability='NATIVE_TOOLS'))
        boundary.APP = SimpleNamespace(upstream=boundary.Upstream('http://127.0.0.1:' + str(cls.upstream.server_port)), capabilities=caps, audit=SimpleNamespace(write=lambda event, **fields: cls.logs.append((event, fields))), unpack_composer_transport=lambda p: p, trusted_composer_intent=lambda p: None)
        class Handler(boundary.BoundaryHandler):
            def _require_api_auth(self):
                pass
        cls.server = boundary.ReusableThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=lambda: cls.server.serve_forever(poll_interval=.02), daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.upstream.shutdown(); cls.server.server_close(); cls.upstream.server_close(); boundary.APP = cls.old_app

    def request(self, mode='normal', **extra):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        self.addCleanup(client.close)
        body = {'model': 'test', 'messages': [{'role': 'user', 'content': mode}], 'stream': True, **extra}
        client.request('POST', '/v1/chat/completions', json.dumps(body), {'Content-Type': 'application/json'})
        return client, client.getresponse()

    def test_progressive_delivery_and_metrics(self):
        started = time.monotonic(); _, response = self.request()
        first = response.readline(); first_time = time.monotonic() - started
        rest = response.read(); elapsed = time.monotonic() - started
        self.assertIn(b'First visible words', first)
        self.assertLess(first_time, elapsed - .10)
        self.assertEqual((first + rest).count(b'data: [DONE]'), 1)
        data = [json.loads(line[6:]) for line in (first + rest).splitlines() if line.startswith(b'data: {')]
        self.assertEqual(contents(data), 'First visible words then final words.')
        terminal = next(x for x in data if x.get('choices') and x['choices'][0]['finish_reason'])
        self.assertEqual(terminal['usage']['completion_tokens'], 8)
        self.assertEqual(terminal['timings']['predicted_n'], 8)

    def test_forwarded_metadata_advertises_requested_connection_close(self):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        self.addCleanup(client.close)
        client.request('GET', '/v1/models', headers={'Connection': 'close'})
        response = client.getresponse()
        self.assertTrue(response.will_close)
        self.assertEqual(json.loads(response.read())['data'][0]['meta']['n_ctx'], 32768)
        client.request('POST', '/v1/chat/completions', json.dumps({'model': 'test', 'messages': [{'role': 'user', 'content': 'normal'}], 'stream': True}), {'Content-Type': 'application/json'})
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'data: [DONE]', response.read())

    def test_json_error_advertises_requested_connection_close(self):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        self.addCleanup(client.close)
        client.request('POST', '/v1/chat/completions', json.dumps({'model': 'test', 'messages': []}), {'Content-Type': 'application/json', 'Connection': 'close'})
        response = client.getresponse()
        self.assertEqual(response.status, 400)
        self.assertTrue(response.will_close)
        response.read()
        client.request('GET', '/v1/models', headers={'Connection': 'close'})
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        response.read()

    def test_errors_have_no_successful_done(self):
        for mode in ('bad-json', 'truncated'):
            with self.subTest(mode=mode):
                _, response = self.request(mode); raw = response.read()
                self.assertIn(b'aag_compatibility_error', raw)
                self.assertNotIn(b'[DONE]', raw)
                self.assertNotIn(b'"finish_reason":"stop"', raw)

    def test_http_errors_and_invalid_options(self):
        _, response = self.request('http-error'); self.assertEqual(response.status, 502); response.read()
        _, response = self.request(stream_options='invalid'); self.assertEqual(response.status, 400); response.read()

    def test_stream_allows_prefill_longer_than_ordinary_request_timeout(self):
        client = boundary.Upstream('http://127.0.0.1:' + str(self.upstream.server_port), timeout=.02)
        with client.open_stream({'stream': True, 'messages': [{'role': 'user', 'content': 'prefill-delay'}]}) as response:
            events = list(validated_text_events(read_events(response)))
        self.assertEqual(contents(events), 'First visible words then final words.')
        self.assertEqual(client.timeout, .02)

    def test_json_only_upstream_fallback(self):
        _, response = self.request('json-fallback'); data = response.read()
        self.assertIn(b'A complete validated answer.', data)
        self.assertEqual(data.count(b'[DONE]'), 1)
        self.assertFalse(self.logs[-1][1]['true_stream'])

    def test_stop_and_sampler_options_forwarded_unchanged(self):
        _, response = self.request(stop=['END'], temperature=.2, seed=17, cache_prompt=True); response.read()
        payload = MockUpstream.requests[-1]
        for key, value in {'stop': ['END'], 'temperature': .2, 'seed': 17, 'cache_prompt': True}.items():
            self.assertEqual(payload[key], value)
        self.assertTrue(payload['stream'])

    def test_tool_calls_still_buffered_and_validated(self):
        tools = [{'type': 'function', 'function': {'name': 'safe_tool', 'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value'], 'additionalProperties': False}}}]
        _, response = self.request(tools=tools); raw = response.read()
        self.assertFalse(MockUpstream.requests[-1]['stream'])
        self.assertIn(b'safe_tool', raw)
        self.assertIn(b'"finish_reason":"tool_calls"', raw)
        self.assertEqual(raw.count(b'[DONE]'), 1)

    def test_disconnect_closes_upstream(self):
        MockUpstream.disconnected.clear()
        client, response = self.request('disconnect'); response.readline()
        response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        response.close(); client.close()
        self.assertTrue(MockUpstream.disconnected.wait(2))


if __name__ == '__main__':
    unittest.main(verbosity=2)
