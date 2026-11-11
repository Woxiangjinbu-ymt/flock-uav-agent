"""V2 query-evidence and compact-boundary checks; offline only."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
from flockcue.memory.ledger import ExecutionMemoryLedger, SessionEvidenceIndexMemory
from flockcue.memory.recent_sensing import ExecutionMemoryLedgerV2
from flockcue.evidence.ledger import encode
from test_memory_ledger import ROUTE, position, sense, path


def ledger(command="Drone 1 visit Fixed Target 10.", command_id="current", index=None, **kwargs):
    g = ExecutionMemoryLedgerV2(command, command_id, index=index, **kwargs)
    g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"},
                                     {"id": "d2", "name": "Drone 2"}])
    return g


def advice(g, actor="d1"):
    return g.decision_context(actor_id=actor, decision_kind="locate")


def test_repeated_default_empty_scans_produce_sourced_advice_without_executing():
    g = ledger()
    position(g)
    first, last = sense(g), sense(g)
    before = len(g._events)
    view = advice(g)
    checks = g.empty_query_checks("d1")
    assert len(checks) == 1 and checks[0]["retained_count"] == 2
    assert checks[0]["source_events"] == [last, first]
    assert view["checked_here"][0]["last"] == {"command_id": "current", "evidence_id": last}
    assert "avoid another immediate identical scan" in view["advice"]
    assert "purpose differs" in view["advice"]
    assert len(g._events) == before
    assert g.projection()[0]["knowledge_state"] == "unknown"
    assert g.projection()[0]["execution_state"] == "unattempted"
    assert len(encode(view)) <= 1400
    assert "original_command" not in view and "actual_effects" not in view


@pytest.mark.parametrize("failure", [{"error": "sensor failed"}, {"status": "failed"},
                                     {"status": "error"}, {"success": False},
                                     {"tool_exception": "lost"}])
def test_latest_failed_query_cancels_empty_scan_advice(failure):
    g = ledger()
    position(g)
    sense(g)
    sense(g, change=failure)
    assert not g.empty_query_checks()
    assert "advice" not in advice(g)
    assert advice(g)["last_query"][0]["state"] == "failed"
    sense(g)
    assert g.empty_query_checks()[0]["retained_count"] == 1


def test_transport_unknown_and_pending_query_allow_retry():
    g = ledger()
    position(g)
    sense(g)
    pending = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
    assert not g.empty_query_checks()
    g.end_tool(pending, error="response lost")
    assert not g.empty_query_checks()
    sense(g)
    assert g.empty_query_checks()[0]["retained_count"] == 1


@pytest.mark.parametrize("args", [{"drone_id": "d1", "detail": "full"},
                                  {"drone_id": "d1", "detail": None},
                                  {"drone_id": "d1", "radius": 20},
                                  {"drone_id": "d1", "purpose": "obstacle-check"}])
def test_nondefault_query_never_suppressed_as_same_purpose(args):
    g = ledger()
    position(g)
    sense(g)
    g.observe_tool("get_nearby_entities", args, {"targets": []})
    assert not g.empty_query_checks()
    assert "advice" not in advice(g)


def test_explicit_default_detail_is_same_query_but_collective_sense_is_not():
    g = ledger()
    position(g)
    sense(g)
    g.observe_tool("get_nearby_entities", {"drone_id": "d1", "detail": "summary"}, {"targets": []})
    assert g.empty_query_checks()[0]["retained_count"] == 2
    g.observe_tool("sense_nearby_entities", {"drone_ids": ["d1"]}, {"per_drone": {"d1": {"targets": []}}})
    assert not g.empty_query_checks()


def test_action_even_rejected_or_same_point_invalidates_and_new_query_restarts_count():
    g = ledger()
    position(g)
    sense(g)
    action = g.begin_tool("move_to", {"drone_id": "d1", "x": 0, "y": 0, "z": 5})
    assert not g.empty_query_checks()
    g.end_tool(action, {"status": "error", "drone_id": "d1"})
    position(g)
    assert not g.empty_query_checks()
    sense(g)
    assert g.empty_query_checks()[0]["retained_count"] == 1


def test_changed_public_position_requires_new_query_even_without_movement_receipt():
    g = ledger()
    position(g)
    sense(g)
    position(g, xyz=(1, 0, 5))
    assert not g.empty_query_checks()
    sense(g)
    assert g.empty_query_checks()[0]["position"] == [1, 0, 5]
    assert g.empty_query_checks()[0]["retained_count"] == 1


def test_same_position_status_read_preserves_query_evidence():
    g = ledger()
    position(g)
    sense(g)
    position(g)
    assert g.empty_query_checks()[0]["retained_count"] == 1


def test_earlier_command_empty_query_not_reused_for_advice():
    index = SessionEvidenceIndexMemory("scene")
    old = ledger(command_id="old", index=index)
    position(old)
    sense(old)
    new = ledger(command_id="new", index=index)
    position(new)
    assert not new.empty_query_checks()
    assert "advice" not in advice(new)
    assert len(new.snapshot()["execution_memory"]["observation_attempts"]) == 1


def test_other_actor_does_not_supply_current_actor_checked_here_claim():
    g = ledger("Drone 2 visit Fixed Target 10.")
    position(g, actor="d1")
    position(g, actor="d2")
    sense(g, actor="d1")
    assert not g.empty_query_checks("d2")
    assert "advice" not in advice(g, "d2")
    assert not g.empty_query_checks("d1")  # No guessed scout intention.


@pytest.mark.parametrize("command", ["Drone 1 visit Moving Target 10.",
                                     "Drone 1 take a photo of Fixed Target 10.",
                                     "If possible, Drone 1 visit Fixed Target 10.", ROUTE])
def test_dynamic_or_unsupported_purpose_has_no_static_search_advice(command):
    g = ledger(command)
    position(g)
    sense(g)
    assert not g.empty_query_checks()
    assert "advice" not in advice(g)


def test_known_target_or_actor_visit_removes_search_advice():
    g = ledger()
    position(g)
    sense(g)
    g.observe_tool("get_target_info", {"target_id": "t10"},
                   {"id": "t10", "name": "Fixed Target 10", "position": {"x": 8, "y": 0, "z": 0}})
    assert not g.empty_query_checks()
    assert advice(g)["goals"][0]["knowledge"] == "localized"


def test_overlapped_same_actor_query_not_treated_as_reusable_empty():
    g = ledger()
    position(g)
    one = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
    two = g.begin_tool("get_drone_status", {"drone_id": "d1"})
    g.end_tool(one, {"targets": []})
    g.end_tool(two, {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}})
    assert not g.empty_query_checks()


def test_epoch_reset_removes_advice_and_keeps_source_task_in_audit():
    g = ledger()
    position(g)
    sense(g)
    g.index.clear()
    assert not g.empty_query_checks()
    assert "advice" not in advice(g)
    assert advice(g)["scene_changed"] is True
    assert g.snapshot()["execution_memory"]["full_handoff"]["original_command"] == g.command


def test_notes_cannot_create_or_clear_real_empty_query_evidence():
    g = ledger()
    position(g)
    g.observe_tool("update_blackboard_notes", {"note": "looked here twice; target absent"}, {"success": True})
    assert not g.empty_query_checks()
    sense(g)
    g.observe_tool("update_blackboard_notes", {"note": "all done"}, {"success": True})
    assert g.empty_query_checks()[0]["retained_count"] == 1
    assert advice(g)["goals"][0]["visit"] == "unattempted"


def test_guidance_ablation_changes_advice_only_with_same_derived_records():
    left, right = ledger(), ledger(guidance_enabled=False)
    for g in (left, right):
        position(g)
        sense(g)
        sense(g)
    assert left.empty_query_checks() == right.empty_query_checks()
    a, b = advice(left), advice(right)
    assert a.pop("advice")
    assert a == b
    assert left.projection() == right.projection()


def test_route_handoff_retains_gap_and_completed_coordinates_do_not_assert_landing():
    g = ledger(ROUTE)
    path(g, [[10, 20, 5]], partial=True)
    view = g.handoff({"plan_id": "p", "status": "completed", "cursor": 99, "done": True})
    assert view["routes"][0]["next"] == {"ordinal": 2, "point": [40.0, 50.0, 5.0]}
    assert view["routes"][0]["ordered"] == 1
    assert "actual_effects" not in view
    path(g, [[40, 50, 5], [70, 80, 5]])
    completed = g.handoff()["routes"][0]
    assert completed["ordered"] == completed["total"] == 3
    assert "next" not in completed and "remaining" not in completed
    assert completed["unchecked"] == "landing_and_other_clauses"
    assert g.handoff()["mission_completion"] == "not_evaluated"
    assert "done" not in view["plan"]


def test_cross_actor_contact_clue_kept_with_no_transferred_completion():
    index = SessionEvidenceIndexMemory("scene")
    old = ledger(command_id="old", index=index)
    position(old)
    old.observe_tool("move_to", {"drone_id": "d1", "x": 200, "y": 0, "z": 5},
        {"command": "move_to", "drone_id": "d1", "status": "success",
         "message": "Drone moved to position (200, 0, 5) - Reached target(s): Fixed Target 10"})
    new = ledger("Drone 2 visit Fixed Target 10.", "new", index)
    position(new, actor="d2", xyz=(200, 0, 5))
    sense(new, actor="d2")
    sense(new, actor="d2")
    value = advice(new, "d2")
    assert value["goals"][0]["clue"]["by"] == "d1"
    assert value["goals"][0]["clue"]["points"] == [[0, 0, 5], [200, 0, 5]]
    assert value["goals"][0]["clue"]["not_target_center"] is True
    assert value["goals"][0]["visit"] == "unattempted"
    assert value["checked_here"][0]["actor"] == "d2"
    assert len(encode(value)) <= 1400


def test_long_history_compacts_repeats_and_keeps_original_full_audit():
    g = ledger()
    position(g)
    for _ in range(80):
        sense(g)
    value = advice(g)
    assert len(encode(value)) <= 1400
    assert len(value["checked_here"]) == 1 and value["checked_here"][0]["retained_count"] == 64
    assert value["goals"][0]["target"] == "Fixed Target 10"
    snapshot = g.snapshot()
    assert len(snapshot["execution_memory"]["observation_attempts"]) == 64
    assert snapshot["execution_memory"]["observation_attempts_omitted"] == 16
    assert snapshot["execution_memory"]["full_handoff"]["original_command"] == g.command


@pytest.mark.parametrize("budget", [0, 1, 2, 100, 500, 1000, 3900, 9000])
def test_compact_view_hard_limit_and_omissions(budget):
    command = " ".join(f"Drone 1 visit Fixed Target {i}." for i in range(40))
    g = ledger(command, max_summary_chars=budget)
    position(g)
    sense(g)
    result = g.summary()
    assert len(result) <= min(budget, 3900)
    if budget >= 1000:
        view = json.loads(result)
        assert view["omitted"]["goals"] > 0
    assert g.snapshot()["execution_memory"]["full_handoff"]["original_command"] == command


def test_v1_full_records_and_semantics_unchanged_for_same_events():
    left = ExecutionMemoryLedger("Drone 1 visit Fixed Target 10.", "current")
    right = ExecutionMemoryLedgerV2("Drone 1 visit Fixed Target 10.", "current")
    for g in (left, right):
        g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}])
        position(g)
        sense(g)
    old, new = left.snapshot(), right.snapshot()
    assert old["events"] == new["events"]
    assert old["execution_memory"]["observation_attempts"] == new["execution_memory"]["observation_attempts"]
    assert old["execution_memory"]["full_handoff"] == new["execution_memory"]["full_handoff"]
    assert left.projection() == right.projection()


@pytest.mark.parametrize("failure", [{"status": "error"}, {"success": False}, {"error": "stale read"}])
def test_failed_status_refresh_with_same_position_does_not_reuse_empty_scan(failure):
    g = ledger()
    position(g)
    sense(g)
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, **failure})
    assert not g.empty_query_checks()
    assert "advice" not in advice(g)


def test_public_sensor_radius_change_starts_a_new_empty_query_run():
    g = ledger()
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": 20})
    sense(g)
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": 40})
    assert not g.empty_query_checks()
    sense(g)
    assert g.empty_query_checks()[0]["retained_count"] == 1


def test_newer_list_state_at_changed_position_cancels_old_empty_location():
    g = ledger()
    position(g)
    sense(g)
    g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1", "position": {"x": 10, "y": 0, "z": 5}}])
    assert not g.empty_query_checks()


def test_static_task_on_other_actor_does_not_advise_dynamic_target_actor():
    g = ledger("Drone 1 visit Moving Target 10. Drone 2 visit Fixed Target 11.")
    position(g)
    sense(g)
    assert not g.empty_query_checks("d1")
    assert "advice" not in advice(g, "d1")
    position(g, actor="d2")
    sense(g, actor="d2")
    view = advice(g, "d2")
    assert "advice" in view
    assert view["checked_here"][0]["applicable_goals"] == [{"id": "ref-2", "target": "Fixed Target 11"}]


@pytest.mark.parametrize("extra", ["Drone 1 visit Moving Target 11.", "Drone 1 take a photo of Fixed Target 11."])
def test_mixed_purpose_actor_gets_query_record_without_generic_search_advice(extra):
    g = ledger("Drone 1 visit Fixed Target 10. " + extra)
    position(g)
    sense(g)
    view = advice(g)
    assert view["checked_here"][0]["retained_count"] == 1
    assert "advice" not in view


def test_route_focus_uses_bound_projection_actor():
    command = ROUTE + " " + ROUTE.replace("Drone 1", "Drone 2")
    g = ledger(command)
    view = g.decision_context(actor_id="d2", decision_kind="handoff")
    assert view["routes"][0]["actor"] == "Drone 2"


def test_old_query_event_window_does_not_claim_immediate_reuse():
    g = ledger()
    position(g)
    sense(g)
    for _ in range(17):
        g.observe_tool("get_target_info", {"target_id": "other"}, {"error": "not found"})
    assert not g.empty_query_checks()


def test_guidance_ablation_does_not_change_budgeted_evidence_selection():
    command = " ".join(f"Drone 1 visit Fixed Target {i}." for i in range(30))
    left = ledger(command)
    right = ledger(command, guidance_enabled=False)
    for g in (left, right):
        position(g)
        sense(g)
    a, b = advice(left), advice(right)
    assert a.pop("advice")
    assert a == b
    assert a["omitted"]["goals"] > 0
