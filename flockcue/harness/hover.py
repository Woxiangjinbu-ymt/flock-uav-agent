"""Narrow, read-only completion assessment from command-local public evidence.

State goals, explicit action requests, and elapsed-duration claims are distinct.
This module neither executes tools nor reads a simulator/checker/clock. It never
changes historical unknown action effects and is not a general task verifier.
"""
from __future__ import annotations

import math
import re


COMPLETION_CONTRACTS = ("off", "hover_completion_v1", "hover_completion_v2")
SCHEMA = "hover-completion-v1"
TOLERANCE = 1e-6
# The raw take_off tool catches transport exceptions and returns text, while
# the harness reports a structured exception. Accept only this known lost-
# response form; arbitrary errors can precede dispatch and do not establish it.
_RAW_TAKEOFF_TIMEOUT = "Error during takeoff: Request timed out before response was received"
_NUMBER = r"(\d+(?:\.\d+)?)"
_HOVER_WORD = re.compile(r"\bhov(?:er|ers|ering)\b|悬停", re.I)
_ACTIONS = frozenset({
    "take_off", "land", "move_to", "move_to_and_sense", "navigate_to",
    "navigate_to_and_sense", "move_towards", "move_towards_and_sense",
    "move_along_path", "move_along_path_and_sense", "move_within_distance",
    "change_altitude", "return_home", "hover", "rotate", "set_home",
    "calibrate", "take_photo", "send_message", "broadcast", "charge",
})
_MOVEMENT = _ACTIONS - {"hover", "rotate", "set_home", "calibrate", "take_photo",
                       "send_message", "broadcast", "charge"}
_ANCHOR_READS = frozenset({"list_drones", "get_drone_status", "get_weather"})


def validate_completion_contract(value):
    if not isinstance(value, str) or value not in COMPLETION_CONTRACTS:
        raise ValueError("completion_contract must be off, hover_completion_v1 or hover_completion_v2")
    return value


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def parse_hover_contract(command):
    """Full-match a deliberately small grammar; never erase trailing clauses."""
    if not isinstance(command, str):
        raise TypeError("command must be a string")
    text = " ".join(command.split())
    result = {"kind": "not_applicable", "actor_name": None, "target_altitude": None,
              "takeoff_required": False, "explicit_hover_required": False,
              "duration_seconds": None, "interpretation_complete": False}
    if not _HOVER_WORD.search(text):
        return result
    result["kind"] = "unsupported"
    # A duration suffix is recognized only after a complete supported base.
    duration = re.fullmatch(r"(.+?)(?: for " + _NUMBER + r" seconds?| briefly)\.?", text, re.I)
    base = duration.group(1) if duration else text
    if base.endswith("."):
        base = base[:-1]
    patterns = (
        (rf"Command drone Drone (\d+) to take off to an altitude of {_NUMBER} meters and hover in place", "state_goal", True),
        (rf"Command drone Drone (\d+) to take off to an altitude of {_NUMBER} meters and then execute a hover command", "action_required", True),
        (r"Command drone Drone (\d+) to execute a hover command", "action_required", False),
        (r"Hover Drone (\d+) in place", "state_goal", False),
        (r"Command drone Drone (\d+) to hover in place", "state_goal", False),
    )
    for pattern, kind, takeoff in patterns:
        match = re.fullmatch(pattern, base, re.I)
        if not match:
            continue
        altitude = float(match.group(2)) if takeoff else None
        seconds = float(duration.group(2)) if duration and duration.group(2) else None
        if ((altitude is not None and (not _finite(altitude) or altitude <= 0))
                or (seconds is not None and (not _finite(seconds) or seconds <= 0))):
            return result
        result.update(kind="duration_unverified" if duration else kind,
            actor_name="Drone " + match.group(1), target_altitude=altitude,
            takeoff_required=takeoff, explicit_hover_required=kind == "action_required",
            duration_seconds=seconds, interpretation_complete=True)
        return result
    return result


