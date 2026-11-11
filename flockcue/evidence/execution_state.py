"""Pure projection of public ledger/frame evidence, never a task checker.

No world, clock, provider, tool, or model is consulted here. ``applied`` and
``remaining`` contain only explicit receipt fields, not requested values or a
later observation substituted for historical execution. All recommendations
are advisory; this module cannot retry, resume, or certify mission completion.
"""
from __future__ import annotations

import json
import re

from flockcue.evidence.receipts import command_outcome


SCHEMA = "task-execution-state-v1"
_ACTIONS = frozenset({
    "move_to", "move_to_and_sense", "navigate_to", "navigate_to_and_sense",
    "move_towards", "move_towards_and_sense", "move_along_path",
    "move_along_path_and_sense", "move_within_distance", "take_off", "land",
    "return_home", "change_altitude", "hover", "rotate", "set_home",
    "calibrate", "take_photo", "send_message", "broadcast", "charge",
})
_TERMINAL = frozenset({"completed", "cancelled", "needs_replan", "interrupted", "abandoned", "failed"})
_DEPENDENCY_REASONS = frozenset({
    "scene_scope_changed", "external_action_changed_dependencies",
    "actor_state_changed_between_units", "search_actor_state_changed_between_units",
    "shared_endpoint_static_obstacle_contract_changed", "takeoff_precondition_changed",
})
_APPLIED_FIELDS = (
    "final_position", "successful_points_count", "successful_points",
    "completed_waypoints", "current_waypoint_index", "arrival_verified",
    "within_distance", "distance_after", "distance_traveled", "applied_distance",
    "applied_duration", "applied_heading", "applied_altitude",
)
_REMAINING_FIELDS = (
    "distance_remaining", "remaining_distance", "remaining_waypoints",
    "unsuccessful_points_count", "unsuccessful_points", "remaining_duration",
)
_VALIDATION_ERROR = re.compile(r"\bvalidation\s*errors?\b", re.I)


def _json_copy(value):
    # Refuse objects and non-finite numbers rather than silently inventing text.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _snapshot(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a JSON object or None")
    return _json_copy(value)


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _object(value):
    return value if isinstance(value, dict) else {}


def _primary(tool, payload):
    if tool.endswith("_and_sense") and isinstance(payload, dict):
        for key in ("navigation_result", "move_result"):
            if key in payload:
                return payload[key], [key]
    return payload, []


def _validation_error(payload):
    if isinstance(payload, str):
        return bool(_VALIDATION_ERROR.search(payload))
    if isinstance(payload, dict):
        return payload.get("error_type") in ("ValidationError", "ToolValidationError") or any(
            isinstance(payload.get(key), str) and _VALIDATION_ERROR.search(payload[key])
            for key in ("error", "message", "tool_exception")
        )
    return False


def _effect(event, receipt, is_action):
    if event.get("status") == "pending" or event.get("end_sequence") is None:
        return "unknown", "pending_tool_call"
    if _validation_error(receipt):
        return "failure", "tool_validation_error"
    if isinstance(receipt, dict) and any(k in receipt for k in ("tool_exception", "harness_transport_error")):
        return "unknown", "tool_transport_or_execution_exception"
    if is_action:
        expected_actor = _object(event.get("args")).get("drone_id")
        if isinstance(receipt, dict) and expected_actor is not None and receipt.get("drone_id") not in (None, expected_actor):
            return "unknown", "wrong_actor_receipt"
        # The ledger can deliberately downgrade a receipt after a transport or
        # parse error. A derived client outcome is allowed only for its public
        # navigation/relative-distance contract, never for arbitrary JSON.
        if event.get("status") == "unknown":
            return "unknown", "unconfirmed_action_effect"
        outcome = command_outcome(receipt)
        if isinstance(receipt, dict) and ("arrival_verified" in receipt or "within_distance" in receipt):
            derived = receipt.get("outcome")
            if isinstance(derived, str) and derived in {"completed", "partial", "blocked", "error", "unknown"}:
                flags = [receipt[k] for k in ("arrival_verified", "within_distance") if k in receipt]
                invalid_flag = any(not isinstance(flag, bool) for flag in flags) or any(
                    receipt.get(key) is not None and not isinstance(receipt[key], bool)
                    for key in ("success", "partial_success"))
                if invalid_flag:
                    outcome = "unknown"
                elif derived == "completed" and (not all(flags) or receipt.get("success") is False or receipt.get("error")
                                                  or receipt.get("status") in ("error", "partial_success")
                                                  or receipt.get("partial_success") is True):
                    outcome = "unknown"
                elif derived != "completed" and (any(flags) or receipt.get("status") == "success"):
                    outcome = "unknown"
                elif derived == "partial" and (receipt.get("status") == "error" or receipt.get("partial_success") is False or receipt.get("error")):
                    outcome = "unknown"
                elif derived in {"blocked", "error"} and (receipt.get("partial_success") is True or receipt.get("status") == "partial_success" or receipt.get("success") is True):
                    outcome = "unknown"
                else:
                    outcome = derived
        return {"completed": "success", "partial": "partial", "blocked": "failure", "error": "failure"}.get(outcome, "unknown"), outcome
    if event.get("status") in {"error", "blocked", "failed"}:
        return "failure", "tool_reported_failure"
    if event.get("status") == "observed" and isinstance(receipt, (dict, list)):
        return "success", "observation_returned"
    return "unknown", "unconfirmed_tool_result"


