"""Semantic acceptance checks based on unmodified dev240 instructions.

The receipts below contain only agent-facing observations.  They are small
counterexamples, not a reconstruction of the benchmark's hidden validators.
"""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_progress import TaskProgressLedger, TaskProgressMiddleware


FIXTURE = Path(__file__).parent / "fixtures" / "m1_task_progress.json"
TASKS = {
    task["task_id"]: task
    for task in json.loads(FIXTURE.read_text(encoding="utf-8"))["tasks"]
}


def ledger_for(task_id, **kwargs):
    task = TASKS[task_id]
    return TaskProgressLedger(task["instruction"], command_id=task_id, **kwargs)


def observe(ledger, name, args, payload):
    """Use the same input_json envelope exposed by the actual UAV tools."""
    ledger.observe_tool(name, {"input_json": json.dumps(args)}, json.dumps(payload))


def drone(drone_id, name, position):
    return {"id": drone_id, "name": name, "position": position, "status": "hovering"}


def nearby(targets):
    return {"drone_id": "d5", "targets": targets, "drones": [], "obstacles": []}


@pytest.mark.parametrize("task_id", list(TASKS))
def test_real_instruction_and_clause_source_order_are_preserved(task_id):
    ledger = ledger_for(task_id)
    snap = ledger.snapshot()
    instruction = TASKS[task_id]["instruction"]
    assert snap["command"] == instruction
    assert snap["command_id"] == task_id
    assert snap["clauses"]
    previous_end = 0
    for clause in snap["clauses"]:
        assert previous_end <= clause["start"] < clause["end"] <= len(instruction)
        assert clause["text"] == instruction[clause["start"]:clause["end"]]
        previous_end = clause["end"]
    assert instruction[previous_end:].strip() == ""
    # An instruction is a requirement, not evidence that an action happened.
    assert snap["events"] == []
    assert snap["entities"] == {}


def test_sequential_target_binding_is_exact_and_never_substitutes_shape():
    ledger = ledger_for("bcaf22cf")
    names = ledger.snapshot()["entity_bindings"]
    assert {"Drone 5", "Polygon Target 1", "Circle Target 1", "Polygon Target 2"} <= set(names)
    assert all(binding["state"] == "unresolved" for binding in names.values())

    observe(ledger, "sense_nearby_entities", {}, {"per_drone": {"d5": nearby([
        {"id": "p1", "name": "Polygon Target 1", "type": "polygon"},
    ])}})
    names = ledger.snapshot()["entity_bindings"]
    assert names["Polygon Target 1"]["state"] == "exact"
    assert names["Polygon Target 1"]["entities"][0]["id"] == "p1"
    assert names["Circle Target 1"]["state"] == "unresolved"
    assert names["Polygon Target 2"]["state"] == "unresolved"


def test_duplicate_observed_names_remain_ambiguous():
    ledger = ledger_for("bfc721cc")
    observe(ledger, "sense_nearby_entities", {}, {"per_drone": {"d5": nearby([
        {"id": "c3-a", "name": "Circle Target 3", "type": "circle"},
        {"id": "c3-b", "name": "Circle Target 3", "type": "circle"},
    ])}})
    binding = ledger.snapshot()["entity_bindings"]["Circle Target 3"]
    assert binding["state"] == "ambiguous"
    assert {entity["id"] for entity in binding["entities"]} == {"c3-a", "c3-b"}


def test_unknown_target_and_name_used_as_id_404_do_not_prove_nonexistence():
    ledger = ledger_for("bfc721cc")
    observe(ledger, "get_target_info", {"target_id": "Circle Target 3"},
            {"error": "404 Not Found"})
    snap = ledger.snapshot()
    assert snap["entity_bindings"]["Circle Target 3"]["state"] == "unresolved"
    assert snap["entities"] == {}
    assert snap["events"][0]["status"] == "error"
    assert snap["events"][0]["payload"] == {"error": "404 Not Found"}


