"""Opt-in public flight-state contract; scripted receipts, no network/provider.

The strict PublicWorld derivative reproduces the server's status precondition,
without treating these unit receipts as real simulator validation.
"""
import copy
from dataclasses import asdict
import socket

from langchain_core.messages import AIMessage
import pytest

from test_harness_entry_contract import actions, revision_runtime
from test_minimal_harness import PublicWorld, SEARCH, SIMPLE, finish, graph_agent
from test_task_progress_integration import call
from harness_core import HarnessInterrupted, HarnessRuntime


V3 = "flight_state_v3"
AIRBORNE = {"hovering", "flying", "moving"}
MISSING = object()


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in flight-state unit tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


class FlightWorld(PublicWorld):
    def __init__(self, status="idle", z=57, *, takeoff_modes=None, blocked=False):
        super().__init__(blocked=blocked)
        self.takeoff_modes = takeoff_modes or {}
        for drone in self.drones.values():
            if status is MISSING:
                drone.pop("status")
            else:
                drone["status"] = copy.deepcopy(status)
            drone["position"]["z"] = z

    def _reply(self, name, args, result):
        self.calls.append((name, copy.deepcopy(args)))
        self.after_call(name)
        return copy.deepcopy(result)

    def _call(self, name, **args):
        if name == "move_to":
            status = self.drones[args["drone_id"]].get("status")
            if not isinstance(status, str) or status not in AIRBORNE:
                return self._reply(name, args, {
                    "status": "error", "command": name, "drone_id": args["drone_id"],
                    "message": "Cannot move in current status " + str(status)})
        if name == "take_off":
            actor = args["drone_id"]
            mode = self.takeoff_modes.get(actor)
            if mode == "error":
                return self._reply(name, args, {"status": "error", "command": name,
                                              "drone_id": actor, "message": "Takeoff rejected"})
            if mode in {"receipt_only", "position_only", "wrong_actor"}:
                if mode == "position_only":
                    self.drones[actor]["position"]["z"] = args["altitude"]
                return self._reply(name, args, {"status": "success", "command": name,
                                              "drone_id": "other" if mode == "wrong_actor" else actor})
            if mode == "unknown_after_effect":
                self.fail = "take_off"  # Existing fake applies once, then loses reply.
        return super()._call(name, **args)


def make(world=None, *, revision=V3, scheduler="applicability", clock=lambda: 0):
    return revision_runtime(SEARCH, scheduler, world=world or FlightWorld(),
                            revision=revision, clock=clock)


def actor_calls(world, actor, *names):
    return [(name, args) for name, args in world.calls
            if args.get("drone_id") == actor and (not names or name in names)]


def reject_without_effect(runtime, world, frame):
    saved = copy.deepcopy(asdict(frame))
    calls = copy.deepcopy(world.calls)
    drones = copy.deepcopy(world.drones)
    for action, kwargs in (("start", {"skill": frame.skill}),
                           ("resume", {"plan_id": frame.plan_id})):
        response = runtime.control(action, **kwargs)
        assert response["status"] == "rejected" and not response["resumable"]
        assert response["plan_status"] == saved["status"]
        assert response["plan_reason"] == saved["reason"]
        assert response["cursor"] == saved["cursor"] and response["state"] == saved["state"]
        assert asdict(frame) == saved
        assert world.calls == calls and world.drones == drones


@pytest.mark.parametrize("revision", ["legacy", "entry_contract_v2"])
def test_old_revisions_still_reproduce_idle_above_ground_move_error(revision):
    r, w, _ = make(revision=revision)
    result, _ = finish(r, "public_search")
    assert result["status"] == "needs_replan"
    assert result["reason"] == "search_move_error"
    assert [name for name, _ in actions(w)] == ["move_to"]
    assert w.moves == 0 and result["cursor"] == 0


