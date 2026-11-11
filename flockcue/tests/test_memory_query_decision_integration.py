"""The real graph observes query decisions but never gates public tools."""
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
    '<query_decision>{"actor":"u1","decision":"refresh","reason":"none",'
    '"evidence":[],"summary":"No observed change; I want confirmation."}</query_decision>'])
def test_missing_malformed_and_unsupported_refreshes_all_execute(stream, declaration):
    repeated = call("get_nearby_entities", "sense-2", drone_id="u1")
    repeated.content = declaration
    replies = [call("get_drone_status", "status", drone_id="u1"),
               call("get_nearby_entities", "sense-1", drone_id="u1"), repeated,
               AIMessage(content="Target remains unresolved.")]
    agents, traces = [], []
    for mode in ("evidence_memory_v2", "evidence_memory_v3"):
        agent, actions = sensing_agent(mode, replies)
        agent.execute("Drone 1 must visit Fixed Target 1.",
                      **({"step_callback": lambda _: None} if stream else {}))
        agents.append(agent); traces.append(list(actions))
    old, new = agents
    assert traces[0] == traces[1]
    assert sum(name == "get_nearby_entities" for name, _ in traces[1]) == 2
    assert len(old.llm.requests) == len(new.llm.requests) == 4
    assert old.llm.bound_tools == new.llm.bound_tools
    assert new._task_progress_middleware.variant == "evidence_memory_v3"
    assert new._task_progress.query_decisions
    assert any("query_decision" in x["text"] for x in new._task_progress.injections)
    assert all(r["previous_prefix_preserved"] for r in new._task_progress.model_requests[1:])
    for a, b in zip(old.llm.requests, new.llm.requests):
        raw = lambda rows: [(m.tool_call_id, m.content.split("\n\n[M1-R task evidence;")[0])
                            for m in rows if isinstance(m, ToolMessage)]
        assert raw(a) == raw(b)


@pytest.mark.parametrize("method", ["query_decision_context", "record_model_response"])
def test_audit_failure_never_retries_model_or_blocks_tools(monkeypatch, method):
    from flockcue.memory.query_decision import ExecutionMemoryLedgerV3
    def broken(*args, **kwargs):
        raise ValueError("controlled audit failure")
    monkeypatch.setattr(ExecutionMemoryLedgerV3, method, broken)
    replies = [call("get_drone_status", "status", drone_id="u1"),
               call("get_nearby_entities", "read", drone_id="u1"), AIMessage(content="Unresolved.")]
    agent, actions = sensing_agent("evidence_memory_v3", replies)
    agent.execute("Drone 1 must visit Fixed Target 1.")
    assert len(agent.llm.requests) == 3
    assert [name for name, _ in actions] == ["get_drone_status", "get_nearby_entities"]


def test_audit_runs_before_the_tools_in_its_response(monkeypatch):
    from flockcue.memory.query_decision import ExecutionMemoryLedgerV3
    original = ExecutionMemoryLedgerV3.record_model_response
    observed = []
    def record(self, content, tool_calls, *, context):
        observed.append(([t["id"] for t in tool_calls], [e["tool_call_id"] for e in self._events]))
        return original(self, content, tool_calls, context=context)
    monkeypatch.setattr(ExecutionMemoryLedgerV3, "record_model_response", record)
    agent, _ = sensing_agent("evidence_memory_v3", [
        call("get_drone_status", "status", drone_id="u1"),
        call("get_nearby_entities", "read", drone_id="u1"), AIMessage(content="Unresolved.")])
    agent.execute("Drone 1 must visit Fixed Target 1.")
    assert len(observed) == 3
    for proposed_ids, known_ids in observed:
        assert not set(proposed_ids) & set(known_ids)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_harness_pause_keeps_normal_action_counts_and_records_decisions(monkeypatch, asynchronous):
    agent = graph_agent(monkeypatch, policy="every_step")
    agent.task_progress_mode = "evidence_memory_v3_harness"
    if asynchronous:
        agent.blackboard = agent._blackboard_for_current_command()
        agent.tools = agent._create_tools(agent.blackboard)
        agent._harness_task_started = time.monotonic()
        agent._harness_cancel_check = lambda: None
        agent._initialize_task_progress(SIMPLE, "v3-async")
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
    assert agent._task_progress_middleware.variant == "evidence_memory_v3_harness"
    assert agent._task_progress.query_decisions


def test_interruption_still_preserves_real_takeoff_receipt(monkeypatch):
    agent = graph_agent(monkeypatch)
    agent.task_progress_mode = "evidence_memory_v3_harness"
    class CancelAfterTakeoff(BaseCallbackHandler):
        def _check_cancellation(self):
            if len(agent.client.calls) >= 3:
                raise RuntimeError("controlled cancellation")
    result = agent.execute(SIMPLE, callbacks=[CancelAfterTakeoff()])
    assert not result["success"]
    assert result["harness"]["frames"][0]["status"] == "interrupted"
    assert result["memory_handoff"]["mission_completion"] == "not_evaluated"
    assert agent._task_progress.snapshot()["events"][-1]["tool_name"] == "take_off"


def test_explicit_v3_initialization_cannot_silently_fall_back(monkeypatch):
    import execution_memory_v3
    def broken(*args, **kwargs):
        raise ValueError("controlled initialization error")
    monkeypatch.setattr(execution_memory_v3, "ExecutionMemoryLedgerV3", broken)
    agent, _ = make_agent("evidence_memory_v3", [AIMessage(content="[TASK DONE]")])
    assert not agent.execute("Drone 1 must visit Fixed Target 1.")["success"]
    assert not agent.llm.requests
