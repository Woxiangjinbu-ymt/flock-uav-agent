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
import validate_hover_input_contract as local
from general_agent_local import validate_trace


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


def test_fixed_sixteen_scripted_cases_and_predeclared_outcomes():
    plan = local.read(local.CONFIG / "plan.json")
    cases = local.validate_plan(plan)
    assert len(cases) == 16
    assert tuple(plan["expected_claim"].values()) == (
        True, True, False, False, False, False, True, True,
        True, True, False, True, False, True, False, None)
    assert tuple(plan["expected_actions"].values()) == (1, 1, 1, 1, 1, 2, 1, 1, 2, 1, 0, 1, 1, 2, 2, 0)
    for case in cases:
        trace = local.read(local.CONFIG / case["trace_file"])
        assert validate_trace(trace) == trace
        assert trace[0] == {"tool": "list_drones", "arguments": {}}
        assert trace[-1] == {"final": "Task complete. [TASK DONE]"}
    assert [c["trial_id"] for c in cases if "api_fault" in c] == ["unknown-raw", "unknown-skill"]


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(planned_episodes=17),
    lambda p: p.update(provider_calls_authorized=1),
    lambda p: p.update(task_id="unseen-test"),
    lambda p: p.update(source_manifest_sha256="changed"),
    lambda p: p["groups"].reverse(),
    lambda p: p["groups"][1].update(derived_command="an arbitrary new task"),
    lambda p: p["groups"][-1].update(profile="profile.json"),
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
    assert new["agent"].pop("completion_contract") == "hover_completion_v1"
    assert new["agent"].pop("tool_input_contract") == "json_object_or_string_v1"
    new.update(schema_version=3, profile_id="general-agent-v3-local")
    assert new == old
    assert old["agent"]["input_layout"] == "compact_tools_v1"
    assert old["harness"]["revision"] == "search_confirm_v9"


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


def test_all_sixteen_slots_are_prepared_before_any_execution_and_tamper_is_rejected(tmp_path):
    output = tmp_path / "local"
    manifest = local.prepare_campaign(output)
    assert local.load_campaign(output) == manifest
    assert [len(b["trial_ids"]) for b in manifest["bundles"]] == [12, 2, 1, 1]
    assert not list(output.rglob("process-ledger.jsonl"))
    assert not list(output.rglob("worker.json"))
    assert all(b["sources"] == manifest["bundles"][0]["sources"] for b in manifest["bundles"])
    with pytest.raises(FileExistsError):
        local.prepare_campaign(output)
    path = output / "selection-source/experiments/hover-input-contract-v1/traces/scalar-reject.json"
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