def _global_state(lifecycle, harness):
    reasons = []
    for field, reason in (("cancelled", "cancelled"), ("deadline_reached", "deadline"), ("closed", "closed")):
        if lifecycle.get(field) is True:
            reasons.append(reason)
    if lifecycle.get("stop_reason"):
        reasons.append(str(lifecycle["stop_reason"]))
    status = lifecycle.get("status")
    if status in {"completed", "success", "failed", "error", "cancelled", "interrupted", "closed", "terminated", "timeout", "deadline"}:
        reasons.append(f"lifecycle_{status}")
    for event in _rows(harness.get("events")):
        if event.get("kind") in {"task_interrupt", "task_end"}:
            reasons.append(str(event.get("reason") or event["kind"]))
    if harness.get("closed") is True:
        reasons.append("harness_closed")
    return {"stopped": bool(reasons), "stop_reasons": list(dict.fromkeys(reasons)),
            "further_actions_allowed": not reasons,
            "status": status or ("stopped" if reasons else "unspecified")}


def _source_index(harness):
    by_evidence, by_call = {}, {}
    for event in _rows(harness.get("events")):
        if event.get("kind") not in {"public_call_start", "public_call_end"}:
            continue
        source = {"plan_id": event.get("plan_id"), "harness_event_sequence": event.get("sequence")}
        if event.get("evidence_id"):
            by_evidence.setdefault(event["evidence_id"], source)
        if event.get("call_id"):
            by_call.setdefault(event["call_id"], source)
    return by_evidence, by_call


def _tool_record(event, source_index):
    tool = str(event.get("tool_name", ""))
    is_action = event.get("scope") == "action_receipt" or tool in _ACTIONS
    receipt, path = _primary(tool, event.get("payload"))
    effect, reason = _effect(event, receipt, is_action)
    source = source_index[0].get(event.get("evidence_id")) or source_index[1].get(event.get("tool_call_id")) or {}
    # A receipt for another actor cannot be attached as this actor's effect.
    measured = _object(receipt) if is_action and reason != "wrong_actor_receipt" else {}
    return {
        "evidence_id": event.get("evidence_id"), "tool_call_id": event.get("tool_call_id"),
        "tool_name": tool, "kind": "action" if is_action else "observation_or_tool",
        "requested": _object(event.get("args")),
        "applied": {key: measured[key] for key in _APPLIED_FIELDS if key in measured and measured[key] is not None} or None,
        "remaining": {key: measured[key] for key in _REMAINING_FIELDS if key in measured and measured[key] is not None} or None,
        "effect_status": effect, "effect_reason": reason,
        "receipt_status": event.get("status"), "receipt": receipt,
        "ordering": event.get("ordering", "unknown"), "overlaps": event.get("overlaps", []),
        "start_sequence": event.get("start_sequence"), "end_sequence": event.get("end_sequence"),
        "source_clause_ids": event.get("source_clause_ids", []),
        "source": {"evidence_id": event.get("evidence_id"), "tool_call_id": event.get("tool_call_id"),
                   "plan_id": source.get("plan_id"), "receipt_path": path,
                   "harness_event_sequence": source.get("harness_event_sequence"),
                   "origin": "harness_public_call" if source else "ledger_tool_call"},
        "completion_scope": "this_call_only_not_task_completion",
    }


def _frame_record(frame, harness, stopped):
    invalidations = [event for event in _rows(harness.get("events"))
                     if event.get("kind") == "plan_invalidated" and event.get("plan_id") == frame.get("plan_id")]
    dependency_invalid = bool(invalidations) or frame.get("reason") in _DEPENDENCY_REASONS
    status = frame.get("status", "unknown")
    known_status = status in _TERMINAL or status in {"ready", "paused", "running"}
    result = {key: frame.get(key) for key in (
        "plan_id", "plan_version", "task_id", "scene_id", "scene_epoch", "skill", "origin",
        "source_obligations", "actor_bindings", "evidence_refs", "steps", "cursor", "status", "reason",
        "continue_guards", "invalidators", "local_repair_scope", "stop_condition",
        "api_calls", "api_budget", "repairs", "repair_budget", "deadline", "units", "state",
    )}
    result.update(terminal=status in _TERMINAL if known_status else None,
                  resumable=status in {"ready", "paused"} and not stopped and not dependency_invalid,
                  dependency_status="invalidated" if dependency_invalid else "not_independently_revalidated",
                  invalidation_evidence=[{key: event.get(key) for key in ("sequence", "reason", "tool_call_id")} for event in invalidations],
                  mission_completion="not_evaluated")
    return result


