"""Public fixed-target follow-up contracts; scripted receipts, no simulator/model."""
import copy
import math
import socket
from types import SimpleNamespace

import pytest
from shapely.geometry import Point

import harness_skills
from coordinated_search_r2 import PublicSearchPolicy
from harness_core import HarnessInterrupted
from harness_integration import install_harness
from observed_target_search import ObservedTargetFollowup
from test_harness_blocked_candidate import (DESTINATION_ERROR, ENDPOINT, PATH_ERROR,
    SmallCandidatePolicy, advance, move_points)
from test_harness_flight_state import FlightWorld, actor_calls, reject_without_effect
from test_harness_shared_endpoint import make as shared_runtime
from test_harness_terminal_progress import (ExhaustedPolicy, ProgressWorld,
    assert_no_completion, limit_api)
from test_minimal_harness import finish


V6 = "terminal_progress_v6"
V7 = "observed_target_v7"
TARGET_XY = (125.0, 100.0)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in observed-target tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def target(**updates):
    value = {"id": "t1", "name": "Fixed Target 1", "type": "fixed",
             "position": {"x": TARGET_XY[0], "y": TARGET_XY[1], "z": 0.0},
             "is_reached": False, "radius": 5, "velocity": None, "moving_path": None}
    value.update(updates)
    return value


def nearby(*items):
    return {"targets": list(items), "drones": [], "obstacles": []}


def reached_action(**updates):
    receipt = {"drone_id": "d1", "command": "move_to", "status": "success",
               "message": "Drone moved to position (125.0, 100.0, 20.0), heading: 0.0° "
                          "(battery: -0.1%/-1mAh, remaining: 99.9%/99mAh) - Reached target(s): Fixed Target 1"}
    receipt.update(updates)
    return {"tool": "move_to", "args": {"drone_id": "d1", "x": 125, "y": 100, "z": 20},
            "receipt": receipt}


def pure():
    world = FlightWorld("hovering", 20)
    for drone in world.drones.values():
        drone["task_radius"] = 20
    return ObservedTargetFollowup(), PublicSearchPolicy(list(world.drones.values()), []), world.drones


def choose(f, p, states, actor="d1", shared=None):
    return f.choose(actor, states[actor], p, shared or {}, p.choose(actor), round_number=1, sequence=1)


@pytest.mark.parametrize("updates", [
    {"id": ""}, {"id": 3}, {"name": "Target 1"}, {"name": "Fixed Target 0"},
    {"name": "Fixed Target 1 extra"}, {"type": "moving"}, {"type": "circle"},
    {"position": None}, {"position": [125, 100, 0]},
    {"position": {"x": True, "y": 100, "z": 0}},
    {"position": {"x": 125, "y": float("nan"), "z": 0}},
    {"position": {"x": 125, "y": 100, "z": float("inf")}},
    {"position": {"x": 125, "y": 100}},
])
def test_only_finite_identified_standard_fixed_points_are_candidates(updates):
    f, p, states = pure()
    f.observe(nearby(target(**updates)), "d1", "e1")
    assert choose(f, p, states) is None and not f.snapshot()["targets"]


@pytest.mark.parametrize("updates", [{"status": "error"}, {"error": "failed"},
    {"harness_transport_error": "TimeoutError"}, {"success": False}, {"success": 1},
    {"partial_success": True}, {"partial_success": 0}, {"drones": None}, {"obstacles": {}}])
def test_failed_or_incomplete_nearby_adds_no_target(updates):
    f, p, states = pure()
    f.observe({**nearby(target()), **updates}, "d1", "e1")
    assert choose(f, p, states) is None


def test_missing_source_does_not_authorize_target_and_snapshot_is_detached():
    f, p, states = pure()
    f.observe(nearby(target()), "d1", None)
    assert not f.snapshot()["targets"]
    f.observe(nearby(target()), "d1", "e1")
    saved = f.snapshot()
    saved["targets"][0]["position"][0] = 999
    assert f.snapshot()["targets"][0]["position"][0] == 125


