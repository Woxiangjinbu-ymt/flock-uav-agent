"""Final request-boundary checks for the opt-in execution-state middleware.

Uses real LangChain requests, R2 ledgers and Harness runtimes, but never creates
a model client or dispatches a provider/simulator call.
"""
import asyncio
import copy
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))

from agent_profile import AgentProfile
from harness_core import HarnessRuntime
from harness_integration import HarnessMiddleware
from task_evidence_context import digest, message_value
from task_evidence_r2 import TaskEvidenceLedgerR2, TaskEvidenceR2Middleware


COMMAND = "Drone 1 takes off to 15 meters, rotates to 96 degrees, then hovers."
MARKER = "[Execution recovery contract: advisory public evidence, not task completion]\n"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Execution-state middleware checks must not use network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def components(monkeypatch, enabled):
    value = json.loads((ROOT / "experiments/general-agent-v1/profile.json").read_text())
    if enabled:
        value.update(schema_version=2, profile_id="general-agent-v2-local")
        value["agent"]["execution_contract"] = "task_execution_state_v1"
    for key, setting in AgentProfile.from_dict(value).environment().items():
        monkeypatch.setenv(key, setting)
    ledger = TaskEvidenceLedgerR2(COMMAND, "command-1")
    def forbidden(*args, **kwargs):
        raise AssertionError("No skill or public API execution belongs in a model-request test")
    runtime = HarnessRuntime(task_id="command-1", command=COMMAND, scene_id="scene-1",
        scene_epoch=0, ledger=ledger, invoke=forbidden, skill_factory=forbidden,
        scope=lambda: ("scene-1", 0), started=0, clock=lambda: 1,
        revision="observed_target_v7", static_obstacle_contract=True)
    return ledger, runtime, HarnessMiddleware(ledger, runtime)


def request(content="Original system instruction."):
    return ModelRequest(model=object(), tools=[],
        system_message=None if content is None else SystemMessage(content=content),
        messages=[HumanMessage(content=COMMAND)])


def messages(value):
    return ([value.system_message] if value.system_message else []) + value.messages


def projection(value):
    content = value.system_message.content
    text = content if isinstance(content, str) else "".join(
        block.get("text", "") for block in content if isinstance(block, dict))
    return json.loads(text.split(MARKER, 1)[1])


def invoke(middleware, value, *, asynchronous=False):
    seen = []
    response = SimpleNamespace(result=[AIMessage(content="Public scripted final.")])
    def handler(actual):
        seen.append(actual)
        return response
    async def async_handler(actual):
        return handler(actual)
    if asynchronous:
        result = asyncio.run(middleware.awrap_model_call(value, async_handler))
    else:
        result = middleware.wrap_model_call(value, handler)
    assert result is response and len(seen) == 1
    return seen[0]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("content", ["Original system instruction.", None,
                                      [{"type": "text", "text": "Original structured instruction."}]])
def test_v2_audit_hashes_include_the_final_system_projection(monkeypatch, asynchronous, content):
    ledger, _, middleware = components(monkeypatch, True)
    original = request(content)
    before = [copy.deepcopy(message_value(message)) for message in messages(original)]
    actual = invoke(middleware, original, asynchronous=asynchronous)
    assert projection(actual)["schema"] == "task-recovery-prompt-v1"
    expected = [digest(message_value(message)) for message in messages(actual)]
    audit = ledger.model_requests[-1]
    assert audit["scope"] == "pre_provider_langchain_messages_not_wire_payload"
    assert audit["message_sha256"] == expected
    assert audit["request_messages_sha256"] == digest(expected)
    assert audit["message_sha256"][0] == digest(message_value(actual.system_message))
    if original.system_message is not None:
        assert audit["message_sha256"][0] != digest(message_value(original.system_message))
    assert [message_value(message) for message in messages(original)] == before
    assert ledger.snapshot()["observer_errors"] == []


def test_v1_matches_existing_r2_model_inputs_and_does_not_add_execution_projection(monkeypatch):
    ledger, _, middleware = components(monkeypatch, False)
    reference = TaskEvidenceR2Middleware(TaskEvidenceLedgerR2(COMMAND, "command-1"))
    original = request()
    actual = invoke(middleware, original)
    expected = reference._model_request(original)
    assert [message_value(message) for message in messages(actual)] == [
        message_value(message) for message in messages(expected)]
    assert MARKER not in actual.system_message.content
    assert ledger.model_requests[-1]["message_sha256"] == reference.ledger.model_requests[-1]["message_sha256"]


def test_v2_recovery_uses_visible_tool_evidence_without_private_or_full_receipt_data(monkeypatch):
    ledger, runtime, middleware = components(monkeypatch, True)
    first = invoke(middleware, request())
    assert projection(first)["items"] == []

    # These private roots must never be consulted by the public-state adapter.
    original_snapshot = ledger.snapshot
    def snapshot_with_private_roots():
        return {**original_snapshot(), "checker": {"passed": True, "secret": "PRIVATE_CHECKER_SENTINEL"},
                "world": {"hidden_target": "PRIVATE_WORLD_SENTINEL"}}
    monkeypatch.setattr(ledger, "snapshot", snapshot_with_private_roots)
    runtime.private_checker = {"secret": "PRIVATE_RUNTIME_SENTINEL"}
    evidence = ledger.begin_tool("take_off", {"drone_id": "actor-1", "altitude": 15}, "public-call-1")
    ledger.end_tool(evidence, error=TimeoutError("PUBLIC_ERROR_DETAIL_NOT_A_PROMPT_FIELD"))
    second = invoke(middleware, request())
    data = projection(second)
    assert data["mission_completion"] == "not_evaluated" and data["automatic_retry"] is False
    assert data["items"] == [{
        "category": "unknown_effect", "evidence_id": evidence, "tool_name": "take_off",
        "plan_id": None, "recommendation": "observe_current_public_state_without_certifying_history_do_not_replay",
        "eligible_now": True, "issue_state": "unresolved_in_public_record", "followup_evidence": [],
    }]
    encoded = json.dumps(data)
    for forbidden in ("PRIVATE_CHECKER_SENTINEL", "PRIVATE_WORLD_SENTINEL", "PRIVATE_RUNTIME_SENTINEL",
                      "PUBLIC_ERROR_DETAIL_NOT_A_PROMPT_FIELD", "actor-1", "altitude", "receipt"):
        assert forbidden not in encoded
    assert ledger.model_requests[-1]["message_sha256"][0] == digest(message_value(second.system_message))
    assert ledger.model_requests[-1]["message_sha256"][0] != ledger.model_requests[0]["message_sha256"][0]
    assert ledger.model_requests[-1]["common_prefix_messages"] == 0
    assert ledger.model_requests[-1]["previous_prefix_preserved"] is False
    assert ledger.snapshot()["observer_errors"] == []