def test_observing_one_target_does_not_supply_the_all_targets_denominator():
    ledger = ledger_for("9c0b52a0")
    observe(ledger, "get_nearby_entities", {"drone_id": "d5"}, nearby([
        {"id": "f1", "name": "Fixed Target 1", "type": "fixed"},
    ]))
    snap = ledger.snapshot()
    assert "all targets" in snap["command"]
    assert "95%" in snap["command"]
    assert set(snap["entities"]) == {"target:f1"}
    # No task-level percentage/denominator is invented from local visibility.
    assert "task_progress" not in snap
    assert "completion_percentage" not in snap
    assert "total_targets" not in snap
    assert "task_success" not in snap


def test_two_takeoffs_and_landings_are_distinct_history_events():
    ledger = ledger_for("6294fe84")
    for name in ("take_off", "land", "take_off", "land"):
        args = {"drone_id": "d17"}
        if name == "take_off":
            args["altitude"] = 15
        observe(ledger, name, args, {"status": "success", "drone_id": "d17"})
    events = ledger.snapshot()["events"]
    assert [event["tool_name"] for event in events] == ["take_off", "land", "take_off", "land"]
    assert len({event["evidence_id"] for event in events}) == 4
    assert all(event["status"] == "completed" for event in events)


def test_current_position_updates_without_erasing_past_arrival_event():
    ledger = ledger_for("bcaf22cf")
    original_position = {"x": 10, "y": 20, "z": 21}
    later_position = {"x": 100, "y": 120, "z": 21}
    observe(ledger, "list_drones", {}, [drone("d5", "Drone 5", original_position)])
    observe(ledger, "navigate_to", {"drone_id": "d5", **original_position},
            {"success": True, "arrival_verified": True, "drone_id": "d5",
             "final_position": original_position})
    arrival = copy.deepcopy(ledger.snapshot()["events"][-1])
    observe(ledger, "get_drone_status", {"drone_id": "d5"},
            drone("d5", "Drone 5", later_position))
    snap = ledger.snapshot()
    assert snap["entities"]["drone:d5"]["facts"]["position"]["value"] == later_position
    assert snap["events"][1] == arrival
    assert arrival["payload"]["final_position"] == original_position


def test_photo_receipt_does_not_certify_the_requested_target_is_in_the_photo():
    ledger = ledger_for("bcaf22cf")
    observe(ledger, "get_nearby_entities", {"drone_id": "d5"}, nearby([
        {"id": "p1", "name": "Polygon Target 1", "type": "polygon"},
    ]))
    photo_receipt = {"status": "success", "drone_id": "d5", "photo_id": "photo-1"}
    observe(ledger, "take_photo", {"drone_id": "d5"}, photo_receipt)
    snap = ledger.snapshot()
    assert snap["events"][-1]["payload"] == photo_receipt
    assert snap["events"][-1]["status"] == "completed"
    assert "captured_targets" not in json.dumps(snap)
    assert "target_in_photo" not in json.dumps(snap)
    assert "photo_id" not in snap["entities"]["target:p1"]["facts"]


def test_partial_movement_keeps_its_prefix_and_new_perception():
    ledger = ledger_for("c451cd74")
    receipt = {
        "drone_id": "d7",
        "move_result": {
            "success": False, "partial_success": True, "drone_id": "d7",
            "completed_waypoints": 1, "successful_points": [[1, 2, 22]],
            "unsuccessful_points": [[3, 4, 22]],
        },
        "nearby": nearby([{"id": "c8", "name": "Circle Target 8", "type": "circle"}]),
    }
    observe(ledger, "move_along_path_and_sense", {"drone_id": "d7"}, receipt)
    snap = ledger.snapshot()
    assert snap["events"][-1]["status"] == "partial"
    assert snap["events"][-1]["payload"] == receipt
    assert snap["entity_bindings"]["Circle Target 8"]["state"] == "exact"


def test_unverified_intermediate_arrival_does_not_block_later_valid_work():
    ledger = ledger_for("c451cd74")
    receipt = {"success": False, "arrival_verified": False, "drone_id": "d7",
               "final_position": {"x": 1, "y": 2, "z": 22}, "distance_remaining": 1.08}
    observe(ledger, "navigate_to", {"drone_id": "d7", "x": 2, "y": 2, "z": 22}, receipt)
    observe(ledger, "move_along_path", {"drone_id": "d7", "coverage_plan_id": "plan-c8"},
            {"status": "success", "drone_id": "d7", "completed_waypoints": 4})
    events = ledger.snapshot()["events"]
    assert len(events) == 2
    assert events[0]["payload"] == receipt
    assert events[1]["tool_name"] == "move_along_path"
    assert events[1]["status"] == "completed"


