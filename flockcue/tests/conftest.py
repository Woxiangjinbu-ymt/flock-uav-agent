"""Make shared Agent4Drone and script fixtures available to the component suite."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for directory in (".", "scripts/tests", "agent4drone/tests", "scripts", "agent4drone", "flockcue/tests"):
    sys.path.insert(0, str(ROOT / directory))
