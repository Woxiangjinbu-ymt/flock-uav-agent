"""Frozen-pilot gates and actual SDK wire checks using MockTransport only."""
import asyncio
import copy
import json
from pathlib import Path
import socket
import sys

import openai
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
sys.path.insert(0, str(ROOT / "scripts"))
import deepseek_behavior_pilot as pilot
from harness_deepseek_adapter import httpx

KEY = "synthetic-pilot-auth-canary-not-a-real-key"
TOOLS = [{"type": "function", "function": {"name": "list_drones", "description": "Public drone list",
    "parameters": {"type": "object", "properties": {}, "required": []}}}]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pilot tests forbid provider/network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def completion(index, tool=False):
    message = {"role": "assistant", "content": "Public final response", "reasoning_content": "synthetic-reason-" + str(index)}
    if tool:
        message["content"] = ""
        message["tool_calls"] = [{"id": "pilot-tool-0", "type": "function", "function": {"name": "list_drones", "arguments": "{}"}}]
    return {"id": "mock-pilot-" + str(index), "object": "chat.completion", "created": 1,
        "model": "deepseek-flash", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}


def test_runner_factory_emits_frozen_deepseek_wire_and_preserves_tool_reasoning_history():
    payloads = []
    def respond(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer " + KEY
        payloads.append(json.loads(request.content))
        assert len(payloads) <= 2, "Unexpected retry"
        return httpx.Response(200, json=completion(len(payloads) - 1, tool=len(payloads) == 1), request=request)
    sync = openai.DefaultHttpxClient(transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False)
    asynchronous = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False)
    # These are the kwargs supplied by run_worker -> UAVControlAgent._create_llm.
    model = pilot.model_factory_for(KEY)(http_client=sync, http_async_client=asynchronous,
        model="placeholder", api_key="synthetic-worker-placeholder", base_url="https://offline.invalid/v1",
        temperature=0.1, max_tokens=8192, max_retries=0, streaming=False)
    try:
        history = [HumanMessage("Read public drone status.")]
        first = model.invoke(history, tools=TOOLS)
        assert first.additional_kwargs["reasoning_content"] == "synthetic-reason-0"
        assert first.tool_calls[0]["id"] == "pilot-tool-0"
        history += [first, ToolMessage('[{"id":"d1","name":"Drone 1"}]', tool_call_id="pilot-tool-0")]
        second = model.invoke(history, tools=TOOLS)
        assert second.additional_kwargs["reasoning_content"] == "synthetic-reason-1"
    finally:
        sync.close()
        asyncio.run(asynchronous.aclose())
    assert len(payloads) == 2
    for payload in payloads:
        assert payload["model"] == "deepseek-flash"
        assert payload["max_tokens"] == 8192 and "max_completion_tokens" not in payload
        assert payload["thinking"] == {"type": "enabled"} and payload["reasoning_effort"] == "high"
        assert payload["stream"] is False and "temperature" not in payload
        assert KEY not in json.dumps(payload)
    assistant = next(m for m in payloads[1]["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "synthetic-reason-0"
    assert assistant["tool_calls"][0]["id"] == "pilot-tool-0"
    assert model.max_retries == model.root_client.max_retries == 0


def test_factory_does_not_retry_failed_provider_response():
    count = 0
    def fail(request):
        nonlocal count
        count += 1
        return httpx.Response(500, json={"error": {"message": "controlled failure"}}, request=request)
    sync = openai.DefaultHttpxClient(transport=httpx.MockTransport(fail), trust_env=False, follow_redirects=False)
    asynchronous = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(fail), trust_env=False, follow_redirects=False)
    model = pilot.model_factory_for(KEY)(http_client=sync, http_async_client=asynchronous, max_tokens=8192)
    try:
        with pytest.raises(openai.APIStatusError):
            model.invoke([HumanMessage("read state")], tools=TOOLS)
    finally:
        sync.close()
        asyncio.run(asynchronous.aclose())
    assert count == 1


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value) + "\n").encode())


