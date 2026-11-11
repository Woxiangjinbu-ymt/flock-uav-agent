"""Completion memory preserves evidence and never gates real graph tools."""
import asyncio
import json
from pathlib import Path
import sys
import time

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
from test_task_progress_integration import make_agent, call
from test_minimal_harness import graph_agent, SIMPLE


def sensing_agent(mode, replies):
    agent, actions = make_agent(mode, replies)
    @tool
    def get_nearby_entities(input_json: str) -> str:
        """Read the current local entities, including deliberate refreshes."""
        actions.append(("get_nearby_entities", json.loads(input_json)))
        return json.dumps({"targets": [], "drones": [], "obstacles": []})
    original = agent._create_tools
    agent._create_tools = lambda board: [*original(board), get_nearby_entities]
    return agent, actions


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("declaration", ["", "<query_decision>{broken}</query_decision>",
    '<query_decision>{"actor":"u1","observed_change":{"kind":"none","evidence":[],"summary":"No change observed."},'
    '"planned_action":{"kind":"refresh","reason":"Confirm unchanged facts."}}</query_decision>',
    '<query_decision>{"actor":"u1","observed_change":{"kind":"position_changed","evidence":["e3"],'
    '"summary":"I plan to move so the position has changed."},'
    '"planned_action":{"kind":"change_viewpoint","reason":"Try another point."}}</query_decision>'])
def test_missing_malformed_and_unsupported_refreshes_all_execute(stream, declaration):
    repeated = call("get_nearby_entities", "sense-2", drone_id="u1")
    repeated.content = declaration
    replies = [call("get_drone_status", "status", drone_id="u1"),
               call("get_nearby_entities", "sense-1", drone_id="u1"), repeated,
               AIMessage(content="Target remains unresolved.")]
    agents, traces = [], []
    for mode in ("evidence_memory_v5", "evidence_memory_v7"):
        agent, actions = sensing_agent(mode, replies)
        agent.execute("Drone 1 must visit Fixed Target 1.",
                      **({"step_callback": lambda _: None} if stream else {}))
        agents.append(agent); traces.append(list(actions))
    old, new = agents
    assert traces[0] == traces[1]
    assert sum(name == "get_nearby_entities" for name, _ in traces[1]) == 2
    assert len(old.llm.requests) == len(new.llm.requests) == 4
    assert old.llm.bound_tools == new.llm.bound_tools
    assert new._task_progress_middleware.variant == "evidence_memory_v7"
    assert new._task_progress.query_decisions
    assert any("query_decision" in x["text"] for x in new._task_progress.injections)
    assert all(r["previous_prefix_preserved"] for r in new._task_progress.model_requests[1:])
    for a, b in zip(old.llm.requests, new.llm.requests):
        raw = lambda rows: [(m.tool_call_id, m.content.split("\n\n[M1-R task evidence;")[0])
                            for m in rows if isinstance(m, ToolMessage)]
        assert raw(a) == raw(b)


@pytest.mark.parametrize("method", ["query_decision_context", "record_model_response"])
def test_audit_failure_never_retries_model_or_blocks_tools(monkeypatch, method):
    from flockcue.memory.route_projection import ExecutionMemoryLedgerV7
    def broken(*args, **kwargs):
        raise ValueError("controlled audit failure")
    monkeypatch.setattr(ExecutionMemoryLedgerV7, method, broken)
    replies = [call("get_drone_status", "status", drone_id="u1"),
               call("get_nearby_entities", "read", drone_id="u1"), AIMessage(content="Unresolved.")]
    agent, actions = sensing_agent("evidence_memory_v7", replies)
    agent.execute("Drone 1 must visit Fixed Target 1.")
    assert len(agent.llm.requests) == 3
    assert [name for name, _ in actions] == ["get_drone_status", "get_nearby_entities"]


