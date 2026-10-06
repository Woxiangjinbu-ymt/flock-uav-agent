"""M1 observation and middleware contracts, without a model or server."""
import asyncio
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_progress import TaskProgressLedger, TaskProgressMiddleware


def request(tool="move_to", args=None, call_id="call-1"):
    return SimpleNamespace(tool_call={"name": tool, "args": args or {}, "id": call_id})


def model_request(system="B1 system"):
    return ModelRequest(model=object(), system_message=SystemMessage(content=system),
                        messages=[HumanMessage(content="original task")], tools=[])


def position(ledger, drone_id="d1"):
    return ledger.snapshot()["entities"][f"drone:{drone_id}"]["facts"]["position"]


def observe_position(ledger, x=0, drone_id="d1"):
    ledger.observe_tool("get_drone_status", {"drone_id": drone_id},
                        {"id": drone_id, "name": "Drone 1", "position": {"x": x, "y": 0, "z": 10}})


def test_shadow_preserves_model_and_tool_result_identity_and_single_execution():
    ledger = TaskProgressLedger("Fly Drone 1")
    middleware = TaskProgressMiddleware(ledger, "shadow")
    req = model_request()
    calls = []
    returned = object()
    assert middleware.wrap_model_call(req, lambda r: calls.append(r) or returned) is returned
    assert calls == [req] and calls[0] is req
    tool_req = request(args={"input_json": '{"drone_id":"d1","x":3}'})
    output = ToolMessage(content='{"status":"success"}', tool_call_id="call-1")
    calls.clear()
    assert middleware.wrap_tool_call(tool_req, lambda r: calls.append(r) or output) is output
    assert calls == [tool_req]
    assert ledger.snapshot()["events"][0]["args"] == {"drone_id": "d1", "x": 3}
    assert ledger.snapshot()["mission_completion"] == "not_evaluated"


def test_summary_is_ephemeral_bounded_and_does_not_accumulate_or_mutate():
    ledger = TaskProgressLedger("Find Circle Target 1", max_summary_chars=650)
    middleware = TaskProgressMiddleware(ledger, "summary")
    req = model_request()
    original = req.system_message.model_dump()
    seen = []
    for _ in range(2):
        middleware.wrap_model_call(req, lambda r: seen.append(r) or None)
    assert req.system_message.model_dump() == original
    assert seen[0].messages is req.messages
    assert seen[0].tools is req.tools
    assert seen[0].system_message.content == seen[1].system_message.content
    assert seen[0].system_message.content.count("M1 advisory") == 1
    assert len(ledger.summary()) <= 650
    assert "not a global total" in ledger.summary()


def test_system_content_blocks_are_copied_without_flattening():
    original = [{"type": "text", "text": "original"}]
    req = model_request(original)
    middleware = TaskProgressMiddleware(TaskProgressLedger("hello"), "summary")
    changed = middleware.wrap_model_call(req, lambda r: r)
    assert req.system_message.content == original
    assert changed.system_message.content[:-1] == original
    assert changed.system_message.content[-1]["text"].startswith("M1 advisory")


def test_tool_exception_identity_preserved_and_no_retry():
    ledger = TaskProgressLedger("Move Drone 1")
    observe_position(ledger)
    middleware = TaskProgressMiddleware(ledger)
    error = RuntimeError("transport outcome unknown")
    calls = []

    def execute(req):
        calls.append(req)
        raise error

    with pytest.raises(RuntimeError) as caught:
        middleware.wrap_tool_call(request(args={"drone_id": "d1"}), execute)
    assert caught.value is error and len(calls) == 1
    assert ledger.snapshot()["events"][-1]["status"] == "unknown"
    assert position(ledger)["validity"] == "stale"


