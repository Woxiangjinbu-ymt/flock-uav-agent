"""Pairwise action regressions, without a model or live simulator."""
import copy
import json
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relative_movement import create_relative_movement_tool
from blackboard import PerceptionBlackboard
from uav_langchain_tools import _navigate_to_destination


class Client:
    def __init__(self, moving=(100, 0, 20), reference=(0, 0, 20), session=None):
        self.positions = {"moving": dict(zip("xyz", moving)), "reference": dict(zip("xyz", reference))}
        self.session = {} if session is None else session
        self.reads = []
        self.session_reads = 0
        self.moves = []

    def get_current_session(self):
        self.session_reads += 1
        return self.session

    def get_drone_status(self, drone_id):
        self.reads.append(drone_id)
        return {"id": drone_id, "position": copy.deepcopy(self.positions[drone_id])}

    def move_to(self, drone_id, x, y, z):
        self.moves.append((drone_id, x, y, z))
        self.positions[drone_id] = {"x": x, "y": y, "z": z}
        return {"status": "success"}


def invoke(client, callback=None, **overrides):
    calls = []

    def navigate(drone_id, destination, altitude, safety_buffer, tolerance):
        calls.append((drone_id, destination, altitude, safety_buffer, tolerance))
        if callback:
            return callback(client, calls[-1])
        return _navigate_to_destination(client, PerceptionBlackboard(), drone_id,
                                        destination, altitude, safety_buffer, tolerance)

    tool = create_relative_movement_tool(client, navigate)
    params = {"drone_id": "moving", "reference_drone_id": "reference", "max_distance": 19, **overrides}
    return json.loads(tool.invoke({"input_json": json.dumps(params)})), calls


def test_real_boundary_regression_19_012_is_corrected_with_b1():
    client = Client(moving=(19.012, 0, 17), reference=(0, 0, 17))
    result, calls = invoke(client)
    assert result["distance_before"] == 19.012
    assert result["distance_after"] == pytest.approx(18.9)
    assert result["success"] and result["within_distance"]
    assert len(calls) == len(client.moves) == 1
    assert calls[0][4] < result["distance_margin"]
    assert result["navigation_result"]["movement_strategy"] == "move_to"
    assert client.reads[-2:] == ["moving", "reference"]


def test_max_distance_is_final_bound_not_travel_length():
    client = Client(moving=(200, 100, 20), reference=(100, 100, 20))
    result, calls = invoke(client)
    assert calls[0][1] == pytest.approx((118.9, 100))
    assert 200 - client.positions["moving"]["x"] > 80
    assert result["distance_mode"] == "2d"
    assert result["requested_distance_mode"] == "session"


def test_session_2d_ignores_height_difference_but_preserves_moving_height():
    client = Client(moving=(100, 0, 3), reference=(0, 0, 80))
    result, calls = invoke(client)
    assert result["success"] and result["distance_mode"] == "2d"
    assert calls[0][2] == 3
    assert client.positions["reference"]["z"] == 80


def test_session_3d_computes_horizontal_radius_at_preserved_height():
    client = Client(moving=(30, 0, 26), reference=(0, 0, 20), session={"is_distance_3d": True})
    result, calls = invoke(client, max_distance=10)
    assert calls[0][1][0] == pytest.approx(math.sqrt(9.9**2 - 6**2))
    assert result["distance_after"] == pytest.approx(9.9)
    assert result["distance_mode"] == "3d" and calls[0][2] == 26


@pytest.mark.parametrize("mode,expected", [("2d", True), ("3d", False)])
def test_explicit_mode_overrides_session_without_extra_metadata_call(mode, expected):
    client = Client(moving=(10, 0, 0), reference=(0, 0, 30), session={"is_distance_3d": True})
    result, calls = invoke(client, distance_mode=mode)
    assert result["success"] is expected
    assert client.session_reads == 0 and not calls


def test_impossible_height_does_not_take_off_or_change_altitude():
    client = Client(moving=(100, 0, 0), reference=(0, 0, 30), session={"is_distance_3d": True})
    result, calls = invoke(client)
    assert result["reason"] == "altitude_separation_exceeds_max_distance"
    assert result["within_distance"] is False and not calls and not client.moves
    assert client.positions["moving"]["z"] == 0


