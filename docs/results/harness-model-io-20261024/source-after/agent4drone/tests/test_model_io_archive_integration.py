"""Actual pinned OpenAI SDK/LangChain graph against local MockTransport only.

No provider, simulator, credentials, or benchmark. Transport bytes are the
independent oracle; graph-preparation logs are not the serialized request.
"""
import asyncio
import base64
import copy
import gzip
import hashlib
import json
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from openai import _base_client

import uav_agent
from test_agent_episode_isolation import RecordedPublicWorld, ThreadCall
from uav_agent import UAVControlAgent


httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
AUTH_CANARY = "synthetic-auth-canary-not-a-real-key"
URL_CANARY = "synthetic-query-secret"
PRIVATE_CANARY = "private-checker-canary-never-policy-input"
COMMAND = "读取两架无人机公开状态，保持工具调用身份。🛰"


@pytest.fixture(autouse=True)
def no_network_or_configuration(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External sockets/configuration forbidden in model-I/O tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(uav_agent, "load_llm_settings", forbidden)
    for name in ("UAV_MODEL_IO_ARCHIVE", "UAV_MODEL_IO_ARCHIVE_DIR", "UAV_MODEL_REQUEST_AUDIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UAV_HARNESS_REVISION", "observed_target_v7")
    monkeypatch.setenv("UAV_HARNESS_STATIC_OBSTACLES", "1")
    monkeypatch.setenv("UAV_HARNESS_SCHEDULER", "applicability")
    # Exercise the SDK's actual retry loop, without its deliberate backoff sleep.
    monkeypatch.setattr(_base_client.BaseClient, "_calculate_retry_timeout", lambda *args, **kwargs: 0)


def completion(message=None, ident="mock-response"):
    message = message or {"role": "assistant", "content": "已核对 [TASK DONE]"}
    return {"id": ident, "object": "chat.completion", "created": 1,
            "model": "offline-model", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}


def tools_message():
    return {"role": "assistant", "content": "公开检查", "reasoning_content": "先分别读取，保留证据链。",
            "tool_calls": [{"id": ident, "type": "function", "function": {
                "name": "get_drone_status", "arguments": json.dumps({
                    "input_json": json.dumps({"drone_id": drone}, ensure_ascii=False)}, ensure_ascii=False)}}
                for ident, drone in (("call_甲", "d1"), ("call_乙", "d2"))]}


def raw_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class SyncChunks(httpx.SyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks, self.error, self.reads, self.closed = chunks, error, 0, False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


class AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks, self.error, self.reads, self.closed = chunks, error, 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


class SDKPublicWorld(RecordedPublicWorld):
    def get_drone_status(self, drone_id):
        # Production create_uav_tools passes this public-client argument
        # positionally; the older skill-only fake accepted keyword arguments.
        return self._call("get_drone_status", drone_id=drone_id)


class LocalTransport:
    def __init__(self, replies=None):
        self.replies = list(replies or [completion()])
        self.requests, self.response_bodies, self.streams, self.hook_reads = [], [], [], []

    def respond(self, request, *, asynchronous=False):
        index = len(self.requests)
        self.requests.append(request)
        value = self.replies[index]
        if callable(value):
            value = value(request)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, httpx.Response):
            if hasattr(value.stream, "reads"):
                self.streams.append(value.stream)
            return value
        status, payload = value if isinstance(value, tuple) else (200, value)
        body = payload if isinstance(payload, bytes) else raw_json(payload)
        self.response_bodies.append(body)
        stream = (AsyncChunks if asynchronous else SyncChunks)([body[:13], body[13:]])
        self.streams.append(stream)
        return httpx.Response(status, stream=stream, headers={"content-type": "application/json",
                              "x-auth-canary": AUTH_CANARY, "authorization": AUTH_CANARY,
                              "retry-after-ms": "1"}, request=request)

    async def arespond(self, request):
        return self.respond(request, asynchronous=True)


@contextmanager
def sdk_agent(monkeypatch, tmp_path, *, replies=None, enabled=True, mode="evidence_harness", model_options=None):
    transport = LocalTransport(replies)
    world = SDKPublicWorld()
    # This object is deliberately adjacent to, but absent from, the public API.
    world.private_checker = {"secret": PRIVATE_CANARY}
    sync = openai.DefaultHttpxClient(transport=httpx.MockTransport(transport.respond), trust_env=False)
    asynchronous = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(transport.arespond), trust_env=False)
    options = {"max_retries": 2, "max_tokens": 123, "top_p": 0.77, "seed": 17,
               "extra_body": {"thinking": {"type": "enabled"}},
               "default_query": {"synthetic_key": URL_CANARY}}
    options.update(model_options or {})
    monkeypatch.setattr(uav_agent, "UAVAPIClient", lambda *args, **kwargs: world)
    monkeypatch.setattr(uav_agent, "ChatOpenAI", lambda **kwargs: ChatOpenAI(
        http_client=sync, http_async_client=asynchronous, **options, **kwargs))
    kwargs = {"model_io_archive_dir": tmp_path / "model-io"}
    if enabled is not None:
        kwargs["model_io_archive"] = enabled
    agent = UAVControlAgent(base_url="offline://public-fixture", llm_provider="openai-compatible",
                            llm_model="offline-model", llm_api_key=AUTH_CANARY,
                            llm_base_url="https://offline.invalid/v1", verbose=False,
                            task_progress_mode=mode, toolchain_json_recording=False, **kwargs)
    agent.max_empty_response_retries = 0
    def after_archive_hook(response):
        transport.hook_reads.append((transport.streams[-1].reads if transport.streams else None,
                                     response.is_stream_consumed))
    async def after_archive_ahook(response):
        after_archive_hook(response)
    sync.event_hooks["response"].append(after_archive_hook)
    asynchronous.event_hooks["response"].append(after_archive_ahook)
    try:
        yield agent, world, transport
    finally:
        sync.close()
        asyncio.run(asynchronous.aclose())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def kind(records, value):
    return [record["data"] for record in records if record["kind"] == value]


