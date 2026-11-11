"""Separate observed changes from intended actions; audit has no control power.

Only structured change fields have narrow receipt-based checks. Free text and
planned actions remain model statements. V2 facts and tool execution are intact.
"""
from __future__ import annotations

import copy
import json
import math

from execution_memory_v2 import ExecutionMemoryLedgerV2
from execution_memory_v3 import (_TAG, _text_content, parse_query_decision as parse_v3,
    audit_query_decision as audit_v3, query_decision_context_for as context_v3)
from task_evidence import encode, point
from task_evidence_r3 import receipt_points
from task_progress import _ACTIONS

QUERY_DECISION_CONTRACT = (
    'Before repeating get_nearby_entities for checked_here, give in the SAME response: '
    '<query_decision>{"actor":"ID","observed_change":{"kind":"none|position_changed|'
    'sensing_failed|conditions_changed|uncertain","evidence":[],"summary":"one observed fact"},'
    '"planned_action":{"kind":"reuse|change_viewpoint|refresh","reason":"why this next step",'
    '"evidence":[]}}</query_decision>. Change baseline is the LAST SUCCESSFUL same-actor scan. '
    'observed_change describes only what ALREADY happened after that scan; a proposed move is '
    'planned_action, never position_changed. Cite existing eN (this command) or command/eN. '
    'If no public change was observed, use none and say so; this does not guarantee an unchanged world. '
    'Put a different information need/purpose in planned_action.reason. Prefer reuse/change_viewpoint '
    'when an identical refresh adds no information. Multiple actors may use one JSON array. No extra turn/tool.'
)
_CHANGES = {"none", "position_changed", "sensing_failed", "conditions_changed", "uncertain"}
_PLANS = {"reuse", "change_viewpoint", "refresh"}


def _contract_view(base, limit):
    if base.get("checked_here") and base.get("advice"):
        candidate = {**base, "query_decision_contract": QUERY_DECISION_CONTRACT}
        if len(encode(candidate)) <= min(3900, limit):
            return candidate
    return copy.deepcopy(base)


def query_decision_context_for(ledger):
    """Immutable pre-model public facts; these audit fields never enter a prompt."""
    with ledger._lock:
        result = context_v3(ledger)
        actual_view = ledger.decision_context(**ledger._memory_focus)
        can_show = "query_decision_contract" in actual_view
        has_contract_mode = bool(getattr(ledger, "query_decision_format", None) or
                                 getattr(ledger, "query_decision_contract_enabled", False))
        result["contract_active"] = can_show
        result["contract_omitted_for_budget"] = bool(result["contract_eligible"] and has_contract_mode and not can_show)
        result["contract_version"] = ("split_v4" if getattr(ledger, "query_decision_format", None) == "split_v4" else
                                      "v3_legacy" if getattr(ledger, "query_decision_contract_enabled", False) else "none")
        result["audit_format"] = "split_v4"
        valid = ledger.index_epoch == ledger.index.epoch
        events = copy.deepcopy(ledger._events) if valid else []
        observations = [copy.deepcopy(r) for r in ledger._observation_attempts if valid and
                        r["command_id"] == ledger.command_id and r["epoch"] == ledger.index_epoch]
        by_event = {e["evidence_id"]: e for e in events}
        baselines, latest = {}, {}
        for record in observations:
            actor = record["observer_id"]
            if not actor:
                continue
            latest[actor] = copy.deepcopy(record)
            event = by_event.get(record["evidence_id"])
            if (event and record["tool"] == "get_nearby_entities"
                    and ledger._default_query(event, actor)
                    and record["state"] in {"empty_targets", "targets_observed"}
                    and record["ordering"] == "certain" and event["status"] == "observed"
                    and not ledger._actor_conflict(event, actor)):
                baselines[actor] = {**copy.deepcopy(record),
                    "sensing_signature": ledger._sensing_signature(actor, event["start_sequence"])}
        result.update(public_events=events, observation_records=observations,
                      baseline_successful_query=baselines, latest_query_outcome=latest)
        return result


def _valid_refs(value):
    return isinstance(value, list) and len(value) <= 8 and all(isinstance(v, str) and 1 <= len(v) <= 256 for v in value)


