"""Opt-in entry/terminal contracts with scripted public receipts, no provider."""
import copy
from dataclasses import asdict

import pytest

from test_minimal_harness import PublicWorld, SEARCH, SIMPLE, finish, graph_agent, runtime
from flockcue.runtime.core import HarnessInterrupted, HarnessRuntime


def revision_runtime(command=SEARCH, scheduler="applicability", *, world=None,
                     revision="entry_contract_v2", clock=lambda: 0):
    old, world, board = runtime(command, scheduler, world=world, clock=clock)
    current = HarnessRuntime(
        task_id=old.task_id, command=old.command, scene_id=old.scene_id,
        scene_epoch=old.scene_epoch, ledger=old.ledger, invoke=old.invoke,
        skill_factory=old.skill_factory, scope=old.scope, started=old.started,
        scheduler=scheduler, fixed_k=old.fixed_k, clock=clock, revision=revision)
    return current, world, board


def empty_world():
    world = PublicWorld()
    world.obstacles = []
    return world


def actions(world):
    return [(name, args) for name, args in world.calls
            if name in {"take_off", "change_altitude", "rotate", "hover", "move_to"}]


def test_legacy_empty_entry_and_terminal_feedback_are_unchanged():
    r, w, _ = revision_runtime(world=empty_world(), revision="legacy")
    result = r.control("start", skill="public_search")
    assert result["reason"] == "empty_public_obstacle_blackboard"
    assert w.calls == [("get_current_session", {})]
    assert "resumable" not in result and "revision" not in r.snapshot()
    assert r.control("start", skill="public_search") == {
        "status": "rejected", "reason": "skill_already_committed_use_plan_id"}
    assert r.control("resume", plan_id=result["plan_id"]) == result


@pytest.mark.parametrize("scheduler", sorted(HarnessRuntime.POLICIES))
def test_empty_entry_observes_fleet_once_before_motion_and_keeps_scoped_evidence(scheduler):
    r, w, board = revision_runtime(scheduler=scheduler, world=empty_world())
    result, _ = finish(r, "public_search")
    assert result["status"] == "completed" and not result["resumable"]
    assert result["state"]["progress_percent"] == 96
    first_action = next(i for i, (name, _) in enumerate(w.calls) if name == "take_off")
    assert w.calls[:first_action] == [
        ("get_current_session", {}), ("list_drones", {}),
        ("get_nearby_entities", {"drone_id": "d1"}),
        ("get_nearby_entities", {"drone_id": "d2"}),
        ("get_drone_status", {"drone_id": "d1"})]
    observations = result["state"]["entry_observations"]
    assert [o["actor"] for o in observations] == ["d1", "d2"]
    assert all(o["status"] == "valid_empty" and o["obstacle_count"] == 0
               and o["scene_id"] == "s1" and o["scene_epoch"] == 0
               and o["scope"] == "local_obstacle_observation" for o in observations)
    frame = r.frames[result["plan_id"]]
    assert all(o["evidence_ref"] in frame.evidence_refs for o in observations)
    assert result["state"]["entry_observations_complete"] is True
    assert board.full()["obstacles"] == []
    assert frame.source_obligations[0]["text"] == SEARCH
    assert len(r.ledger.snapshot()["events"]) == len(w.calls)


def test_fresh_unobserved_board_requires_real_sensing_not_synthetic_empty():
    r, w, board = revision_runtime(world=empty_world())
    board._step = 0  # No observation marker; the entry must read public receipts.
    r.control("start", skill="public_search")
    assert w.calls[2:4] == [("get_nearby_entities", {"drone_id": "d1"}),
                             ("get_nearby_entities", {"drone_id": "d2"})]


def test_nonempty_entry_preserves_the_legacy_public_call_sequence():
    runs = []
    for revision in ("legacy", "entry_contract_v2"):
        r, w, _ = revision_runtime(world=PublicWorld(blocked=True), revision=revision)
        result, _ = finish(r, "public_search")
        assert "entry_observations" not in result["state"]
        runs.append((w.calls, w.drones, result["state"]))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("receipt", [
    None, [], {}, {"obstacles": []},
    {"drones": [], "targets": [], "obstacles": None},
    {"drones": [], "targets": [], "obstacles": [{}]},
    {"drones": [], "targets": [], "obstacles": [], "status": "error"},
    {"drones": [], "targets": [], "obstacles": [], "success": False},
    {"drones": [], "targets": [], "obstacles": [], "success": 1},
    {"drones": [], "targets": [], "obstacles": [], "error": "read failed"},
])
def test_invalid_entry_read_is_not_empty_and_cannot_start_motion(receipt):
    r, w, board = revision_runtime(world=empty_world())
    invoke = r.invoke
    r.invoke = lambda item: copy.deepcopy(receipt) if item.tool == "get_nearby_entities" else invoke(item)
    result = r.control("start", skill="public_search")
    assert result["status"] == "needs_replan"
    assert result["reason"] == "entry_public_perception_unavailable"
    assert result["state"]["entry_observations"][0]["status"] == "invalid"
    assert not result["resumable"] and not actions(w)
    assert not board.obstacles


