"""Power profiles from power-profiles-daemon: which one is active, and switching between them as the user."""
from .util import has_cmd, run


def available():
    return has_cmd("powerprofilesctl")


def parse_profiles(text):
    """Profile names from `powerprofilesctl list`, which prints a line such as '* performance:' per profile."""
    return [line.strip().lstrip("*").strip().rstrip(":")
            for line in text.splitlines()
            if line[:2] in ("* ", "  ") and line[2:3] != " " and line.rstrip().endswith(":")]


def profiles():
    rc, o = run(["powerprofilesctl", "list"], timeout=10)
    return parse_profiles(o) if rc == 0 else []


def active():
    rc, o = run(["powerprofilesctl", "get"], timeout=10)
    return o.strip() if rc == 0 else ""


def set_profile(name):
    """Switch to `name` (one of profiles()) for the logged-in user. True when the daemon accepted it."""
    rc, _o = run(["powerprofilesctl", "set", name], timeout=10)
    return rc == 0
