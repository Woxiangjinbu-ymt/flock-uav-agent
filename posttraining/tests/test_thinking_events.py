"""Public-prefix fixtures for thinking-events-v1, independent of teacher labels."""
import builtins
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
from thinking_events.events import extract_events


def call(seq, tool, args, result):
    return {"call_id": f"call-{seq}", "tool": tool, "arguments": args, "result": result,
            "sequence": seq, "wire_message_index": seq + 2}


def evaluate(*batches, task="Fly the mission.", **fields):
    return extract_events({"task": task, "turn": len(batches) + 1,
                           "batches": list(batches), "gaps": [], **fields})


def types(result):
    return [event["type"] for event in result["events"]]


def drone(ident="a", name="Drone 1", z=0, x=5):
    return {"id": ident, "name": name, "status": "hovering" if z else "idle",
            "position": {"x": x, "y": 9, "z": z}, "speed": 0}


def action(seq, tool, args, result=None):
    args = {"drone_id": "a", **args}
    success = {"drone_id": args["drone_id"], "status": "success", "command": tool}
    return call(seq, tool, {"input_json": json.dumps(args)}, success if result is None else result)


def status(seq, ident="a", z=12, x=5):
    state = drone(ident, "Drone 1" if ident == "a" else "Drone 2", z, x)
    return call(seq, "get_drone_status", {"drone_id": ident}, state)


def test_start_text_only_and_missing_batch_are_distinct():
    assert evaluate()["event_state"] == "start"
    assert evaluate([])["event_state"] == "E0"
    assert evaluate([], gaps=["missing:call"])["event_state"] == "indeterminate"
    assert extract_events({"task": "Go", "turn": 2, "batches": []})["event_state"] == "indeterminate"


@pytest.mark.parametrize(("result", "expected"), [
    ({"status": "error", "reason": "path_blocked", "drone_id": "a"}, "E1"),
    ({"status": "error", "message": "battery low"}, "E4"),
    ("Error during takeoff: Request timed out before response was received", "E2"),
    ("Error invoking tool 'move_to' with kwargs {} with error: connection reset", "E2"),
    ({"tool_exception": "TimeoutError"}, "E2"),
    ({"tool_exception": "ValidationError: 1 validation error"}, "E4"),
    ("Error invoking tool 'move_to' with kwargs {} with error: input_json: Field required", "E4"),
    ({"status": "partial_success"}, "E3"),
    ({"status": "success", "drone_id": "b"}, "E2"),
    ({"status": "success", "message": "No error"}, None),
])
def test_action_cause_is_specific_and_unique(result, expected):
    output = evaluate([action(1, "move_to", {}, result)])
    assert types(output) == ([expected] if expected else [])
    assert output["event_state"] == ("event" if expected else "E0")


@pytest.mark.parametrize("layer", ["outer", "inner", "last_move"])
def test_wrong_aircraft_in_composite_motion(layer):
    navigation = {"outcome": "completed", "arrival_verified": True, "success": True, "drone_id": "a",
                  "last_move_result": {"drone_id": "a", "status": "success"}}
    payload = {"drone_id": "a", "navigation_result": navigation,
               "nearby": {"targets": [{"id": "t"}]}}
    {"outer": payload, "inner": navigation, "last_move": navigation["last_move_result"]}[layer]["drone_id"] = "b"
    output = evaluate([action(1, "navigate_to_and_sense", {}, payload)])
    assert types(output) == ["E2"]
    assert output["events"][0]["actor_id"] == "a"


def test_unknown_then_later_observation_preserves_time_and_mixed_gaps():
    first = [action(4, "take_off", {"altitude": 12}, {"harness_transport_error": "TimeoutError"})]
    earlier = evaluate(first, gaps=["missing:other"])
    assert types(earlier) == ["E2"]
    assert earlier["gaps"] == ["missing:other"]
    assert types(evaluate(first, [status(6)])) == ["E5"]
    assert earlier == evaluate(first, gaps=["missing:other"])


def test_subprogram_only_uses_returned_components():
    def sub(state, outcome="needs_replan", **kwargs):
        return [call(8, "run_harness", {}, {"status": outcome, "state": state, **kwargs})]
    assert evaluate(sub({}))["event_state"] == "indeterminate"
    assert types(evaluate(sub({"remaining_waypoints": [{"x": 3}]}))) == ["E3"]
    assert types(evaluate(sub({"remaining_waypoints": []}, "completed"))) == []
    assert types(evaluate(sub({}, "unsupported", reason="unsupported_skill_or_full_task_grammar"))) == ["E4"]
    last = {"tool": "move_to", "args": {"drone_id": "a"}, "receipt": {"status": "partial_success"}}
    assert types(evaluate(sub({"last_action": last, "remaining_waypoints": 2}))) == ["E3"]
    targets = {"observed_target_followup": {"targets": [{"target_id": "t"}]}}
    assert types(evaluate(sub(targets))) == ["E5"]
    assert types(evaluate(sub(targets), sub(targets, "completed"))) == []