def test_explicit_altitude_can_make_3d_relation_feasible():
    client = Client(moving=(100, 0, 3), reference=(0, 0, 30))
    result, calls = invoke(client, z=30, distance_mode="3d", safety_buffer=1.5)
    assert result["success"] and calls[0][2:4] == (30, 1.5)


def test_already_within_bound_does_not_apply_optional_altitude():
    client = Client(moving=(19, 0, 20))
    result, calls = invoke(client, z=50)
    assert result["success"] and result["navigation_result"] is None and not calls
    assert client.positions["moving"]["z"] == 20
    assert client.reads == ["moving", "reference"]


def test_equal_vertical_separation_moves_to_reference_xy_and_checks_boundary():
    client = Client(moving=(1, 0, 39), reference=(0, 0, 20))
    result, calls = invoke(client, distance_mode="3d")
    assert result["boundary_only"] and calls[0][1] == (0, 0)
    assert result["success"] and result["distance_after"] == 19


def test_tiny_boundary_error_is_not_skipped_by_default_one_meter_tolerance():
    client = Client(moving=(0.200001, 0, 20))
    result, calls = invoke(client, max_distance=.2)
    assert result["success"] and len(client.moves) == 1
    assert result["distance_after"] == pytest.approx(.198)
    assert calls[0][4] < .002


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, None, "no"])
def test_bad_distance_rejected_before_reads_or_movement(bad):
    client = Client()
    result, calls = invoke(client, max_distance=bad)
    assert not result["success"] and result["outcome"] == "error"
    assert not client.reads and not calls and client.session_reads == 0


@pytest.mark.parametrize("override", [
    {"drone_id": "reference"}, {"reference_drone_id": ""}, {"drone_id": 7},
    {"z": float("nan")}, {"z": float("inf")}, {"z": -1},
    {"safety_buffer": 0}, {"safety_buffer": float("inf")}, {"distance_mode": "guess"},
])
def test_illegal_parameters_do_not_execute(override):
    client = Client()
    result, calls = invoke(client, **override)
    assert not result["success"] and not calls and not client.reads


@pytest.mark.parametrize("outcome", ["blocked", "partial", "unknown"])
def test_navigation_failure_retains_real_observation_and_never_retries(outcome):
    def blocked(client, args):
        client.positions["moving"]["x"] = 30
        return {"success": False, "outcome": outcome, "message": "original reason"}

    result, calls = invoke(Client(), blocked)
    assert result["outcome"] == outcome and not result["success"]
    assert result["distance_after"] == 30 and result["within_distance"] is False
    assert result["navigation_result"]["message"] == "original reason"
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["partial", "blocked", "unknown"])
def test_satisfied_relation_does_not_require_completing_the_selected_navigation_path(outcome):
    def partial(client, args):
        client.positions["moving"]["x"] = 18.95
        return {"success": False, "outcome": outcome}

    result, calls = invoke(Client(), partial)
    assert result["within_distance"] is True and result["success"] is True
    assert result["distance_after"] == 18.95
    assert result["outcome"] == "completed" and len(calls) == 1
    assert result["navigation_result"] == {"success": False, "outcome": outcome}


def test_reference_moves_after_navigation_reports_actual_failure_without_following():
    def move_reference(client, args):
        drone, xy, altitude, _, _ = args
        client.move_to(drone, *xy, altitude)
        client.positions["reference"]["x"] = -100
        return {"success": True, "outcome": "completed"}

    result, calls = invoke(Client(), move_reference)
    assert not result["success"] and not result["within_distance"]
    assert result["reference_moved"] and result["distance_after"] == pytest.approx(118.9)
    assert result["reference_position"]["x"] == -100 and len(calls) == 1


def test_post_move_observation_error_is_unknown_and_retains_navigation_receipt():
    def move_then_lose_reference(client, args):
        client.positions["reference"].pop("z")
        return {"success": True, "outcome": "completed"}

    result, calls = invoke(Client(), move_then_lose_reference)
    assert result["outcome"] == "unknown" and result["distance_after"] is None
    assert result["within_distance"] is None and not result["success"]
    assert result["navigation_result"]["success"] is True and len(calls) == 1
