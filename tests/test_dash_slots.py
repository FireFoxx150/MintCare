import unittest

from mintcare.gui import DASH_ORDER, dash_slots


def positions(slots):
    return {key: (c, r, w, h) for key, c, r, w, h in slots}


class DashboardPacking(unittest.TestCase):
    def test_default_order_gives_the_designed_wide_layout(self):
        p = positions(dash_slots(DASH_ORDER, "wide"))
        self.assertEqual(p["cpu"], (0, 0, 1, 1))
        self.assertEqual(p["memory"], (1, 0, 1, 1))
        self.assertEqual(p["disk"], (2, 0, 1, 1))
        self.assertEqual(p["performance"], (0, 1, 2, 1))
        self.assertEqual(p["health"], (2, 1, 1, 2))
        self.assertEqual(p["gpu"], (0, 3, 2, 1))

    def test_cards_never_overlap(self):
        for mode in ("wide", "medium", "narrow"):
            cells = set()
            for key, c, r, w, h in dash_slots(DASH_ORDER, mode):
                for dr in range(h):
                    for dc in range(w):
                        self.assertNotIn((r + dr, c + dc), cells, (mode, key))
                        cells.add((r + dr, c + dc))

    def test_user_order_moves_a_card_to_the_front(self):
        keys = ["details"] + [k for k in DASH_ORDER if k != "details"]
        self.assertEqual(positions(dash_slots(keys, "wide"))["details"], (0, 0, 1, 1))

    def test_narrow_is_one_column(self):
        self.assertTrue(all(c == 0 for _k, c, _r, _w, _h in dash_slots(DASH_ORDER, "narrow")))


if __name__ == "__main__":
    unittest.main()