def test_audit_runs_before_the_tools_in_its_response(monkeypatch):
    from flockcue.memory.route_projection import ExecutionMemoryLedgerV7
    original = ExecutionMemoryLedgerV7.record_model_response
    observed = []
    def record(self, content, tool_calls, *, context):
        observed.append(([t["id"] for t in tool_calls], [e["tool_call_id"] for e in self._events]))
        return original(self, content, tool_calls, context=context)
    monkeypatch.setattr(ExecutionMemoryLedgerV7, "record_model_response", record)
    agent, _ = sensing_agent("evidence_memory_v7", [
        call("get_drone_status", "status", drone_id="u1"),
        call("get_nearby_entities", "read", drone_id="u1"), AIMessage(content="Unresolved.")])
    agent.execute("Drone 1 must visit Fixed Target 1.")
    assert len(observed) == 3
    for proposed_ids, known_ids in observed:
        assert not set(proposed_ids) & set(known_ids)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_harness_pause_keeps_normal_action_counts_and_records_decisions(monkeypatch, asynchronous):
    agent = graph_agent(monkeypatch, policy="every_step")
    agent.task_progress_mode = "evidence_memory_v7_harness"
    if asynchronous:
        agent.blackboard = agent._blackboard_for_current_command()
        agent.tools = agent._create_tools(agent.blackboard)
        agent._harness_task_started = time.monotonic()
        agent._harness_cancel_check = lambda: None
        agent._initialize_task_progress(SIMPLE, "v7-async")
        result = asyncio.run(agent._create_agent_runtime().ainvoke({
            "messages": [{"role": "user", "content": SIMPLE}]}))
        messages = result["messages"]
    else:
        result = agent.execute(SIMPLE)
        assert "memory_handoff" in result
        messages = agent.llm.requests[-1]
    response = json.loads(next(m.content.split("\n\n[M1-R task evidence;")[0]
                               for m in messages if isinstance(m, ToolMessage)))
    assert response["status"] == "paused"
    assert len(agent.llm.requests) == 2
    assert [name for name, _ in agent.client.calls].count("take_off") == 1
    assert agent._task_progress_middleware.variant == "evidence_memory_v7_harness"
    assert agent._task_progress.query_decisions


def test_interruption_still_preserves_real_takeoff_receipt(monkeypatch):
    agent = graph_agent(monkeypatch)
    agent.task_progress_mode = "evidence_memory_v7_harness"
    class CancelAfterTakeoff(BaseCallbackHandler):
        def _check_cancellation(self):
            if len(agent.client.calls) >= 3:
                raise RuntimeError("controlled cancellation")
    result = agent.execute(SIMPLE, callbacks=[CancelAfterTakeoff()])
    assert not result["success"]
    assert result["harness"]["frames"][0]["status"] == "interrupted"
    assert result["memory_handoff"]["mission_completion"] == "not_evaluated"
    assert agent._task_progress.snapshot()["events"][-1]["tool_name"] == "take_off"


def test_explicit_v7_initialization_cannot_silently_fall_back(monkeypatch):
    import execution_memory_v7
    def broken(*args, **kwargs):
        raise ValueError("controlled initialization error")
    monkeypatch.setattr(execution_memory_v7, "ExecutionMemoryLedgerV7", broken)
    agent, _ = make_agent("evidence_memory_v7", [AIMessage(content="[TASK DONE]")])
    assert not agent.execute("Drone 1 must visit Fixed Target 1.")["success"]
    assert not agent.llm.requests


