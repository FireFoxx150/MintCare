"""The local assistant talks only to Ollama on this PC: the model list, a streamed answer, and clear errors.

Module under test: mintcare/assistant.py (local_reply, ollama_models, backend choice). No existing tests cover
it, and the tests use a stub Ollama server on 127.0.0.1, so nothing leaves the machine.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from mintcare import assistant
from tests.helpers import TempDirTest


class StubOllama(BaseHTTPRequestHandler):
    last_request = None

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, json.dumps({"models": [{"name": "qwen2.5-coder:14b"}]}).encode())

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        StubOllama.last_request = req
        if req.get("model") == "gone":
            self._send(404, json.dumps({"error": "model not found"}).encode())
            return
        lines = [{"message": {"role": "assistant", "content": "Hel"}, "done": False},
                 {"message": {"role": "assistant", "content": "lo"}, "done": False},
                 {"done": True}]
        self._send(200, b"".join(json.dumps(x).encode() + b"\n" for x in lines), "application/x-ndjson")


class LocalAssistant(TempDirTest):
    def setUp(self):
        super().setUp()
        self.server = HTTPServer(("127.0.0.1", 0), StubOllama)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        patch = mock.patch.object(assistant, "OLLAMA", f"http://127.0.0.1:{self.server.server_address[1]}")
        patch.start()
        self.addCleanup(patch.stop)
        assistant.set_backend("local")
        assistant.set_local_model("qwen2.5-coder:14b")

    def test_model_list_comes_from_ollama(self):
        self.assertEqual(assistant.ollama_models(), ["qwen2.5-coder:14b"])

    def test_answer_streams_and_is_kept_as_plain_text(self):
        chunks = []
        res = assistant.local_reply([{"role": "user", "content": "hi"}], chunks.append)
        self.assertIsNone(res["error"])
        self.assertEqual(chunks, ["Hel", "lo"])
        self.assertEqual(res["text"], "Hello")
        self.assertEqual(res["messages"], [{"role": "assistant", "content": "Hello"}])
        self.assertIsNone(res["cost"])                 # no price on this PC, so the page shows no cost
        self.assertEqual(StubOllama.last_request["messages"][0]["role"], "system")

    def test_the_chat_entry_point_uses_the_local_model_when_chosen(self):
        res = assistant.stream_reply([{"role": "user", "content": "hi"}], lambda t: None, allow_checks=True)
        self.assertEqual(res["text"], "Hello")
        self.assertEqual(res["model"], "qwen2.5-coder:14b")

    def test_a_model_that_is_not_installed_says_how_to_add_it(self):
        assistant.set_local_model("gone")
        res = assistant.local_reply([{"role": "user", "content": "hi"}], lambda t: None)
        self.assertIn("ollama pull gone", res["error"])
        self.assertEqual(res["messages"], [])

    def test_ollama_not_running_is_reported_not_raised(self):
        with mock.patch.object(assistant, "OLLAMA", "http://127.0.0.1:1"):
            self.assertIsNone(assistant.ollama_models())
            res = assistant.local_reply([{"role": "user", "content": "hi"}], lambda t: None)
        self.assertIn("Couldn't reach Ollama", res["error"])


class History(unittest.TestCase):
    def test_claude_blocks_keep_their_text_and_drop_checks(self):
        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": [{"type": "text", "text": "A"}, {"type": "tool_use", "id": "x"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        ]
        msgs = assistant._ollama_messages(history)
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant"])
        self.assertEqual(msgs[2]["content"], "A")


class Address(unittest.TestCase):
    def test_the_default_address_is_this_pc_only(self):
        self.assertTrue(assistant.OLLAMA.startswith("http://127.0.0.1:"))


if __name__ == "__main__":
    unittest.main()
