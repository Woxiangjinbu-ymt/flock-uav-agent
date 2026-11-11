"""Scoped completion, explicit schemas and public graph decisions; no provider."""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
sys.path.insert(0, str(ROOT / "scripts"))
from agent_profile import AgentProfile
from template.system_prompt import build_system_prompt
from tool_input_contract import adapt_input_json_tools
from test_task_progress_integration import make_agent
from uav_agent import UAVControlAgent
from general_agent_local import validate_trace


STATE = "Command drone Drone 1 to take off to an altitude of 12 meters and hover in place."
ACTION = "Command drone Drone 1 to take off to an altitude of 12 meters and then execute a hover command."


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Local integration cannot call a provider")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for key in ("UAV_COMPLETION_CONTRACT", "UAV_TOOL_INPUT_CONTRACT"):
        monkeypatch.delenv(key, raising=False)


def decision(name, ident, arguments=None, *, encoding="object"):
    args = {} if arguments is None else {"input_json": arguments if encoding == "object" else json.dumps(arguments)}
    return AIMessage(content="", tool_calls=[{"name": name, "id": ident, "args": args, "type": "tool_call"}])


def graph(names, *, encoding="object", completion="hover_completion_v1", inputs="json_object_or_string_v1"):
    state = {"id": "u1", "name": "Drone 1", "status": "idle", "speed": 0,
             "position": {"x": 1, "y": 2, "z": 0}}
    calls = []

    @tool
    def list_drones() -> str:
        """List publicly visible drone identities and current positions."""
        return json.dumps([state])

    @tool
    def take_off(input_json: str) -> str:
        """Take off; input should be a JSON string with drone_id and altitude."""
        args = json.loads(input_json)
        calls.append(("take_off", args))
        state.update(status="hovering", position={"x": 1, "y": 2, "z": args["altitude"]})
        return json.dumps({"drone_id": "u1", "command": "take_off", "status": "success"})

    @tool
    def hover(input_json: str) -> str:
        """Execute hover with the drone_id supplied as a JSON string."""
        args = json.loads(input_json)
        calls.append(("hover", args))
        return json.dumps({"drone_id": "u1", "command": "hover", "status": "success"})

    @tool
    def get_drone_status(input_json: str) -> str:
        """Read the public drone_id state using a JSON string."""
        json.loads(input_json)
        return json.dumps(state)

    replies = [decision("list_drones", "names")]
    for i, name in enumerate(names):
        params = {"drone_id": "u1", **({"altitude": 12} if name == "take_off" else {})}
        replies.append(decision(name, f"call-{i}", params, encoding=encoding))
    replies.append(AIMessage(content="Mission done. [TASK DONE]"))
    agent, _ = make_agent("evidence_r2", replies)
    agent.completion_contract, agent.tool_input_contract = completion, inputs
    agent.input_layout = "compact_tools_v1"
    agent._create_tools = lambda _: [list_drones, take_off, hover, get_drone_status]
    agent._build_system_prompt = UAVControlAgent._build_system_prompt.__get__(agent)
    return agent, calls


@pytest.mark.parametrize("encoding", ["string", "object"])
@pytest.mark.parametrize("version", [1, 2])
def test_state_goal_verified_without_redundant_hover_and_no_extra_model_calls(encoding, version):
    agent, calls = graph(["take_off", "get_drone_status"], encoding=encoding,
                         completion=f"hover_completion_v{version}", inputs=f"json_object_or_string_v{version}")
    result = agent.execute(STATE)
    assert result["success"] is True
    assert result["task_completed"] is True
    assert result["completion_assessment"]["completion_claim_accepted"] is True
    assert result["completion_assessment"]["schema"] == f"hover-completion-v{version}"
    assert [name for name, _ in calls] == ["take_off"]
    assert len(agent.llm.requests) == 4
    assert result["output"] == result["model_output"] == "Mission done. [TASK DONE]"
    prompt = agent.llm.requests[0][0].content
    assert "pass a JSON object (preferred)" in prompt and "AVAILABLE TOOLS:\n- list_drones\n- take_off" in prompt


@pytest.mark.parametrize("command,names", [(STATE, ["take_off"]), (ACTION, ["take_off", "get_drone_status"])])
def test_missing_evidence_rejects_claim_without_rewriting_model_history_or_run_success(command, names):
    agent, calls = graph(names)
    result = agent.execute(command)
    assert result["success"] is True  # Normal-return status is not task completion.
    assert result["task_completed"] is None
    assert result["completion_assessment"]["completion_claim_accepted"] is False
    assert "[TASK DONE]" not in result["output"]
    assert result["model_output"] == "Mission done. [TASK DONE]"
    assert agent.llm.replies[-1].content == result["model_output"]
    assert [n for n, _ in calls] == ["take_off"]
    assert len(agent.llm.requests) == len(names) + 2


def test_explicit_action_requires_hover_then_fresh_state():
    agent, calls = graph(["take_off", "hover", "get_drone_status"])
    result = agent.execute(ACTION)
    assert result["task_completed"] is True
    assert [n for n, _ in calls] == ["take_off", "hover"]


