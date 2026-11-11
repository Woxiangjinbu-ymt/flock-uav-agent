"""Tie package imports to the existing module object used by flat snapshot runners."""
from importlib import import_module
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_file_location
import sys


class FlatModuleAlias:
    """Only handle published flat paths that link to this checkout's FlockCue package."""

    def __init__(self, root, aliases=None):
        if aliases is not None:
            self.aliases = dict(aliases)
            return
        self.aliases = {}
        for directory in ("agent4drone", "scripts", "compat/agent4drone", "compat/scripts"):
            for link in (root / directory).glob("*.py"):
                if link.is_symlink():
                    source = link.resolve()
                    if source.is_relative_to(root / "flockcue"):
                        name = ".".join(source.relative_to(root).with_suffix("").parts)
                        self.aliases[name] = (link.stem, source)

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.aliases:
            return spec_from_file_location(fullname, self.aliases[fullname][1], loader=self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        # Only one module holds model observers, middleware classes and patched
        # tools when the baseline Agent and native FlockCue call each other.
        name = module.__name__
        sys.modules[name] = import_module(self.aliases[name][0])

    def get_code(self, fullname):
        # runpy uses get_code instead of exec_module for python -m.
        return SourceFileLoader(fullname, str(self.aliases[fullname][1])).get_code(fullname)
