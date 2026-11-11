"""Local enforcing-transport tests; no provider or simulator calls."""
import asyncio
import base64
import copy
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import harness_request_limits as limits


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture(params=["httpx", "httpx2"])
def httpx(request):
    return pytest.importorskip(request.param)


def response_body(usage=None):
    return {"model": "local", "choices": [{"message": {"role": "assistant", "content": "工具🛰",
            "tool_calls": [{"id": "local-call", "type": "function", "function": {"name": "hover", "arguments": "{}"}}]}}],
            "usage": usage if usage is not None else {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}


def request(httpx, **changes):
    body = {"model": "local", "stream": False, "max_tokens": 64}
    body.update(changes)
    return httpx.Request("POST", "https://local.invalid/v1/chat/completions", json=body)


def setup(httpx, handler=None, *, clock=time.monotonic, deadline=None, cancel=lambda: False, **changes):
    config = dict(max_http_attempts=3, max_total_tokens=100, max_output_tokens=64, per_request_timeout_seconds=1.0)
    config.update(changes)
    controller = limits.RequestLimitController(limits.RequestLimits(**config),
        task_deadline=clock() + 10 if deadline is None else deadline, clock=clock, cancel_check=cancel)
    class Transport(httpx.BaseTransport):
        def __init__(self):
            self.calls, self.close_count = [], 0
        def handle_request(self, req):
            self.calls.append((req.content, copy.deepcopy(req.extensions.get("timeout"))))
            return handler(req) if handler else httpx.Response(200, json=response_body())
        def close(self):
            self.close_count += 1
    transport = Transport()
    guarded = limits.make_guarded_transport(httpx, transport, controller)
    return controller, transport, guarded


class Call:
    def __init__(self, function):
        self.value = self.error = None
        self.done = threading.Event()
        def run():
            try:
                self.value = function()
            except BaseException as exc:
                self.error = exc
            finally:
                self.done.set()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
    def finish(self):
        assert self.done.wait(2), "Caller failed to return within local test bound"
        self.thread.join(1)
        return self


def test_success_exact_body_and_usage(httpx):
    ctrl, raw, guarded = setup(httpx)
    req = request(httpx)
    original = req.content
    reply = guarded.handle_request(req)
    assert reply.json() == response_body() and req.content == original
    assert len(raw.calls) == 1 and ctrl.drain(1)
    result = ctrl.snapshot()
    assert result["known_total_tokens"] == 10 and result["unknown_usage_attempts"] == 0
    assert result["admitted_http_attempts"] == result["delegated_http_attempts"] == 1
    assert result["attempts"][0]["request_body_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["attempts"][0]["delivered_to_sdk"] and not result["threads_still_alive"]
    ctrl.close(); ctrl.close()
    assert raw.close_count == 1


@pytest.mark.parametrize("values", [{}, {"max_tokens": None}, {"max_tokens": True}, {"max_tokens": 0},
    {"max_tokens": 65}, {"max_tokens": 1.5}, {"max_tokens": 32, "max_completion_tokens": 31}])
def test_output_validation_before_dispatch(httpx, values):
    ctrl, raw, guarded = setup(httpx)
    req = httpx.Request("POST", "https://local.invalid", json={"stream": False, **values})
    with pytest.raises(limits.RequestLimitError, match="invalid_output_limit"):
        guarded.handle_request(req)
    assert not raw.calls and ctrl.snapshot()["admitted_http_attempts"] == 0


def test_completion_token_alias_and_remaining_timeout(httpx):
    ctrl, raw, guarded = setup(httpx, clock=lambda: 4.0, deadline=4.5)
    req = httpx.Request("POST", "https://local.invalid", json={"max_completion_tokens": 64})
    req.extensions["timeout"] = {"read": .2, "connect": None, "write": 5, "pool": 10}
    guarded.handle_request(req)
    assert raw.calls[0][1] == {"read": .2, "connect": .5, "write": .5, "pool": .5}
    assert ctrl.snapshot()["attempts"][0]["admitted_timeout_seconds"] == .5


@pytest.mark.parametrize("value", [True, None, "false", 1])
def test_stream_rejected_before_io(httpx, value):
    ctrl, raw, guarded = setup(httpx)
    with pytest.raises(limits.RequestLimitError, match="streaming_unsupported"):
        guarded.handle_request(request(httpx, stream=value))
    assert not raw.calls and ctrl.snapshot()["delegated_http_attempts"] == 0


def test_response_sse_rejected_before_read(httpx):
    class Unread(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("SSE body must not be consumed")
    ctrl, raw, guarded = setup(httpx, lambda req: httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"}, stream=Unread()))
    with pytest.raises(limits.RequestLimitError, match="streaming_unsupported"):
        guarded.handle_request(request(httpx))
    assert len(raw.calls) == 1 and ctrl.snapshot()["unknown_usage_attempts"] == 1


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'{"usage":null}',
    b'{"usage":{"prompt_tokens":7}}',
    b'{"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":11}}',
    b'{"usage":{"prompt_tokens":true,"completion_tokens":3,"total_tokens":4}}',
    b'{"usage":{"prompt_tokens":-1,"completion_tokens":3,"total_tokens":2}}',
    b'{"usage":{"prompt_tokens":"7","completion_tokens":3,"total_tokens":10}}'])
def test_unknown_usage_never_delivered_or_retried(httpx, body):
    ctrl, raw, guarded = setup(httpx, lambda req: httpx.Response(200, content=body))
    hooks = []
    with httpx.Client(transport=guarded, event_hooks={"response": [lambda response: hooks.append(response)]}) as client:
        with pytest.raises(limits.UsageUnavailable):
            client.post("https://local.invalid", json={"max_tokens": 64, "stream": False})
        with pytest.raises(limits.RequestLimitError, match="unknown_usage"):
            client.post("https://local.invalid", json={"max_tokens": 64, "stream": False})
    result = ctrl.snapshot()
    assert len(raw.calls) == 1 and hooks == []
    assert result["known_total_tokens"] == 0 and result["unknown_usage_attempts"] == 1
    assert result["attempts"][0]["usage"] is None and not result["attempts"][0]["delivered_to_sdk"]
    assert base64.b64decode(result["attempts"][0]["diagnostic_response"]["body"]["base64"]) == body


@pytest.mark.parametrize("cap", [10, 5])
def test_known_token_stop_is_post_response_not_cost_precount(httpx, cap):
    ctrl, raw, guarded = setup(httpx, max_total_tokens=cap)
    assert guarded.handle_request(request(httpx)).status_code == 200
    with pytest.raises(limits.RequestLimitError, match="token_limit"):
        guarded.handle_request(request(httpx))
    result = ctrl.snapshot()
    assert result["known_total_tokens"] == 10 >= cap and len(raw.calls) == 1
    assert result["attempts"][0]["delivered_to_sdk"]


def test_actual_attempt_limit_distinct_from_pretransport_audit_hooks(httpx):
    ctrl, raw, guarded = setup(httpx, max_http_attempts=1)
    hooks = []
    with httpx.Client(transport=guarded, event_hooks={"request": [lambda req: hooks.append(req)]}) as client:
        client.post("https://local.invalid", json={"max_tokens": 64})
        with pytest.raises(limits.RequestLimitError, match="http_attempt_limit"):
            client.post("https://local.invalid", json={"max_tokens": 64})
    assert len(hooks) == 2 and len(raw.calls) == ctrl.snapshot()["delegated_http_attempts"] == 1


def test_http_error_preserved_with_unknown_usage_and_future_stop(httpx):
    body = b'{"error":{"message":"local failure"}}'
    ctrl, raw, guarded = setup(httpx, lambda req: httpx.Response(500, content=body))
    reply = guarded.handle_request(request(httpx))
    assert reply.status_code == 500 and reply.content == body
    with pytest.raises(limits.RequestLimitError, match="http_error"):
        guarded.handle_request(request(httpx))
    assert ctrl.snapshot()["unknown_usage_attempts"] == 1 and len(raw.calls) == 1


@pytest.mark.parametrize("kind", ["ReadTimeout", "KeyboardInterrupt"])
def test_underlying_exception_identity_preserved_no_retry(httpx, kind):
    failure = httpx.ReadTimeout("local read failure") if kind == "ReadTimeout" else KeyboardInterrupt()
    def fail(req):
        raise failure
    ctrl, raw, guarded = setup(httpx, fail)
    with pytest.raises(type(failure)) as caught:
        guarded.handle_request(request(httpx))
    assert caught.value is failure and ctrl.snapshot()["unknown_usage_attempts"] == 1
    assert ctrl.drain(1) and len(raw.calls) == 1
    with pytest.raises(limits.RequestLimitError, match="transport_error"):
        guarded.handle_request(request(httpx))


@pytest.mark.parametrize("boundary", ["task_deadline", "cancelled", "closed"])
def test_initial_guard_has_zero_io(httpx, boundary):
    ctrl, raw, guarded = setup(httpx, deadline=0 if boundary == "task_deadline" else None,
                               cancel=lambda: boundary == "cancelled")
    if boundary == "closed":
        ctrl.close()
    with pytest.raises(limits.RequestLimitError, match=boundary):
        guarded.handle_request(request(httpx))
    assert not raw.calls and ctrl.snapshot()["admitted_http_attempts"] == 0


def test_blocked_read_timeout_late_result_private_only_until_explicit_drain(httpx):
    entered, release = threading.Event(), threading.Event()
    body = json.dumps(response_body(), ensure_ascii=False).encode()
    class BlockedBody(httpx.SyncByteStream):
        def __iter__(self):
            entered.set()
            assert release.wait(2)
            yield body
    ctrl, raw, guarded = setup(httpx, lambda req: httpx.Response(200, stream=BlockedBody()), per_request_timeout_seconds=.03)
    hooks = []
    client = httpx.Client(transport=guarded, event_hooks={"response": [lambda response: hooks.append(response)]})
    started = time.monotonic()
    try:
        with pytest.raises(limits.RequestLimitError, match="request_timeout"):
            client.post("https://local.invalid", json={"max_tokens": 64})
        assert entered.is_set() and time.monotonic() - started < .7
        before = ctrl.snapshot()
        assert before["needs_process_termination"] and before["unknown_usage_attempts"] == 1
        assert not ctrl.drain(0) and hooks == []
        ctrl.close()
        assert raw.close_count == 0  # Underlying transport is still in use.
        release.set()
        assert ctrl._jobs[0].done.wait(1)
        assert ctrl.snapshot()["attempts"] == before["attempts"]  # No background settlement.
        assert ctrl.drain(1)
        after = ctrl.snapshot()
        assert after["known_total_tokens"] == 10 and after["unknown_usage_attempts"] == 0
        assert after["stop_reason"] == "request_timeout" and not after["reusable"]
        assert not after["needs_process_termination"] and hooks == []
        late = after["attempts"][0]["late_response"]["body"]
        assert base64.b64decode(late["base64"]) == body and late["sha256"] == hashlib.sha256(body).hexdigest()
        assert ctrl.drain(0) and ctrl.snapshot()["known_total_tokens"] == 10
        with pytest.raises(limits.RequestLimitError, match="request_timeout"):
            guarded.handle_request(request(httpx))
    finally:
        release.set(); ctrl.drain(1); client.close()
    assert raw.close_count == 1


@pytest.mark.parametrize("boundary", ["cancelled", "task_deadline", "closed"])
def test_boundary_while_blocked_never_delivers_response(httpx, boundary):
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    now = [0.0]
    def block(req):
        entered.set(); assert release.wait(2)
        return httpx.Response(200, json=response_body())
    ctrl, raw, guarded = setup(httpx, block, clock=lambda: now[0], deadline=5, cancel=cancelled.is_set)
    call = Call(lambda: guarded.handle_request(request(httpx)))
    try:
        assert entered.wait(1)
        if boundary == "cancelled": cancelled.set()
        elif boundary == "task_deadline": now[0] = 5
        else: ctrl.close()
        call.finish()
        assert isinstance(call.error, limits.RequestLimitError) and call.error.reason == boundary
        assert call.value is None and ctrl.snapshot()["needs_process_termination"]
    finally:
        release.set(); ctrl.drain(1)
    assert not ctrl.snapshot()["attempts"][0]["delivered_to_sdk"]


def test_concurrent_request_rejected_without_poisoning_first(httpx):
    entered, release = threading.Event(), threading.Event()
    def block(req):
        entered.set(); assert release.wait(2)
        return httpx.Response(200, json=response_body())
    ctrl, raw, guarded = setup(httpx, block)
    call = Call(lambda: guarded.handle_request(request(httpx)))
    try:
        assert entered.wait(1)
        with pytest.raises(limits.RequestLimitError, match="concurrent_request"):
            guarded.handle_request(request(httpx))
        with pytest.raises(limits.RequestLimitError, match="concurrent_request"):
            ctrl.drain(0)
    finally:
        release.set(); call.finish(); ctrl.drain(1)
    assert call.error is None and call.value.status_code == 200
    assert len(raw.calls) == 1 and ctrl.snapshot()["stop_reason"] is None


def test_delayed_thread_cannot_start_http_after_caller_timeout(httpx, monkeypatch):
    real_thread = threading.Thread
    release, thread_started = threading.Event(), threading.Event()
    def delayed_thread(*, target, **kwargs):
        def delayed():
            thread_started.set(); assert release.wait(2); target()
        return real_thread(target=delayed, **kwargs)
    monkeypatch.setattr(limits.threading, "Thread", delayed_thread)
    ctrl, raw, guarded = setup(httpx, per_request_timeout_seconds=.03)
    try:
        with pytest.raises(limits.RequestLimitError, match="request_timeout"):
            guarded.handle_request(request(httpx))
        assert thread_started.is_set() and not raw.calls
    finally:
        release.set(); assert ctrl.drain(1)
    result = ctrl.snapshot()
    assert not raw.calls and result["delegated_http_attempts"] == 0
    assert result["attempts"][0]["usage_status"] == "not_dispatched"
    assert result["stop_reason"] == "request_timeout"


def test_async_explicitly_rejected_and_no_io(httpx):
    ctrl, raw, guarded = setup(httpx)
    with pytest.raises(limits.UnsupportedAsyncTransport):
        asyncio.run(guarded.handle_async_request(request(httpx)))
    with pytest.raises(limits.UnsupportedAsyncTransport):
        asyncio.run(guarded.__aenter__())
    assert not raw.calls and ctrl.snapshot()["admitted_http_attempts"] == 0


@pytest.mark.parametrize("field,value", [("max_http_attempts", 0), ("max_total_tokens", True),
    ("max_output_tokens", 1.5), ("per_request_timeout_seconds", float("inf")),
    ("per_request_timeout_seconds", float("nan")), ("per_request_timeout_seconds", 0)])
def test_invalid_limits(field, value):
    config = dict(max_http_attempts=1, max_total_tokens=100, max_output_tokens=64, per_request_timeout_seconds=1)
    config[field] = value
    with pytest.raises(ValueError):
        limits.RequestLimits(**config)
