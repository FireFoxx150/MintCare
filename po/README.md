# Translating MintCare

1. `po/update-pot.sh` — refresh `po/mintcare.pot` from the code.
2. Start a language: `msginit -i po/mintcare.pot -o po/de.po -l de_DE.UTF-8` (use your language code).
3. Translate the `msgstr` entries (Poedit or any text editor).
4. `po/compile.sh` builds `locale/<lang>/LC_MESSAGES/mintcare.mo`; running `./mintcare.py` from
   the source tree picks it up. `./build-deb.sh` installs compiled translations into
   `/usr/share/locale`.

To update an existing translation after the code changes, run `po/update-pot.sh`, then
`msgmerge --update po/es.po po/mintcare.pot` and translate the new or fuzzy entries.

Messages with values use numbered fields, e.g. `"{0} of {1} items selected"`: keep every `{0}`,
`{1:.1f}`… in your translation (you may reorder them). `run-tests.sh` checks this for every
`.po` file. Markup such as `<b>`, `<tt>` and `&amp;` must stay intact too.

Available: Spanish (`es`), French (`fr`), German (`de`), Portuguese (`pt`, Brazilian wording)
and Italian (`it`). Command output, the CLI and the exported health report stay in English.

For developers: put `# xgettext:no-python-format` on the line before any `_()` string that
contains `%` (e.g. `"{0}% full"`), otherwise xgettext flags it as a printf string and msgfmt
rejects translations that move the `%`.