def decoded(blob):
    result = base64.b64decode(blob["base64"], validate=True)
    assert hashlib.sha256(result).hexdigest() == blob["sha256"]
    assert len(result) == blob["length"]
    return result


def blobs(value):
    if isinstance(value, dict):
        if {"base64", "sha256", "length"}.issubset(value):
            yield value
        else:
            for item in value.values():
                yield from blobs(item)
    elif isinstance(value, list):
        for item in value:
            yield from blobs(item)


def request_bodies(records):
    result = []
    for data in kind(records, "http_request"):
        fields = list(blobs(data))
        assert len(fields) == 1, "Each HTTP request must have exactly one serialized body"
        result.append(decoded(fields[0]))
    return result


def response_bodies(records):
    output = {}
    for data in kind(records, "http_response_chunk"):
        fields = list(blobs(data))
        assert len(fields) == 1
        output.setdefault(data["attempt_id"], bytearray()).extend(decoded(fields[0]))
    return [bytes(value) for value in output.values()]


def archive_for(result):
    from model_io_archive import verify_archive
    info = result["model_io_archive"]
    assert info["path"] is not None
    records = rows(info["path"])
    checked = verify_archive(info["path"])
    assert checked["valid"], checked
    assert checked["complete"] == info["complete"]
    return info, records


def assert_no_nonpolicy_data(records):
    text = json.dumps(records, ensure_ascii=False)
    text += "\n".join(decoded(blob).decode("utf-8", errors="replace") for blob in blobs(records))
    for canary in (AUTH_CANARY, URL_CANARY, PRIVATE_CANARY):
        assert canary not in text


@pytest.mark.parametrize("mode", ["off", "evidence_harness"])
def test_actual_sdk_body_tools_unicode_reasoning_and_off_on_equivalence(monkeypatch, tmp_path, mode):
    observations = []
    for enabled in (False, True):
        with sdk_agent(monkeypatch, tmp_path / str(enabled), enabled=enabled, mode=mode,
                       replies=[completion(tools_message()), completion()]) as (agent, world, transport):
            result = agent.execute(COMMAND)
            assert result["success"]
            assert len(transport.requests) == 2
            assert transport.hook_reads == [(0, False), (0, False)]
            assert all(stream.reads == 2 for stream in transport.streams)
            body_bytes = [request.content for request in transport.requests]
            payload = json.loads(body_bytes[0])
            assert payload["temperature"] == 0.1 and payload["top_p"] == 0.77
            assert payload["seed"] == 17 and payload.get("max_tokens", payload.get("max_completion_tokens")) == 123
            assert payload["thinking"] == {"type": "enabled"}
            assert any(COMMAND in str(message.get("content")) for message in payload["messages"])
            assert any(tool["function"]["name"] == "get_drone_status" for tool in payload["tools"])
            assert AUTH_CANARY in transport.requests[0].headers["authorization"]
            assert URL_CANARY in str(transport.requests[0].url)
            next_messages = json.loads(body_bytes[1])["messages"]
            assert {message["tool_call_id"] for message in next_messages if message["role"] == "tool"} == {"call_甲", "call_乙"}
            assert any(message.get("reasoning_content") == "先分别读取，保留证据链。" for message in next_messages)
            observations.append({"requests": body_bytes, "public_calls": copy.deepcopy(world.calls),
                                 "success": result["success"], "output": result["output"],
                                 "steps": [(action.tool, action.tool_input, receipt)
                                           for action, receipt in result["intermediate_steps"]],
                                 "token_usage": result["token_usage"]})
            if not enabled:
                assert "model_io_archive" not in result
                assert not (tmp_path / str(enabled) / "model-io").exists()
                continue
            info, records = archive_for(result)
            assert info["complete"]
            assert request_bodies(records) == body_bytes
            assert response_bodies(records) == transport.response_bodies
            assert len(kind(records, "model_start")) == len(kind(records, "model_end")) == 2
            assert len(kind(records, "tool_start")) == len(kind(records, "tool_end")) == 2
            assert len({item["logical_call_id"] for item in kind(records, "http_request")}) == 2
            first_id = kind(records, "model_start")[0]["logical_call_id"]
            starts = kind(records, "tool_start")
            assert {item["raw_tool_call_id"] for item in starts} == {"call_甲", "call_乙"}
            assert all(item["source_model_id"] == first_id and item["source_relation"] == "unique" for item in starts)
            outputs = {item["output"]["tool_call_id"]: item["output"] for item in kind(records, "tool_end")}
            actual_tools = {message["tool_call_id"]: message for message in next_messages if message["role"] == "tool"}
            assert set(outputs) == set(actual_tools)
            assert all(output["content"] == actual_tools[ident]["content"] for ident, output in outputs.items())
            ai = kind(records, "model_end")[0]["output"]["result"][0]
            assert ai["additional_kwargs"]["reasoning_content"] == "先分别读取，保留证据链。"
            assert {call["id"] for call in ai["tool_calls"]} == set(outputs)
            assert_no_nonpolicy_data(records)
            assert Path(info["path"]).stat().st_mode & 0o777 == 0o600
            assert not agent.toolchain_json_recording
    assert observations[0] == observations[1]


