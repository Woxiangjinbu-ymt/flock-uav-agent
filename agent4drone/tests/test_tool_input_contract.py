"""Explicit object/string schemas and real local graph/archive behavior; no API."""
import copy
import ast
import base64
import json
from pathlib import Path
import socket
import sys

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tool_input_contract import (
    InputJsonObjectOrStringV1, InputJsonObjectOrStringV2, adapt_input_json_tools, tool_input_rules,
    validate_input_json_contract,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tool-input tests must not call a provider")
    for key in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, key, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def recording_tool():
    calls = []

    @tool
    def act(input_json: str) -> str:
        """Read a parameter object. Input should be a JSON string with: drone_id."""
        assert type(input_json) is str
        params = json.loads(input_json)
        calls.append(params)
        return json.dumps({"received": params}, ensure_ascii=False)

    return act, calls


CONTRACTS = ("json_object_or_string_v1", "json_object_or_string_v2")


def adapted(tool_value, contract="json_object_or_string_v1"):
    return adapt_input_json_tools([tool_value], contract)[0]


@pytest.mark.parametrize("value", [None, True, 1, "", "future", [], {}])
def test_unknown_contract_rejected(value):
    with pytest.raises(ValueError):
        validate_input_json_contract(value)


def test_legacy_contract_is_exact_noop_and_keeps_object_rejection():
    original, calls = recording_tool()
    tools = [original]
    before = convert_to_openai_tool(original)
    assert adapt_input_json_tools(tools, "string_v1") is tools
    assert convert_to_openai_tool(original) == before
    with pytest.raises(ValidationError):
        original.invoke({"input_json": {"drone_id": "d1"}})
    assert calls == []


def test_schema_matches_explicit_object_and_string_and_adapter_is_idempotent():
    original, calls = recording_tool()
    before = copy.deepcopy(convert_to_openai_tool(original))
    new = adapted(original)
    native = convert_to_openai_tool(new)["function"]
    assert new is not original and new.func is original.func
    assert native["parameters"]["required"] == ["input_json"]
    # LangChain's provider subset schema omits the root model_config, while
    # actual invocation still validates extra='forbid' on this args model.
    assert new.args_schema.model_json_schema()["additionalProperties"] is False
    assert {b["type"] for b in native["parameters"]["properties"]["input_json"]["anyOf"]} == {"object", "string"}
    assert "JSON object (preferred) or legacy JSON-object string" in new.description
    assert "drone_id" in new.description and convert_to_openai_tool(original) == before
    assert new.metadata["input_json_contract"] == "json_object_or_string_v1"
    assert adapted(new) is new and calls == []


@pytest.mark.parametrize("contract", CONTRACTS)
def test_object_and_legacy_string_have_identical_backend_values_without_mutation(contract):
    original, calls = recording_tool()
    new = adapted(original, contract)
    value = {"drone_id": "无人机🛰", "altitude": 12.0, "nested": {"waypoints": [1, None, True, "NaN"]}}
    raw = {"input_json": copy.deepcopy(value)}
    before = copy.deepcopy(raw)
    assert new.invoke(raw) == new.invoke({"input_json": json.dumps(value)})
    assert calls == [value, value] and raw == before
    assert calls[0] is not raw["input_json"]
    legacy = ' { "drone_id" : "d1", "altitude":12 } '
    assert new.args_schema.model_validate({"input_json": legacy}).input_json == legacy


BAD_VALUES = [
    None, True, 12, 1.5, [], [{"drone_id": "d1"}],
    "null", "true", "12", "[]", '[{"drone_id":"d1"}]', '"nested string"',
    "{", "{drone_id:'d1'}", '{"drone_id":"a","drone_id":"b"}',
    '{"waypoints":[{"x":1,"x":2}]}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}',
    '{"x":1e9999}', {"x": float("nan")}, {"x": float("inf")}, {"a": [float("-inf")]},
    {1: "not-a-string-key"}, {"nested": {1: "bad"}}, {"x": (1, 2)}, {"x": b"bytes"}, {"x": {1, 2}},
]


@pytest.mark.parametrize("value", BAD_VALUES)
@pytest.mark.parametrize("contract", CONTRACTS)
def test_invalid_envelope_or_json_never_calls_backend(value, contract):
    original, calls = recording_tool()
    with pytest.raises(ValidationError):
        adapted(original, contract).invoke({"input_json": value})
    assert calls == []


@pytest.mark.parametrize("outer", [{}, {"drone_id": "d1"}, {"input_json": {}, "drone_id": "d1"}])
@pytest.mark.parametrize("contract", CONTRACTS)
def test_missing_or_extra_outer_field_does_not_guess_input(outer, contract):
    original, calls = recording_tool()
    with pytest.raises(ValidationError):
        adapted(original, contract).invoke(outer)
    assert calls == []


@pytest.mark.parametrize("contract", CONTRACTS)
def test_cycles_and_excess_depth_are_rejected_before_backend(contract):
    original, calls = recording_tool()
    cycle = {}; cycle["again"] = cycle
    deep = cursor = {}
    for _ in range(130):
        cursor["next"] = {}; cursor = cursor["next"]
    for value in (cycle, deep):
        with pytest.raises(ValidationError):
            adapted(original, contract).invoke({"input_json": value})
    assert calls == []


@pytest.mark.parametrize("contract", CONTRACTS)
def test_no_parameter_tools_unchanged_and_mixed_argument_schema_rejected(contract):
    @tool
    def no_args() -> str:
        """A read requiring no parameters."""
        return "ok"

    @tool
    def mixed(input_json: str, outside: str) -> str:
        """A deliberately unsupported two-field tool."""
        return "unreachable"

    assert adapted(no_args, contract) is no_args
    with pytest.raises(ValueError, match="one explicit"):
        adapted(mixed, contract)


@pytest.mark.parametrize("contract,schema", [(CONTRACTS[0], InputJsonObjectOrStringV1), (CONTRACTS[1], InputJsonObjectOrStringV2)])
def test_all_production_parameter_tools_including_relative_share_contract(contract, schema):
    from blackboard import PerceptionBlackboard
    from uav_langchain_tools import create_uav_tools
    from test_uav_langchain_tools import FakeUAVClient
    client = FakeUAVClient()
    originals = create_uav_tools(client, PerceptionBlackboard())
    newer = adapt_input_json_tools(originals, contract)
    assert [t.name for t in newer] == [t.name for t in originals]
    params = [t for t in newer if "input_json" in t.args]
    assert len(params) == 28 and "move_within_distance" in {t.name for t in params}
    assert all(t.args_schema is schema for t in params)
    assert all(t.args["input_json"]["type"] == "string" for t in originals if "input_json" in t.args)
    takeoff = next(t for t in newer if t.name == "take_off")
    takeoff.invoke({"input_json": {"drone_id": "drone-1", "altitude": 12}})
    assert len(client.takeoff_calls) == 1 and client.takeoff_calls[0]["altitude"] == 12


@pytest.mark.parametrize("contract", CONTRACTS)
def test_run_harness_object_reaches_existing_control_without_widening_action_fields(monkeypatch, contract):
    from test_minimal_harness import graph_agent, SIMPLE
    replies = [AIMessage(content="", tool_calls=[{"name": "run_harness", "id": "original-object-call",
        "args": {"input_json": {"action": "start", "skill": "simple_chain"}}, "type": "tool_call"}]),
        AIMessage(content="Done [TASK DONE]")]
    agent = graph_agent(monkeypatch, replies=replies)
    world = agent.client
    create = agent._create_agent_runtime
    def create_with_explicit_contract():
        agent.tools = adapt_input_json_tools(agent.tools, contract)
        return create()
    agent._create_agent_runtime = create_with_explicit_contract
    result = agent.execute(SIMPLE)
    assert result["success"]
    assert [name for name, _ in world.calls if name in {"take_off", "rotate", "hover"}] == ["take_off", "rotate", "hover"]
    harness_tool = next(t for t in agent.tools if t.name == "run_harness")
    count = len(world.calls)
    rejected = json.loads(harness_tool.invoke({"input_json": {"action": "start", "skill": "simple_chain", "override": True}}))
    assert rejected["status"] == "rejected" and len(world.calls) == count


@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("encoding", ["object", "string"])
def test_real_sdk_wire_and_archive_keep_original_object_and_call_id(monkeypatch, tmp_path, invalid, contract, encoding):
    from test_model_io_archive_integration import sdk_agent, completion, archive_for, kind, request_bodies
    value = ["d1"] if invalid else {"drone_id": "d1"}
    if encoding == "string":
        value = json.dumps(value)
    arguments = {"input_json": value}
    raw_call = {"id": "object-call-unchanged", "type": "function", "function": {
        "name": "get_drone_status", "arguments": json.dumps(arguments)}}
    response = completion({"role": "assistant", "content": "", "tool_calls": [raw_call]})
    with sdk_agent(monkeypatch, tmp_path, mode="off", replies=[response, completion()]) as (agent, world, transport):
        create = agent._create_tools
        agent._create_tools = lambda board: adapt_input_json_tools(create(board), contract)
        result = agent.execute("Read the public status of drone d1.")
        assert result["success"]
        info, records = archive_for(result)
        assert info["complete"] and len(transport.requests) == 2
        starts = kind(records, "tool_start")
        assert starts[0]["input"]["args"] == arguments
        assert starts[0]["raw_tool_call_id"] == raw_call["id"]
        bodies = [json.loads(body) for body in request_bodies(records)]
        native = next(t["function"] for t in bodies[0]["tools"] if t["function"]["name"] == "get_drone_status")
        assert {t["type"] for t in native["parameters"]["properties"]["input_json"]["anyOf"]} == {"object", "string"}
        assert native["parameters"]["required"] == ["input_json"]
        assert ("description" in native["parameters"]["properties"]["input_json"]) == (contract == CONTRACTS[0])
        wire = bodies[1]
        history = next(m for m in wire["messages"] if m["role"] == "assistant")
        assert history["tool_calls"][0] == raw_call
        reply = next(m for m in wire["messages"] if m["role"] == "tool")
        assert reply["tool_call_id"] == raw_call["id"]
        backend_reads = [args for name, args in world.calls if name == "get_drone_status"]
        assert backend_reads == ([] if invalid else [{"drone_id": "d1"}])
        if invalid:
            assert "input_json" in reply["content"] and "object" in reply["content"]


def test_prompt_rules_match_schema_without_repeating_tool_descriptions():
    assert "pass a JSON string" in tool_input_rules("string_v1")
    text = tool_input_rules("json_object_or_string_v1")
    assert '"input_json": {"drone_id"' in text
    assert "JSON object (preferred)" in text and "legacy JSON-object string" in text
    assert "duplicate keys" in text and "nonfinite" in text


def test_v2_reuses_strict_validator_but_omits_repeated_native_field_prose():
    original, calls = recording_tool()
    new = adapted(original, CONTRACTS[1])
    native = convert_to_openai_tool(new)["function"]
    assert new.func is original.func and new.args_schema is InputJsonObjectOrStringV2
    assert new.metadata["input_json_contract"] == CONTRACTS[1]
    assert new.args_schema.object_or_legacy_string.__func__ is InputJsonObjectOrStringV1.object_or_legacy_string.__func__
    assert new.args_schema.model_json_schema()["additionalProperties"] is False
    assert native["parameters"]["properties"]["input_json"] == {
        "anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": True}]}
    assert new.description == original.description.replace("Input should be a JSON string with:", "Parameters:")
    assert "Input representation:" not in new.description and adapted(new, CONTRACTS[1]) is new
    assert calls == []


