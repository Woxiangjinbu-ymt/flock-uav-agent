"""Offline contract checks against the real FastAPI command responses.

Each case gets a short-lived process: the server's module-level controllers and
imports cannot leak into the agent tests, and all logs live in pytest's temp dir.
No listener, live service, model, or external HTTP request is used.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _server_ui_dependency_path():
    """The separate server runtime supplies pygame for its screenshot import."""
    root = Path(__file__).resolve().parents[2]
    for packages in (root / "server" / ".venv" / "lib").glob("python*/site-packages"):
        if (packages / "pygame").is_dir():
            return str(packages)
    return None


@pytest.mark.parametrize("case", ["direct", "altitude", "obstacle", "battery", "drone_state"])
def test_navigation_with_real_server_contract(case, tmp_path):
    if importlib.util.find_spec("pygame") is None and _server_ui_dependency_path() is None:
        pytest.skip("Real-server integration requires its pygame dependency or installed server/.venv")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), case],
        cwd=tmp_path,
        env={**os.environ, "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1]) == {"case": case, "passed": True}


def _run_case(case):
    from unittest.mock import patch

    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / "agent4drone"), str(root / "server")]
    if importlib.util.find_spec("pygame") is None:
        # Keep the active agent runtime first, borrowing only dependencies it
        # lacks from the repository's separately installed server runtime.
        sys.path.append(_server_ui_dependency_path())

    # Redirect agent logging before its API client configures any handlers.
    import logging_config

    logging_config.LOG_DIR = Path.cwd() / "agent_logs"

    from fastapi.testclient import TestClient
    from api.server import app, drone_controller, obstacle_controller, session_controller
    from uav_api_client import UAVAPIClient
    from uav_langchain_tools import create_uav_tools

    # The context closes TestClient's portal; the child process also isolates
    # the server's global controller graph and default lifespan example session.
    with TestClient(app) as transport:
        session = session_controller.add_session({"name": "B1 contract", "with_examples": False})
        session_controller.set_current_session(session["id"])
        drone = drone_controller.add_drone({
            "id": "contract-drone",
            "name": "Contract Drone",
            "model": "TestModel",
            "max_speed": 10.0,
            "max_altitude": 50.0,
            "battery_capacity": 1000.0,
            "battery_level": 5.0 if case == "battery" else 100.0,
            "perceived_radius": 100.0,
            "position": {"x": 0.0, "y": 0.0, "z": 10.0},
            "status": "idle" if case == "drone_state" else "hovering",
        })
        if case == "obstacle":
            obstacle_controller.add_obstacle({
                "id": "contract-obstacle",
                "name": "Blocking Circle",
                "type": "circle",
                "position": {"x": 10.0, "y": 0.0, "z": 0.0},
                "radius": 2.0,
                "height": 20.0,
            })

        requests_seen = []
        command_receipts = []

        def in_process_request(method, url, **kwargs):
            requests_seen.append((method, url, kwargs))
            response = transport.request(method, url, **kwargs)
            assert response.status_code == 200, response.text
            if "/command/" in url:
                command_receipts.append(response.json())
            return response

        client = UAVAPIClient("http://testserver")
        tools = {tool.name: tool for tool in create_uav_tools(client)}
        target = {"x": 0.0, "y": 0.0, "z": 20.0} if case == "altitude" else {
            "x": 20.0, "y": 0.0, "z": 10.0,
        }
        with patch("uav_api_client.requests.request", side_effect=in_process_request):
            result = json.loads(tools["navigate_to"].invoke({
                "input_json": json.dumps({"drone_id": drone["id"], **target, "detail": "full"}),
            }))

        # These are unmodified server receipts, not fixtures that happen to use
        # the client's legacy success boolean.
        assert command_receipts
        assert all("success" not in receipt for receipt in command_receipts)
        commands = [receipt["command"] for receipt in command_receipts]
        if case in {"battery", "drone_state"}:
            assert result["success"] is False
            assert commands == ["move_to"]
            assert command_receipts[0]["status"] == "error"
            reason = "battery" if case == "battery" else "current status"
            assert reason in command_receipts[0]["message"].lower()
            assert result["last_move_result"]["outcome"] == "error"
            assert all("/others" not in url for _, url, _ in requests_seen)
        else:
            assert result["success"] is True
            assert result["final_position"] == target
            assert result["last_move_result"]["outcome"] == "completed"
            if case == "obstacle":
                assert commands == ["move_to", "move_along_path"]
                assert [r["status"] for r in command_receipts] == ["error", "success"]
                assert result["direct_move_result"]["outcome"] == "blocked"
                assert result["movement_strategy"] == "planned_fallback"
                fallback = [kwargs for _, url, kwargs in requests_seen if url.endswith("/move_along_path")]
                assert len(fallback) == 1
                assert fallback[0]["json"]["allow_partial_move"] is False
            else:
                assert commands == ["move_to"]
                assert result["movement_strategy"] == "move_to"

    print(json.dumps({"case": case, "passed": True}))


if __name__ == "__main__":
    _run_case(sys.argv[1])
