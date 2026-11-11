"""Receipt/decision-boundary semantics; no model, network, or simulator calls."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution_memory import ExecutionMemoryLedger, SessionEvidenceIndexMemory
from task_evidence import encode
from task_evidence_r2 import TaskEvidenceLedgerR2


ROUTE = ("Drone 1 route: fly to and start at (10, 20, 5), then waypoints "
         "(40, 50, 5), then end (70, 80, 5) and land.")


def ledger(command=ROUTE, command_id="now", index=None, **kwargs):
    g = ExecutionMemoryLedger(command, command_id, index=index, **kwargs)
    g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"},
                                     {"id": "d2", "name": "Drone 2"}])
    return g


def position(g, actor="d1", xyz=(0, 0, 5)):
    return g.observe_tool("get_drone_status", {"drone_id": actor},
                          {"id": actor, "position": dict(zip("xyz", xyz))})


def sense(g, targets=(), actor="d1", change=None):
    value = {"targets": list(targets), "obstacles": [], "drones": []}
    value.update(change or {})
    return g.observe_tool("get_nearby_entities", {"drone_id": actor}, value, "sense-call")


def path(g, points, partial=False, actor="d1"):
    return g.observe_tool("move_along_path", {"drone_id": actor,
        "waypoints": [[10, 20, 5], [40, 50, 5], [70, 80, 5]]},
        {"command": "move_along_path", "drone_id": actor,
         "status": "partial_success" if partial else "success",
         "successful_points": points, "successful_points_count": len(points)}, "path-call")


def attempts(g):
    return g.snapshot()["execution_memory"]["observation_attempts"]


def test_empty_failed_and_unobserved_are_distinct_with_actual_position():
    g = ledger("Drone 1 visit Fixed Target 10.")
    pos = position(g)
    empty = sense(g)
    failed = sense(g, change={"error": "sensor unavailable"})
    unknown = g.observe_tool("get_nearby_entities", {"drone_id": "d1"},
                             {"blackboard": {"targets": []}})
    records = attempts(g)
    assert [r["state"] for r in records] == ["empty_targets", "failed", "unknown"]
    assert records[0]["position"] == {"point": [0, 0, 5], "evidence_id": pos}
    assert [r["evidence_id"] for r in records] == [empty, failed, unknown]
    assert "coverage_extent_unknown" in records[0]["scope"]
    assert g.projection()[0]["knowledge_state"] == "unknown"
    assert g.projection()[0]["execution_state"] == "unattempted"
    assert "global_absence" in records[0]["scope"]


def test_query_intent_requires_real_observation_and_never_changes_visit():
    g = ledger("Drone 1 visit Fixed Target 10.")
    eid = sense(g)
    assert g.decision_context(decision_kind="locate")["observations"][0]["associated_obligations"] == []
    g.note_localization_attempt(eid, "ref-1")
    g.note_localization_attempt(eid, "ref-1")
    assert len(g.snapshot()["execution_memory"]["localization_links"]) == 1
    assert g.decision_context(decision_kind="locate")["observations"][0]["associated_obligations"] == ["ref-1"]
    assert g.projection()[0]["execution_state"] == "unattempted"
    with pytest.raises(ValueError):
        g.note_localization_attempt("invented", "ref-1")
    with pytest.raises(ValueError):
        g.note_localization_attempt(eid, "invented")


def test_motion_invalidates_local_observation_without_erasing_history():
    g = ledger()
    position(g)
    sense(g)
    pending = g.begin_tool("move_to", {"drone_id": "d1", "x": 9, "y": 8, "z": 5})
    assert g.decision_context(decision_kind="locate")["observations"][0]["freshness"] == "stale_after_action"
    g.end_tool(pending, error="transport failed after submission")
    sense(g)
    assert attempts(g)[0]["position"]["point"] == [0, 0, 5]
    assert attempts(g)[1]["position"] is None
    assert attempts(g)[1]["state"] == "empty_targets"


def test_failed_movement_and_successful_sensing_are_separate():
    g = ledger()
    position(g)
    g.observe_tool("navigate_to_and_sense", {"drone_id": "d1", "x": 10, "y": 20, "z": 5},
                   {"drone_id": "d1", "navigation_result": {"drone_id": "d1", "outcome": "blocked",
                    "success": False, "arrival_verified": False}, "nearby": {"targets": []}})
    record = attempts(g)[0]
    assert record["state"] == "empty_targets" and record["position"] is None
    assert record["receipt_path"] == ["nearby"]
    assert g.route_projection()[0]["ordered_prefix"] == 0


def test_concurrent_actor_motion_keeps_observation_order_uncertain():
    g = ledger()
    position(g)
    query = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
    move = g.begin_tool("move_to", {"drone_id": "d1", "x": 10, "y": 20, "z": 5})
    g.end_tool(query, {"targets": []})
    g.end_tool(move, {"command": "move_to", "drone_id": "d1", "status": "success",
                      "message": "Drone moved to position (10, 20, 5)"})
    assert attempts(g)[0]["ordering"] == "uncertain"
    assert attempts(g)[0]["position"] is None
    assert g.decision_context(decision_kind="locate")["observations"][0]["freshness"] == "order_unknown"


def test_multi_drone_sensing_is_attributed_per_real_reply():
    g = ledger()
    g.observe_tool("sense_nearby_entities", {"drone_ids": ["d1", "d2"]}, {"per_drone": {
        "d1": {"targets": []}, "d2": {"targets": [{"id": "t10", "name": "Fixed Target 10"}]}}})
    assert [(r["observer_id"], r["state"]) for r in attempts(g)] == [("d1", "empty_targets"), ("d2", "targets_observed")]
    assert g.decision_context(actor_id="d2", decision_kind="locate")["observations"][0]["targets"][0]["id"] == "t10"


def test_handoff_preserves_partial_effect_and_gap_not_plan_done():
    g = ledger()
    eid = path(g, [[10, 20, 5]], partial=True)
    value = g.handoff({"plan_id": "p1", "status": "completed", "cursor": 999,
                       "state": {"done": True}, "steps": [[70, 80, 5]]})
    assert value["original_command"] == ROUTE
    assert value["plan"]["cursor"] == 999 and "state" not in value["plan"]
    assert value["mission_completion"] == "not_evaluated"
    assert value["routes"][0]["ordered_prefix"] == 1
    assert [p["point"] for p in value["routes"][0]["remaining_original_goals"]] == [[40.0, 50.0, 5.0], [70.0, 80.0, 5.0]]
    assert value["actual_effects"][0]["evidence_id"] == eid
    assert value["actual_effects"][0]["actual_point"] == [10, 20, 5]
    assert value["unparsed_or_unverified_clauses"][0]["status"] == "not_fully_evaluated"
    assert "landing" in value["routes"][0]["scope"]
    # Replanning metadata cannot rewrite source obligations or fill a gap.
    g.set_decision_context(decision_kind="replan", plan={"plan_id": "p2", "cursor": 0})
    assert json.loads(g.summary())["routes"][0]["next_unconfirmed"]["ordinal"] == 2
    assert g.command == ROUTE


def test_out_of_order_arrival_and_wrong_actor_do_not_close_route():
    g = ledger()
    path(g, [[10, 20, 5], [70, 80, 5]])
    path(g, [[40, 50, 5]], actor="d2")
    view = g.handoff()["routes"][0]
    assert view["ordered_prefix"] == 1
    assert view["later_observed_ordinals"] == [3]
    assert view["next_unconfirmed"]["point"] == [40.0, 50.0, 5.0]


def test_same_scene_history_is_available_but_completion_does_not_transfer():
    index = SessionEvidenceIndexMemory("scene")
    first = ledger("Drone 1 visit Fixed Target 10.", "before", index)
    position(first)
    first.observe_tool("move_to", {"drone_id": "d1", "x": 10, "y": 0, "z": 5},
        {"command": "move_to", "drone_id": "d1", "status": "success",
         "message": "Drone moved to position (10, 0, 5) - Reached target(s): Fixed Target 10"})
    sense(first)
    assert first.projection()[0]["execution_state"] == "evidenced"
    second = ledger("Drone 2 visit Fixed Target 10.", "after", index)
    view = second.projection()[0]
    assert view["knowledge_state"] == "reported_only"
    assert view["execution_state"] == "unattempted"
    assert view["report"]["command_id"] == "before"
    context = second.decision_context(decision_kind="locate")
    assert context["observations"][0]["freshness"] == "historical_other_command"
    assert context["observations"][0]["command_id"] == "before"
    assert context["actual_effects"] == []


def test_scene_reset_rejects_late_sensing_and_drops_all_old_views():
    index = SessionEvidenceIndexMemory("scene")
    g = ledger(index=index)
    sense(g)
    pending = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
    index.clear()
    g.end_tool(pending, {"targets": [{"id": "t10", "name": "Fixed Target 10"}]})
    assert index.observation_attempts == [] and index.observation_attempt_count == 0
    assert attempts(g) == []
    context = g.handoff()
    assert context["scene_valid"] is False
    assert context["observations"] == context["routes"] == context["actual_effects"] == []
    assert context["original_command"] == ROUTE


def test_bounded_storage_summary_and_source_omissions_are_explicit():
    g = ledger("Drone 1 visit Fixed Target 10. " + "Keep the original task. " * 200,
               max_summary_chars=2200)
    for _ in range(70):
        sense(g)
    snapshot = g.snapshot()["execution_memory"]
    assert len(snapshot["observation_attempts"]) == 64
    assert snapshot["observation_attempts_omitted"] == 6
    context = g.decision_context(decision_kind="locate")
    assert len(encode(context)) <= 2200
    assert context["original_command_omitted_chars"] > 0
    assert context["omitted"]["observations"] > 0
    assert context["omitted"]["unparsed_or_unverified_clauses"] > 0
    assert snapshot["full_handoff"]["original_command"] == g.command
    assert snapshot["full_handoff"]["unparsed_or_unverified_clauses"][-1]["text"].strip() == "Keep the original task."


@pytest.mark.parametrize("cap", [0, 1, 2, 50, 200, 1000, 3900])
def test_summary_respects_even_tiny_character_budgets(cap):
    g = ledger(max_summary_chars=cap)
    sense(g)
    assert len(g.summary()) <= cap
    assert g.snapshot()["execution_memory"]["full_handoff"]["original_command"] == ROUTE


def test_no_observation_or_intent_is_invented_from_model_notes():
    g = ledger("Drone 1 visit Fixed Target 10.")
    g.observe_tool("update_blackboard_notes", {"note": "FT10 absent, all done"},
                   {"blackboard": {"notes": [{"text": "FT10 absent"}]}})
    assert attempts(g) == []
    assert g.projection()[0]["knowledge_state"] == "unknown"
    assert g.projection()[0]["execution_state"] == "unattempted"


def test_parent_r2_evidence_unchanged_for_same_receipts():
    command = "Drone 1 visit Fixed Target 10."
    memory = ledger(command)
    parent = TaskEvidenceLedgerR2(command, "now")
    parent.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}, {"id": "d2", "name": "Drone 2"}])
    targets = [{"id": "t10", "name": "Fixed Target 10", "position": {"x": 8, "y": 9, "z": 0}}]
    for g in (memory, parent):
        position(g)
        sense(g, targets)
    assert memory.projection() == parent.projection()


def test_long_sensing_history_keeps_original_goal_and_some_observation():
    g = ledger("Drone 1 visit Fixed Target 10.", max_summary_chars=3900)
    position(g)
    for _ in range(80):
        sense(g)
    for decision in ("locate", "handoff", "continue"):
        value = g.decision_context(actor_id="d1", decision_kind=decision)
        assert value["obligations"][0]["target_name"] == "Fixed Target 10"
        assert value["observations"] and value["omitted"]["observations"] > 0
        assert len(encode(value)) <= 3900


def test_handoff_prioritizes_unfinished_actor_and_does_not_fill_with_path_history():
    command = " ".join(ROUTE.replace("Drone 1", f"Drone {i}") for i in range(1, 4))
    g = ledger(command, max_summary_chars=3900)
    g.observe_tool("list_drones", {}, [{"id": "d3", "name": "Drone 3"}])
    path(g, [[10, 20, 5], [40, 50, 5], [70, 80, 5]], actor="d1")
    path(g, [[10, 20, 5], [40, 50, 5], [70, 80, 5]], actor="d2")
    for _ in range(30):
        path(g, [[10, 20, 5]], actor="d3")
    sense(g, actor="d3")
    value = g.handoff()
    assert value["routes"][0]["required_actor"] == "Drone 3"
    assert value["routes"][0]["next_unconfirmed"]["ordinal"] == 2
    assert value["observations"]
    assert value["omitted"]["actual_effects"] > 0


def test_shared_contact_path_checks_remain_visible_for_other_required_actor():
    index = SessionEvidenceIndexMemory("scene")
    old = ledger("Drone 1 visit Fixed Target 10.", "old", index)
    position(old)
    old.observe_tool("move_to", {"drone_id": "d1", "x": 10, "y": 0, "z": 5},
        {"command": "move_to", "drone_id": "d1", "status": "success",
         "message": "Drone moved to position (10, 0, 5) - Reached target(s): Fixed Target 10"})
    eid = sense(old)
    g = ledger("Drone 2 visit Fixed Target 10.", "new", index)
    value = g.decision_context(actor_id="d2", decision_kind="locate")
    assert value["obligations"][0]["execution_state"] == "unattempted"
    assert value["obligations"][0]["contact_hint"]["actor_id"] == "d1"
    assert value["clue_checks"][0]["observer_id"] == "d1"
    assert value["clue_checks"][0]["check"] == {"command_id": "old", "evidence_id": eid, "receipt_path": []}
    assert value["observations"][0]["observer_id"] == "d1"
    assert "absence" in value["clue_check_scope"]


def test_clue_check_is_not_invented_for_failed_off_path_or_unknown_position():
    g = ledger("Drone 1 visit Fixed Target 10.")
    position(g)
    g.observe_tool("move_to", {"drone_id": "d1", "x": 10, "y": 0, "z": 5},
        {"command": "move_to", "drone_id": "d1", "status": "success",
         "message": "Drone moved to position (10, 0, 5) - Reached target(s): Fixed Target 10"})
    sense(g, change={"error": "unavailable"})
    position(g, xyz=(5, 10, 5))
    sense(g)
    sense(g, actor="d2")
    assert g.snapshot()["execution_memory"]["full_handoff"]["clue_checks"] == []
    # Valid query on the witnessed segment is a geometric association, not
    # an inferred intention or a claim that the whole segment was covered.
    position(g, xyz=(5, 0, 5))
    sense(g)
    checks = g.snapshot()["execution_memory"]["full_handoff"]["clue_checks"]
    assert len(checks) == 1 and checks[0]["position"]["point"] == [5, 0, 5]
    assert "intent_and_coverage_not_inferred" in checks[0]["scope"]


def test_confirmed_discrete_points_do_not_certify_intervening_search_segment():
    hint = {"kind": "confirmed_waypoints_only", "points": [[0, 0, 5], [10, 0, 5]]}
    assert not ExecutionMemoryLedger._on_hint([5, 0, 5], hint)
    assert ExecutionMemoryLedger._on_hint([10, 0, 5], hint)
    assert not ExecutionMemoryLedger._on_hint([10, 0, 6], hint)


def test_wrong_observer_and_composite_exception_never_create_valid_queries():
    g = ledger()
    position(g)
    g.observe_tool("navigate_to_and_sense", {"drone_id": "d1"},
                   {"drone_id": "d2", "nearby": {"targets": []}})
    g.observe_tool("move_to_and_sense", {"drone_id": "d1"}, "Error: failed to sense")
    assert [r["state"] for r in attempts(g)] == ["unknown_observer", "failed"]
    assert all(r["position"] is None for r in attempts(g))


def test_relevant_target_discovered_by_other_actor_is_not_filtered_out():
    g = ledger("Drone 2 visit Fixed Target 10.")
    sense(g, actor="d1", targets=[{"id": "t10", "name": "Fixed Target 10",
                                    "position": {"x": 10, "y": 0, "z": 0}}])
    value = g.decision_context(actor_id="d2", decision_kind="locate")
    assert value["obligations"][0]["knowledge_state"] == "localized"
    assert value["obligations"][0]["execution_state"] == "unattempted"
    assert value["observations"][0]["observer_id"] == "d1"


@pytest.mark.parametrize("failure", [{"status": "error"}, {"status": "failed"},
                                     {"success": False}, {"tool_exception": "lost"}])
def test_explicit_sensor_failure_never_counts_as_valid_empty_observation(failure):
    g = ledger()
    sense(g, change=failure)
    assert attempts(g)[0]["state"] == "failed"
    assert attempts(g)[0]["targets"] == []
