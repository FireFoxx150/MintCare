"""The gettext plumbing works: a compiled catalogue translates marked strings."""
import gettext
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from mintcare import i18n
from tests.helpers import TempDirTest

PO = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Dashboard"
msgstr "Übersicht"
'''


@unittest.skipUnless(shutil.which("msgfmt"), "msgfmt not installed")
class Gettext(TempDirTest):
    def test_compiled_catalogue_translates(self):
        po = Path(self.tmp) / "de.po"
        po.write_text(PO)
        mo = Path(self.tmp) / "locale" / "de" / "LC_MESSAGES" / "mintcare.mo"
        mo.parent.mkdir(parents=True)
        subprocess.run(["msgfmt", "-o", str(mo), str(po)], check=True)
        t = i18n.load(str(Path(self.tmp) / "locale"), languages=["de"])
        self.assertEqual(t.gettext("Dashboard"), "Übersicht")
        self.assertEqual(t.gettext("Not translated"), "Not translated")

    def test_fallback_without_catalogue(self):
        t = i18n.load(self.tmp, languages=["zz"])
        self.assertEqual(t.gettext("Dashboard"), "Dashboard")

    def test_pot_is_generated(self):
        root = Path(__file__).resolve().parent.parent
        out = Path(self.tmp) / "test.pot"        # not po/mintcare.pot: keep the work tree clean
        subprocess.run(["xgettext", "--language=Python", "--keyword=_", "--from-code=UTF-8", "-o", str(out),
                        *map(str, sorted((root / "mintcare").glob("*.py")))], check=True)
        pot = out.read_text()
        for s in ("Dashboard", "Diagnose & Fix", "TCP BBR congestion control", "Package download cache"):
            self.assertIn(f'msgid "{s}"', pot)

    def test_shipped_translations_keep_placeholders(self):
        """A translation that drops or renames a {0} would crash .format() at runtime."""
        root = Path(__file__).resolve().parent.parent
        for po in sorted((root / "po").glob("*.po")):
            mo = Path(self.tmp) / (po.stem + ".mo")
            subprocess.run(["msgfmt", "--check", "-o", str(mo), str(po)], check=True)
            with open(mo, "rb") as f:
                cat = gettext.GNUTranslations(f)._catalog
            fields = lambda s: sorted(set(re.findall(r"(?<!\{)\{[^{}]*\}", s)))
            for key, text in cat.items():
                msgid = key[0] if isinstance(key, tuple) else key
                if msgid:
                    with self.subTest(lang=po.stem, msgid=msgid):
                        self.assertEqual(fields(text), fields(msgid))


if __name__ == "__main__":
    unittest.main()
