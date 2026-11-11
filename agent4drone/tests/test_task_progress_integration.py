"""Real agent graph with scripted model replies: no provider or simulator calls."""
import copy
import json
from pathlib import Path
import sys

import pytest
from pydantic import Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uav_agent
from blackboard import PerceptionBlackboard
from uav_agent import UAVControlAgent


class ScriptedModel(BaseChatModel):
    replies: list = Field(default_factory=list)
    requests: list = Field(default_factory=list)
    bound_tools: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-scripted-m1-integration"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools.append([t.name if hasattr(t, "name") else t["name"] for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        reply = self.replies[len(self.requests) - 1]
        return ChatResult(generations=[ChatGeneration(message=copy.deepcopy(reply))])


def call(name, call_id, **arguments):
    return AIMessage(content="", tool_calls=[{
        "name": name, "id": call_id,
        "args": {"input_json": json.dumps(arguments)}, "type": "tool_call",
    }])


def make_agent(mode, replies):
    executed = []

    @tool
    def get_drone_status(input_json: str) -> str:
        """Read the current drone status."""
        executed.append(("get_drone_status", json.loads(input_json)))
        return json.dumps({"id": "u1", "name": "Drone 1", "status": "flying",
                           "position": {"x": 1, "y": 2, "z": 3}})

    @tool
    def navigate_to_and_sense(input_json: str) -> str:
        """Move and observe local entities."""
        executed.append(("navigate_to_and_sense", json.loads(input_json)))
        return json.dumps({"navigation_result": {
                               "success": False, "arrival_verified": False, "outcome": "partial",
                               "drone_id": "u1", "final_position": {"x": 8, "y": 0, "z": 4}},
                           "nearby": {"targets": [{"id": "t1", "name": "Circle Target 1"}]}})

    @tool
    def take_photo(input_json: str) -> str:
        """Take a photo; no target identities are returned."""
        executed.append(("take_photo", json.loads(input_json)))
        return json.dumps({"success": True, "photo_id": "p1"})

    agent = UAVControlAgent.__new__(UAVControlAgent)
    agent.task_progress_mode = mode
    agent._task_progress = None
    agent._task_progress_middleware = None
    agent.llm = ScriptedModel(replies=replies)
    agent.llm_provider = "offline"
    agent.llm_model = "scripted"
    agent.max_iterations = 150
    agent.max_empty_response_retries = 2
    agent.debug = False
    agent.verbose = False
    agent.share_blackboard_by_session = True
    agent.toolchain_json_recording = False
    agent._session_blackboards = {}
    agent.session_context = {"session_id": "s1"}
    blackboard = PerceptionBlackboard("s1")
    agent._blackboard_for_current_command = lambda: blackboard
    agent._create_tools = lambda _: [get_drone_status, navigate_to_and_sense, take_photo]
    agent._build_system_prompt = lambda: "Frozen baseline system prompt."
    return agent, executed


def request_dump(agent):
    return [[m.model_dump(exclude={"id"}) for m in request] for request in agent.llm.requests]


def test_off_and_shadow_preserve_exact_model_inputs_tools_outputs_and_call_count():
    replies = [call("get_drone_status", "read", drone_id="u1"), AIMessage(content="Done [TASK DONE]")]
    off, off_actions = make_agent("off", replies)
    shadow, shadow_actions = make_agent("shadow", replies)
    off_result = off.execute("Check Drone 1")
    shadow_result = shadow.execute("Check Drone 1")
    assert off._task_progress is None
    assert shadow._task_progress is not None
    assert request_dump(off) == request_dump(shadow)
    assert off.llm.bound_tools == shadow.llm.bound_tools
    assert len(off.llm.requests) == len(shadow.llm.requests) == 2
    assert off_actions == shadow_actions == [("get_drone_status", {"drone_id": "u1"})]
    assert off_result == shadow_result