@pytest.mark.parametrize("setting,explicit,expected", [
    (None, None, False), ("0", None, False), ("true", None, False),
    ("1", None, True), ("1", False, False), ("0", True, True),
])
def test_explicit_opt_in_and_environment_exact_one(monkeypatch, tmp_path, setting, explicit, expected):
    if setting is not None:
        monkeypatch.setenv("UAV_MODEL_IO_ARCHIVE", setting)
    with sdk_agent(monkeypatch, tmp_path, enabled=explicit, mode="off") as (agent, _, transport):
        result = agent.execute("读取说明 [PUBLIC]")
        assert result["success"] and len(transport.requests) == 1
        assert ("model_io_archive" in result) is expected
        assert bool(list(tmp_path.rglob("*.jsonl"))) is expected
        if expected:
            assert archive_for(result)[0]["complete"]


@pytest.mark.parametrize("failure", ["429", "timeout"])
def test_sync_sdk_retry_keeps_attempts_under_one_logical_call(monkeypatch, tmp_path, failure):
    first = (429, {"error": {"message": "synthetic retry", "type": "rate_limit_error"}})
    if failure == "timeout":
        first = lambda request: httpx.ReadTimeout("synthetic timeout " + AUTH_CANARY, request=request)
    with sdk_agent(monkeypatch, tmp_path, replies=[first, completion()]) as (agent, _, transport):
        result = agent.execute("重试读取")
        assert result["success"] and len(transport.requests) == 2
        info, records = archive_for(result)
        requests = kind(records, "http_request")
        assert len(requests) == 2 and requests[0]["logical_call_id"] == requests[1]["logical_call_id"]
        assert requests[0]["attempt_id"] != requests[1]["attempt_id"]
        assert request_bodies(records) == [request.content for request in transport.requests]
        assert request_bodies(records)[0] == request_bodies(records)[1]
        assert len(kind(records, "model_start")) == len(kind(records, "model_end")) == 1
        assert info["complete"] is (failure == "429")
        assert_no_nonpolicy_data(records)


def test_empty_terminal_response_is_retained_without_fabricating_output(monkeypatch, tmp_path):
    with sdk_agent(monkeypatch, tmp_path, replies=[completion({"role": "assistant", "content": ""}),
                                                  completion()]) as (agent, _, transport):
        agent.max_empty_response_retries = 1
        result = agent.execute(COMMAND)
        # Existing final-output extraction falls back to the user text for an
        # empty AI reply, so this real path does not enter its empty-string retry.
        # We preserve and document that pre-existing behavior, not fix it here.
        assert not result["success"] and result["empty_response_retries"] == 0
        info, records = archive_for(result)
        assert info["complete"] and len(transport.requests) == 1
        assert len({request["logical_call_id"] for request in kind(records, "http_request")}) == 1
        assert len({record["archive_id"] for record in records}) == 1
        assert kind(records, "model_end")[0]["output"]["result"][0]["content"] == ""


@pytest.mark.parametrize("failure", ["invalid_json", "http_400"])
def test_closed_error_trace_is_not_command_success(monkeypatch, tmp_path, failure):
    reply = b"{" if failure == "invalid_json" else (400, {"error": {"message": "synthetic error " + AUTH_CANARY,
                                                                  "type": "invalid_request_error"}})
    with sdk_agent(monkeypatch, tmp_path, replies=[reply], model_options={"max_retries": 0}) as (agent, _, transport):
        result = agent.execute(COMMAND)
        assert not result["success"] and len(transport.requests) == 1
        info, records = archive_for(result)
        assert request_bodies(records) == [transport.requests[0].content]
        assert response_bodies(records) == transport.response_bodies
        assert len(kind(records, "model_end")) == 1
        # Provider error BODY is intentionally retained; exception strings must
        # not become an additional accidental source of request/header context.
        assert kind(records, "archive_end")
        assert not any(record.get("success") is True for record in kind(records, "model_end"))


