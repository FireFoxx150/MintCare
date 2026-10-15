# Releasing MintCare

Each step below that publishes something needs your accounts and your go-ahead.

## Before the first public release
- Licence: GPL-3.0-or-later (`LICENSE`, `debian/copyright`).
- Maintainer: the name/e-mail in `debian/control`, `debian/changelog` and `build-deb.sh` is public.
- Repository: https://github.com/FireFoxx150/MintCare

## Every release
1. Bump `__version__` in `mintcare/__init__.py` and add a `debian/changelog` entry
   (`dch -v X.Y.Z` from `devscripts`, or edit by hand).
2. `./run-tests.sh` and `./build-deb.sh`, then try the `.deb` from `dist/`.
3. `git commit -am "Release X.Y.Z" && git tag vX.Y.Z`.

## GitHub release (automated)
1. Create an empty repository on GitHub and push: `git remote add origin https://github.com/FireFoxx150/MintCare.git`
   then `git push -u origin main --tags`.
2. `.github/workflows/ci.yml` runs the tests and builds a `.deb` on every push.
3. Pushing a tag `vX.Y.Z` runs `.github/workflows/release.yml`, which checks the tag matches the
   version, runs the tests and attaches the `.deb` to a GitHub Release.

## Verifying a release
Each release has the `.deb`, a `SHA256SUMS` file, and a signed build-provenance attestation
(Sigstore, made by the release workflow; no keys to manage). Anyone can check that a `.deb` was
built by this repository's workflow from the tagged source:
```
sha256sum -c SHA256SUMS
gh attestation verify mintcare_X.Y.Z_all.deb --repo FireFoxx150/MintCare   # gh 2.49 or newer
```
MintCare's own updater checks the SHA-256 that GitHub records for the asset before installing.

## Launchpad PPA (updates through Update Manager)
One-time setup, all on your accounts:
1. Create a GPG key for the maintainer address in `debian/control`
   (`gpg --full-generate-key`), then a Launchpad account; upload the key
   (`gpg --keyserver keyserver.ubuntu.com --send-keys KEYID`, then add its fingerprint on Launchpad),
   sign the Ubuntu Code of Conduct and create a PPA named `mintcare`.
2. `sudo apt install devscripts debhelper dput`

Every release, after tagging:
```
packaging/ppa.sh noble 1        # Mint 22.x; builds dist/ppa/mintcare_X.Y.Z+ppa1~noble1_source.changes
dput ppa:YOUR-LAUNCHPAD-NAME/mintcare dist/ppa/mintcare_X.Y.Z+ppa1~noble1_source.changes
```
`ppa.sh` signs with your key when it finds one; without a key it builds unsigned as a dry run, which
Launchpad would reject. Bump the last argument (`2`, `3`…) to re-upload the same version. The PPA
version sorts above the GitHub `.deb` of the same release, so `.deb` users move to the PPA cleanly.

Users then run `sudo add-apt-repository ppa:YOUR-LAUNCHPAD-NAME/mintcare && sudo apt install mintcare`
and get updates through Mint's Update Manager.