def _ordered(event):
    return (isinstance(event, dict) and type(event.get("start_sequence")) is int
            and type(event.get("end_sequence")) is int
            and 0 < event["start_sequence"] < event["end_sequence"]
            and event.get("ordering") == "certain" and event.get("overlaps") == []
            and isinstance(event.get("evidence_id"), str) and bool(event["evidence_id"]))


def _actor(event, actor):
    args = event.get("args")
    if isinstance(args, dict):
        if args.get("drone_id") == actor:
            return True
        if isinstance(args.get("drone_ids"), list) and actor in args["drone_ids"]:
            return True
    return actor in event.get("entity_ids", []) if isinstance(event.get("entity_ids"), list) else False


def _receipt_matches(event, actor, tool):
    payload = event.get("payload")
    return (isinstance(payload, dict) and payload.get("drone_id") == actor
            and payload.get("command") == tool and payload.get("status") == "success"
            and not payload.get("error") and payload.get("success", True) is True
            and payload.get("partial_success", False) is False
            and payload.get("outcome", "completed") == "completed")


def _read_receipt(event):
    """The small v2 read allowlist uses actual public handler result shapes."""
    tool, args, payload = event.get("tool_name"), event.get("args"), event.get("payload")
    if (not isinstance(tool, str) or tool not in _ANCHOR_READS or event.get("scope") != "observation"
            or event.get("status") != "observed" or not isinstance(args, dict)):
        return False

    def clean(value):
        return (isinstance(value, dict) and not value.get("error")
                and value.get("success", True) is True
                and value.get("status") not in ("error", "failed", "unknown")
                and not any(k in value for k in ("tool_exception", "harness_transport_error")))

    def drone(value):
        return (clean(value) and isinstance(value.get("id"), str) and bool(value["id"])
                and isinstance(value.get("name"), str) and bool(value["name"])
                and isinstance(value.get("status"), str) and bool(value["status"])
                and isinstance(value.get("position"), dict)
                and all(_finite(value["position"].get(k)) for k in ("x", "y", "z"))
                and ("speed" not in value or _finite(value["speed"])))

    if tool == "list_drones":
        return (args == {} and isinstance(payload, list) and bool(payload)
                and all(drone(d) for d in payload)
                and len({d["id"] for d in payload}) == len(payload))
    if tool == "get_drone_status":
        return (set(args) == {"drone_id"} and drone(payload) and payload["id"] == args["drone_id"])
    return (args == {} and clean(payload) and isinstance(payload.get("weather"), str)
            and bool(payload["weather"]) and _finite(payload.get("wind_speed"))
            and payload["wind_speed"] >= 0 and _finite(payload.get("visibility"))
            and payload["visibility"] >= 0)


def _anchor_read_component(events, anchor, before):
    """Prove closed read-only overlap without rewriting ledger ordering.

    Ledger sequence numbers describe tool invocation intervals. Reconstruct
    overlaps and check their reciprocal IDs instead of trusting a bare flag.
    All connected reads must finish before takeoff. Action receipt ordering and
    final-state freshness retain their v1 rules.
    """
    def interval(event):
        start, end = event.get("start_sequence"), event.get("end_sequence")
        return (type(start) is int and type(end) is int and 0 < start < end
                and event.get("status") != "pending")

    by_id = {e["evidence_id"]: e for e in events}
    component, pending = set(), [anchor["evidence_id"]]
    while pending:
        ident = pending.pop()
        if ident in component:
            continue
        event = by_id[ident]
        if not interval(event) or event["end_sequence"] >= before:
            return None
        neighbors = set()
        for other in events:
            if other is event:
                continue
            start, end = other.get("start_sequence"), other.get("end_sequence")
            if interval(other):
                if {start, end} & {event["start_sequence"], event["end_sequence"]}:
                    return None  # A ledger sequence number denotes one boundary.
                if event["start_sequence"] < end and start < event["end_sequence"]:
                    neighbors.add(other["evidence_id"])
            elif not ((type(start) is int and start >= event["end_sequence"])
                      or (type(end) is int and 0 < end <= event["start_sequence"])):
                return None  # Missing boundaries cannot exclude an overlap.
            # Also detect a one-sided declared link whose intervals disagree.
            reverse = other.get("overlaps")
            if isinstance(reverse, list) and ident in reverse and other["evidence_id"] not in neighbors:
                return None
        declared = event.get("overlaps")
        if (not isinstance(declared, list) or any(not isinstance(i, str) for i in declared)
                or len(declared) != len(set(declared)) or set(declared) != neighbors
                or event.get("ordering") != ("uncertain" if neighbors else "certain")):
            return None
        component.add(ident)
        pending.extend(neighbors - component)
    reads = [e for e in events if e["evidence_id"] in component]
    # Serial anchors keep the v1 payload boundary. Extra receipt checks justify
    # the new parallel-read exemption only; unrelated calls gain no new gate.
    if len(reads) > 1 and any(not _read_receipt(e) for e in reads):
        return None
    return [e["evidence_id"] for e in sorted(reads, key=lambda e: e["start_sequence"])]


