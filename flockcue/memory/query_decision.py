"""An optional same-response query decision contract; auditing never gates tools.

V2 facts, guidance, and receipt semantics remain unchanged. A model declaration
is an untrusted statement of intent: even a valid citation only establishes
that an event existed before the response, not that its stated reason is true.
"""
from __future__ import annotations

import copy
import json
import re

from execution_memory import _SENSE
from execution_memory_v2 import ExecutionMemoryLedgerV2
from task_evidence import encode
from task_progress import _MOVEMENT

QUERY_DECISION_CONTRACT = (
    'Before querying get_nearby_entities again for an actor in checked_here, put one short '
    '<query_decision>{"actor":"ID","decision":"reuse|change_viewpoint|refresh",'
    '"reason":"position_changed|sensing_failed|conditions_changed|purpose_changed|uncertain|none",'
    '"evidence":["eN"],"summary":"one factual sentence"}</query_decision> in the SAME response. '
    'For refresh, the summary must say why another read is needed and what was actually observed '
    'to change/fail (or which purpose/uncertainty differs); cite existing events '
    '(eN is this command; command/eN is historical). If no observed change, say none with [] '
    'and explicitly state that no change was observed. '
    'If nothing changed and no different information is needed, prefer reuse or change_viewpoint '
    'instead of refresh. Do not invent changes. Multiple actors may use one JSON array. No extra call or turn.'
)
_TAG = re.compile(r"<query_decision>(.*?)</query_decision>", re.S)
_DECISIONS = {"reuse", "change_viewpoint", "refresh"}
_REASONS = {"position_changed", "sensing_failed", "conditions_changed", "purpose_changed", "uncertain", "none"}


def _contract_view(base, limit):
    result = copy.deepcopy(base)
    if result.get("checked_here") and result.get("advice"):
        candidate = {**result, "query_decision_contract": QUERY_DECISION_CONTRACT}
        if len(encode(candidate)) <= min(3900, limit):
            result = candidate
    return result


def query_decision_context_for(ledger):
    """Capture pre-model evidence and eligibility, also for a fresh V2 control."""
    with ledger._lock:
        focus = copy.deepcopy(ledger._memory_focus)
        base = ExecutionMemoryLedgerV2.decision_context(ledger, **focus)
        eligible = bool(base.get("checked_here") and base.get("advice"))
        valid = ledger.index_epoch == ledger.index.epoch
        known = set()
        if valid:
            for event in ledger._events:
                if event["end_sequence"] is not None:
                    known.add(f"{ledger.command_id}/{event['evidence_id']}")
            for reports in ledger._reports.values():
                for report in reports:
                    known.add(f"{report['command_id']}/{report['evidence_id']}")
            for record in ledger._observation_attempts:
                if record["epoch"] == ledger.index_epoch:
                    known.add(f"{record['command_id']}/{record['evidence_id']}")
        can_show = "query_decision_contract" in _contract_view(base, ledger.max_summary_chars)
        return {"command_id": ledger.command_id, "epoch": ledger.index_epoch,
                "scene_valid": valid, "sequence": ledger._sequence,
                "request_index": len(ledger.model_requests) - 1 if ledger.model_requests else None,
                "contract_eligible": eligible,
                "contract_active": bool(getattr(ledger, "query_decision_contract_enabled", False) and can_show),
                "contract_omitted_for_budget": bool(eligible and not can_show),
                "eligible_actors": [row["actor"] for row in base.get("checked_here", [])] if eligible else [],
                "checked_here": copy.deepcopy(base.get("checked_here", [])) if eligible else [],
                "known_evidence_refs": sorted(known)}


def _text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item["text"] for item in content if isinstance(item, dict)
                         and item.get("type") == "text" and isinstance(item.get("text"), str))
    return ""


def parse_query_decision(content):
    """Bounded syntactic parsing only; malformed output cannot block a tool."""
    text = _text_content(content)
    if len(text) > 32000:
        return {"status": "invalid", "error": "response_too_long", "declarations": []}
    matches = _TAG.findall(text)
    if not matches:
        return {"status": "invalid" if "<query_decision>" in text else "missing",
                "error": "unclosed_tag" if "<query_decision>" in text else None, "declarations": []}
    if len(matches) != 1:
        return {"status": "invalid", "error": "multiple_tags", "declarations": []}
    if len(matches[0]) > 4096:
        return {"status": "invalid", "error": "declaration_too_long", "declarations": []}
    try:
        def reject_constant(value):
            raise ValueError(value)
        value = json.loads(matches[0], parse_constant=reject_constant)
    except (TypeError, ValueError):
        return {"status": "invalid", "error": "invalid_json", "declarations": []}
    declarations = [value] if isinstance(value, dict) else value
    if not isinstance(declarations, list) or not 1 <= len(declarations) <= 8:
        return {"status": "invalid", "error": "invalid_declaration_list", "declarations": []}
    actors = set()
    for row in declarations:
        if (not isinstance(row, dict) or set(row) != {"actor", "decision", "reason", "evidence", "summary"}
                or not isinstance(row["actor"], str) or not 1 <= len(row["actor"]) <= 128
                or row["actor"] in actors
                or not isinstance(row["decision"], str) or row["decision"] not in _DECISIONS
                or not isinstance(row["reason"], str) or row["reason"] not in _REASONS
                or not isinstance(row["summary"], str) or not 1 <= len(row["summary"]) <= 240
                or not isinstance(row["evidence"], list) or len(row["evidence"]) > 8
                or any(not isinstance(ref, str) or not 1 <= len(ref) <= 256 for ref in row["evidence"])):
            return {"status": "invalid", "error": "invalid_schema_or_duplicate_actor", "declarations": []}
        actors.add(row["actor"])
    return {"status": "valid", "error": None, "declarations": copy.deepcopy(declarations)}