@pytest.mark.parametrize("stream", [False, True])
def test_summary_preserves_tool_results_and_allows_actions_after_partial_move(stream):
    replies = [call("navigate_to_and_sense", "move", drone_id="u1", x=9, y=0, z=4),
               call("take_photo", "photo", drone_id="u1"), AIMessage(content="Done [TASK DONE]")]
    off, off_actions = make_agent("off", replies)
    enabled, enabled_actions = make_agent("summary", replies)
    kwargs = {"step_callback": lambda _: None} if stream else {}
    baseline = off.execute("Drone 1: approach Circle Target 1 and take a photo.", **kwargs)
    result = enabled.execute("Drone 1: approach Circle Target 1 and take a photo.", **kwargs)
    assert result == baseline
    assert enabled_actions == off_actions
    assert len(enabled.llm.requests) == 3
    for baseline_request, enabled_request in zip(off.llm.requests, enabled.llm.requests):
        assert [m.model_dump(exclude={"id"}) for m in baseline_request if not isinstance(m, SystemMessage)] == [
            m.model_dump(exclude={"id"}) for m in enabled_request if not isinstance(m, SystemMessage)]
        assert len([m for m in enabled_request if isinstance(m, SystemMessage)]) == 1
        assert enabled_request[0].content.startswith("Frozen baseline system prompt.")
        assert len(enabled_request[0].content) <= len("Frozen baseline system prompt.") + 4100
    assert enabled.llm.requests[-1][0].content != off.llm.requests[-1][0].content
    assert [m.content for m in enabled.llm.requests[-1] if isinstance(m, ToolMessage)] == [
        m.content for m in off.llm.requests[-1] if isinstance(m, ToolMessage)]


def test_observer_setup_failure_keeps_baseline_model_calls(monkeypatch):
    import task_progress
    def broken(*args, **kwargs):
        raise ValueError("unsupported observation")
    monkeypatch.setattr(task_progress, "TaskProgressLedger", broken)
    agent, actions = make_agent("summary", [AIMessage(content="Done [TASK DONE]")])
    result = agent.execute("Broadcast the user message.")
    assert result["success"] is True
    assert agent._task_progress is None
    assert agent.llm.requests[0][0].content == "Frozen baseline system prompt."
    assert len(agent.llm.requests) == 1 and actions == []


def test_empty_response_retry_keeps_ledger_but_next_command_resets_it():
    agent, _ = make_agent("summary", [call("get_drone_status", "read", drone_id="u1"),
                                       AIMessage(content=""), AIMessage(content="Done [TASK DONE]"),
                                       AIMessage(content="Done [TASK DONE]")])
    original = agent._create_agent_runtime
    ledgers = []
    def build():
        ledgers.append(agent._task_progress)
        return original()
    agent._create_agent_runtime = build
    # Use the original empty-output recovery boundary without changing baseline
    # final-output extraction, which may otherwise fall back to the user text.
    first = agent.execute("")
    assert first["empty_response_retries"] == 1
    assert ledgers[0] is ledgers[1]
    second = agent.execute("Next mission: check Drone 2")
    assert second["success"] is True
    assert ledgers[2] is not ledgers[0]


def test_mode_configuration_default_off_explicit_override_and_invalid_value(monkeypatch):
    class SessionClient:
        def __init__(self, *args, **kwargs):
            pass
        def get_current_session(self):
            return {"id": "s1"}
    monkeypatch.setattr(uav_agent, "UAVAPIClient", SessionClient)
    monkeypatch.setattr(UAVControlAgent, "_create_llm", lambda *a, **kw: None)
    monkeypatch.setattr(UAVControlAgent, "_create_tools", lambda *a: [])
    monkeypatch.setattr(UAVControlAgent, "_create_agent_runtime", lambda *a: None)
    monkeypatch.delenv("UAV_TASK_PROGRESS_MODE", raising=False)
    assert UAVControlAgent().task_progress_mode == "off"
    monkeypatch.setenv("UAV_TASK_PROGRESS_MODE", "summary")
    assert UAVControlAgent().task_progress_mode == "summary"
    assert UAVControlAgent(task_progress_mode="off").task_progress_mode == "off"
    monkeypatch.setenv("UAV_TASK_PROGRESS_MODE", "invalid")
    with pytest.raises(ValueError, match="Unsupported task_progress_mode"):
        UAVControlAgent()
