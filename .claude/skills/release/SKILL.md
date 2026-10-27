---
name: release
description: Prepare a MintCare release - version bump, changelog, tests, .deb build and lintian. Stops before pushing or tagging so the maintainer can confirm.
disable-model-invocation: true
---

Prepare a MintCare release. Ask for the new version number first if the user did not give one.

1. Check `git status`. Stop if there are uncommitted changes to tracked files.
2. Bump `__version__` in `mintcare/__init__.py`, the version in `packaging/mintcare.1` (title line and date), and the install line in `README.md`.
3. Add a `debian/changelog` entry above the previous one. Use the maintainer line already in the file. Check the weekday with `date -d today`.
4. Run `./run-tests.sh`. Stop if anything fails.
5. Run `./build-deb.sh`, then `lintian dist/mintcare_<version>_all.deb`. Stop on any error or warning.
6. Commit with the message `<version>: <one-line summary>`.
7. Report the commit, the .deb path and its SHA256. Then ask before pushing or tagging. Do not push, tag or publish without a clear yes.

Give the user the install command for their own terminal, since sudo does not work from the session:
`sudo apt install ./dist/mintcare_<version>_all.deb`
