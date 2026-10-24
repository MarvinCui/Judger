import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trigger_agent.api import APIError, DeepSeek, Interrupted
from trigger_agent.config import Config


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.payload = body
        finish_reason = self.server.finish_reason
        send_done = self.server.send_done
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [
            {"choices": [{"delta": {"content": 'Hello'}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "write_file", "arguments": '{"path":"a.txt",'}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"content":"hello"}'}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": finish_reason}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        ]
        try:
            for event in events:
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
            if send_done:
                self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # Cancellation deliberately closes a response before the server finishes.


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.finish_reason = "tool_calls"
        self.server.send_done = True
        self.client = DeepSeek(Config(Path("."), base_url=f"http://127.0.0.1:{self.server.server_port}", api_key="test-only"))

    def test_sse_fragments_tool_calls_and_usage(self):
        text = []
        message, usage = self.client.stream([{"role": "user", "content": "test"}], on_text=text.append)
        self.assertEqual("".join(text), 'Hello')
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "write_file")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"])["content"], "hello")
        self.assertEqual(usage["total_tokens"], 15)
        self.assertEqual(self.server.payload["thinking"], {"type": "disabled"})

    def test_truncated_tool_call_not_returned_for_execution(self):
        self.server.finish_reason = "length"
        with self.assertRaises(APIError):
            self.client.stream([{"role": "user", "content": "test"}])

    def test_dropped_connection_not_returned_for_execution(self):
        self.server.send_done = False
        with self.assertRaises(APIError):
            self.client.stream([{"role": "user", "content": "test"}])

    def test_committed_feedback_interrupts_stream(self):
        cancelled = threading.Event()
        with self.assertRaises(Interrupted):
            self.client.stream([{"role": "user", "content": "test"}],
                               on_text=lambda text: cancelled.set(), cancelled=cancelled.is_set)


if __name__ == "__main__":
    unittest.main()