def test_parallel_entities_attribute_each_call_and_each_observer_once():
    batch = [call(5, "sense_nearby_entities", {}, {"per_drone": {
        "a": {"targets": [{"id": "t"}]}, "b": {"targets": [{"id": "t"}]}},
        "blackboard": {"targets": [{"id": "t"}]}}),
        call(3, "get_target_info", {"target_id": "t"}, {"id": "t", "last_updated": 7})]
    result = evaluate(batch)
    assert [(e["call_id"], e["actor_id"]) for e in result["events"]] == [
        ("call-5", "a"), ("call-5", "b"), ("call-3", None)]
    assert result["events"][0]["evidence"] == {"sequence": 5, "wire_message_index": 7}
    assert evaluate(batch, [call(9, "get_target_info", {"target_id": "t"}, {"id": "t", "last_updated": 8})])["event_state"] == "E0"
    assert evaluate(batch, history_gaps=["trimmed"])["event_state"] == "indeterminate"


def test_shared_blackboard_only_has_fleet_scope_even_on_an_aircraft_call():
    payload = {"drone_id": "a", "move_result": {"status": "success", "drone_id": "a"},
               "nearby": {"targets": [{"id": "local"}]},
               "blackboard": {"targets": [{"id": "local"}, {"id": "fleet-only"}]}}
    result = evaluate([action(5, "move_to_and_sense", {}, payload)])
    assert {e["entity_id"]: (e["scope"], e["actor_id"]) for e in result["events"]} == {
        "local": ("drone", "a"), "fleet-only": ("fleet", None)}


def test_progress_session_scoped_and_parallel_conflict():
    def read(i, value, session="s"):
        return call(i, "get_mission_progress", {}, {"progress_percentage": value, "session_id": session})
    assert evaluate([read(1, 30)])["event_state"] == "E0"
    assert evaluate([read(1, 30)], [read(3, 50, "other")])["event_state"] == "E0"
    result = evaluate([read(1, 30)], [read(3, 50), read(4, 50)])
    assert types(result) == ["E5", "E5"]
    assert {event["previous_percent"] for event in result["events"]} == {30}
    conflict = [read(2, 35), read(3, 40)]
    assert evaluate([read(1, 30)], conflict)["event_state"] == "indeterminate"
    assert evaluate([read(1, 30)], conflict, [read(4, 39)])["event_state"] == "E0"


TASK = "Command drone Drone 1 to take off to an altitude of 12 meters and hover in place."
EXPLICIT = TASK.replace("hover in place", "then execute a hover command")
CHECKPOINT = "Drone Drone 1 takes off to 12 meters, flies to checkpoint at (5, 9, 12), and hovers briefly."


def test_hover_anchor_action_order_and_first_confirmation():
    anchor = [call(1, "list_drones", {}, [drone()]),
              call(2, "get_weather", {}, {"weather": "clear", "wind_speed": 1, "visibility": 500})]
    ascent = [action(3, "take_off", {"altitude": 12})]
    obs = [status(4)]
    assert types(evaluate(anchor, ascent, obs, task=TASK)) == ["E6"]
    assert types(evaluate(anchor, ascent, obs, [status(7)], task=TASK)) == []
    assert "E6" not in types(evaluate(anchor, ascent, [status(4, x=6)], task=TASK))
    assert "E6" not in types(evaluate(anchor, ascent, [status(4, z=11)], task=TASK))
    assert "E6" not in types(evaluate(anchor, ascent, [status(4, ident="b")], task=TASK))
    assert "E6" not in types(evaluate(anchor, ascent, obs, task=EXPLICIT))
    hover = action(5, "hover", {})
    assert types(evaluate(anchor, ascent, [hover], [status(7)], task=EXPLICIT)) == ["E6"]
    assert "E6" not in types(evaluate(anchor, ascent, [hover, status(7)], task=EXPLICIT))
    duration = evaluate(anchor, ascent, obs, task=TASK[:-1] + " for 5 seconds.")
    assert "E6" not in types(duration)
    assert duration["clause_coverage"][0]["status"] == "elapsed_duration_unverified"


def test_checkpoint_only_matching_named_actor_and_original_destination_once():
    init = [call(1, "list_drones", {}, [drone(), drone("b", "Drone 2")])]
    def blocked(seq, ident="a", x=5):
        return action(seq, "navigate_to", {"drone_id": ident, "x": x, "y": 9, "z": 12},
                      {"status": "error", "reason": "path_blocked"})
    first = evaluate(init, [blocked(2)], task=CHECKPOINT)
    assert types(first) == ["E1", "E6"]
    clause = next(row for row in first["clause_coverage"] if row["supported"])
    assert CHECKPOINT[slice(*clause["span"])] == "flies to checkpoint at (5, 9, 12)"
    assert clause["clause_id"] == first["events"][1]["clause_id"]
    assert types(evaluate(init, [blocked(2)], [blocked(3)], task=CHECKPOINT)) == ["E1"]
    assert types(evaluate(init, [blocked(2, "b")], task=CHECKPOINT)) == ["E1"]
    assert types(evaluate(init, [blocked(2, x=4)], task=CHECKPOINT)) == ["E1"]
    assert not evaluate(init, [blocked(2)], task="Send it there.")["clause_coverage"][0]["supported"]


def test_effects_ignore_reply_future_and_private_data_without_io(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("event extraction attempted filesystem access")
    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    prefix = {"task": TASK, "turn": 2, "batches": [[status(2)]], "gaps": []}
    expected = json.dumps(extract_events(prefix), sort_keys=True)
    augmented = copy.deepcopy(prefix)
    augmented.update(reasoning_content="changed", future_receipts=[action(3, "hover", {})],
                     final_checker={"success": True}, terminal_ledger={"obligations": []})
    assert json.dumps(extract_events(augmented), sort_keys=True) == expected