def _position_anchor(events, actor, name, before, version):
    candidates = [e for e in events if e.get("tool_name") in ("list_drones", "get_drone_status")
                  and type(e.get("start_sequence")) is int and e["start_sequence"] < before
                  and (e["tool_name"] == "list_drones" or _actor(e, actor))]
    if not candidates:
        return None
    event = max(candidates, key=lambda e: e["start_sequence"])
    refs = (_anchor_read_component(events, event, before) if version == "hover_completion_v2"
            else [event["evidence_id"]] if _ordered(event) else None)
    if refs is None or event.get("status") != "observed" or event["end_sequence"] >= before:
        return None
    payload = event.get("payload")
    if event["tool_name"] == "list_drones":
        if not isinstance(payload, list):
            return None
        matches = [d for d in payload if isinstance(d, dict) and (d.get("id") == actor or d.get("name") == name)]
        if len(matches) != 1:
            return None
        state = matches[0]
    else:
        state = payload
    if (not isinstance(state, dict) or state.get("id") != actor
            or state.get("name", name) != name or state.get("error")):
        return None
    point = state.get("position")
    if not isinstance(point, dict) or any(not _finite(point.get(k)) for k in ("x", "y", "z")):
        return None
    if version == "hover_completion_v2" and len(refs) > 1:
        for other in events:
            if other["evidence_id"] not in refs or other["tool_name"] == "get_weather":
                continue
            payload = other["payload"]
            if other["tool_name"] == "list_drones":
                matches = [d for d in payload if d.get("id") == actor or d.get("name") == name]
                if len(matches) != 1:
                    return None
                observed = matches[0]
            elif other["args"]["drone_id"] == actor:
                observed = payload
            else:
                continue
            if (observed.get("id") != actor or observed.get("name") != name
                    or any(abs(observed["position"][k] - point[k]) > TOLERANCE for k in ("x", "y", "z"))):
                return None
    if any(e.get("tool_name") in _ACTIONS and _actor(e, actor)
           and type(e.get("start_sequence")) is int and event["start_sequence"] < e["start_sequence"] < before
           for e in events):
        return None
    return event, point, refs


