"""gettext setup. Translations live in /usr/share/locale (installed) or ./locale (source tree)."""

import gettext
from pathlib import Path

DOMAIN = "mintcare"
_SOURCE_LOCALE = Path(__file__).resolve().parent.parent / "locale"


def load(localedir=None, languages=None):
    if localedir is None:
        localedir = str(_SOURCE_LOCALE) if _SOURCE_LOCALE.is_dir() else "/usr/share/locale"
    return gettext.translation(DOMAIN, localedir, languages=languages, fallback=True)


_t = load()
_ = _t.gettext
ngettext = _t.ngettext
