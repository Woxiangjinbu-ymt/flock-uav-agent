"""Offline checks for the original-arm scope and passive HTTP response observation."""
import copy
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import zipfile

import httpx2
import pytest
from langchain_openai import ChatOpenAI

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import run_agent4drone_matched_subset as launch


def test_only_frozen_whole_sessions_in_original_order():
    plan = launch.read(launch.ROOT / launch.PLAN_FILE)
    assert launch.sha(launch.ROOT / launch.PLAN_FILE) == launch.PLAN_SHA
    assert len(launch.check_plan(plan)) == 10
    with pytest.raises(ValueError):
        launch.check_plan(launch.read(launch.ROOT / launch.PARENT_PLAN))
    modified = copy.deepcopy(plan)
    modified["sessions"][0]["tasks"].reverse()
    with pytest.raises(ValueError):
        launch.check_plan(modified)


def test_actual_sdk_request_and_response_metadata(tmp_path):
    with zipfile.ZipFile(launch.OCTOBER / "source.zip") as archive:
        code = launch.telemetry_source(archive.read(launch.METRICS).decode())
    metrics = ModuleType("matched_observation")
    exec(compile(code, launch.METRICS, "exec"), metrics.__dict__)
    llm = ChatOpenAI(model="deepseek-flash", temperature=0.1, api_key="offline-fake-key",
                     base_url="https://model.invalid")
    outbound = []
    reply = {"id": "mock", "object": "chat.completion", "created": 1, "model": "deepseek-flash",
             "system_fingerprint": "simulated-fingerprint",
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "SECRET_REPLY"}}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7,
                       "completion_tokens_details": {"reasoning_tokens": 1}}}

    def respond(request):
        outbound.append(request.content)
        return httpx2.Response(200, json=reply)

    fake_agent = SimpleNamespace(ProviderTokenLoggingCallback=type("Callback", (), {}))
    service = SimpleNamespace(process_agent_command=lambda job, command: llm.invoke(command))
    path = tmp_path / metrics.METRICS_FILENAME
    with patch.object(httpx2.HTTPTransport, "handle_request", side_effect=respond), \
            patch.dict(sys.modules, uav_agent=fake_agent):
        without_observer = service.process_agent_command("job", "SECRET_REQUEST")
        undo = metrics.install_agent_metrics(service, path, "https://model.invalid")
        try:
            with_observer = service.process_agent_command("job", "SECRET_REQUEST")
        finally:
            undo()
    assert len(outbound) == 2 and outbound[0] == outbound[1]
    assert with_observer.content == without_observer.content == "SECRET_REPLY"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    response, = (r for r in records if r["event"] == "http_end")
    assert (response["job_id"], response["reported_model"], response["system_fingerprint"],
            response["reasoning_tokens"], response["usage"]["total_tokens"]) == (
                "job", "deepseek-flash", "simulated-fingerprint", 1, 7)
    assert "SECRET" not in path.read_text()
