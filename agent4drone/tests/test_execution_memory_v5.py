"""Completion evidence survives observation changes without becoming a gate."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution_memory import SessionEvidenceIndexMemory
from execution_memory_v4 import ExecutionMemoryLedgerV4, query_decision_context_for
from execution_memory_v5 import ExecutionMemoryLedgerV5
from task_evidence import encode
from test_execution_memory import ROUTE, path, position, sense


def ledger(command="Drone 1 must visit Fixed Target 10.", cls=ExecutionMemoryLedgerV5, **kwargs):
    g = cls(command, **kwargs)
    g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}, {"id": "d2", "name": "Drone 2"}])
    return g


def contact(g, actor="d1", target="Fixed Target 10"):
    return g.observe_tool("move_to", {"drone_id": actor, "x": 10, "y": 0, "z": 5},
        {"command": "move_to", "drone_id": actor, "status": "success",
         "message": f"Drone moved to position (10, 0, 5) - Reached target(s): {target}"})


def land(g, actor="d1", **fields):
    return g.observe_tool("land", {"drone_id": actor},
                         {"command": "land", "drone_id": actor, "status": "success", **fields})


def route(g):
    return path(g, [[10, 20, 5], [40, 50, 5], [70, 80, 5]])


def done(g, kind):
    return [r for r in g.completion_context()["done"] if r["kind"] == kind]


def test_visit_receipt_survives_false_target_flags_without_center_localization():
    g = ledger(command_id="current")
    eid = contact(g)
    assert done(g, "visit")[0]["source"] == {"command": "current", "event": eid}
    assert g.projection()[0]["knowledge_state"] == "reported_only"
    sense(g, [{"id": "t10", "name": "Fixed Target 10", "position": {"x": 9, "y": 0, "z": 5},
               "is_reached": False, "reached_by": []}])
    view = g.completion_context()
    assert len(view["done"]) == 1
    assert view["remaining"] == view["unverified"] == []
    assert g.decision_context()["resume"]["done"][0]["source"]["event"] == eid
    assert "does not undo" in g.decision_context()["resume"]["policy"]
    assert view["mission_completion"] == "not_evaluated"


def test_other_actor_contact_is_only_knowledge():
    g = ledger()
    contact(g, actor="d2")
    assert not done(g, "visit")
    assert g.completion_context()["remaining"][0]["actor"] == "d1"


def test_previous_command_and_epoch_never_transfer_completion():
    index = SessionEvidenceIndexMemory("session")
    old = ledger(command_id="old", index=index)
    contact(old)
    new = ledger(command_id="new", index=index)
    assert not done(new, "visit")
    assert new.projection()[0]["knowledge_state"] == "reported_only"
    index.clear()
    assert not done(old, "visit")
    assert old.completion_context()["scene_valid"] is False


def test_plan_done_model_text_and_localization_do_not_prove_visit():
    g = ledger()
    sense(g, [{"id": "t10", "name": "Fixed Target 10", "position": {"x": 9, "y": 0, "z": 5},
               "is_reached": True, "reached_by": ["d1"]}])
    g.record_model_response("I visited Fixed Target 10", [], context=g.query_decision_context())
    g.decision_context(plan={"status": "completed", "done": True})
    assert not done(g, "visit")


def test_repeated_original_visit_does_not_share_one_receipt():
    g = ledger("Drone 1 visit Fixed Target 10. Drone 1 visit Fixed Target 10.")
    contact(g)
    assert not done(g, "visit")
    assert len(g.completion_context()["remaining"]) == 2
    assert g.completion_context()["unverified"]


@pytest.mark.parametrize("suffix", [" Then take a photo.", " and return home.", " while maintaining altitude 50."])
def test_extra_original_clauses_are_not_swallowed(suffix):
    command = "Drone 1 must visit Fixed Target 10" + suffix
    g = ledger(command)
    contact(g)
    full = g.completion_context()
    assert done(g, "visit")
    assert "".join(r["text"] for r in full["unverified"]) == command
    assert full["mission_completion"] == "not_evaluated"


def test_route_coordinates_and_terminal_landing_are_separate():
    g = ledger(ROUTE)
    eid = route(g)
    assert done(g, "route_coordinates")[0]["ordered"] == 3
    assert not done(g, "landing")
    assert [r["kind"] for r in g.completion_context()["remaining"]] == ["landing"]
    landed = land(g)
    assert done(g, "landing")[0]["source"]["event"] == landed
    assert done(g, "route_coordinates")[0]["source"]["event"] == eid
    assert g.completion_context()["remaining"] == g.completion_context()["unverified"] == []


def test_interrupted_route_preserves_exact_original_suffix():
    g = ledger(ROUTE)
    path(g, [[10, 20, 5]], partial=True)
    remaining = g.completion_context()["remaining"]
    assert remaining[0]["from_ordinal"] == 2
    assert remaining[0]["goals"] == [{"ordinal": 2, "point": [40, 50, 5]}, {"ordinal": 3, "point": [70, 80, 5]}]
    assert remaining[1]["kind"] == "landing"
    assert remaining[1]["after"] == "ordered_route_coordinates"


def test_early_landing_cannot_complete_later_route():
    g = ledger(ROUTE)
    land(g)
    route(g)
    assert not done(g, "landing")


@pytest.mark.parametrize("case", ["failed", "wrong_command", "wrong_actor", "unknown", "intervening_move", "after_takeoff", "after_unknown"])
def test_invalid_or_nonterminal_landing_is_not_done(case):
    g = ledger(ROUTE)
    route(g)
    if case == "failed": land(g, status="error")
    elif case == "wrong_command": land(g, command="take_off")
    elif case == "wrong_actor": land(g, drone_id="d2")
    elif case == "unknown": g.observe_tool("land", {"drone_id": "d1"}, {"outcome": "completed"})
    elif case == "intervening_move": contact(g); land(g)
    else:
        land(g)
        pending = g.begin_tool("take_off", {"drone_id": "d1", "altitude": 5})
        if case == "after_takeoff":
            g.end_tool(pending, {"command": "take_off", "drone_id": "d1", "status": "success"})
        else:
            g.end_tool(pending, error="transport outcome unknown")
    assert not done(g, "landing")
    assert any(r["kind"] == "landing" for r in g.completion_context()["remaining"])


def test_concurrent_same_actor_landing_is_not_certified():
    g = ledger(ROUTE)
    route(g)
    landing = g.begin_tool("land", {"drone_id": "d1"})
    query = g.begin_tool("get_drone_status", {"drone_id": "d1"})
    g.end_tool(landing, {"command": "land", "drone_id": "d1", "status": "success"})
    g.end_tool(query, {"id": "d1", "status": "idle", "position": {"x": 70, "y": 80, "z": 0}})
    assert not done(g, "landing")


def test_later_ground_status_keeps_landing_but_airborne_status_does_not():
    g = ledger(ROUTE)
    route(g)
    land(g)
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "status": "idle", "position": {"x": 70, "y": 80, "z": 0}})
    assert done(g, "landing")
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "status": "hovering", "position": {"x": 70, "y": 80, "z": 5}})
    assert not done(g, "landing")


def test_route_intro_altitude_requirement_remains_unverified():
    intro = "Drone Drone 1 perform a marathon flight at 5 meters. "
    g = ledger(intro + ROUTE)
    assert g.routes
    route(g)
    land(g)
    assert g.completion_context()["unverified"][0]["text"] == intro


def test_same_coordinate_receipt_does_not_complete_repeated_route_ordinal():
    command = "Drone 1 route: fly to and start at (10,20,5), then waypoints (10,20,5), then end (70,80,5) and land."
    g = ledger(command)
    path(g, [[10, 20, 5], [70, 80, 5]])
    assert done(g, "route_coordinates")[0]["ordered"] == 1
    assert g.completion_context()["remaining"][0]["from_ordinal"] == 2


def test_failed_sense_remains_retryable_and_queries_are_never_blocked():
    g = ledger()
    position(g)
    sense(g, change={"error": "failed"})
    original = copy.deepcopy(g._events)
    context = g.query_decision_context()
    g.record_model_response("Retrying", [{"name": "get_nearby_entities", "args": {"drone_id": "d1"}, "id": "q"}], context=context)
    assert g._events == original
    assert not g.empty_query_checks()
    assert g.completion_context()["remaining"]
    sense(g)
    assert len(g._events) == len(original) + 1


@pytest.mark.parametrize("cap", [0, 100, 1000, 2000, 3900])
def test_bound_never_removes_v4_facts_or_hides_omissions(cap):
    g = ledger(max_summary_chars=cap)
    position(g)
    sense(g)
    g.set_decision_context(actor_id="d1", decision_kind="locate")
    base = ExecutionMemoryLedgerV4.decision_context(g, **g._memory_focus)
    revised = g.decision_context(**g._memory_focus)
    revised.pop("resume", None)
    revised.pop("resume_omitted", None)
    assert revised == base
    assert len(g.summary()) <= cap
    assert query_decision_context_for(g)["contract_active"] == ("query_decision_contract" in base)


def test_large_unverified_input_keeps_original_and_explicit_omissions():
    command = "Drone 1 must visit Fixed Target 10. " + "Take photographs and return home. " * 100
    g = ledger(command)
    contact(g)
    assert g.command == command
    assert g.completion_context()["unverified"][0]["text"] == command
    resume = g.decision_context()["resume"]
    assert resume.get("omitted", {}).get("unverified") or resume["unverified"][0]["omitted_chars"] > 0


def test_all_six_frozen_fixtures_replay_without_provider_or_v4_fact_changes():
    root = Path(__file__).resolve().parents[2] / "experiments/execution-memory-mvp-v4/fixtures"
    cases = ["clue-horizontal", "clue-vertical", "route-interrupted", "route-blocked", "control-complete", "control-failed-sense"]
    for case in cases:
        fixture = json.loads((root / f"{case}.json").read_text())
        a = ExecutionMemoryLedgerV4(fixture["command"], "controlled-task")
        b = ExecutionMemoryLedgerV5(fixture["command"], "controlled-task")
        for entry in fixture["prefix"]:
            for g in (a, b):
                eid = g.begin_tool(entry["tool"], entry["args"])
                g.end_tool(eid, entry.get("output"), error=entry.get("error"))
        revised = b.decision_context(decision_kind=fixture["decision_kind"])
        assert len(encode(revised)) <= 3900
        resume = revised.pop("resume")
        assert revised == a.decision_context(decision_kind=fixture["decision_kind"])
        assert len(encode(resume)) <= 1100
        if case == "control-complete":
            assert not resume["remaining"] and not resume["unverified"] and not resume.get("omitted")
            assert {r["kind"] for r in resume["done"]} == {"route_coordinates", "landing"}
        elif case.startswith("route-"):
            assert any(r["kind"] == "route_coordinates" and r["from_ordinal"] == 3 for r in resume["remaining"])
        else:
            assert not resume["done"] and resume["remaining"]


def test_snapshot_keeps_full_completion_audit_separate_from_query_notes():
    g = ledger()
    contact(g)
    original = g.summary()
    g.record_model_response("{" , [], context=g.query_decision_context())
    snapshot = g.snapshot()
    assert snapshot["revision"] == "execution-memory-v5"
    assert snapshot["completion_memory"] == g.completion_context()
    assert snapshot["query_decisions"]
    assert g.summary() == original


@pytest.mark.parametrize("wrong_actor", [False, True])
def test_reusing_command_id_cannot_import_previous_task_visit(wrong_actor):
    index = SessionEvidenceIndexMemory("session")
    first = ledger(command_id="reused", index=index)
    contact(first)
    second = ledger(command_id="reused", index=index)
    assert not done(second, "visit")
    # Neither an unrelated movement nor a wrong-actor receipt with identical
    # text can authenticate an inherited report with the same event ID.
    second.observe_tool("move_to", {"drone_id": "d1", "x": 10, "y": 0, "z": 5},
                        {"command": "move_to", "drone_id": "d2" if wrong_actor else "d1", "status": "success",
                         "message": ("Drone moved to position (10, 0, 5) - Reached target(s): Fixed Target 10"
                                     if wrong_actor else "Drone moved to position (9, 0, 5)")})
    assert not done(second, "visit")


def test_actual_nested_partial_contact_survives_outer_navigation_failure():
    g = ledger()
    eid = g.observe_tool("navigate_to", {"drone_id": "d1", "x": 100, "y": 0, "z": 5},
                        {"success": False, "reason": "obstacle_blocked", "last_move_result": {
                            "command": "move_along_path", "drone_id": "d1", "status": "partial_success",
                            "successful_points": [[10, 0, 5]], "successful_points_count": 1,
                            "message": "Partial path - Reached target(s): Fixed Target 10"}})
    assert g.projection()[0]["execution_state"] == "evidenced"
    assert done(g, "visit")[0]["source"]["event"] == eid


@pytest.mark.parametrize("recovery", ["failed_land_status", "refly_return", "repeat_land"])
def test_fresh_endpoint_anchor_allows_legitimate_landing_recovery(recovery):
    g = ledger(ROUTE)
    route(g)
    if recovery == "failed_land_status":
        land(g, status="error")
        position(g, xyz=(70, 80, 5))
    elif recovery == "refly_return":
        land(g)
        g.observe_tool("take_off", {"drone_id": "d1", "altitude": 5},
                       {"command": "take_off", "drone_id": "d1", "status": "success"})
        path(g, [[70, 80, 5]])
    else:
        land(g)
    last = land(g)
    assert done(g, "landing")[0]["source"]["event"] == last


@pytest.mark.parametrize("status", ["idle", "unknown", "moving", "landing"])
def test_post_landing_height_or_nonterminal_status_removes_terminal_support(status):
    g = ledger(ROUTE)
    route(g)
    land(g)
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "status": status,
                    "position": {"x": 70, "y": 80, "z": 5 if status in {"idle", "unknown"} else 0}})
    assert not done(g, "landing")


def test_final_route_point_inside_path_is_not_landing_location():
    g = ledger(ROUTE)
    path(g, [[10, 20, 5], [40, 50, 5], [70, 80, 5], [90, 90, 5]])
    land(g)
    assert done(g, "route_coordinates")
    assert not done(g, "landing")
    # A real return, not just passing that point earlier, can restore the anchor.
    path(g, [[70, 80, 5]])
    land(g)
    assert done(g, "landing")
