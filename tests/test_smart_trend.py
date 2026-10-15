"""SMART counters are recorded and growth is reported."""
import json
from pathlib import Path

from mintcare import hardware
from tests.helpers import TempDirTest

DAY = 86400


def drive(**counters):
    return {"serial": "S1", "model": "Disk", "device": "/dev/sda", "health": "ok", "notes": [],
            "counters": dict({"realloc": 0, "pending": 0, "offline_unc": 0, "crc": 28}, **counters)}


class SmartTrend(TempDirTest):
    def test_no_note_without_growth(self):
        p = Path(self.tmp) / "h.json"
        hardware.track([drive()], p, now=1000 * DAY)
        d = drive()
        hardware.track([d], p, now=1001 * DAY)
        self.assertEqual(d["notes"], [])
        self.assertEqual(d["health"], "ok")

    def test_growth_raises_health(self):
        p = Path(self.tmp) / "h.json"
        hardware.track([drive()], p, now=1000 * DAY)
        d = drive(crc=31, pending=2)
        hardware.track([d], p, now=1005 * DAY)
        levels = sorted(level for level, _t in d["notes"])
        self.assertEqual(levels, ["fail", "warn"])           # pending → fail, crc growth → warn
        self.assertEqual(d["health"], "fail")
        self.assertTrue(any(t.startswith("3 ") for _l, t in d["notes"]))

    def test_old_samples_ignored_and_daily_dedup(self):
        p = Path(self.tmp) / "h.json"
        hardware.track([drive()], p, now=1000 * DAY)
        hardware.track([drive()], p, now=1000 * DAY + 60)     # same day, same counters: not stored
        self.assertEqual(len(json.loads(p.read_text())["S1"]), 1)
        d = drive(realloc=5)
        hardware.track([d], p, now=1000 * DAY + 40 * DAY)    # only sample is older than 30 days
        self.assertEqual(d["notes"], [])

    def test_drive_without_counters_is_skipped(self):
        p = Path(self.tmp) / "h.json"
        d = {"serial": "X", "model": "USB", "device": "/dev/sdb", "health": "info", "notes": []}
        hardware.track([d], p)
        self.assertEqual(json.loads(p.read_text()), {})
