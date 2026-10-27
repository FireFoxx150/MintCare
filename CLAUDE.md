# CLAUDE.md

MintCare: a GTK 3 Python app that cleans, optimises and diagnoses Linux Mint.
Licence GPL-3.0-or-later. Source: https://github.com/FireFoxx150/MintCare

## Build and run

- Run from the source tree: `./mintcare.py` (GUI) or `./mintcare.py <command>` (CLI, e.g. `./mintcare.py hardware`).
- Build the Debian package: `./build-deb.sh` writes `dist/mintcare_<version>_all.deb`. Check it with `lintian dist/mintcare_<version>_all.deb`. CI runs lintian with `--fail-on error,warning`, so warnings fail the build.
- Build a Launchpad source package (never uploads): `packaging/ppa.sh noble 1`. It prints the `dput` command; uploading needs the maintainer's GPG key and Launchpad account.
- Compile translations: `po/compile.sh` (writes `locale/<lang>/LC_MESSAGES/mintcare.mo`).
- Refresh the translation template after changing UI strings: `po/update-pot.sh`. It regenerates `po/mintcare.pot` and merges it into every `po/*.po`.
- Validate a catalogue: `msgfmt --check -o /dev/null po/<lang>.po`. Fuzzy entries count as untranslated.

## Tests

- Full suite: `./run-tests.sh`. This is stdlib `unittest` with `-W error::ResourceWarning`, so no packages are needed.
- Single module: `python3 -m unittest tests.test_scripts -v`
- Single test: `python3 -m unittest tests.test_scripts.TestUntrustedText.test_title_and_quarantine_path`
- Headless CI run: `MINTCARE_SKIP_SYSTEM_TESTS=1 xvfb-run -a ./run-tests.sh`. Tests in `tests/test_system.py` need a real Mint desktop and are skipped when this variable is set.
- Tests must never need root or change the real system. `tests/__init__.py` points the XDG config, state, data and cache folders at a temporary directory before any module is imported. Keep that in place when adding tests.

## Releases

- Bump `__version__` in `mintcare/__init__.py`, add a `debian/changelog` entry, and update the version in `packaging/mintcare.1` and the install line in `README.md`.
- Run `./run-tests.sh` and `./build-deb.sh`, then commit and tag `vX.Y.Z`.
- Pushing the tag runs `.github/workflows/release.yml`, which publishes the `.deb`, `SHA256SUMS` and a provenance attestation.
- Publishing, pushing and PPA uploads need the maintainer's go-ahead. See `RELEASING.md`.

## Coding conventions

- Python 3, standard library where possible. GTK 3 through PyGObject for the GUI.
- Every user-visible string goes through gettext: `_("...")` for text, `ngettext(singular, plural, n)` for counts, and `N_()` for strings defined at module level and translated later. Use `{0}`, `{1}` placeholders with `.format()`, not f-strings, so translators can reorder them.
- A `_()` string that contains `%` needs `# xgettext:no-python-format` on the line before it.
- Shell commands that run as root go through `runner.run_steps` and are built with `shlex.quote` / `q()`. Never put a value from outside MintCare (process names, file names, package or Flatpak names, serials) into shell syntax. Quote it, or keep it out of the script.
- Tweaks write only MintCare-owned files (names containing `mintcare`) or toggle services, and record a marker so they can be reverted. A user-level step must not use root.
- Health checks and report output must not expose personal details. `report._scrub` and `assistant._private` remove names, paths, serials, MAC addresses and credentials in URLs; new output that leaves the machine goes through them.
- Keep changes small and in the style of the surrounding code. Add a test for new non-trivial logic.
- Keep the five translations (es, fr, de, pt, it) complete with no fuzzy entries when UI strings change.

## Repository layout

- `mintcare/`: application code. `gui.py` is the GTK interface, `cli.py` the command line, `checks.py` the health checks, `tweaks.py` the optimisation tweaks, `runner.py` the root-step runner, `hardware.py` the hardware and SMART data, `report.py` the health report, `assistant.py` the AI assistant.
- `tests/`: unit tests. `run-tests.sh` is the entry point.
- `po/`: translation sources. `locale/` and `*.mo` are generated and git-ignored.
- `debian/`: Debian packaging. `packaging/`: install helpers, polkit policy, PPA script, man page.
- `.github/workflows/`: `ci.yml` (tests, build, lint on every push) and `release.yml` (tagged releases).
- `docs/screenshots/`: README screenshots, rendered by `render.py`. Check them for personal details before committing.
