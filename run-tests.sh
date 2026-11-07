#!/bin/bash
# Runs the test suite (stdlib unittest; no extra packages needed). Tests never need root
# and never change the system: user-level state goes to throwaway temp folders.
cd "$(dirname "$0")" && exec python3 -W error::ResourceWarning -m unittest discover -s tests -t . "$@"
