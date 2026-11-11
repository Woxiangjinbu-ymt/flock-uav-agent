"""Actual installed SDK against synthetic MockTransport, never a provider."""
import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import openai
import pytest
from langchain_core.messages import AIMessage, ChatMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models import base as sdk_base

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import harness_deepseek_adapter as adapter

httpx = adapter.httpx
TOOLS = [{"type": "function", "function": {"name": "read_public_status", "description": "Synthetic public fixture",
    "parameters": {"type": "object", "properties": {"actor": {"type": "string"}}, "required": ["actor"]}}}]


@pytest.fixture(autouse=True)
def network_forbidden(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Network forbidden: local adapter test only")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, reject)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def response(index=0, *, tool=False, reasoning="synthetic local reasoning", status=200):
    message = {"role": "assistant", "content": "公开答复"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool:
        message["content"] = ""
        message["tool_calls"] = [{"id": "call_甲" if index == 0 else "call_乙", "type": "function",
            "function": {"name": "read_public_status", "arguments": '{"actor":"d1"}'}}]
    return {"id": "synthetic-response-" + str(index), "object": "chat.completion", "created": 1,
        "model": "deepseek-flash", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}


class Wire:
    def __init__(self, replies=None, status=200):
        self.replies = replies or [response()]
        self.status = status
        self.requests = []
        self.response_bodies = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        assert request.method == "POST"
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer " + adapter.SYNTHETIC_KEY
        assert len(self.requests) < len(self.replies), "Unexpected retry or extra request"
        self.requests.append(bytes(request.content))
        body = json.dumps(self.replies[len(self.requests) - 1], ensure_ascii=False, separators=(",", ":")).encode()
        self.response_bodies.append(body)
        return httpx.Response(self.status, content=body, headers={"content-type": "application/json"})

    @property
    def payloads(self):
        return [json.loads(x) for x in self.requests]


@pytest.fixture
def build():
    models = []
    def create(wire=None, **kwargs):
        wire = wire or Wire()
        options = dict(thinking="disabled", max_tokens=64, transport=wire.transport)
        options.update(kwargs)
        model = adapter.make_local_deepseek_candidate(**options)
        models.append(model)
        return model, wire
    yield create
    for model in models:
        model.http_client.close()
        asyncio.run(model.http_async_client.aclose())


def records(wire):
    def body(value):
        return {"base64": base64.b64encode(value).decode(), "sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    return [{"request": body(request), "response": body(response_body)}
        for request, response_body in zip(wire.requests, wire.response_bodies)]


def test_real_sdk_baseline_and_candidate_wire_history(build):
    # Other component tests import the live Agent, which patches the SDK at
    # process scope. Run the unchanged native-SDK control in its own process.
    if getattr(sdk_base, "_uav_reasoning_content_patched", False) and "FLOCKCUE_ISOLATED_SDK_TEST" not in os.environ:
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{__file__}::test_real_sdk_baseline_and_candidate_wire_history"],
            env={**os.environ, "FLOCKCUE_ISOLATED_SDK_TEST": "1"}, check=True,
        )
        return
    assert not getattr(sdk_base, "_uav_reasoning_content_patched", False)
    baseline_wire = Wire([response(0, tool=True, reasoning="r0"), response(1, reasoning="r1")])
    sync = openai.DefaultHttpxClient(transport=baseline_wire.transport, trust_env=False, follow_redirects=False)
    asynchronous = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(lambda request: None), trust_env=False)
    baseline = ChatOpenAI(model="deepseek-flash", api_key=adapter.SYNTHETIC_KEY, base_url=adapter.BASE_URL,
        max_tokens=64, temperature=None, max_retries=0, streaming=False, use_responses_api=False,
        http_client=sync, http_async_client=asynchronous,
        extra_body={"thinking": {"type": "enabled"}}, reasoning_effort="high")
    try:
        history = [HumanMessage("读取公开状态")]
        first = baseline.invoke(history, tools=TOOLS)
        assert first.additional_kwargs.get("reasoning_content") is None
        history += [first, ToolMessage("公开回执", tool_call_id="call_甲")]
        baseline.invoke(history, tools=TOOLS)
    finally:
        sync.close(); asyncio.run(asynchronous.aclose())
    replies = [response(0, tool=True, reasoning="r0"), response(1, tool=True, reasoning="r1"),
               response(2, reasoning="r2"), response(3, reasoning="r3")]
    candidate, wire = build(Wire(replies), thinking="enabled", reasoning_effort="high")
    history = [HumanMessage("读取公开状态")]
    first = candidate.invoke(history, tools=TOOLS)
    assert first.additional_kwargs["reasoning_content"] == "r0"
    assert first.tool_calls[0]["id"] == "call_甲"
    history += [first, ToolMessage("公开回执", tool_call_id="call_甲")]
    second = candidate.invoke(history, tools=TOOLS)
    assert second.additional_kwargs["reasoning_content"] == "r1"
    history += [second, ToolMessage("公开回执二", tool_call_id="call_乙")]
    third = candidate.invoke(history, tools=TOOLS)
    history += [third, HumanMessage("新的用户回合")]
    before = copy.deepcopy(history)
    candidate.invoke(history, tools=TOOLS)
    assert history == before
    assert all("max_completion_tokens" in p and "max_tokens" not in p for p in baseline_wire.payloads)
    assert all("max_tokens" in p and "max_completion_tokens" not in p for p in wire.payloads)
    assert "reasoning_content" not in baseline_wire.payloads[1]["messages"][1]
    assert wire.payloads[1]["messages"][1]["reasoning_content"] == "r0"
    assert [m["reasoning_content"] for m in wire.payloads[3]["messages"] if m["role"] == "assistant"] == ["r0", "r1", "r2"]
    assert all(p["thinking"] == {"type": "enabled"} and p["reasoning_effort"] == "high"
        and "temperature" not in p and p["stream"] is False for p in wire.payloads)
    evidence_path = os.getenv("DEEPSEEK_ADAPTER_EVIDENCE")
    if evidence_path:
        value = {"scope": "Unpatched installed SDK vs local-only candidate; synthetic messages and key; no provider",
            "baseline": records(baseline_wire), "candidate": records(wire),
            "baseline_response_reasoning_lost": True, "candidate_response_reasoning_preserved": True,
            "tool_call_ids_preserved": True, "cross_human_reasoning_preserved_with_tools": True,
            "real_provider_acceptance": "unverified"}
        with Path(evidence_path).open("x") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2); stream.write("\n")


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_explicit_efforts_serialize(build, effort):
    model, wire = build(thinking="enabled", reasoning_effort=effort)
    model.invoke([HumanMessage("hello")])
    assert wire.payloads[0]["reasoning_effort"] == effort
    assert "temperature" not in wire.payloads[0]


