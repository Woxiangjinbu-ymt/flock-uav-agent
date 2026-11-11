"""M1-R semantic counterexamples; all receipts are offline fixtures."""
import asyncio
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
from blackboard import PerceptionBlackboard
from flockcue.evidence.ledger import SessionEvidenceIndex, TaskEvidenceLedger, references
from flockcue.evidence.middleware import MARKER, RULES, TaskEvidenceMiddleware, message_value
from test_task_progress_integration import call, make_agent, request_dump


def ledger(command="Drone 1 flies to target Fixed Target 10.", cid="c1", index=None):
    return TaskEvidenceLedger(command, cid, index=index)


def status(g, actor="d1", name="Drone 1", x=0):
    return g.observe_tool("get_drone_status", {"drone_id": actor},
                          {"id": actor, "name": name, "position": {"x": x, "y": 0, "z": 10}})


def receipt(name="Fixed Target 10", actor="d1", **extra):
    return {"status": "success", "command": "move_to", "drone_id": actor,
            "message": f"Drone moved to position - Reached target(s): {name}", **extra}


def reach(g, actor="d1", name="Fixed Target 10"):
    return g.observe_tool("move_to", {"drone_id": actor, "x": 100, "y": 0, "z": 10}, receipt(name, actor))


def view(g):
    return g.projection()[0]


def test_name_report_is_not_an_id_coordinate_or_full_task_completion():
    g = ledger("Drone 1 flies to target Fixed Target 10, then takes a photo and moves west.")
    status(g)
    reach(g)
    v = view(g)
    assert (v["knowledge_state"], v["target_id"], v["position"]) == ("reported_only", None, None)
    assert v["execution_state"] == "evidenced"
    assert v["report"]["localization_hint"]["points"] == [[0, 0, 10], [100, 0, 10]]
    assert g.snapshot()["mission_completion"] == "not_evaluated"
    assert g.command.endswith("then takes a photo and moves west.")


def test_prior_command_and_wrong_actor_do_not_discharge_current_visit():
    index = SessionEvidenceIndex("s1")
    donor = ledger(cid="old", index=index)
    status(donor)
    reach(donor)
    current = ledger(index=index)
    assert view(current)["knowledge_state"] == "reported_only"
    assert view(current)["execution_state"] == "unattempted"
    status(current, "d2", "Drone 2")
    reach(current, "d2")
    assert view(current)["execution_state"] == "unattempted"
    reach(current)
    assert view(current)["execution_state"] == "evidenced"
    assert view(current)["execution_evidence_id"]
    # A later command still starts with no current-actor completion.
    assert view(ledger(cid="next", index=index))["execution_state"] == "unattempted"


@pytest.mark.parametrize("bad", [
    {"error": "Reached target(s): Fixed Target 10"},
    {"status": "error", "message": "Error - Reached target(s): Fixed Target 10"},
    {"status": "success", "error": "contradiction", "message": "X - Reached target(s): Fixed Target 10"},
    {"message": "I reached Fixed Target 10"},
    {"status": "success", "user_note": {"message": "X - Reached target(s): Fixed Target 10"}},
    "Error: target Fixed Target 10 not found",
])
def test_requests_errors_and_arbitrary_nested_text_never_create_knowledge(bad):
    g = ledger()
    g.observe_tool("move_to", {"drone_id": "d1", "target_id": "Fixed Target 10"}, bad)
    assert view(g)["knowledge_state"] == "unknown"
    assert not g.index.reports


def test_read_tool_cannot_forge_action_report():
    g = ledger()
    g.observe_tool("get_target_info", {"target_id": "Fixed Target 10"}, receipt())
    assert view(g)["knowledge_state"] == "unknown"


def test_partial_path_uses_only_returned_points_and_keeps_unknown_start():
    g = ledger()
    g.observe_tool("move_along_path", {"drone_id": "d1", "waypoints": [[5, 0, 10], [999, 0, 10]]},
                   receipt(status="partial_success", command="move_along_path",
                           successful_points=[[5, 0, 10]], unsuccessful_points=[[999, 0, 10]]))
    hint = view(g)["report"]["localization_hint"]
    assert hint["points"] == [[5, 0, 10]]
    assert hint["kind"] == "confirmed_waypoints_only"
    assert hint["start_evidence_id"] is None