def seal_manifest(run, manifest):
    put(run / "manifest.json", manifest)
    put(run / "manifest.sha256", (pilot.sha((run / "manifest.json").read_bytes()) + "\n").encode())


@pytest.fixture
def frozen(tmp_path):
    run = tmp_path / "run"
    put(run / "source/inert.py", b"# unit-test inert source\n")
    put(run / "inputs/inert.pkl", b"not a pickle; never deserialized")
    put(run / "PROTOCOL_CN.md", b"Synthetic authorization gate protocol\n")
    manifest = {"schema_version": 1, "scope": "deepseek_behavior_pilot_v1", "workspace": str(tmp_path),
        "study": str(run / "inputs"), "model": pilot.PILOT["model"], "online_pilot": copy.deepcopy(pilot.PILOT),
        "local_smoke": True, "trials": pilot.trials(), "authorization": {
            "source": "user_explicit_small_scale_deepseek_api_authorization_20261102", "episodes": 0,
            "max_http_attempts": 0, "batch_cost_limit_usd": "0", "old_14_slot_and_360_queues_resumed": False},
        "sources": {"inert.py": pilot.sha((run / "source/inert.py").read_bytes())},
        "inputs": {"inert.pkl": pilot.sha((run / "inputs/inert.pkl").read_bytes())},
        "protocol_sha256": pilot.sha((run / "PROTOCOL_CN.md").read_bytes())}
    seal_manifest(run, manifest)
    return run, manifest


def test_frozen_local_smoke_has_zero_online_authorization_and_no_credential_lookup(frozen):
    run, manifest = frozen
    assert pilot.load_manifest(run) == manifest
    key = pilot.credential(manifest)
    assert key == "synthetic-local-deepseek-candidate-not-a-real-key"
    assert not (Path(manifest["workspace"]) / "agent4drone/llm_settings.json").exists()


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(model="other-model"),
    lambda m: m.update(local_smoke=1),
    lambda m: m["online_pilot"].update(reasoning_effort="max"),
    lambda m: m["online_pilot"].update(batch_budget="2.01"),
    lambda m: m["online_pilot"].update(base_url="https://elsewhere.invalid"),
    lambda m: m["trials"].reverse(),
    lambda m: m["trials"][0].update(task_progress_mode="evidence_harness"),
    lambda m: m["trials"][0]["runtime"]["request_limits"].update(max_http_attempts=13),
    lambda m: m["authorization"].update(episodes=1),
    lambda m: m["authorization"].update(max_http_attempts=1),
    lambda m: m["authorization"].update(batch_cost_limit_usd="2.00"),
    lambda m: m["authorization"].update(old_14_slot_and_360_queues_resumed=True),
    lambda m: m["authorization"].update(episodes=False),
    lambda m: m["authorization"].update(old_14_slot_and_360_queues_resumed=0),
    lambda m: m["online_pilot"].update(sdk_retries=False),
])
def test_fixed_config_and_authorization_cannot_change_even_if_manifest_hash_is_rewritten(frozen, mutation):
    run, manifest = frozen
    mutation(manifest)
    seal_manifest(run, manifest)
    with pytest.raises(ValueError):
        pilot.load_manifest(run)


@pytest.mark.parametrize("relative", ["source/inert.py", "inputs/inert.pkl", "PROTOCOL_CN.md", "manifest.json"])
def test_frozen_hashes_detect_file_tampering(frozen, relative):
    run, _ = frozen
    path = run / relative
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        pilot.load_manifest(run)


def test_frozen_inventory_rejects_extra_source_or_symlink(frozen):
    run, _ = frozen
    put(run / "source/extra.py", b"unsealed code")
    with pytest.raises(ValueError, match="inventory"):
        pilot.load_manifest(run)
    (run / "source/extra.py").unlink()
    (run / "inputs/alias.pkl").symlink_to(run / "inputs/inert.pkl")
    with pytest.raises(ValueError, match="Symlink"):
        pilot.load_manifest(run)