def test_disabled_omits_all_reasoning_and_preserves_tool_ids(build):
    model, wire = build()
    history = [HumanMessage("old"), AIMessage(content="", additional_kwargs={"reasoning_content": "old reason"},
        tool_calls=[{"id": "original-id", "name": "read_public_status", "args": {"actor": "d1"}}]),
        ToolMessage("public", tool_call_id="original-id"), HumanMessage("new")]
    result = model.invoke(history, tools=TOOLS)
    p = wire.payloads[0]
    assert p["thinking"] == {"type": "disabled"} and p["temperature"] == 0.1
    assert "reasoning_effort" not in p
    assert all("reasoning_content" not in m for m in p["messages"])
    assert p["messages"][1]["tool_calls"][0]["id"] == p["messages"][2]["tool_call_id"] == "original-id"
    assert "reasoning_content" not in result.additional_kwargs


def test_no_tools_clears_history_reasoning_even_enabled(build):
    model, wire = build(thinking="enabled", reasoning_effort="high")
    model.invoke([HumanMessage("old"), AIMessage("answer", additional_kwargs={"reasoning_content": "prior"}), HumanMessage("new")])
    assert "reasoning_content" not in wire.payloads[0]["messages"][1]


@pytest.mark.parametrize("changes", [
    {"thinking": None}, {"thinking": "auto"}, {"thinking": True},
    {"max_tokens": 0}, {"max_tokens": -1}, {"max_tokens": True}, {"max_tokens": 1.5}, {"max_tokens": 393217},
    {"model": "uncovered-model"}, {"reasoning_effort": "high"}, {"temperature": float("nan")},
    {"temperature": True}, {"thinking": "enabled"}, {"thinking": "enabled", "reasoning_effort": "medium"},
    {"thinking": "enabled", "reasoning_effort": "high", "temperature": 0.1},
])
def test_invalid_factory_configuration_rejected_before_dispatch(build, changes):
    wire = Wire()
    with pytest.raises(ValueError):
        build(wire, **changes)
    assert wire.requests == []


def test_real_transport_rejected():
    class NoMock(httpx.BaseTransport):
        def handle_request(self, request):
            raise AssertionError("Must not dispatch")
    with pytest.raises(ValueError, match="MockTransport"):
        adapter.make_local_deepseek_candidate(thinking="disabled", max_tokens=64, transport=NoMock())


@pytest.mark.parametrize("maximum", [1, 393216])
def test_output_boundaries_use_actual_max_tokens(build, maximum):
    model, wire = build(max_tokens=maximum)
    model.invoke("hello")
    assert wire.payloads[0]["max_tokens"] == maximum


@pytest.mark.parametrize("kwargs", [
    {"stream": True}, {"stream": False}, {"extra_body": {"thinking": {"type": "enabled"}}},
    {"max_tokens": 64}, {"max_completion_tokens": 64}, {"response_format": {"type": "json_object"}},
    {"reasoning_effort": "high"}, {"extra_headers": {"x": "y"}},
])
def test_unknown_or_override_parameters_fail_before_dispatch(build, kwargs):
    model, wire = build()
    with pytest.raises(ValueError):
        model.invoke("hello", **kwargs)
    assert wire.requests == []


