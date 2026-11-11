"""Same-response query declarations are audited but never treated as evidence."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution_memory_v2 import ExecutionMemoryLedgerV2
from execution_memory_v3 import (ExecutionMemoryLedgerV3, QUERY_DECISION_CONTRACT,
                                 audit_query_decision, parse_query_decision,
                                 query_decision_context_for)
from task_evidence import encode
from test_execution_memory import ROUTE, position, sense, path


def ledger(kind=ExecutionMemoryLedgerV3, command="Drone 1 visit Fixed Target 10.", **kwargs):
    g = kind(command, "current", **kwargs)
    g.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}, {"id": "d2", "name": "Drone 2"}])
    return g


def eligible(g):
    position(g)
    sense(g)
    sense(g)
    g.set_decision_context(actor_id="d1", decision_kind="locate")
    return g


def declaration(actor="d1", decision="refresh", reason="none", evidence=None, **change):
    return {"actor": actor, "decision": decision, "reason": reason,
            "evidence": evidence or [], "summary": "No change was observed; another read is a choice.", **change}


def tag(row=None):
    return "<query_decision>" + json.dumps(declaration() if row is None else row) + "</query_decision>"


def query(actor="d1", tool_id="q1"):
    return {"name": "get_nearby_entities", "args": {"drone_id": actor}, "id": tool_id}


def test_v3_only_adds_contract_to_same_v2_evidence():
    control = eligible(ledger(ExecutionMemoryLedgerV2))
    treatment = eligible(ledger())
    before = len(treatment._events)
    c, t = control.decision_context(**control._memory_focus), treatment.decision_context(**treatment._memory_focus)
    assert t.pop("query_decision_contract") == QUERY_DECISION_CONTRACT
    assert c == t
    assert len(treatment._events) == before
    assert len(treatment.summary()) <= 3900
    assert "what was actually observed" in QUERY_DECISION_CONTRACT
    assert query_decision_context_for(control)["contract_eligible"] is True
    assert query_decision_context_for(control)["contract_active"] is False
    assert treatment.query_decision_context()["contract_active"] is True


@pytest.mark.parametrize("command,activity", [(ROUTE, "none"), (ROUTE, "complete"),
                                               ("Drone 1 visit Fixed Target 10.", "failed"),
                                               ("Drone 1 visit Moving Target 10.", "empty")])
def test_ineligible_views_are_byte_identical_to_v2(command, activity):
    control, treatment = ledger(ExecutionMemoryLedgerV2, command), ledger(command=command)
    for g in (control, treatment):
        if activity == "complete":
            path(g, [[10, 20, 5], [40, 50, 5], [70, 80, 5]])
        if activity in {"failed", "empty"}:
            position(g)
            sense(g, change={"error": "lost"} if activity == "failed" else None)
        g.set_decision_context(decision_kind="handoff")
    assert control.summary() == treatment.summary()
    assert not treatment.query_decision_context()["contract_active"]


def test_small_budget_omits_contract_without_dropping_or_changing_facts():
    control, treatment = eligible(ledger(ExecutionMemoryLedgerV2, max_summary_chars=1100)), eligible(ledger(max_summary_chars=1100))
    assert control.summary() == treatment.summary()
    capture = treatment.query_decision_context()
    assert capture["contract_eligible"] is True
    assert capture["contract_active"] is False
    assert capture["contract_omitted_for_budget"] is True
    row = audit_query_decision(capture, "Refresh", [query()])
    assert row["contract_omitted_for_budget"] is True


def test_valid_refresh_none_is_a_model_statement_not_new_evidence():
    g = eligible(ledger())
    capture = g.query_decision_context()
    before = (copy.deepcopy(g._events), g.summary(), g.projection(), g.empty_query_checks())
    row = g.record_model_response(tag(), [query()], context=capture)
    assert row["parse_status"] == "valid"
    assert row["declarations"][0]["grounding"] == "no_observed_change_declared"
    assert row["declarations"][0]["reason_truth"] == "not_verified_model_statement"
    assert row["sensing_calls"][0]["execution_policy"] == "allowed_unchanged"
    assert row["sensing_calls"][0]["contract_applies"] is True
    assert (g._events, g.summary(), g.projection(), g.empty_query_checks()) == before
    assert g.snapshot()["query_decisions"] == [row]


@pytest.mark.parametrize("content", ["I will refresh.", "<query_decision>{bad}</query_decision>",
                                     "<query_decision>{}", tag({"actor": "d1"})])
def test_missing_or_malformed_annotation_never_blocks_actual_observation(content):
    g = eligible(ledger())
    row = g.record_model_response(content, [query()], context=g.query_decision_context())
    assert row["parse_status"] in {"missing", "invalid"}
    assert row["sensing_calls"][0]["execution_policy"] == "allowed_unchanged"
    prior = len(g._events)
    sense(g)
    assert len(g._events) == prior + 1
    assert g.empty_query_checks()[0]["retained_count"] == 3


def test_known_reference_existence_is_not_claimed_reason_validation():
    g = eligible(ledger())
    capture = g.query_decision_context()
    eid = g._events[-1]["evidence_id"]
    row = g.record_model_response(tag(declaration(reason="position_changed", evidence=[eid])), [query()], context=capture)
    fact = row["declarations"][0]
    assert fact["references"][0]["known_before_model"] is True
    assert fact["grounding"] == "references_exist_meaning_not_verified"
    assert fact["reason_truth"] == "not_verified_model_statement"
    # The cited record is actually an empty scan; no position change is inferred.
    assert g.empty_query_checks()[0]["retained_count"] == 2


def test_late_evidence_never_backfills_a_pre_model_capture():
    g = eligible(ledger())
    capture = g.query_decision_context()
    old = copy.deepcopy(capture)
    later = sense(g)
    row = g.record_model_response(tag(declaration(reason="sensing_failed", evidence=[later])), [query()], context=capture)
    assert row["declarations"][0]["grounding"] == "unknown_reference"
    assert row["decision_sequence"] == old["sequence"]
    assert capture == old


def test_recording_without_capture_cannot_claim_references_were_pre_model():
    g = eligible(ledger())
    eid = g._events[-1]["evidence_id"]
    row = g.record_model_response(tag(declaration(reason="position_changed", evidence=[eid])), [query()])
    assert row["capture_status"] == "missing"
    assert row["declarations"][0]["grounding"] == "unknown_reference"


def test_actor_and_tool_call_binding_does_not_transfer_a_declaration():
    g = eligible(ledger())
    row = audit_query_decision(g.query_decision_context(), tag(), [query("d1", "first"), query("d2", "second")])
    first, second = row["sensing_calls"]
    assert first["tool_call_id"] == "first" and first["declaration_status"] == "present"
    assert second["tool_call_id"] == "second" and second["declaration_status"] == "missing_actor"
    assert second["contract_applies"] is False


def test_multiple_actors_one_tag_and_content_blocks_are_supported():
    content = [{"type": "text", "text": tag([declaration(), declaration("d2")])}]
    parsed = parse_query_decision(content)
    assert parsed["status"] == "valid"
    assert [r["actor"] for r in parsed["declarations"]] == ["d1", "d2"]


@pytest.mark.parametrize("value", [[], [declaration(), declaration()],
                                   declaration(decision="block"), declaration(reason="made_up"),
                                   declaration(evidence=[{}]), declaration(summary="x" * 241),
                                   declaration(actor=None), declaration(extra="ignored")])
def test_invalid_schema_is_recorded_without_accepting_partial_claims(value):
    result = parse_query_decision(tag(value))
    assert result["status"] == "invalid" and result["declarations"] == []


def test_ambiguous_multiple_tags_and_non_json_constants_are_rejected():
    assert parse_query_decision(tag() + tag())["status"] == "invalid"
    assert parse_query_decision('<query_decision>{"actor":NaN}</query_decision>')["status"] == "invalid"
    assert parse_query_decision("x" * 32001)["error"] == "response_too_long"


def test_no_tool_response_still_records_reuse_choice_when_context_active():
    g = eligible(ledger())
    row = g.record_model_response(tag(declaration(decision="reuse")), [], context=g.query_decision_context())
    assert row["contract_active"] is True and row["sensing_calls"] == []
    assert row["eligible_actors_without_query"] == ["d1"]
    assert row["declarations"][0]["decision"] == "reuse"


def test_reuse_claim_does_not_cancel_actual_query_or_change_duplicate_count():
    g = eligible(ledger())
    calls = [query()]
    original = copy.deepcopy(calls)
    row = g.record_model_response(tag(declaration(decision="reuse")), calls, context=g.query_decision_context())
    assert calls == original
    assert row["sensing_calls"][0]["decision_call_relation"] == "reuse_but_query_requested"
    sense(g)
    assert g.empty_query_checks()[0]["retained_count"] == 3


def test_viewpoint_change_audit_respects_order_but_never_calls_it_execution():
    g = eligible(ledger())
    move = {"name": "move_to", "args": {"drone_id": "d1", "x": 5, "y": 0, "z": 5}, "id": "m1"}
    content = tag(declaration(decision="change_viewpoint"))
    before = audit_query_decision(g.query_decision_context(), content, [query(), move])
    after = audit_query_decision(g.query_decision_context(), content, [move, query()])
    assert before["sensing_calls"][0]["movement_requested_before_query"] is False
    assert after["sensing_calls"][0]["movement_requested_before_query"] is True
    assert before["sensing_calls"][0]["call_ordinal"] == 0
    assert after["sensing_calls"][0]["call_ordinal"] == 1
    assert "request" in after["sensing_calls"][0]["decision_call_relation"]


def test_openai_function_envelope_and_input_json_are_auditable_without_mutation():
    g = eligible(ledger())
    calls = [{"id": "q", "type": "function", "function": {"name": "get_nearby_entities",
             "arguments": json.dumps({"input_json": json.dumps({"drone_id": "d1"})})}}]
    original = copy.deepcopy(calls)
    row = audit_query_decision(g.query_decision_context(), tag(), calls)
    assert row["sensing_calls"][0]["actor"] == "d1"
    assert calls == original


def test_guidance_disabled_also_means_no_v3_contract():
    g = eligible(ledger(guidance_enabled=False))
    assert "query_decision_contract" not in g.decision_context(**g._memory_focus)
    assert not g.query_decision_context()["contract_active"]


def test_contract_and_audit_never_promote_notes_or_plan_state():
    g = eligible(ledger())
    g.record_model_response(tag(declaration(reason="conditions_changed", evidence=["invented/e99"],
                                            summary="The target was removed; all complete.")), [query()],
                            context=g.query_decision_context())
    view = g.handoff({"plan_id": "p", "status": "completed", "cursor": 100})
    assert view["mission_completion"] == "not_evaluated"
    assert view["goals"][0]["knowledge"] == "unknown"
    assert view["goals"][0]["visit"] == "unattempted"
    assert "query_decisions" not in view


def test_snapshot_keeps_v1_receipts_and_original_instruction_intact():
    g = eligible(ledger())
    before = g.snapshot()["execution_memory"]["full_handoff"]
    g.record_model_response(tag(), [query()], context=g.query_decision_context())
    snapshot = g.snapshot()
    assert snapshot["execution_memory"]["full_handoff"] == before
    assert snapshot["revision"] == "execution-memory-v3"
    assert len(snapshot["query_decisions"]) == 1


def test_one_declaration_for_multiple_actor_queries_is_marked_not_individually_justified():
    g = eligible(ledger())
    row = g.record_model_response(tag(), [query(tool_id="one"), query(tool_id="two")],
                                   context=g.query_decision_context())
    for call in row["sensing_calls"]:
        assert call["actor_sensing_call_count"] == 2
        assert call["declaration_binding"] == "multiple_queries_same_actor_not_individually_justified"
        assert call["execution_policy"] == "allowed_unchanged"
