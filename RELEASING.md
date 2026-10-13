# Releasing MintCare

Nothing here has been published yet. Each step below needs your accounts and your go-ahead.

## Before the first public release
- **Choose a licence** (e.g. GPL-3.0-or-later or MIT), add a `LICENSE` file and fill in
  `debian/copyright`. Launchpad PPAs and most users expect an open-source licence.
- Check the maintainer name/e-mail in `debian/control` and `debian/changelog` (taken from your
  git config) — they become public.

## Every release
1. Bump `__version__` in `mintcare/__init__.py` and add a `debian/changelog` entry
   (`dch -v X.Y.Z` from `devscripts`, or edit by hand).
2. `./run-tests.sh` and `./build-deb.sh`, then try the `.deb` from `dist/`.
3. `git commit -am "Release X.Y.Z" && git tag vX.Y.Z`.

## GitHub release (automated)
1. Create an empty repository on GitHub and push: `git remote add origin git@github.com:USER/mintcare.git`
   then `git push -u origin main --tags`.
2. `.github/workflows/ci.yml` runs the tests and builds a `.deb` on every push.
3. Pushing a tag `vX.Y.Z` runs `.github/workflows/release.yml`, which checks the tag matches the
   version, runs the tests and attaches the `.deb` to a GitHub Release.

## Launchpad PPA (updates through Update Manager)
1. Create a Launchpad account, upload your GPG key, sign the Code of Conduct and create a PPA.
2. `sudo apt install devscripts debhelper dput`
3. For each Ubuntu base Mint uses (22.x = noble): set the changelog version to e.g.
   `1.6.0~ppa1~noble` and the series to `noble`, then
   `debuild -S -sa` (signs with your key) and `dput ppa:USER/mintcare ../mintcare_*_source.changes`.
4. Users then run `sudo add-apt-repository ppa:USER/mintcare && sudo apt install mintcare`
   and get updates through Mint's Update Manager.
