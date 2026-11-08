#!/bin/bash
# Installs MintCare's files under DESTDIR (shared by build-deb.sh and debian/rules).
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:?usage: install-tree.sh DESTDIR}"
install -Dm755 mintcare.py                   "$DEST/usr/lib/mintcare/mintcare.py"
for f in mintcare/*.py; do install -Dm644 "$f" "$DEST/usr/lib/mintcare/$f"; done
install -Dm755 packaging/mintcare-root       "$DEST/usr/lib/mintcare/mintcare-root"
install -Dm755 packaging/mintcare            "$DEST/usr/bin/mintcare"
install -Dm644 packaging/org.mintcare.policy "$DEST/usr/share/polkit-1/actions/org.mintcare.policy"
install -Dm644 packaging/mintcare.svg        "$DEST/usr/share/icons/hicolor/scalable/apps/mintcare.svg"
sed -e 's|@EXEC@|mintcare|' -e 's|^Icon=.*|Icon=mintcare|' mintcare.desktop.in \
  | install -Dm644 /dev/stdin "$DEST/usr/share/applications/mintcare.desktop"
install -Dm644 README.md                     "$DEST/usr/share/doc/mintcare/README.md"
install -Dm644 debian/copyright              "$DEST/usr/share/doc/mintcare/copyright"
install -d "$DEST/usr/share/man/man1"
gzip -9n < packaging/mintcare.1 > "$DEST/usr/share/man/man1/mintcare.1.gz"
chmod 644 "$DEST/usr/share/man/man1/mintcare.1.gz"
po/compile.sh "$DEST/usr/share/locale" >/dev/null
if [ -d "$DEST/usr/share/locale" ]; then          # independent of the builder's umask
  find "$DEST/usr/share/locale" -type d -exec chmod 755 {} +
  find "$DEST/usr/share/locale" -type f -exec chmod 644 {} +
fi
