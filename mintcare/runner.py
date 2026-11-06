"""Run batches of shell steps, root steps through a single pkexec prompt."""

import fcntl
import os
import shlex
from .i18n import _
import subprocess

BEGIN, END = "@@MINTCARE-BEGIN", "@@MINTCARE-END"
HELPER = "/usr/lib/mintcare/mintcare-root"
# Runs the batch that arrives on stdin, from a private copy in a folder only its owner can write to.
# packaging/mintcare-root has the same logic; keep the two in step.
ROOT_RUN = (
    'work=$(mktemp -d /tmp/mintcare-batch.XXXXXXXX) || exit 1\n'
    'trap \'rm -rf -- "$work"\' EXIT\n'
    'cat > "$work/batch.sh" && [ -s "$work/batch.sh" ] && /bin/bash "$work/batch.sh" </dev/null\n'
)


def _script(steps):
    parts = ["#!/bin/bash", "export DEBIAN_FRONTEND=noninteractive LC_ALL=C"]
    for sid, title, body in steps:
        parts.append(f'echo "{BEGIN} {sid}"\necho {shlex.quote("==> " + title)}\n'
                     f"(\n{body}\n) </dev/null 2>&1\n"
                     f'echo "{END} {sid} $?"')
    return "\n".join(parts) + "\n"


def _sealed(text):
    """The batch in an in-memory file, sealed read-only and rewound to the start.

    Sealing happens before the password prompt, so root runs the bytes the user
    approved, whatever else runs as the same user in the meantime.
    """
    fd = os.memfd_create("mintcare-batch", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(text.encode())
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
        os.lseek(fd, 0, os.SEEK_SET)
    except BaseException:
        os.close(fd)
        raise
    return fd


def run_steps(steps, root, on_line=print):
    """steps: list of (id, title, script). Returns {id: returncode}.

    on_line(text) receives human-readable output as it streams.
    A cancelled/failed pkexec authentication yields rc 126/127 for every step.
    """
    if not steps:
        return {}
    batch = _sealed(_script(steps))
    if root and os.geteuid() != 0:
        # The .deb ships a helper with its own polkit action, so the prompt
        # names MintCare; running from source falls back to plain bash.
        cmd = ["pkexec", HELPER] if os.access(HELPER, os.X_OK) else ["pkexec", "/bin/bash", "-c", ROOT_RUN]
    else:
        cmd = ["/bin/bash", "-c", ROOT_RUN]
    results = {}
    try:
        with subprocess.Popen(cmd, stdin=batch, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, bufsize=1) as p:
            prc = _stream(p, results, on_line)
    finally:
        os.close(batch)
    for sid, _t, _b in steps:
        if sid not in results:
            results[sid] = prc or 1
    if prc in (126, 127) and not any(rc == 0 for rc in results.values()):
        on_line(_("Authentication was cancelled or failed."))
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