@pytest.mark.parametrize("messages", [
    [ChatMessage(role="surprise", content="x"), HumanMessage("hello")],
    [HumanMessage("hello"), ToolMessage("wrong", tool_call_id="unknown")],
    [HumanMessage("hello"), AIMessage("", tool_calls=[{"id": "a", "name": "read_public_status", "args": {}}])],
    [HumanMessage("hello"), AIMessage("", tool_calls=[{"id": "a", "name": "read_public_status", "args": {}}]), ToolMessage("wrong", tool_call_id="b")],
    [HumanMessage("hello"), AIMessage("", tool_calls=[{"id": "a", "name": "read_public_status", "args": {}}, {"id": "a", "name": "read_public_status", "args": {}}])],
    [HumanMessage("hello"), AIMessage("", tool_calls=[{"id": "a", "name": "read_public_status", "args": {}}]), HumanMessage("new")],
])
def test_invalid_roles_or_tool_identity_fail_before_dispatch(build, messages):
    model, wire = build()
    with pytest.raises(ValueError):
        model.invoke(messages)
    assert wire.requests == []


def test_missing_old_reasoning_with_tools_is_not_invented(build):
    model, wire = build(thinking="enabled", reasoning_effort="high")
    with pytest.raises(ValueError, match="every prior assistant"):
        model.invoke([HumanMessage("old"), AIMessage("old answer"), HumanMessage("new")], tools=TOOLS)
    assert wire.requests == []


@pytest.mark.parametrize("choice", ["required", {"type": "function", "function": {"name": "read_public_status"}}])
def test_thinking_forced_tool_choices_rejected(build, choice):
    model, wire = build(thinking="enabled", reasoning_effort="high")
    with pytest.raises(ValueError):
        model.invoke("hello", tools=TOOLS, tool_choice=choice)
    assert wire.requests == []


def test_stream_and_async_rejected_without_dispatch(build):
    model, wire = build()
    with pytest.raises(ValueError):
        list(model.stream("hello"))
    with pytest.raises(ValueError):
        asyncio.run(model.ainvoke("hello"))
    async def streaming():
        async for _ in model.astream("hello"):
            pass
    with pytest.raises(ValueError):
        asyncio.run(streaming())
    assert wire.requests == []


def test_http_error_is_not_retried(build):
    model, wire = build(Wire([{"error": {"message": "synthetic failure", "type": "server_error"}}], status=500))
    with pytest.raises(openai.APIStatusError):
        model.invoke("hello")
    assert len(wire.requests) == 1


@pytest.mark.parametrize("reasoning", [None, 12, {"unexpected": "object"}])
def test_malformed_thinking_tool_response_rejected(build, reasoning):
    model, wire = build(Wire([response(tool=True, reasoning=reasoning)]), thinking="enabled", reasoning_effort="high")
    with pytest.raises(ValueError):
        model.invoke("hello", tools=TOOLS)
    assert len(wire.requests) == 1


def test_candidate_does_not_patch_sdk_converter(build):
    before = (sdk_base._convert_dict_to_message, sdk_base._convert_message_to_dict)
    model, _ = build(thinking="enabled", reasoning_effort="low")
    model.invoke("hello")
    assert before == (sdk_base._convert_dict_to_message, sdk_base._convert_message_to_dict)


@pytest.mark.parametrize("bad", ["missing", "empty", "duplicate", "invalid_arguments", "legacy_function"])
def test_invalid_response_tool_identity_rejected_before_return(build, bad):
    payload = response(tool=True)
    message = payload["choices"][0]["message"]
    call = message["tool_calls"][0]
    if bad == "missing":
        call.pop("id")
    elif bad == "empty":
        call["id"] = ""
    elif bad == "duplicate":
        message["tool_calls"].append(copy.deepcopy(call))
    elif bad == "invalid_arguments":
        call["function"]["arguments"] = "not valid json"
    else:
        message["function_call"] = message.pop("tool_calls")[0]["function"]
    model, wire = build(Wire([payload]), thinking="enabled", reasoning_effort="high")
    with pytest.raises(ValueError):
        model.invoke("hello", tools=TOOLS)
    assert len(wire.requests) == 1


def test_response_cannot_reuse_prior_tool_identity(build):
    model, wire = build(Wire([response(0, tool=True), response(0, tool=True)]), thinking="enabled", reasoning_effort="high")
    history = [HumanMessage("hello")]
    first = model.invoke(history, tools=TOOLS)
    history += [first, ToolMessage("public", tool_call_id="call_甲")]
    with pytest.raises(ValueError, match="reused"):
        model.invoke(history, tools=TOOLS)
    assert len(wire.requests) == 2


def test_legacy_input_function_call_rejected(build):
    model, wire = build()
    with pytest.raises(ValueError, match="legacy"):
        model.invoke([HumanMessage("hello"), AIMessage("", additional_kwargs={"function_call": {"name": "old", "arguments": "{}"}})])
    assert wire.requests == []
