#!/bin/bash
# Compiles po/<lang>.po into <dest>/<lang>/LC_MESSAGES/mintcare.mo (default dest: ./locale).
set -e
cd "$(dirname "$0")/.."
dest="${1:-locale}"
for po in po/*.po; do
  [ -e "$po" ] || continue
  lang="$(basename "$po" .po)"
  mkdir -p "$dest/$lang/LC_MESSAGES"
  msgfmt --check -o "$dest/$lang/LC_MESSAGES/mintcare.mo" "$po"
  echo "compiled $lang"
done