def assess_hover_completion(command, ledger_snapshot, contract="hover_completion_v1"):
    """Return a separate evidence verdict without changing run/checker semantics.

Unverified means insufficient or adverse evidence, not a fabricated task failure.
Freshness is relative to observed command-local events; concurrent external world
writers and elapsed physical holding time are outside this contract.
"""
    version = validate_completion_contract(contract)
    if version == "off":
        raise ValueError("Completion assessment requires an enabled contract")
    contract = parse_hover_contract(command)
    result = {"schema": SCHEMA if version == "hover_completion_v1" else "hover-completion-v2",
        "kind": contract["kind"], "contract": contract,
        "status": "unverified", "task_completed": None, "completion_claim_accepted": False,
        "reasons": [], "evidence_refs": [], "actor_id": None,
        "position_tolerance_meters": TOLERANCE, "speed_tolerance": TOLERANCE,
        "historical_action_effects_rewritten": False,
        "scope": "supported_command_public_evidence_only_not_general_task_or_elapsed_time_verification"}

    def reject(reason, *, status="unverified", refs=()):
        result.update(status=status, reasons=[reason], evidence_refs=list(dict.fromkeys(refs)))
        return result

    if contract["kind"] == "not_applicable":
        result["completion_claim_accepted"] = None
        return reject("outside_hover_contract", status="not_applicable")
    if contract["kind"] == "unsupported":
        return reject("hover_command_not_fully_supported", status="unsupported")
    if contract["kind"] == "duration_unverified":
        return reject("elapsed_hover_duration_not_verified_by_current_public_api")
    if not isinstance(ledger_snapshot, dict):
        return reject("command_local_ledger_unavailable")
    ledger = ledger_snapshot
    if ledger.get("command") != command:
        return reject("ledger_command_mismatch")
    if ledger.get("observer_errors") != []:
        return reject("ledger_observer_errors_or_unknown_integrity")
    bindings = ledger.get("entity_bindings")
    binding = bindings.get(contract["actor_name"]) if isinstance(bindings, dict) else None
    if not isinstance(binding, dict) or binding.get("state") != "exact":
        return reject("actor_binding_not_exact")
    bound = binding.get("entities")
    if (not isinstance(bound, list) or len(bound) != 1 or not isinstance(bound[0], dict)
            or bound[0].get("kind") != "drone" or not isinstance(bound[0].get("id"), str)
            or not bound[0]["id"]):
        return reject("actor_binding_not_unique_drone")
    actor = bound[0]["id"]
    result["actor_id"] = actor
    events = ledger.get("events")
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        return reject("ledger_events_malformed")
    ids = [e.get("evidence_id") for e in events]
    if any(not isinstance(eid, str) or not eid for eid in ids) or len(ids) != len(set(ids)):
        return reject("ledger_evidence_ids_invalid")
    relevant = [e for e in events if _actor(e, actor)]
    # Malformed/pending actor events cannot be hidden behind an older good read.
    if any(not isinstance(e.get("args"), dict)
           or type(e.get("start_sequence")) is not int or e["start_sequence"] <= 0
           or type(e.get("end_sequence")) is not int or e["end_sequence"] <= e["start_sequence"]
           or e.get("status") == "pending" for e in relevant):
        return reject("actor_event_pending_or_malformed")
    observations = [e for e in relevant if e.get("tool_name") == "get_drone_status"]
    if not observations:
        return reject("fresh_actor_status_required")
    latest = max(observations, key=lambda e: e["start_sequence"])
    refs = [latest["evidence_id"]]
    if (not _ordered(latest) or latest.get("status") != "observed"
            or latest.get("args", {}).get("drone_id") != actor):
        return reject("latest_status_not_complete_ordered_observation", refs=refs)
    if any(e.get("tool_name") in _ACTIONS and e["end_sequence"] >= latest["start_sequence"]
           for e in relevant):
        return reject("status_stale_after_or_overlapping_actor_action", refs=refs)
    state = latest.get("payload")
    if (not isinstance(state, dict) or state.get("id") != actor
            or state.get("name", contract["actor_name"]) != contract["actor_name"]
            or state.get("error") or state.get("success", True) is not True):
        return reject("status_actor_or_receipt_invalid", refs=refs)
    position = state.get("position")
    if (not isinstance(position, dict) or any(not _finite(position.get(k)) for k in ("x", "y", "z"))
            or position["z"] <= 0 or state.get("status") != "hovering"
            or ("speed" in state and (not _finite(state["speed"]) or abs(state["speed"]) > TOLERANCE))):
        return reject("hovering_airborne_stationary_state_not_confirmed", refs=refs)
    altitude = contract["target_altitude"]
    if altitude is not None and abs(position["z"] - altitude) > TOLERANCE:
        return reject("requested_altitude_not_confirmed", refs=refs)
    takeoff = None
    if contract["takeoff_required"]:
        attempts = [e for e in relevant if e.get("tool_name") == "take_off"]
        if not attempts:
            return reject("same_command_takeoff_attempt_required", refs=refs)
        takeoff = max(attempts, key=lambda e: e["start_sequence"])
        args = takeoff.get("args", {})
        takeoff_payload = takeoff.get("payload")
        unknown = (takeoff.get("status") == "unknown"
            and ((isinstance(takeoff_payload, dict)
                  and any(k in takeoff_payload for k in ("tool_exception", "harness_transport_error"))
                  and takeoff_payload.get("drone_id", actor) == actor)
                 or takeoff_payload == _RAW_TAKEOFF_TIMEOUT))
        if (not _ordered(takeoff) or args.get("drone_id") != actor
                or not _finite(args.get("altitude")) or abs(args["altitude"] - altitude) > TOLERANCE
                or takeoff["end_sequence"] >= latest["start_sequence"]
                or not (unknown or (takeoff.get("status") == "completed"
                    and _receipt_matches(takeoff, actor, "take_off")))):
            return reject("takeoff_attempt_not_ordered_matching_public_evidence", refs=refs)
        refs.insert(0, takeoff["evidence_id"])
        anchor = _position_anchor(events, actor, contract["actor_name"], takeoff["start_sequence"], version)
        if anchor is None:
            return reject("fresh_pre_takeoff_position_anchor_required", refs=refs)
        anchor_event, anchor_point, anchor_refs = anchor
        refs = anchor_refs + refs
        if any(abs(position[k] - anchor_point[k]) > TOLERANCE for k in ("x", "y")):
            return reject("in_place_position_changed_from_pre_takeoff_anchor", refs=refs)
        # This exact grammar requests an in-place takeoff, not subsequent travel.
        if any(e.get("tool_name") in _MOVEMENT and e is not takeoff
               and e["start_sequence"] > takeoff["start_sequence"] for e in relevant):
            return reject("in_place_goal_has_intervening_movement", refs=refs)
    if contract["explicit_hover_required"]:
        hover_events = [e for e in relevant if e.get("tool_name") == "hover"]
        if not hover_events:
            return reject("explicit_hover_action_receipt_required", refs=refs)
        hover = max(hover_events, key=lambda e: e["start_sequence"])
        if (not _ordered(hover) or hover.get("status") != "completed"
                or hover.get("args", {}).get("drone_id") != actor
                or not _receipt_matches(hover, actor, "hover")
                or hover["end_sequence"] >= latest["start_sequence"]
                or (takeoff is not None and takeoff["end_sequence"] >= hover["start_sequence"])):
            return reject("explicit_hover_not_confirmed_in_required_order", refs=refs)
        refs.insert(-1, hover["evidence_id"])
    result.update(status="verified", task_completed=True, completion_claim_accepted=True,
        reasons=["public_hover_goal_and_required_action_evidence_verified"], evidence_refs=refs)
    return result


