"""Compact projections never invent landing coordinates or change completion."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
from flockcue.memory.ledger import SessionEvidenceIndexMemory
from flockcue.memory.state_changes import query_decision_context_for
from flockcue.memory.remaining_work import ExecutionMemoryLedgerV5
from flockcue.memory.compact_view import ExecutionMemoryLedgerV6
from flockcue.evidence.ledger import encode
from test_memory_ledger import ROUTE, position, sense, path
from test_memory_remaining_work import contact, land, route


def ledger(command=ROUTE, cls=ExecutionMemoryLedgerV6, **kwargs):
    g = cls(command, **kwargs)
    g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}, {"id": "d2", "name": "Drone 2"}])
    return g


def completed(**kwargs):
    g = ledger(**kwargs)
    route(g)
    land(g)
    return g


def position_read(g, xyz=(70, 80, 0), status="idle", actor="d1", **fields):
    return g.observe_tool("get_drone_status", {"drone_id": actor},
                         {"id": actor, "position": dict(zip("xyz", xyz)), "status": status, **fields})


def landing_row(g):
    return next(r for r in g.decision_context()["resume"]["done"] if r["kind"] == "landing")


def test_land_without_position_stays_unknown_not_waypoint_or_default_zero():
    g = completed(command_id="current")
    original = copy.deepcopy(g._events)
    observation = g.terminal_observations()[0]
    assert observation["position_state"] == "unknown" and "position" not in observation
    assert landing_row(g)["position"] == "unknown"
    view = g.decision_context()
    assert "without coordinates" in view["resume"]["policy"]
    assert "Historical route points are not landing coordinates" in view["resume"]["policy"]
    assert view["resume"]["remaining"] == []
    assert "routes" not in view and "goals" not in view
    assert g._events == original


@pytest.mark.parametrize("field", ["position", "final_position"])
def test_explicit_successful_land_position_is_sourced_as_returned(field):
    g = ledger(command_id="current")
    route(g)
    eid = land(g, **{field: {"x": 70, "y": 80, "z": 0}})
    observed = g.terminal_observations()[0]
    assert observed["position"] == [70, 80, 0]  # Returned field, never a synthesized zero.
    assert observed["position_source"] == {"command": "current", "event": eid}
    assert observed["position_path"] == [field]


def test_requested_nested_or_message_position_is_not_terminal_position():
    g = ledger()
    route(g)
    g.observe_tool("land", {"drone_id": "d1", "x": 70, "y": 80, "z": 0},
                   {"command": "land", "drone_id": "d1", "status": "success",
                    "message": "landed at (70,80,0)", "details": {"position": {"x": 70, "y": 80, "z": 0}}})
    assert landing_row(g)["position"] == "unknown"


@pytest.mark.parametrize("fields", [{"position": {"x": 70, "y": 80, "z": "0"}},
                                    {"position": [70, 80, 0], "final_position": [70, 80, 5]}])
def test_invalid_or_conflicting_landing_fields_are_unknown(fields):
    g = ledger()
    route(g)
    land(g, **fields)
    assert g.terminal_observations()[0]["position_state"] in {"unknown", "conflicting"}


def test_successful_post_land_status_supplies_position_with_source():
    g = completed(command_id="current")
    eid = position_read(g)
    assert landing_row(g)["position"] == [70, 80, 0]
    assert landing_row(g)["position_source"] == {"command": "current", "event": eid}
    assert g.completion_context() == ExecutionMemoryLedgerV5.completion_context(g)


def test_list_drones_position_is_public_but_duplicate_identity_is_ambiguous():
    g = completed()
    row = {"id": "d1", "status": "idle", "position": {"x": 70, "y": 80, "z": 0}}
    g.observe_tool("list_drones", {}, [{"id": "d2"}, row])
    assert g.terminal_observations()[0]["position_path"] == [1, "position"]
    g.observe_tool("list_drones", {}, [row, row])
    assert landing_row(g)["position"] == "unknown"


@pytest.mark.parametrize("fault", ["failure", "wrong_identity", "missing_position", "unknown_status", "pending", "overlap"])
def test_uncertain_refresh_does_not_reuse_old_position_as_current(fault):
    g = completed()
    position_read(g)
    if fault == "failure": position_read(g, success=False)
    elif fault == "wrong_identity": position_read(g, id="d2")
    elif fault == "missing_position":
        g.observe_tool("get_drone_status", {"drone_id": "d1"}, {"id": "d1", "status": "idle"})
    elif fault == "unknown_status": position_read(g, status="unknown")
    else:
        read = g.begin_tool("get_drone_status", {"drone_id": "d1"})
        if fault == "overlap":
            other = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
            g.end_tool(read, {"id": "d1", "status": "idle", "position": {"x": 70, "y": 80, "z": 0}})
            g.end_tool(other, {"targets": []})
    assert landing_row(g)["position"] == "unknown"
    assert any(r["kind"] == "landing" for r in g.completion_context()["done"])


def test_other_actor_read_does_not_supply_or_invalidate_position():
    g = completed()
    position_read(g, actor="d2", xyz=(12, 13, 0))
    assert landing_row(g)["position"] == "unknown"
    position_read(g)
    position_read(g, actor="d2", xyz=(12, 13, 0))
    assert landing_row(g)["position"] == [70, 80, 0]


@pytest.mark.parametrize("later", ["takeoff", "unknown_action", "positive_height"])
def test_later_actions_preserve_v5_landing_invalidation(later):
    g = completed()
    position_read(g)
    if later == "positive_height": position_read(g, xyz=(70, 80, 5))
    else:
        eid = g.begin_tool("take_off", {"drone_id": "d1", "altitude": 5})
        if later == "takeoff": g.end_tool(eid, {"command": "take_off", "drone_id": "d1", "status": "success"})
        else: g.end_tool(eid, error="transport unknown")
    assert g.terminal_observations() == []
    assert any(r["kind"] == "landing" for r in g.decision_context()["resume"]["remaining"])
    assert g.completion_context() == ExecutionMemoryLedgerV5.completion_context(g)


def test_previous_command_and_epoch_never_supply_terminal_position():
    index = SessionEvidenceIndexMemory("session")
    first = completed(command_id="old", index=index)
    position_read(first)
    assert ledger(command_id="new", index=index).terminal_observations() == []
    index.clear()
    assert first.terminal_observations() == []
    assert first.decision_context()["scene_changed"]


def test_completed_visit_is_once_with_source_and_not_relocalized():
    g = ledger("Drone 1 must visit Fixed Target 10.", command_id="current")
    position(g)
    sense(g)
    eid = contact(g)
    view = g.decision_context()
    assert "goals" not in view and "routes" not in view
    assert view["resume"]["done"] == [{"id": "ref-1", "kind": "visit", "actor": "d1", "target": "Fixed Target 10",
                                        "source": {"command": "current", "event": eid}}]
    assert not view["resume"]["remaining"]
    assert g.completion_context() == ExecutionMemoryLedgerV5.completion_context(g)


def test_query_reason_facts_and_contract_are_exactly_v5():
    g = ledger("Drone 1 must visit Fixed Target 10.")
    position(g)
    sense(g)
    sense(g)
    g.set_decision_context(actor_id="d1", decision_kind="locate")
    baseline = ExecutionMemoryLedgerV5.decision_context(g, **g._memory_focus)
    view = g.decision_context(**g._memory_focus)
    for key in ("checked_here", "advice", "query_decision_contract", "last_query"):
        assert view.get(key) == baseline.get(key)
    capture = query_decision_context_for(g)
    assert capture["contract_active"] is True
    assert capture["latest_query_outcome"]["d1"]["state"] == "empty_targets"


def test_failed_sense_remains_retryable_and_audit_does_not_control_tools():
    g = ledger("Drone 1 must visit Fixed Target 10.")
    position(g)
    sense(g, change={"error": "failed"})
    assert g.decision_context()["last_query"][0]["state"] == "failed"
    assert g.decision_context()["resume"]["remaining"]
    before = copy.deepcopy(g._events)
    audit = g.record_model_response("Retry", [{"name": "get_nearby_entities", "args": {"drone_id": "d1"}, "id": "retry"}],
                                    context=g.query_decision_context())
    assert audit["sensing_calls"][0]["execution_policy"] == "allowed_unchanged"
    assert g._events == before
    sense(g)
    assert len(g._events) == len(before) + 1


def test_route_suffix_and_prefix_each_appear_once():
    g = ledger()
    path(g, [[10, 20, 5]], partial=True)
    view = g.decision_context()
    assert view["resume"]["done"][0]["ordered"] == 1
    assert view["resume"]["remaining"][0]["goals"] == [
        {"ordinal": 2, "point": [40, 50, 5]}, {"ordinal": 3, "point": [70, 80, 5]}]
    assert view["resume"]["remaining"][1]["kind"] == "landing"
    assert "routes" not in view


def test_large_view_marks_omissions_instead_of_false_completion():
    command = " ".join(f"Drone {n} must visit Fixed Target {n}." for n in range(1, 40))
    g = ledger(command)
    view = g.decision_context()
    assert len(encode(view)) <= 3900
    assert view["resume"]["omitted"]["remaining"] > 0
    assert view["resume"]["unverified"] or view["resume"]["omitted"].get("unverified")
    assert g.completion_context()["unverified"][0]["text"] == command


@pytest.mark.parametrize("cap", [0, 100, 500, 1500, 3900])
def test_small_caps_preserve_v5_contract_or_explicit_omissions(cap):
    g = ledger("Drone 1 must visit Fixed Target 10.", max_summary_chars=cap)
    position(g)
    sense(g)
    baseline = ExecutionMemoryLedgerV5.decision_context(g)
    view = g.decision_context()
    assert len(g.summary()) <= cap
    assert view.get("query_decision_contract") == baseline.get("query_decision_contract")
    if view.get("schema") == "execution-memory-v6" and not view["resume"]["remaining"]:
        assert view["resume"]["omitted"].get("remaining")


def test_six_fixtures_are_shorter_with_identical_evidence_and_sources():
    root = Path(__file__).resolve().parents[2] / "experiments/execution-memory-mvp-v5/fixtures"
    for case in ("clue-horizontal", "clue-vertical", "route-interrupted", "route-blocked", "control-complete", "control-failed-sense"):
        fixture = json.loads((root / f"{case}.json").read_text())
        a = ExecutionMemoryLedgerV5(fixture["command"], "controlled-task")
        b = ExecutionMemoryLedgerV6(fixture["command"], "controlled-task")
        for entry in fixture["prefix"]:
            for g in (a, b):
                eid = g.begin_tool(entry["tool"], entry["args"])
                g.end_tool(eid, entry.get("output"), error=entry.get("error"))
        av = a.decision_context(decision_kind=fixture["decision_kind"])
        bv = b.decision_context(decision_kind=fixture["decision_kind"])
        assert a.completion_context() == b.completion_context()
        assert a.projection() == b.projection() and a.route_projection() == b.route_projection()
        assert a._events == b._events
        assert len(encode(bv)) < len(encode(av)), (case, len(encode(av)), len(encode(bv)))
        assert not bv["resume"].get("omitted")
        for key in ("checked_here", "advice", "query_decision_contract", "last_query"):
            assert bv.get(key) == av.get(key)


def test_snapshot_keeps_full_v5_audit_and_does_not_mutate_facts():
    g = completed()
    original = (copy.deepcopy(g._events), g.completion_context())
    snapshot = g.snapshot()
    assert snapshot["revision"] == "execution-memory-v6"
    assert snapshot["completion_memory"] == original[1]
    assert snapshot["terminal_observations"] == g.terminal_observations()
    assert (g._events, g.completion_context()) == original


@pytest.mark.parametrize("xyz", [[999, 999, 20], [999, 999, 0], [70, 80, 20]])
def test_conflicting_land_coordinates_are_audited_not_presented_as_known(xyz):
    g = ledger(command_id="current")
    route(g)
    eid = land(g, position=xyz)
    original = g.completion_context()
    observation = g.terminal_observations()[0]
    assert observation["position_state"] == "conflicting"
    assert observation["reported_positions"][0]["value"] == xyz
    assert observation["reported_positions"][0]["source"]["event"] == eid
    row = landing_row(g)
    assert row["position"] == "unknown" and row["evidence_conflict"]
    assert g.decision_context()["resume"]["unverified"][0]["kind"] == "terminal_evidence_conflict"
    assert g.completion_context() == original
    position_read(g)
    assert g.terminal_observations()[0]["position_state"] == "conflicting"
    assert any(r["reason"] == "land_receipt_disagrees_with_post_landing_status"
               for r in g.terminal_observations()[0]["conflicts"])


def test_normal_missing_landing_position_never_adds_a_new_verification_obligation():
    g = completed()
    assert not g.decision_context()["resume"]["unverified"]
    assert not g.decision_context()["resume"]["remaining"]
    assert g.terminal_observations()[0]["position_state"] == "unknown"


def test_historical_clue_source_is_preserved_without_transferring_visit():
    index = SessionEvidenceIndexMemory("session")
    old = ledger("Drone 1 must visit Fixed Target 10.", command_id="old", index=index)
    position(old)
    contact(old)
    current = ledger("Drone 2 must visit Fixed Target 10.", command_id="new", index=index)
    view = current.decision_context()
    assert not view["resume"]["done"]
    clue = view["resume"]["remaining"][0]["clue"]
    assert clue["by"] == "d1" and clue["source"]["command"] == "old"
    assert "clues may be historical" in view["scope"]
