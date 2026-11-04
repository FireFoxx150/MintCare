"""DNS benchmark: how quickly public DNS servers answer one lookup, timed with plain UDP queries."""
import secrets
import socket
import statistics
import struct
import time

RESOLVERS = (("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Quad9", "9.9.9.9"), ("OpenDNS", "208.67.222.222"))
QNAME = "example.com"


def query(name, qid):
    """A DNS query packet asking for the IPv4 address (record type A) of `name`."""
    labels = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split("."))
    return struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack(">HH", 1, 1)


def ping(server, name=QNAME, timeout=2.0, port=53):
    """Milliseconds `server` took to answer, or None when it doesn't answer correctly in time."""
    qid = secrets.randbelow(1 << 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((server, port))                 # the kernel then drops packets from anyone else
            t0 = time.perf_counter()
            s.send(query(name, qid))
            reply = s.recv(512)
            ms = (time.perf_counter() - t0) * 1000
        except OSError:
            return None
    answered = len(reply) >= 12 and struct.unpack(">H", reply[:2])[0] == qid and bool(reply[2] & 0x80)
    return ms if answered else None


def benchmark(servers=RESOLVERS, name=QNAME, tries=3):
    """[(label, median ms or None)] for each server, fastest first; a server that never answers goes last."""
    rows = []
    for label, addr in servers:
        times = [t for t in (ping(addr, name) for _i in range(tries)) if t is not None]
        rows.append((label, statistics.median(times) if times else None))
    return sorted(rows, key=lambda r: (r[1] is None, r[1] or 0))