def completion_rules(command, contract="hover_completion_v1"):
    """Brief model-visible contract text, with no hidden checks or raw receipts."""
    if validate_completion_contract(contract) == "off":
        return ""
    contract = parse_hover_contract(command)
    kind = contract["kind"]
    if kind == "not_applicable":
        return ""
    if kind == "unsupported":
        return ("Hover completion contract: this full instruction is outside the supported grammar. "
                "Keep all original obligations; do not claim contract-verified completion.")
    if kind == "duration_unverified":
        return ("Hover completion contract: duration parameters and immediate success receipts do not "
                "verify elapsed holding time in this simulator. Do not claim the requested duration was verified.")
    action = ("An explicitly requested hover command needs its own successful receipt in the requested order. "
              if contract["explicit_hover_required"] else
              "This is a state goal; an already verified hovering state does not require another hover command. ")
    takeoff = ("The requested takeoff must have a matching attempt in this command before the final observation. "
               "Observe its starting XY before takeoff and confirm the same XY afterward for in-place hovering. "
               if contract["takeoff_required"] else "")
    return ("Hover completion contract: " + action + takeoff
            + "Resolve the exact drone and use a fresh public get_drone_status after its last action to verify "
            "airborne hovering, requested altitude, and zero speed when reported. A [TASK DONE] marker alone "
            "is not evidence. After unknown effects, inspect state; do not replay an uncertain action or "
            "rewrite its historical effect as success. No elapsed-duration claim is established here.")
