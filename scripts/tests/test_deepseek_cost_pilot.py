"""Ten-slot cost-pair freeze gates; no model or worker is dispatched here."""
import ast
import copy
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import deepseek_contract_pilot as previous
import deepseek_cost_pilot as pilot
from harness_deepseek_adapter import httpx
from harness_live_cost import LiveCostLedger, LiveCostError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Cost-pilot tests cannot dispatch network requests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def frozen(tmp_path):
    run = tmp_path / "frozen"
    manifest = pilot.prepare(run, local_smoke=True)
    return run, manifest


def reseal(run, manifest):
    pilot.write(run / "manifest.json", manifest)
    (run / "manifest.sha256").write_text(pilot.sha((run / "manifest.json").read_bytes()) + "\n")


def test_fixed_ten_slots_compare_old_and_new_integrated_capabilities():
    trials = pilot.trials()
    conditions = ["short_chain", "navigation", "search", "unknown_takeoff", "search_complete"]
    order = [("control", "candidate"), ("candidate", "control"),
             ("control", "candidate"), ("candidate", "control"), ("control", "candidate")]
    assert len(trials) == 10
    assert [(t["condition"], t["arm"]) for t in trials] == [
        (condition, arm) for condition, arms in zip(conditions, order) for arm in arms]
    assert [t["task_id"] for t in trials] == ["c74d7020"] * 2 + ["6c3f98ce"] * 2 + ["fdd21809"] * 2 + ["c74d7020"] * 2 + ["b1ad4f3e"] * 2
    for t in trials:
        assert t["task_progress_mode"] == "evidence_harness"
        assert t["scheduler"] == "applicability"
        assert t["runtime"] == previous.RUNTIME == pilot.RUNTIME
        assert t["watchdog_seconds"] == 515
        assert (t["harness_revision"], t["input_layout"]) == (
            ("observed_target_v7", "full_v1") if t["arm"] == "control"
            else ("search_confirm_v9", "compact_tools_v1"))
        if t["condition"] == "unknown_takeoff":
            assert t["api_fault"] == {"kind": "response_lost_after_success", "method": "POST",
                "endpoint": "/drones/3d229034/command/take_off", "occurrence": 1}
        else:
            assert "api_fault" not in t
    trials[0]["runtime"]["request_limits"]["max_http_attempts"] = 1
    assert trials[1]["runtime"] == pilot.RUNTIME


def test_new_authorization_is_bounded_and_separate_from_local_and_old_batches():
    auth = pilot.authorization(False)
    assert auth["source"] == "user_explicit_deepseek_cost_pilot_authorization_20261103"
    assert auth["episodes"] == 10 and auth["max_http_attempts"] == 120
    assert auth["batch_cost_limit_usd"] == "2.00" and auth["trial_cost_limit_usd"] == "0.50"
    assert auth["old_6_14_360_queues_resumed"] is False
    assert auth["budget_source"] == "new_bounded_implementation_allocation_not_inherited_old_run"
    local = pilot.authorization(True)
    assert local["episodes"] == local["max_http_attempts"] == 0
    assert local["batch_cost_limit_usd"] == local["trial_cost_limit_usd"] == "0"


def test_local_prepare_freezes_both_origins_without_credential_access(frozen, monkeypatch):
    run, manifest = frozen
    def forbidden(*args, **kwargs):
        raise AssertionError("Local manifest verification must not read credentials")
    monkeypatch.setattr(pilot, "credential", forbidden)
    assert pilot.load_manifest(run) == manifest
    assert manifest["scope"] == "deepseek_cost_pilot_v1"
    assert manifest["online_variant_contract"] == "harness_cost_pair_v1"
    assert manifest["online_contract"] == previous.CONTRACT
    assert manifest["inputs"]["origin-manifest.json"] == pilot.STUDY_SHA256
    assert manifest["inputs"]["extra-origin-manifest.json"] == pilot.EXTRA_STUDY_SHA256
    extra_origin = json.loads((run / "inputs/extra-origin-manifest.json").read_bytes())
    fixture = json.loads((run / "inputs/fixtures/b1ad4f3e.json").read_bytes())
    assert "split" not in fixture  # Preserve the legacy fixture bytes, label exposure in the protocol.
    for name in ("fixtures/b1ad4f3e.json", "fixtures/b1ad4f3e.pkl", "fixtures/scene-35ce64d8.timeline.pkl"):
        assert manifest["inputs"][name] == extra_origin["files"][name]
    assert manifest["inputs"]["dataset/" + pilot.EXTRA_SCENE] == fixture["source_sha256"]
    assert not (run / "process-ledger.jsonl").exists()
    assert not (run / "cost-ledger.jsonl").exists()
    assert not (run / "trials").exists()


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(scope=previous.SCOPE),
    lambda m: m.pop("online_variant_contract"),
    lambda m: m.update(online_variant_contract="future"),
    lambda m: m.update(online_contract="legacy"),
    lambda m: m.update(model="different-model"),
    lambda m: m.update(local_smoke=1),
    lambda m: m["trials"][0].update(task_progress_mode="off"),
    lambda m: m["trials"][0].update(input_layout="compact_tools_v1"),
    lambda m: m["trials"][1].update(harness_revision="search_progress_v8"),
    lambda m: m["trials"][1].pop("input_layout"),
    lambda m: m["trials"].reverse(),
    lambda m: m["trials"].pop(),
    lambda m: m["trials"].append(copy.deepcopy(m["trials"][-1])),
    lambda m: m["trials"][8].update(task_id="7eba7390"),
    lambda m: m["trials"][6]["api_fault"].update(occurrence=2),
    lambda m: m["trials"][0].update(api_fault=copy.deepcopy(m["trials"][6]["api_fault"])),
    lambda m: m["online_common_runtime"]["request_limits"].update(max_total_tokens=200001),
    lambda m: m["online_implementation_budget"].update(search_rounds=17),
    lambda m: m["online_pilot"].update(batch_budget="2.01"),
    lambda m: m["online_pilot"].update(sdk_retries=1),
    lambda m: m["authorization"].update(episodes=10),
    lambda m: m["authorization"].update(old_6_14_360_queues_resumed=True),
])
def test_rehashed_manifest_cannot_change_fixed_comparison(frozen, mutation):
    run, manifest = frozen
    mutation(manifest)
    reseal(run, manifest)
    with pytest.raises(ValueError):
        pilot.load_manifest(run)


