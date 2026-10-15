"""The internet speed test measures against a server with Cloudflare's __down/__up endpoints."""
import http.server
import threading
import unittest
from urllib.parse import parse_qs, urlparse

from mintcare import troubleshoot


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        n = int(parse_qs(urlparse(self.path).query).get("bytes", ["0"])[0])
        self.send_response(200)
        self.send_header("Content-Length", str(n))
        self.end_headers()
        self.wfile.write(bytes(n))

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class SpeedTest(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.host = f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_measures_all_three(self):
        r = troubleshoot.speed_test(self.host, seconds=0.5)
        self.assertGreater(r["ping_ms"], 0)
        self.assertGreater(r["down_mbps"], 1)
        self.assertGreater(r["up_mbps"], 1)

    def test_unreachable_raises(self):
        with self.assertRaises(OSError):
            troubleshoot.speed_test("http://127.0.0.1:9")