def test_observer_failures_do_not_change_tool_or_model_execution(monkeypatch):
    ledger = TaskProgressLedger("task")
    middleware = TaskProgressMiddleware(ledger, "summary")

    def broken(*args, **kwargs):
        raise ValueError("observer failed")

    monkeypatch.setattr(ledger, "begin_tool", broken)
    monkeypatch.setattr(ledger, "summary", broken)
    result = object()
    req = request()
    model_req = model_request()
    calls = []
    assert middleware.wrap_tool_call(req, lambda r: calls.append(r) or result) is result
    assert middleware.wrap_model_call(model_req, lambda r: calls.append(r) or result) is result
    assert calls == [req, model_req]
    assert len(ledger.snapshot()["observer_errors"]) == 2


def test_async_wrappers_preserve_single_call_and_cancellation():
    async def run():
        ledger = TaskProgressLedger("task")
        middleware = TaskProgressMiddleware(ledger, "summary")
        calls = []
        output = ToolMessage(content='{"status":"success"}', tool_call_id="call-1")

        async def execute(req):
            calls.append(req)
            return output

        assert await middleware.awrap_tool_call(request(), execute) is output
        req = model_request()
        assert await middleware.awrap_model_call(req, execute) is output
        assert len(calls) == 2
        assert calls[-1] is not req and req.system_message.content == "B1 system"
        error = asyncio.CancelledError("cancel")

        async def cancelled(req):
            calls.append(req)
            raise error

        with pytest.raises(asyncio.CancelledError) as caught:
            await middleware.awrap_tool_call(request(), cancelled)
        assert caught.value is error and len(calls) == 3
        assert ledger.snapshot()["events"][-1]["status"] == "unknown"
    asyncio.run(run())


def test_overlapping_position_replies_have_no_invented_temporal_order():
    ledger = TaskProgressLedger("Move Drone 1")
    first = ledger.begin_tool("get_drone_status", {"drone_id": "d1"}, "a")
    second = ledger.begin_tool("get_drone_status", {"drone_id": "d1"}, "b")
    ledger.end_tool(second, {"id": "d1", "position": {"x": 2, "y": 0, "z": 10}})
    ledger.end_tool(first, {"id": "d1", "position": {"x": 1, "y": 0, "z": 10}})
    assert position(ledger)["validity"] == "order_unknown"
    assert all(event["ordering"] == "uncertain" for event in ledger.snapshot()["events"])
    observe_position(ledger, 3)
    assert position(ledger)["validity"] == "observed"
    assert position(ledger)["value"]["x"] == 3


@pytest.mark.parametrize("read_finishes_first", [True, False])
@pytest.mark.parametrize("already_known", [True, False])
def test_overlapping_read_and_movement_never_claim_current_position(read_finishes_first, already_known):
    ledger = TaskProgressLedger("Move Drone 1")
    if already_known:
        observe_position(ledger)
    read = ledger.begin_tool("get_drone_status", {"drone_id": "d1"})
    move = ledger.begin_tool("move_to", {"drone_id": "d1", "x": 9, "y": 0, "z": 10})
    read_reply = {"id": "d1", "name": "Drone 1", "position": {"x": 0, "y": 0, "z": 10}}
    if read_finishes_first:
        ledger.end_tool(read, read_reply)
        ledger.end_tool(move, {"status": "success"})
    else:
        ledger.end_tool(move, {"status": "success"})
        ledger.end_tool(read, read_reply)
    assert position(ledger)["validity"] == "order_unknown"
    observe_position(ledger, 9)
    assert position(ledger)["validity"] == "observed"


def test_large_task_summary_keeps_complete_json_actions_and_explicit_omissions():
    command = "Drone 1 visit " + ", ".join(f"Circle Target {i}" for i in range(1, 71)) + "; then land."
    ledger = TaskProgressLedger(command, max_summary_chars=4000)
    observe_position(ledger)
    ledger.observe_tool("take_photo", {"drone_id": "d1"}, {"status": "success"})
    summary = ledger.summary()
    data = json.loads(summary.split("\n", 1)[1])
    assert len(summary) <= 4000
    assert data["omitted"]["entity_bindings"] > 0
    assert any(event["tool_name"] == "take_photo" for event in data["recent_tool_evidence"])
    assert data["last_known_entities"]
    assert data["clauses"]
    assert any(event["source_clause_ids"] for event in data["recent_tool_evidence"])
    assert ledger.snapshot()["command"] == command


