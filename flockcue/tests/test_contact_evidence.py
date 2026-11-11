"""R2 boundaries: preserved R1 inputs, legal feedback, and useful scoped hints."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
from flockcue.evidence.contact_evidence import (SessionEvidenceIndexR2, TaskEvidenceLedgerR2, aggregate_requirements,
                              public_progress)
from test_task_progress_integration import make_agent, call, request_dump

COMMAND = "Assign drones to all targets until the session task progress exceeds 95%."


def g(command="Drone 2 flies to target Fixed Target 10.", cid="current", index=None, **kwargs):
    return TaskEvidenceLedgerR2(command, cid, index=index or SessionEvidenceIndexR2("s1"), **kwargs)


def relative(ledger, *, actor="d1", distance=40, heading=90, end=(70, 90, 20), wrapped=False, **changes):
    payload = {"command": "move_towards", "drone_id": actor, "status": "success", "outcome": "completed",
               "message": f"Drone moved {distance:.2f}m to position ({end[0]:.2f}, {end[1]:.2f}, {end[2]:.2f}), heading: {heading:.1f}° (battery: healthy) - Reached target(s): Fixed Target 10"}
    payload.update(changes)
    tool = "move_towards_and_sense" if wrapped else "move_towards"
    output = {"move_result": payload, "nearby": {"targets": []}} if wrapped else payload
    return ledger.observe_tool(tool, {"drone_id": actor, "distance": distance, "heading": heading}, output, "source-call")


def bind_actor(ledger):
    ledger.observe_tool("list_drones", {}, [{"id": "d2", "name": "Drone 2"}])


def metadata(percent=80, visited=16):
    return {"id": "s1", "targets": [{"id": "forbidden-entity-canary", "position": [999, 999, 0]}],
            "statistics": {"task_progress": {"task_type": "target_assignment", "progress_percentage": percent,
                            "details": {"total_targets": 20, "visited_targets": visited, "unvisited_targets": 20 - visited}}}}


def read_progress(ledger, percent=80, visited=16):
    return ledger.observe_tool("get_mission_progress", {}, public_progress(metadata(percent, visited), "s1"), "progress-call")


@pytest.mark.parametrize("wrapped", [False, True])
def test_relative_hint_survives_command_boundary_without_transferring_completion(wrapped):
    index = SessionEvidenceIndexR2("s1")
    donor = g(cid="donor", index=index)
    relative(donor, wrapped=wrapped)
    current = g(index=index)
    bind_actor(current)
    item = current.projection()[0]
    assert item["report"]["localization_hint"]["points"] == [[30, 90, 20], [70, 90, 20]]
    assert item["report"]["actor_id"] == "d1" and item["actor_id"] == "d2"
    assert item["report"]["command_id"] == "donor"
    assert item["knowledge_state"] == "reported_only" and item["execution_state"] == "unattempted"
    assert item["position"] is None and item["target_id"] is None
    assert "required actor" in item["next_step"]
    source = donor.snapshot()["events"][0]["payload"]
    for key in item["report"]["message_path"]: source = source[key]
    assert source == item["report"]["message"]


@pytest.mark.parametrize("change", [{"status": "error"}, {"outcome": "partial", "status": "partial_success"},
                                    {"drone_id": "wrong"}, {"message": "Unstructured proposed move"},
                                    {"error": "contradiction"}, {"command": "move_to"}])
def test_invalid_receipt_does_not_create_relative_hint(change):
    ledger = g(); relative(ledger, **change)
    assert not ledger.index.relative_contacts


def test_receipt_direction_distance_and_nested_fallback_are_not_invented():
    for args in ({"drone_id": "d1", "distance": 400, "heading": 90},
                 {"drone_id": "d1", "distance": 40, "heading": False},
                 {"drone_id": "d1", "distance": 40, "heading": 90, "dz": 10}):
        ledger = g(); donor = g(); relative(donor)
        payload = donor.snapshot()["events"][0]["payload"]
        ledger.observe_tool("move_towards", args, payload)
        assert not ledger.index.relative_contacts
        ledger.observe_tool("navigate_to", args, {"outcome": "partial", "arrival_verified": False, "last_move_result": payload})
        assert not ledger.index.relative_contacts


def test_nested_contact_is_not_assigned_the_primary_relative_segment():
    donor = g(); relative(donor)
    payload = donor.snapshot()["events"][0]["payload"]
    payload["last_move_result"] = {"command": "move_to", "status": "success", "drone_id": "d1",
                                    "message": "Reached another place - Reached target(s): Fixed Target 11"}
    ledger = g()
    ledger.observe_tool("move_towards", {"drone_id": "d1", "distance": 40, "heading": 90}, payload)
    assert ledger.index.relative_contacts["Fixed Target 10"]
    assert not ledger.index.relative_contacts["Fixed Target 11"]


@pytest.mark.parametrize("same_actor", [False, True])
def test_actor_overlap_is_local_and_clear_rejects_late_contacts(same_actor):
    ledger = g(); donor = g(); relative(donor)
    payload = donor.snapshot()["events"][0]["payload"]
    args = {"drone_id": "d1", "distance": 40, "heading": 90}
    first = ledger.begin_tool("move_towards", args)
    other = ledger.begin_tool("hover", {"drone_id": "d1" if same_actor else "d2"})
    ledger.end_tool(first, payload); ledger.end_tool(other, {"status": "success"})
    assert bool(ledger.index.relative_contacts) is not same_actor
    late = ledger.begin_tool("move_towards", args)
    ledger.index.clear(); ledger.end_tool(late, payload)
    bind_actor(ledger)
    assert not ledger.index.relative_contacts
    assert "next_step" not in ledger.projection()[0]


def test_shorter_historical_segment_selected_and_known_target_uses_r1_view():
    ledger = g(); bind_actor(ledger)
    relative(ledger, distance=79, heading=180, end=(154, 36, 18))
    relative(ledger, distance=88, heading=0, end=(154, 124, 18))
    item = ledger.projection()[0]
    assert item["report"]["localization_hint"]["points"][-1] == [154, 36, 18]
    assert len(ledger.summary()) <= 3900
    ledger.observe_tool("get_target_info", {"target_id": "t1"},
                        {"id": "t1", "name": "Fixed Target 10", "position": {"x": 150, "y": 44, "z": 0}})
    assert "next_step" not in ledger.projection()[0]
    assert ledger.projection()[0]["knowledge_state"] == "localized"


def test_aggregate_reader_preserves_span_and_does_not_capture_local_coverage_or_conditions():
    ref = aggregate_requirements(COMMAND)[0]
    assert COMMAND[slice(*ref["source_span"])] == "session task progress exceeds 95%"
    assert (ref["operator"], ref["threshold_percent"]) == (">", 95)
    for text in ("Achieve at least 95% total area coverage of Waypoint 1.",
                 "If session task progress exceeds 95%, land.", "Do not let session task progress exceed 95%.",
                 "Continue until progress exceeds 95%.", "Ensure session task progress exceeds 110%."):
        assert aggregate_requirements(text) == []
    text = "If named targets alone do not exceed 50% session task progress, continue until progress exceeds 50%."
    assert aggregate_requirements(text)[0]["threshold_percent"] == 50


def test_public_progress_filters_entities_rejects_wrong_session_missing_and_inconsistent_data():
    result = public_progress(metadata(), "s1")
    assert result["progress_percentage"] == 80 and "forbidden-entity-canary" not in json.dumps(result)
    assert "error" in public_progress(metadata(), "other")
    assert "error" in public_progress({"id": "s1"}, "s1")
    assert "error" in public_progress(metadata(100, 16), "s1")
    assert "error" in public_progress(metadata(float("nan")), "s1")


def test_public_progress_matches_existing_server_integer_rounding():
    value = metadata()
    value["statistics"]["task_progress"].update(
        progress_percentage=int((29 / 100) * 100),
        details={"total_targets": 100, "visited_targets": 29, "unvisited_targets": 71})
    assert "error" not in public_progress(value, "s1")


def test_progress_is_fresh_only_after_nonoverlapping_read_and_never_mission_verdict():
    ledger = g(COMMAND)
    assert ledger.aggregate_projection()[0]["threshold_state"] == "unknown"
    read_progress(ledger)
    assert ledger.aggregate_projection()[0]["threshold_state"] == "observed_threshold_not_met"
    moving = ledger.begin_tool("take_off", {"drone_id": "d1"})
    assert ledger.aggregate_projection()[0]["freshness"] == "stale_after_action_or_overlap"
    read_progress(ledger, 100, 20)  # Read while a movement is in flight is not fresh proof.
    ledger.end_tool(moving, {"status": "success"})
    assert ledger.aggregate_projection()[0]["threshold_state"] == "unknown"
    read_progress(ledger, 100, 20)
    assert ledger.aggregate_projection()[0]["threshold_state"] == "observed_threshold_met"
    ledger.observe_tool("get_mission_progress", {}, {"error": "unavailable"})
    assert ledger.aggregate_projection()[0]["threshold_state"] == "unknown"
    assert ledger.snapshot()["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("operator,state", [("exceeds", "observed_threshold_not_met"), ("at least", "observed_threshold_met")])
def test_exact_threshold_operator_and_new_command_no_progress_transfer(operator, state):
    ledger = g(f"Continue until the session task progress {operator} 95%.")
    read_progress(ledger, 95, 19)
    assert ledger.aggregate_projection()[0]["threshold_state"] == state
    assert g(COMMAND, cid="new", index=ledger.index).aggregate_projection()[0]["threshold_state"] == "unknown"
    ledger.index.clear()
    assert ledger.aggregate_projection()[0]["threshold_state"] == "unknown"


def test_aggregate_budget_and_named_obligations_remain_visible_or_explicitly_omitted():
    command = "Drone 2 flies to target Fixed Target 10. " + COMMAND
    ledger = g(command); bind_actor(ledger); read_progress(ledger)
    summary = json.loads(ledger.summary())
    assert len(ledger.summary()) <= 3900
    assert summary["aggregate_requirements"][0]["threshold_state"] == "observed_threshold_not_met"
    assert len(summary["items"]) + summary["omitted"] == 1
    hints = g(command, aggregate_feedback=False)
    assert hints.aggregate == []


@pytest.mark.parametrize("stream", [False, True])
def test_unaffected_task_preserves_r1_model_inputs_tools_actions_and_calls(stream):
    replies = [call("get_drone_status", "read", drone_id="u1"), AIMessage(content="Done [TASK DONE]")]
    r1, acts1 = make_agent("evidence", replies)
    r2, acts2 = make_agent("evidence_r2", replies)
    kw = {"step_callback": lambda _: None} if stream else {}
    assert r1.execute("Coordinate takeoff and then move north.", **kw) == r2.execute("Coordinate takeoff and then move north.", **kw)
    assert request_dump(r1) == request_dump(r2)
    assert r1.llm.bound_tools == r2.llm.bound_tools and acts1 == acts2


def test_shadow_adds_neither_public_tool_nor_model_input_or_client_calls():
    replies = [AIMessage(content="Done [TASK DONE]")]
    off, _ = make_agent("off", replies); shadow, _ = make_agent("evidence_r2_shadow", replies)
    shadow.client = SimpleNamespace(get_current_session=lambda: pytest.fail("Unexpected metadata request"))
    assert off.execute(COMMAND) == shadow.execute(COMMAND)
    assert request_dump(off) == request_dump(shadow)
    assert off.llm.bound_tools == shadow.llm.bound_tools


@pytest.mark.parametrize("stream", [False, True])
def test_real_graph_tool_is_task_scoped_observed_once_and_prefix_stable(stream):
    replies = [AIMessage(content="", tool_calls=[{"name": "get_mission_progress", "id": "read-progress", "args": {}, "type": "tool_call"}]),
               AIMessage(content="Incomplete, 80% only [TASK DONE]"), AIMessage(content="Done [TASK DONE]")]
    agent, _ = make_agent("evidence_r2", replies)
    reads = []
    agent.client = SimpleNamespace(get_current_session=lambda: reads.append(1) or metadata())
    kw = {"step_callback": lambda _: None} if stream else {}
    agent.execute(COMMAND, **kw)
    ledger = agent._task_progress
    assert reads == [1] and len(ledger.progress_observations) == 1
    assert ledger.aggregate_projection()[0]["threshold_state"] == "observed_threshold_not_met"
    assert ledger.model_requests[1]["previous_prefix_preserved"] is True
    assert "get_mission_progress" in agent.llm.bound_tools[0]
    assert len(ledger.injections) == 2
    assert "forbidden-entity-canary" not in str(agent.llm.requests)
    # No final-answer veto or automatic extra inference is added.
    assert len(agent.llm.requests) == 2
    agent.execute("Take a photo.")
    assert "get_mission_progress" not in agent.llm.bound_tools[-1] and reads == [1]


def test_hints_only_mode_keeps_original_toolset_for_aggregate_task():
    agent, _ = make_agent("evidence_r2_hints", [AIMessage(content="Done [TASK DONE]")])
    agent.execute(COMMAND)
    assert agent._task_progress.aggregate == []
    assert "get_mission_progress" not in agent.llm.bound_tools[0]


def test_r2_actual_sdk_wire_prefix_hashes_and_public_tool_without_network(monkeypatch):
    import hashlib
    import openai
    import uav_agent
    from openai import _base_client
    from langchain_openai import ChatOpenAI
    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    requests, reads = [], []

    def respond(req):
        requests.append(req)
        first = len(requests) == 1
        message = {"role": "assistant", "content": "Incomplete: 80% [TASK DONE]"}
        if first:
            message = {"role": "assistant", "content": "", "tool_calls": [{"id": "progress", "type": "function",
                       "function": {"name": "get_mission_progress", "arguments": "{}"}}]}
        return httpx.Response(200, json={"id": f"mock-{len(requests)}", "object": "chat.completion", "created": 0,
            "model": "mock", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if first else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    sync_client = openai.DefaultHttpxClient(transport=httpx.MockTransport(respond))
    async_client = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(uav_agent, "ChatOpenAI", lambda **kw: ChatOpenAI(
        http_client=sync_client, http_async_client=async_client, **kw))
    agent, _ = make_agent("evidence_r2", [])
    agent.client = SimpleNamespace(get_current_session=lambda: reads.append(1) or metadata())
    agent._model_request_audit_enabled = True
    agent._model_wire_requests, agent._model_wire_errors = [], []
    agent.llm = agent._create_llm("openai-compatible", "mock", "offline-key", "https://offline.invalid/v1", 0.1)
    try:
        result = agent.execute(COMMAND)
        assert result["success"] and len(requests) == 2 and reads == [1]
        assert len(agent._model_wire_requests) == 2 and not agent._model_wire_errors
        for actual, audit in zip(requests, agent._model_wire_requests):
            assert audit["body_sha256"] == hashlib.sha256(actual.content).hexdigest()
            assert "headers" not in audit and "offline-key" not in json.dumps(audit)
        old, new = agent._model_wire_requests
        assert new["common_prefix_messages"] == new["previous_message_count"]
        assert old["tools_sha256"] == new["tools_sha256"]
        body = json.loads(requests[-1].content)
        assert "forbidden-entity-canary" not in str(body)
        assert "observed_threshold_not_met" in str(body)
        assert any(t["function"]["name"] == "get_mission_progress" for t in body["tools"])
    finally:
        sync_client.close()
        asyncio.run(async_client.aclose())
