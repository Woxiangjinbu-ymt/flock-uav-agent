"""Observed changes, planned actions and receipt validity stay distinct."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution_memory_v2 import ExecutionMemoryLedgerV2
from execution_memory_v3 import ExecutionMemoryLedgerV3
from execution_memory_v4 import (ExecutionMemoryLedgerV4, QUERY_DECISION_CONTRACT,
                                 audit_query_decision, parse_query_decision,
                                 query_decision_context_for)
from test_execution_memory import ROUTE, position, sense, path


def ledger(kind=ExecutionMemoryLedgerV4, command="Drone 1 visit Fixed Target 10.", **kwargs):
    g = kind(command, "current", **kwargs)
    g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}, {"id": "d2", "name": "Drone 2"}])
    return g


def eligible(g):
    position(g)
    sense(g)
    sense(g)
    g.set_decision_context(actor_id="d1", decision_kind="locate")
    return g


def declaration(kind="none", evidence=None, plan="change_viewpoint", actor="d1", **change):
    return {"actor": actor,
            "observed_change": {"kind": kind, "evidence": evidence or [],
                                "summary": "No public change has been observed.", **change},
            "planned_action": {"kind": plan, "reason": "Use a different feasible point along the prior path.", "evidence": []}}


def tag(value=None):
    return "<query_decision>" + json.dumps(declaration() if value is None else value) + "</query_decision>"


def query(actor="d1", id="q"):
    return {"name": "get_nearby_entities", "args": {"drone_id": actor}, "id": id}


def move(g, xyz=(10, 0, 5), actor="d1"):
    return g.observe_tool("move_to", {"drone_id": actor, **dict(zip("xyz", xyz))},
        {"command": "move_to", "drone_id": actor, "status": "success",
         "message": f"Drone moved to position ({xyz[0]}, {xyz[1]}, {xyz[2]})"})


def audit(g, value=None, calls=None, context=None):
    return audit_query_decision(context or query_decision_context_for(g), tag(value), calls or [query()])


def support(row):
    return row["declarations"][0]["observed_change_support"]


def test_conditional_contract_preserves_v2_facts_without_extra_events():
    a, b = eligible(ledger(ExecutionMemoryLedgerV2)), eligible(ledger())
    old = copy.deepcopy(b._events)
    original = a.decision_context(**a._memory_focus)
    revised = b.decision_context(**b._memory_focus)
    assert revised.pop("query_decision_contract") == QUERY_DECISION_CONTRACT
    assert revised == original
    assert b._events == old
    assert len(b.summary()) <= 3900
    assert "LAST SUCCESSFUL" in QUERY_DECISION_CONTRACT
    assert "never position_changed" in QUERY_DECISION_CONTRACT


@pytest.mark.parametrize("activity", ["route", "complete", "failed"])
def test_control_views_remain_byte_identical_to_v2(activity):
    command = ROUTE if activity != "failed" else "Drone 1 visit Fixed Target 10."
    a, b = ledger(ExecutionMemoryLedgerV2, command), ledger(command=command)
    for g in (a, b):
        if activity == "complete":
            path(g, [[10, 20, 5], [40, 50, 5], [70, 80, 5]])
        elif activity == "failed":
            position(g)
            sense(g, change={"error": "read failed"})
    assert a.summary() == b.summary()


def test_actual_contract_eligibility_reflects_v3_and_v4_different_lengths():
    assert len(QUERY_DECISION_CONTRACT) > 0
    from execution_memory_v3 import QUERY_DECISION_CONTRACT as V3_CONTRACT
    # Different versions are checked by their real views, not a guessed length.
    for cap in (1100, 1800, 2000, 3900):
        for cls in (ExecutionMemoryLedgerV2, ExecutionMemoryLedgerV3, ExecutionMemoryLedgerV4):
            g = eligible(ledger(cls, max_summary_chars=cap))
            actual = "query_decision_contract" in g.decision_context(**g._memory_focus)
            capture = query_decision_context_for(g)
            assert capture["contract_active"] is actual
            if cls is ExecutionMemoryLedgerV2:
                assert not capture["contract_omitted_for_budget"]
            if not actual:
                base = ExecutionMemoryLedgerV2.decision_context(g, **g._memory_focus)
                assert g.decision_context(**g._memory_focus) == base


def test_planned_move_does_not_become_an_observed_change():
    g = eligible(ledger())
    capture = query_decision_context_for(g)
    calls = [{"name": "move_to", "args": {"drone_id": "d1", "x": 10, "y": 0, "z": 5}, "id": "m"}, query()]
    correct = audit(g, declaration(), calls, capture)
    assert support(correct)["status"] == "supported"
    assert correct["declarations"][0]["planned_action"]["scope"] == "intent_not_execution"
    false_change = audit(g, declaration("position_changed", [g._events[-1]["evidence_id"]]), calls, capture)
    assert support(false_change)["status"] == "unsupported"
    assert support(false_change)["reason"] == "cited_events_are_not_after_the_last_successful_query"
    assert false_change["sensing_calls"][0]["execution_policy"] == "allowed_unchanged"


def test_actual_post_baseline_movement_supports_position_change():
    g = eligible(ledger())
    eid = move(g)
    row = audit(g, declaration("position_changed", [eid]))
    assert support(row)["status"] == "supported"
    assert support(row)["supporting_events"] == [eid]
    assert support(audit(g))["status"] == "unsupported"


def test_earlier_move_is_not_change_after_new_successful_query():
    g = ledger()
    position(g)
    eid = move(g)
    sense(g)
    assert support(audit(g, declaration("position_changed", [eid])))["status"] == "unsupported"
    assert support(audit(g))["status"] == "supported"


def test_other_actor_movement_cannot_support_declared_actor_change():
    g = eligible(ledger())
    eid = move(g, actor="d2")
    row = audit(g, declaration("position_changed", [eid]))
    assert support(row)["status"] == "unsupported"
    assert support(row)["reason"] == "cited_actor_does_not_match_the_declared_actor"


def test_same_position_successful_status_is_not_position_change():
    g = eligible(ledger())
    eid = position(g)
    row = audit(g, declaration("position_changed", [eid]))
    assert support(row)["status"] == "unsupported"
    assert support(row)["reason"] == "cited_valid_position_does_not_differ_from_baseline"
    assert support(audit(g))["status"] == "supported"


def test_changed_valid_status_position_is_real_public_change():
    g = eligible(ledger())
    eid = position(g, xyz=(4, 0, 5))
    assert support(audit(g, declaration("position_changed", [eid])))["status"] == "supported"


@pytest.mark.parametrize("failure", [{"error": "failed"}, {"success": False}, {"status": "error"}])
def test_failed_status_reply_with_position_does_not_prove_movement(failure):
    g = eligible(ledger())
    eid = g.observe_tool("get_drone_status", {"drone_id": "d1"},
                         {"id": "d1", "position": {"x": 20, "y": 0, "z": 5}, **failure})
    assert support(audit(g, declaration("position_changed", [eid])))["status"] != "supported"


def test_latest_failed_query_has_own_evidence_and_does_not_reuse_old_empty():
    g = eligible(ledger())
    baseline_id = g._events[-1]["evidence_id"]
    failed_id = sense(g, change={"error": "sensor unavailable"})
    capture = query_decision_context_for(g)
    assert capture["baseline_successful_query"]["d1"]["evidence_id"] == baseline_id
    assert capture["latest_query_outcome"]["d1"]["evidence_id"] == failed_id
    assert capture["latest_query_outcome"]["d1"]["state"] == "failed"
    assert not capture["contract_active"]
    assert not g.empty_query_checks()
    assert support(audit(g, declaration("sensing_failed", [failed_id]), context=capture))["status"] == "supported"
    assert support(audit(g, context=capture))["status"] == "unsupported"


def test_failure_can_be_grounded_without_any_successful_query_baseline():
    g = ledger()
    position(g)
    eid = sense(g, change={"error": "read failed"})
    capture = query_decision_context_for(g)
    assert capture["baseline_successful_query"] == {}
    assert support(audit(g, declaration("sensing_failed", [eid]), context=capture))["status"] == "supported"
    assert support(audit(g, context=capture))["status"] == "unverified"


def test_unknown_query_and_uncertain_action_never_prove_no_change():
    g = eligible(ledger())
    eid = g.observe_tool("get_nearby_entities", {"drone_id": "d1"}, {"unrecognized": "reply"})
    assert support(audit(g, declaration("sensing_failed", [eid])))["status"] == "unverified"
    assert support(audit(g))["status"] == "unverified"
    g = eligible(ledger())
    pending = g.begin_tool("move_to", {"drone_id": "d1", "x": 10, "y": 0, "z": 5})
    assert support(audit(g))["status"] == "unverified"
    g.end_tool(pending, error="outcome unknown")
    assert support(audit(g))["status"] == "unverified"


def test_real_sensing_radius_change_and_position_both_supported():
    g = ledger()
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": 20})
    sense(g)
    eid = g.observe_tool("get_drone_status", {"drone_id": "d1"},
                         {"id": "d1", "position": {"x": 10, "y": 0, "z": 5}, "perceived_radius": 40})
    assert support(audit(g, declaration("conditions_changed", [eid])))["status"] == "supported"
    assert support(audit(g, declaration("position_changed", [eid])))["status"] == "supported"


def test_unknown_previous_radius_is_not_fabricated_for_condition_comparison():
    g = eligible(ledger())
    eid = g.observe_tool("get_drone_status", {"drone_id": "d1"},
                         {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": 40})
    result = support(audit(g, declaration("conditions_changed", [eid])))
    assert result["status"] == "unverified"


def test_free_text_is_never_verified_even_with_structured_change_support():
    g = eligible(ledger())
    eid = move(g)
    row = audit(g, declaration("position_changed", [eid], summary="The entire task is complete and the target moved."))
    assert support(row)["status"] == "supported"
    assert row["declarations"][0]["free_text_verification"] == "not_verified"
    assert "free_text_not_verified" in support(row)["scope"]
    assert g.projection()[0]["knowledge_state"] == "unknown"


def test_late_event_and_scene_reset_cannot_backfill_pre_model_support():
    g = eligible(ledger())
    capture = query_decision_context_for(g)
    old = copy.deepcopy(capture)
    eid = move(g)
    assert support(audit(g, declaration("position_changed", [eid]), context=capture))["status"] == "unverified"
    assert capture == old
    g.index.clear()
    assert support(audit(g, declaration("position_changed", [eid])))["status"] == "unverified"


def test_model_reply_changes_only_audit_not_memory_tools_or_prompt():
    g = eligible(ledger())
    capture = g.query_decision_context()
    original = (copy.deepcopy(g._events), g.summary(), g.projection(), g.empty_query_checks())
    calls = [query()]
    copied_calls = copy.deepcopy(calls)
    row = g.record_model_response(tag(), calls, context=capture)
    assert calls == copied_calls
    assert (g._events, g.summary(), g.projection(), g.empty_query_checks()) == original
    assert row["parse_status"] == "valid"
    assert g.snapshot()["query_decisions"] == [row]
    sense(g)
    assert g.empty_query_checks()[0]["retained_count"] == 3


@pytest.mark.parametrize("content", ["Refreshing", "<query_decision>{bad}</query_decision>",
                                     tag({"actor": "d1"}), "<query_decision>{}"])
def test_missing_or_invalid_declaration_never_blocks_or_falsely_scores_correct(content):
    g = eligible(ledger())
    row = g.record_model_response(content, [query()], context=g.query_decision_context())
    assert row["parse_status"] in {"missing", "invalid"}
    assert row["declarations"] == []
    assert row["sensing_calls"][0]["execution_policy"] == "allowed_unchanged"
    sense(g)
    assert g.empty_query_checks()[0]["retained_count"] == 3


def test_legacy_v3_claim_is_distinct_and_gets_same_semantic_window_check():
    g = eligible(ledger(ExecutionMemoryLedgerV3))
    eid = g._events[-1]["evidence_id"]
    value = {"actor": "d1", "decision": "change_viewpoint", "reason": "position_changed",
             "evidence": [eid], "summary": "I will move to another point."}
    row = audit_query_decision(query_decision_context_for(g), tag(value), [query()])
    assert row["declaration_format"] == "v3_legacy"
    assert support(row)["status"] == "unsupported"
    assert row["declarations"][0]["planned_action"]["scope"] == "intent_not_execution"


def test_planned_purpose_is_not_observed_change_and_is_not_semantically_certified():
    g = eligible(ledger())
    value = declaration()
    value["planned_action"]["reason"] = "Read for a new purpose."
    row = audit(g, value)
    assert support(row)["status"] == "supported"
    assert row["declarations"][0]["planned_action"]["scope"] == "intent_not_execution"
    value["observed_change"]["kind"] = "purpose_changed"
    assert parse_query_decision(tag(value))["status"] == "invalid"


def test_multiple_query_and_order_audit_remains_intent_only():
    g = eligible(ledger())
    calls = [query(id="first"), {"name": "move_to", "args": {"drone_id": "d1"}, "id": "move"}, query(id="last")]
    row = audit(g, calls=calls)
    assert row["sensing_calls"][0]["movement_requested_before_query"] is False
    assert row["sensing_calls"][1]["movement_requested_before_query"] is True
    assert all(r["declaration_binding"] == "multiple_queries_same_actor_not_individually_justified" for r in row["sensing_calls"])
    assert support(row)["status"] == "supported"  # No real movement has happened yet.


def test_no_capture_never_certifies_a_change():
    g = eligible(ledger())
    row = g.record_model_response(tag(), [query()])
    assert support(row)["status"] == "unverified"


def test_array_and_optional_planned_evidence_parse_without_interpreting_notes():
    first, second = declaration(), declaration(actor="d2")
    first["planned_action"].pop("evidence")
    parsed = parse_query_decision(tag([first, second]))
    assert parsed["status"] == "valid"
    assert parsed["format"] == "split_v4"


def test_same_actor_concurrent_position_read_is_not_grounded_change():
    g = eligible(ledger())
    pos = g.begin_tool("get_drone_status", {"drone_id": "d1"})
    sensing = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
    g.end_tool(pos, {"id": "d1", "position": {"x": 10, "y": 0, "z": 5}})
    g.end_tool(sensing, {"targets": []})
    assert support(audit(g, declaration("position_changed", [pos])))["status"] != "supported"


def test_uncertain_failed_query_cannot_certify_no_change():
    g = eligible(ledger())
    sensing = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
    status = g.begin_tool("get_drone_status", {"drone_id": "d1"})
    g.end_tool(sensing, {"error": "sensor failure"})
    g.end_tool(status, {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": 30})
    assert support(audit(g))["status"] == "unverified"
    assert support(audit(g, declaration("sensing_failed", [sensing])))["status"] == "unverified"


def test_baseline_radius_is_known_before_query_not_from_overlapping_list():
    g = ledger()
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": 30})
    sensing = g.begin_tool("get_nearby_entities", {"drone_id": "d1"})
    listing = g.begin_tool("list_drones", {})
    g.end_tool(listing, [{"id": "d1", "perceived_radius": 60}])
    g.end_tool(sensing, {"targets": []})
    capture = query_decision_context_for(g)
    assert capture["baseline_successful_query"]["d1"]["sensing_signature"]["perceived_radius"] == 30
    assert support(audit(g, declaration("conditions_changed", [listing]), context=capture))["status"] != "supported"


@pytest.mark.parametrize("radius", ["30", True, float("nan"), float("inf")])
def test_invalid_baseline_radius_cannot_support_conditions_change(radius):
    g = ledger()
    g.observe_tool("get_drone_status", {"drone_id": "d1"},
                   {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": radius})
    sense(g)
    status = g.observe_tool("get_drone_status", {"drone_id": "d1"},
                            {"id": "d1", "position": {"x": 0, "y": 0, "z": 5}, "perceived_radius": 30})
    assert support(audit(g, declaration("conditions_changed", [status])))["status"] == "unverified"


@pytest.mark.parametrize("length,status", [(240, "valid"), (241, "valid"), (277, "valid"),
                                          (325, "valid"), (1024, "valid"), (1025, "invalid")])
def test_planned_reason_has_its_own_audit_length_limit(length, status):
    value = declaration()
    value["planned_action"]["reason"] = "x" * length
    parsed = parse_query_decision(tag(value))
    assert parsed["status"] == status
    assert parsed["format"] == "split_v4"
    assert parsed["declarations"] == ([value] if status == "valid" else [])


@pytest.mark.parametrize("reason", ["", None, 12, {}, []])
def test_planned_reason_still_requires_a_nonempty_string(reason):
    value = declaration()
    value["planned_action"]["reason"] = reason
    assert parse_query_decision(tag(value))["status"] == "invalid"


@pytest.mark.parametrize("length,status", [(240, "valid"), (241, "invalid")])
def test_observed_summary_limit_is_not_relaxed_with_plan_reason(length, status):
    value = declaration(summary="x" * length)
    value["planned_action"]["reason"] = "y" * 1024
    assert parse_query_decision(tag(value))["status"] == status


@pytest.mark.parametrize("length,status", [(4096, "valid"), (4097, "invalid")])
def test_full_declaration_limit_is_unchanged_for_long_plan_reason(length, status):
    value = declaration()
    value["planned_action"]["reason"] = "x" * 1024
    body = json.dumps(value).ljust(length)
    assert len(body) == length
    parsed = parse_query_decision("<query_decision>" + body + "</query_decision>")
    assert parsed["status"] == status
    if status == "invalid":
        assert parsed["error"] == "declaration_too_long"


@pytest.mark.parametrize("length,status", [(32000, "valid"), (32001, "invalid")])
def test_full_response_limit_is_unchanged_for_long_plan_reason(length, status):
    value = declaration()
    value["planned_action"]["reason"] = "x" * 1024
    parsed = parse_query_decision(tag(value).ljust(length))
    assert parsed["status"] == status
    if status == "invalid":
        assert parsed["error"] == "response_too_long"


def test_long_planned_reason_never_certifies_an_observed_move_or_changes_memory():
    g = eligible(ledger())
    capture = g.query_decision_context()
    original = (copy.deepcopy(g._events), g.summary(), g.projection(), g.empty_query_checks())
    value = declaration()
    value["planned_action"]["reason"] = ("I plan to move to another viewpoint. " * 30)[:1024]
    calls = [{"name": "move_to", "args": {"drone_id": "d1", "x": 10, "y": 0, "z": 5}, "id": "m"}, query()]
    saved_calls = copy.deepcopy(calls)
    row = g.record_model_response(tag(value), calls, context=capture)
    assert row["parse_status"] == "valid"
    assert row["declarations"][0]["observed_change"]["kind"] == "none"
    assert support(row)["status"] == "supported"
    assert row["declarations"][0]["planned_action"]["scope"] == "intent_not_execution"
    assert row["declarations"][0]["free_text_verification"] == "not_verified"
    assert calls == saved_calls
    assert (g._events, g.summary(), g.projection(), g.empty_query_checks()) == original
    value["observed_change"].update(kind="position_changed", evidence=[g._events[-1]["evidence_id"]])
    assert support(audit(g, value, calls, capture))["status"] == "unsupported"


@pytest.mark.parametrize("length,status", [(240, "valid"), (241, "invalid")])
def test_legacy_v3_summary_limit_is_unchanged(length, status):
    value = {"actor": "d1", "decision": "change_viewpoint", "reason": "none",
             "evidence": [], "summary": "x" * length}
    parsed = parse_query_decision(tag(value))
    assert parsed["format"] == "v3_legacy"
    assert parsed["status"] == status


# Exact content from the sealed v4 study's clue-horizontal-r1-v4 and
# clue-vertical-r1-v4 response-1-original.json. Keep tests independent of
# experiment-file availability; these are post-run audit regressions only.
@pytest.mark.parametrize("content,length", [
    ('<query_decision>{"actor":"drone-2","observed_change":{"kind":"none","evidence":["e15"],'
     '"summary":"No public change since drone-2\'s last scan; drone-1 remains at (200,0,20) and no targets were sensed."},'
     '"planned_action":{"kind":"change_viewpoint","reason":"The prior scan at (200,0,20) returned no targets, '
     'and drone-1\'s executed segment (0,0,20)->(200,0,20) is a witnessed path, not the target center. '
     'Moving drone-2 to a different feasible point along that path (e.g., (100,0,20)) gives a new sensing viewpoint '
     'to search for Fixed Target 1 rather than repeating an identical scan.","evidence":["e7","e15"]}}</query_decision>', 325),
    ('<query_decision>{"actor":"drone-2","observed_change":{"kind":"none","evidence":["e15"],'
     '"summary":"No public change since drone-2\'s last scan; drone-1 still hovering at (0,200,20) and no targets sensed."},'
     '"planned_action":{"kind":"change_viewpoint","reason":"The prior scan at (0,200,20) returned no targets; '
     'drone-1\'s executed segment (0,0,20)->(0,200,20) is a witnessed path, not the target center, '
     'so I will move drone-2 to a different feasible point along that path to search for Fixed Target 1 '
     'rather than repeat an identical scan.","evidence":["e7","e15"]}}</query_decision>', 277),
])
def test_sealed_real_replies_accept_long_plan_reason_without_certifying_it(content, length):
    parsed = parse_query_decision(content)
    assert parsed["status"] == "valid"
    assert parsed["format"] == "split_v4"
    value = parsed["declarations"][0]
    assert len(value["planned_action"]["reason"]) == length
    assert value["observed_change"]["kind"] == "none"
    assert len(value["observed_change"]["summary"]) <= 240
    # Parsing a saved reply alone cannot recreate missing pre-model facts.
    row = audit_query_decision({"capture_missing": True}, content, [])
    assert support(row)["status"] == "unverified"
    assert row["declarations"][0]["planned_action"]["scope"] == "intent_not_execution"
    assert row["declarations"][0]["free_text_verification"] == "not_verified"