@pytest.mark.parametrize("failure", [None, "429", "timeout"])
def test_async_sdk_graph_and_retry_association(monkeypatch, tmp_path, failure):
    from model_io_archive import ModelIOArchive, ModelIOArchiveMiddleware, verify_archive
    replies = [completion(tools_message()), completion()]
    if failure == "429":
        replies.insert(0, (429, {"error": {"message": "synthetic", "type": "rate_limit_error"}}))
    elif failure == "timeout":
        replies.insert(0, lambda request: httpx.ReadTimeout("synthetic timeout", request=request))
    with sdk_agent(monkeypatch, tmp_path, replies=replies, mode="off") as (agent, world, transport):
        archive = ModelIOArchive("explicit-async-graph", tmp_path / "async")
        agent._model_io_archive = archive
        graph = create_agent(agent.llm, tools=agent.tools, system_prompt="冻结公开状态检查",
                             middleware=[ModelIOArchiveMiddleware(archive)])
        async def run():
            try:
                return await graph.ainvoke({"messages": [HumanMessage(content=COMMAND)]})
            finally:
                archive.close("command_end")
        result = asyncio.run(run())
        assert result["messages"][-1].content == "已核对 [TASK DONE]"
        records = rows(archive.path)
        checked = verify_archive(archive.path)
        assert checked["valid"] and checked["complete"] is (failure != "timeout")
        assert request_bodies(records) == [request.content for request in transport.requests]
        assert response_bodies(records) == transport.response_bodies
        assert all(count == 0 and consumed is False for count, consumed in transport.hook_reads)
        requests = kind(records, "http_request")
        assert len(requests) == (2 if failure is None else 3)
        assert len({item["logical_call_id"] for item in requests}) == 2
        if failure:
            assert requests[0]["logical_call_id"] == requests[1]["logical_call_id"]
            assert requests[0]["attempt_id"] != requests[1]["attempt_id"]
        assert {item["raw_tool_call_id"] for item in kind(records, "tool_start")} == {"call_甲", "call_乙"}
        assert sum(name == "get_drone_status" for name, _ in world.calls) == 2
        assert_no_nonpolicy_data(records)


def sse_body():
    result = []
    for delta, finish in (({"role": "assistant", "content": "你好"}, None),
                          ({"content": " [TASK DONE]"}, None), ({}, "stop")):
        result.append(b"data: " + raw_json({"id": "stream-response", "object": "chat.completion.chunk",
                     "created": 1, "model": "offline-model", "choices": [{"index": 0,
                      "delta": delta, "finish_reason": finish}]}) + b"\n\n")
    result.append(b"data: [DONE]\n\n")
    return result


@pytest.mark.parametrize("ending", ["eof", "done_marker", "broken"])
def test_actual_sdk_stream_tee_never_preconsumes_and_partial_is_incomplete(monkeypatch, tmp_path, ending):
    partial = ending == "broken"
    chunks = sse_body() if ending != "eof" else sse_body()[:-1]
    stream = SyncChunks(chunks[:1] if partial else chunks,
                        httpx.ReadError("synthetic interrupted stream") if partial else None)
    def reply(request):
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}, request=request)
    with sdk_agent(monkeypatch, tmp_path, replies=[reply],
                   model_options={"streaming": True, "max_retries": 0}) as (agent, _, transport):
        result = agent.execute(COMMAND)
        info, records = archive_for(result)
        assert result["success"] is (not partial)
        # SDK [DONE] ends semantic SSE parsing before requesting underlying EOF.
        # The passive observer must not drain it to upgrade capture eligibility.
        assert info["complete"] is (ending == "eof")
        assert transport.hook_reads == [(0, False)]
        assert json.loads(request_bodies(records)[0])["stream"] is True
        assert response_bodies(records) == [b"".join(chunks[:1] if partial else chunks)]
        assert kind(records, "http_response_end")[0]["complete"] is (ending == "eof")
        if partial:
            assert kind(records, "model_end")[0]["status"] == "error"
        else:
            assert "你好" in result["output"]


