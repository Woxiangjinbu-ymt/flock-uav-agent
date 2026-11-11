"""Local fake-client probe; run with agent4drone/.venv/bin/python, no APIs."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from blackboard import PerceptionBlackboard
from uav_langchain_tools import _navigate_to_destination


class Client:
    def __init__(self, blocked=False):
        self.position = {"x": 0.0, "y": 0.0, "z": 10.0}
        self.calls = []
        self.blocked = blocked

    def get_drone_status(self, drone_id):
        return {"position": self.position.copy()}

    def move_to(self, drone_id, x, y, z):
        self.calls.append("move_to")
        if self.blocked:
            return {"status": "error", "message": "Path blocked by obstacle"}
        self.position = {"x": x, "y": y, "z": z}
        return {"status": "success", "message": "Drone moved"}

    def get_nearby_entities(self, drone_id):
        self.calls.append("sense")
        return {"targets": [], "obstacles": [], "drones": []}

    def move_along_path(self, *args, **kwargs):
        self.calls.append("move_along_path")
        return {"status": "success"}


output = {}
for name, destination, altitude, blocked in [
    ("api_status_success", (10.0, 0.0), 10.0, False),
    ("api_status_error", (10.0, 0.0), 10.0, True),
    ("same_xy_wrong_z", (0.0, 0.0), 20.0, False),
]:
    client = Client(blocked)
    output[name] = {
        "result": _navigate_to_destination(client, PerceptionBlackboard(), "test", destination, altitude, 1.0, 1.0),
        "calls": client.calls,
    }
print(json.dumps(output, ensure_ascii=False, indent=2))
