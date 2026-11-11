"""Offline execution-event labels for the FlockCue development archives."""

VERSION = "thinking-events-v1"

# A standalone worker can start this package with either scripts/ or posttraining/ as its cwd.
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _entry in (str(_root), str(_root / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
