# Contributing to MintCare

Thank you for helping. MintCare is a GTK 3 Python app that cleans, optimises and diagnoses Linux Mint. Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before you take part.

## Running from source

```bash
./mintcare.py                 # the GUI
./mintcare.py <command>       # the command line, e.g. ./mintcare.py hardware
```

## Before you open a pull request

- Run `./run-tests.sh`. The tests never need root and never change the real system.
- If you change a user-visible string, update the five catalogues (es, fr, de, pt, it):
  1. Run `po/update-pot.sh`. It regenerates `po/mintcare.pot` and merges it into every catalogue.
  2. Translate each new entry.
  3. Check each catalogue with `msgfmt --check -o /dev/null po/<lang>.po`. Fuzzy entries count as untranslated.
- If you change the packaging, build with `./build-deb.sh` and check the result with `lintian --fail-on error,warning dist/mintcare_<version>_all.deb`. CI fails on warnings.
- Open your pull request from a branch. Don't commit to `main`.

## Conventions

- Python 3, using the standard library where possible. The GUI uses GTK 3 through PyGObject.
- Wrap every user-visible string in gettext: `_()` for text, `ngettext()` for counts, and `N_()` for strings translated later. Use `{0}` placeholders with `.format()`, not f-strings, so translators can reorder them. If a `_()` string contains `%`, put `# xgettext:no-python-format` on the line before it.
- Shell commands that run as root go through `runner.run_steps`. Quote every value from outside MintCare with `shlex.quote` (or `q()`). Never put a process name, file name, package name or serial number into shell syntax unquoted.
- Tweaks write only MintCare-owned files (names containing `mintcare`) or toggle services, and record a marker so they can be reverted. A user-level step must not use root.
- Health checks and report output must not expose personal details: names, paths, serial numbers, MAC addresses or credentials in URLs.
- Keep changes small and in the style of the surrounding code. Add a test for new non-trivial logic.
- Write commit messages in the imperative, with a short subject line.

## Reporting bugs and asking for features

Use the issue templates. For a security problem, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Releases

The maintainer makes releases. See [RELEASING.md](RELEASING.md).

## Licence

MintCare is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE).
