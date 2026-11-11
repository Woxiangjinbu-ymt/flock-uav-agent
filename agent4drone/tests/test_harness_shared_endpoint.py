"""Static-scene, exact-endpoint sharing with public scripted receipts only.

Production choose/accept remain active on a small candidate fixture. No server,
simulator, model or network is called. Different commanded altitudes are outside
public_search's fixed-z=20 interface; the tests verify this scope explicitly.
"""
import copy
from dataclasses import asdict
import socket
from types import SimpleNamespace

import pytest

from test_minimal_harness import SEARCH, SIMPLE, finish, runtime
from test_harness_blocked_candidate import (
    DESTINATION_ERROR, ENDPOINT, PATH_ERROR, SECOND, THIRD, V4,
    EndpointWorld, SmallCandidatePolicy, advance, move_points,
)
from test_harness_flight_state import FlightWorld, actor_calls, reject_without_effect
import harness_skills
from harness_core import HarnessInterrupted, HarnessRuntime, SkillExit
from harness_integration import install_harness


V5 = "shared_endpoint_v5"
OMIT = object()
CHANGED = "shared_endpoint_static_obstacle_contract_changed"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in shared-endpoint tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def small_candidates(monkeypatch):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", SmallCandidatePolicy)


def make(world=None, *, revision=V5, static=True, scheduler="every_step", clock=lambda: 0,
         command=SEARCH):
    old, world, board = runtime(command, scheduler, world=world or EndpointWorld(), clock=clock)
    kwargs = {} if static is OMIT else {"static_obstacle_contract": static}
    current = HarnessRuntime(
        task_id=old.task_id, command=old.command, scene_id=old.scene_id,
        scene_epoch=old.scene_epoch, ledger=old.ledger, invoke=old.invoke,
        skill_factory=old.skill_factory, scope=old.scope, started=old.started,
        scheduler=scheduler, fixed_k=old.fixed_k, clock=clock, revision=revision, **kwargs)
    return current, world, board


def obstacle(**updates):
    value = {"id": "far", "name": "Far obstacle", "type": "circle",
             "position": {"x": 1000, "y": 1000, "z": 0}, "radius": 1, "height": 0}
    value.update(updates)
    return value


def known_world(**kwargs):
    world = EndpointWorld(**kwargs)
    world.obstacles = [obstacle()]
    return world


def assert_no_shared(result):
    assert not result["state"].get("shared_blocked_endpoints")


@pytest.mark.parametrize("static", [OMIT, False])
def test_undeclared_static_contract_rejects_before_any_public_call(static):
    r, w, _ = make(static=static)
    result = advance(r)
    assert result["reason"] == "shared_endpoint_requires_static_obstacle_contract"
    assert result["status"] == "needs_replan" and not result["resumable"]
    assert w.calls == [] and r.ledger.snapshot()["events"] == []
    assert r.frames[result["plan_id"]].api_calls == 0
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("value", [None, 0, 1, "1", "true", [], {}])
def test_static_runtime_option_is_strict_boolean(value):
    with pytest.raises(ValueError, match="must be boolean"):
        make(static=value)


@pytest.mark.parametrize("env,enabled", [(None, False), ("", False), ("0", False),
                                        ("true", False), ("01", False), ("1 ", False), ("1", True)])
def test_real_install_uses_only_exact_env_one_without_model_calls(monkeypatch, env, enabled):
    monkeypatch.setenv("UAV_HARNESS_REVISION", V5)
    if env is None:
        monkeypatch.delenv("UAV_HARNESS_STATIC_OBSTACLES", raising=False)
    else:
        monkeypatch.setenv("UAV_HARNESS_STATIC_OBSTACLES", env)
    old, w, board = runtime(SEARCH, world=EndpointWorld())
    agent = SimpleNamespace(blackboard=board, client=w, tools=[],
                            _harness_task_started=0, _harness_cancel_check=lambda: None)
    middleware = install_harness(agent, old.ledger)
    assert middleware.harness is agent._harness
    assert agent._harness.revision == V5
    assert agent._harness.static_obstacle_contract is enabled
    assert "Entry contract v2 is enabled" in agent.tools[0].description
    assert "static-obstacle" in agent.tools[0].description
    assert w.calls == []
    monkeypatch.delenv("UAV_HARNESS_REVISION")
    other = SimpleNamespace(blackboard=board, client=w, tools=[],
                            _harness_task_started=0, _harness_cancel_check=lambda: None)
    install_harness(other, old.ledger)
    assert other._harness.revision == "legacy"
    assert "Entry contract v2 is enabled" not in other.tools[0].description