def test_first_observer_owns_single_attempt_and_actual_policy_state_stays_consistent():
    f, p, states = pure()
    f.observe(nearby(target()), "d1", "e1")
    f.observe(nearby(target()), "d2", "e2")
    assert choose(f, p, states, "d2") is None
    count = len(p.candidates)
    original = copy.deepcopy(p.candidates)
    result = choose(f, p, states)
    assert result[0] == count and result[1] == TARGET_XY
    assert p.candidates[:-1] == original and len(p.candidate_cover) == count + 1
    expected = frozenset(i for i, cell in enumerate(p.score_cells) if math.dist(TARGET_XY, cell) <= p.radius)
    assert p.candidate_cover[count] == expected
    before = set(p.covered)
    p.accept("d1", count, "completed", TARGET_XY, [], round_number=1, sequence=1)
    f.record_action(count, "completed", "e3")
    assert p.xy["d1"] == TARGET_XY and count in p.selected and p.covered == before | expected
    assert choose(f, p, states) is None and choose(f, p, states, "d2") is None
    attempt = f.snapshot()["attempts"][0]
    assert attempt["observation_ref"] == "e1" and attempt["action_ref"] == "e3"
    assert attempt["actor"] == "d1" and attempt["status"] == "action_returned"


@pytest.mark.parametrize("radius", [None, True, 0, -1, "20", float("nan"), float("inf"), 19.999])
def test_invalid_or_insufficient_task_radius_cannot_authorize_height(radius):
    f, p, states = pure()
    f.observe(nearby(target()), "d1", "e1")
    states["d1"]["task_radius"] = radius
    assert choose(f, p, states) is None


@pytest.mark.parametrize("z,allowed", [(0, True), (40, True), (-0.001, False), (40.001, False)])
def test_height_qualification_includes_exact_task_radius_boundary(z, allowed):
    f, p, states = pure()
    f.observe(nearby(target(position={"x": 125, "y": 100, "z": z})), "d1", "e1")
    assert (choose(f, p, states) is not None) == allowed


@pytest.mark.parametrize("updates", [{"name": "Fixed Target 2"}, {"type": "moving"},
    {"position": {"x": 126, "y": 100, "z": 0}}, {"position": None}])
def test_identity_or_geometry_change_disables_permanently(updates):
    f, p, states = pure()
    f.observe(nearby(target()), "d1", "e1")
    f.observe(nearby(target(**updates)), "d2", "e2")
    f.observe(nearby(target()), "d1", "e3")
    assert choose(f, p, states) is None
    assert f.snapshot()["targets"][0]["disabled_reason"] == "observed_identity_or_geometry_changed"


def test_duplicate_names_disable_both_ids_without_claiming_either_completed():
    f, p, states = pure()
    f.observe(nearby(target(), target(id="other")), "d1", "e1")
    assert choose(f, p, states) is None
    assert all(t["disabled_reason"] == "ambiguous_public_name" for t in f.snapshot()["targets"])
    assert f.snapshot()["positive_names"] == {}


@pytest.mark.parametrize("invalid", [{"position": None}, {"type": "moving"}])
@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_name_with_ineligible_id_still_suppresses_valid_candidate(invalid, reverse):
    f, p, states = pure()
    rows = [target(), target(id="other", **invalid)]
    if reverse:
        rows.reverse()
    for i, row in enumerate(rows):
        f.observe(nearby(row), "d1", f"e{i}")
    assert choose(f, p, states) is None


@pytest.mark.parametrize("old_directed", [False, True])
def test_fresh_index_cannot_bypass_existing_same_actor_origin_destination_attempt(old_directed):
    f, p, states = pure()
    if old_directed:
        f.observe(nearby(target()), "d1", "e0")
        prior = choose(f, p, states)
        old_index, destination = prior[:2]
        # Distinct public IDs/names can share a point; the existing failed edge
        # remains evidence even when this new ID has not itself been scheduled.
        next_target = target(id="t2", name="Fixed Target 2")
    else:
        old_index, destination = 0, p.candidates[0]
        next_target = target(position={"x": destination[0], "y": destination[1], "z": 0})
    p.accept("d1", old_index, "blocked", p.xy["d1"], [], round_number=1, sequence=1)
    f.observe(nearby(next_target), "d1", "e1")
    assert choose(f, p, states) is None


@pytest.mark.parametrize("other", ["actor", "origin"])
def test_path_attempt_memory_is_not_expanded_to_other_actor_or_origin(other):
    f, p, states = pure()
    old_index, destination = 0, p.candidates[0]
    actor = "d2" if other == "actor" else "d1"
    origin = p.xy["d1"] if other == "actor" else (p.xy["d1"][0] + 1, p.xy["d1"][1])
    p.tried_edges.add((actor, origin, old_index))
    f.observe(nearby(target(position={"x": destination[0], "y": destination[1], "z": 0})), "d1", "e1")
    assert choose(f, p, states) is not None


