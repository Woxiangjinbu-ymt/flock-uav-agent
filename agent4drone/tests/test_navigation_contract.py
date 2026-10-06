"""B1 navigation regressions using server-shaped command responses, without a server."""

import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blackboard import PerceptionBlackboard
from uav_api_client import UAVAPIClient
import uav_langchain_tools as navigation


START = {"x": 0.0, "y": 0.0, "z": 10.0}
TARGET = {"x": 100.0, "y": 0.0, "z": 10.0}
OBSTACLE = {
    "id": "obstacle-1",
    "type": "circle",
    "position": {"x": 50.0, "y": 0.0, "z": 0.0},
    "radius": 2.0,
}
_DEFAULT = object()


class NavigationClient:
    def __init__(
        self,
        direct_result=_DEFAULT,
        direct_position=_DEFAULT,
        fallback_result=_DEFAULT,
        fallback_position=_DEFAULT,
        initial_position=None,
    ):
        self.position = copy.deepcopy(START if initial_position is None else initial_position)
        self.direct_result = {"status": "success"} if direct_result is _DEFAULT else direct_result
        self.direct_position = TARGET if direct_position is _DEFAULT else direct_position
        self.fallback_result = {"status": "success"} if fallback_result is _DEFAULT else fallback_result
        self.fallback_position = TARGET if fallback_position is _DEFAULT else fallback_position
        self.moves = []
        self.paths = []
        self.senses = []

    def get_drone_status(self, drone_id):
        return {"id": drone_id, "status": "hovering", "position": copy.deepcopy(self.position)}

    def move_to(self, drone_id, x, y, z):
        self.moves.append((drone_id, x, y, z))
        self.position = copy.deepcopy(self.direct_position)
        return copy.deepcopy(self.direct_result)

    def move_along_path(self, drone_id, waypoints, allow_partial_move=True):
        self.paths.append((drone_id, copy.deepcopy(waypoints), allow_partial_move))
        self.position = copy.deepcopy(self.fallback_position)
        return copy.deepcopy(self.fallback_result)

    def get_nearby_entities(self, drone_id):
        self.senses.append(drone_id)
        return {"targets": [], "obstacles": [copy.deepcopy(OBSTACLE)], "drones": []}


def navigate(client, **overrides):
    params = {
        "client": client,
        "blackboard": PerceptionBlackboard(),
        "drone_id": "drone-1",
        "destination": (TARGET["x"], TARGET["y"]),
        "altitude": TARGET["z"],
        "safety_buffer": 1.0,
        "tolerance": 1.0,
    }
    params.update(overrides)
    return navigation._navigate_to_destination(**params)


@pytest.fixture
def planner_calls(monkeypatch):
    calls = []

    def deterministic_path(start, destination, **kwargs):
        calls.append((start, destination, kwargs))
        return [start, (50.0, 5.0), destination]

    monkeypatch.setattr(navigation, "find_path", deterministic_path)
    return calls


@pytest.mark.parametrize("receipt", [{"status": "success"}, {"success": True}])
def test_completed_command_and_observed_xyz_arrival_succeed(receipt):
    client = NavigationClient(direct_result=receipt)

    result = navigate(client)

    assert result["success"] is True
    assert result["outcome"] == "completed"
    assert result["arrival_verified"] is True
    assert result["distance_remaining"] == 0.0
    assert result["last_move_result"] == receipt
    assert len(client.moves) == 1
    assert client.paths == client.senses == []


def test_same_xy_different_altitude_requires_movement():
    client = NavigationClient(initial_position={**TARGET, "z": 2.0})

    result = navigate(client)

    assert client.moves == [("drone-1", 100.0, 0.0, 10.0)]
    assert result["success"] is True
    assert result["arrival_verified"] is True


@pytest.mark.parametrize("position, expect_move", [
    ({"x": 99.5, "y": 0.0, "z": 9.5}, False),
    ({"x": 99.2, "y": 0.0, "z": 9.2}, True),
])
def test_arrival_tolerance_is_euclidean_xyz(position, expect_move):
    client = NavigationClient(initial_position=position)

    result = navigate(client)

    assert bool(client.moves) is expect_move
    assert result["success"] is True
    assert result["arrival_verified"] is True
    if not expect_move:
        assert result["movement_strategy"] == "already_at_destination"
        assert result["distance_remaining"] == pytest.approx(math.sqrt(0.5))


@pytest.mark.parametrize("position", [
    {**TARGET, "x": 95.0},
    {**TARGET, "z": 5.0},
    {"x": 99.2, "y": 0.0, "z": 9.2},
])
def test_completed_command_without_xyz_arrival_fails_without_retry(position):
    client = NavigationClient(direct_position=position)

    result = navigate(client)

    assert result["success"] is False
    assert result["outcome"] == "error"
    assert result["last_move_result"]["status"] == "success"
    assert result["arrival_verified"] is False
    assert result["distance_remaining"] > 1.0
    assert client.paths == client.senses == []