@pytest.mark.parametrize("revision,second_destination,repairs", [(V4, ENDPOINT, 2), (V5, THIRD, 1)])
def test_two_actor_same_destination_repeats_in_v4_but_is_shared_in_v5(
        small_candidates, revision, second_destination, repairs):
    r, w, _ = make(EndpointWorld(actors=2), revision=revision)
    result = advance(r)
    assert move_points(w, "d1") == [ENDPOINT]
    result = advance(r, result)
    assert move_points(w, "d2") == [second_destination]
    assert r.frames[result["plan_id"]].repairs == repairs
    if revision == V5:
        assert len(result["state"]["shared_blocked_endpoints"]) == 1
        assert {e["actor"] for e in result["state"]["blocked_endpoints"]} == {"d1"}
    else:
        assert_no_shared(result)


def test_shared_evidence_preserves_source_actor_and_move_ref_without_fake_consumer_failure(small_candidates):
    r, w, _ = make(EndpointWorld(actors=2))
    first = advance(r)
    original = copy.deepcopy(first["state"]["shared_blocked_endpoints"])
    second = advance(r, first)
    assert second["state"]["shared_blocked_endpoints"] == original
    saved = original[0]
    events = r.ledger.snapshot()["events"]
    source = next(e for e in events if e["evidence_id"] == saved["evidence_ref"])
    assert saved["actor"] == source["args"]["drone_id"] == source["payload"]["drone_id"] == "d1"
    assert source["tool_name"] == "move_to"
    assert source["args"] == saved["action"]["args"]
    assert source["payload"] == saved["action"]["receipt"]
    assert saved["destination"] == [*ENDPOINT, 20.0]
    assert saved["candidate_index"] == 0 and saved["sequence"] == 1
    consumer = [e for e in events if e["tool_name"] == "move_to" and e["args"]["drone_id"] == "d2"]
    assert len(consumer) == 1 and consumer[0]["payload"]["status"] == "success"
    assert consumer[0]["args"] == {"drone_id": "d2", "x": THIRD[0], "y": THIRD[1], "z": 20.0}


def test_exact_endpoint_does_not_expand_to_neighbor_or_origin_altitude(monkeypatch):
    near = (ENDPOINT[0] + 0.001, ENDPOINT[1])
    class NeighborPolicy(SmallCandidatePolicy):
        def __init__(self, drones, obstacles):
            super().__init__(drones, obstacles)
            self.candidates[1] = near
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", NeighborPolicy)
    w = EndpointWorld(actors=2)
    w.drones["d1"]["position"]["z"] = 57
    w.drones["d2"]["position"]["z"] = 35
    r, _, _ = make(w)
    result = advance(r)
    result = advance(r, result)
    assert move_points(w, "d2") == [near]
    assert result["state"]["shared_blocked_endpoints"][0]["destination"] == [*ENDPOINT, 20.0]
    assert result["state"]["shared_endpoint_scope"]["altitude"] == 20.0
    assert result["state"]["shared_endpoint_scope"]["match"] == "exact_xyz"
    assert all(a["z"] == 20.0 for n, a in w.calls if n == "move_to")
    assert not actor_calls(w, "d1", "take_off") and not actor_calls(w, "d2", "take_off")


