#!/usr/bin/env python3
# PostToolUse hook: byte-compile a Python file after Edit/Write so a syntax error shows up at once.
import json, py_compile, sys

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
if path.endswith(".py"):
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        print(f"Syntax error in {path}: {e.msg}", file=sys.stderr)
        sys.exit(2)
