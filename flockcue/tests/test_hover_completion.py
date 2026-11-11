"""Narrow public hover goals preserve uncertainty and explicit action semantics."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
from flockcue.runtime.hover import assess_hover_completion, completion_rules, parse_hover_contract, validate_completion_contract
from flockcue.evidence.task_progress import TaskProgressLedger

STATE = "Command drone Drone 4 to take off to an altitude of 12 meters and hover in place."
ACTION = "Command drone Drone 4 to take off to an altitude of 12 meters and then execute a hover command."
HOVER = "Command drone Drone 4 to execute a hover command."
PURE = "Hover Drone 4 in place."
DRONE = "actor4"


def status(**changes):
    return {"id": DRONE, "name": "Drone 4", "status": "hovering",
            "position": {"x": 10, "y": 20, "z": 12}, "speed": 0, **changes}


def receipt(tool="take_off", **changes):
    return {"command": tool, "drone_id": DRONE, "status": "success", "outcome": "completed", **changes}


def ledger(command=STATE, *, unknown=False, explicit=False):
    l = TaskProgressLedger(command, "current-command")
    l.observe_tool("list_drones", {}, [status(status="idle", position={"x": 10, "y": 20, "z": 0})])
    if command not in (PURE, HOVER):
        if unknown:
            eid = l.begin_tool("take_off", {"drone_id": DRONE, "altitude": 12})
            l.end_tool(eid, error="Request timed out before response was received")
        else:
            l.observe_tool("take_off", {"drone_id": DRONE, "altitude": 12}, receipt())
    if explicit:
        l.observe_tool("hover", {"drone_id": DRONE}, receipt("hover"))
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status())
    return l


def assess(l):
    return assess_hover_completion(l.command, l.snapshot())


@pytest.mark.parametrize("value", [None, True, "hover_completion_v3", {}, ""])
def test_contract_selector_rejects_implicit_or_unknown_values(value):
    with pytest.raises(ValueError):
        validate_completion_contract(value)


@pytest.mark.parametrize("value", ["off", "hover_completion_v1", "hover_completion_v2"])
def test_contract_selector(value):
    assert validate_completion_contract(value) == value


@pytest.mark.parametrize("command,kind", [(STATE, "state_goal"), (ACTION, "action_required"),
    (HOVER, "action_required"), (PURE, "state_goal"), ("Command drone Drone 4 to hover in place.", "state_goal"),
    (STATE[:-1] + " for 5 seconds.", "duration_unverified"),
    (PURE[:-1] + " briefly.", "duration_unverified"),
    (ACTION[:-1] + " for 2.5 seconds.", "duration_unverified"),
    (STATE + " Then visit Target 1.", "unsupported"), ("Hover Drone 4 and Drone 5.", "unsupported"),
    ("让 Drone 4 悬停。", "unsupported"), ("Take a photo.", "not_applicable"),
    (STATE.replace("12", "0"), "unsupported"), (PURE[:-1] + " for 0 seconds.", "unsupported"),
    (PURE + "...", "unsupported")])
def test_parser_matches_whole_supported_instruction(command, kind):
    assert parse_hover_contract(command)["kind"] == kind


def test_whitespace_and_case_are_normalized_but_task_snapshot_identity_is_exact():
    cmd = "  hover Drone 4   in place.  "
    l = ledger(cmd)
    assert parse_hover_contract(cmd)["actor_name"] == "Drone 4"
    assert assess(l)["status"] == "verified"
    assert assess_hover_completion(PURE, l.snapshot())["reasons"] == ["ledger_command_mismatch"]


def test_state_goal_accepts_takeoff_then_fresh_hovering_without_redundant_action():
    l = ledger(); before = l.snapshot()
    result = assess(l)
    assert result["task_completed"] is result["completion_claim_accepted"] is True
    assert result["status"] == "verified"
    assert not any(e["tool_name"] == "hover" for e in before["events"])
    assert result["evidence_refs"] == [e["evidence_id"] for e in before["events"]]
    assert l.snapshot() == before
    assert "payload" not in json.dumps(result) and "battery" not in json.dumps(result)


def test_existing_hovering_state_satisfies_pure_goal_without_any_action():
    l = ledger(PURE)
    assert assess(l)["status"] == "verified"
    assert [e["tool_name"] for e in l.snapshot()["events"]] == ["list_drones", "get_drone_status"]


@pytest.mark.parametrize("command", [ACTION, HOVER])
def test_explicit_hover_is_not_satisfied_by_status_alone(command):
    result = assess(ledger(command))
    assert result["status"] == "unverified" and result["task_completed"] is None
    assert result["completion_claim_accepted"] is False
    assert result["reasons"] == ["explicit_hover_action_receipt_required"]
    assert assess(ledger(command, explicit=True))["status"] == "verified"


def test_unknown_takeoff_can_have_state_goal_verified_without_rewriting_effect():
    l = ledger(unknown=True); before = l.snapshot()
    result = assess(l)
    assert result["status"] == "verified" and not result["historical_action_effects_rewritten"]
    assert l.snapshot() == before and before["events"][1]["status"] == "unknown"
    assert assess(ledger(ACTION, unknown=True, explicit=True))["status"] == "verified"


@pytest.mark.parametrize("command,explicit", [(STATE, False), (ACTION, True)])
def test_raw_lost_takeoff_response_can_be_followed_by_independent_state_proof(command, explicit):
    l = TaskProgressLedger(command, "current-command")
    l.observe_tool("list_drones", {}, [status(status="idle", position={"x": 10, "y": 20, "z": 0})])
    l.observe_tool("take_off", {"drone_id": DRONE, "altitude": 12},
                   "Error during takeoff: Request timed out before response was received")
    assert assess(l)["status"] == "unverified"  # Timeout text alone proves no final state.
    if explicit:
        l.observe_tool("hover", {"drone_id": DRONE}, receipt("hover"))
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status())
    before = l.snapshot()
    result = assess(l)
    assert result["status"] == "verified" and result["task_completed"] is True
    assert result["historical_action_effects_rewritten"] is False
    assert before["events"][1]["status"] == "unknown"
    assert l.snapshot() == before


@pytest.mark.parametrize("payload", [
    "Error during takeoff: invalid altitude",
    "Error during takeoff: 'str' object has no attribute 'get'",
    "Error parsing JSON input: invalid input",
    "Error during takeoff: timed out",
    "Request timed out before response was received",
    "Error during hover: Request timed out before response was received",
    "Error during takeoff: Request timed out before response was received; success",
    "arbitrary unknown response",
    {"error": "Request timed out before response was received"},
    {"tool_exception": "Request timed out before response was received", "drone_id": "other"},
])
def test_unknown_takeoff_errors_outside_known_transport_forms_stay_unverified(payload):
    s = ledger(unknown=True).snapshot()
    s["events"][1]["payload"] = payload
    result = assess_hover_completion(STATE, s)
    assert result["status"] == "unverified" and result["task_completed"] is None
    assert result["reasons"] == ["takeoff_attempt_not_ordered_matching_public_evidence"]


@pytest.mark.parametrize("payload", [
    "Error during hover: Request timed out before response was received",
    "Error during takeoff: Request timed out before response was received",
])
def test_raw_unknown_hover_is_not_a_confirmed_explicit_action(payload):
    s = ledger(ACTION, explicit=True).snapshot()
    s["events"][2].update(status="unknown", payload=payload)
    before = copy.deepcopy(s)
    result = assess_hover_completion(ACTION, s)
    assert result["status"] == "unverified" and result["completion_claim_accepted"] is False
    assert result["reasons"] == ["explicit_hover_not_confirmed_in_required_order"]
    assert s == before


@pytest.mark.parametrize("damage", ["missing_ledger", "command", "observer_error", "observer_missing",
    "ambiguous", "multiple_actor", "wrong_kind", "no_events", "duplicate_evidence", "bad_event",
    "no_status", "pending", "overlap", "uncertain", "read_error", "latest_error", "status_actor",
    "status_name", "request_actor", "wrong_altitude", "ground", "moving", "speed", "nan", "bool_position",
    "missing_position", "missing_status", "takeoff_actor", "takeoff_altitude", "takeoff_uncertain",
    "takeoff_error", "takeoff_receipt_actor", "takeoff_receipt_command", "no_takeoff", "bad_args",
    "string_success"])
@pytest.mark.parametrize("version", ["hover_completion_v1", "hover_completion_v2"])
def test_missing_ambiguous_stale_or_wrong_public_evidence_never_verifies(damage, version):
    s = ledger().snapshot(); final = s["events"][-1]; takeoff = s["events"][1]
    if damage == "missing_ledger": s = None
    elif damage == "command": s["command"] = "other"
    elif damage == "observer_error": s["observer_errors"] = ["dropped receipt"]
    elif damage == "observer_missing": s.pop("observer_errors")
    elif damage == "ambiguous": s["entity_bindings"]["Drone 4"]["state"] = "ambiguous"
    elif damage == "multiple_actor": s["entity_bindings"]["Drone 4"]["entities"] *= 2
    elif damage == "wrong_kind": s["entity_bindings"]["Drone 4"]["entities"][0]["kind"] = "target"
    elif damage == "no_events": s["events"] = []
    elif damage == "duplicate_evidence": final["evidence_id"] = takeoff["evidence_id"]
    elif damage == "bad_event": s["events"].append(None)
    elif damage == "no_status": s["events"].pop()
    elif damage == "pending": final.update(status="pending", end_sequence=None)
    elif damage == "overlap": final["overlaps"] = ["other"]
    elif damage == "uncertain": final["ordering"] = "uncertain"
    elif damage == "read_error": final["status"] = "error"
    elif damage == "latest_error":
        s["events"].append({**copy.deepcopy(final), "evidence_id": "e100", "start_sequence": 100, "end_sequence": 101, "status": "error", "payload": "Error"})
    elif damage == "status_actor": final["payload"]["id"] = "other"
    elif damage == "status_name": final["payload"]["name"] = "Drone 40"
    elif damage == "request_actor": final["args"]["drone_id"] = "other"
    elif damage == "wrong_altitude": final["payload"]["position"]["z"] = 13
    elif damage == "ground": final["payload"]["position"]["z"] = 0
    elif damage == "moving": final["payload"]["status"] = "moving"
    elif damage == "speed": final["payload"]["speed"] = 1
    elif damage == "nan": final["payload"]["position"]["x"] = float("nan")
    elif damage == "bool_position": final["payload"]["position"]["z"] = True
    elif damage == "missing_position": final["payload"].pop("position")
    elif damage == "missing_status": final["payload"].pop("status")
    elif damage == "takeoff_actor": takeoff["args"]["drone_id"] = "other"
    elif damage == "takeoff_altitude": takeoff["args"]["altitude"] = 11
    elif damage == "takeoff_uncertain": takeoff["ordering"] = "uncertain"
    elif damage == "takeoff_error": takeoff.update(status="error", payload={"status": "error"})
    elif damage == "takeoff_receipt_actor": takeoff["payload"]["drone_id"] = "other"
    elif damage == "takeoff_receipt_command": takeoff["payload"]["command"] = "hover"
    elif damage == "no_takeoff": s["events"].pop(1)
    elif damage == "bad_args": final["args"] = []
    elif damage == "string_success": final["payload"]["success"] = "false"
    result = assess_hover_completion(STATE, s, contract=version)
    assert result["status"] == "unverified" and result["task_completed"] is None
    assert result["completion_claim_accepted"] is False


@pytest.mark.parametrize("tool", ["land", "take_off", "hover", "move_to", "rotate", "take_photo"])
def test_every_later_actor_action_invalidates_older_status(tool):
    l = ledger()
    l.observe_tool(tool, {"drone_id": DRONE}, receipt(tool))
    assert assess(l)["reasons"] == ["status_stale_after_or_overlapping_actor_action"]


def test_intervening_movement_cannot_be_disguised_by_final_matching_altitude():
    l = ledger()
    l.observe_tool("move_to", {"drone_id": DRONE, "x": 99, "y": 99, "z": 12}, receipt("move_to"))
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status(position={"x": 99, "y": 99, "z": 12}))
    assert assess(l)["status"] == "unverified"


@pytest.mark.parametrize("damage", ["missing", "xy_drift", "ambiguous", "uncertain", "read_error", "nan", "after_takeoff", "movement"])
def test_in_place_requires_valid_same_actor_pre_takeoff_xy_anchor(damage):
    s = ledger().snapshot(); anchor = s["events"][0]
    if damage == "missing": s["events"].pop(0)
    elif damage == "xy_drift": s["events"][-1]["payload"]["position"]["x"] += 2
    elif damage == "ambiguous": anchor["payload"].append(copy.deepcopy(anchor["payload"][0]))
    elif damage == "uncertain": anchor["ordering"] = "uncertain"
    elif damage == "read_error": anchor["status"] = "error"
    elif damage == "nan": anchor["payload"][0]["position"]["x"] = float("nan")
    elif damage == "after_takeoff": anchor.update(start_sequence=100, end_sequence=101)
    else:
        # Public motion after the anchor invalidates it even if takeoff is later.
        s["events"][1]["start_sequence"] = 5; s["events"][1]["end_sequence"] = 6
        s["events"][-1]["start_sequence"] = 7; s["events"][-1]["end_sequence"] = 8
        s["events"].append({"evidence_id": "motion", "tool_name": "move_to", "args": {"drone_id": DRONE},
            "entity_ids": [DRONE], "start_sequence": 3, "end_sequence": 4, "ordering": "certain", "overlaps": [],
            "status": "completed", "payload": receipt("move_to")})
    assert assess_hover_completion(STATE, s)["status"] == "unverified"


def test_status_query_can_supply_pre_takeoff_xy_anchor():
    l = TaskProgressLedger(STATE, "current")
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status(status="idle", position={"x": 10, "y": 20, "z": 0}))
    l.observe_tool("take_off", {"drone_id": DRONE, "altitude": 12}, receipt())
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status())
    assert assess(l)["status"] == "verified"


def test_other_actor_actions_do_not_invalidate_resolved_drone():
    l = ledger()
    l.observe_tool("land", {"drone_id": "other"}, receipt("land", drone_id="other"))
    assert assess(l)["status"] == "verified"


@pytest.mark.parametrize("damage", ["unknown", "false_success", "receipt_actor", "receipt_command", "before_takeoff", "overlap", "pending"])
def test_explicit_hover_requires_confirmed_ordered_receipt(damage):
    s = ledger(ACTION, explicit=True).snapshot(); hover = s["events"][2]
    if damage == "unknown": hover.update(status="unknown", payload={"tool_exception": "timeout"})
    elif damage == "false_success": hover["payload"]["success"] = False
    elif damage == "receipt_actor": hover["payload"]["drone_id"] = "other"
    elif damage == "receipt_command": hover["payload"]["command"] = "take_off"
    elif damage == "before_takeoff":
        s["events"][1]["start_sequence"], hover["start_sequence"] = hover["start_sequence"], s["events"][1]["start_sequence"]
        s["events"][1]["end_sequence"], hover["end_sequence"] = hover["end_sequence"], s["events"][1]["end_sequence"]
    elif damage == "overlap": hover["overlaps"] = ["other"]
    else: hover.update(status="pending", end_sequence=None)
    assert assess_hover_completion(ACTION, s)["status"] == "unverified"


def test_duration_is_not_verified_even_by_echoed_duration_and_success_history():
    command = PURE[:-1] + " for 5 seconds."
    l = ledger(command, explicit=True)
    s = l.snapshot(); s["events"][2]["payload"]["duration"] = 5
    result = assess_hover_completion(command, s)
    assert result["kind"] == "duration_unverified" and result["task_completed"] is None
    assert result["completion_claim_accepted"] is False
    assert "elapsed" in result["reasons"][0]


def test_not_applicable_is_distinct_from_unsupported_hover_and_no_hidden_api():
    out = assess_hover_completion("Take a photo.", None)
    assert out["status"] == "not_applicable" and out["completion_claim_accepted"] is None
    out = assess_hover_completion(STATE + " Then visit all targets.", None)
    assert out["status"] == "unsupported" and out["completion_claim_accepted"] is False
    assert completion_rules("Take a photo.") == ""
    assert "already verified hovering state" in completion_rules(STATE)
    assert "own successful receipt" in completion_rules(ACTION)
    assert "elapsed holding time" in completion_rules(PURE[:-1] + " for 5 seconds.")


V2 = "hover_completion_v2"
WEATHER = {"weather": "clear", "wind_speed": 6.2, "visibility": 8000.0}


def parallel_reads(command=STATE, *, anchor="list_drones", weather_ends_first=False, unknown=False):
    l = TaskProgressLedger(command, "parallel-read-command")
    initial = status(status="idle", position={"x": 10, "y": 20, "z": 0})
    first = l.begin_tool(anchor, {} if anchor == "list_drones" else {"drone_id": DRONE})
    weather = l.begin_tool("get_weather", {})
    calls = [(first, [initial] if anchor == "list_drones" else initial), (weather, WEATHER)]
    for ident, payload in reversed(calls) if weather_ends_first else calls:
        l.end_tool(ident, payload)
    l.observe_tool("take_off", {"drone_id": DRONE, "altitude": 12},
                   "Error during takeoff: Request timed out before response was received" if unknown else receipt())
    if command == ACTION:
        l.observe_tool("hover", {"drone_id": DRONE}, receipt("hover"))
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status())
    return l


@pytest.mark.parametrize("anchor", ["list_drones", "get_drone_status"])
@pytest.mark.parametrize("weather_ends_first", [False, True])
def test_v2_accepts_only_proven_closed_read_overlap_without_changing_ledger(anchor, weather_ends_first):
    l = parallel_reads(anchor=anchor, weather_ends_first=weather_ends_first)
    before = l.snapshot()
    old = assess_hover_completion(STATE, before)
    assert old["status"] == "unverified" and old["reasons"] == ["fresh_pre_takeoff_position_anchor_required"]
    new = assess_hover_completion(STATE, before, contract=V2)
    assert new["schema"] == "hover-completion-v2" and new["status"] == "verified"
    assert new["evidence_refs"] == [e["evidence_id"] for e in before["events"]]
    assert l.snapshot() == before and before["events"][0]["ordering"] == "uncertain"


@pytest.mark.parametrize("damage", ["unknown_tool", "sense_tool", "action_tool", "scope", "bad_args",
    "missing_id", "duplicate_id", "self_id", "one_sided", "missing_overlaps", "hidden_overlap",
    "certain_with_overlap", "missing_ordering", "nonoverlapping_reference", "duplicate_boundary",
    "pending", "inverted_interval", "noninteger_interval", "error_status", "unknown_status",
    "string_receipt", "error_receipt", "exception_receipt", "false_success", "missing_weather",
    "nan_weather", "negative_weather", "anchor_receipt_error", "anchor_missing_xyz", "anchor_actor",
    "anchor_duplicate_actor", "xy_drift"])
def test_v2_rejects_unproven_or_inconsistent_parallel_read_evidence(damage):
    s = parallel_reads().snapshot(); anchor, other = s["events"][:2]
    if damage == "unknown_tool": other["tool_name"] = "unknown_read"
    elif damage == "sense_tool": other["tool_name"] = "sense_nearby_entities"
    elif damage == "action_tool": other["tool_name"] = "rotate"
    elif damage == "scope": other["scope"] = "action_receipt"
    elif damage == "bad_args": other["args"] = {"unexpected": 1}
    elif damage == "missing_id": anchor["overlaps"] = ["missing"]
    elif damage == "duplicate_id": anchor["overlaps"] *= 2
    elif damage == "self_id": anchor["overlaps"].append(anchor["evidence_id"])
    elif damage == "one_sided": other["overlaps"] = []
    elif damage == "missing_overlaps": other.pop("overlaps")
    elif damage == "hidden_overlap":
        for e in (anchor, other): e.update(ordering="certain", overlaps=[])
    elif damage == "certain_with_overlap": other["ordering"] = "certain"
    elif damage == "missing_ordering": other.pop("ordering")
    elif damage == "nonoverlapping_reference": other.update(start_sequence=100, end_sequence=101)
    elif damage == "duplicate_boundary": other["end_sequence"] = anchor["end_sequence"]
    elif damage == "pending": other.update(status="pending", end_sequence=None)
    elif damage == "inverted_interval": other["end_sequence"] = 1
    elif damage == "noninteger_interval": other["start_sequence"] = 2.0
    elif damage == "error_status": other["status"] = "error"
    elif damage == "unknown_status": other["status"] = "unknown"
    elif damage == "string_receipt": other["payload"] = "clear"
    elif damage == "error_receipt": other["payload"]["error"] = "failed"
    elif damage == "exception_receipt": other["payload"]["tool_exception"] = "timeout"
    elif damage == "false_success": other["payload"]["success"] = False
    elif damage == "missing_weather": other["payload"].pop("weather")
    elif damage == "nan_weather": other["payload"]["wind_speed"] = float("nan")
    elif damage == "negative_weather": other["payload"]["visibility"] = -1
    elif damage == "anchor_receipt_error": anchor["payload"][0]["error"] = "failed"
    elif damage == "anchor_missing_xyz": anchor["payload"][0]["position"].pop("x")
    elif damage == "anchor_actor": anchor["payload"][0]["id"] = "other"
    elif damage == "anchor_duplicate_actor": anchor["payload"].append(copy.deepcopy(anchor["payload"][0]))
    else: s["events"][-1]["payload"]["position"]["x"] += 1
    result = assess_hover_completion(STATE, s, contract=V2)
    assert result["status"] == "unverified" and result["completion_claim_accepted"] is False


@pytest.mark.parametrize("extra_tool", ["get_weather", "rotate", "unknown_tool"])
def test_v2_checks_the_whole_connected_overlap_component(extra_tool):
    l = TaskProgressLedger(STATE, "connected-read-command")
    first = l.begin_tool("list_drones", {})
    weather = l.begin_tool("get_weather", {})
    l.end_tool(first, [status(status="idle", position={"x": 10, "y": 20, "z": 0})])
    extra = l.begin_tool(extra_tool, {} if extra_tool != "rotate" else {"drone_id": "other"})
    l.end_tool(weather, WEATHER)
    l.end_tool(extra, WEATHER if extra_tool == "get_weather" else receipt("rotate", drone_id="other"))
    l.observe_tool("take_off", {"drone_id": DRONE, "altitude": 12}, receipt())
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status())
    result = assess_hover_completion(STATE, l.snapshot(), contract=V2)
    assert (result["status"] == "verified") is (extra_tool == "get_weather")


@pytest.mark.parametrize("takeoff_first", [False, True])
@pytest.mark.parametrize("unknown", [False, True])
def test_v2_never_falls_back_to_older_anchor_when_takeoff_overlaps_new_list(takeoff_first, unknown):
    l = TaskProgressLedger(STATE, "overlapping-action-command")
    l.observe_tool("list_drones", {}, [status(status="idle", position={"x": 10, "y": 20, "z": 0})])
    calls = [("take_off", {"drone_id": DRONE, "altitude": 12}), ("list_drones", {})]
    ids = [(tool, l.begin_tool(tool, args)) for tool, args in (calls if takeoff_first else reversed(calls))]
    for tool, ident in ids:
        if tool == "list_drones": l.end_tool(ident, [status()])
        elif unknown: l.end_tool(ident, error="Request timed out before response was received")
        else: l.end_tool(ident, receipt())
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status())
    result = assess_hover_completion(STATE, l.snapshot(), contract=V2)
    assert result["status"] == "unverified"
    assert result["reasons"] == ["takeoff_attempt_not_ordered_matching_public_evidence"]


@pytest.mark.parametrize("command", [STATE, ACTION])
def test_v2_preserves_unknown_takeoff_history_and_explicit_hover_requirement(command):
    l = parallel_reads(command, unknown=True); before = l.snapshot()
    result = assess_hover_completion(command, before, contract=V2)
    assert result["status"] == "verified" and before["events"][2]["status"] == "unknown"
    assert l.snapshot() == before
    if command == ACTION:
        before["events"][3].update(status="unknown", payload={"tool_exception": "timeout"})
        assert assess_hover_completion(command, before, contract=V2)["status"] == "unverified"


def test_v2_does_not_relax_final_status_overlap():
    l = ledger()
    final = l.begin_tool("get_drone_status", {"drone_id": DRONE})
    weather = l.begin_tool("get_weather", {})
    l.end_tool(final, status()); l.end_tool(weather, WEATHER)
    result = assess_hover_completion(STATE, l.snapshot(), contract=V2)
    assert result["reasons"] == ["latest_status_not_complete_ordered_observation"]


@pytest.mark.parametrize("damage", ["serial_short_receipt", "serial_missing_scope", "unrelated_read_metadata"])
def test_v2_preserves_serial_anchor_boundaries_and_ignores_unrelated_metadata(damage):
    s = ledger().snapshot()
    if damage == "serial_short_receipt":
        for key in ("name", "status"): s["events"][0]["payload"][0].pop(key)
    elif damage == "serial_missing_scope": s["events"][0].pop("scope")
    else:
        s["events"].append({"evidence_id": "unrelated", "tool_name": "other_read", "args": {},
            "entity_ids": [], "start_sequence": 100, "end_sequence": 101, "status": "error",
            "ordering": "uncertain", "overlaps": ["unknown"], "payload": "Error"})
    old = assess_hover_completion(STATE, s)
    new = assess_hover_completion(STATE, s, contract=V2)
    assert old["status"] == new["status"] == "verified"


@pytest.mark.parametrize("drift", [0, 1])
def test_parallel_drone_reads_must_agree_on_the_same_actor_position(drift):
    l = TaskProgressLedger(STATE, "parallel-drone-reads")
    first = l.begin_tool("list_drones", {})
    second = l.begin_tool("get_drone_status", {"drone_id": DRONE})
    l.end_tool(first, [status(status="idle", position={"x": 10, "y": 20, "z": 0})])
    l.end_tool(second, status(status="idle", position={"x": 10 + drift, "y": 20, "z": 0}))
    l.observe_tool("take_off", {"drone_id": DRONE, "altitude": 12}, receipt())
    l.observe_tool("get_drone_status", {"drone_id": DRONE}, status(position={"x": 10 + drift, "y": 20, "z": 12}))
    result = assess_hover_completion(STATE, l.snapshot(), contract=V2)
    assert (result["status"] == "verified") is (drift == 0)


@pytest.mark.parametrize("command", [STATE, ACTION, PURE, PURE[:-1] + " for 5 seconds.",
                                      STATE + " Then visit Target 1.", "Take a photo."])
def test_v2_adds_no_prompt_text_and_does_not_expand_grammar(command):
    assert completion_rules(command, contract=V2) == completion_rules(command)
    assert completion_rules(command, contract="off") == ""
    if command not in (STATE, ACTION, PURE):
        old = assess_hover_completion(command, None)
        new = assess_hover_completion(command, None, contract=V2)
        assert {k:v for k,v in old.items() if k != "schema"} == {k:v for k,v in new.items() if k != "schema"}


def test_real_parallel_weather_receipt_passes_v2_while_all_frozen_v1_results_stay_unchanged():
    run = Path(__file__).resolve().parents[2] / "docs/results/deepseek-hover-pilot-20261103/run-01"
    old_source = run / "source/agent4drone/hover_completion.py"
    namespace = {"__name__": "frozen_hover_v1_regression", "__file__": str(old_source)}
    exec(compile(old_source.read_text(), str(old_source), "exec"), namespace)
    manifest = json.loads((run / "manifest.json").read_text())
    for trial in manifest["trials"]:
        path = run / "trials" / trial["trial_id"] / "policy/task-state.json"
        raw = path.read_bytes(); snapshot = json.loads(raw)
        frozen = namespace["assess_hover_completion"](snapshot["command"], snapshot)
        assert assess_hover_completion(snapshot["command"], snapshot) == frozen
        assert assess_hover_completion(snapshot["command"], snapshot, contract="hover_completion_v1") == frozen
        if trial["trial_id"] == "state_hover-candidate":
            assert frozen["reasons"] == ["fresh_pre_takeoff_position_anchor_required"]
            new = assess_hover_completion(snapshot["command"], snapshot, contract=V2)
            assert new["status"] == "verified" and new["evidence_refs"] == ["e1", "e2", "e7", "e11"]
        else:
            new = assess_hover_completion(snapshot["command"], snapshot, contract=V2)
            assert new["status"] == frozen["status"] and new["task_completed"] is frozen["task_completed"]
        assert path.read_bytes() == raw


def test_v2_preserves_all_sixteen_previous_local_acceptance_verdicts():
    root = Path(__file__).resolve().parents[2] / "docs/results/hover-input-contract-20261103/local-03/runs"
    paths = list(root.rglob("policy/task-state.json"))
    assert len(paths) == 16
    for path in paths:
        raw = path.read_bytes(); snapshot = json.loads(raw)
        old = assess_hover_completion(snapshot["command"], snapshot)
        new = assess_hover_completion(snapshot["command"], snapshot, contract=V2)
        assert {k:v for k,v in old.items() if k != "schema"} == {k:v for k,v in new.items() if k != "schema"}
        assert path.read_bytes() == raw


@pytest.mark.parametrize("version", ["off", "hover_completion_v3", None])
def test_assessment_rejects_disabled_or_unknown_versions(version):
    with pytest.raises(ValueError):
        assess_hover_completion(STATE, ledger().snapshot(), contract=version)