@pytest.mark.parametrize("message", [PATH_ERROR, "Path blocked by obstacle"])
def test_path_or_generic_failure_is_not_shared_and_can_retry_after_origin_change(small_candidates, message):
    r, w, _ = make(EndpointWorld(actors=2, message=message))
    result = advance(r)
    result = advance(r, result)
    assert move_points(w, "d2") == [ENDPOINT]
    assert_no_shared(result)
    # Separate single actor case keeps the existing origin-specific retry.
    r, w, _ = make(EndpointWorld(message=message))
    result = advance(r)
    result = advance(r, result)
    result = advance(r, result)
    assert move_points(w) == [ENDPOINT, SECOND, ENDPOINT]
    assert_no_shared(result)


@pytest.mark.parametrize("updates", [
    {"command": "take_off"}, {"drone_id": None}, {"message": DESTINATION_ERROR + " extra"},
    {"message": "Target position conflicts with obstacle: Position conflicts with point obstacle 'Point'"},
])
def test_nonqualifying_error_does_not_propagate_to_other_actor(small_candidates, updates):
    receipt = {"command": "move_to", "drone_id": "d1", "status": "error", "message": DESTINATION_ERROR}
    receipt.update(updates)
    r, w, _ = make(EndpointWorld(actors=2, receipt=receipt))
    result = advance(r)
    assert result["status"] == "paused"
    assert_no_shared(result)
    w.receipt = None  # d2 gets its own valid receipt; do not forge an actor match.
    result = advance(r, result)
    assert move_points(w, "d2") == [ENDPOINT]
    assert [e["actor"] for e in result["state"]["shared_blocked_endpoints"]] == ["d2"]


@pytest.mark.parametrize("receipt,reason", [
    ({"status": "partial_success", "command": "move_to", "drone_id": "d1", "message": DESTINATION_ERROR}, "search_move_partial"),
    ({"status": "error", "success": True, "command": "move_to", "drone_id": "d1", "message": DESTINATION_ERROR}, "unknown_action_effect_observed_do_not_replay"),
    ({"harness_transport_error": "TimeoutError"}, "unknown_action_effect_observed_do_not_replay"),
    ({"status": "error", "command": "move_to", "drone_id": "wrong", "message": DESTINATION_ERROR}, "wrong_actor_receipt"),
    ({"status": "error", "command": "move_to", "drone_id": "d1", "message": "Insufficient battery"}, "search_move_error"),
])
def test_partial_unknown_wrong_actor_and_battery_cannot_create_shared_evidence(small_candidates, receipt, reason):
    r, w, _ = make(EndpointWorld(actors=2, receipt=receipt))
    result = advance(r)
    assert result["reason"] == reason and result["status"] == "needs_replan"
    assert_no_shared(result)
    assert move_points(w, "d1") == [ENDPOINT] and not move_points(w, "d2")
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_changed_xyz_after_error_is_not_shared(small_candidates, axis):
    r, w, _ = make(EndpointWorld(actors=2, effect=(axis, 1)))
    result = advance(r)
    assert result["status"] == "needs_replan"
    assert_no_shared(result)
    assert not move_points(w, "d2")
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("change", ["epoch", "external_action", "cancel"])
def test_paused_shared_plan_cannot_continue_after_existing_invalidation(small_candidates, change):
    r, w, board = make(EndpointWorld(actors=2))
    result = advance(r)
    assert result["state"]["shared_blocked_endpoints"]
    if change == "epoch":
        board.clear()
    elif change == "external_action":
        r.external_begin("external", "move_to", {"drone_id": "unbound"}, True)
        r.external_end("external")
    else:
        r.control("cancel", plan_id=result["plan_id"])
    before = copy.deepcopy(w.calls)
    rejected = r.control("resume", plan_id=result["plan_id"])
    assert rejected["status"] == "rejected" and w.calls == before
    assert rejected["reason"] == ("scene_scope_changed" if change == "epoch" else "plan_not_resumable")


def test_new_runtime_does_not_inherit_prior_shared_endpoints(small_candidates):
    w = EndpointWorld(actors=2)
    r, _, _ = make(w)
    result = advance(r)
    r.control("cancel", plan_id=result["plan_id"])
    new, _, _ = make(w)
    before = len(w.calls)
    result = advance(new)
    assert [(a["x"], a["y"]) for n, a in w.calls[before:] if n == "move_to"] == [ENDPOINT]
    assert result["state"]["shared_blocked_endpoints"][0]["sequence"] == 1


