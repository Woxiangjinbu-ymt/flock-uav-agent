"""New fixed eight-slot gates, genuine SDK wire, and durable cost boundaries."""
import asyncio
import copy
import json
from pathlib import Path
import socket
import sys

import openai
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import deepseek_contract_pilot as pilot
from harness_deepseek_adapter import httpx
from harness_live_cost import LiveCostLedger, LiveCostError, make_cost_transport
from episode_termination import should_block_cost_on_request_stop


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Contract pilot tests cannot call a provider")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def frozen(tmp_path):
    run = tmp_path / "run"
    return run, pilot.prepare(run, local_smoke=True)


def reseal(run, manifest):
    pilot.write(run / "manifest.json", manifest)
    (run / "manifest.sha256").write_text(pilot.sha((run / "manifest.json").read_bytes()) + "\n")


def test_fixed_eight_slots_budget_and_public_api_fault_scope():
    trials = pilot.trials()
    assert len(trials) == 8
    assert [t["arm"] for t in trials] == ["baseline", "integrated", "integrated", "baseline",
                                          "baseline", "integrated", "integrated", "baseline"]
    assert [t["task_id"] for t in trials] == ["c74d7020"] * 2 + ["6c3f98ce"] * 2 + ["fdd21809"] * 2 + ["c74d7020"] * 2
    assert all(t["runtime"] == pilot.RUNTIME for t in trials)
    assert all("api_fault" not in t for t in trials[:6])
    assert trials[6]["api_fault"] == trials[7]["api_fault"] == {
        "kind": "response_lost_after_success", "method": "POST",
        "endpoint": "/drones/3d229034/command/take_off", "occurrence": 1}
    assert pilot.PILOT["batch_budget"] == "2.00" and pilot.PILOT["trial_budget"] == "0.50"


def test_prepare_binds_development_inputs_and_has_no_credential_lookup(frozen):
    run, manifest = frozen
    assert pilot.load_manifest(run) == manifest
    assert manifest["online_contract"] == "task_execution_state_v1"
    assert manifest["authorization"]["episodes"] == manifest["authorization"]["max_http_attempts"] == 0
    assert pilot.credential(manifest) == "synthetic-local-deepseek-candidate-not-a-real-key"
    assert manifest["inputs"]["origin-manifest.json"] == pilot.STUDY_SHA256


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(model="other"),
    lambda m: m.update(online_contract="legacy"),
    lambda m: m["online_common_runtime"]["request_limits"].update(max_total_tokens=200001),
    lambda m: m["online_implementation_budget"].update(repair_budget=17),
    lambda m: m.update(online_fault_contract="other"),
    lambda m: m["online_pilot"].update(batch_budget="2.01"),
    lambda m: m["online_pilot"].update(trial_budget="0.51"),
    lambda m: m["online_pilot"].update(sdk_retries=1),
    lambda m: m["online_pilot"].update(thinking="disabled"),
    lambda m: m["trials"].reverse(),
    lambda m: m["trials"][6]["api_fault"].update(occurrence=2),
    lambda m: m["trials"][0].update(api_fault=m["trials"][6]["api_fault"]),
    lambda m: m["authorization"].update(episodes=8),
    lambda m: m["authorization"].update(old_6_14_360_queues_resumed=True),
    lambda m: m.update(local_smoke=1),
])
def test_rehashed_manifest_cannot_expand_fixed_scope(frozen, mutation):
    run, manifest = frozen
    mutation(manifest)
    reseal(run, manifest)
    with pytest.raises(ValueError):
        pilot.load_manifest(run)


def test_rehashed_fixture_cannot_break_origin_binding(frozen):
    run, manifest = frozen
    name = "fixtures/c74d7020.json"
    path = run / "inputs" / name
    value = json.loads(path.read_bytes())
    value["command"] += " changed"
    value["command_sha256"] = pilot.sha(value["command"].encode())
    pilot.write(path, value)
    manifest["inputs"][name] = pilot.sha(path.read_bytes())
    reseal(run, manifest)
    with pytest.raises(ValueError):
        pilot.load_manifest(run)


def test_extra_inventory_and_overwrite_rejected(frozen):
    run, _ = frozen
    with pytest.raises(FileExistsError):
        pilot.prepare(run, local_smoke=True)
    (run / "source/extra.py").write_text("# unsealed")
    with pytest.raises(ValueError, match="inventory"):
        pilot.load_manifest(run)


def settled_worker(tmp_path, *, stop=None):
    ledger = LiveCostLedger.create(tmp_path / "cost-ledger.jsonl", pilot.cost_policy(), pilot.PILOT["batch_budget"])
    ledger.start_trial("first", pilot.PILOT["trial_budget"])
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions", json={
        "model": "deepseek-flash", "max_tokens": 8192, "messages": [{"role": "user", "content": "Read status"}]})
    reservation = ledger.reserve("first", request)
    usage = {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}
    ledger.settle(reservation["reservation_id"], json.dumps({"usage": usage}).encode())
    result = {"trial_id": "first", "drained": True, "limits": {"closed": True,
        "needs_process_termination": False, "threads_still_alive": [], "stop_reason": stop,
        "admitted_http_attempts": 1, "delegated_http_attempts": 1, "unknown_usage_attempts": 0,
        "unknown_usage_attempt_ids": [], "known_total_tokens": 26, "attempts": [{"attempt_id": 1,
            "delegated": True, "usage": usage, "usage_status": "known", "request_body_length": len(request.content),
            "request_body_sha256": pilot.sha(request.content)}]},
        "agent": {"archive_verify": {"complete": False}}}
    return ledger, result


