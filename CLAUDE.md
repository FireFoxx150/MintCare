# CLAUDE.md

MintCare: a GTK 3 Python app that cleans, optimises and diagnoses Linux Mint.
Licence GPL-3.0-or-later. Source: https://github.com/FireFoxx150/MintCare

## Where things are

- Commands, tests, releases and the repository layout: `docs/development.md`.
- Release steps: `RELEASING.md`.

## Rules

- Be concise. Skip preamble and restating my question.
- Publishing, pushing and PPA uploads need the maintainer's go-ahead. See `RELEASING.md`.
- Tests must never need root or change the real system. `tests/__init__.py` points the XDG config, state, data and cache folders at a temporary directory before any module is imported. Keep that in place when adding tests.
- Python 3, standard library where possible. GTK 3 through PyGObject for the GUI.
- Every user-visible string goes through gettext: `_("...")` for text, `ngettext(singular, plural, n)` for counts, and `N_()` for strings defined at module level and translated later. Use `{0}`, `{1}` placeholders with `.format()`, not f-strings, so translators can reorder them.
- A `_()` string that contains `%` needs `# xgettext:no-python-format` on the line before it.
- Shell commands that run as root go through `runner.run_steps` and are built with `shlex.quote` / `q()`. Never put a value from outside MintCare (process names, file names, package or Flatpak names, serials) into shell syntax. Quote it, or keep it out of the script.
- Tweaks write only MintCare-owned files (names containing `mintcare`) or toggle services, and record a marker so they can be reverted. A user-level step must not use root. The GameMode profile is the one exception: it writes `/etc/gamemode.ini`, the system-wide GameMode settings file, and backs up any file it replaces.
- Health checks and report output must not expose personal details. `report._scrub` removes names, paths, IP and MAC addresses, and credentials in URLs; new output that leaves the machine goes through it. The report blanks drive serial numbers when it collects its data.
- Keep changes small and in the style of the surrounding code. Add a test for new non-trivial logic.
- Keep the five translations (es, fr, de, pt, it) complete with no fuzzy entries when UI strings change.
