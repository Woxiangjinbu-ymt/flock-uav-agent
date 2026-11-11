"""Opt-in tool-catalog compaction preserves native schemas and recovery evidence."""
import copy
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "agent4drone")))
import uav_agent
from flockcue.runtime.integration import HARNESS_RULES, HarnessMiddleware
from flockcue.evidence.middleware import MARKER
from flockcue.evidence.contact_evidence import TaskEvidenceLedgerR2
from template.system_prompt import build_system_prompt


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Input-layout tests must not access a network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.delenv("UAV_AGENT_INPUT_LAYOUT", raising=False)


def tools():
    @tool
    def navigate(input_json: str) -> str:
        """Navigate with explicit input_json. Partial effects are not completion."""
        return input_json
    @tool
    def observe() -> str:
        """Return an observation; never infer an unseen target's absence."""
        return "observed"
    return [navigate, observe]


def outside_catalog(text):
    prefix, remainder = text.split("AVAILABLE TOOLS:\n", 1)
    catalog, suffix = remainder.rsplit("\n\nBe concise, safe, and operationally precise.", 1)
    return prefix, catalog, suffix


def test_unset_and_explicit_full_layout_have_legacy_catalog_bytes():
    native = tools()
    implicit = build_system_prompt(native)
    explicit = build_system_prompt(native, input_layout="full_v1")
    assert implicit == explicit
    catalog = outside_catalog(implicit)[1]
    assert catalog == ("- navigate: Navigate with explicit input_json. Partial effects are not completion.\n"
                       "- observe: Return an observation; never infer an unseen target's absence.")


def test_compact_changes_only_catalog_and_preserves_order_and_all_rules():
    native = tools()
    full = build_system_prompt(native)
    compact = build_system_prompt(native, input_layout="compact_tools_v1")
    before, after = outside_catalog(full), outside_catalog(compact)
    assert before[0] == after[0] and before[2] == after[2]
    assert after[1] == "- navigate\n- observe"
    assert "For tools with an `input_json` argument, pass a JSON string." in compact
    assert "Do not treat a partial path as complete or blindly repeat a movement with an uncertain result." in compact


def test_native_schema_descriptions_and_parameter_contract_are_not_modified():
    native = tools()
    before = copy.deepcopy([convert_to_openai_tool(t) for t in native])
    for layout in ("full_v1", "compact_tools_v1"):
        build_system_prompt(native, input_layout=layout)
        assert [convert_to_openai_tool(t) for t in native] == before
    function = before[0]["function"]
    assert function["description"] == native[0].description
    assert function["parameters"]["properties"]["input_json"]["type"] == "string"


def test_compaction_does_not_silently_normalize_invalid_model_arguments():
    native = tools()
    build_system_prompt(native, input_layout="compact_tools_v1")
    with pytest.raises(ValueError):
        native[0].invoke({"input_json": {"drone_id": "d1"}})
    assert native[0].invoke({"input_json": '{"drone_id":"d1"}'}) == '{"drone_id":"d1"}'


@pytest.mark.parametrize("layout", ["", "compact", "FULL_V1", " full_v1", 1, True, None, {}])
def test_unknown_layout_rejected_by_template(layout):
    with pytest.raises(ValueError, match="input_layout"):
        build_system_prompt(tools(), input_layout=layout)


@pytest.mark.parametrize("environment,explicit,expected", [
    (None, None, "full_v1"), ("compact_tools_v1", None, "compact_tools_v1"),
    ("compact_tools_v1", "full_v1", "full_v1"), ("full_v1", "compact_tools_v1", "compact_tools_v1"),
])
def test_constructor_explicit_selection_precedes_environment_without_provider(environment, explicit, expected, monkeypatch):
    if environment is not None:
        monkeypatch.setenv("UAV_AGENT_INPUT_LAYOUT", environment)
    class StopBeforeModel(Exception):
        pass
    def stop(*args, **kwargs):
        raise StopBeforeModel
    monkeypatch.setattr(uav_agent, "UAVAPIClient", stop)
    agent = uav_agent.UAVControlAgent.__new__(uav_agent.UAVControlAgent)
    with pytest.raises(StopBeforeModel):
        agent.__init__(input_layout=explicit)
    assert agent.input_layout == expected
    # Existing instances do not silently switch when process environment changes.
    monkeypatch.setenv("UAV_AGENT_INPUT_LAYOUT", "invalid")
    agent.tools = tools()
    assert agent._build_system_prompt() == build_system_prompt(agent.tools, input_layout=expected)


