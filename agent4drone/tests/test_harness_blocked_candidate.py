"""Plan-local endpoint evidence with scripted public receipts; no simulator/model.

A small candidate fixture retains the production choose/accept methods. Its
ranking makes the same failed endpoint attractive again after a legal move, so
the tests distinguish endpoint evidence from the existing origin-specific edge.
"""
import copy
from dataclasses import asdict
import socket

import pytest

from test_harness_entry_contract import actions, revision_runtime
from test_harness_flight_state import FlightWorld, actor_calls, reject_without_effect
from test_minimal_harness import SEARCH, finish
import harness_skills
from coordinated_search_r2 import PublicSearchPolicy
from harness_core import HarnessInterrupted
from harness_skills import _endpoint_conflict


V4 = "blocked_candidate_v4"
ENDPOINT = (200.0, 100.0)
SECOND = (110.0, 100.0)
THIRD = (120.0, 100.0)
DESTINATION_ERROR = "Target position conflicts with obstacle: Position conflicts with polygon obstacle 'Unseen obstacle'"
PATH_ERROR = "Path to waypoint 1 blocked: Flight path intersects with ObstacleType.POLYGON obstacle 'Unseen obstacle'"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in blocked-candidate tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


class SmallCandidatePolicy(PublicSearchPolicy):
    def __init__(self, drones, obstacles):
        super().__init__(drones, obstacles)
        self.candidates = [ENDPOINT, SECOND, THIRD]
        self.candidate_cover = [frozenset(range(10)), frozenset({10}), frozenset({11})]
        self.covered = set()
        self.initial_covered_count = 0


@pytest.fixture
def small_candidates(monkeypatch):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", SmallCandidatePolicy)


class EndpointWorld(FlightWorld):
    def __init__(self, *, message=DESTINATION_ERROR, actors=1, receipt=None, effect=None,
                 status="hovering", z=20):
        super().__init__(status, z)
        if actors == 1:
            self.drones.pop("d2")
        self.obstacles = []  # The public rejection need not include obstacle geometry.
        self.message = message
        self.receipt = receipt
        self.effect = effect

    def _call(self, name, **args):
        if name == "move_to" and (args["x"], args["y"]) == ENDPOINT:
            actor = args["drone_id"]
            if self.effect:
                axis, delta = self.effect
                self.drones[actor]["position"][axis] += delta
            receipt = {"command": "move_to", "drone_id": actor,
                       "status": "error", "message": self.message}
            if self.receipt is not None:
                receipt = copy.deepcopy(self.receipt)
            return self._reply(name, args, receipt)
        return super()._call(name, **args)


def make(world=None, *, revision=V4, scheduler="every_step", clock=lambda: 0):
    return revision_runtime(SEARCH, scheduler, world=world or EndpointWorld(),
                            revision=revision, clock=clock)


def move_points(world, actor="d1"):
    return [(args["x"], args["y"]) for _, args in actor_calls(world, actor, "move_to")]


def advance(runtime, response=None):
    if response is None:
        return runtime.control("start", skill="public_search")
    assert response["status"] == "paused"
    return runtime.control("resume", plan_id=response["plan_id"])


def action_receipt(**updates):
    receipt = {"command": "move_to", "drone_id": "d1", "status": "error", "message": DESTINATION_ERROR}
    receipt.update(updates)
    return {"tool": "move_to", "args": {"drone_id": "d1", "x": 200, "y": 100, "z": 20},
            "receipt": receipt}


@pytest.mark.parametrize("kind", ["circle", "ellipse", "polygon"])
def test_narrow_classification_accepts_explicit_supported_endpoint_error(kind):
    action = action_receipt(message=DESTINATION_ERROR.replace("polygon", kind))
    assert _endpoint_conflict(action, "d1")


@pytest.mark.parametrize("updates", [
    {"status": "partial_success"}, {"status": "success"}, {"status": None},
    {"status": "error", "success": True}, {"status": "error", "partial_success": True},
    {"status": "error", "success": "false"}, {"status": "unknown"},
    {"drone_id": None}, {"drone_id": "d2"}, {"command": None}, {"command": "take_off"},
    {"message": PATH_ERROR}, {"message": "Path blocked by obstacle"},
    {"message": DESTINATION_ERROR + " Extra explanation."},
    {"message": "Quoted: " + DESTINATION_ERROR}, {"message": DESTINATION_ERROR + "\n"},
    {"message": "Insufficient battery to complete path"}, {"message": None},
    {"message": DESTINATION_ERROR, "error": "invalid battery information"},
])
def test_ambiguous_other_actor_or_non_endpoint_receipt_is_not_endpoint_evidence(updates):
    assert not _endpoint_conflict(action_receipt(**updates), "d1")


