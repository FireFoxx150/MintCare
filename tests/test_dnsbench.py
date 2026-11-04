"""DNS benchmark: the query packet, the timing of one answer over UDP, and the order of the results."""
import socket
import struct
import threading
import unittest
from unittest import mock

from mintcare import dnsbench


class Stub:
    """A resolver on 127.0.0.1 that answers each query with an empty, correct response (or a wrong ID)."""

    def __init__(self, bad_id=False):
        self.bad_id = bad_id
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.done.is_set():
            try:
                data, addr = self.sock.recvfrom(512)
            except socket.timeout:
                continue
            ident = bytes([data[0] ^ 0xFF, data[1]]) if self.bad_id else data[:2]
            self.sock.sendto(ident + struct.pack(">HHHHH", 0x8180, 1, 0, 0, 0) + data[12:], addr)

    def close(self):
        self.done.set()
        self.thread.join()
        self.sock.close()


class Query(unittest.TestCase):
    def test_header_question_type_a_and_class_in(self):
        self.assertEqual(dnsbench.query("example.com", 0x1234),
                         b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
                         b"\x07example\x03com\x00\x00\x01\x00\x01")


class Ping(unittest.TestCase):
    def test_an_answer_is_timed(self):
        stub = Stub()
        try:
            ms = dnsbench.ping("127.0.0.1", port=stub.port)
        finally:
            stub.close()
        self.assertIsNotNone(ms)
        self.assertGreaterEqual(ms, 0)

    def test_a_reply_with_another_id_is_no_answer(self):
        stub = Stub(bad_id=True)
        try:
            self.assertIsNone(dnsbench.ping("127.0.0.1", port=stub.port))
        finally:
            stub.close()

    def test_a_closed_port_is_no_answer(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertIsNone(dnsbench.ping("127.0.0.1", port=port, timeout=1))


class Benchmark(unittest.TestCase):
    def test_fastest_first_and_silent_servers_last(self):
        times = {"1.1.1.1": 12.0, "8.8.8.8": None, "9.9.9.9": 20.0}
        with mock.patch.object(dnsbench, "ping", side_effect=lambda addr, *a: times[addr]):
            rows = dnsbench.benchmark([("A", "1.1.1.1"), ("B", "8.8.8.8"), ("C", "9.9.9.9")], tries=2)
        self.assertEqual([r[0] for r in rows], ["A", "C", "B"])
        self.assertIsNone(rows[-1][1])


if __name__ == "__main__":
    unittest.main()
