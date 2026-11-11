"""Acceptance scope/provenance checks; no model, process or pickle execution."""
from copy import deepcopy
import json
from pathlib import Path
import pickle
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "agent4drone"))
import validate_hover_compact_contract as local
from general_agent_local import validate_trace
import general_agent_local as scripted


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Acceptance unit tests forbid network, processes and pickle execution")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)


def test_fixed_twenty_scripted_cases_and_predeclared_outcomes():
    plan = local.read(local.CONFIG / "plan.json")
    cases = local.validate_plan(plan)
    assert len(cases) == 20
    assert tuple(plan["expected_claim"].values()) == (
        True, True, False, False, False, False, True, True,
        True, True, False, True, True, False, False, False, True, False, None, False)
    assert tuple(plan["expected_actions"].values()) == (1, 1, 1, 1, 1, 2, 1, 1, 2, 1, 0, 1, 1, 1, 1, 1, 2, 2, 0, 1)
    for case in cases:
        trace = local.read(local.CONFIG / case["trace_file"])
        assert validate_trace(trace) == trace
        assert trace[0] == {"tool": "list_drones", "arguments": {}} or "parallel_tools" in trace[0]
        assert trace[-1] == {"final": "Task complete. [TASK DONE]"}
    assert [c["trial_id"] for c in cases if "api_fault" in c] == ["unknown-raw", "unknown-skill", "parallel-unknown"]


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(planned_episodes=21),
    lambda p: p.update(provider_calls_authorized=1),
    lambda p: p.update(task_id="unseen-test"),
    lambda p: p.update(source_manifest_sha256="changed"),
    lambda p: p["groups"].reverse(),
    lambda p: p["groups"][1].update(derived_command="an arbitrary new task"),
    lambda p: p["groups"][-2].update(profile="profile.json"),
    lambda p: p["expected_claim"].pop("scalar-reject"),
])
def test_campaign_cannot_expand_or_silently_change_condition(mutate):
    plan = local.read(local.CONFIG / "plan.json")
    mutate(plan)
    with pytest.raises(ValueError):
        local.validate_plan(plan)


def test_profiles_keep_compact_v9_and_make_new_contracts_explicit():
    old = local.read(local.CONFIG / "legacy-profile.json")
    new = local.read(local.CONFIG / "profile.json")
    assert old == local.read(ROOT / "experiments/harness-cost-v1/recommended-profile.json")
    assert new["schema_version"] == 5 and new["profile_id"] == "general-agent-v5-local"
    assert new["agent"].pop("completion_contract") == "hover_completion_v2"
    assert new["agent"].pop("tool_input_contract") == "json_object_or_string_v2"
    new.update(schema_version=3, profile_id="general-agent-v3-local")
    assert new == old
    assert old["agent"]["input_layout"] == "compact_tools_v1"
    assert old["harness"]["revision"] == "search_confirm_v9"
    assert local.read(local.CONFIG / "v4-profile.json") == local.read(ROOT / "experiments/hover-input-contract-v1/profile.json")


def test_object_string_scripts_only_change_representation():
    a = local.read(local.CONFIG / "traces/state-string.json")
    b = local.read(local.CONFIG / "traces/state-object.json")
    assert [step.get("tool") for step in a] == ["list_drones", "take_off", "get_drone_status", None]
    for step in b:
        step.pop("input_json_encoding", None)
    assert a == b


@pytest.mark.parametrize("group", ["action", "duration"])
def test_public_instruction_derivation_preserves_original_snapshot_and_checker(tmp_path, group):
    study = ROOT / local.STUDY
    fixture_name = "fixtures/" + local.TASK + ".json"
    original_raw = (study / fixture_name).read_bytes()
    original_manifest = (study / "manifest.json").read_bytes()
    original = json.loads(original_raw)
    derivative = tmp_path / group
    manifest = local.derive_study(study, derivative, local.DERIVED[group])
    derived = local.read(derivative / fixture_name)
    for key in ("command", "command_sha256", "public_instruction_variant"):
        derived.pop(key)
    expected = deepcopy(original)
    expected.pop("command"); expected.pop("command_sha256")
    assert derived == expected
    assert local.read(derivative / fixture_name)["original_task"] == original["original_task"]
    for name in manifest["files"]:
        if name != fixture_name:
            assert (derivative / name).read_bytes() == (study / name).read_bytes()
    assert (study / fixture_name).read_bytes() == original_raw
    assert (study / "manifest.json").read_bytes() == original_manifest
    assert manifest["provider_calls_authorized"] == 0