def test_duplicate_start_preserves_paused_evidence_without_more_calls(small_candidates):
    r, w, _ = make(EndpointWorld(actors=2))
    result = advance(r)
    frame = r.frames[result["plan_id"]]
    before = copy.deepcopy(asdict(frame)), copy.deepcopy(w.calls)
    rejected = r.control("start", skill="public_search")
    assert rejected["status"] == "rejected" and rejected["resumable"]
    assert (asdict(frame), w.calls) == before
    result = advance(r, result)
    assert move_points(w, "d2") == [THIRD]


@pytest.mark.parametrize("change", ["position", "radius", "height", "type", "deleted"])
def test_observed_geometry_change_or_known_board_deletion_stops_before_next_move(small_candidates, change):
    r, w, board = make(known_world(actors=2))
    result = advance(r)
    if change == "deleted":
        board.obstacles.pop("far")  # Explicit local deletion, not empty nearby.
    else:
        board.obstacles["far"]["facts"][change] = {
            "position": (1001., 1000., 0.), "radius": 2, "height": 25, "type": "ellipse"}[change]
    before_moves = [x for x in w.calls if x[0] == "move_to"]
    result = advance(r, result)
    assert result["reason"] == CHANGED
    assert [x for x in w.calls if x[0] == "move_to"] == before_moves
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_geometry_fingerprint_deepcopies_nested_fields():
    _, _, board = make(known_world())
    board.obstacles["far"]["facts"]["dimensions"] = {"axes": [1, 2]}
    observed = {}
    harness_skills._observe_static_obstacles(board, observed)
    board.obstacles["far"]["facts"]["dimensions"]["axes"][0] = 3
    assert observed["far"]["dimensions"] == {"axes": [1, 2]}
    with pytest.raises(SkillExit, match=CHANGED):
        harness_skills._observe_static_obstacles(board, observed)


def test_geometry_change_between_entry_observers_stops_before_any_action(small_candidates):
    r, w, board = make(known_world(actors=2))
    board.obstacles.clear()
    def change(name):
        if name == "get_nearby_entities":
            w.obstacles[0]["radius"] += 1
    w.after_call = change
    result = advance(r)
    assert result["reason"] == CHANGED
    assert not any(n in {"move_to", "take_off"} for n, _ in w.calls)


def test_visible_change_during_takeoff_stops_before_move(small_candidates):
    r, w, board = make(known_world(status="idle", z=57))
    def change(name):
        if name == "take_off":
            board.obstacles["far"]["facts"]["radius"] = 2
    w.after_call = change
    result = advance(r)
    assert result["reason"] == CHANGED and not move_points(w)
    assert len(actor_calls(w, "d1", "take_off")) == 1


def test_geometry_change_only_discovered_after_move_stops_before_next_actor(small_candidates):
    r, w, _ = make(known_world(actors=2))
    def change(name):
        if name == "move_to":
            w.obstacles[0]["radius"] = 2
    w.after_call = change
    result = advance(r)
    assert result["reason"] == CHANGED
    assert move_points(w, "d1") == [ENDPOINT] and not move_points(w, "d2")
    assert_no_shared(result)  # This read-time guard cannot undo the prior action.


@pytest.mark.parametrize("change", ["empty", "new_id", "metadata", "equivalent_coordinates"])
def test_empty_new_or_non_geometric_observation_keeps_shared_evidence(small_candidates, change):
    r, w, _ = make(known_world(actors=2))
    result = advance(r)
    saved = copy.deepcopy(result["state"]["shared_blocked_endpoints"])
    if change == "empty":
        w.obstacles = []
    elif change == "new_id":
        w.obstacles.append(obstacle(id="other", name="Unrelated", radius=2))
    elif change == "metadata":
        w.obstacles[0].update(name="Renamed", description="Changed note", distance=99, last_updated=100)
    else:
        w.obstacles[0]["position"] = [1000., 1000., 0.]
    result = advance(r, result)
    assert result["status"] == "paused" and move_points(w, "d2") == [THIRD]
    assert result["state"]["shared_blocked_endpoints"] == saved


