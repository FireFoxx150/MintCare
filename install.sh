#!/bin/bash
# Installs MintCare for the current user (no root needed): menu entry + 'mintcare' command.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$DIR/mintcare.py" "$HOME/.local/bin/mintcare"
sed "s|@EXEC@|$DIR/mintcare.py|" "$DIR/mintcare.desktop.in" > "$HOME/.local/share/applications/mintcare.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed. Launch 'MintCare' from the menu, or run: mintcare  (GUI) / mintcare scan (CLI)"