@pytest.mark.parametrize("first,second", [(CONTRACTS[0], CONTRACTS[1]), (CONTRACTS[1], CONTRACTS[0])])
def test_cross_version_rewrap_requires_original_tools_instead_of_accumulating_prose(first, second):
    original, _ = recording_tool()
    already = adapted(original, first)
    before = convert_to_openai_tool(already)
    with pytest.raises(ValueError, match="pre-existing"):
        adapted(already, second)
    assert convert_to_openai_tool(already) == before


def test_v2_rules_are_shared_once_and_keep_nested_json_and_required_field_semantics():
    text = tool_input_rules(CONTRACTS[1])
    assert "JSON object (preferred)" in text and "legacy JSON-object string" in text
    assert "one object" in text and "Nested JSON values are allowed" in text
    assert "duplicate keys" in text and "nonfinite" in text and "required fields" in text
    assert "never infer missing values" in text
    assert len(text.encode()) < len(tool_input_rules(CONTRACTS[0]).encode())


def frozen_native_tools(arm):
    run = ROOT.parent / "docs/results/deepseek-hover-pilot-20261103/run-01"
    archive = next((run / "trials" / ("state_hover-" + arm) / "policy/modelio").glob("*.jsonl"))
    for line in archive.read_text().splitlines():
        row = json.loads(line)
        if row["kind"] == "http_request":
            body = json.loads(base64.b64decode(row["data"]["body"]["base64"]))
            return body["tools"]
    raise AssertionError("Frozen real pilot has no request")


