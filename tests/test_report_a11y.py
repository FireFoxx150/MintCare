import re
import unittest

from mintcare import report


def sample():
    return {"generated": "2026-10-28 10:00", "version": "1.12.6", "private": True, "score": 90,
            "counts": {"fail": 0, "warn": 1, "info": 2}, "checks": [("Packages", "Pending updates", "warn", "3 updates")],
            "system": [("Kernel", "7.0")], "hardware": [("cpu", "Test CPU")], "drives": [], "sensors": [],
            "throttling": [], "boot": {}, "blame": [], "tweaks": [], "startup": [], "flatpaks": [], "history": []}


class ReportAccessibility(unittest.TestCase):
    def test_page_has_language_title_heading_and_named_images(self):
        page = report.render_html(sample())
        self.assertRegex(page, r"<html lang='[a-z]{2}'>")
        self.assertIn("<title>", page)
        self.assertIn("<h1>", page)
        for img in re.findall(r"<img[^>]*>", page):
            self.assertIn("alt=", img, img)

    def test_tables_have_header_rows(self):
        page = report.render_html(sample())
        self.assertIn("<th", page)


if __name__ == "__main__":
    unittest.main()
