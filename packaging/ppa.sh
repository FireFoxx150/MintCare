#!/bin/bash
# Build a Launchpad source package for one Ubuntu series (Mint 22.x = noble).
#   packaging/ppa.sh [SERIES] [PPA_REVISION]     e.g. packaging/ppa.sh noble 1
# Signs with your GPG key when the maintainer address has one (needed for upload);
# otherwise builds unsigned as a dry run. Never uploads: it prints the dput command.
set -euo pipefail
cd "$(dirname "$0")/.."
series="${1:-noble}"
rev="${2:-1}"
ver="$(python3 -c 'import mintcare; print(mintcare.__version__)')"
pkgver="${ver}+ppa${rev}~${series}1"   # "+" sorts above the GitHub .deb of the same version
maint="$(sed -n 's/^Maintainer: .*<\(.*\)>/\1/p' debian/control)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
src="$work/mintcare-$pkgver"
git archive --format=tar HEAD | (mkdir -p "$src" && tar -x -C "$src")
(
  cd "$src"
  # Same entry as the top of debian/changelog, re-versioned for this series
  DEBEMAIL="$maint" dch --newversion "$pkgver" --distribution "$series" --force-distribution \
    "Launchpad build of MintCare $ver for $series."
  if gpg --list-secret-keys "$maint" >/dev/null 2>&1; then
    debuild -S -sa -k"$maint" -d
  else
    echo "No GPG secret key for $maint: building unsigned (dry run, can't be uploaded)." >&2
    debuild -S -sa -us -uc -d
  fi
)
mkdir -p dist/ppa
cp "$work"/mintcare_"$pkgver"* dist/ppa/
echo
echo "Source package in dist/ppa/:"
ls -1 dist/ppa/mintcare_"$pkgver"*
echo
echo "Upload (after checking it):  dput ppa:YOUR-LAUNCHPAD-NAME/mintcare dist/ppa/mintcare_${pkgver}_source.changes"