@pytest.mark.parametrize("receipt", [
    {"status": "error", "message": "Path to waypoint 1 blocked: obstacle-1"},
    {"status": "error", "message": "Target position conflicts with obstacle: obstacle-1"},
    {"success": False, "message": "Direct movement blocked"},
])
def test_spatial_blockage_senses_and_uses_exactly_one_full_path(receipt, planner_calls):
    client = NavigationClient(direct_result=receipt, direct_position=START)

    result = navigate(client)

    assert result["success"] is True
    assert result["outcome"] == "completed"
    assert result["arrival_verified"] is True
    assert result["direct_move_result"] == receipt
    assert result["last_move_result"] == {"status": "success"}
    assert client.senses == ["drone-1"]
    assert len(planner_calls) == len(client.paths) == 1
    assert planner_calls[0][:2] == ((0.0, 0.0), (100.0, 0.0))
    assert planner_calls[0][2]["known_obstacles"][0]["id"] == "obstacle-1"
    assert client.paths[0][1] == [
        {"x": 50.0, "y": 5.0, "z": 10.0}, TARGET,
    ]
    assert client.paths[0][2] is False


@pytest.mark.parametrize("message", [
    "Insufficient battery: requires 4.00%, available 1.00%",
    "Waypoint 1 altitude exceeds maximum (100m)",
    "Waypoint 1 altitude cannot be negative",
    "Cannot move from current status: idle",
    "Waypoint 1 missing coordinates",
])
def test_nonspatial_errors_do_not_sense_plan_or_retry(message, planner_calls):
    receipt = {"status": "error", "message": message}
    client = NavigationClient(direct_result=receipt, direct_position=START)

    result = navigate(client)

    assert result["success"] is False
    assert result["outcome"] == "error"
    assert result["arrival_verified"] is False
    assert result["last_move_result"] == receipt
    assert len(client.moves) == 1
    assert client.senses == client.paths == planner_calls == []


@pytest.mark.parametrize("receipt", [
    {"status": "partial_success", "completed_waypoints": 1},
    {"success": True, "partial_success": True, "completed_waypoints": 1},
])
def test_partial_command_is_incomplete_even_at_destination(receipt):
    client = NavigationClient(direct_result=receipt)

    result = navigate(client)

    assert result["success"] is False
    assert result["outcome"] == "partial"
    assert result["arrival_verified"] is True
    assert result["last_move_result"] == receipt
    assert client.paths == client.senses == []


def test_partial_fallback_preserves_raw_progress_and_does_not_retry(planner_calls):
    receipt = {
        "status": "partial_success",
        "command": "move_along_path",
        "message": "Path to waypoint 2 blocked: obstacle-2",
        "successful_points_count": 1,
        "successful_points": [{"x": 50.0, "y": 5.0, "z": 10.0}],
        "unsuccessful_points_count": 1,
        "unsuccessful_points": [TARGET],
    }
    client = NavigationClient(
        direct_result={"status": "error", "message": "Direct movement blocked"},
        direct_position=START,
        fallback_result=receipt,
        fallback_position={"x": 50.0, "y": 5.0, "z": 10.0},
    )

    result = navigate(client)

    assert result["success"] is False
    assert result["outcome"] == "partial"
    assert result["arrival_verified"] is False
    assert result["last_move_result"] == receipt
    assert len(client.moves) == len(client.paths) == len(client.senses) == len(planner_calls) == 1


@pytest.mark.parametrize("receipt", [
    {"status": "error", "message": "Path to waypoint 2 blocked: obstacle-2"},
    {"status": "success"},
])
def test_fallback_failure_or_wrong_altitude_never_causes_second_plan(receipt, planner_calls):
    client = NavigationClient(
        direct_result={"status": "error", "message": "Direct movement blocked"},
        direct_position=START,
        fallback_result=receipt,
        fallback_position={**TARGET, "z": 5.0},
    )

    result = navigate(client)

    assert result["success"] is False
    assert result["arrival_verified"] is False
    assert result["distance_remaining"] == 5.0
    assert len(client.paths) == len(planner_calls) == len(client.senses) == 1


@pytest.mark.parametrize("receipt", [{}, None, "success", [], {"status": "pending"}])
def test_unknown_receipt_cannot_succeed_or_trigger_fallback(receipt, planner_calls):
    client = NavigationClient(direct_result=receipt)

    result = navigate(client)

    assert result["success"] is False
    assert result["outcome"] == "unknown"
    assert result["arrival_verified"] is True
    assert result["last_move_result"] == receipt
    assert client.senses == client.paths == planner_calls == []


def test_compact_navigation_preserves_failure_reason():
    client = NavigationClient(
        direct_result={"error": "Insufficient battery", "reason_code": "battery_low"},
        direct_position=START,
    )
    tool = {tool.name: tool for tool in navigation.create_uav_tools(client)}["navigate_to"]

    result = json.loads(tool.invoke({"input_json": json.dumps({"drone_id": "drone-1", **TARGET})}))

    assert result["outcome"] == "error"
    assert result["last_move_result"] == {
        "error": "Insufficient battery", "reason_code": "battery_low",
    }