def test_nested_fallback_does_not_connect_unobserved_path_to_old_start():
    g = ledger()
    status(g)
    g.observe_tool("navigate_to", {"drone_id": "d1", "x": 999, "y": 0, "z": 10},
                   {"outcome": "partial", "arrival_verified": False, "final_position": {"x": 5, "y": 0, "z": 10},
                    "movement_strategy": "planned_fallback", "last_move_result": receipt()})
    assert view(g)["report"]["localization_hint"] is None
    assert view(g)["report"]["message_path"] == ["last_move_result", "message"]


def test_and_sense_source_path_resolves_to_actual_nested_receipt():
    g = ledger()
    raw = {"navigation_result": {"last_move_result": receipt(), "arrival_verified": True, "outcome": "completed"},
           "nearby": {"targets": []}}
    g.observe_tool("navigate_to_and_sense", {"drone_id": "d1"}, raw)
    report = view(g)["report"]
    value = raw
    for key in report["message_path"]:
        value = value[key]
    assert value == report["message"]
    assert report["message_path"] == ["navigation_result", "last_move_result", "message"]


def test_nonmovement_error_can_land_drone_and_invalidates_path_origin():
    g = ledger()
    status(g)
    g.observe_tool("take_photo", {"drone_id": "d1"}, {"status": "error", "message": "battery low"})
    reach(g)
    assert view(g)["report"]["localization_hint"] is None


@pytest.mark.parametrize("same_actor", [True, False])
def test_parallel_same_actor_is_uncertain_but_other_actor_does_not_erase_path(same_actor):
    g = ledger()
    status(g)
    first = g.begin_tool("move_to", {"drone_id": "d1", "x": 100, "y": 0, "z": 10})
    other = g.begin_tool("move_to", {"drone_id": "d1" if same_actor else "d2"})
    g.end_tool(first, receipt())
    g.end_tool(other, {"status": "error"})
    v = view(g)
    assert (v["report"]["localization_hint"] is None) == same_actor
    assert (v["execution_state"] == "evidenced") != same_actor


@pytest.mark.parametrize("name", ["Moving Target 1", "Circle Target 1", "Polygon Target 1"])
def test_dynamic_and_area_names_do_not_get_static_point_hints(name):
    g = ledger(f"Drone 1 flies to target {name}.")
    status(g)
    reach(g, name=name)
    assert view(g)["knowledge_state"] == "reported_only"
    assert view(g)["report"]["localization_hint"] is None


def test_localized_ambiguous_and_coordinate_attempt_are_distinct():
    g = ledger()
    status(g)
    target = {"id": "t1", "name": "Fixed Target 10", "position": {"x": 10, "y": 0, "z": 0}}
    g.observe_tool("get_nearby_entities", {"drone_id": "d1"}, {"targets": [target]})
    assert view(g)["knowledge_state"] == "localized"
    assert view(g)["position"]["command_id"] == "c1"
    g.observe_tool("navigate_to", {"drone_id": "d1", "x": 10, "y": 0, "z": 10},
                   {"outcome": "blocked", "arrival_verified": False})
    assert view(g)["execution_state"] == "blocked"
    assert view(g)["execution_evidence_kind"] == "requested_xy_matches_unique_known_target_not_arrival"
    g.observe_tool("get_target_info", {}, {**target, "id": "t2"})
    reach(g)
    assert view(g)["knowledge_state"] == "ambiguous"
    assert view(g)["execution_state"] != "evidenced"