@pytest.mark.parametrize("buffered", [False, True])
def test_compressed_response_scope_is_truthful(monkeypatch, tmp_path, buffered):
    body = raw_json(completion())
    compressed = gzip.compress(body, mtime=0)
    def reply(request):
        if buffered:
            return httpx.Response(200, content=compressed, headers={"content-encoding": "gzip",
                "content-type": "application/json"}, request=request)
        return httpx.Response(200, stream=SyncChunks([compressed]), headers={"content-encoding": "gzip",
                "content-type": "application/json"}, request=request)
    with sdk_agent(monkeypatch, tmp_path, replies=[reply]) as (agent, _, _):
        result = agent.execute(COMMAND)
        info, records = archive_for(result)
        assert result["success"] and info["complete"]
        start = kind(records, "http_response_start")[0]
        assert start["content_encoding"] == "gzip"
        assert start["body_scope"] == ("buffered_httpx_content" if buffered else "httpx_stream_before_content_decoding")
        archived = response_bodies(records)[0]
        assert archived == (body if buffered else compressed)
        assert (archived if buffered else gzip.decompress(archived)) == body


def test_cancelled_after_model_reply_keeps_tool_rejection_and_no_extra_action(monkeypatch, tmp_path):
    cancelled = threading.Event()
    class Cancelled(BaseCallbackHandler):
        def _check_cancellation(self):
            if cancelled.is_set():
                raise RuntimeError("synthetic cancellation")
    def reply(request):
        cancelled.set()
        return completion(tools_message())
    with sdk_agent(monkeypatch, tmp_path, replies=[reply]) as (agent, world, transport):
        before = len(world.calls)
        result = agent.execute(COMMAND, callbacks=[Cancelled()])
        assert not result["success"] and len(transport.requests) == 1
        assert not any(name == "get_drone_status" for name, _ in world.calls[before:])
        _, records = archive_for(result)
        assert kind(records, "model_end")[0]["status"] == "returned"
        assert all(item["status"] == "error" for item in kind(records, "tool_end"))
        assert kind(records, "archive_end")[0]["reason"] == "exception"


def test_escaped_baseexception_closes_partial_archive_and_allows_new_episode(monkeypatch, tmp_path):
    def abort(request):
        raise KeyboardInterrupt("synthetic abort " + AUTH_CANARY)
    with sdk_agent(monkeypatch, tmp_path, replies=[abort, completion()]) as (agent, _, transport):
        with pytest.raises(KeyboardInterrupt):
            agent.execute("episode A")
        old = agent._model_io_archive
        assert old.closed and not old.info()["complete"]
        old_bytes = old.path.read_bytes()
        assert kind(rows(old.path), "archive_end")[0]["reason"] == "execute_aborted"
        result = agent.execute("episode B")
        assert result["success"] and archive_for(result)[0]["complete"]
        assert old.path.read_bytes() == old_bytes
        assert old.path != Path(result["model_io_archive"]["path"])


