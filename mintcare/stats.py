"""Live system statistics sampled from /proc (cheap enough to poll every second)."""

import os
import shutil
import time

from .util import read


class Sampler:
    def __init__(self):
        self._cpu_prev = self._cpu_times()
        self._net_prev = (time.monotonic(), self._net_bytes())

    @staticmethod
    def _cpu_times():
        f = read("/proc/stat").splitlines()[0].split()[1:]
        vals = [int(x) for x in f]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)

    @staticmethod
    def _net_bytes():
        rx = tx = 0
        for line in read("/proc/net/dev").splitlines()[2:]:
            name, _, data = line.partition(":")
            name = name.strip()
            if name == "lo" or name.startswith(("veth", "docker", "br-", "virbr")):
                continue
            f = data.split()
            rx += int(f[0])
            tx += int(f[8])
        return rx, tx

    def sample(self):
        idle, total = self._cpu_times()
        pidle, ptotal = self._cpu_prev
        self._cpu_prev = (idle, total)
        dt = total - ptotal
        cpu = 0.0 if dt <= 0 else 100.0 * (1 - (idle - pidle) / dt)

        mem = {}
        for line in read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            mem[k] = int(v.split()[0]) * 1024
        mtotal = mem.get("MemTotal", 1)
        mavail = mem.get("MemAvailable", 0)
        stotal, sfree = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)

        now = time.monotonic()
        rx, tx = self._net_bytes()
        pt, (prx, ptx) = self._net_prev
        self._net_prev = (now, (rx, tx))
        el = max(now - pt, 1e-3)

        du = shutil.disk_usage("/")
        return {
            "cpu": max(0.0, min(100.0, cpu)),
            "load": os.getloadavg(),
            "mem_total": mtotal, "mem_used": mtotal - mavail,
            "mem_pct": 100.0 * (mtotal - mavail) / mtotal,
            "swap_total": stotal, "swap_used": stotal - sfree,
            "disk_total": du.total, "disk_used": du.used, "disk_free": du.free,
            "disk_pct": 100.0 * du.used / du.total,
            "rx_rate": max(0, rx - prx) / el, "tx_rate": max(0, tx - ptx) / el,
            "uptime": float(read("/proc/uptime", "0").split()[0]),
        }
