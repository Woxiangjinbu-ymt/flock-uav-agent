"""Check the frozen client/tool boundary with a stub; never open a socket."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RUNTIME = ROOT / "experiments/runs/20261006-214358-m1r-fast100-b4e9b6f3/runtime"
sys.path.insert(0, str(RUNTIME / "agent4drone"))
import logging_config
logging_config.LOG_DIR = OUT / "probe-logs"
from uav_api_client import UAVAPIClient
from uav_langchain_tools import create_uav_tools

replay = json.loads((OUT / "aggregate-feedback-replay.json").read_text())
metadata = replay["reconstructed_metadata"]["after"]
response = SimpleNamespace(status_code=200, json=lambda: metadata, raise_for_status=lambda: None)
client = UAVAPIClient("http://offline.invalid", api_key="offline-test")
with patch("requests.request", return_value=response) as network:
    received = client.get_current_session()
    names = [tool.name for tool in create_uav_tools(client)]
    assert received["statistics"]["task_progress"]["progress_percentage"] == 80
    assert received["statistics"]["task_progress"]["details"]["total_targets"] == 20
    assert "get_session_info" not in names and "get_task_progress" not in names
    assert network.call_count == 1
    assert network.call_args.args[:2] == ("GET", "http://offline.invalid/sessions/current")
replay["frozen_client_and_tool_probe"] = {
    "passed": True, "network_requests": 0, "stub_calls": 1,
    "client_preserves_progress_metadata": True,
    "registered_tools": names, "get_session_info_registered": False,
    "implication": "Public metadata exists, but current model tools cannot request it; wiring it changes the frozen model observation protocol.",
}
(OUT / "aggregate-feedback-replay.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2) + "\n")
print("PASS: public progress survives client; get_session_info is defined but absent from the frozen registered tool set")