def test_late_a_response_and_request_cannot_touch_active_b(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def held_reply(request):
        entered.set()
        assert release.wait(5), "Offline response not released"
        return completion()
    with sdk_agent(monkeypatch, tmp_path, replies=[completion(), held_reply]) as (agent, _, transport):
        first = agent.execute("episode A")
        old_info, old_records = archive_for(first)
        old_path = Path(old_info["path"])
        frozen_a = old_path.read_bytes()
        old_request = transport.requests[0]
        running = ThreadCall(lambda: agent.execute("episode B"))
        try:
            assert entered.wait(5)
            active = agent._model_io_archive
            frozen_b = active.path.read_bytes()
            old_hashes = copy.deepcopy(agent._model_wire_requests)
            fake_late = httpx.Response(200, content=b"late-a", request=old_request)
            agent._audit_model_response(fake_late)
            agent._audit_model_request(old_request)  # No current model context on this thread.
            assert old_path.read_bytes() == frozen_a
            assert active.path.read_bytes() == frozen_b
            assert agent._model_wire_requests == old_hashes
        finally:
            release.set()
            running.join()
        assert running.error is None and running.value["success"]
        new_info, new_records = archive_for(running.value)
        assert old_info["command_id"] != new_info["command_id"]
        assert old_info["archive_id"] != new_info["archive_id"]
        assert len(kind(new_records, "http_request")) == len(kind(old_records, "http_request")) == 1
        assert b"late-a" not in b"".join(response_bodies(new_records))


def test_disk_unavailable_is_visible_and_does_not_change_execution(monkeypatch, tmp_path):
    destination = tmp_path / "model-io"
    destination.write_text("file blocks archive directory")
    with sdk_agent(monkeypatch, tmp_path) as (agent, _, transport):
        result = agent.execute(COMMAND)
        assert result["success"] and len(transport.requests) == 1
        assert result["model_io_archive"]["complete"] is False
        assert result["model_io_archive"]["state"] == "incomplete"
        assert destination.read_text() == "file blocks archive directory"


def test_scripted_model_without_wire_is_explicitly_partial(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage
    from test_task_progress_integration import ScriptedModel
    with sdk_agent(monkeypatch, tmp_path, mode="off") as (agent, _, transport):
        agent.llm = ScriptedModel(replies=[AIMessage(content="本地回复 [TASK DONE]")])
        result = agent.execute(COMMAND)
        info, records = archive_for(result)
        assert result["success"] and not info["complete"]
        assert not transport.requests and not kind(records, "http_request")
        assert "wire_missing" in info["reasons"]
        assert kind(records, "model_end")[0]["output"]["result"][0]["content"] == "本地回复 [TASK DONE]"


@pytest.mark.parametrize("damage", ["body", "hash", "sequence", "missing_end", "half_line"])
def test_verifier_rejects_damaged_independent_copy(monkeypatch, tmp_path, damage):
    from model_io_archive import verify_archive
    with sdk_agent(monkeypatch, tmp_path) as (agent, _, _):
        result = agent.execute(COMMAND)
        info, records = archive_for(result)
        original = Path(info["path"]).read_bytes()
        copied = copy.deepcopy(records)
        if damage in {"body", "hash"}:
            body = kind(copied, "http_request")[0]["body"]
            body["base64" if damage == "body" else "sha256"] = (base64.b64encode(b"changed").decode()
                                                                        if damage == "body" else "0" * 64)
        elif damage == "sequence":
            copied[1]["sequence"] += 1
        elif damage == "missing_end":
            copied = copied[:-1]
        text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in copied)
        if damage == "half_line":
            text = text[:-12]
        target = tmp_path / (damage + ".jsonl")
        target.write_text(text)
        checked = verify_archive(target)
        assert not checked["valid"] and not checked["complete"]
        assert Path(info["path"]).read_bytes() == original


def test_nonvisible_ai_headers_and_tool_artifact_never_enter_archive(monkeypatch, tmp_path):
    @tool(response_format="content_and_artifact")
    def get_drone_status(input_json: str):
        """Read a public drone status; test artifact is intentionally non-visible."""
        value = json.loads(input_json)
        return json.dumps({"id": value["drone_id"], "status": "idle"}), {"private_checker": PRIVATE_CANARY}
    with sdk_agent(monkeypatch, tmp_path, mode="off", replies=[completion(tools_message()), completion()],
                   model_options={"include_response_headers": True}) as (agent, _, transport):
        agent._create_tools = lambda board: [get_drone_status]
        result = agent.execute(COMMAND)
        assert result["success"]
        _, records = archive_for(result)
        assert_no_nonpolicy_data(records)
        for output in kind(records, "tool_end"):
            assert "artifact" not in output["output"]
        for output in kind(records, "model_end"):
            for message in output["output"]["result"]:
                assert "headers" not in message.get("response_metadata", {})
        assert request_bodies(records) == [request.content for request in transport.requests]


def interrupt_archive_write(monkeypatch, exception, event="model_start"):
    """Fault the actual archive file boundary once, without faulting SDK/tools."""
    import model_io_archive
    original = model_io_archive.os.fdopen
    hit = []
    class Writer:
        def __init__(self, file):
            self.file = file
        def write(self, value):
            if not hit and ('"kind":"' + event + '"') in value:
                hit.append(True)
                raise exception
            return self.file.write(value)
        def __getattr__(self, name):
            return getattr(self.file, name)
    monkeypatch.setattr(model_io_archive.os, "fdopen", lambda *args, **kwargs: Writer(original(*args, **kwargs)))
    return hit


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_archive_observer_baseexception_propagates_and_next_episode_works(monkeypatch, tmp_path, exception_type):
    error = exception_type("synthetic observer interruption " + AUTH_CANARY)
    hit = interrupt_archive_write(monkeypatch, error)
    with sdk_agent(monkeypatch, tmp_path, mode="off") as (agent, _, transport):
        if exception_type is KeyboardInterrupt:
            with pytest.raises(exception_type) as raised:
                agent.execute("interrupted A")
            assert raised.value is error
        else:
            # Pinned LangGraph intentionally converts a node-raised
            # CancelledError into NodeCancelledError. execute preserves its
            # ordinary graph-error result contract; this is not success.
            failed = agent.execute("interrupted A")
            assert not failed["success"] and "CancelledError" in failed["output"]
        assert hit
        old = agent._model_io_archive
        assert old.closed and not old.info()["complete"]
        frozen = old.path.read_bytes()
        assert not transport.requests
        result = agent.execute("normal B")
        assert result["success"] and archive_for(result)[0]["complete"]
        assert old.path.read_bytes() == frozen


def test_archive_observer_oserror_fails_open_and_reports_incomplete(monkeypatch, tmp_path):
    hit = interrupt_archive_write(monkeypatch, OSError("synthetic write failure " + AUTH_CANARY))
    with sdk_agent(monkeypatch, tmp_path, mode="off") as (agent, _, transport):
        result = agent.execute(COMMAND)
        assert hit and result["success"] and len(transport.requests) == 1
        info = result["model_io_archive"]
        assert not info["complete"] and info["state"] == "incomplete"
        assert "OSError" in info.get("error_types", [])
        assert AUTH_CANARY not in Path(info["path"]).read_text()


def test_async_archive_observer_cancellation_propagates(monkeypatch, tmp_path):
    from model_io_archive import ModelIOArchive, ModelIOArchiveMiddleware
    error = asyncio.CancelledError("synthetic cancellation")
    hit = interrupt_archive_write(monkeypatch, error)
    with sdk_agent(monkeypatch, tmp_path, mode="off") as (agent, _, transport):
        archive = ModelIOArchive("explicit-async-cancel", tmp_path / "async")
        agent._model_io_archive = archive
        graph = create_agent(agent.llm, tools=agent.tools, middleware=[ModelIOArchiveMiddleware(archive)])
        async def run():
            try:
                return await graph.ainvoke({"messages": [HumanMessage(content=COMMAND)]})
            finally:
                archive.close("execute_aborted")
        from langgraph.errors import NodeCancelledError
        with pytest.raises(NodeCancelledError) as raised:
            asyncio.run(run())
        assert raised.value.__cause__ is error
        assert hit and not transport.requests
        assert archive.closed and not archive.info()["complete"]


def test_graph_step_callback_path_keeps_complete_model_and_tool_chain(monkeypatch, tmp_path):
    updates = []
    with sdk_agent(monkeypatch, tmp_path, replies=[completion(tools_message()), completion()]) as (agent, _, transport):
        result = agent.execute(COMMAND, step_callback=lambda steps: updates.append(copy.deepcopy(steps)))
        info, records = archive_for(result)
        assert result["success"] and info["complete"] and updates
        assert len(kind(records, "model_start")) == len(kind(records, "model_end")) == 2
        assert len(kind(records, "tool_start")) == len(kind(records, "tool_end")) == 2
        assert request_bodies(records) == [request.content for request in transport.requests]
        assert json.loads(request_bodies(records)[0]).get("stream", False) is False


def test_sse_callback_cancel_leaves_unconsumed_stream_partial(monkeypatch, tmp_path):
    class StopFirstToken(BaseCallbackHandler):
        raise_error = True
        def on_llm_new_token(self, token, **kwargs):
            raise RuntimeError("synthetic token cancellation")
    chunks = sse_body()
    stream = SyncChunks(chunks)
    def reply(request):
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}, request=request)
    with sdk_agent(monkeypatch, tmp_path, replies=[reply], model_options={"streaming": True}) as (agent, _, transport):
        result = agent.execute(COMMAND, callbacks=[StopFirstToken()])
        info, records = archive_for(result)
        assert not result["success"] and not info["complete"]
        assert stream.reads < len(chunks)
        assert transport.hook_reads == [(0, False)]
        assert kind(records, "http_response_end")[0]["complete"] is False


