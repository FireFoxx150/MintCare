import unittest

from mintcare import maintenance

NOW = 1_000_000_000


class WeeklySummary(unittest.TestCase):
    def test_counts_last_seven_days_and_score_change(self):
        events = [{"ts": NOW - 3600, "kind": "clean", "ok": True},
                  {"ts": NOW - 7200, "kind": "clean", "ok": False},
                  {"ts": NOW - 86400, "kind": "repair", "ok": True},
                  {"ts": NOW - 10 * 86400, "kind": "clean", "ok": True},
                  {"ts": NOW - 100, "kind": "scheduled", "ok": True}]
        points = [{"ts": NOW - 10 * 86400, "score": 60}, {"ts": NOW - 5 * 86400, "score": 80},
                  {"ts": NOW - 60, "score": 95}]
        s = maintenance.weekly_summary(events, points, now=NOW)
        self.assertEqual((s["cleaned"], s["repairs"], s["checkups"]), (1, 1, 1))
        self.assertEqual((s["score_then"], s["score_now"]), (80, 95))

    def test_no_points_gives_no_scores(self):
        s = maintenance.weekly_summary([], [], now=NOW)
        self.assertIsNone(s["score_now"])
        self.assertIsNone(s["score_then"])


if __name__ == "__main__":
    unittest.main()


class MonthlySummary(unittest.TestCase):
    def test_a_month_includes_events_older_than_a_week(self):
        events = [{"ts": NOW - 20 * 86400, "kind": "clean", "ok": True}]
        self.assertEqual(maintenance.weekly_summary(events, [], now=NOW, days=30)["cleaned"], 1)
        self.assertEqual(maintenance.weekly_summary(events, [], now=NOW)["cleaned"], 0)
