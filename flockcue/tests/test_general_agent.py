"""Boundary/coordination tests for the parameterized local Agent entry.

No simulator, provider, subprocess or pickle deserialization runs in these tests.
The full real Agent/SDK/local-world acceptance has its own evidence denominator.
"""
import copy
import json
from pathlib import Path
import pickle
import socket
import subprocess
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import general_agent as coordinator
import general_agent_local as local


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value) + "\n").encode())
    return path


def read(path):
    return json.loads(path.read_text())


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unit test forbids sockets, subprocesses and pickle loads")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)


@pytest.fixture
def prepared(tmp_path):
    workspace, study = tmp_path / "workspace", tmp_path / "study"
    put(workspace / "agent4drone/inert.py", b"# inert source\n")
    put(workspace / "server/inert.py", b"# inert source\n")
    for name in coordinator.SCRIPTS:
        put(workspace / "scripts" / name, b"# never executed in a unit test\n")
    put(workspace / coordinator.PROTOCOL, b"Synthetic local-only protocol\n")
    origin_files = {}
    timeline = b"NOT A PICKLE: immutable initial timeline"
    timeline_path = put(study / "fixtures/timeline.pkl", timeline)
    origin_files["fixtures/timeline.pkl"] = coordinator.sha(timeline)
    cases = []
    for number in (1, 2):
        ident = "task-" + str(number)
        blob = ("NOT A PICKLE: initial world " + str(number)).encode()
        command = "Original public instruction " + str(number)
        fixture = {"task_id": ident, "scene_id": "scene", "command": command,
            "command_sha256": coordinator.sha(command.encode()), "timeline_file": timeline_path.name,
            "timeline_sha256": coordinator.sha(timeline), "snapshot_sha256": coordinator.sha(blob)}
        for name, value in ((ident + ".json", fixture), (ident + ".pkl", blob)):
            path = put(study / "fixtures" / name, value)
            origin_files["fixtures/" + name] = coordinator.sha(path.read_bytes())
        put(tmp_path / "cases" / (ident + ".json"), [{"final": "Local response"}])
        cases.append({"trial_id": ident, "task_id": ident, "trace_file": ident + ".json"})
    put(study / "manifest.json", {"files": origin_files})
    cases_path = put(tmp_path / "cases/cases.json", cases)
    profile = ROOT / "experiments/general-agent-v1/profile.json"
    output = tmp_path / "run"
    manifest = coordinator.prepare(profile, study, cases_path, output, workspace=workspace)
    return {"run": output, "manifest": manifest, "workspace": workspace, "study": study,
        "profile": profile, "cases": cases_path}


def rewrite_manifest(run, mutate):
    manifest = read(run / "manifest.json")
    mutate(manifest)
    put(run / "manifest.json", manifest)
    put(run / "manifest.sha256", (coordinator.sha((run / "manifest.json").read_bytes()) + "\n").encode())


def test_prepare_freezes_selected_tasks_sources_and_traces_without_loading_pickle(prepared):
    run = prepared["run"]
    manifest = coordinator.load_manifest(run)
    assert len(manifest["trials"]) == 2
    assert manifest["study"] == str(run / "inputs")
    assert manifest["online_authorized_executions"] == 0
    assert manifest["trials"][0]["runtime"] == manifest["agent_profile"]["runtime"]
    # Later edits to the development study/source/cases cannot change this attempt.
    put(prepared["study"] / "fixtures/task-1.pkl", b"edited origin")
    put(prepared["workspace"] / "agent4drone/inert.py", b"edited workspace")
    put(prepared["cases"].parent / "task-1.json", [{"final": "edited trace"}])
    assert coordinator.load_manifest(run) == manifest