def test_clear_new_session_and_late_receipt_isolation():
    bb = PerceptionBlackboard("s1")
    bb.task_evidence_index = SessionEvidenceIndex("s1")
    g = ledger(index=bb.task_evidence_index)
    status(g)
    g.observe_tool("get_target_info", {}, {"id": "t1", "name": "Fixed Target 10", "position": {"x": 10, "y": 0, "z": 10}})
    assert view(g)["knowledge_state"] == "localized"
    pending = g.begin_tool("move_to", {"drone_id": "d1"})
    bb.clear()
    g.end_tool(pending, receipt())
    assert not bb.task_evidence_index.reports
    assert not bb.task_evidence_index.entities
    assert view(g)["knowledge_state"] == "unknown"
    assert view(ledger(index=bb.task_evidence_index))["knowledge_state"] == "unknown"
    assert view(ledger(index=SessionEvidenceIndex("s2")))["knowledge_state"] == "unknown"


def test_requirement_extraction_retains_unsupported_negation_and_original_spans():
    command = "Drone 1 flies to target Fixed Target 10, and Drone 2 flies to target Fixed Target 11. Then both move west."
    refs = references(command)
    assert [(r["required_actor"], r["target_name"]) for r in refs] == [("Drone 1", "Fixed Target 10"), ("Drone 2", "Fixed Target 11")]
    for r in refs:
        start, end = r["source_span"]
        assert command[start:end].startswith(r["required_actor"])
        assert command[start:end].endswith(r["target_name"])
    for text in ("Drone 1 and Drone 2 visit Fixed Target 10.", "Drone 1 must not visit Fixed Target 10.",
                 "If possible, Drone 1 visits Fixed Target 10."):
        # Unsupported 'visits' form is conservative; no hidden obligation inferred.
        assert references(text)[0]["required_actor"] is None


def test_bound_four_items_and_keep_current_report_when_session_history_is_evicted():
    g = ledger("Drone 1 flies to target " + ", ".join(f"Fixed Target {i}" for i in range(10)) + ".")
    status(g)
    reach(g, name="Fixed Target 0")
    for _ in range(10):
        reach(g, actor="d2", name="Fixed Target 0")
    by_name = {v["target_name"]: v for v in g.projection()}
    assert by_name["Fixed Target 0"]["execution_state"] == "evidenced"
    assert len(g.index.reports["Fixed Target 0"]) == 8
    summary = json.loads(g.summary())
    assert len(summary["items"]) <= 4 and summary["omitted"] >= 6
    assert len(g.summary()) <= 4000


def request(messages):
    return ModelRequest(model=object(), system_message=SystemMessage(content="original"), messages=messages, tools=[])


def test_prefix_append_batch_boundary_and_event_trigger():
    g = ledger()
    mw = TaskEvidenceMiddleware(g)
    raw = [HumanMessage(content=g.command)]
    first = mw._model_request(request(raw))
    original = copy.deepcopy(raw)
    ai = AIMessage(content="", tool_calls=[{"name": "move_to", "args": {}, "id": "a", "type": "tool_call"},
                                           {"name": "get_drone_status", "args": {}, "id": "b", "type": "tool_call"}])
    raw += [ai, ToolMessage(content="raw-a", tool_call_id="a")]
    status(g)
    reach(g)
    partial = mw._model_request(request(raw))
    assert len(g.injections) == 1  # incomplete batch
    raw += [ToolMessage(content="raw-b", tool_call_id="b")]
    complete = mw._model_request(request(raw))
    assert len(g.injections) == 2
    assert MARKER in complete.messages[-1].content
    assert raw[0] == original[0] and raw[-1].content == "raw-b"
    for before, after in ((first, partial), (partial, complete)):
        assert before.system_message == after.system_message
        assert [message_value(m) for m in before.messages] == [message_value(m) for m in after.messages[:len(before.messages)]]
    mw._model_request(request(raw))
    assert len(g.injections) == 2
    assert all(r["previous_prefix_preserved"] for r in g.model_requests[1:])
    mw._model_request(request([HumanMessage(content="retry")]))
    assert g.model_requests[-1]["segment"] == 1