def parse_query_decision(content):
    """Read V4 or mark a V3 declaration legacy; never reinterpret its prose."""
    text = _text_content(content)
    matches = _TAG.findall(text)
    if len(text) > 32000 or len(matches) != 1 or len(matches[0]) > 4096:
        old = parse_v3(content)
        return {**old, "format": "unrecognized"}
    try:
        value = json.loads(matches[0], parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (TypeError, ValueError):
        return {"status": "invalid", "error": "invalid_json", "declarations": [], "format": "unrecognized"}
    rows = [value] if isinstance(value, dict) else value
    if isinstance(rows, list) and rows and all(isinstance(row, dict) and "decision" in row for row in rows):
        old = parse_v3(content)
        normalized = [{"actor": row["actor"],
                       "observed_change": {"kind": row["reason"], "evidence": row["evidence"], "summary": row["summary"]},
                       "planned_action": {"kind": row["decision"], "reason": row["summary"], "evidence": []}}
                      for row in old["declarations"]]
        return {**old, "declarations": normalized, "format": "v3_legacy"}
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        return {"status": "invalid", "error": "invalid_declaration_list", "declarations": [], "format": "split_v4"}
    actors = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"actor", "observed_change", "planned_action"}:
            return {"status": "invalid", "error": "invalid_schema", "declarations": [], "format": "split_v4"}
        actor, change, plan = row["actor"], row["observed_change"], row["planned_action"]
        if (not isinstance(actor, str) or not 1 <= len(actor) <= 128 or actor in actors
                or not isinstance(change, dict) or set(change) != {"kind", "evidence", "summary"}
                or not isinstance(change["kind"], str) or change["kind"] not in _CHANGES
                or not _valid_refs(change["evidence"])
                or not isinstance(change["summary"], str) or not 1 <= len(change["summary"]) <= 240
                or not isinstance(plan, dict) or set(plan) not in ({"kind", "reason"}, {"kind", "reason", "evidence"})
                or not isinstance(plan["kind"], str) or plan["kind"] not in _PLANS
                or not isinstance(plan["reason"], str) or not 1 <= len(plan["reason"]) <= 1024
                or not _valid_refs(plan.get("evidence", []))):
            return {"status": "invalid", "error": "invalid_schema_or_duplicate_actor", "declarations": [], "format": "split_v4"}
        actors.add(actor)
    return {"status": "valid", "error": None, "declarations": copy.deepcopy(rows), "format": "split_v4"}


def _actor(event):
    return str(event.get("args", {}).get("drone_id", ""))


def _conflicting(event, actor, events):
    return any(other["evidence_id"] in event.get("overlaps", []) and _actor(other) == actor for other in events)


def _public_state(event, actor, events):
    """Only successful public identity/position reads and actual movement points."""
    if event.get("end_sequence") is None or _conflicting(event, actor, events):
        return {}
    payload, rows = event.get("payload"), []
    if event["tool_name"] == "get_drone_status" and _actor(event) == actor and event["status"] == "observed":
        rows = [payload]
    elif event["tool_name"] == "list_drones" and event["status"] == "observed" and isinstance(payload, list):
        rows = payload
    state = {}
    for row in rows:
        if ExecutionMemoryLedgerV2._failed(row) or str(row.get("id", "")) != actor:
            continue
        xyz = point(row.get("position"))
        if xyz is not None:
            state["position"] = xyz
        radius = row.get("perceived_radius")
        if not isinstance(radius, bool) and isinstance(radius, (int, float)) and math.isfinite(radius):
            state["perceived_radius"] = radius
    if _actor(event) == actor:
        points = receipt_points(event)
        if points:
            state["position"] = points[-1]["actual_point"]
    return state


def _support(status, reason, evidence=()):
    return {"status": status, "reason": reason, "supporting_events": list(evidence),
            "scope": "structured_change_only; free_text_not_verified"}


