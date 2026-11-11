"""Resolve standalone data tools and the historical simulation helpers used by fixtures."""
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parents[1]
sys.path[:0] = [str(TOOLS), str(ROOT / "compat/scripts"), str(ROOT / "compat/agent4drone")]
