"""Public execution state preserves uncertainty and never controls tools."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_execution_state import build_task_execution_state, concise_recovery_projection


COMMAND = "Drone 1 visits Fixed Target 1, takes a photo, then hovers briefly."


def event(payload=None, *, tool="move_to", status="completed", evidence_id="e1", **kwargs):
    return {
        "evidence_id": evidence_id, "tool_call_id": f"call-{evidence_id}", "tool_name": tool,
        "args": {"drone_id": "d1", "x": 10, "y": 20, "z": 15}, "payload": payload,
        "scope": "action_receipt" if tool == "move_to" else "observation",
        "status": status, "start_sequence": 1, "end_sequence": 2,
        "ordering": "certain", "overlaps": [], "source_clause_ids": ["c1"], **kwargs,
    }


def frame(status="paused", **kwargs):
    return {"plan_id": "task:p1", "task_id": "task", "scene_id": "scene", "scene_epoch": 2,
            "skill": "simple_chain", "status": status, "reason": "every_step_checkpoint", "cursor": 1,
            "source_obligations": [COMMAND], "actor_bindings": {"Drone 1": "d1"},
            "steps": [{"tool": "take_off"}, {"tool": "rotate"}, {"tool": "hover"}],
            "evidence_refs": ["e1"], "state": {}, **kwargs}


def project(events=(), frames=(), *, ledger_extra=None, harness_events=(), lifecycle=None):
    ledger = {"command": COMMAND, "command_id": "task", "events": list(events), **(ledger_extra or {})}
    harness = {"task_id": "task", "frames": list(frames), "events": list(harness_events)}
    return build_task_execution_state(COMMAND, "task", ledger, harness, lifecycle)


def test_empty_state_retains_original_unsupported_obligations_without_completion():
    state = build_task_execution_state(COMMAND, "task")
    assert state["command"] == state["obligations"]["unparsed_obligations"]["source_text"] == COMMAND
    assert state["obligations"]["interpretation_complete"] is False
    assert state["mission_completion"] == "not_evaluated"
    assert state["actions"] == state["frames"] == state["tool_calls"] == []
    assert state["recovery"]["opportunities"] == {
        "current_candidates": 0, "resumable_frames": 0, "recovery_success_rate": None,
        "rate_status": "no_current_opportunity", "scope": "current_snapshot_not_historical_recovery_denominator",
    }
    json.dumps(state, allow_nan=False)


def test_partial_knowledge_and_source_clauses_do_not_erase_other_requirements():
    clause = {"id": "c1", "text": COMMAND, "requirement_status": "not_evaluated", "start": 0, "end": len(COMMAND)}
    requirement = {"id": "ref-1", "relation": "visit", "execution_state": "evidenced",
                   "execution_scope": "current_command_actor_visit_only; other_clauses_not_evaluated"}
    aggregate = {"threshold_state": "observed_threshold_met", "freshness": "observed"}
    state = project(ledger_extra={"clauses": [clause], "task_evidence": {"requirements": [requirement], "aggregate_requirements": [aggregate]}})
    assert state["obligations"]["requirements"] == [requirement]
    assert state["obligations"]["aggregate_requirements"] == [aggregate]
    assert state["obligations"]["clauses"] == [clause]
    assert state["obligations"]["unparsed_obligations"]["status"] == "not_fully_parsed"
    assert state["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("binding_state", ["exact", "ambiguous", "unresolved"])
def test_entity_binding_and_stale_or_inherited_facts_keep_their_scope(binding_state):
    bindings = {"Drone 1": {"state": binding_state, "entities": []}}
    entities = {"target:t1": {"facts": {"is_reached": {"value": True, "validity": "inherited", "scope": "session_history_not_current_position_or_current_task"}}}}
    state = project(ledger_extra={"entity_bindings": bindings, "entities": entities})
    assert state["entity_bindings"] == bindings and state["entities"] == entities
    assert state["actions"] == [] and state["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("payload,status,expected", [
    ({"status": "success"}, "completed", "success"),
    ({"status": "partial_success"}, "partial", "partial"),
    ({"status": "error", "message": "Insufficient battery"}, "error", "failure"),
    ({"status": "error", "message": "Path blocked by obstacle"}, "blocked", "failure"),
    ({"tool_exception": "ReadTimeout"}, "unknown", "unknown"),
    ({"status": "success", "success": False}, "unknown", "unknown"),
    ({"outcome": "completed"}, "completed", "unknown"),
    ({}, "completed", "unknown"),
    ("Error moving drone and sensing: sensor unavailable", "unknown", "unknown"),
])
def test_action_effect_status_is_not_tool_return_or_derived_status(payload, status, expected):
    state = project([event(payload, status=status)])
    action = state["actions"][0]
    assert action["effect_status"] == expected
    assert action["requested"] == {"drone_id": "d1", "x": 10, "y": 20, "z": 15}
    assert action["applied"] is action["remaining"] is None
    assert action["completion_scope"] == "this_call_only_not_task_completion"
    assert state["mission_completion"] == "not_evaluated"


def test_partial_waypoints_preserve_measured_prefix_and_unfinished_points():
    payload = {"move_result": {"status": "partial_success", "successful_points_count": 1,
                               "successful_points": [[1, 2, 3]], "unsuccessful_points_count": 2,
                               "unsuccessful_points": [[4, 5, 6], [7, 8, 9]]}, "nearby": {"targets": []}}
    state = project([event(payload, tool="move_along_path_and_sense", status="partial")])
    action = state["actions"][0]
    assert action["effect_status"] == "partial"
    assert action["applied"] == {"successful_points_count": 1, "successful_points": [[1, 2, 3]]}
    assert action["remaining"] == {"unsuccessful_points_count": 2, "unsuccessful_points": [[4, 5, 6], [7, 8, 9]]}
    assert action["source"]["receipt_path"] == ["move_result"]
    recovery = state["recovery"]["items"][0]
    assert recovery["category"] == "partial_completion" and recovery["automatic_retry"] is False
    assert recovery["recommendation"] == "preserve_confirmed_effects_and_plan_only_remaining_work"


@pytest.mark.parametrize("payload,status,effect", [
    ({"outcome": "completed", "arrival_verified": True}, "completed", "success"),
    ({"outcome": "partial", "arrival_verified": False, "distance_remaining": 20}, "partial", "partial"),
    ({"success": False, "outcome": "blocked", "arrival_verified": False}, "blocked", "failure"),
    ({"outcome": "completed", "arrival_verified": False}, "completed", "unknown"),
    ({"outcome": "completed", "arrival_verified": "true"}, "completed", "unknown"),
    ({"outcome": "completed", "within_distance": True, "success": False}, "completed", "unknown"),
    ({"outcome": "completed", "within_distance": True, "status": "error"}, "completed", "unknown"),
    ({"outcome": "partial", "arrival_verified": False, "status": "error"}, "partial", "unknown"),
    ({"outcome": "completed", "arrival_verified": True, "success": "true"}, "completed", "unknown"),
    ({"outcome": "partial", "arrival_verified": True}, "partial", "unknown"),
])
def test_client_navigation_contract_keeps_verified_and_partial_distinct(payload, status, effect):
    state = project([event(payload, tool="navigate_to_and_sense", status=status)])
    assert state["actions"][0]["effect_status"] == effect


def test_failed_action_can_report_position_without_claiming_no_side_effects():
    payload = {"status": "error", "message": "Emergency landing executed", "final_position": {"x": 1, "y": 2, "z": 0}}
    action = project([event(payload, status="error")])["actions"][0]
    assert action["effect_status"] == "failure"
    assert action["applied"] == {"final_position": payload["final_position"]}
    assert action["remaining"] is None


def test_wrong_actor_receipt_cannot_be_applied_to_requested_actor():
    payload = {"status": "success", "drone_id": "d2", "final_position": {"x": 10, "y": 20, "z": 15}}
    action = project([event(payload)])["actions"][0]
    assert action["effect_status"] == "unknown" and action["effect_reason"] == "wrong_actor_receipt"
    assert action["applied"] is None
    assert action["receipt"] == payload  # Evidence remains auditable.


def test_later_current_position_does_not_retroactively_resolve_unknown_effect():
    events = [event({"tool_exception": "timeout after dispatch"}, status="unknown"),
              event({"id": "d1", "position": {"x": 10, "y": 20, "z": 15}},
                    tool="get_drone_status", status="observed", evidence_id="e2", start_sequence=3, end_sequence=4)]
    state = project(events, [frame("needs_replan", state={"unknown_effect_observation": events[1]["payload"]})])
    assert state["actions"][0]["effect_status"] == "unknown"
    assert state["actions"][0]["applied"] is state["actions"][0]["remaining"] is None
    assert state["tool_calls"][0]["effect_status"] == "success"
    issue = state["recovery"]["items"][0]
    assert issue["historical_effect_resolved"] is False
    assert issue["recommendation"] == "review_public_followup_without_certifying_historical_effect_do_not_replay"
    assert issue["eligible_now"] is False and issue["issue_state"] == "historical_with_followup"
    assert issue["followup_evidence"][0]["evidence_id"] == "e2"
    assert issue["followup_evidence"][0]["kind"] == "public_state_observation_not_historical_proof"


@pytest.mark.parametrize("status,payload", [("error", {"status": "error"}), ("partial", {"status": "partial_success"}), ("unknown", {"tool_exception": "TimeoutError"})])
def test_explicit_later_same_request_success_suppresses_historical_repair_advice(status, payload):
    events = [event(payload, status=status), event({"status": "success"}, evidence_id="e2", start_sequence=3, end_sequence=4)]
    state = project(events)
    earlier = state["actions"][0]["effect_status"]
    assert earlier == {"error": "failure", "partial": "partial", "unknown": "unknown"}[status]
    issue = state["recovery"]["items"][0]
    assert issue["eligible_now"] is False and issue["historical_effect_resolved"] is False
    assert issue["issue_state"] == "historical_with_followup"
    assert issue["followup_evidence"][0]["kind"] == "same_request_success"
    assert state["recovery"]["opportunities"]["current_candidates"] == 0
    assert state["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("changes", [
    {"args": {"drone_id": "d2", "x": 10, "y": 20, "z": 15}},
    {"args": {"drone_id": "d1", "x": 99, "y": 20, "z": 15}},
    {"tool_name": "navigate_to"}, {"ordering": "uncertain"}, {"overlaps": ["e1"]},
    {"start_sequence": 1},
])
def test_different_or_unordered_success_is_not_a_recovery_followup(changes):
    followup = event({"status": "success"}, evidence_id="e2", start_sequence=3, end_sequence=4)
    followup.update(changes)
    state = project([event({"status": "error"}, status="error"), followup])
    issue = state["recovery"]["items"][0]
    assert issue["followup_evidence"] == [] and issue["eligible_now"] is True


def test_concise_current_issue_precedes_historical_successful_followup():
    state = project([event({"status": "error"}, status="error"),
                     event({"status": "success"}, evidence_id="e2", start_sequence=3, end_sequence=4),
                     event({"tool_exception": "TimeoutError"}, status="unknown", tool="hover", evidence_id="e3", start_sequence=5, end_sequence=6)])
    prompt = concise_recovery_projection(state, 1)
    assert prompt["items"][0]["evidence_id"] == "e3"
    assert prompt["items"][0]["eligible_now"] is True
    assert prompt["omitted"] == 1


@pytest.mark.parametrize("payload", [
    "Error invoking tool 'sense_nearby_entities': 1 validation error for SenseNearbyEntitiesInput\ninput_json\n  Field required [type=missing]",
    {"error_type": "ValidationError", "message": "input_json Field required"},
    {"tool_exception": "1 validation error for TakeOffInput\ninput_json\n Field required"},
])
def test_ui_validation_error_is_failure_even_when_ui_reports_tool_returned(payload):
    state = project([event(payload, tool="sense_nearby_entities", status="unknown", args={})])
    record = state["tool_calls"][0]
    assert record["effect_status"] == "failure" and record["effect_reason"] == "tool_validation_error"
    assert record["applied"] is record["remaining"] is None
    advice = state["recovery"]["items"][0]
    assert advice["category"] == "tool_validation_failure"
    assert advice["recommendation"] == "correct_arguments_then_decide_new_call"
    assert advice["automatic_retry"] is False


def test_pending_action_does_not_offer_duplicate_retry_or_count_as_recovery():
    state = project([event(None, status="pending", end_sequence=None)])
    advice = state["recovery"]["items"][0]
    assert advice["category"] == "in_flight"
    assert advice["recommendation"] == "wait_for_existing_call_do_not_duplicate"
    assert advice["eligible_now"] is False
    assert state["recovery"]["opportunities"]["current_candidates"] == 0


def test_harness_source_is_joined_by_explicit_evidence_or_call_not_guessed_id():
    call = event({"status": "success"}, tool_call_id="harness:task:p1:4")
    harness_event = {"kind": "public_call_start", "sequence": 15, "plan_id": "task:p1", "evidence_id": "e1", "call_id": call["tool_call_id"]}
    state = project([call], [frame()], harness_events=[harness_event])
    source = state["actions"][0]["source"]
    assert source == {"evidence_id": "e1", "tool_call_id": "harness:task:p1:4", "plan_id": "task:p1",
                      "receipt_path": [], "harness_event_sequence": 15, "origin": "harness_public_call"}
    no_link = project([call])["actions"][0]["source"]
    assert no_link["plan_id"] is None


@pytest.mark.parametrize("status", ["ready", "paused"])
def test_ready_and_paused_frames_offer_existing_plan_resume_with_guards(status):
    state = project(frames=[frame(status)])
    item = state["frames"][0]
    assert item["cursor"] == 1 and item["terminal"] is False and item["resumable"] is True
    advice = state["recovery"]["items"][0]
    assert advice["recommendation"] == "resume_existing_plan_id_subject_to_runtime_guards"
    assert state["recovery"]["opportunities"]["resumable_frames"] == 1
    assert state["recovery"]["opportunities"]["recovery_success_rate"] is None


@pytest.mark.parametrize("status", ["completed", "cancelled", "needs_replan", "interrupted", "abandoned", "failed"])
def test_terminal_frame_cannot_resume_or_imply_mission_completion(status):
    state = project(frames=[frame(status)])
    assert state["frames"][0]["terminal"] is True and state["frames"][0]["resumable"] is False
    assert state["recovery"]["items"][0]["resume_allowed"] is False
    assert state["recovery"]["items"][0]["category"] == "terminal_plan"
    assert state["mission_completion"] == state["frames"][0]["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("status", ["running", "future_status"])
def test_running_or_unrecognized_frame_is_not_resumable(status):
    state = project(frames=[frame(status)])
    assert state["frames"][0]["resumable"] is False
    assert state["frames"][0]["terminal"] == (False if status == "running" else None)


@pytest.mark.parametrize("reason", ["scene_scope_changed", "external_action_changed_dependencies", "actor_state_changed_between_units", "shared_endpoint_static_obstacle_contract_changed"])
def test_explicit_dependency_change_prevents_resume_even_if_frame_says_paused(reason):
    state = project(frames=[frame(reason=reason)])
    assert state["frames"][0]["resumable"] is False
    assert state["frames"][0]["dependency_status"] == "invalidated"
    assert state["recovery"]["items"][0]["category"] == "dependency_invalidated"


def test_invalidated_event_preserves_the_source_call_and_plan_scope():
    invalidation = {"kind": "plan_invalidated", "plan_id": "task:p1", "sequence": 8,
                    "reason": "external_action_changed_dependencies", "tool_call_id": "external-2"}
    state = project(frames=[frame()], harness_events=[invalidation])
    assert state["frames"][0]["invalidation_evidence"] == [{"sequence": 8, "reason": invalidation["reason"], "tool_call_id": "external-2"}]
    assert state["frames"][0]["resumable"] is False


@pytest.mark.parametrize("lifecycle", [
    {"closed": True}, {"cancelled": True}, {"deadline_reached": True},
    {"stop_reason": "token_limit"}, {"status": "completed"}, {"status": "timeout"},
    {"closed": True, "stop_reason": None, "cancelled": False},
])
def test_global_lifecycle_stop_dominates_resume_and_raw_action_recovery(lifecycle):
    state = project([event({"status": "partial_success"}, status="partial")], [frame()], lifecycle=lifecycle)
    assert state["lifecycle"]["further_actions_allowed"] is False
    assert state["frames"][0]["resumable"] is False
    assert all(item["recommendation"] == "stop_no_further_actions" and not item["eligible_now"] for item in state["recovery"]["items"])
    assert state["recovery"]["opportunities"]["current_candidates"] == 0
    assert state["recovery"]["opportunities"]["recovery_success_rate"] is None
    assert state["actions"][0]["effect_status"] == "partial"
    assert state["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("kind,reason", [("task_interrupt", "deadline"), ("task_interrupt", "cancelled"), ("task_end", "normal_return")])
def test_runtime_terminal_event_stops_even_without_explicit_lifecycle(kind, reason):
    state = project(frames=[frame()], harness_events=[{"kind": kind, "reason": reason}])
    assert state["lifecycle"]["stopped"] is True and state["frames"][0]["resumable"] is False
    assert reason in state["lifecycle"]["stop_reasons"]


def test_projection_is_detached_and_does_not_consume_hidden_root_fields():
    ledger = {"command": COMMAND, "command_id": "task", "events": [event({"status": "success"})],
              "checker": {"hidden_target_position": [999, 999, 999]}, "world": {"private_score": 100}}
    harness = {"task_id": "task", "frames": [frame()], "events": [], "hidden_checker": "forbidden-canary"}
    before = copy.deepcopy((ledger, harness))
    state = build_task_execution_state(COMMAND, "task", ledger, harness)
    assert (ledger, harness) == before
    encoded = json.dumps(state)
    assert "hidden_target_position" not in encoded and "private_score" not in encoded and "forbidden-canary" not in encoded
    state["actions"][0]["requested"]["x"] = -100
    state["frames"][0]["actor_bindings"]["Drone 1"] = "other"
    assert (ledger, harness) == before


@pytest.mark.parametrize("ledger,harness", [
    ({"command": "different"}, {}), ({"command_id": "other"}, {}),
    ({}, {"task_id": "other"}), ({}, {"frames": [frame(task_id="other")]}),
])
def test_wrong_task_snapshots_are_rejected(ledger, harness):
    with pytest.raises(ValueError, match="does not match"):
        build_task_execution_state(COMMAND, "task", ledger, harness)


def test_non_json_inputs_and_nonfinite_numbers_are_rejected():
    with pytest.raises(TypeError):
        build_task_execution_state(COMMAND, "task", object())
    with pytest.raises(ValueError):
        build_task_execution_state(COMMAND, "task", {"events": [event({"distance_remaining": float("nan")})]})


def test_concise_prompt_has_bounded_issues_and_no_full_receipts_or_requests():
    events = [event({"status": "error", "message": "large receipt " * 1000}, status="error", evidence_id=f"e{i}") for i in range(10)]
    state = project(events, [frame()])
    concise = concise_recovery_projection(state, max_items=3)
    assert len(concise["items"]) == 3 and concise["omitted"] == 8
    assert concise["items"][0]["plan_id"] == "task:p1"
    encoded = json.dumps(concise)
    assert "large receipt" not in encoded and "requested" not in encoded and COMMAND not in encoded
    assert len(encoded) < 2200
    assert concise["mission_completion"] == "not_evaluated" and concise["automatic_retry"] is False


def test_zero_prompt_items_keeps_stop_and_omission_information():
    state = project(frames=[frame()], lifecycle={"cancelled": True})
    concise = concise_recovery_projection(state, 0)
    assert concise["items"] == [] and concise["omitted"] == 1 and concise["global_stop"] is True
    assert concise["stop_reasons"] == ["cancelled"]


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_prompt_limit_validation(limit):
    with pytest.raises(ValueError):
        concise_recovery_projection(project(), limit)


def test_module_import_is_standard_library_only_and_needs_no_model_framework():
    directory = str(Path(__file__).resolve().parents[1])
    code = "import sys; sys.path.insert(0, sys.argv[1]); import task_execution_state; assert 'langchain' not in sys.modules; assert 'requests' not in sys.modules"
    subprocess.run([sys.executable, "-S", "-c", code, directory], check=True)


def test_actual_r2_ledger_and_runtime_snapshots_match_projection_contract():
    from harness_core import Checkpoint, DecisionFrame, HarnessRuntime, PublicCall
    from task_evidence_r2 import TaskEvidenceLedgerR2

    ledger = TaskEvidenceLedgerR2(COMMAND, "task")

    def skill_factory(skill, plan_id, runtime):
        current = DecisionFrame(plan_id, "task", "scene", 0, skill, [COMMAND], [], [], [], "none", "one command", 500)

        def generate():
            yield PublicCall("hover", {"drone_id": "d1"}, True)
            current.cursor = 1
            yield Checkpoint()

        return current, generate()

    runtime = HarnessRuntime(task_id="task", command=COMMAND, scene_id="scene", scene_epoch=0, ledger=ledger,
                             invoke=lambda call: {"status": "success"}, skill_factory=skill_factory,
                             scope=lambda: ("scene", 0), started=0, clock=lambda: 1, scheduler="every_step")
    runtime.control("start", skill="simple_chain")
    state = build_task_execution_state(COMMAND, "task", ledger.snapshot(), runtime.snapshot())
    assert state["actions"][0]["tool_name"] == "hover"
    assert state["actions"][0]["source"]["plan_id"] == "task:p1"
    assert state["frames"][0]["cursor"] == 1 and state["frames"][0]["resumable"] is True
    assert state["mission_completion"] == "not_evaluated"
    runtime.close("worker_closed")
    closed = build_task_execution_state(COMMAND, "task", ledger.snapshot(), runtime.snapshot())
    assert closed["lifecycle"]["stopped"] is True and closed["frames"][0]["resumable"] is False