def test_discovery_clause_reference_survives_later_fact_refresh():
    ledger = TaskProgressLedger("Find Circle Target 1; then photograph it.")
    ledger.observe_tool("sense_nearby_entities", {}, {"per_drone": {"d1": {"targets": [
        {"id": "t1", "name": "Circle Target 1", "position": {"x": 1, "y": 2}}
    ]}}})
    ledger.observe_tool("get_target_info", {"target_id": "t1"}, {
        "id": "t1", "name": "Circle Target 1", "position": {"x": 2, "y": 3}
    })
    assert ledger.snapshot()["events"][0]["source_clause_ids"] == ["clause-1"]


def test_independent_instances_do_not_share_events_entities_or_notes():
    first, second = TaskProgressLedger("Drone 1", "a"), TaskProgressLedger("Drone 1", "b")
    observe_position(first)
    assert first.snapshot()["entity_bindings"]["Drone 1"]["state"] == "exact"
    assert second.snapshot()["entity_bindings"]["Drone 1"]["state"] == "unresolved"
    assert not second.snapshot()["entities"] and not second.snapshot()["events"]


def test_inherited_blackboard_never_becomes_fresh_just_because_returned_again():
    board = {"drones": [{"id": "d1", "name": "Drone 1", "facts": {"position": [1, 2, 3]}}]}
    original = copy.deepcopy(board)
    ledger = TaskProgressLedger("Drone 1", inherited_blackboard=board)
    ledger.observe_tool("sense_nearby_entities", {}, {"per_drone": {"d1": {"targets": []}}, "blackboard": board})
    assert position(ledger)["validity"] == "inherited"
    assert board == original
    observe_position(ledger, 4)
    ledger.observe_tool("sense_nearby_entities", {}, {"blackboard": board})
    assert position(ledger)["value"]["x"] == 4


def test_partial_path_preserves_prefix_and_nearby_without_full_arrival_claim():
    ledger = TaskProgressLedger("Drone 1 visit Circle Target 1")
    receipt = {"move_result": {"status": "partial_success", "successful_points_count": 1,
                               "successful_points": [[10, 0, 20]], "unsuccessful_points_count": 2},
               "nearby": {"targets": [{"id": "t1", "name": "Circle Target 1", "position": {"x": 30, "y": 0}}]}}
    ledger.observe_tool("move_along_path_and_sense", {"drone_id": "d1"}, json.dumps(receipt))
    snapshot = ledger.snapshot()
    assert snapshot["events"][-1]["status"] == "partial"
    assert snapshot["events"][-1]["payload"] == receipt
    assert snapshot["entity_bindings"]["Circle Target 1"]["state"] == "exact"
    assert snapshot["mission_completion"] == "not_evaluated"


def test_movement_then_sensing_error_cannot_prove_no_motion_or_arrival():
    ledger = TaskProgressLedger("Move Drone 1")
    observe_position(ledger)
    ledger.observe_tool("move_to_and_sense", {"drone_id": "d1", "x": 99, "y": 0, "z": 10},
                        "Error moving drone and sensing: sensor unavailable")
    assert position(ledger)["value"]["x"] == 0
    assert position(ledger)["validity"] == "stale"
    assert ledger.snapshot()["events"][-1]["status"] == "unknown"


@pytest.mark.parametrize("tool", ["move_to", "take_photo", "broadcast"])
def test_error_after_emergency_landing_invalidates_even_nonmovement_action(tool):
    ledger = TaskProgressLedger("Drone 1 take photo")
    ledger.observe_tool("get_drone_status", {"drone_id": "d1"}, {
        "id": "d1", "name": "Drone 1", "position": {"x": 0, "y": 0, "z": 20},
        "status": "hovering", "battery_level": .4,
    })
    ledger.observe_tool(tool, {"drone_id": "d1"}, {
        "status": "error", "message": "Emergency landing executed due to critical battery level (<0.5%)",
    })
    facts = ledger.snapshot()["entities"]["drone:d1"]["facts"]
    assert facts["position"]["validity"] == facts["status"]["validity"] == "stale"
    assert facts["position"]["value"]["z"] == 20
    ledger.observe_tool("get_drone_status", {"drone_id": "d1"}, {
        "id": "d1", "position": {"x": 0, "y": 0, "z": 0}, "status": "idle",
    })
    assert position(ledger)["validity"] == "observed" and position(ledger)["value"]["z"] == 0


