"""Offline regressions for copying and recovering coverage plan handles."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from blackboard import PerceptionBlackboard
from uav_langchain_tools import _COVERAGE_PLAN_CACHE, create_uav_tools


class CoverageClient:
    def __init__(self):
        self.drones = [
            {"id": drone_id, "name": drone_id, "position": {"x": x, "y": 0, "z": 20},
             "task_radius": 2.0, "perceived_radius": 50.0, "status": "hovering"}
            for drone_id, x in [("drone-1", 0), ("drone-2", 20)]
        ]
        self.targets = {
            target_id: {"id": target_id, "name": name, "type": "circle",
                        "position": {"x": x, "y": 10, "z": 0}, "radius": 8}
            for target_id, name, x in [
                ("target-1", "Circle Target 1", 10),
                ("target-2", "Circle Target 2", 40),
            ]
        }
        self.moves = []
        self.senses = []

    def list_drones(self):
        return self.drones

    def get_target(self, target_id):
        return self.targets[target_id]

    def move_along_path(self, drone_id, waypoints, allow_partial_move=True):
        self.moves.append((drone_id, waypoints, allow_partial_move))
        return {"status": "success", "drone_id": drone_id, "command": "move_along_path"}

    def get_nearby_entities(self, drone_id):
        self.senses.append(drone_id)
        return {"drone_id": drone_id, "targets": [], "obstacles": [], "drones": []}


@pytest.fixture(autouse=True)
def clean_plan_cache():
    _COVERAGE_PLAN_CACHE.clear()
    yield
    _COVERAGE_PLAN_CACHE.clear()


def tools_for(client, blackboard=None):
    return {item.name: item for item in create_uav_tools(client, blackboard=blackboard)}


def call(tools, name, **payload):
    return json.loads(tools[name].invoke({"input_json": json.dumps(payload)}))


def generate(tools, target_id="target-1", drone_ids=None):
    return call(tools, "generate_coverage_path", target_id=target_id,
                drone_ids=drone_ids or ["drone-1"])


def test_generated_assignments_can_be_copied_directly_into_langchain_calls():
    client = CoverageClient()
    tools = tools_for(client)
    plan = generate(tools, drone_ids=["drone-1", "drone-2"])

    assert client.moves == []
    for assignment in plan["drone_assignments"]:
        execution = assignment["execution_call"]
        assert execution["tool"] == "move_along_path"
        assert isinstance(execution["arguments"]["input_json"], str)
        response = json.loads(tools[execution["tool"]].invoke(execution["arguments"]))
        assert response["status"] == "success"
        drone_id, waypoints, partial = client.moves[-1]
        assert drone_id == assignment["drone_id"]
        assert waypoints == _COVERAGE_PLAN_CACHE[plan["coverage_plan_id"]]["paths"][drone_id]
        assert partial is True


@pytest.mark.parametrize("name", ["move_along_path", "move_along_path_and_sense"])
def test_target_id_instead_of_plan_id_returns_exact_recovery_call_without_moving(name):
    client = CoverageClient()
    tools = tools_for(client)
    plan = generate(tools)

    error = call(tools, name, drone_id="drone-1", coverage_plan_id="target-1",
                 allow_partial_move=False, detail="full")

    assert "was not found" in error["error"]
    assert client.moves == []
    assert client.senses == []
    candidate, = error["candidate_plans"]
    assert candidate["coverage_plan_id"] == plan["coverage_plan_id"]
    assert candidate["target"] == {
        "id": "target-1", "name": "Circle Target 1", "type": "circle",
    }
    execution = candidate["execution_call"]
    assert execution["tool"] == name
    assert json.loads(execution["arguments"]["input_json"]) == {
        "drone_id": "drone-1", "coverage_plan_id": plan["coverage_plan_id"],
        "allow_partial_move": False, "detail": "full",
    }
    tools[name].invoke(execution["arguments"])
    assert len(client.moves) == 1
    assert client.moves[0][2] is False
    assert client.senses == (["drone-1"] if name.endswith("_and_sense") else [])


@pytest.mark.parametrize("name", ["move_along_path", "move_along_path_and_sense"])
def test_wrong_drone_only_suggests_plans_for_the_requested_drone(name):
    client = CoverageClient()
    tools = tools_for(client)
    first = generate(tools, drone_ids=["drone-1"])
    second = generate(tools, target_id="target-2", drone_ids=["drone-2"])

    error = call(tools, name, drone_id="drone-2", coverage_plan_id=first["coverage_plan_id"])

    assert "has no cached path for drone_id 'drone-2'" in error["error"]
    assert client.moves == []
    candidate, = error["candidate_plans"]
    assert candidate["coverage_plan_id"] == second["coverage_plan_id"]
    assert candidate["target"]["id"] == "target-2"
    assert candidate["drone_id"] == "drone-2"
    assert "only if its target matches" in error["recovery"]


def test_multiple_candidates_are_returned_without_choosing_or_executing_one():
    client = CoverageClient()
    tools = tools_for(client)
    first = generate(tools)
    second = generate(tools, target_id="target-2")
    generate(tools, drone_ids=["drone-2"])

    error = call(tools, "move_along_path", drone_id="drone-1", coverage_plan_id="wrong-handle")

    assert [item["coverage_plan_id"] for item in error["candidate_plans"]] == [
        first["coverage_plan_id"], second["coverage_plan_id"],
    ]
    assert [item["target"]["name"] for item in error["candidate_plans"]] == [
        "Circle Target 1", "Circle Target 2",
    ]
    assert client.moves == []


def test_rebuilt_tools_do_not_leak_previous_instance_candidates_even_with_shared_blackboard():
    client = CoverageClient()
    blackboard = PerceptionBlackboard(session_id="same-session")
    previous_tools = tools_for(client, blackboard)
    previous = generate(previous_tools)
    current_tools = tools_for(client, blackboard)

    empty = call(current_tools, "move_along_path", drone_id="drone-1", coverage_plan_id="target-1")
    assert empty["candidate_plans"] == []
    current = generate(current_tools, target_id="target-2")
    error = call(current_tools, "move_along_path_and_sense", drone_id="drone-1",
                 coverage_plan_id="wrong-handle")

    assert [item["coverage_plan_id"] for item in error["candidate_plans"]] == [
        current["coverage_plan_id"],
    ]
    assert previous["coverage_plan_id"] not in json.dumps(error)
    assert "Circle Target 1" not in json.dumps(error)
    assert client.moves == []


def test_evicted_plan_is_not_suggested_and_a_legacy_cached_plan_still_executes():
    client = CoverageClient()
    tools = tools_for(client)
    plan = generate(tools)
    del _COVERAGE_PLAN_CACHE[plan["coverage_plan_id"]]
    error = call(tools, "move_along_path", drone_id="drone-1", coverage_plan_id="target-1")
    assert error["candidate_plans"] == []

    _COVERAGE_PLAN_CACHE["coverage-legacy"] = {"paths": {"drone-1": [{"x": 1, "y": 2}]}}
    result = call(tools, "move_along_path", drone_id="drone-1", coverage_plan_id="coverage-legacy")
    assert result["status"] == "success"
    assert client.moves == [("drone-1", [{"x": 1.0, "y": 2.0}], True)]


@pytest.mark.parametrize("name", ["move_along_path", "move_along_path_and_sense"])
def test_explicit_waypoints_and_existing_partial_defaults_are_unchanged(name):
    client = CoverageClient()
    tools = tools_for(client)
    waypoints = [{"x": 2, "y": 3, "z": 20}]
    call(tools, name, drone_id="drone-1", waypoints=waypoints)
    call(tools, name, drone_id="drone-1", waypoints=waypoints, allow_partial_move="false")

    assert [move[2] for move in client.moves] == [True, False]
    assert all(move[1] == [{"x": 2.0, "y": 3.0, "z": 20.0}] for move in client.moves)