@pytest.mark.parametrize("status", ["idle", "ready"])
@pytest.mark.parametrize("scheduler", sorted(HarnessRuntime.POLICIES))
def test_v3_takes_off_idle_or_ready_at_z57_once_before_search(status, scheduler):
    r, w, _ = make(FlightWorld(status), scheduler=scheduler)
    result, _ = finish(r, "public_search")
    assert result["status"] == "completed" and not result["resumable"]
    assert result["state"]["progress_percent"] == 96 and result["cursor"] == 10
    for actor in w.drones:
        calls = actor_calls(w, actor)
        names = [name for name, _ in calls]
        assert names[:4] == ["get_drone_status", "take_off", "get_drone_status", "move_to"]
        takeoffs = actor_calls(w, actor, "take_off")
        assert takeoffs == [("take_off", {"drone_id": actor, "altitude": 20.0})]
    assert r.frames[result["plan_id"]].source_obligations[0]["text"] == SEARCH
    assert len(r.ledger.snapshot()["events"]) == len(w.calls)


@pytest.mark.parametrize("status", sorted(AIRBORNE))
@pytest.mark.parametrize("z", [0, 57])
def test_v3_airborne_status_does_not_repeat_takeoff_even_at_z0(status, z):
    r, w, _ = make(FlightWorld(status, z))
    result, _ = finish(r, "public_search")
    assert result["status"] == "completed"
    assert not any(name == "take_off" for name, _ in w.calls)
    assert w.moves == 10


@pytest.mark.parametrize("status,z", [("idle", 0), ("ready", 0), ("hovering", 57),
                                     ("flying", 57), ("moving", 57)])
def test_normal_inputs_have_identical_v2_v3_public_calls_and_plan_state(status, z):
    runs = []
    for revision in ("entry_contract_v2", V3):
        r, w, _ = make(FlightWorld(status, z, blocked=True), revision=revision)
        result, _ = finish(r, "public_search")
        frame = r.frames[result["plan_id"]]
        assert result["status"] == "completed"
        runs.append((w.calls, w.drones, asdict(frame)))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("listed,fresh,should_takeoff", [
    ("hovering", "idle", True), ("idle", "hovering", False),
    ("ready", "moving", False), ("flying", "ready", True)])
def test_fresh_status_overrides_fleet_listing_without_extra_read(listed, fresh, should_takeoff):
    w = FlightWorld(listed)
    def change(name):
        if name == "list_drones":
            for drone in w.drones.values():
                drone["status"] = fresh
    w.after_call = change
    r, _, _ = make(w, scheduler="every_step")
    result = r.control("start", skill="public_search")
    assert result["status"] == "paused" and result["cursor"] == 1
    calls = actor_calls(w, "d1")
    expected = ["get_drone_status"]
    if should_takeoff:
        expected += ["take_off", "get_drone_status"]
    expected += ["move_to", "get_drone_status", "get_nearby_entities"]
    assert [name for name, _ in calls] == expected


@pytest.mark.parametrize("status", [MISSING, None, "", "landed", "landing", "taking_off",
                                    "charging", "emergency", "offline", "IDLE", 3, [], {}])
def test_unknown_missing_or_nonstring_fresh_status_cannot_authorize_action(status):
    r, w, _ = make(FlightWorld(status))
    result = r.control("start", skill="public_search")
    assert result["status"] == "needs_replan"
    assert result["reason"] == "search_actor_flight_state_unavailable"
    assert not actions(w) and result["cursor"] == 0
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_takeoff_error_disables_actor_without_retrying_or_moving_it():
    r, w, _ = make(FlightWorld(takeoff_modes={"d1": "error"}))
    result, _ = finish(r, "public_search")
    assert result["status"] == "completed"
    assert len(actor_calls(w, "d1", "take_off")) == 1
    assert not actor_calls(w, "d1", "move_to")
    assert r.frames[result["plan_id"]].repairs == 1
    assert len(actor_calls(w, "d2", "take_off")) == 1