def test_duplicate_raw_tool_ids_are_ambiguous_not_guessed(monkeypatch, tmp_path):
    message = tools_message()
    for call in message["tool_calls"]:
        call["id"] = "duplicate-call-id"
    with sdk_agent(monkeypatch, tmp_path, mode="off", replies=[completion(message), completion()]) as (agent, _, _):
        result = agent.execute(COMMAND)
        info, records = archive_for(result)
        assert not info["complete"]
        starts = kind(records, "tool_start")
        assert starts and all(item["source_relation"] == "ambiguous" for item in starts)
        assert all(item["source_model_id"] is None for item in starts)
        assert "tool_source_ambiguous" in info["reasons"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_middleware_itself_propagates_original_cancelled_error(monkeypatch, tmp_path, asynchronous):
    from model_io_archive import ModelIOArchive, ModelIOArchiveMiddleware
    error = asyncio.CancelledError("synthetic observer cancellation")
    hit = interrupt_archive_write(monkeypatch, error)
    archive = ModelIOArchive("middleware-boundary", tmp_path)
    middleware = ModelIOArchiveMiddleware(archive)
    request = SimpleNamespace(system_message=None, messages=[])
    handler_called = []
    def handler(request):
        handler_called.append(True)
    async def ahandler(request):
        handler_called.append(True)
    try:
        with pytest.raises(asyncio.CancelledError) as raised:
            if asynchronous:
                asyncio.run(middleware.awrap_model_call(request, ahandler))
            else:
                middleware.wrap_model_call(request, handler)
        assert raised.value is error and hit and not handler_called
    finally:
        archive.close("execute_aborted")
    assert not archive.info()["complete"]


@pytest.mark.parametrize("exception_type", [RuntimeError, KeyboardInterrupt])
def test_archive_end_write_failure_never_claims_complete(monkeypatch, tmp_path, exception_type):
    from model_io_archive import verify_archive
    error = exception_type("synthetic final write failure")
    hit = interrupt_archive_write(monkeypatch, error, event="archive_end")
    with sdk_agent(monkeypatch, tmp_path, replies=[completion(), completion()]) as (agent, _, transport):
        if exception_type is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt) as raised:
                agent.execute("A complete execution interrupted during archive close")
            assert raised.value is error
            info = agent._model_io_archive.info()
        else:
            result = agent.execute("A complete execution with archive finalization error")
            assert result["success"]
            info = result["model_io_archive"]
        assert hit and len(transport.requests) == 1
        assert info["state"] == "incomplete" and not info["complete"]
        assert not verify_archive(info["path"])["complete"]
        assert request_bodies(rows(info["path"])) == [transport.requests[0].content]
        result_b = agent.execute("B subsequent episode")
        assert result_b["success"] and archive_for(result_b)[0]["complete"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_dual_protocol_response_stream_preserves_transport_protocol(monkeypatch, tmp_path, asynchronous):
    from model_io_archive import ModelIOArchive, ModelIOArchiveMiddleware, verify_archive
    class Dual(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __init__(self):
            self.reads = 0
        def __iter__(self):
            self.reads += 1
            yield raw_json(completion())
        async def __aiter__(self):
            self.reads += 1
            yield raw_json(completion())
        def close(self):
            pass
        async def aclose(self):
            pass
    stream = Dual()
    def reply(request):
        return httpx.Response(200, stream=stream, headers={"content-type": "application/json"}, request=request)
    with sdk_agent(monkeypatch, tmp_path, mode="off", replies=[reply]) as (agent, _, transport):
        if not asynchronous:
            result = agent.execute(COMMAND)
            assert result["success"]
            info, records = archive_for(result)
        else:
            archive = ModelIOArchive("async-dual", tmp_path / "async")
            agent._model_io_archive = archive
            graph = create_agent(agent.llm, middleware=[ModelIOArchiveMiddleware(archive)])
            asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=COMMAND)]}))
            info = archive.close()
            records = rows(info["path"])
            assert verify_archive(info["path"])["valid"]
        assert info["complete"] and stream.reads == 1
        assert transport.hook_reads == [(0, False)]
        assert response_bodies(records) == [raw_json(completion())]