@pytest.mark.parametrize("change", ["wrong_tool", "wrong_argument_actor", "missing_receipt", "transport_only"])
def test_evidence_requires_the_matching_public_move_call(change):
    action = action_receipt()
    if change == "wrong_tool":
        action["tool"] = "move_along_path"
    elif change == "wrong_argument_actor":
        action["args"]["drone_id"] = "d2"
    elif change == "missing_receipt":
        action.pop("receipt")
    else:
        action["receipt"] = {"harness_transport_error": "TimeoutError"}
    assert not _endpoint_conflict(action, "d1")


def test_choose_default_and_explicit_empty_exclusion_are_identical():
    world = EndpointWorld()
    policy = SmallCandidatePolicy(list(world.drones.values()), [])
    expected = policy.choose("d1")
    assert expected[1] == ENDPOINT
    assert policy.choose("d1", excluded_indices=()) == expected
    assert policy.choose("d1", excluded_indices={expected[0]})[1] == SECOND
    assert policy.choose("d1") == expected  # A call's exclusion does not mutate policy state.


@pytest.mark.parametrize("revision,third", [("flight_state_v3", ENDPOINT), (V4, THIRD)])
def test_endpoint_rejection_survives_origin_change_only_in_v4(small_candidates, revision, third):
    r, w, _ = make(revision=revision)
    result = advance(r)
    assert result["status"] == "paused" and move_points(w) == [ENDPOINT]
    result = advance(r, result)
    assert w.drones["d1"]["position"] == {"x": SECOND[0], "y": SECOND[1], "z": 20.0}
    result = advance(r, result)
    assert move_points(w) == [ENDPOINT, SECOND, third]
    if revision == V4:
        assert len(result["state"]["blocked_endpoints"]) == 1
        assert r.frames[result["plan_id"]].repairs == 1
    else:
        assert "blocked_endpoints" not in result["state"]
        assert r.frames[result["plan_id"]].repairs == 2


@pytest.mark.parametrize("message", [PATH_ERROR, "Path blocked by obstacle"])
def test_path_or_generic_blockage_remains_retryable_from_another_origin(small_candidates, message):
    r, w, _ = make(EndpointWorld(message=message))
    result = advance(r)
    result = advance(r, result)
    result = advance(r, result)
    assert move_points(w) == [ENDPOINT, SECOND, ENDPOINT]
    assert "blocked_endpoints" not in result["state"]


def test_endpoint_evidence_is_not_shared_between_actors(small_candidates):
    r, w, _ = make(EndpointWorld(actors=2))
    result = advance(r)
    assert result["state"]["blocked_endpoints"][0]["actor"] == "d1"
    result = advance(r, result)
    assert move_points(w, "d1") == [ENDPOINT] and move_points(w, "d2") == [ENDPOINT]
    assert {item["actor"] for item in result["state"]["blocked_endpoints"]} == {"d1", "d2"}


@pytest.mark.parametrize("new_obstacle", [False, True])
def test_empty_perception_and_unrelated_new_geometry_do_not_retract_endpoint(small_candidates, new_obstacle):
    r, w, _ = make()
    result = advance(r)
    original = copy.deepcopy(result["state"]["blocked_endpoints"])
    if new_obstacle:
        w.obstacles = [{"id": "unrelated", "name": "Elsewhere", "type": "circle",
                        "position": {"x": 1000, "y": 1000, "z": 0}, "radius": 1, "height": 0}]
    result = advance(r, result)
    result = advance(r, result)
    assert move_points(w) == [ENDPOINT, SECOND, THIRD]
    assert result["state"]["blocked_endpoints"] == original
    assert result["state"]["new_public_obstacles"] == int(new_obstacle)