@pytest.mark.parametrize("mode", ["evidence_shadow", "evidence_prompt", "evidence"])
@pytest.mark.parametrize("stream", [False, True])
def test_real_graph_modes_leave_tools_and_raw_results_unchanged(mode, stream):
    replies = [call("get_drone_status", "read", drone_id="u1"),
               call("navigate_to_and_sense", "move", drone_id="u1", x=9, y=0, z=4),
               call("take_photo", "photo", drone_id="u1"), AIMessage(content="Done [TASK DONE]")]
    off, off_actions = make_agent("off", replies)
    enabled, actions = make_agent(mode, replies)
    kwargs = {"step_callback": lambda _: None} if stream else {}
    command = "Drone 1 flies to target Circle Target 1 and takes a photo."
    assert enabled.execute(command, **kwargs) == off.execute(command, **kwargs)
    assert actions == off_actions and len(enabled.llm.requests) == 4
    assert enabled.llm.bound_tools == off.llm.bound_tools
    if mode == "evidence_shadow":
        assert request_dump(enabled) == request_dump(off)
    if mode == "evidence_prompt":
        assert not enabled._task_progress.injections
        assert all(r[0].content.endswith(RULES) for r in enabled.llm.requests)
    if mode == "evidence":
        audit = enabled._task_progress.snapshot()["task_evidence"]
        assert all(r["previous_prefix_preserved"] for r in audit["model_requests"][1:])
        assert not enabled._task_progress.snapshot()["observer_errors"]
        assert all(len(i["text"]) <= 4000 for i in audit["injections"])


def test_async_tools_execute_once_and_observer_errors_fail_open(monkeypatch):
    g = ledger()
    mw = TaskEvidenceMiddleware(g)
    req = SimpleNamespace(tool_call={"name": "move_to", "args": {"drone_id": "d1"}, "id": "a"})
    output = ToolMessage(content=json.dumps(receipt()), tool_call_id="a")
    calls = []
    async def handler(r):
        calls.append(r)
        return output
    assert asyncio.run(mw.awrap_tool_call(req, handler)) is output
    assert calls == [req]
    monkeypatch.setattr(g, "summary", lambda: (_ for _ in ()).throw(ValueError("bad summary")))
    async def model(r):
        return r
    result = asyncio.run(mw.awrap_model_call(request([HumanMessage(content="task")]), model))
    assert result.messages[0].content == "task"
    assert g._observer_errors == ["bad summary"]


def test_opaque_message_artifact_never_prevents_original_model_call():
    g = ledger()
    mw = TaskEvidenceMiddleware(g)
    raw = [HumanMessage(content="task"), ToolMessage(content="raw", tool_call_id="x", artifact=object())]
    calls = []
    result = mw.wrap_model_call(request(raw), lambda r: calls.append(r) or "ok")
    assert result == "ok" and len(calls) == 1
    assert g._observer_errors


