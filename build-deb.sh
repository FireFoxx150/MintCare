#!/bin/bash
# Builds dist/mintcare_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")"
VER=$(python3 -c "import mintcare; print(mintcare.__version__)")
PKG=build/mintcare_${VER}_all
rm -rf build && mkdir -p "$PKG/DEBIAN" dist && chmod 755 "$PKG"

packaging/install-tree.sh "$PKG"

SIZE=$(du -sk "$PKG/usr" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CTRL
Package: mintcare
Version: $VER
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, pkexec | policykit-1
Recommends: timeshift, ufw, udisks2, gir1.2-xapp-1.0, gir1.2-webkit2-4.1, libnotify-bin
Suggests: smartmontools, flatpak
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
