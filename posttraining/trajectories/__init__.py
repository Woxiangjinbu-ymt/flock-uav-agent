"""CPU-only regression for stateful sessions from the frozen 600-task pool."""

# Old standalone workers launch this package with only scripts/ on their path.
# A detached flat handoff already contains its own dependencies.
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if (_root / "compat/scripts").is_dir():
    for _path in (_root, _root / "compat/scripts", _root / "compat/agent4drone"):
        if str(_path) not in sys.path:
            sys.path.insert(0, str(_path))