@pytest.mark.parametrize("answer", ["I have not yet verified the final state.", "Mission completed successfully."])
def test_final_answer_without_marker_cannot_override_missing_evidence(answer):
    agent, _ = graph(["take_off"])
    agent.llm.replies[-1] = AIMessage(content=answer)
    result = agent.execute(STATE)
    assert result["success"] is True and result["task_completed"] is None
    assert result["completion_assessment"]["completion_marker_present"] is False
    assert result["completion_assessment"]["completion_claim_accepted"] is False
    assert result["model_output"] == answer
    assert result["output"].startswith("Task completion is not verified")
    prompt = agent.llm.requests[0][0].content
    assert "Always end the final answer" not in prompt
    assert "otherwise state that completion is unverified" in prompt


def test_empty_response_recovery_uses_scoped_completion_rule_only_when_enabled():
    agent, _ = graph([])
    recovery = agent._empty_response_recovery_command(STATE, [], 1)
    assert "otherwise state that completion is unverified" in recovery
    assert "final answer must end" not in recovery
    assert "finish only when the mission is complete" not in recovery
    assert "explicitly state why completion remains unverified" in recovery
    assert "must end with [TASK DONE]" in agent._empty_response_recovery_command("Take a photo.", [], 1)
    agent.completion_contract = "off"
    assert "must end with [TASK DONE]" in agent._empty_response_recovery_command(STATE, [], 1)


def test_off_and_legacy_representation_do_not_gain_implicit_new_acceptance():
    agent, calls = graph(["take_off", "get_drone_status"], completion="off", inputs="string_v1")
    result = agent.execute(STATE)
    assert "completion_assessment" not in result
    assert not calls  # Legacy schema still rejects objects before the backend.


def profile_value():
    value = json.loads((ROOT / "experiments/harness-cost-v1/recommended-profile.json").read_text())
    value.update(schema_version=4, profile_id="general-agent-v4-local")
    value["agent"].update(completion_contract="hover_completion_v1", tool_input_contract="json_object_or_string_v1")
    return value


def test_versioned_profile_binds_both_new_contracts_and_keeps_no_online_authorization():
    profile = AgentProfile(profile_value())
    assert profile.agent_kwargs()["completion_contract"] == "hover_completion_v1"
    assert profile.agent_kwargs()["tool_input_contract"] == "json_object_or_string_v1"
    assert profile.environment()["UAV_AGENT_INPUT_LAYOUT"] == "compact_tools_v1"
    assert profile.environment()["UAV_HARNESS_REVISION"] == "search_confirm_v9"
    assert profile.to_dict()["online_authorized_executions"] == 0
    legacy = json.loads((ROOT / "experiments/harness-cost-v1/recommended-profile.json").read_text())
    old = AgentProfile(legacy)
    assert old.environment()["UAV_COMPLETION_CONTRACT"] == "off"
    assert old.environment()["UAV_TOOL_INPUT_CONTRACT"] == "string_v1"
    assert "completion_contract" not in old.agent_kwargs()
    legacy["agent"]["completion_contract"] = "hover_completion_v1"
    with pytest.raises(ValueError):
        AgentProfile(legacy)


def test_v5_local_candidate_is_explicit_and_v4_selectors_stay_frozen():
    value = profile_value()
    value["agent"].update(completion_contract="hover_completion_v2", tool_input_contract="json_object_or_string_v2")
    with pytest.raises(ValueError):
        AgentProfile(value)
    value.update(schema_version=5, profile_id="general-agent-v5-local")
    profile = AgentProfile(value)
    assert profile.agent_kwargs()["completion_contract"] == "hover_completion_v2"
    assert profile.agent_kwargs()["tool_input_contract"] == "json_object_or_string_v2"
    assert profile.environment()["UAV_COMPLETION_CONTRACT"] == "hover_completion_v2"
    assert profile.environment()["UAV_TOOL_INPUT_CONTRACT"] == "json_object_or_string_v2"
    assert profile.to_dict()["online_authorized_executions"] == 0
    assert profile.to_dict()["execution"]["transport"] == "mock"


@pytest.mark.parametrize("path,value", [("completion_contract", "arbitrary_verifier"), ("tool_input_contract", "coerce_anything")])
def test_unknown_contracts_rejected_before_any_provider_or_api_initialization(path, value):
    with pytest.raises(ValueError):
        UAVControlAgent(**{path: value})
    p = profile_value()
    p["agent"][path] = value
    with pytest.raises(ValueError):
        AgentProfile(p)


def test_completion_requires_a_public_ledger():
    with pytest.raises(ValueError, match="ledger"):
        UAVControlAgent(completion_contract="hover_completion_v1", task_progress_mode="off")


def test_trace_object_mode_is_explicit_and_old_trace_is_unchanged():
    base = {"tool": "take_off", "arguments": {"input_json": {"drone_id": "u1", "altitude": 12}}}
    assert validate_trace([base]) == [base]
    new = {**base, "input_json_encoding": "object"}
    assert validate_trace([new]) == [new]
    for invalid in (True, "string", None, {}):
        with pytest.raises(ValueError):
            validate_trace([{**base, "input_json_encoding": invalid}])


def test_unknown_prompt_contract_rejected():
    with pytest.raises(ValueError):
        build_system_prompt([], tool_input_contract="unknown")