def test_unknown_takeoff_effect_observes_once_and_rejects_restart():
    r, w, _ = make(FlightWorld(takeoff_modes={"d1": "unknown_after_effect"}))
    result = r.control("start", skill="public_search")
    assert result["reason"] == "unknown_action_effect_observed_do_not_replay"
    assert [name for name, _ in actor_calls(w, "d1")] == ["get_drone_status", "take_off", "get_drone_status"]
    assert not actor_calls(w, "d1", "move_to") and w.drones["d1"]["status"] == "hovering"
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("mode,reason", [
    ("receipt_only", "search_takeoff_effect_mismatch"),
    ("position_only", "search_takeoff_state_mismatch"),
    ("wrong_actor", "wrong_actor_receipt")])
def test_success_receipt_without_verified_state_never_authorizes_move(mode, reason):
    r, w, _ = make(FlightWorld(takeoff_modes={"d1": mode}))
    result = r.control("start", skill="public_search")
    assert result["status"] == "needs_replan" and result["reason"] == reason
    assert len(actor_calls(w, "d1", "take_off")) == 1
    assert not actor_calls(w, "d1", "move_to") and result["cursor"] == 0
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("status", [MISSING, None, "idle", "ready", "landing", [], {}])
def test_takeoff_position_is_not_enough_if_followup_status_is_unavailable(status):
    w = FlightWorld()
    def replace(name):
        if name == "take_off":
            if status is MISSING:
                w.drones["d1"].pop("status")
            else:
                w.drones["d1"]["status"] = copy.deepcopy(status)
    w.after_call = replace
    r, _, _ = make(w)
    result = r.control("start", skill="public_search")
    assert result["reason"] == "search_takeoff_state_mismatch"
    assert [name for name, _ in actor_calls(w, "d1")] == ["get_drone_status", "take_off", "get_drone_status"]
    assert not actor_calls(w, "d1", "move_to")


@pytest.mark.parametrize("status", ["idle", "ready", "landing"])
def test_resume_uses_changed_status_after_previous_search_attempt(status):
    r, w, _ = make(FlightWorld("hovering"), scheduler="every_step")
    result = r.control("start", skill="public_search")  # d1
    result = r.control("resume", plan_id=result["plan_id"])  # d2
    assert result["status"] == "paused" and result["cursor"] == 2
    w.drones["d1"]["status"] = status  # Same public position; no external tool call.
    before = len(w.calls)
    result = r.control("resume", plan_id=result["plan_id"])
    calls = w.calls[before:]
    assert calls[0] == ("get_drone_status", {"drone_id": "d1"})
    if status == "landing":
        assert result["reason"] == "search_actor_flight_state_unavailable"
        assert len(calls) == 1 and result["cursor"] == 2
    else:
        assert result["status"] == "paused" and result["cursor"] == 3
        assert [name for name, _ in calls[:4]] == ["get_drone_status", "take_off", "get_drone_status", "move_to"]
        assert len(actor_calls(w, "d1", "take_off")) == 1


@pytest.mark.parametrize("revision", ["legacy", "entry_contract_v2"])
def test_old_airborne_cache_still_reproduces_paused_status_change_error(revision):
    r, w, _ = make(FlightWorld("hovering"), scheduler="every_step", revision=revision)
    result = r.control("start", skill="public_search")
    result = r.control("resume", plan_id=result["plan_id"])
    w.drones["d1"]["status"] = "idle"
    result = r.control("resume", plan_id=result["plan_id"])
    assert result["reason"] == "search_move_error" and result["cursor"] == 2
    assert not actor_calls(w, "d1", "take_off")


@pytest.mark.parametrize("trigger", ["get_drone_status", "take_off"])
def test_epoch_change_between_reads_and_actions_stops_further_io(trigger):
    w = FlightWorld()
    r, _, board = make(w)
    w.after_call = lambda name: board.clear() if name == trigger else None
    result = r.control("start", skill="public_search")
    assert result["reason"] == "scene_scope_changed"
    assert w.calls[-1][0] == trigger and not actor_calls(w, "d1", "move_to")


@pytest.mark.parametrize("trigger", ["get_drone_status", "take_off"])
def test_deadline_between_reads_and_actions_stops_further_io(trigger):
    now = [0]
    w = FlightWorld()
    r, _, _ = make(w, clock=lambda: now[0])
    w.after_call = lambda name: now.__setitem__(0, 500) if name == trigger else None
    with pytest.raises(HarnessInterrupted):
        r.control("start", skill="public_search")
    assert w.calls[-1][0] == trigger and not actor_calls(w, "d1", "move_to")
    assert r.ledger.snapshot()["events"][-1]["end_sequence"] is not None


