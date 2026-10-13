#!/bin/bash
# Builds dist/mintcare_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")"
VER=$(python3 -c "import mintcare; print(mintcare.__version__)")
PKG=build/mintcare_${VER}_all
rm -rf build && mkdir -p "$PKG/DEBIAN" dist && chmod 755 "$PKG"

install -Dm755 mintcare.py                 "$PKG/usr/lib/mintcare/mintcare.py"
for f in mintcare/*.py; do install -Dm644 "$f" "$PKG/usr/lib/mintcare/$f"; done
install -Dm755 packaging/mintcare-root     "$PKG/usr/lib/mintcare/mintcare-root"
install -Dm755 packaging/mintcare          "$PKG/usr/bin/mintcare"
install -Dm644 packaging/org.mintcare.policy "$PKG/usr/share/polkit-1/actions/org.mintcare.policy"
install -Dm644 packaging/mintcare.svg      "$PKG/usr/share/icons/hicolor/scalable/apps/mintcare.svg"
sed -e 's|@EXEC@|mintcare|' -e 's|^Icon=.*|Icon=mintcare|' mintcare.desktop.in \
  | install -Dm644 /dev/stdin "$PKG/usr/share/applications/mintcare.desktop"
install -Dm644 README.md                   "$PKG/usr/share/doc/mintcare/README.md"

SIZE=$(du -sk "$PKG/usr" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CTRL
Package: mintcare
Version: $VER
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, pkexec | policykit-1
Recommends: timeshift, ufw
Installed-Size: $SIZE
Maintainer: Matthew <firefoxx.ms@gmail.com>
Description: Clean, optimise and repair Linux Mint
 MintCare diagnoses common problems (TCP BBR, broken packages, disk space,
 drivers, firewall, failed services...) and fixes them, applies reversible
 performance tweaks in the spirit of Chris Titus Tech's linutil, and cleans
 junk such as package caches, old kernels and stale logs.
CTRL

cat > "$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
  command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
fi
POST
cat > "$PKG/DEBIAN/postrm" <<'POST'
#!/bin/sh
set -e
# Tweaks are only reverted from inside MintCare; on purge just drop the bookkeeping.
if [ "$1" = purge ]; then rm -rf /var/lib/mintcare; fi
POST
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

find "$PKG" -name __pycache__ -prune -exec rm -rf {} +
fakeroot dpkg-deb --build -Zxz "$PKG" "dist/mintcare_${VER}_all.deb"
rm -rf build
echo "Built dist/mintcare_${VER}_all.deb"
