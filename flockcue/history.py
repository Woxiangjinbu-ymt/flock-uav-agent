"""Resolve a registered historical source blob when materializing an archived run."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = Path(__file__).with_name("layout-source-lock.json")


@lru_cache(maxsize=1)
def source_registry():
    return json.loads(REGISTRY.read_bytes())


def sealed_source(root, name, expected_sha256):
    """Return the exact bytes to place in a frozen snapshot; never execute another revision."""
    root = Path(root).resolve()
    logical = Path(name)
    if logical.is_absolute() or ".." in logical.parts:
        raise ValueError(f"Non-local source path: {name}")
    current = (root / logical).resolve()
    if not current.is_relative_to(root):
        raise ValueError(f"Source outside snapshot: {name}")
    data = current.read_bytes()
    active_sha = sha256(data).hexdigest()
    if active_sha == expected_sha256:
        return data
    registry = source_registry()
    entry = registry["sources"].get(str(logical))
    if (root != ROOT or not entry or expected_sha256 != entry["before_sha256"]
            or active_sha != entry["after_sha256"] or str(current.relative_to(root)) != entry["path"]):
        raise ValueError(f"Source differs from the registered revisions: {name}")
    # A full local replay may already contain the original files, including
    # when the checkout is shallow or delivered as an exported directory.
    frozen = root / "docs/results/hover-compact-contract-20261103/local-01/runs/action/source" / logical
    if frozen.is_file():
        archived = frozen.read_bytes()
        if sha256(archived).hexdigest() == expected_sha256:
            return archived
    try:
        data = subprocess.check_output(["git", "show", f"{registry['checkpoint']}:{logical}"],
                                       cwd=root, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Fetch tag project-layout-before-20261111 for the original source: {name}") from exc
    if sha256(data).hexdigest() != expected_sha256:
        raise ValueError(f"Git blob differs from the frozen source: {name}")
    return data