@pytest.mark.parametrize("boundary", ["same_episode", "open_a", "closed_a"])
def test_reused_http_request_is_quarantined_not_rebound(monkeypatch, tmp_path, boundary):
    from model_io_archive import ModelIOArchive, ModelIOArchiveMiddleware, request_is_ambiguous
    with sdk_agent(monkeypatch, tmp_path, mode="off") as (agent, _, transport):
        first = ModelIOArchive("manual-owner-A", tmp_path / "manual")
        second = first if boundary == "same_episode" else ModelIOArchive("manual-owner-B", tmp_path / "manual")
        request = httpx.Request("POST", "https://offline.invalid/v1/chat/completions",
                                content=raw_json({"messages": [{"role": "user", "content": "owner A"}]}))
        model_request = SimpleNamespace(system_message=None, messages=[])
        def bind(archive):
            agent._model_io_archive = archive
            def handler(_):
                agent._audit_model_request(request)
                return ModelResponse(result=[AIMessage(content="synthetic hook-boundary only")])
            return ModelIOArchiveMiddleware(archive).wrap_model_call(model_request, handler)
        bind(first)
        if boundary == "closed_a":
            first.close()
        before_first = first.path.read_bytes()
        first_info = first.info()
        bind(second)
        assert request_is_ambiguous(request)
        # The same mutable request cannot identify the old or new reply anymore.
        # Neither a delayed A nor B receipt may be invented as another owner.
        agent._audit_model_response(httpx.Response(200, content=b"late-owner-A", request=request))
        agent._audit_model_response(httpx.Response(200, content=b"owner-B", request=request))
        first.close(); second.close()
        for archive in (first, second):
            assert not archive.info()["complete"]
            assert response_bodies(rows(archive.path)) == []
        if boundary == "closed_a":
            assert first.path.read_bytes() == before_first and first.info() == first_info
        assert not transport.requests  # Explicit hook misuse probe, not an SDK rollout.


def test_direct_worker_cannot_inherit_parent_archive_context(monkeypatch, tmp_path):
    from model_io_archive import ModelIOArchive, ModelIOArchiveMiddleware
    with sdk_agent(monkeypatch, tmp_path, mode="off") as (agent, _, transport):
        archive = ModelIOArchive("explicit-unsupported-worker", tmp_path / "worker")
        archive.mark_incomplete("observation_worker_not_instrumented")
        agent._model_io_archive = archive
        agent._in_observation_worker = True
        request = httpx.Request("POST", "https://offline.invalid/v1/chat/completions",
                                content=raw_json({"model": "offline", "messages": [{"role": "user", "content": "worker"}]}))
        def handler(_):
            agent._audit_model_request(request)
            return ModelResponse(result=[AIMessage(content="worker hook boundary")])
        ModelIOArchiveMiddleware(archive).wrap_model_call(SimpleNamespace(messages=[], system_message=None), handler)
        info = archive.close()
        assert not info["complete"] and "observation_worker_not_instrumented" in info["reasons"]
        assert not kind(rows(archive.path), "http_request")
        assert len(agent._model_wire_requests) == 1
        assert agent._model_wire_requests[0]["agent_role"] == "observation_worker"
        assert not transport.requests