def test_existing_attempt_is_never_overwritten(prepared):
    before = (prepared["run"] / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        coordinator.prepare(prepared["profile"], prepared["study"], prepared["cases"], prepared["run"],
                            workspace=prepared["workspace"])
    assert (prepared["run"] / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("relative", ["source/agent4drone/inert.py", "inputs/fixtures/task-1.pkl",
    "inputs/traces/task-1.json", "PROTOCOL_CN.md", "manifest.json"])
def test_sealed_input_or_manifest_tampering_is_rejected(prepared, relative):
    path = prepared["run"] / relative
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        coordinator.load_manifest(prepared["run"])


@pytest.mark.parametrize("relative", ["source/agent4drone/injected.py", "inputs/unsealed.json"])
def test_undeclared_file_cannot_enter_frozen_import_or_input_tree(prepared, relative):
    put(prepared["run"] / relative, b"unsealed")
    with pytest.raises(ValueError, match="inventory"):
        coordinator.load_manifest(prepared["run"])


def test_undeclared_symlink_directory_cannot_inject_imports(prepared):
    (prepared["run"] / "source/agent4drone/injected").symlink_to(prepared["workspace"], target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        coordinator.load_manifest(prepared["run"])


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(online_authorized_executions=True),
    lambda m: m.update(online_authorized_executions=1),
    lambda m: m.update(scope="online"),
    lambda m: m.update(local_model="remote-model"),
    lambda m: m.update(profile_sha256="0" * 64),
    lambda m: m.update(study=m["origin_study"]),
    lambda m: m["trials"][0]["runtime"].update(task_seconds=1),
    lambda m: m["trials"][0].update(scheduler="failure"),
    lambda m: m["trials"][0].update(trace_file="unsealed.json"),
])
def test_semantic_gates_still_reject_invalid_resealed_manifest(prepared, mutation):
    rewrite_manifest(prepared["run"], mutation)
    with pytest.raises(ValueError):
        coordinator.load_manifest(prepared["run"])


@pytest.mark.parametrize("case", [
    {"trial_id": "../escape", "task_id": "task", "trace_file": "trace.json"},
    {"trial_id": "trial", "task_id": "nested/task", "trace_file": "trace.json"},
    {"trial_id": "trial", "task_id": "task", "trace_file": 123},
    {"trial_id": "trial", "task_id": "task", "trace_file": "trace.json", "private_checker": {}},
    {"trial_id": "trial", "task_id": "task", "trace_file": "trace.json", "fault": {"kind": "cancel", "at_response": True}},
])
def test_case_selection_rejects_ambiguous_or_unsafe_fields(case):
    with pytest.raises(ValueError):
        coordinator.validate_cases([case])


def test_case_denominator_is_bounded_and_trial_ids_are_unique():
    case = {"trial_id": "trial", "task_id": "task", "trace_file": "trace.json"}
    for cases in ([], [case, copy.deepcopy(case)], [case] * 33):
        with pytest.raises(ValueError):
            coordinator.validate_cases(cases)


def test_trace_cannot_escape_cases_directory_and_prepare_leaves_no_partial_attempt(prepared):
    cases = read(prepared["cases"])
    cases[0]["trace_file"] = "../outside.json"
    put(prepared["cases"].parent.parent / "outside.json", [{"final": "outside"}])
    put(prepared["cases"], cases)
    output = prepared["run"].parent / "rejected"
    with pytest.raises(ValueError):
        coordinator.prepare(prepared["profile"], prepared["study"], prepared["cases"], output,
                            workspace=prepared["workspace"])
    assert not output.exists()


def test_explicit_null_fault_is_equivalent_to_no_fault(prepared):
    cases = read(prepared["cases"])
    cases[0]["fault"] = None
    put(prepared["cases"], cases)
    output = prepared["run"].parent / "null-fault"
    result = coordinator.prepare(prepared["profile"], prepared["study"], prepared["cases"], output,
                                 workspace=prepared["workspace"])
    assert result["trials"][0].get("fault") is None


def test_fault_index_must_refer_to_an_existing_scripted_response(prepared):
    cases = read(prepared["cases"])
    cases[0]["fault"] = {"kind": "cancel", "at_response": 1}
    put(prepared["cases"], cases)
    with pytest.raises(ValueError, match="outside"):
        coordinator.prepare(prepared["profile"], prepared["study"], prepared["cases"], prepared["run"].parent / "invalid",
                            workspace=prepared["workspace"])


def receipt(role, value):
    return {"role": role, "content": json.dumps(value)}


def test_actor_binding_uses_only_unambiguous_public_tool_receipts():
    public = receipt("tool", [{"id": "public-id", "name": "Drone 3"}])
    messages = [receipt("user", [{"id": "private-id", "name": "Drone 3"}]), public]
    assert local.resolve_public({"input_json": {"drone_id": "$drone:Drone 3"}}, messages) == {
        "input_json": {"drone_id": "public-id"}}
    with pytest.raises(ValueError, match="absent or ambiguous"):
        local.resolve_public("$drone:Drone 3", messages[:1])
    messages.append(receipt("tool", [{"id": "another-id", "name": "Drone 3"}]))
    with pytest.raises(ValueError, match="absent or ambiguous"):
        local.resolve_public("$drone:Drone 3", messages)


@pytest.mark.parametrize("trace", [[], [{"final": 3}], [{"tool": "take_off", "arguments": [], "final": "x"}],
    [{"tool": "take_off", "arguments": {}, "hidden": {}}], [{"final": "x"}] * 33])
def test_trace_accepts_only_explicit_public_response_shapes(trace):
    with pytest.raises(ValueError):
        local.validate_trace(trace)


def test_local_transport_requires_advertised_tool_and_never_invents_extra_response(tmp_path):
    trial = {"trial_id": "trace", "trace_file": "traces/trace.json"}
    put(tmp_path / "inputs" / trial["trace_file"], [{"tool": "take_off", "arguments": {
        "input_json": {"drone_id": "$drone:Drone 3", "altitude": 14}}}])
    (tmp_path / "trials/trace/private").mkdir(parents=True)
    factory = local.factory_for(tmp_path, trial)
    payload = {"messages": [receipt("tool", [{"id": "d3", "name": "Drone 3"}])], "tools": []}
    with httpx.Client(transport=factory(httpx, {}), trust_env=False) as client:
        with pytest.raises(ValueError, match="not advertised"):
            client.post("https://offline.invalid/v1/chat/completions", json=payload)
    payload["tools"] = [{"function": {"name": "take_off"}}]
    with httpx.Client(transport=factory(httpx, {}), trust_env=False) as client:
        response = client.post("https://offline.invalid/v1/chat/completions", json=payload).json()
        arguments = json.loads(response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        assert json.loads(arguments["input_json"])["drone_id"] == "d3"
        with pytest.raises(RuntimeError, match="Script exhausted"):
            client.post("https://offline.invalid/v1/chat/completions", json=payload)


def public_artifacts(run, trial):
    folder = run / "trials" / trial["trial_id"] / "policy"
    put(folder / "task-state.json", {"command": "Original instruction plus unparsed obligations",
        "task_evidence": {"requirements": [{"status": "unknown", "text": "unparsed"}]}, "observer_errors": []})
    put(folder / "agent-result.json", {"output": "Claimed success [TASK DONE]"})


def worker_record(manifest, *, returned=True, stop=None):
    return {"profile_sha256": manifest["profile_sha256"], "network_attempts": [],
        "agent": {"success": returned, "archive_verify": {"complete": True}}, "limits": {"stop_reason": stop}}


def test_result_separates_normal_return_public_claim_and_failed_checker(prepared):
    run, manifest = prepared["run"], prepared["manifest"]
    trial = manifest["trials"][0]
    public_artifacts(run, trial)
    result = coordinator.result_layers(run, trial, worker_record(manifest), {"checker": {"passed": False}})
    assert result["run"]["status"] == "returned"
    assert result["run"]["agent_returned_normally"] is True
    assert result["public_evidence"]["whole_task"] == "not_evaluated"
    assert result["public_evidence"]["requirements"][0]["status"] == "unknown"
    assert result["public_evidence"]["agent_claim"] == "Claimed success [TASK DONE]"
    assert result["independent_evaluation"]["passed"] is False
    missing = coordinator.result_layers(run, trial)
    assert missing["independent_evaluation"] == {"status": "not_scored", "passed": None}


@pytest.mark.parametrize("stop,expected", [("cancelled", "cancelled"), ("task_deadline", "timed_out"),
    ("request_timeout", "timed_out"), ("http_attempt_limit", "stopped"), (None, "error")])
def test_execution_stop_status_remains_distinct_from_evaluation(prepared, stop, expected):
    run, manifest = prepared["run"], prepared["manifest"]
    trial = manifest["trials"][0]
    public_artifacts(run, trial)
    result = coordinator.result_layers(run, trial, worker_record(manifest, returned=False, stop=stop))
    assert result["run"]["status"] == expected
    assert result["independent_evaluation"]["passed"] is None


def process(trial, mode, *, killed=False):
    return {"trial_id": trial["trial_id"], "mode": mode, "pid": 1 if mode == "worker" else 2,
        "returncode": -9 if killed else 0, "exited": True, "kill_reason": "watchdog" if killed else None}


def test_multiple_trials_are_completed_and_sealed_once_without_automatic_rerun(prepared, monkeypatch):
    run, manifest = prepared["run"], prepared["manifest"]
    monkeypatch.setattr(coordinator, "ROOT", run / "source")
    calls = []
    def launch(root, mode, trial):
        calls.append((trial["trial_id"], mode))
        public_artifacts(root, trial)
        return process(trial, mode)
    monkeypatch.setattr(coordinator, "launch", launch)
    monkeypatch.setattr(coordinator, "validate_worker", lambda *a, **kw: worker_record(manifest))
    monkeypatch.setattr(coordinator, "validate_score", lambda *a: {"network_attempts": [], "checker": {"passed": False}})
    summary = coordinator.run_prepared(run)
    assert summary["worker_count"] == summary["scorer_count"] == 2
    assert calls == [("task-1", "worker"), ("task-1", "score"), ("task-2", "worker"), ("task-2", "score")]
    assert len(read(run / "results.json")) == 2
    assert coordinator.verify(run)["valid"]
    with pytest.raises(FileExistsError):
        coordinator.run_prepared(run)
    assert len(calls) == 4


def test_killed_worker_stops_batch_without_scoring_retry_or_next_trial(prepared, monkeypatch):
    run = prepared["run"]
    monkeypatch.setattr(coordinator, "ROOT", run / "source")
    calls = []
    def launch(root, mode, trial):
        calls.append((trial["trial_id"], mode))
        return process(trial, mode, killed=True)
    monkeypatch.setattr(coordinator, "launch", launch)
    with pytest.raises(RuntimeError, match="Incomplete worker"):
        coordinator.run_prepared(run)
    assert calls == [("task-1", "worker")]
    assert not (run / "campaign-seal.json").exists()
    assert not read(run / "incomplete.json")["automatic_retry"]
    assert read(run / "results.json")[0]["result"]["independent_evaluation"]["passed"] is None