@pytest.mark.parametrize("before", [True, False])
def test_positive_receipt_suppresses_even_before_first_observation_and_false_never_revives(before):
    f, p, states = pure()
    if not before:
        f.observe(nearby(target()), "d1", "e1")
    f.observe_move(reached_action(), "d1", "e2")
    f.observe(nearby(target(is_reached=False)), "d1", "e3")
    assert choose(f, p, states) is None
    positive = f.snapshot()["positive_names"]["Fixed Target 1"]
    assert positive["scope"] == "name_only_suppression_not_target_id_completion"
    assert positive["evidence_ref"] == "e2" and "completed" not in f.snapshot()["targets"][0]


def test_positive_boolean_flag_survives_later_false_and_cannot_claim_mission_completion():
    f, p, states = pure()
    f.observe(nearby(target(is_reached=True)), "d1", "e1")
    f.observe(nearby(target(is_reached=False)), "d1", "e2")
    assert choose(f, p, states) is None and f.snapshot()["targets"][0]["positive_flag_ref"] == "e1"
    assert f.snapshot()["positive_names"] == {}


@pytest.mark.parametrize("updates", [{"status": "error"}, {"status": "partial_success"},
    {"drone_id": "d2"}, {"command": "take_off"}, {"success": False}, {"success": 1},
    {"partial_success": True}, {"error": "failed"}, {"unsuccessful_points_count": 1},
    {"message": "Quoted: " + reached_action()["receipt"]["message"]},
    {"message": reached_action()["receipt"]["message"] + "."},
    {"message": "Drone moved to position (125,100,20) - Reached target(s): unknown"}])
def test_ambiguous_failed_or_wrong_actor_receipt_does_not_suppress(updates):
    f, p, states = pure()
    f.observe_move(reached_action(**updates), "d1", "e1")
    f.observe(nearby(target()), "d1", "e2")
    assert not f.snapshot()["positive_names"] and choose(f, p, states) is not None


@pytest.mark.parametrize("kind", ["endpoint", "edge", "shared", "disabled"])
def test_existing_known_geometry_shared_exact_endpoint_and_disabled_actor_still_guard(kind):
    f, p, states = pure()
    f.observe(nearby(target()), "d1", "e1")
    shared = {}
    if kind == "endpoint":
        p.blocked = Point(*TARGET_XY).buffer(1)
    elif kind == "edge":
        p.blocked = Point(112, 100).buffer(1)
    elif kind == "shared":
        shared = {(*TARGET_XY, 20): {"source": "e0"}}
    else:
        p.disable("d1")
    assert choose(f, p, states, shared=shared) is None
    assert f.snapshot()["attempts"] == []


class TargetWorld(FlightWorld):
    def __init__(self, *, actors=1, mode="success", targets=None, initial_targets=True):
        super().__init__("hovering", 20)
        if actors == 1:
            self.drones.pop("d2")
        for drone in self.drones.values():
            drone["task_radius"] = 20
        self.obstacles = []
        self.targets = [target()] if targets is None else targets
        self.mode, self.initial_targets = mode, initial_targets

    def _call(self, name, **args):
        if name == "get_current_session":
            return self._reply(name, args, {"id": "s1", "statistics": {"task_progress": {
                "task_type": "target_assignment", "progress_percentage": 0,
                "details": {"total_targets": 3, "visited_targets": 0, "unvisited_targets": 3}}}})
        if name == "get_nearby_entities":
            return self._reply(name, args, nearby(*(self.targets if self.initial_targets or self.moves else [])))
        if name == "move_to" and (args["x"], args["y"]) == TARGET_XY:
            if self.mode in {"endpoint", "path", "partial", "wrong_actor", "effect_mismatch"}:
                receipt = {"drone_id": args["drone_id"], "command": name, "status": "success"}
                if self.mode in {"endpoint", "path"}:
                    receipt.update(status="error", message=DESTINATION_ERROR if self.mode == "endpoint" else PATH_ERROR)
                elif self.mode == "partial":
                    receipt["status"] = "partial_success"
                elif self.mode == "wrong_actor":
                    receipt["drone_id"] = "other"
                return self._reply(name, args, receipt)
            if self.mode == "unknown":
                self.fail = "move_to"
        return super()._call(name, **args)


def make(world=None, **kwargs):
    return shared_runtime(world or TargetWorld(), revision=kwargs.pop("revision", V7), **kwargs)


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", SmallCandidatePolicy)
    monkeypatch.setattr(harness_skills, "MAX_ROUNDS", 3)