def test_derivation_rejects_unplanned_instruction(tmp_path):
    with pytest.raises(ValueError, match="Unknown source or derived instruction"):
        local.derive_study(ROOT / local.STUDY, tmp_path / "bad", "anything else")
    assert not (tmp_path / "bad").exists()


def test_all_twenty_slots_are_prepared_before_any_execution_and_tamper_is_rejected(tmp_path):
    output = tmp_path / "local"
    manifest = local.prepare_campaign(output)
    assert local.load_campaign(output) == manifest
    assert [len(b["trial_ids"]) for b in manifest["bundles"]] == [15, 2, 1, 1, 1]
    assert not list(output.rglob("process-ledger.jsonl"))
    assert not list(output.rglob("worker.json"))
    assert all(b["sources"] == manifest["bundles"][0]["sources"] for b in manifest["bundles"])
    with pytest.raises(FileExistsError):
        local.prepare_campaign(output)
    path = output / "selection-source/experiments/hover-compact-contract-v2/traces/scalar-reject.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        local.load_campaign(output)


def test_input_audit_reads_actual_model_response_and_tool_span_formats(tmp_path):
    path = tmp_path / "archive.jsonl"
    records = [
        {"kind": "model_end", "data": {"output": {"result": [{"tool_calls": [
            {"id": "object", "name": "take_off", "args": {"input_json": {"drone_id": "u1"}}},
            {"id": "string", "name": "hover", "args": {"input_json": "{}"}}]}]}}},
        {"kind": "tool_start", "data": {"tool_span_id": "span", "raw_tool_call_id": "invalid"}},
        {"kind": "tool_end", "data": {"tool_span_id": "span", "output": {"status": "error"}}},
    ]
    before = deepcopy(records)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    audit = local.input_rejection_facts(path)
    assert audit == {"original_object_call_count": 1,
        "original_object_calls": [{"id": "object", "name": "take_off"}],
        "tool_error_count": 1, "tool_error_call_ids": ["invalid"]}
    assert local.rows(path) == before


def test_physical_action_comparison_ignores_clock_but_keeps_payload_and_commit():
    base = {"action": True, "endpoint": "/drones/u1/command/take_off", "params": {"altitude": 12},
            "body": None, "committed": True, "relative_seconds": .1}
    later = {**base, "relative_seconds": 100}
    assert local.normalized_actions([base]) == local.normalized_actions([later])
    for changed in ({"params": {"altitude": 18}}, {"committed": False}, {"endpoint": "/drones/u2/command/take_off"}):
        assert local.normalized_actions([base]) != local.normalized_actions([{**base, **changed}])
    assert local.normalized_actions([{**base, "action": False}]) == []


def parallel_step():
    return {"parallel_tools": [{"tool": "list_drones", "arguments": {}},
                               {"tool": "get_weather", "arguments": {}}],
            "local_api_barrier": "two_public_calls_v1"}


@pytest.mark.parametrize("change", [
    lambda x: x.pop("local_api_barrier"),
    lambda x: x.update(local_api_barrier=True),
    lambda x: x.update(local_api_barrier="automatic"),
    lambda x: x["parallel_tools"].pop(),
    lambda x: x["parallel_tools"].append({"tool": "get_weather", "arguments": {}}),
    lambda x: x["parallel_tools"].__setitem__(0, {"final": "done"}),
    lambda x: x["parallel_tools"].__setitem__(0, parallel_step()),
    lambda x: x.update(final="mixed"),
])
def test_parallel_fixture_is_explicit_two_tools_without_nested_or_silent_modes(change):
    step = parallel_step(); change(step)
    with pytest.raises(ValueError): validate_trace([step])


