# Translating MintCare

1. `po/update-pot.sh` — refresh `po/mintcare.pot` from the code.
2. Start a language: `msginit -i po/mintcare.pot -o po/de.po -l de_DE.UTF-8` (use your language code).
3. Translate the `msgstr` entries (Poedit or any text editor).
4. `po/compile.sh` builds `locale/<lang>/LC_MESSAGES/mintcare.mo`; running `./mintcare.py` from
   the source tree picks it up. `./build-deb.sh` installs compiled translations into
   `/usr/share/locale`.

Messages built with f-strings (counts, sizes, file names) are still English-only; they need
converting to `_("… {n} …").format(n=…)` before they can be translated.