def test_actual_sdk_serialization_and_graph_prefix_with_mock_transport(monkeypatch):
    """Use the installed SDK serializer, but intercept every HTTP call locally."""
    import hashlib
    import openai
    import uav_agent
    from openai import _base_client
    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    requests = []
    def respond(req):
        requests.append(req)
        n = len(requests)
        message = {"role": "assistant", "content": "Done [TASK DONE]"}
        if n <= 2:
            name = "get_drone_status" if n == 1 else "navigate_to_and_sense"
            args = {"drone_id": "u1"} if n == 1 else {"drone_id": "u1", "x": 9, "y": 0, "z": 4}
            message = {"role": "assistant", "content": "", "tool_calls": [{"id": f"call-{n}", "type": "function",
                       "function": {"name": name, "arguments": json.dumps({"input_json": json.dumps(args)})}}]}
        return httpx.Response(200, json={"id": f"mock-{n}", "object": "chat.completion", "created": 0,
            "model": "mock", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if n <= 2 else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    from langchain_openai import ChatOpenAI
    sync_client = openai.DefaultHttpxClient(transport=httpx.MockTransport(respond))
    async_client = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(uav_agent, "ChatOpenAI", lambda **kw: ChatOpenAI(
        http_client=sync_client, http_async_client=async_client, **kw))
    agent, actions = make_agent("evidence", [])
    agent._model_request_audit_enabled = True
    agent._model_wire_requests, agent._model_wire_errors = [], []
    agent.llm = agent._create_llm("openai-compatible", "mock", "offline-key", "https://offline.invalid/v1", 0.1)
    try:
        result = agent.execute("Drone 1 flies to target Circle Target 1.")
        assert result["success"] and len(requests) == 3 and len(actions) == 2
        assert len(agent._model_wire_requests) == 3
        assert not agent._model_wire_errors
        for actual, audit in zip(requests, agent._model_wire_requests):
            assert audit["body_sha256"] == hashlib.sha256(actual.content).hexdigest()
            assert "headers" not in audit and "offline-key" not in json.dumps(audit)
        for record in agent._model_wire_requests[1:]:
            assert record["common_prefix_messages"] == record["previous_message_count"]
        assert all(r["tools_sha256"] == agent._model_wire_requests[0]["tools_sha256"] for r in agent._model_wire_requests)
    finally:
        sync_client.close()
        asyncio.run(async_client.aclose())


def test_visible_reset_metadata_and_explicit_reset_use_no_extra_api_calls():
    agent, _ = make_agent("evidence", [AIMessage(content="Done [TASK DONE]")])
    board = PerceptionBlackboard("s1")
    board.task_evidence_index = SessionEvidenceIndex("s1")
    agent._session_blackboards["s1"] = board
    agent.blackboard = board
    count = [10]
    calls = []
    def current():
        calls.append(1)
        return {"id": "s1", "created_at": 1, "statistics": {"total_commands_executed": count[0]}}
    agent.client = SimpleNamespace(get_current_session=current)
    assert agent._current_session_id() == "s1"
    g = ledger(index=board.task_evidence_index)
    reach(g)
    count[0] = 0
    assert agent._current_session_id() == "s1"
    assert not board.task_evidence_index.reports
    assert calls == [1, 1]
    agent.reset_task_evidence("s1")
    assert calls == [1, 1]


def test_empty_retry_keeps_command_evidence_and_next_command_has_new_view():
    agent, _ = make_agent("evidence", [call("get_drone_status", "read", drone_id="u1"),
                                       AIMessage(content=""), AIMessage(content="Done [TASK DONE]"),
                                       AIMessage(content="Done [TASK DONE]")])
    built = []
    original = agent._create_agent_runtime
    def build():
        built.append(agent._task_progress)
        return original()
    agent._create_agent_runtime = build
    assert agent.execute("")["empty_response_retries"] == 1
    assert built[0] is built[1]
    assert built[0].model_requests[-1]["segment"] == 1
    assert agent.execute("Drone 1 flies to target Fixed Target 10.")["success"]
    assert built[-1] is not built[0]
    assert built[-1].index is built[0].index
    assert view(built[-1])["execution_state"] == "unattempted"


def test_audit_trace_saved_with_toolchain_and_async_request_hook(tmp_path, monkeypatch):
    import httpx
    import toolchain_recorder
    from uav_agent import UAVControlAgent
    agent, _ = make_agent("evidence", [AIMessage(content="Done [TASK DONE]")])
    agent._model_request_audit_enabled = True
    agent._model_wire_requests, agent._model_wire_errors = [], []
    req = httpx.Request("POST", "https://offline.invalid/v1/chat/completions", json={"messages": [{"role": "user", "content": "task"}]})
    asyncio.run(agent._aaudit_model_request(req))
    assert len(agent._model_wire_requests) == 1
    records = []
    monkeypatch.setattr(toolchain_recorder, "write_toolchain_json", lambda record: records.append(record) or tmp_path / "trace.json")
    monkeypatch.setattr(toolchain_recorder, "request_history_jsonl_path", lambda record: tmp_path / "requests.jsonl")
    monkeypatch.setattr(toolchain_recorder, "write_request_history_jsonl", lambda *a: None)
    agent.toolchain_json_recording = True
    UAVControlAgent._record_toolchain_if_enabled(agent, {"success": True}, command_id="c1", command="task",
        started_at="2026-10-06T00:00:00", completed_at="2026-10-06T00:00:01", status="completed", empty_response_retries=0)
    assert records[0]["model_requests_wire"][0]["scope"] == "serialized_http_request_body"
    assert records[0]["model_request_audit_errors"] == []