@pytest.mark.parametrize("position", [
    {"x": 0.0, "y": 0.0},
    {"x": 0.0, "y": 0.0, "z": None},
    {**START, "x": float("nan")},
    {**START, "y": float("inf")},
    {**START, "z": float("nan")},
])
def test_invalid_initial_position_fails_closed_before_any_move(position):
    client = NavigationClient(initial_position=position)

    try:
        result = navigate(client)
    except ValueError:
        pass
    else:
        assert result["success"] is False
        assert result["arrival_verified"] is False
    assert client.moves == client.paths == client.senses == []


@pytest.mark.parametrize("overrides", [
    {"destination": (float("nan"), 0.0)},
    {"destination": (100.0, float("inf"))},
    {"altitude": float("nan")},
    {"altitude": float("inf")},
])
def test_nonfinite_requested_coordinates_never_issue_moves(overrides):
    client = NavigationClient()

    try:
        result = navigate(client, **overrides)
    except ValueError:
        pass
    else:
        assert result["success"] is False
        assert result["arrival_verified"] is False
    assert client.moves == client.paths == client.senses == []


@pytest.mark.parametrize("tool_name", ["navigate_to", "navigate_to_and_sense"])
@pytest.mark.parametrize("parameter", ["arrival_tolerance", "safety_buffer"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0, -1.0])
def test_invalid_navigation_limits_fail_before_movement(tool_name, parameter, value):
    client = NavigationClient()
    tools = {tool.name: tool for tool in navigation.create_uav_tools(client)}

    result = tools[tool_name].invoke({"input_json": json.dumps({
        "drone_id": "drone-1", **TARGET, parameter: value,
    })})

    assert result.startswith("Error:") or result.startswith("Error ")
    assert client.moves == client.paths == client.senses == []


@pytest.mark.parametrize("tool_name", ["navigate_to", "navigate_to_and_sense"])
def test_omitted_altitude_preserves_observed_altitude(tool_name):
    client = NavigationClient(
        initial_position={**START, "z": 37.0},
        direct_position={**TARGET, "z": 37.0},
    )
    tools = {tool.name: tool for tool in navigation.create_uav_tools(client)}

    result = json.loads(tools[tool_name].invoke({"input_json": json.dumps({
        "drone_id": "drone-1", "x": 100.0, "y": 0.0,
    })}))

    navigation_result = result.get("navigation_result", result)
    assert navigation_result["success"] is True
    assert client.moves == [("drone-1", 100.0, 0.0, 37.0)]


@pytest.mark.parametrize("tool_name", ["navigate_to", "navigate_to_and_sense"])
def test_omitted_altitude_with_missing_observed_z_never_defaults_to_zero(tool_name):
    client = NavigationClient(initial_position={"x": 0.0, "y": 0.0})
    tools = {tool.name: tool for tool in navigation.create_uav_tools(client)}

    result = tools[tool_name].invoke({"input_json": json.dumps({
        "drone_id": "drone-1", "x": 100.0, "y": 0.0,
    })})

    assert result.startswith("Error:") or result.startswith("Error ")
    assert client.moves == client.paths == client.senses == []


def test_real_api_client_status_response_reaches_navigation(monkeypatch):
    """Exercise the actual HTTP/client boundary without contacting a live service."""
    state = {"position": copy.deepcopy(START)}
    requests = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return copy.deepcopy(self.payload)

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs))
        if method == "GET" and url.endswith("/drones/drone-1"):
            return Response({"id": "drone-1", "position": state["position"]})
        if method == "POST" and url.endswith("/command/move_to"):
            state["position"] = dict(kwargs["params"])
            return Response({"status": "success", "command": "move_to", "drone_id": "drone-1"})
        raise AssertionError(f"Unexpected request: {method} {url}")

    monkeypatch.setattr("uav_api_client.requests.request", request)

    result = navigate(UAVAPIClient(base_url="http://navigation-test.invalid"))

    assert result["success"] is True
    assert result["arrival_verified"] is True
    assert result["last_move_result"]["status"] == "success"
    assert [method for method, _, _ in requests] == ["GET", "POST", "GET"]


def test_compact_sense_result_keeps_outcome_and_arrival_evidence():
    client = NavigationClient()
    tools = {tool.name: tool for tool in navigation.create_uav_tools(client)}

    result = json.loads(tools["navigate_to_and_sense"].invoke({"input_json": json.dumps({
        "drone_id": "drone-1", **TARGET,
    })}))

    assert result["navigation_result"]["success"] is True
    assert result["navigation_result"]["outcome"] == "completed"
    assert result["navigation_result"]["arrival_verified"] is True
    assert result["navigation_result"]["last_move_result"]["status"] == "success"
    assert client.senses == ["drone-1"]