def test_opaque_compound_tool_error_does_not_fabricate_movement_evidence():
    ledger = ledger_for("bfc721cc")
    opaque_error = "Error moving drone and sensing: request timed out"
    ledger.observe_tool("move_to_and_sense", {"input_json": json.dumps(
        {"drone_id": "d22", "x": 400, "y": 500, "z": 24})}, opaque_error)
    snap = ledger.snapshot()
    event = snap["events"][-1]
    assert event["status"] != "completed"
    assert event["payload"] == opaque_error
    entity = snap["entities"].get("drone:d22", {})
    assert "position" not in entity.get("facts", {})


def test_task_switch_retains_observed_entities_but_not_previous_task_actions():
    inherited = {"targets": [{"id": "c8", "name": "Circle Target 8", "type": "circle",
                              "position": {"x": 10, "y": 20, "z": 0}}]}
    ledger = ledger_for("c451cd74", inherited_blackboard=inherited)
    snap = ledger.snapshot()
    assert snap["entity_bindings"]["Circle Target 8"]["state"] == "exact"
    assert snap["events"] == []
    fact = snap["entities"]["target:c8"]["facts"]["position"]
    assert fact["validity"] == "inherited"
    assert snap["command_id"] == "c451cd74"


def test_observer_does_not_mutate_original_tool_arguments_or_receipt():
    ledger = ledger_for("fa30ebff")
    args = {"input_json": json.dumps({"drone_id": "d19", "altitude": 17})}
    receipt = {"status": "success", "drone_id": "d19", "position": {"x": 0, "y": 0, "z": 17}}
    original_args, original_receipt = copy.deepcopy(args), copy.deepcopy(receipt)
    ledger.observe_tool("take_off", args, receipt)
    assert args == original_args
    assert receipt == original_receipt
    # A second UAV is not rejected merely because the first was observed first.
    observe(ledger, "take_off", {"drone_id": "d6", "altitude": 17},
            {"status": "success", "drone_id": "d6"})
    assert len(ledger.snapshot()["events"]) == 2


def test_bounded_summary_does_not_discard_the_full_long_task():
    ledger = ledger_for("cf8ee51f", max_summary_chars=600)
    summary = ledger.summary()
    assert len(summary) <= 600
    assert ledger.snapshot()["command"] == TASKS["cf8ee51f"]["instruction"]
    assert ledger.snapshot()["command"].endswith("(67, 885, 27) and land.")
    assert "advisory" in summary.lower()


@pytest.mark.parametrize(("task_id", "actions"), [
    ("b1eb0270", [("take_off", "d3"), ("rotate", "d3"), ("hover", "d3")]),
    ("6294fe84", [("take_off", "d17"), ("move_to", "d17"), ("land", "d17"),
                  ("take_off", "d17"), ("move_to", "d17"), ("land", "d17")]),
    ("53357761", [("take_off", "d11"), ("navigate_to", "d11"),
                  ("broadcast", "d11"), ("broadcast", "d11"), ("broadcast", "d11")]),
    ("fa30ebff", [("take_off", "d19"), ("take_off", "d6")]),
])
def test_old_contract_self_lock_counterexamples_remain_transparent(task_id, actions):
    """Unresolved evidence cannot become a new prerequisite for legal actions."""
    ledger = ledger_for(task_id)
    middleware = TaskProgressMiddleware(ledger, mode="shadow")
    executed = []
    for index, (tool_name, drone_id) in enumerate(actions):
        request = SimpleNamespace(tool_call={
            "name": tool_name, "id": f"call-{index}",
            "args": {"input_json": json.dumps({"drone_id": drone_id})},
        })
        receipt = {"status": "success", "drone_id": drone_id}

        def original_handler(received_request):
            assert received_request is request
            executed.append((tool_name, drone_id))
            return receipt

        assert middleware.wrap_tool_call(request, original_handler) is receipt
    assert executed == actions
    assert len(ledger.snapshot()["events"]) == len(actions)
    assert ledger.snapshot()["mission_completion"] == "not_evaluated"
