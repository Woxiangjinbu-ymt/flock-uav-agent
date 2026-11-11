"""Make the shared FlockCue fixtures available when testing Agent4Drone alone."""
from pathlib import Path
import sys


_ROOT = Path(__file__).resolve().parents[2]
for _name in ("compat/agent4drone", "flockcue/tests"):
    _directory = _ROOT / _name
    if _directory.is_dir():
        sys.path.insert(0, str(_directory))