def _change_support(context, actor, change):
    if context.get("capture_missing") or not context.get("scene_valid", False):
        return _support("unverified", "missing_pre_model_capture_or_scene_changed")
    kind = change["kind"]
    if kind in {"uncertain", "purpose_changed"}:
        return _support("unverified", "uncertainty_or_intent_is_not_an_observed_state_change")
    command = str(context.get("command_id", ""))
    refs = [ref if "/" in ref else f"{command}/{ref}" for ref in change["evidence"]]
    known = set(context.get("known_evidence_refs", []))
    if any(ref not in known for ref in refs):
        return _support("unverified", "reference_not_present_before_model")
    events = context.get("public_events", [])
    records = context.get("observation_records", [])
    baseline = context.get("baseline_successful_query", {}).get(actor)
    boundary = baseline["end_sequence"] if baseline else -1
    baseline_position = baseline.get("position", {}).get("point") if baseline and baseline.get("position") else None
    radius = baseline.get("sensing_signature", {}).get("perceived_radius") if baseline else None
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius):
        radius = None
    later = [event for event in events if event.get("end_sequence") is not None
             and event["start_sequence"] > boundary and event["end_sequence"] <= context.get("sequence", -1)]
    failed = {r["evidence_id"] for r in records if r["observer_id"] == actor and r["state"] == "failed"
              and r["ordering"] == "certain" and r["start_sequence"] > boundary}
    unknown_queries = {r["evidence_id"] for r in records if r["observer_id"] == actor
                       and (r["ordering"] != "certain" or
                            r["state"] not in {"failed", "empty_targets", "targets_observed"})
                       and r["start_sequence"] > boundary}
    changes = {}
    for event in later:
        state = _public_state(event, actor, events)
        if baseline_position is not None and "position" in state and not ExecutionMemoryLedgerV2._same_point(state["position"], baseline_position):
            changes.setdefault(event["evidence_id"], set()).add("position_changed")
        if radius is not None and "perceived_radius" in state and state["perceived_radius"] != radius:
            changes.setdefault(event["evidence_id"], set()).add("conditions_changed")
    if kind == "none":
        if not baseline:
            return _support("unverified", "no_successful_same_actor_query_baseline")
        if changes or failed:
            return _support("unsupported", "a_supported_public_change_or_query_failure_followed_the_baseline", [*changes, *sorted(failed)])
        uncertain_action = any(_actor(event) == actor and event["tool_name"] in _ACTIONS for event in later)
        pending = any(_actor(event) == actor and event.get("end_sequence") is None and event["start_sequence"] > boundary for event in events)
        if uncertain_action or pending or unknown_queries:
            return _support("unverified", "later_action_or_query_has_unresolved_effects")
        return _support("supported", "no_change_observed_in_supported_public_fields_after_successful_query")
    if not refs:
        return _support("unverified", "change_claim_has_no_evidence_reference")
    if kind in {"position_changed", "conditions_changed"} and not baseline:
        return _support("unverified", "no_successful_same_actor_query_baseline")
    if kind == "position_changed" and baseline_position is None:
        return _support("unverified", "baseline_position_unknown")
    if kind == "conditions_changed" and radius is None:
        return _support("unverified", "baseline_sensing_radius_unknown_other_conditions_not_supported")
    by_ref = {f"{command}/{event['evidence_id']}": event for event in events}
    cited = [by_ref[ref] for ref in refs if ref in by_ref]
    if not cited:
        return _support("unsupported", "references_are_outside_the_current_command_change_window")
    timely = [event for event in cited if event.get("end_sequence") is not None and event["start_sequence"] > boundary
              and event["end_sequence"] <= context.get("sequence", -1)]
    if not timely:
        return _support("unsupported", "cited_events_are_not_after_the_last_successful_query")
    for event in timely:
        if kind == "sensing_failed" and event["evidence_id"] in failed:
            return _support("supported", "same_actor_query_failure_observed_after_baseline", [event["evidence_id"]])
        if kind in changes.get(event["evidence_id"], set()):
            return _support("supported", "same_actor_public_fact_differs_from_successful_query_baseline", [event["evidence_id"]])
    if all(_actor(event) and _actor(event) != actor for event in timely):
        return _support("unsupported", "cited_actor_does_not_match_the_declared_actor")
    if kind == "position_changed" and any("position" in _public_state(event, actor, events) for event in timely):
        return _support("unsupported", "cited_valid_position_does_not_differ_from_baseline")
    if kind == "sensing_failed" and any(event["evidence_id"] in unknown_queries for event in timely):
        return _support("unverified", "query_outcome_unknown_not_a_verified_failure")
    return _support("unverified", "cited_public_fields_do_not_support_this_change_kind")


def audit_query_decision(context, content, tool_calls):
    """Audit intended calls and narrowly verify structured observed-change claims."""
    context = context if isinstance(context, dict) else {"capture_missing": True}
    parsed = parse_query_decision(content)
    legacy = [{"actor": row["actor"], "decision": row["planned_action"]["kind"],
               "reason": row["observed_change"]["kind"], "evidence": row["observed_change"]["evidence"],
               "summary": row["observed_change"]["summary"]} for row in parsed["declarations"]]
    wire = "<query_decision>" + json.dumps(legacy) + "</query_decision>" if legacy else ""
    result = audit_v3(context, wire, tool_calls)
    result.update(parse_status=parsed["status"], parse_error=parsed["error"], declaration_format=parsed["format"],
                  contract_version=context.get("contract_version", "unknown"))
    for call in result["sensing_calls"]:
        if parsed["status"] != "valid":
            call["declaration_status"] = parsed["status"]
    for row, declaration in zip(result["declarations"], parsed["declarations"]):
        row["observed_change"] = copy.deepcopy(declaration["observed_change"])
        row["planned_action"] = {**copy.deepcopy(declaration["planned_action"]), "scope": "intent_not_execution"}
        row["observed_change_support"] = _change_support(context, row["actor"], row["observed_change"])
        row["free_text_verification"] = "not_verified"
        row["baseline_successful_query"] = copy.deepcopy(context.get("baseline_successful_query", {}).get(row["actor"]))
        row["latest_query_outcome"] = copy.deepcopy(context.get("latest_query_outcome", {}).get(row["actor"]))
    result["scope"] = "audit_only; structured_changes_have_narrow_public_checks; free_text_and_plans_are_not_facts; all_tools_allowed"
    return result


class ExecutionMemoryLedgerV4(ExecutionMemoryLedgerV2):
    query_decision_format = "split_v4"

    def __init__(self, *args, **kwargs):
        self.query_decisions = []
        super().__init__(*args, **kwargs)

    def decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        return _contract_view(super().decision_context(actor_id, decision_kind, plan), self.max_summary_chars)

    def query_decision_context(self):
        return query_decision_context_for(self)

    def record_model_response(self, content, tool_calls, *, context=None):
        with self._lock:
            try:
                row = audit_query_decision(context if context is not None else {"capture_missing": True}, content, tool_calls)
            except Exception as exc:
                row = {"audit_error": type(exc).__name__, "scope": "audit_only; all_tools_allowed"}
            self.query_decisions.append(copy.deepcopy(row))
            return copy.deepcopy(row)

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "execution-memory-v4"
            result["query_decisions"] = copy.deepcopy(self.query_decisions)
            return result
