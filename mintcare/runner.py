"""Run batches of shell steps, root steps through a single pkexec prompt."""

import os
import subprocess
import tempfile

BEGIN, END = "@@MINTCARE-BEGIN", "@@MINTCARE-END"
HELPER = "/usr/lib/mintcare/mintcare-root"


def _script(steps):
    parts = ["#!/bin/bash", "export DEBIAN_FRONTEND=noninteractive LC_ALL=C"]
    for sid, title, body in steps:
        parts.append(f'echo "{BEGIN} {sid}"\necho "==> {title}"\n'
                     f"(\n{body}\n) </dev/null 2>&1\n"
                     f'echo "{END} {sid} $?"')
    return "\n".join(parts) + "\n"


def run_steps(steps, root, on_line=print):
    """steps: list of (id, title, script). Returns {id: returncode}.

    on_line(text) receives human-readable output as it streams.
    A cancelled/failed pkexec authentication yields rc 126/127 for every step.
    """
    if not steps:
        return {}
    fd, path = tempfile.mkstemp(prefix="mintcare-", suffix=".sh", dir="/tmp")
    with os.fdopen(fd, "w") as f:
        f.write(_script(steps))
    os.chmod(path, 0o600 if not root else 0o644)
    if root and os.geteuid() != 0:
        # The .deb ships a helper with its own polkit action, so the prompt
        # names MintCare; running from source falls back to plain bash.
        cmd = ["pkexec", HELPER if os.access(HELPER, os.X_OK) else "/bin/bash", path]
    else:
        cmd = ["/bin/bash", path]
    results = {}
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, bufsize=1) as p:
            prc = _stream(p, results, on_line)
    finally:
        os.unlink(path)
    for sid, _t, _b in steps:
        if sid not in results:
            results[sid] = prc or 1
    if prc in (126, 127) and not any(rc == 0 for rc in results.values()):
        on_line("Authentication was cancelled or failed.")
    return results


def _stream(p, results, on_line):
    for line in p.stdout:
        line = line.rstrip("\n")
        if line.startswith(BEGIN):
            continue
        if line.startswith(END):
            _x, sid, rc = line.split()
            results[sid] = int(rc)
            on_line("    ✔ done" if rc == "0" else f"    ✖ failed (exit {rc})")
            continue
        if line.startswith("(Reading database ...") and "files and directories" not in line:
            continue  # dpkg progress spam
        on_line(line)
    return p.wait()
