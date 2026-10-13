#!/usr/bin/env python3
"""MintCare launcher: no arguments opens the GUI, otherwise runs the CLI."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from mintcare.cli import main
        sys.exit(main(sys.argv[1:]))
    from mintcare.gui import main
    sys.exit(main())