@pytest.mark.parametrize("budget,has_memory", [(4, False), (5, False), (6, False), (7, True)])
def test_existing_api_budget_still_bounds_move_and_confirmation(small_candidates, budget, has_memory):
    r, w, _ = make(scheduler="applicability")
    factory = r.skill_factory
    def limited(*args):
        frame, generator = factory(*args)
        frame.api_budget = budget
        return frame, generator
    r.skill_factory = limited
    result = advance(r)
    assert result["reason"] == "api_budget_exhausted" and len(w.calls) == budget
    assert bool(result["state"].get("shared_blocked_endpoints")) == has_memory
    assert len(move_points(w)) == int(budget >= 5)
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_repair_budget_is_not_increased_to_enable_sharing(small_candidates):
    r, w, _ = make(EndpointWorld(actors=2), scheduler="applicability")
    factory = r.skill_factory
    def limited(*args):
        frame, generator = factory(*args)
        frame.repair_budget = 0
        return frame, generator
    r.skill_factory = limited
    result = advance(r)
    assert result["reason"] == "local_repair_budget_exhausted"
    assert r.frames[result["plan_id"]].repairs == 1
    assert len(result["state"]["shared_blocked_endpoints"]) == 1
    assert not move_points(w, "d2")
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("change", ["epoch", "deadline"])
def test_post_move_interrupt_prevents_confirmation_and_shared_registration(small_candidates, change):
    now = [0]
    r, w, board = make(clock=lambda: now[0])
    def interrupt(name):
        if name == "move_to":
            board.clear() if change == "epoch" else now.__setitem__(0, 500)
    w.after_call = interrupt
    if change == "deadline":
        with pytest.raises(HarnessInterrupted):
            advance(r)
    else:
        assert advance(r)["reason"] == "scene_scope_changed"
    assert w.calls[-1][0] == "move_to"
    assert not next(iter(r.frames.values())).state.get("shared_blocked_endpoints")


def test_v5_inherits_idle_positive_altitude_and_empty_entry(small_candidates):
    r, w, _ = make(EndpointWorld(status="idle", z=57, actors=2))
    result, _ = finish(r, "public_search")
    assert result["reason"] == "no_public_candidate"
    assert all(x["status"] == "valid_empty" for x in result["state"]["entry_observations"])
    assert len(actor_calls(w, "d1", "take_off")) == len(actor_calls(w, "d2", "take_off")) == 1
    assert result["state"]["shared_endpoint_scope"]["scene_epoch"] == 0
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("static", [False, True])
def test_simple_chain_does_not_require_static_search_contract(static):
    r, w, _ = make(FlightWorld("idle", 0), static=static, command=SIMPLE)
    result, _ = finish(r, "simple_chain")
    assert result["status"] == "completed" and "shared_endpoint_scope" not in result["state"]
    assert [n for n, _ in actor_calls(w, "d1", "take_off", "rotate", "hover")] == ["take_off", "rotate", "hover"]
    old, before_world, _ = make(FlightWorld("idle", 0), revision=V4, static=False, command=SIMPLE)
    baseline, _ = finish(old, "simple_chain")
    assert w.calls == before_world.calls
    assert asdict(r.frames[result["plan_id"]]) == asdict(old.frames[baseline["plan_id"]])


def test_untriggered_v4_v5_behavior_and_budgets_match_except_declared_scope():
    runs = []
    for revision in (V4, V5):
        r, w, _ = make(FlightWorld("idle", 57, blocked=True), revision=revision)
        result, _ = finish(r, "public_search")
        assert result["status"] == "completed"
        frame = asdict(r.frames[result["plan_id"]])
        frame["state"].pop("shared_endpoint_scope", None)
        assert_no_shared(result)
        runs.append((w.calls, w.drones, frame))
    assert runs[0] == runs[1]
