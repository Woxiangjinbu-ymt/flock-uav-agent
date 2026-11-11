"""Real agent graph and harness handoff checks; no provider calls."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
from test_task_progress_integration import make_agent, call
from test_minimal_harness import graph_agent, runtime, SIMPLE


@pytest.mark.parametrize("stream", [False, True])
def test_memory_graph_preserves_public_actions_and_immutable_prefix(stream):
    replies = [call("get_drone_status", "status", drone_id="u1"),
               call("navigate_to_and_sense", "move", drone_id="u1", x=9, y=0, z=4),
               AIMessage(content="Unresolved target, no completion claim.")]
    old, old_actions = make_agent("evidence_r3", replies)
    new, new_actions = make_agent("evidence_memory", replies)
    kwargs = {"step_callback": lambda _: None} if stream else {}
    command = "Drone 1: approach Circle Target 1 and take a photo."
    old.execute(command, **kwargs)
    new.execute(command, **kwargs)
    assert old_actions == new_actions
    assert len(new.llm.requests) == len(old.llm.requests) == 3
    assert old.llm.bound_tools == new.llm.bound_tools
    for old_request, new_request in zip(old.llm.requests, new.llm.requests):
        assert [(m.tool_call_id, m.content.split("\n\n[M1-R task evidence;")[0])
                for m in old_request if isinstance(m, ToolMessage)] == [
            (m.tool_call_id, m.content.split("\n\n[M1-R task evidence;")[0])
            for m in new_request if isinstance(m, ToolMessage)]
    assert new._task_progress.command == command
    assert all(row["previous_prefix_preserved"] for row in new._task_progress.model_requests[1:])
    assert new._task_progress_middleware.variant == "evidence_memory"


def test_explicit_memory_initialization_error_is_not_silent_baseline(monkeypatch):
    import execution_memory
    def broken(*args, **kwargs):
        raise ValueError("deliberate initialization failure")
    monkeypatch.setattr(execution_memory, "ExecutionMemoryLedger", broken)
    agent, _ = make_agent("evidence_memory", [AIMessage(content="[TASK DONE]")])
    result = agent.execute("Check Drone 1")
    assert not result["success"]
    assert not agent.llm.requests


@pytest.mark.parametrize("asynchronous", [False, True])
def test_harness_pause_returns_handoff_without_extra_calls(monkeypatch, asynchronous):
    agent = graph_agent(monkeypatch, policy="every_step")
    agent.task_progress_mode = "evidence_memory_harness"
    if asynchronous:
        import time
        agent.blackboard = agent._blackboard_for_current_command()
        agent.tools = agent._create_tools(agent.blackboard)
        agent._harness_task_started = time.monotonic()
        agent._harness_cancel_check = lambda: None
        agent._initialize_task_progress(SIMPLE, "async-memory")
        result = asyncio.run(agent._create_agent_runtime().ainvoke({
            "messages": [{"role": "user", "content": SIMPLE}]}))
        messages = result["messages"]
    else:
        agent.execute(SIMPLE)
        messages = agent.llm.requests[-1]
    response = json.loads(next(m.content.split("\n\n[M1-R task evidence;")[0]
                               for m in messages if isinstance(m, ToolMessage)))
    assert response["status"] == "paused"
    assert response["memory_handoff"]["mission_completion"] == "not_evaluated"
    assert agent._task_progress.command == SIMPLE
    assert len(agent.llm.requests) == 2
    assert [name for name, _ in agent.client.calls].count("take_off") == 1
    assert agent._task_progress_middleware.variant == "evidence_memory_harness"


def test_real_interruption_retains_handoff_and_actual_receipt(monkeypatch):
    agent = graph_agent(monkeypatch)
    agent.task_progress_mode = "evidence_memory_harness"
    class CancelAfterTakeoff(BaseCallbackHandler):
        def _check_cancellation(self):
            if len(agent.client.calls) >= 3:
                raise RuntimeError("controlled cancellation")
    result = agent.execute(SIMPLE, callbacks=[CancelAfterTakeoff()])
    assert not result["success"]
    assert [name for name, _ in agent.client.calls] == ["list_drones", "get_drone_status", "take_off"]
    assert result["harness"]["frames"][0]["status"] == "interrupted"
    assert result["harness"]["handoffs"][0]["memory_handoff"]["mission_completion"] == "not_evaluated"
    events = result["harness"]["events"]
    assert any(e["kind"] == "interruption_handoff" for e in events)
    assert agent._task_progress.snapshot()["events"][-1]["tool_name"] == "take_off"


def test_handoff_observer_error_cannot_replay_or_hide_action():
    harness, world, _ = runtime(scheduler="every_step")
    def broken(frame):
        raise RuntimeError("observer only")
    harness.handoff_provider = broken
    response = harness.control("start", skill="simple_chain")
    assert response["status"] == "paused"
    assert response["memory_handoff_error"] == "RuntimeError"
    assert sum(name == "take_off" for name, _ in world.calls) == 1
    assert "handoffs" in harness.snapshot()


def test_raw_tool_failure_returns_remaining_original_route_without_recorder():
    command = "Drone 1 route: fly to and start at (10,0,20), then waypoints (20,0,20), then end (30,0,20) and land."
    agent, _ = make_agent("evidence_memory", [
        call("get_drone_status", "bind", drone_id="u1"),
        call("move_to", "first", drone_id="u1", x=10, y=0, z=20)])
    @tool
    def move_to(input_json: str) -> str:
        """Execute a direct movement and return its actual receipt."""
        return json.dumps({"status": "success", "command": "move_to", "drone_id": "u1",
                           "message": "Drone moved to position (10, 0, 20)"})
    original = agent._create_tools
    agent._create_tools = lambda board: [*original(board), move_to]
    result = agent.execute(command)  # Third model request raises; no new graph or action.
    assert not result["success"]
    route = result["memory_handoff"]["routes"][0]
    assert route["ordered_prefix"] == 1
    assert [row["point"] for row in route["remaining_original_goals"]] == [[20, 0, 20], [30, 0, 20]]
    assert agent._task_progress.command == command


def test_empty_output_graph_restart_keeps_original_obligations_and_evidence():
    command = "Drone 1 route: fly to and start at (10,0,20), then waypoints (20,0,20), then end (30,0,20) and land."
    agent, _ = make_agent("evidence_memory", [
        call("get_drone_status", "bind", drone_id="u1"),
        call("move_to", "first", drone_id="u1", x=10, y=0, z=20),
        AIMessage(content=""), AIMessage(content="Remaining route still needs execution.")])
    @tool
    def move_to(input_json: str) -> str:
        """Return an actual direct movement receipt."""
        return json.dumps({"status": "success", "command": "move_to", "drone_id": "u1",
                           "message": "Drone moved to position (10, 0, 20)"})
    original_tools = agent._create_tools
    agent._create_tools = lambda board: [*original_tools(board), move_to]
    extract = agent._extract_final_output
    # Exercise the existing recovery path even though its generic fallback may
    # otherwise pick nonempty user/tool text as a terminal output.
    agent._extract_final_output = lambda messages: ("" if isinstance(messages[-1], AIMessage)
        and not messages[-1].content else extract(messages))
    result = agent.execute(command)
    assert result["empty_response_retries"] == 1
    assert agent._task_progress.command == command
    assert result["memory_handoff"]["routes"][0]["ordered_prefix"] == 1
    assert len(agent._task_progress.snapshot()["events"]) == 2
    assert agent._task_progress_middleware._segment == 1