def test_real_generator_replaces_normal_slot_and_does_not_repeat_or_claim_success(small):
    r, w, _ = make(scheduler="every_step")
    first = advance(r)
    assert move_points(w) == [TARGET_XY] and first["cursor"] == 1
    state = first["state"]["observed_target_followup"]
    attempt = state["attempts"][0]
    assert attempt["fallback_exploration"]["destination"] == [*ENDPOINT, 20]
    assert attempt["candidate_index"] == 3
    events = {e["evidence_id"]: e for e in r.ledger.snapshot()["events"]}
    assert events[attempt["observation_ref"]]["tool_name"] == "get_nearby_entities"
    assert events[attempt["action_ref"]]["args"] == {"drone_id": "d1", "x": 125, "y": 100, "z": 20}
    result = first
    while result["status"] == "paused":
        result = advance(r, result)
    assert move_points(w).count(TARGET_XY) == 1 and len(move_points(w)) <= 3
    assert_no_completion(result)
    frame = r.frames[result["plan_id"]]
    assert frame.deadline == 500 and frame.api_budget == 2000 and frame.repair_budget == 16
    reject_without_effect(r, w, frame)


@pytest.mark.parametrize("mode,reason", [("partial", "search_move_partial"),
    ("wrong_actor", "wrong_actor_receipt"), ("effect_mismatch", "search_move_effect_mismatch"),
    ("unknown", "unknown_action_effect_observed_do_not_replay")])
def test_target_attempt_unconfirmed_effect_is_never_completion_or_replay(small, mode, reason):
    r, w, _ = make(TargetWorld(mode=mode))
    result, _ = finish(r, "public_search")
    assert result["reason"] == reason and move_points(w) == [TARGET_XY]
    assert_no_completion(result)
    assert len(result["state"]["observed_target_followup"]["attempts"]) == 1
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("mode", ["endpoint", "path"])
def test_failed_directed_index_never_leaks_to_ordinary_exploration(small, mode):
    r, w, _ = make(TargetWorld(mode=mode), scheduler="every_step")
    result, _ = finish(r, "public_search")
    assert move_points(w).count(TARGET_XY) == 1
    assert r.frames[result["plan_id"]].repairs == 1 and result["cursor"] <= 3
    assert result["state"]["observed_target_followup"]["attempts"][0]["outcome"] == "blocked"
    assert_no_completion(result)


def test_directed_to_grid_alias_cannot_retry_same_edge_but_new_origin_remains_allowed(small):
    class GridAliasWorld(TargetWorld):
        def __init__(self):
            super().__init__(targets=[target(position={"x": ENDPOINT[0], "y": ENDPOINT[1], "z": 0})])
        def _call(self, name, **args):
            if name == "move_to" and (args["x"], args["y"]) == ENDPOINT:
                return self._reply(name, args, {"command": name, "drone_id": args["drone_id"],
                    "status": "error", "message": PATH_ERROR})
            return super()._call(name, **args)
    r, w, _ = make(GridAliasWorld(), scheduler="every_step")
    result = advance(r)
    assert move_points(w) == [ENDPOINT]
    attempt = result["state"]["observed_target_followup"]["attempts"][0]
    assert attempt["candidate_index"] == 3 and attempt["outcome"] == "blocked"
    assert attempt["fallback_exploration"]["candidate_index"] == 0
    assert not result["state"].get("shared_blocked_endpoints")
    result = advance(r, result)
    assert move_points(w) == [ENDPOINT, (110.0, 100.0)]
    assert w.drones["d1"]["position"] == {"x": 110.0, "y": 100.0, "z": 20.0}
    result = advance(r, result)
    assert move_points(w) == [ENDPOINT, (110.0, 100.0), ENDPOINT]
    assert len(result["state"]["observed_target_followup"]["attempts"]) == 1
    assert r.frames[result["plan_id"]].repairs == 2


@pytest.mark.parametrize("budget,move_count", [(4, 0), (5, 1)])
def test_budget_exhaustion_before_or_after_directed_action_never_adds_a_retry(small, budget, move_count):
    r, w, _ = make()
    limit_api(r, budget)
    result, _ = finish(r, "public_search")
    assert result["reason"] == "api_budget_exhausted" and len(w.calls) == budget
    assert move_points(w).count(TARGET_XY) == move_count
    assert_no_completion(result)
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_current_plan_observation_required_and_other_actor_cannot_take_ownership(small):
    r, w, board = make(TargetWorld(actors=2, initial_targets=False), scheduler="every_step")
    board.ingest_nearby("d2", nearby(target(id="stale", name="Fixed Target 2")))
    first = advance(r)
    assert move_points(w) == [ENDPOINT]
    second = advance(r, first)
    assert TARGET_XY not in move_points(w, "d2")
    third = advance(r, second)
    assert move_points(w) == [ENDPOINT, TARGET_XY]
    targets = third["state"]["observed_target_followup"]["targets"]
    assert [t["target_id"] for t in targets] == ["t1"] and targets[0]["owner"] == "d1"