def test_public_reach_history_keeps_actor_qualifier_without_current_visit_claim():
    ledger = TaskProgressLedger("Drone 1 visits Circle Target 1")
    ledger.observe_tool("get_target_info", {"target_id": "t1"}, {
        "id": "t1", "name": "Circle Target 1", "is_reached": True, "reached_by": ["another-drone"],
    })
    facts = ledger.snapshot()["entities"]["target:t1"]["facts"]
    assert facts["reached_by"]["value"] == ["another-drone"]
    assert facts["is_reached"]["scope"] == "session_history_not_current_position_or_current_task"
    assert ledger.snapshot()["mission_completion"] == "not_evaluated"
    assert "reached_by" in ledger.summary()


def test_returned_position_is_evidence_even_when_navigation_did_not_arrive():
    ledger = TaskProgressLedger("Drone 1 move to 99,0")
    ledger.observe_tool("navigate_to", {"drone_id": "d1", "x": 99, "y": 0},
                        {"success": False, "outcome": "blocked", "arrival_verified": False,
                         "drone_id": "d1", "final_position": {"x": 0, "y": 0, "z": 10}})
    assert position(ledger)["value"]["x"] == 0
    assert ledger.snapshot()["events"][-1]["status"] == "blocked"
    assert '"arrival_verified":false' in ledger.summary()


@pytest.mark.parametrize("navigation_result", [None, {"outcome": "partial", "success": False}])
def test_relative_relation_receipt_remains_primary_when_nested_navigation_is_optional(navigation_result):
    ledger = TaskProgressLedger("Drone 1 approach Drone 2")
    ledger.observe_tool("move_within_distance", {"drone_id": "d1", "reference_drone_id": "d2"}, {
        "success": True, "outcome": "completed", "within_distance": True,
        "distance_after": 16.64, "navigation_attempted": navigation_result is not None,
        "navigation_result": navigation_result, "drone_id": "d1", "reference_drone_id": "d2",
        "drone_position_after": {"x": 10, "y": 0, "z": 10},
        "reference_position_after": {"x": 0, "y": 0, "z": 10},
    })
    assert ledger.snapshot()["events"][-1]["status"] == "completed"
    assert position(ledger)["value"]["x"] == 10
    assert position(ledger, "d2")["value"]["x"] == 0
    summary_data = json.loads(ledger.summary().split("\n", 1)[1])
    assert summary_data["recent_tool_evidence"][0]["effects"]["within_distance"] is True
    assert ledger.snapshot()["mission_completion"] == "not_evaluated"


def test_unparseable_output_is_unknown_and_nonfinite_cannot_poison_snapshot():
    ledger = TaskProgressLedger("Drone 1")
    observe_position(ledger)
    ledger.observe_tool("move_to", {"drone_id": "d1"}, {"status": "success", "bad": float("nan")})
    assert ledger.snapshot()["events"][-1]["status"] == "unknown"
    assert position(ledger)["validity"] == "stale"
    json.dumps(ledger.snapshot(), allow_nan=False)


def test_off_mode_cannot_accidentally_install_middleware():
    with pytest.raises(ValueError, match="off must not instantiate"):
        TaskProgressMiddleware(TaskProgressLedger("task"), "off")


@pytest.mark.parametrize("budget", [0, 8, 100, 600, 4000])
def test_summary_always_respects_character_bound(budget):
    ledger = TaskProgressLedger("Find Circle Target 1 " * 100, max_summary_chars=budget)
    assert len(ledger.summary()) <= budget
