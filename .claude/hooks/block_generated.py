#!/usr/bin/env python3
# PreToolUse hook: refuse edits to generated files (built packages, compiled translations).
import fnmatch, json, sys

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
blocked = ("*/dist/*", "*/locale/*", "*.mo")
if any(fnmatch.fnmatch(path, pat) for pat in blocked):
    print(f"{path} is generated; change its source and rebuild instead.", file=sys.stderr)
    sys.exit(2)