def _tool_calls(tool_calls):
    result = []
    for call in tool_calls if isinstance(tool_calls, list) else []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        name = function.get("name")
        args = function.get("arguments", function.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if isinstance(args, dict) and set(args) == {"input_json"}:
            try:
                args = json.loads(args["input_json"]) if isinstance(args["input_json"], str) else args["input_json"]
            except ValueError:
                args = {}
        result.append({"id": call.get("id"), "name": name,
                       "args": args if isinstance(args, dict) else {}})
    return result


def audit_query_decision(context, content, tool_calls):
    """Pure audit of a response against a pre-model capture; no tool mutation."""
    context = context if isinstance(context, dict) else {}
    parsed = parse_query_decision(content)
    known = set(context.get("known_evidence_refs", []))
    command = str(context.get("command_id", ""))
    declarations = []
    for row in parsed["declarations"]:
        refs = [{"supplied": ref, "resolved": ref if "/" in ref else f"{command}/{ref}",
                 "known_before_model": (ref if "/" in ref else f"{command}/{ref}") in known}
                for ref in row["evidence"]]
        grounding = ("unknown_reference" if any(not ref["known_before_model"] for ref in refs)
                     else "references_exist_meaning_not_verified" if refs
                     else "no_observed_change_declared" if row["reason"] == "none"
                     else "uncertainty_declared_without_reference" if row["reason"] == "uncertain"
                     else "claimed_change_without_reference")
        declarations.append({**row, "references": refs, "grounding": grounding,
                             "reason_truth": "not_verified_model_statement"})
    by_actor = {row["actor"]: row for row in declarations}
    eligible = set(context.get("eligible_actors", []))
    calls = _tool_calls(tool_calls)
    query_counts = {}
    for call in calls:
        if call["name"] in _SENSE and call["args"].get("drone_id") is not None:
            actor = str(call["args"]["drone_id"])
            query_counts[actor] = query_counts.get(actor, 0) + 1
    moved = set()
    sensing = []
    for ordinal, call in enumerate(calls):
        if call["name"] in _MOVEMENT:
            moved.add(str(call["args"].get("drone_id")))
        if call["name"] not in _SENSE:
            continue
        actor = str(call["args"]["drone_id"]) if call["args"].get("drone_id") is not None else None
        declaration = by_actor.get(actor)
        relation = "not_declared"
        if declaration:
            relation = ("reuse_but_query_requested" if declaration["decision"] == "reuse" else
                        "change_viewpoint_with_prior_movement_request" if declaration["decision"] == "change_viewpoint" and actor in moved else
                        "change_viewpoint_without_prior_movement_request" if declaration["decision"] == "change_viewpoint" else
                        "refresh_query_requested")
        sensing.append({"tool_call_id": call["id"], "call_ordinal": ordinal,
                        "tool": call["name"], "actor": actor,
                        "contract_applies": call["name"] == "get_nearby_entities" and actor in eligible,
                        "declaration_status": "present" if declaration else parsed["status"] if parsed["status"] != "valid" else "missing_actor",
                        "actor_sensing_call_count": query_counts.get(actor, 0),
                        "declaration_binding": "multiple_queries_same_actor_not_individually_justified" if declaration and query_counts.get(actor, 0) > 1 else
                                               "single_query" if declaration else "not_declared",
                        "decision": declaration["decision"] if declaration else None,
                        "reason": declaration["reason"] if declaration else None,
                        "movement_requested_before_query": actor in moved,
                        "decision_call_relation": relation, "execution_policy": "allowed_unchanged"})
    return {"command_id": command, "epoch": context.get("epoch"),
            "decision_sequence": context.get("sequence"), "request_index": context.get("request_index"),
            "contract_eligible": bool(context.get("contract_eligible")),
            "contract_active": bool(context.get("contract_active")),
            "contract_omitted_for_budget": bool(context.get("contract_omitted_for_budget")),
            "capture_status": "missing" if context.get("capture_missing") else "provided",
            "parse_status": parsed["status"], "parse_error": parsed["error"],
            "declarations": declarations, "sensing_calls": sensing,
            "eligible_actors_without_query": sorted(eligible - {row["actor"] for row in sensing}),
            "scope": "audit_only; model_reasons_are_not_facts; references_checked_before_model; all_tools_allowed"}


class ExecutionMemoryLedgerV3(ExecutionMemoryLedgerV2):
    query_decision_contract_enabled = True

    def __init__(self, *args, **kwargs):
        self.query_decisions = []
        super().__init__(*args, **kwargs)

    def decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        base = super().decision_context(actor_id, decision_kind, plan)
        return _contract_view(base, self.max_summary_chars)

    def query_decision_context(self):
        return query_decision_context_for(self)

    def record_model_response(self, content, tool_calls, *, context=None):
        """Append audit only; never change evidence or propagate it to a prompt."""
        with self._lock:
            try:
                capture = context if context is not None else {
                    "command_id": self.command_id, "epoch": self.index_epoch,
                    "capture_missing": True, "known_evidence_refs": []}
                row = audit_query_decision(capture, content, tool_calls)
            except Exception as exc:
                row = {"audit_error": type(exc).__name__, "scope": "audit_only; all_tools_allowed"}
            self.query_decisions.append(copy.deepcopy(row))
            return copy.deepcopy(row)

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "execution-memory-v3"
            result["query_decisions"] = copy.deepcopy(self.query_decisions)
            return result
