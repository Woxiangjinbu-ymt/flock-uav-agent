"""Student training and teacher-trajectory tools for FlockCue."""
from pathlib import Path
import sys

from flockcue._flat_imports import FlatModuleAlias

_root = Path(__file__).resolve().parent.parent
_scripts = _root / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))


def _compatibility_names():
    """Use the same class objects for native imports and standalone bundle imports."""
    aliases = {}
    for link in _scripts.glob("*.py"):
        if link.is_symlink() and link.resolve().is_relative_to(_root / "posttraining"):
            source = link.resolve()
            name = ".".join(source.relative_to(_root).with_suffix("").parts)
            aliases[name] = (link.stem, source)
    for old in ("flockcue_session_cpu", "flockcue_qwen35", "thinking_events"):
        for source in (_scripts / old).resolve().glob("*.py"):
            if source.name != "__init__.py":
                name = ".".join(source.relative_to(_root).with_suffix("").parts)
                aliases[name] = (old + "." + source.stem, source)
    return aliases


sys.meta_path.insert(0, FlatModuleAlias(_root, aliases=_compatibility_names()))