def replay_schema_tools():
    """Rebuild the frozen signatures/descriptions without executing any tool."""
    def input_tool(input_json: str) -> str:
        raise AssertionError("Schema replay must not execute a backend")
    def no_args() -> str:
        raise AssertionError("Schema replay must not execute a backend")
    originals = []
    for item in frozen_native_tools("control"):
        f = item["function"]
        func = input_tool if "input_json" in f["parameters"].get("properties", {}) else no_args
        originals.append(StructuredTool.from_function(func=func, name=f["name"], description=f["description"]))
    return originals


def test_v1_native_wire_still_exactly_matches_frozen_real_pilot_and_v2_preserves_parameters():
    originals = replay_schema_tools()
    before = [convert_to_openai_tool(t) for t in originals]
    assert before == frozen_native_tools("control")
    one = [convert_to_openai_tool(t) for t in adapt_input_json_tools(originals, CONTRACTS[0])]
    two = [convert_to_openai_tool(t) for t in adapt_input_json_tools(originals, CONTRACTS[1])]
    assert one == frozen_native_tools("candidate")
    encode = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode()
    assert encode(one) == encode(frozen_native_tools("candidate"))
    assert encode(before) == encode(frozen_native_tools("control"))
    parameters = 0
    for original, v1, v2 in zip(before, one, two):
        f = original["function"]
        if "input_json" not in f["parameters"].get("properties", {}):
            assert original == v1 == v2
            continue
        parameters += 1
        expected = copy.deepcopy(v1)
        expected["function"]["parameters"]["properties"]["input_json"].pop("description")
        expected["function"]["description"] = f["description"].replace("Input should be a JSON string with:", "Parameters:")
        assert expected == v2
    assert parameters == 29  # Includes the relative tool and injected run_harness.
    coverage = next(t["function"] for t in two if t["function"]["name"] == "generate_coverage_path")
    assert "exact arguments (input_json string) to copy into the next tool call." in coverage["description"]
    assert len(encode(two)) < 0.65 * len(encode(one))
    assert [convert_to_openai_tool(t) for t in originals] == before


def test_v1_shared_prompt_rules_still_match_frozen_source_bytes():
    path = ROOT.parent / "docs/results/deepseek-hover-pilot-20261103/run-01/source/agent4drone/tool_input_contract.py"
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "tool_input_rules"]
    namespace = {"validate_input_json_contract": lambda value: value}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    for contract in ("string_v1", CONTRACTS[0]):
        assert tool_input_rules(contract).encode() == namespace["tool_input_rules"](contract).encode()