def _followups(record, records):
    """Identify bounded follow-up evidence, never retroactively fix a receipt.

    Equality of tool+arguments links a later successful attempt. A later public
    actor-state read can inform an unknown effect, but cannot prove its history.
    Unordered/overlapping calls are deliberately not linked by list position.
    """
    end = record["end_sequence"]
    if (record["ordering"] != "certain" or record["overlaps"] or isinstance(end, bool)
            or not isinstance(end, (int, float))):
        return []
    actor = record["requested"].get("drone_id")
    linked = {}
    for later in records:
        start = later["start_sequence"]
        if (later["ordering"] != "certain" or later["overlaps"] or later["effect_status"] != "success"
                or isinstance(start, bool) or not isinstance(start, (int, float)) or start <= end):
            continue
        kind = None
        if later["tool_name"] == record["tool_name"] and later["requested"] == record["requested"]:
            kind = "same_request_success"
        elif (record["effect_status"] == "unknown" and actor is not None
              and later["tool_name"] == "get_drone_status" and later["requested"].get("drone_id") == actor
              and _object(later["receipt"]).get("id") == actor):
            kind = "public_state_observation_not_historical_proof"
        if kind is not None:
            linked[kind] = {"kind": kind, "evidence_id": later["evidence_id"],
                            "tool_call_id": later["tool_call_id"], "tool_name": later["tool_name"],
                            "start_sequence": start, "plan_id": later["source"]["plan_id"]}
    return list(linked.values())


def _recovery(records, frames, global_state):
    stopped = global_state["stopped"]
    items = []
    for record in records:
        effect, reason = record["effect_status"], record["effect_reason"]
        if effect == "success":
            continue
        pending = reason == "pending_tool_call"
        category = "in_flight" if pending else "tool_validation_failure" if reason == "tool_validation_error" else {
            "failure": "explicit_failure", "partial": "partial_completion", "unknown": "unknown_effect",
        }[effect]
        recommendation = {
            "in_flight": "wait_for_existing_call_do_not_duplicate",
            "tool_validation_failure": "correct_arguments_then_decide_new_call",
            "explicit_failure": "inspect_failure_and_current_public_state_then_replan",
            "partial_completion": "preserve_confirmed_effects_and_plan_only_remaining_work",
            "unknown_effect": "observe_current_public_state_without_certifying_history_do_not_replay",
        }[category]
        followups = _followups(record, records) if not pending else []
        if followups:
            recommendation = ("review_public_followup_without_certifying_historical_effect_do_not_replay"
                              if effect == "unknown" else "review_successful_followup_do_not_replay_historical_request")
        items.append({"category": category, "evidence_id": record["evidence_id"],
                      "tool_call_id": record["tool_call_id"], "tool_name": record["tool_name"],
                      "plan_id": record["source"]["plan_id"], "recommendation": "stop_no_further_actions" if stopped else recommendation,
                      "issue_state": "historical_with_followup" if followups else "in_flight" if pending else "unresolved_in_public_record",
                      "followup_evidence": followups,
                      "eligible_now": not stopped and not pending and not followups, "automatic_retry": False,
                      "historical_effect_resolved": False})
    for frame in frames:
        if frame["dependency_status"] == "invalidated":
            category, advice = "dependency_invalidated", "reobserve_dependencies_and_replan_do_not_resume_old_plan"
        elif frame["resumable"]:
            category, advice = "resumable_plan", "resume_existing_plan_id_subject_to_runtime_guards"
        elif frame["terminal"]:
            category, advice = "terminal_plan", "do_not_resume_or_restart_this_skill_review_original_obligations"
        else:
            category, advice = "non_resumable_plan", "do_not_issue_resume_until_runtime_reports_ready_or_paused"
        items.append({"category": category, "plan_id": frame["plan_id"], "status": frame["status"],
                      "reason": frame["reason"], "cursor": frame["cursor"],
                      "recommendation": "stop_no_further_actions" if stopped else advice,
                      "eligible_now": not stopped and category in {"dependency_invalidated", "resumable_plan"},
                      "resume_allowed": frame["resumable"], "automatic_retry": False})
    eligible = sum(item["eligible_now"] for item in items)
    resumable = sum(frame["resumable"] for frame in frames)
    return {
        "advisory_only": True, "automatic_retry": False,
        "global_stop": stopped, "items": items,
        "opportunities": {"current_candidates": eligible, "resumable_frames": resumable,
                          "recovery_success_rate": None,
                          "rate_status": "not_evaluated" if eligible else "no_current_opportunity",
                          "scope": "current_snapshot_not_historical_recovery_denominator"},
    }


