# Releasing MintCare

Nothing here has been published yet. Each step below needs your accounts and your go-ahead.

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

## Launchpad PPA (updates through Update Manager)
1. Create a Launchpad account, upload your GPG key, sign the Code of Conduct and create a PPA.
2. `sudo apt install devscripts debhelper dput`
3. For each Ubuntu base Mint uses (22.x = noble): set the changelog version to e.g.
   `1.7.1~ppa1~noble` and the series to `noble`, then
   `debuild -S -sa` (signs with your key) and `dput ppa:USER/mintcare ../mintcare_*_source.changes`.
4. Users then run `sudo add-apt-repository ppa:USER/mintcare && sudo apt install mintcare`
   and get updates through Mint's Update Manager.