@pytest.mark.parametrize("parallel", [False, True])
def test_parallel_wire_keeps_explicit_object_and_legacy_trace_conversion(tmp_path, monkeypatch, parallel):
    import httpx
    trace = [{"tool": "take_off", "arguments": {"input_json": {"drone_id": "u1", "altitude": 12}}}]
    if parallel:
        trace = [{"parallel_tools": [trace[0], {**trace[0], "input_json_encoding": "object"}],
                  "local_api_barrier": "two_public_calls_v1"}]
    path = tmp_path / "inputs/trace.json"; path.parent.mkdir(); path.write_text(json.dumps(trace))
    (tmp_path / "trials/test/private").mkdir(parents=True)
    barriers = []
    monkeypatch.setattr(scripted, "arm_local_api_barrier", lambda *args: barriers.append(args))
    factory = scripted.factory_for(tmp_path, {"trial_id": "test", "trace_file": "trace.json"})
    transport = factory(httpx, {})
    payload = {"tools": [{"function": {"name": "take_off"}}], "messages": []}
    response = transport.handle_request(httpx.Request("POST", "https://local.invalid", json=payload))
    calls = response.json()["choices"][0]["message"]["tool_calls"]
    assert isinstance(json.loads(calls[0]["function"]["arguments"])["input_json"], str)
    if parallel:
        assert isinstance(json.loads(calls[1]["function"]["arguments"])["input_json"], dict)
        assert [c["id"] for c in calls] == ["script-0-0", "script-0-1"]
        assert barriers[0][0] == ["script-0-0", "script-0-1"]
    else:
        assert [c["id"] for c in calls] == ["script-0"] and not barriers
    transport.close()


def test_barrier_rendezvous_precedes_both_public_backend_calls_and_is_one_shot(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import uav_agent
    from harness_full100_world import ORIGIN
    path = tmp_path / "barrier.jsonl"
    called = []
    class Client:
        def _request(self, method, endpoint, **kwargs):
            evidence = local.rows(path)
            assert len(evidence) == 3 and evidence[-1]["event"] == "released"
            called.append(endpoint)
            return {"endpoint": endpoint}
    monkeypatch.setattr(uav_agent, "UAVAPIClient", Client)
    scripted.arm_local_api_barrier(["a", "b"], path)
    def request(ident):
        token = ORIGIN.set({"kind": "model_tool", "tool_call_id": ident})
        try: return Client()._request("GET", "/" + ident)
        finally: ORIGIN.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request, ident) for ident in ("a", "b")]
        assert [f.result(timeout=5) for f in futures] == [{"endpoint": "/a"}, {"endpoint": "/b"}]
    # Later requests from the same tool do not rendezvous a second time.
    assert request("a") == {"endpoint": "/a"}
    assert sorted(called) == ["/a", "/a", "/b"]
    assert len(local.rows(path)) == 3


def overlap_fixture():
    events = [{"tool_name": name, "tool_call_id": call, "evidence_id": evidence,
               "start_sequence": start, "end_sequence": end, "overlaps": [peer],
               "ordering": "uncertain", "scope": "observation", "status": "observed"}
              for name, call, evidence, peer, start, end in (
                  ("list_drones", "a", "e1", "e2", 1, 3), ("get_weather", "b", "e2", "e1", 2, 4))]
    barriers = [{"event": "entered", "tool_call_id": "a"}, {"event": "entered", "tool_call_id": "b"},
                {"event": "released", "tool_call_ids": ["a", "b"]}]
    return {"events": events}, barriers


def test_parallel_acceptance_requires_actual_mutual_intervals_and_barrier_evidence():
    state, barriers = overlap_fixture(); original = deepcopy((state, barriers))
    result = local.parallel_facts(state, barriers, ["list_drones", "get_weather"])
    assert result["barrier_and_two_public_calls"] and result["actual_mutual_overlap"]
    assert (state, barriers) == original


@pytest.mark.parametrize("mutate", [
    lambda s, b: s["events"][0].update(overlaps=[]),
    lambda s, b: s["events"][0].update(ordering="certain"),
    lambda s, b: s["events"][1].update(start_sequence=4, end_sequence=6),
    lambda s, b: s["events"][1].update(end_sequence=None),
    lambda s, b: b.pop(),
    lambda s, b: b.append({"event": "broken"}),
    lambda s, b: b[1].update(tool_call_id="a"),
])
def test_nonparallel_missing_or_inconsistent_evidence_is_not_a_true_parallel_acceptance(mutate):
    state, barriers = overlap_fixture(); mutate(state, barriers)
    result = local.parallel_facts(state, barriers, ["list_drones", "get_weather"])
    assert not (result["barrier_and_two_public_calls"] and result["actual_mutual_overlap"])
