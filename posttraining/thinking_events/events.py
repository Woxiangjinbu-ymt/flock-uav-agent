"""Deterministic turn-level events from authenticated, currently visible receipts."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

# The frozen Agent modules import each other by their top-level names.
_agent_dir = str(Path(__file__).resolve().parents[2] / "agent4drone")
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from task_execution_state import _ACTIONS, _effect, _primary, _validation_error

from .event_clauses import ClauseTracker
from .event_observations import object_value, update_information


_INVOCATION_ERROR = re.compile(r"^Error invoking tool '[^']+' with kwargs .*? with error:(.*)", re.S)
_ARGUMENT_ERROR = re.compile(r"field required|input should be|validation errors?|unexpected keyword|missing required|invalid (?:argument|parameter)", re.I)
_TRANSPORT = re.compile(r"timed?\s*out|timeout|connection (?:reset|refused|lost)|transport error", re.I)


def _arguments(value):
    value = object_value(value)
    if set(value) != {"input_json"}:
        return value
    inner = value["input_json"]
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            return {}
    return object_value(inner)


def _action(tool, args, result):
    """Classify the action component, including identity at both wrapper levels."""
    receipt, _ = _primary(tool, result)
    actor = args.get("drone_id")
    if not isinstance(actor, str) or not actor:
        return None, "action_actor_missing"
    nested_move = object_value(receipt).get("last_move_result")
    for layer in (object_value(result), object_value(receipt), object_value(nested_move)):
        if layer.get("drone_id") not in (None, actor):
            return "E2", "wrong_actor_receipt"
    if isinstance(receipt, str) and _TRANSPORT.search(receipt):
        return "E2", "transport_timeout_or_exception"
    if isinstance(receipt, dict):
        errors = " ".join(str(receipt.get(key, "")) for key in ("error", "tool_exception", "harness_transport_error"))
        if _TRANSPORT.search(errors):
            return "E2", "transport_timeout_or_exception"
    # _effect requires a completed invocation boundary, supplied by the archive
    # verifier. It interprets navigation's receipt-level flags and raw statuses.
    status, reason = _effect({"status": "returned", "end_sequence": 1, "args": args}, receipt, True)
    return {"success": None, "partial": "E3", "failure": "E1" if reason == "blocked" else "E4",
            "unknown": "E2"}[status], reason


def _observation_success(tool, args, result):
    if tool == "list_drones":
        return isinstance(result, list) and all(isinstance(row, dict) and row.get("id") for row in result)
    payload = object_value(result)
    if not payload or any(key in payload for key in ("tool_exception", "harness_transport_error")):
        return False
    if tool == "get_drone_status":
        return bool(payload.get("id")) and payload["id"] == args.get("drone_id")
    if tool == "get_target_info":
        return bool(payload.get("id")) and payload["id"] == args.get("target_id")
    if tool == "get_weather":
        return bool(payload.get("weather"))
    if tool == "get_mission_progress":
        return "progress_percentage" in payload or "progress_percent" in payload
    if tool in {"get_nearby_entities", "sense_nearby_entities", "get_blackboard"}:
        return any(key in payload for key in ("targets", "obstacles", "drones", "per_drone", "blackboard"))
    return payload.get("status") == "success" or payload.get("success") is True


def _remaining(state):
    for key in ("remaining_waypoints", "unsuccessful_points", "unsuccessful_points_count"):
        value = state.get(key)
        if (isinstance(value, list) and value) or (type(value) is int and value > 0):
            return True
    return False


def _classify(source):
    call = {**source, "arguments": _arguments(source["arguments"])}
    tool, args, result = call["tool"], call["arguments"], call["result"]
    call.update(actor_id=args.get("drone_id"), observation_ok=False, success=False,
                effects=[], gaps=[])
    payload = object_value(result)
    invocation = _INVOCATION_ERROR.match(result) if isinstance(result, str) else None
    validation = bool(invocation and _ARGUMENT_ERROR.search(invocation.group(1)))
    if _validation_error(result) or validation:
        call["effects"].append(("E4", "tool_validation_error"))
    elif tool in _ACTIONS:
        event, reason = _action(tool, args, result)
        if event:
            call["effects"].append((event, reason))
        elif reason == "action_actor_missing":
            call["gaps"].append(reason)
        else:
            call["success"] = True
        call["observation_ok"] = (tool.endswith("_and_sense") and reason != "wrong_actor_receipt"
                                  and any(key in payload for key in ("nearby", "blackboard", "per_drone")))
    elif tool == "run_harness":
        state = object_value(payload.get("state"))
        last = object_value(state.get("last_action"))
        status = payload.get("status")
        if status in {"rejected", "unsupported", "failed", "error"} and payload.get("reason"):
            call["effects"].append(("E4", str(payload["reason"])))
        if last.get("tool") in _ACTIONS and "receipt" in last:
            last_args = object_value(last.get("args"))
            call["actor_id"] = last_args.get("drone_id")
            event, reason = _action(last["tool"], last_args, last["receipt"])
            call["last_action_event"] = event
            call["last_action_success"] = event is None and reason == "completed"
            if event:
                call["effects"].append((event, reason))
            elif reason == "action_actor_missing":
                call["gaps"].append(reason)
        if _remaining(state) or _remaining(payload):
            if not any(code == "E3" for code, _ in call["effects"]):
                call["effects"].append(("E3", "remaining_waypoints"))
        call["observation_ok"] = bool(state)
        call["success"] = status == "completed"
        if not call["effects"] and not call["success"]:
            call["gaps"].append("subprogram_effect_unconfirmed")
    elif (payload.get("status") in ("error", "failed", "rejected") or payload.get("success") is False
          or payload.get("error")) and not any(k in payload for k in ("tool_exception", "harness_transport_error")):
        call["effects"].append(("E4", "tool_reported_failure"))
    elif _observation_success(tool, args, result):
        call["observation_ok"] = call["success"] = True
    else:
        call["gaps"].append("observation_unconfirmed")
    return call


def extract_events(prefix):
    """Rebuild history solely from the batches still in this request's context.

    Each batch is one assistant decision's ordered, verified tool messages.
    Current reply, usage, and evaluation have no input to this function.
    """
    tracker = ClauseTracker(prefix["task"])
    history_gaps = list(prefix.get("history_gaps", ()))
    current_gaps = list(prefix.get("gaps", ()))
    if prefix["turn"] > 1 and not prefix["batches"]:
        current_gaps.append("previous_decision_batch_missing")
    known, progress = set(), {}
    events, gaps = [], []
    for index, raw_batch in enumerate(prefix["batches"]):
        events, gaps = [], []
        latest = index == len(prefix["batches"]) - 1
        batch = [_classify(receipt) for receipt in raw_batch]

        def emit(code, call, reason, **details):
            actor = details.pop("actor_id", call["actor_id"])
            events.append({"type": code, "call_id": call["call_id"],
                           "scope": "drone" if actor else "fleet", "actor_id": actor,
                           "evidence": {"sequence": call["sequence"],
                                        "wire_message_index": call.get("wire_message_index")},
                           "reason": reason, **details})

        for call in batch:
            for code, reason in call["effects"]:
                emit(code, call, reason)
            gaps.extend(f"{call['call_id']}:{reason}" for reason in call["gaps"])
        update_information(batch, known, progress, not history_gaps, emit, gaps)
        tracker.update(batch, emit, history_incomplete=bool(history_gaps or (current_gaps and latest)))
    gaps.extend(history_gaps)
    gaps.extend(current_gaps)
    if prefix["turn"] == 1:
        state, events = "start", []
    elif events:
        state = "event"
    elif gaps:
        state = "indeterminate"
    else:
        state = "E0"
    return {"event_state": state, "events": events, "gaps": list(dict.fromkeys(gaps)),
            "clause_coverage": tracker.coverage}
