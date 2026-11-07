import unittest

from mintcare import theme


class Themes(unittest.TestCase):
    def test_every_theme_has_status_colours_and_a_name(self):
        for key, _title in theme.NAMES:
            if key == "system":
                continue
            self.assertIn(key, theme.PALETTES, key)
            self.assertEqual(set(theme.STATUS[key]), {"ok", "warn", "fail"}, key)

    def test_colour_blind_theme_has_no_red_green_pair(self):
        st = theme.STATUS["colourblind"]
        ok, warn, fail = st["ok"], st["warn"], st["fail"]
        self.assertNotIn(ok.lower(), ("#10b981", "#22c55e"))
        # the green colour must not be used for any status in this theme
        self.assertEqual(len({ok, warn, fail}), 3)
        self.assertGreater(theme.PALETTES["colourblind"]["emerald"], "#000000")

    def test_resolve_unknown_falls_back_to_dark(self):
        self.assertEqual(theme.resolve("nonsense"), "dark")
        self.assertEqual(theme.resolve("colourblind"), "colourblind")


if __name__ == "__main__":
    unittest.main()


class FocusRing(unittest.TestCase):
    def test_every_theme_draws_a_focus_outline(self):
        for name in theme.PALETTES:
            with self.subTest(theme=name):
                css = theme.css(name).decode()
                self.assertIn("*:focus { outline: 2px solid", css)