def test_memory_cites_move_receipt_not_the_following_state_or_nearby(small_candidates):
    r, w, _ = make()
    result = advance(r)
    frame = r.frames[result["plan_id"]]
    saved = result["state"]["blocked_endpoints"][0]
    events = {e["evidence_id"]: e for e in r.ledger.snapshot()["events"]}
    source = events[saved["evidence_ref"]]
    assert source["tool_name"] == "move_to" and source["args"] == saved["action"]["args"]
    assert source["payload"] == saved["action"]["receipt"]
    assert source["payload"]["status"] == "error" and source["payload"]["drone_id"] == "d1"
    assert saved["destination"] == [*ENDPOINT, 20.0] and saved["candidate_index"] == 0
    assert saved["round"] == 1 and saved["sequence"] == 1
    assert events[frame.evidence_refs[-1]]["tool_name"] == "get_nearby_entities"
    assert saved["evidence_ref"] != frame.evidence_refs[-1]


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_failed_move_that_changes_xyz_never_creates_endpoint_memory(small_candidates, axis):
    r, w, _ = make(EndpointWorld(effect=(axis, 1)))
    result = advance(r)
    assert result["status"] == "needs_replan" and "blocked_endpoints" not in result["state"]
    assert result["reason"] == ("search_blocked_move_effect_mismatch" if axis == "z" else "skill_exception:ValueError")
    assert move_points(w) == [ENDPOINT]
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("receipt,reason", [
    ({"status": "partial_success", "command": "move_to", "drone_id": "d1", "message": DESTINATION_ERROR}, "search_move_partial"),
    ({"status": "error", "success": True, "command": "move_to", "drone_id": "d1", "message": DESTINATION_ERROR}, "unknown_action_effect_observed_do_not_replay"),
    ({"harness_transport_error": "TimeoutError"}, "unknown_action_effect_observed_do_not_replay"),
    ({"status": "error", "command": "move_to", "drone_id": "d2", "message": DESTINATION_ERROR}, "wrong_actor_receipt"),
])
def test_incomplete_contradictory_or_wrong_actor_move_stops_without_learning(small_candidates, receipt, reason):
    r, w, _ = make(EndpointWorld(receipt=receipt))
    result = advance(r)
    assert result["status"] == "needs_replan" and result["reason"] == reason
    assert "blocked_endpoints" not in result["state"] and move_points(w) == [ENDPOINT]
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("invalidation", ["epoch", "external_action", "cancel"])
def test_existing_invalidation_prevents_reusing_a_paused_plan(small_candidates, invalidation):
    r, w, board = make()
    result = advance(r)
    assert result["state"]["blocked_endpoints"]
    if invalidation == "epoch":
        board.clear()
    elif invalidation == "external_action":
        r.external_begin("external", "move_to", {"drone_id": "d1"}, True)
        r.external_end("external")
    else:
        r.control("cancel", plan_id=result["plan_id"])
    previous_calls = copy.deepcopy(w.calls)
    response = r.control("resume", plan_id=result["plan_id"])
    assert response["status"] == "rejected" and w.calls == previous_calls
    assert response["reason"] == ("scene_scope_changed" if invalidation == "epoch" else "plan_not_resumable")


def test_new_runtime_plan_does_not_inherit_endpoint_memory(small_candidates):
    w = EndpointWorld()
    r, _, _ = make(w)
    old = advance(r)
    assert old["state"]["blocked_endpoints"]
    r.control("cancel", plan_id=old["plan_id"])
    new, _, _ = make(w)
    before = len(w.calls)
    result = advance(new)
    new_moves = [(n, a) for n, a in w.calls[before:] if n == "move_to"]
    assert [(a["x"], a["y"]) for _, a in new_moves] == [ENDPOINT]
    assert result["state"]["blocked_endpoints"][0]["sequence"] == 1


@pytest.mark.parametrize("budget,has_memory", [(4, False), (5, False), (6, False), (7, True)])
def test_api_budget_still_guards_move_and_required_confirmation(small_candidates, budget, has_memory):
    r, w, _ = make(scheduler="applicability")
    factory = r.skill_factory
    def limited(*args):
        frame, generator = factory(*args)
        frame.api_budget = budget
        return frame, generator
    r.skill_factory = limited
    result = advance(r)
    assert result["reason"] == "api_budget_exhausted" and len(w.calls) == budget
    assert bool(result["state"].get("blocked_endpoints")) == has_memory
    assert len(move_points(w)) == int(budget >= 5)
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("invalidation", ["epoch", "deadline"])
def test_scope_change_after_failed_move_prevents_recording_or_more_io(small_candidates, invalidation):
    now = [0]
    r, w, board = make(clock=lambda: now[0])
    def interrupt(name):
        if name == "move_to":
            board.clear() if invalidation == "epoch" else now.__setitem__(0, 500)
    w.after_call = interrupt
    if invalidation == "deadline":
        with pytest.raises(HarnessInterrupted):
            advance(r)
    else:
        assert advance(r)["reason"] == "scene_scope_changed"
    assert w.calls[-1][0] == "move_to"
    assert "blocked_endpoints" not in next(iter(r.frames.values())).state


def test_v4_inherits_empty_entry_flight_state_and_terminal_contract(small_candidates):
    r, w, _ = make(EndpointWorld(status="idle", z=57))
    result, _ = finish(r, "public_search")
    assert result["reason"] == "no_public_candidate" and not result["resumable"]
    assert result["state"]["entry_observations"][0]["status"] == "valid_empty"
    assert len(actor_calls(w, "d1", "take_off")) == 1
    names = [n for n, _ in actor_calls(w, "d1")]
    first_move = names.index("move_to")
    assert names[first_move - 2:first_move + 1] == ["take_off", "get_drone_status", "move_to"]
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_no_endpoint_error_keeps_v3_v4_calls_and_plan_state_identical():
    runs = []
    for revision in ("flight_state_v3", V4):
        r, w, _ = make(FlightWorld("idle", 57, blocked=True), revision=revision)
        result, _ = finish(r, "public_search")
        assert result["status"] == "completed"
        assert "blocked_endpoints" not in result["state"]
        runs.append((w.calls, w.drones, asdict(r.frames[result["plan_id"]])))
    assert runs[0] == runs[1]
