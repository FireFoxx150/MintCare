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


def N_(s):
    """Mark a string for translation without translating it here (it's passed to _() where it's shown)."""
    return s


def current_language():
    """The language gettext is using, such as 'de' (or 'en' when there is no translation)."""
    localedir = str(_SOURCE_LOCALE) if _SOURCE_LOCALE.is_dir() else "/usr/share/locale"
    found = gettext.find(DOMAIN, localedir)
    return Path(found).parts[-3].split("_")[0] if found else "en"