@pytest.mark.parametrize("name", [
    "extra-origin-manifest.json", "fixtures/b1ad4f3e.json", "fixtures/b1ad4f3e.pkl",
    "fixtures/scene-35ce64d8.timeline.pkl", "dataset/" + pilot.EXTRA_SCENE,
])
def test_rehashed_extra_input_still_must_match_external_origin(frozen, name):
    run, manifest = frozen
    path = run / "inputs" / name
    if name.endswith(".json"):
        value = json.loads(path.read_bytes())
        value["test_tamper"] = True
        pilot.write(path, value)
    else:
        path.write_bytes(path.read_bytes() + b"tampered")
    manifest["inputs"][name] = pilot.sha(path.read_bytes())
    reseal(run, manifest)
    with pytest.raises(ValueError):
        pilot.load_manifest(run)


def test_prepare_does_not_overwrite_and_inventory_cannot_expand(frozen):
    run, _ = frozen
    with pytest.raises(FileExistsError):
        pilot.prepare(run, local_smoke=True)
    (run / "inputs/unfrozen.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        pilot.load_manifest(run)


def test_cost_policy_and_reconciliation_are_unchanged_from_verified_contract():
    assert pilot.cost_policy() == previous.cost_policy()
    # The new coordinator changes selectors and inputs, not accounting or close behavior.
    for name in ("finish_worker_cost", "read_cost_snapshot"):
        bodies = []
        for module in (previous, pilot):
            tree = ast.parse(Path(module.__file__).read_text())
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            bodies.append(ast.dump(node, include_attributes=False))
        assert bodies[0] == bodies[1]


@pytest.mark.parametrize("unknown", [False, True])
def test_cost_close_does_not_convert_unknown_usage_into_permission(tmp_path, unknown):
    ledger = LiveCostLedger.create(tmp_path / "cost-ledger.jsonl", pilot.cost_policy(), "2.00")
    ledger.start_trial("first", "0.50")
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions", json={
        "max_tokens": 8192, "messages": [{"role": "user", "content": "Status"}]})
    reservation = ledger.reserve("first", request)
    usage = {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}
    ledger.settle(reservation["reservation_id"], json.dumps({"usage": usage}).encode())
    attempt = {"attempt_id": 1, "delegated": True, "usage": usage, "usage_status": "known",
        "request_body_length": len(request.content), "request_body_sha256": pilot.sha(request.content)}
    result = {"trial_id": "first", "drained": True, "limits": {"closed": True,
        "needs_process_termination": False, "threads_still_alive": [], "stop_reason": "token_limit",
        "admitted_http_attempts": 1, "delegated_http_attempts": 1, "unknown_usage_attempts": 0,
        "unknown_usage_attempt_ids": [], "known_total_tokens": 26, "attempts": [attempt]},
        "agent": {"archive_verify": {"complete": False}}}
    if unknown:
        attempt["usage_status"] = "unknown"
        result["limits"].update(unknown_usage_attempts=1, unknown_usage_attempt_ids=[1])
    try:
        state = pilot.finish_worker_cost(ledger, "first", result)
        assert state["blocked"] is unknown
        if unknown:
            with pytest.raises(LiveCostError):
                ledger.start_trial("second", "0.50")
        else:
            ledger.start_trial("second", "0.50")
            ledger.finish_trial("second")
    finally:
        ledger.close()


@pytest.mark.parametrize("condition,expected", [
    ("short_chain", ["list_drones", "run_harness"]),
    ("navigation", ["list_drones", "take_off", "navigate_to", "hover"]),
    ("search", ["list_drones", "run_harness"]),
    ("unknown_takeoff", ["list_drones", "run_harness", "get_drone_status", "hover"]),
    ("search_complete", ["list_drones", "run_harness"]),
])
def test_local_scripts_are_same_public_decisions_in_both_integrated_arms(condition, expected):
    sequences = []
    for trial in [t for t in pilot.trials() if t["condition"] == condition]:
        transport = pilot.mock_transport(httpx, trial)
        body = {"tools": [{"function": {"name": name}} for name in expected],
                "messages": [{"role": "tool", "content": json.dumps([
                    {"id": "actor-3", "name": "Drone 3"}, {"id": "actor-4", "name": "Drone 4"}])}]}
        sequence = []
        try:
            for _ in range(len(expected) + 1):
                response = transport.handle_request(httpx.Request("POST", "https://offline.invalid", json=body))
                message = response.json()["choices"][0]["message"]
                if "tool_calls" not in message:
                    assert "[TASK DONE]" in message["content"]
                    continue
                call = message["tool_calls"][0]["function"]
                args = json.loads(call["arguments"])
                if "input_json" in args:
                    assert isinstance(args["input_json"], str)
                sequence.append((call["name"], args))
        finally:
            transport.close()
        assert [name for name, _ in sequence] == expected
        sequences.append(sequence)
    assert sequences[0] == sequences[1]
