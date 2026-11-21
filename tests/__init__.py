# Keep every test (and the CLI subprocesses they start) out of the real ~/.config, ~/.local/state
# and ~/.local/share: MintCare reads these paths when its modules are first imported.
import atexit
import os
import shutil
import tempfile

_home = tempfile.mkdtemp(prefix="mintcare-test-xdg-")
atexit.register(shutil.rmtree, _home, True)
for _var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[_var] = os.path.join(_home, _var.lower())
# Launchpad builds run with a HOME that doesn't exist, so code that reads Path.home() needs a real one here.
os.environ["HOME"] = os.path.join(_home, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