@pytest.mark.parametrize("stop", [None, "token_limit", "http_attempt_limit"])
def test_known_settled_budget_partial_archive_can_finish_and_next_trial_start(tmp_path, stop):
    ledger, result = settled_worker(tmp_path, stop=stop)
    if stop and should_block_cost_on_request_stop(stop):
        ledger.block(stop)
    state = pilot.finish_worker_cost(ledger, "first", result)
    assert not state["blocked"] and state["trials"]["first"]["status"] == "finished"
    ledger.close()
    with LiveCostLedger.open(tmp_path / "cost-ledger.jsonl", pilot.cost_policy(), pilot.PILOT["batch_budget"]) as next_ledger:
        next_ledger.start_trial("second", pilot.PILOT["trial_budget"])
        next_ledger.finish_trial("second")


@pytest.mark.parametrize("damage", ["preblocked", "unknown", "hash", "undrained", "deadline"])
def test_normal_stop_name_cannot_override_unknown_or_blocked_cost(tmp_path, damage):
    ledger, result = settled_worker(tmp_path, stop="token_limit")
    if damage == "preblocked":
        ledger.block("earlier_timeout")
    elif damage == "unknown":
        result["limits"]["attempts"][0]["usage_status"] = "unknown"
        result["limits"].update(unknown_usage_attempts=1, unknown_usage_attempt_ids=[1])
    elif damage == "hash":
        result["limits"]["attempts"][0]["request_body_sha256"] = "0" * 64
    elif damage == "deadline":
        assert should_block_cost_on_request_stop("task_deadline")
        ledger.block("task_deadline")
    else:
        result["drained"] = False
    assert pilot.finish_worker_cost(ledger, "first", result)["blocked"]
    ledger.close()
    with LiveCostLedger.open(tmp_path / "cost-ledger.jsonl", pilot.cost_policy(), pilot.PILOT["batch_budget"]) as other:
        with pytest.raises(LiveCostError):
            other.start_trial("next", "0.50")


def test_unknown_usage_mock_response_locks_real_reservation(tmp_path):
    ledger = LiveCostLedger.create(tmp_path / "cost-ledger.jsonl", pilot.cost_policy(), "2.00")
    ledger.start_trial("unknown", "0.50")
    dispatched = []
    def respond(request):
        dispatched.append(request)
        return httpx.Response(200, json={"choices": []}, request=request)
    transport = make_cost_transport(httpx, httpx.MockTransport(respond), ledger, "unknown")
    with pytest.raises(LiveCostError):
        transport.handle_request(httpx.Request("POST", "https://api.deepseek.com/chat/completions", json={"max_tokens": 8192}))
    state = ledger.snapshot()
    assert len(dispatched) == 1 and state["blocked"] and state["locked_reservation"] != "0"
    ledger.close()


def test_readonly_ledger_replay_never_opens_or_creates_locks(tmp_path, monkeypatch):
    ledger, result = settled_worker(tmp_path, stop="http_attempt_limit")
    expected = pilot.finish_worker_cost(ledger, "first", result)
    ledger.close()
    def forbidden(*args, **kwargs):
        raise AssertionError("audit must not open a ledger")
    monkeypatch.setattr(LiveCostLedger, "open", forbidden)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert pilot.read_cost_snapshot(tmp_path) == expected
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    (tmp_path / "cost-ledger.jsonl.head.json").write_text("{}")
    with pytest.raises(ValueError, match="head"):
        pilot.read_cost_snapshot(tmp_path)


def test_genuine_sdk_mock_cost_wire_and_reasoning_history(tmp_path):
    trial = pilot.trials()[0]
    ledger = LiveCostLedger.create(tmp_path / "cost-ledger.jsonl", pilot.cost_policy(), "2.00")
    ledger.start_trial(trial["trial_id"], "0.50")
    mock = pilot.mock_transport(httpx, trial)
    bodies = []
    class Record(httpx.BaseTransport):
        def handle_request(self, request):
            bodies.append(json.loads(request.content))
            return mock.handle_request(request)
    sync = openai.DefaultHttpxClient(transport=make_cost_transport(httpx, Record(), ledger, trial["trial_id"]),
                                    trust_env=False, follow_redirects=False)
    asynchronous = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(lambda _: None), trust_env=False)
    model = pilot.model_factory_for("synthetic-test-only")(http_client=sync, http_async_client=asynchronous, max_tokens=8192)
    tools = [{"type": "function", "function": {"name": name, "description": name,
        "parameters": {"type": "object", "properties": {}}}} for name in ("list_drones", "take_off", "hover")]
    try:
        first = model.invoke([HumanMessage("Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.")], tools=tools)
        model.invoke([HumanMessage("Command drone Drone 4 to take off to an altitude of 12 meters and hover in place."), first,
            ToolMessage('[{"id":"public-actor","name":"Drone 4"}]', tool_call_id=first.tool_calls[0]["id"])], tools=tools)
        assert len(bodies) == 2
        assert all(b["model"] == "deepseek-flash" and b["thinking"] == {"type": "enabled"}
                   and b["reasoning_effort"] == "high" and b["max_tokens"] == 8192
                   and b["stream"] is False and "temperature" not in b for b in bodies)
        assert next(m for m in bodies[1]["messages"] if m["role"] == "assistant")["reasoning_content"]
        assert "synthetic-test-only" not in json.dumps(bodies)
        assert model.max_retries == model.root_client.max_retries == 0
        state = ledger.snapshot()
        assert len(state["attempts"]) == 2 and all(a["status"] == "settled" for a in state["attempts"].values())
        ledger.finish_trial(trial["trial_id"])
    finally:
        sync.close(); asyncio.run(asynchronous.aclose()); ledger.close()
