"""Compact memory reaches the real graph without blocking public calls."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_task_progress_integration import make_agent, call
from test_minimal_harness import graph_agent, SIMPLE


@pytest.mark.parametrize("stream", [False, True])
def test_advice_never_blocks_refresh_or_changes_public_receipts(stream):
    replies = [call("get_drone_status", "status", drone_id="u1"),
               call("get_nearby_entities", "sense-1", drone_id="u1"),
               call("get_nearby_entities", "sense-2", drone_id="u1"),
               AIMessage(content="Target remains unresolved.")]
    traces, agents = [], []
    for mode in ("evidence_r3", "evidence_memory_v2"):
        agent, actions = make_agent(mode, replies)
        @tool
        def get_nearby_entities(input_json: str) -> str:
            """Read current nearby entities, including an intentional refresh."""
            actions.append(("get_nearby_entities", json.loads(input_json)))
            return json.dumps({"targets": [], "drones": [], "obstacles": []})
        original = agent._create_tools
        agent._create_tools = lambda board: [*original(board), get_nearby_entities]
        agent.execute("Drone 1 must visit Fixed Target 1.",
                      **({"step_callback": lambda _: None} if stream else {}))
        traces.append(list(actions)); agents.append(agent)
    old, new = agents
    assert traces[0] == traces[1]
    assert sum(name == "get_nearby_entities" for name, _ in traces[1]) == 2
    assert len(old.llm.requests) == len(new.llm.requests) == 4
    assert old.llm.bound_tools == new.llm.bound_tools
    assert all(row["previous_prefix_preserved"] for row in new._task_progress.model_requests[1:])
    assert new._task_progress_middleware.variant == "evidence_memory_v2"
    assert any("checked_here" in injection["text"] for injection in new._task_progress.injections)
    for a, b in zip(old.llm.requests, new.llm.requests):
        raw = lambda rows: [(m.tool_call_id, m.content.split("\n\n[M1-R task evidence;")[0])
                            for m in rows if isinstance(m, ToolMessage)]
        assert raw(a) == raw(b)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_harness_v2_preserves_pause_without_extra_actions(monkeypatch, asynchronous):
    agent = graph_agent(monkeypatch, policy="every_step")
    agent.task_progress_mode = "evidence_memory_v2_harness"
    if asynchronous:
        agent.blackboard = agent._blackboard_for_current_command()
        agent.tools = agent._create_tools(agent.blackboard)
        agent._harness_task_started = time.monotonic()
        agent._harness_cancel_check = lambda: None
        agent._initialize_task_progress(SIMPLE, "v2-async")
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
    assert response["memory_handoff"]["mission_completion"] == "not_evaluated"
    assert len(agent.llm.requests) == 2
    assert [name for name, _ in agent.client.calls].count("take_off") == 1
    assert agent._task_progress_middleware.variant == "evidence_memory_v2_harness"


def test_interrupted_v2_harness_keeps_original_task_and_receipts(monkeypatch):
    agent = graph_agent(monkeypatch)
    agent.task_progress_mode = "evidence_memory_v2_harness"
    class CancelAfterTakeoff(BaseCallbackHandler):
        def _check_cancellation(self):
            if len(agent.client.calls) >= 3:
                raise RuntimeError("controlled cancellation")
    result = agent.execute(SIMPLE, callbacks=[CancelAfterTakeoff()])
    assert not result["success"]
    assert result["memory_handoff"]["mission_completion"] == "not_evaluated"
    assert result["harness"]["frames"][0]["status"] == "interrupted"
    assert agent._task_progress.command == SIMPLE
    assert agent._task_progress.snapshot()["events"][-1]["tool_name"] == "take_off"


def test_v2_initialization_failure_does_not_silently_become_baseline(monkeypatch):
    import execution_memory_v2
    def broken(*args, **kwargs):
        raise ValueError("controlled initialization error")
    monkeypatch.setattr(execution_memory_v2, "ExecutionMemoryLedgerV2", broken)
    agent, _ = make_agent("evidence_memory_v2", [AIMessage(content="[TASK DONE]")])
    result = agent.execute("Drone 1 must visit Fixed Target 1.")
    assert not result["success"]
    assert not agent.llm.requests