@pytest.mark.parametrize("stream", [False, True])
def test_completed_visit_survives_negative_object_flag_and_queries_still_execute(stream):
    """Completion advice must neither erase raw contradictions nor gate tools."""
    replies = [call("get_drone_status", "identity", drone_id="u1"),
               call("move_to", "visit", drone_id="u1", x=100, y=0, z=20),
               call("get_nearby_entities", "confirm", drone_id="u1"),
               call("get_nearby_entities", "repeat-confirm", drone_id="u1"),
               AIMessage(content="The required visit has an execution receipt.")]
    agents, traces = [], []
    for mode in ("evidence_memory_v5", "evidence_memory_v7"):
        agent, actions = make_agent(mode, replies)
        @tool
        def move_to(input_json: str) -> str:
            """Move to the requested point and report actual target contact."""
            actions.append(("move_to", json.loads(input_json)))
            return json.dumps({"drone_id": "u1", "command": "move_to", "status": "success",
                "outcome": "completed", "message": "Drone moved to position (100.0, 0.0, 20.0), heading: 90.0 - Reached target(s): Fixed Target 1"})
        @tool
        def get_nearby_entities(input_json: str) -> str:
            """Return local target object fields without changing visit history."""
            actions.append(("get_nearby_entities", json.loads(input_json)))
            return json.dumps({"drones": [], "obstacles": [], "targets": [{
                "id": "t1", "name": "Fixed Target 1", "is_reached": False, "reached_by": [],
                "position": {"x": 90, "y": 0, "z": 20}}]})
        old_tools = agent._create_tools
        agent._create_tools = lambda board: [*old_tools(board), move_to, get_nearby_entities]
        agent.execute("Drone 1 must visit Fixed Target 1.",
                      **({"step_callback": lambda _: None} if stream else {}))
        agents.append(agent); traces.append(list(actions))
    assert traces[0] == traces[1]
    assert [name for name, _ in traces[1]] == ["get_drone_status", "move_to",
                                             "get_nearby_entities", "get_nearby_entities"]
    old, new = agents
    assert len(old.llm.requests) == len(new.llm.requests) == 5
    assert old.llm.bound_tools == new.llm.bound_tools
    progress = new._task_progress.completion_context()
    assert progress["done"] and not progress["remaining"] and not progress["unverified"]
    assert any("resume" in row["text"] for row in new._task_progress.injections)
    for old_request, new_request in zip(old.llm.requests, new.llm.requests):
        raw = lambda rows: [(m.tool_call_id, m.content.split("\n\n[M1-R task evidence;")[0])
                            for m in rows if isinstance(m, ToolMessage)]
        assert raw(old_request) == raw(new_request)
    assert '"is_reached": false' in str(raw(new.llm.requests[-1]))


@pytest.mark.parametrize("stream", [False, True])
def test_landing_without_coordinates_reaches_final_prompt_without_an_extra_query(stream):
    from test_memory_ledger import ROUTE
    points = [(10, 20, 5), (40, 50, 5), (70, 80, 5)]
    replies = [call("get_drone_status", "identity", drone_id="u1")]
    replies += [call("move_to", f"waypoint-{n}", drone_id="u1", x=x, y=y, z=z)
                for n, (x, y, z) in enumerate(points)]
    replies += [call("land", "landing", drone_id="u1"),
                AIMessage(content="Completed the route and landed successfully.")]
    agents, traces = [], []
    for mode in ("evidence_memory_v5", "evidence_memory_v7"):
        agent, actions = make_agent(mode, replies)

        @tool
        def move_to(input_json: str) -> str:
            """Move to a waypoint and return its actual arrival receipt."""
            args = json.loads(input_json)
            actions.append(("move_to", args))
            return json.dumps({"drone_id": "u1", "command": "move_to", "status": "success",
                "message": f"Drone moved to position ({args['x']}, {args['y']}, {args['z']})"})

        @tool
        def land(input_json: str) -> str:
            """Land successfully; this public receipt contains no coordinates."""
            actions.append(("land", json.loads(input_json)))
            return json.dumps({"drone_id": "u1", "command": "land", "status": "success"})

        old_tools = agent._create_tools
        agent._create_tools = lambda board: [*old_tools(board), move_to, land]
        agent.execute(ROUTE, **({"step_callback": lambda _: None} if stream else {}))
        agents.append(agent)
        traces.append(list(actions))
    old, new = agents
    assert traces[0] == traces[1]
    assert [name for name, _ in traces[1]] == ["get_drone_status", "move_to", "move_to", "move_to", "land"]
    assert len(new.llm.requests) == len(old.llm.requests) == 6
    assert old.llm.bound_tools == new.llm.bound_tools
    view = new._task_progress.decision_context()
    landing = next(row for row in view["resume"]["done"] if row["kind"] == "landing")
    assert landing["position"] == "unknown"
    assert view["resume"]["remaining"] == []
    final_text = "\n".join(str(m.content) for m in new.llm.requests[-1])
    assert '"position":"unknown"' in final_text or '"position": "unknown"' in final_text
    assert "without coordinates" in final_text
    for old_request, new_request in zip(old.llm.requests, new.llm.requests):
        def raw(rows):
            return [(m.tool_call_id, m.content.split("\n\n[M1-R task evidence;")[0])
                    for m in rows if isinstance(m, ToolMessage)]
        assert raw(old_request) == raw(new_request)