def build_task_execution_state(command, command_id, ledger_snapshot=None, harness_snapshot=None, lifecycle=None):
    """Build a detached JSON public state; unsupported obligations stay verbatim.

    ``lifecycle`` may supply ``status``, ``stop_reason``, ``cancelled``,
    ``deadline_reached`` and ``closed``. A global stop dominates every recovery
    suggestion. Deadlines are not inferred from wall clocks in this pure API.
    Non-action calls live in ``tool_calls``; ``actions`` contains action calls.
    A failed request can have side effects, so its unknown amount remains null.
    """
    if not isinstance(command, str) or not isinstance(command_id, str):
        raise TypeError("command and command_id must be strings")
    ledger = _snapshot(ledger_snapshot, "ledger_snapshot")
    harness = _snapshot(harness_snapshot, "harness_snapshot")
    lifecycle = _snapshot(lifecycle, "lifecycle")
    for actual, expected, name in ((ledger.get("command"), command, "ledger command"),
                                   (ledger.get("command_id"), command_id, "ledger command_id"),
                                   (harness.get("task_id"), command_id, "harness task_id")):
        if actual is not None and actual != expected:
            raise ValueError(f"{name} does not match the projected task")
    for frame in _rows(harness.get("frames")):
        if frame.get("task_id") is not None and frame["task_id"] != command_id:
            raise ValueError("frame task_id does not match the projected task")
    global_state = _global_state(lifecycle, harness)
    source_index = _source_index(harness)
    records = [_tool_record(event, source_index) for event in _rows(ledger.get("events"))]
    frames = [_frame_record(frame, harness, global_state["stopped"]) for frame in _rows(harness.get("frames"))]
    evidence = _object(ledger.get("task_evidence"))
    return {
        "schema": SCHEMA, "command": command, "command_id": command_id,
        "mission_completion": "not_evaluated",
        "obligations": {"original_command": command, "original_task_controls": True,
                        "clauses": _rows(ledger.get("clauses")),
                        "requirements": _rows(evidence.get("requirements")),
                        "aggregate_requirements": _rows(evidence.get("aggregate_requirements")),
                        "unparsed_obligations": {"source_text": command, "status": "not_fully_parsed"},
                        "interpretation_complete": False},
        "entity_bindings": _object(ledger.get("entity_bindings")),
        "entities": _object(ledger.get("entities")),
        "actions": [record for record in records if record["kind"] == "action"],
        "tool_calls": [record for record in records if record["kind"] != "action"],
        "frames": frames, "lifecycle": global_state,
        "recovery": _recovery(records, frames, global_state),
        "sources": {"ledger_present": ledger_snapshot is not None, "harness_present": harness_snapshot is not None,
                    "ledger_revision": ledger.get("revision"), "harness_revision": harness.get("revision"),
                    "scheduler": harness.get("scheduler"), "observer_errors": ledger.get("observer_errors", [])},
        "limits": ["public_ledger_and_frames_only", "tool_success_and_skill_completion_are_not_mission_success",
                   "unknown_remaining_is_null", "current_state_does_not_prove_historical_effect",
                   "no_automatic_retry", "in_process_resume_only", "unparsed_original_obligations_retained"],
    }


def concise_recovery_projection(state, max_items=4):
    """Bounded advisory prompt data: IDs/control/issues, never full receipts.

    Current candidates precede historical issues; newer items win within each
    group. Follow-up evidence never changes an earlier call's effect status.
    """
    if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 0:
        raise ValueError("max_items must be a nonnegative integer")
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        raise ValueError("expected a task-execution-state-v1 projection")
    recovery = _object(state.get("recovery"))
    items = _rows(recovery.get("items"))
    selected = sorted(reversed(items), key=lambda item: not item.get("eligible_now", False))[:max_items]
    fields = ("category", "evidence_id", "tool_name", "plan_id", "status", "cursor", "recommendation", "resume_allowed", "eligible_now", "issue_state", "followup_evidence")
    return _json_copy({
        "schema": "task-recovery-prompt-v1", "command_id": state["command_id"],
        "mission_completion": "not_evaluated", "original_task_controls": True,
        "global_stop": recovery.get("global_stop", False),
        "stop_reasons": _object(state.get("lifecycle")).get("stop_reasons", []),
        "automatic_retry": False,
        "items": [{key: item[key] for key in fields if key in item} for item in selected],
        "omitted": len(items) - len(selected),
        "opportunities": recovery.get("opportunities", {}),
    })
