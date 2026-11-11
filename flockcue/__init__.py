"""FlockCue execution-feedback agent harness."""
from pathlib import Path
import sys

# Published runners used flat names and materialize source snapshots under
# these two directories. Keep both old CLI calls and new package imports usable.
_ROOT = Path(__file__).resolve().parent.parent
for _directory in (_ROOT / "scripts", _ROOT / "agent4drone"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

if __name__ == "agent_harness" and (_ROOT / "flockcue/_flat_imports.py").is_file():
    from importlib import import_module

    sys.modules[__name__] = import_module("flockcue")
elif __name__ == "flockcue":
    from ._flat_imports import FlatModuleAlias

    sys.meta_path.insert(0, FlatModuleAlias(_ROOT))
