#!/bin/bash
# Regenerates po/mintcare.pot from the source, and merges it into any existing translations.
set -e
cd "$(dirname "$0")/.."
xgettext --language=Python --keyword=_ --keyword=ngettext:1,2 --keyword=N_ --from-code=UTF-8 \
  --package-name=MintCare --package-version="$(python3 -c 'import mintcare; print(mintcare.__version__)')" \
  --add-comments=TRANSLATORS --sort-by-file -o po/mintcare.pot mintcare/*.py
for po in po/*.po; do [ -e "$po" ] && msgmerge --quiet --update --backup=none "$po" po/mintcare.pot; done
echo "po/mintcare.pot: $(grep -c '^msgid ' po/mintcare.pot) strings"
