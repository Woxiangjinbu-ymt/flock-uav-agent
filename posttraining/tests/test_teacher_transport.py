"""Exercise the fixed teacher against the real local SDK, including admission failures."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
import time

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

SCRIPTS = (Path(__file__).resolve().parents[2] / "scripts")
sys.path.insert(0, str(SCRIPTS))
from harness_live_cost import LiveCostError
from flockcue_session_cpu.teacher_transport import TeacherFixture, create_ledger, ledger_snapshot

LIMITS = json.loads((SCRIPTS.parent / "docs/specs/04-deepseek-pilot40/pilot40.json").read_bytes())["pilot_limits"]
TOOLS = [{"type": "function", "function": {"name": name, "description": name,
          "parameters": {"type": "object", "properties": {}}}} for name in ("list_drones", "take_off")]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Local teacher attempted network access")
    monkeypatch.setattr(socket, "getaddrinfo", reject)
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)


def test_sdk_history_and_scene_ledger_handoff(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS.parent / "agent4drone"))
    from tool_input_contract import adapt_input_json_tools
    from uav_langchain_tools import create_uav_tools
    from thinking_events.sense_example_prepare import DESCRIPTIONS, sense_function

    tools = adapt_input_json_tools(create_uav_tools(object()), "json_object_or_string_v2")
    path = tmp_path / "cost-ledger.jsonl"
    create_ledger(path, LIMITS)
    for scene in ("scene1", "scene2"):
        task = scene + "-task1"
        folder = tmp_path / task
        folder.mkdir()
        teacher = TeacherFixture(path, LIMITS)
        try:
            assert teacher.begin(task, "Drone 1 take off to 15", folder, time.monotonic() + 60)
            model = teacher.model_factory()
            assert model.max_retries == 0
            history = [HumanMessage(content="Drone 1 take off to 15")]
            bound = model.bind_tools(tools)
            for turn in range(4):
                reply = bound.invoke(history)
                if turn == 0:
                    first = reply
                receipt = '[{"id":"d1","name":"Drone 1"}]' if turn == 0 else '{"success":true}'
                history.extend([reply, ToolMessage(receipt, tool_call_id=reply.tool_calls[0]["id"])])
            final = bound.invoke(history)
            assert not final.tool_calls and final.content.endswith("[TASK DONE]")
            request = json.loads((folder / "fixture-wire/turn-02.request.json").read_bytes())
            assert sha256(sense_function(request)["description"].encode()).hexdigest() == DESCRIPTIONS["B"]
            assistant, tool = request["messages"][-2:]
            assert assistant["reasoning_content"] == first.additional_kwargs["reasoning_content"]
            assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]
            assert json.loads(tool["content"])[0]["id"] == "d1"
            times = [json.loads(p.read_bytes()) for p in sorted((folder / "fixture-timing").glob("*.json"))]
            assert len(times) == 5
            for i, entry in enumerate(times, 1):
                assert entry["task_id"] == task and entry["attempt"] == entry["fixture_turn"] == i
                assert entry["fixture_dispatched"] and entry["sdk_response_received"] and entry["stop_reason"] is None
                start = entry["transport"]["start_monotonic_ns"]
                assert start <= entry["fixture"]["start_monotonic_ns"] < entry["fixture"]["end_monotonic_ns"]
                assert entry["fixture"]["end_monotonic_ns"] <= entry["transport"]["end_monotonic_ns"]
                for name in ("transport", "fixture"):
                    interval = entry[name]
                    assert datetime.fromisoformat(interval["end_utc"]) >= datetime.fromisoformat(interval["start_utc"])
                    assert interval["elapsed_ns"] == interval["end_monotonic_ns"] - interval["start_monotonic_ns"]
                stem = folder / "fixture-wire" / f"turn-{i:02d}"
                assert entry["request_body_sha256"] == sha256(stem.with_suffix(".request.json").read_bytes()).hexdigest()
                assert entry["response_body_sha256"] == sha256(stem.with_suffix(".response.json").read_bytes()).hexdigest()
            result = teacher.finish()
            assert result["fixture_dispatches"] == 5 and result["stop_reason"] is None
        finally:
            teacher.close()
    state = ledger_snapshot(path, LIMITS)
    assert len(state["attempts"]) == 10 and len(state["trials"]) == 2
    assert not state["blocked"] and state["reserved"] == "0"


@pytest.mark.parametrize("trial_cap,batch_cap,missing,reason", [
    ("2.162688", "30", False, "trial_budget_insufficient"),
    ("4", "2.162688", False, "batch_budget_insufficient"),
    ("4", "30", True, "unknown_or_invalid_usage"),
])
def test_dispatch_and_missing_usage(tmp_path, trial_cap, batch_cap, missing, reason):
    path = tmp_path / "cost-ledger.jsonl"
    create_ledger(path, LIMITS, batch_cap=batch_cap)
    teacher = TeacherFixture(path, LIMITS, per_task_cap=trial_cap, batch_cap=batch_cap, missing_usage=missing)
    try:
        assert teacher.begin("task1", "Drone 1", tmp_path, time.monotonic() + 60)
        model = teacher.model_factory()
        if not missing:
            model.invoke([HumanMessage(content="Drone 1")], tools=TOOLS)
        with pytest.raises(LiveCostError, match=reason):
            model.invoke([HumanMessage(content="Drone 1")], tools=TOOLS)
        result = teacher.finish()
        assert result["stop_reason"] == teacher.blocked_reason() == reason
        assert result["fixture_dispatches"] == 1
        assert not teacher.begin("task2", "Drone 2", tmp_path, time.monotonic() + 60)
        assert len(list((tmp_path / "fixture-wire").glob("*.response.json"))) == 1
        times = [json.loads(p.read_bytes()) for p in sorted((tmp_path / "fixture-timing").glob("*.json"))]
        assert len(times) == (1 if missing else 2)
        refused = times[-1]
        assert not refused["sdk_response_received"] and refused["stop_reason"] == reason
        assert refused["fixture_dispatched"] is missing and (refused["fixture"] is not None) is missing
        assert (refused["response_body_sha256"] is not None) is missing
    finally:
        teacher.close()
    state = ledger_snapshot(path, LIMITS, batch_cap=batch_cap)
    assert len(state["attempts"]) == 1 and state["blocked"]
    assert state["reserved"] == ("2.162688" if missing else "0")


@pytest.mark.parametrize("key,limit,reason,stops_batch", [
    ("max_http_attempts_per_task", 1, "task_request_limit", False),
    ("max_billed_usage_tokens_per_task_stop_after_response", 1232, "task_token_limit", False),
    ("max_http_attempts_total", 1, "batch_request_limit", True),
])
def test_task_and_batch_quotas(tmp_path, key, limit, reason, stops_batch):
    limits = {**LIMITS, key: limit}
    path = tmp_path / "ledger.jsonl"
    create_ledger(path, limits)
    teacher = TeacherFixture(path, limits)
    try:
        assert teacher.begin("first", "Drone 1", tmp_path, time.monotonic() + 60)
        model = teacher.model_factory()
        model.invoke([HumanMessage(content="Drone 1")], tools=TOOLS)
        with pytest.raises(LiveCostError, match=reason):
            model.invoke([HumanMessage(content="Drone 1")], tools=TOOLS)
        result = teacher.finish()
        assert result["stop_reason"] == reason and result["batch_stopped"] is stops_batch
        assert len(teacher.ledger.snapshot()["attempts"]) == 1
        if not stops_batch:
            folder = tmp_path / "second"
            folder.mkdir()
            assert teacher.begin("second", "Drone 2", folder, time.monotonic() + 60)
            model.invoke([HumanMessage(content="Drone 2")], tools=TOOLS)
            assert teacher.finish()["fixture_dispatches"] == 1
    finally:
        teacher.close()