def test_v6_ignores_fixed_target_optimization_and_no_target_v7_has_same_calls(small):
    runs = []
    for rev, values in [(V6, [target()]), (V6, []), (V7, [])]:
        r, w, _ = make(TargetWorld(targets=values), revision=rev)
        result, _ = finish(r, "public_search")
        runs.append((w.calls, result))
        if rev == V6:
            assert "observed_target_followup" not in result["state"]
    assert runs[0][0] == runs[1][0] == runs[2][0]


@pytest.mark.parametrize("final,status,reason", [(100, "completed", "skill_public_stop_condition"),
    (95, "needs_replan", "no_public_candidate"),
    (TimeoutError("lost read"), "needs_replan", "public_progress_unavailable")])
def test_inherits_terminal_confirmation_complete_noncomplete_and_failed_read(monkeypatch, final, status, reason):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", ExhaustedPolicy)
    r, w, _ = make(ProgressWorld(final), scheduler="applicability")
    result = advance(r)
    assert result["status"] == status and result["reason"] == reason and w.progress_reads == 2
    assert not actor_calls(w, "d1", "move_to", "take_off")
    assert result["mission_completion"] == "not_evaluated"
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_inherits_terminal_budget_boundary_without_extra_api(monkeypatch):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", ExhaustedPolicy)
    r, w, _ = make(ProgressWorld(), scheduler="applicability")
    limit_api(r, 3)
    result = advance(r)
    assert result["reason"] == "api_budget_exhausted" and w.progress_reads == 1 and len(w.calls) == 3
    assert_no_completion(result)


@pytest.mark.parametrize("updates", [{"session_id": "other"}, {"source_endpoint": "/private/check"},
    {"scope": "whole_task_checker"}, {"partial_success": True}, {"success": 1},
    {"progress_percentage": float("nan")}, {"progress_percentage": True}])
def test_terminal_numeric_completion_still_requires_valid_public_contract(monkeypatch, updates):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", ExhaustedPolicy)
    r, w, _ = make(ProgressWorld(), scheduler="applicability")
    invoke = r.invoke
    def altered(call):
        result = invoke(call)
        if call.tool == "get_mission_progress" and w.progress_reads == 2:
            return {**result, **updates}
        return result
    r.invoke = altered
    result = advance(r)
    assert result["reason"] == "public_progress_unavailable" and w.progress_reads == 2
    assert_no_completion(result)


@pytest.mark.parametrize("change", ["epoch", "deadline", "cancel"])
def test_directed_action_remains_under_epoch_deadline_cancel_guards(small, change):
    now, cancelled = [0], [False]
    r, w, board = make(clock=lambda: now[0])
    def cancel():
        if cancelled[0]:
            raise RuntimeError("cancelled by test")
    r.cancel_check = cancel
    def stop(name):
        if name == "get_drone_status":
            if change == "epoch":
                board.clear()
            elif change == "deadline":
                now[0] = 500
            else:
                cancelled[0] = True
    w.after_call = stop
    if change == "epoch":
        result = advance(r)
        assert result["reason"] == "scene_scope_changed"
    else:
        with pytest.raises(HarnessInterrupted if change == "deadline" else RuntimeError):
            advance(r)
    assert not actor_calls(w, "d1", "move_to", "take_off")
    assert next(iter(r.frames.values())).status != "completed"


def test_static_contract_required_and_install_keeps_legacy_default(monkeypatch):
    r, w, board = make(static=False)
    result = advance(r)
    assert result["reason"] == "shared_endpoint_requires_static_obstacle_contract" and w.calls == []
    agent = SimpleNamespace(blackboard=board, client=w, tools=[], _harness_task_started=0,
                            _harness_cancel_check=lambda: None)
    monkeypatch.setenv("UAV_HARNESS_REVISION", V7)
    monkeypatch.setenv("UAV_HARNESS_STATIC_OBSTACLES", "1")
    install_harness(agent, r.ledger)
    assert agent._harness.revision == V7 and agent._harness.static_obstacle_contract is True
    monkeypatch.delenv("UAV_HARNESS_REVISION")
    install_harness(agent, r.ledger)
    assert agent._harness.revision == "legacy" and w.calls == []
