# Development

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

## Releases

- Bump `__version__` in `mintcare/__init__.py`, add a `debian/changelog` entry, and update the version in `packaging/mintcare.1` and the install line in `README.md`.
- Run `./run-tests.sh` and `./build-deb.sh`, then commit and tag `vX.Y.Z`.
- Pushing the tag runs `.github/workflows/release.yml`, which publishes the `.deb`, `SHA256SUMS` and a provenance attestation.

## Repository layout

- `mintcare/`: application code. `gui.py` is the GTK interface, `cli.py` the command line, `checks.py` the health checks, `tweaks.py` the optimisation tweaks, `runner.py` the root-step runner, `hardware.py` the hardware and SMART data, `report.py` the health report, `assistant.py` the AI assistant.
- `tests/`: unit tests. `run-tests.sh` is the entry point.
- `po/`: translation sources. `locale/` and `*.mo` are generated and git-ignored.
- `debian/`: Debian packaging. `packaging/`: install helpers, polkit policy, PPA script, man page.
- `.github/workflows/`: `ci.yml` (tests, build, lint on every push) and `release.yml` (tagged releases).
- `docs/screenshots/`: README screenshots, rendered by `render.py`. Check them for personal details before committing.