def test_lost_entry_read_does_not_become_empty_or_retry():
    r, w, _ = revision_runtime(world=empty_world())
    invoke = r.invoke
    reads = []
    def lost(item):
        if item.tool == "get_nearby_entities":
            reads.append(item.args)
            raise TimeoutError("offline lost perception reply")
        return invoke(item)
    r.invoke = lost
    result = r.control("start", skill="public_search")
    assert result["reason"] == "entry_public_perception_unavailable"
    assert reads == [{"drone_id": "d1"}] and not actions(w)
    assert len(r.ledger.snapshot()["events"]) == 3


def test_newly_sensed_public_obstacles_are_ingested_without_private_geometry():
    w = PublicWorld()
    r, _, board = revision_runtime(world=w)
    board.obstacles.clear()
    result, _ = finish(r, "public_search")
    assert result["status"] == "completed"
    assert [o["status"] for o in result["state"]["entry_observations"]] == ["valid_nonempty"] * 2
    assert set(board.obstacles) == {"o1"}


def test_scene_reset_during_entry_does_not_ingest_late_receipt_or_move():
    r, w, board = revision_runtime(world=empty_world())
    w.after_call = lambda name: board.clear() if name == "get_nearby_entities" else None
    result = r.control("start", skill="public_search")
    assert result["reason"] == "scene_scope_changed" and not actions(w)
    assert result["state"]["entry_observations"] == []


def test_entry_reads_obey_api_budget_and_deadline_before_any_action():
    r, w, _ = revision_runtime(world=empty_world())
    factory = r.skill_factory
    def limited(*args):
        frame, generator = factory(*args)
        frame.api_budget = 3  # Progress, fleet, first nearby. The second cannot run.
        return frame, generator
    r.skill_factory = limited
    result = r.control("start", skill="public_search")
    assert result["reason"] == "api_budget_exhausted"
    assert len(result["state"]["entry_observations"]) == 1 and not actions(w)
    now = [0]
    r, w, _ = revision_runtime(world=empty_world(), clock=lambda: now[0])
    w.after_call = lambda name: now.__setitem__(0, 500) if name == "get_nearby_entities" else None
    with pytest.raises(HarnessInterrupted):
        r.control("start", skill="public_search")
    assert not actions(w)


def test_duplicate_start_on_paused_plan_returns_the_actual_resumable_id():
    r, w, _ = revision_runtime(SIMPLE, scheduler="every_step")
    first = r.control("start", skill="simple_chain")
    before = copy.deepcopy(w.calls)
    rejected = r.control("start", skill="simple_chain")
    assert rejected["status"] == "rejected" and rejected["plan_status"] == "paused"
    assert rejected["reason"] == "skill_already_committed_resume_existing_plan"
    assert rejected["plan_id"] == first["plan_id"] and rejected["resumable"]
    assert w.calls == before
    continued = r.control("resume", plan_id=first["plan_id"])
    assert continued["cursor"] == 2
    assert sum(name == "take_off" for name, _ in w.calls) == 1


@pytest.mark.parametrize("terminal", ["needs_replan", "completed", "cancelled"])
def test_terminal_controls_explicitly_reject_without_replay_or_state_loss(terminal):
    w = PublicWorld()
    if terminal == "needs_replan":
        w.fail = "take_off"  # The action applies but its reply is lost.
    r, _, _ = revision_runtime(SIMPLE, scheduler="every_step", world=w)
    first = r.control("start", skill="simple_chain")
    if terminal == "completed":
        while first["status"] == "paused":
            first = r.control("resume", plan_id=first["plan_id"])
    if terminal == "cancelled":
        first = r.control("cancel", plan_id=first["plan_id"])
    frame = r.frames[first["plan_id"]]
    before = copy.deepcopy(asdict(frame))
    calls = copy.deepcopy(w.calls)
    for action, kwargs in [("start", {"skill": "simple_chain"}), ("resume", {"plan_id": frame.plan_id})]:
        result = r.control(action, **kwargs)
        assert result["status"] == "rejected" and not result["resumable"]
        assert result["plan_status"] == terminal and result["plan_reason"] == before["reason"]
        assert result["cursor"] == before["cursor"] and result["state"] == before["state"]
        assert result["reason"] == ("plan_not_resumable" if action == "resume" else
                                     "skill_already_committed_not_resumable")
        assert asdict(frame) == before and w.calls == calls
    assert sum(name == "take_off" for name, _ in w.calls) == 1


def test_revision_is_explicit_and_does_not_relax_full_task_grammar():
    with pytest.raises(ValueError, match="revision"):
        revision_runtime(revision="typo")
    r, w, _ = revision_runtime(SEARCH + " Take a photo.", world=empty_world())
    assert r.control("start", skill="public_search")["status"] == "unsupported"
    assert w.calls == []


def test_install_harness_opt_in_exposes_contract_only_on_selected_tool(monkeypatch):
    monkeypatch.setenv("UAV_HARNESS_REVISION", "entry_contract_v2")
    agent = graph_agent(monkeypatch)
    result = agent.execute(SIMPLE)
    assert result["success"] and result["harness"]["revision"] == "entry_contract_v2"
    selected = next(tool for tool in agent.tools if tool.name == "run_harness")
    assert "Entry contract v2 is enabled" in selected.description
    monkeypatch.delenv("UAV_HARNESS_REVISION")
    legacy = graph_agent(monkeypatch)
    result = legacy.execute(SIMPLE)
    assert "revision" not in result["harness"]
    selected = next(tool for tool in legacy.tools if tool.name == "run_harness")
    assert "Entry contract v2 is enabled" not in selected.description