def test_invalid_environment_rejected_before_any_client_construction(monkeypatch):
    monkeypatch.setenv("UAV_AGENT_INPUT_LAYOUT", "typo")
    def forbidden(*args, **kwargs):
        raise AssertionError("Client must not be constructed with invalid input layout")
    monkeypatch.setattr(uav_agent, "UAVAPIClient", forbidden)
    with pytest.raises(ValueError, match="input_layout"):
        uav_agent.UAVControlAgent()


def test_agent_hook_keeps_harness_rules_under_both_layouts():
    agent = uav_agent.UAVControlAgent.__new__(uav_agent.UAVControlAgent)
    agent.tools, agent._harness = tools(), object()
    for layout in ("full_v1", "compact_tools_v1"):
        agent.input_layout = layout
        assert agent._build_system_prompt() == build_system_prompt(agent.tools, input_layout=layout) + "\n\n" + HARNESS_RULES


def test_search_v8_tool_description_inherits_v7_without_changing_old_schema(monkeypatch):
    from blackboard import PerceptionBlackboard
    from flockcue.runtime.integration import install_harness
    from flockcue.evidence.contact_evidence import SessionEvidenceIndexR2
    rendered = {}
    for revision in ("observed_target_v7", "search_progress_v8"):
        monkeypatch.setenv("UAV_HARNESS_REVISION", revision)
        monkeypatch.setenv("UAV_HARNESS_STATIC_OBSTACLES", "1")
        board = PerceptionBlackboard(session_id="scene")
        agent = SimpleNamespace(blackboard=board, client=SimpleNamespace(), tools=[],
                                _harness_task_started=0.0, _harness_cancel_check=lambda: False)
        ledger = TaskEvidenceLedgerR2("task", "command", index=SessionEvidenceIndexR2("scene"))
        install_harness(agent, ledger)
        rendered[revision] = convert_to_openai_tool(agent.tools[-1])
    old, new = (rendered[key]["function"] for key in ("observed_target_v7", "search_progress_v8"))
    assert new["parameters"] == old["parameters"]
    assert new["description"].startswith(old["description"] + "\nSearch progress v8")
    assert "search_progress_stalled" not in old["description"]
    assert "not proof of impossibility or task completion" in new["description"]
    assert "No action, API, round or repair budget is added." in new["description"]


@pytest.mark.parametrize("kind", ["unknown", "partial"])
def test_raw_history_reasoning_tool_links_and_recovery_projection_identical(kind, monkeypatch):
    monkeypatch.setenv("UAV_TASK_EXECUTION_STATE", "1")
    command = "Drone 1 flies to target Fixed Target 1 and takes a photo."
    ledger = TaskEvidenceLedgerR2(command, "command")
    receipt = ({"tool_exception": "Request timed out before response was received"} if kind == "unknown" else
               {"status": "partial_success", "successful_points": [[1, 2, 3]], "unsuccessful_points": [[4, 5, 6]],
                "successful_points_count": 1, "unsuccessful_points_count": 1})
    ledger.observe_tool("move_along_path", {"drone_id": "d1", "waypoints": [[1, 2, 3], [4, 5, 6]]}, receipt, "raw-call")
    runtime = SimpleNamespace(snapshot=lambda: {"task_id": "command", "frames": [], "events": []})
    raw = [HumanMessage(content=command), AIMessage(content="", additional_kwargs={"reasoning_content": "original reasoning stays"},
        tool_calls=[{"name": "navigate", "args": {"input_json": "{}"}, "id": "raw-call", "type": "tool_call"}]),
        ToolMessage(content=json.dumps(receipt), tool_call_id="raw-call")]
    raw_before = copy.deepcopy(raw)
    native = tools()
    prepared = []
    for layout in ("full_v1", "compact_tools_v1"):
        middleware = HarnessMiddleware(ledger, runtime)
        request = ModelRequest(model=object(), system_message=SystemMessage(content=build_system_prompt(native, input_layout=layout)),
            messages=raw, tools=native)
        prepared.append(middleware._prepare(request))
    assert raw == raw_before
    assert prepared[0].messages == prepared[1].messages
    assert prepared[0].messages[1].additional_kwargs == {"reasoning_content": "original reasoning stays"}
    assert prepared[0].messages[2].tool_call_id == "raw-call"
    marker = "[Execution recovery contract: advisory public evidence, not task completion]"
    projections = [json.loads(p.system_message.content.split(marker)[1]) for p in prepared]
    assert projections[0] == projections[1]
    assert any(item["category"] == {"unknown": "unknown_effect", "partial": "partial_completion"}[kind]
               for item in projections[0]["items"])
    assert projections[0]["automatic_retry"] is False
    assert MARKER in prepared[0].messages[0].content
