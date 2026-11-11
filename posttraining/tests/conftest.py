"""Expose the flat names exercised by historical worker and SFT fixtures."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "compat/scripts"), str(ROOT / "compat/agent4drone")]