@pytest.mark.parametrize("budget,last", [(3, "get_drone_status"), (4, "take_off"), (5, "get_drone_status")])
def test_existing_api_budget_bounds_added_takeoff_and_confirmation(budget, last):
    r, w, _ = make()
    factory = r.skill_factory
    def limited(*args):
        frame, generator = factory(*args)
        frame.api_budget = budget
        return frame, generator
    r.skill_factory = limited
    result = r.control("start", skill="public_search")
    assert result["reason"] == "api_budget_exhausted" and result["cursor"] == 0
    assert len(w.calls) == budget and w.calls[-1][0] == last
    assert not actor_calls(w, "d1", "move_to")
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_v3_inherits_empty_entry_observation_and_complete_task_grammar():
    w = FlightWorld()
    w.obstacles = []
    r, _, _ = make(w, scheduler="every_step")
    result = r.control("start", skill="public_search")
    assert result["status"] == "paused" and result["resumable"]
    assert [entry["status"] for entry in result["state"]["entry_observations"]] == ["valid_empty", "valid_empty"]
    first_action = next(i for i, (name, _) in enumerate(w.calls) if name == "take_off")
    assert [name for name, _ in w.calls[:first_action]] == [
        "get_current_session", "list_drones", "get_nearby_entities", "get_nearby_entities", "get_drone_status"]
    r, w, _ = revision_runtime(SEARCH + " Take a photo.", revision=V3)
    assert r.control("start", skill="public_search")["status"] == "unsupported"
    assert w.calls == []


@pytest.mark.parametrize("terminal", ["needs_replan", "completed", "cancelled"])
def test_v3_inherits_terminal_controls_without_replay_or_evidence_loss(terminal):
    w = FlightWorld("landing" if terminal == "needs_replan" else "idle")
    r, _, _ = make(w, scheduler="every_step")
    result = r.control("start", skill="public_search")
    if terminal == "completed":
        while result["status"] == "paused":
            result = r.control("resume", plan_id=result["plan_id"])
    elif terminal == "cancelled":
        result = r.control("cancel", plan_id=result["plan_id"])
    assert result["status"] == terminal
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_v3_duplicate_start_keeps_paused_plan_and_does_not_takeoff_again():
    r, w, _ = make(scheduler="every_step")
    result = r.control("start", skill="public_search")
    frame = r.frames[result["plan_id"]]
    before = copy.deepcopy(asdict(frame)), copy.deepcopy(w.calls)
    rejected = r.control("start", skill="public_search")
    assert rejected["status"] == "rejected" and rejected["resumable"]
    assert rejected["plan_status"] == "paused" and rejected["plan_id"] == frame.plan_id
    assert (asdict(frame), w.calls) == before


def test_env_revision_reaches_real_agent_runtime_factory_and_keeps_default(monkeypatch):
    monkeypatch.setenv("UAV_HARNESS_REVISION", V3)
    agent = graph_agent(monkeypatch, replies=[
        call("run_harness", "commit", action="start", skill="public_search"),
        AIMessage(content="Public skill completed. [TASK DONE]")])
    agent.client = FlightWorld()
    result = agent.execute(SEARCH)
    assert result["success"] and result["harness"]["revision"] == V3
    assert len(actor_calls(agent.client, "d1", "take_off")) == 1
    selected = next(tool for tool in agent.tools if tool.name == "run_harness")
    assert "Entry contract v2 is enabled" in selected.description
    monkeypatch.delenv("UAV_HARNESS_REVISION")
    legacy = graph_agent(monkeypatch)
    result = legacy.execute(SIMPLE)
    assert result["success"] and "revision" not in result["harness"]
    selected = next(tool for tool in legacy.tools if tool.name == "run_harness")
    assert "Entry contract v2 is enabled" not in selected.description
