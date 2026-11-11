"""Independent v2 boundary/coordination review; no model, world or live API.

Actual Agent + SDK + ASGI cases have a separate sealed evidence denominator.
These tests exercise the public preparation/continuation boundary with inert
snapshots and joined child reports; they do not manufacture simulator success.
"""
import copy
import json
from pathlib import Path
import pickle
import socket
import subprocess
import sys

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
sys.path.insert(0, str(ROOT / "scripts"))
import general_agent as coordinator
import general_agent_local as local
import audit_agent_workflow as auditor
from agent_profile import AgentProfile


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value) + "\n").encode())
    return path


def read(path):
    return json.loads(path.read_bytes())


def profile_value(scheduler="applicability"):
    value = read(ROOT / "experiments/general-agent-v1/profile.json")
    value.update(schema_version=2, profile_id="general-agent-v2-local")
    value["agent"]["execution_contract"] = "task_execution_state_v1"
    value["harness"]["scheduler"] = scheduler
    return value


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Boundary review forbids sockets, subprocesses and pickle loads")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)


@pytest.fixture
def prepared(tmp_path):
    workspace, study = tmp_path / "workspace", tmp_path / "study"
    for component in ("agent4drone", "server"):
        put(workspace / component / "inert.py", b"# not executable evidence\n")
    for name in coordinator.SCRIPTS:
        put(workspace / "scripts" / name, b"# inert frozen script\n")
    put(workspace / coordinator.PROTOCOL, b"v1 protocol\n")
    put(workspace / coordinator.EXECUTION_PROTOCOL, b"v2 execution contract\n")
    timeline = b"INERT INITIAL TIMELINE"
    put(study / "fixtures/timeline.pkl", timeline)
    files, cases = {"fixtures/timeline.pkl": coordinator.sha(timeline)}, []
    for number in (1, 2):
        ident, command = f"task-{number}", f"Complete public instruction {number} with unparsed clause"
        blob = f"INERT SNAPSHOT {number}".encode()
        fixture = {"task_id": ident, "scene_id": "scene", "command": command,
            "command_sha256": coordinator.sha(command.encode()), "timeline_file": "timeline.pkl",
            "timeline_sha256": coordinator.sha(timeline), "snapshot_sha256": coordinator.sha(blob)}
        for name, content in ((ident + ".json", fixture), (ident + ".pkl", blob)):
            file = put(study / "fixtures" / name, content)
            files["fixtures/" + name] = coordinator.sha(file.read_bytes())
        put(tmp_path / "cases" / (ident + ".json"), [{"final": "Public scripted reply"}])
        cases.append({"trial_id": ident, "task_id": ident, "trace_file": ident + ".json"})
    put(study / "manifest.json", {"files": files})
    cases_path = put(tmp_path / "cases/cases.json", cases)
    profile_path = put(tmp_path / "profile.json", profile_value())
    run = tmp_path / "run"
    manifest = coordinator.prepare(profile_path, study, cases_path, run, workspace=workspace)
    return {"run": run, "manifest": manifest, "workspace": workspace, "study": study,
            "profile": profile_path, "cases": cases_path}


@pytest.mark.parametrize("scheduler", ["applicability", "every_step", "failure", "fixed"])
def test_v2_scheduler_is_an_explicit_fingerprinted_profile(scheduler):
    profile = AgentProfile.from_dict(profile_value(scheduler))
    assert profile.execution_contract == "task_execution_state_v1"
    assert profile.environment()["UAV_TASK_EXECUTION_STATE"] == "1"
    assert profile.environment()["UAV_HARNESS_SCHEDULER"] == scheduler
    assert len({AgentProfile.from_dict(profile_value(s)).fingerprint for s in
                ("applicability", "every_step", "failure", "fixed")}) == 4
    original = AgentProfile.load(ROOT / "experiments/general-agent-v1/profile.json")
    assert original.execution_contract is None
    assert original.environment()["UAV_TASK_EXECUTION_STATE"] == "0"


@pytest.mark.parametrize("change", [
    lambda p: p.update(schema_version=True),
    lambda p: p.update(profile_id="general-agent-v1-local"),
    lambda p: p["agent"].pop("execution_contract"),
    lambda p: p["agent"].update(execution_contract="implicit"),
    lambda p: p["harness"].update(scheduler="invented"),
    lambda p: p.update(online_authorized_executions=1),
])
def test_version_contract_and_authorization_cannot_be_mixed(change):
    value = profile_value()
    change(value)
    with pytest.raises(ValueError):
        AgentProfile.from_dict(value)


def test_v2_prepare_seals_new_protocol_without_changing_old_profile(prepared):
    run = prepared["run"]
    manifest = coordinator.load_manifest(run)
    assert manifest["scope"] == "general_agent_v2_execution_contract"
    assert (run / "PROTOCOL_CN.md").read_bytes() == b"v2 execution contract\n"
    assert manifest["agent_profile"]["schema_version"] == 2
    assert manifest["online_authorized_executions"] == 0
    assert read(ROOT / "experiments/general-agent-v1/profile.json")["schema_version"] == 1
    put(prepared["profile"], profile_value("every_step"))
    second = run.parent / "other-profile"
    second_manifest = coordinator.prepare(prepared["profile"], prepared["study"], prepared["cases"],
                                         second, workspace=prepared["workspace"])
    assert second_manifest["profile_sha256"] != manifest["profile_sha256"]
    assert coordinator.load_manifest(run) == manifest


@pytest.mark.parametrize("change", [
    lambda m: m.update(scope=coordinator.SCOPE),
    lambda m: m["trials"][0].update(scheduler="every_step"),
    lambda m: m["trials"][0]["runtime"]["request_limits"].update(max_total_tokens=52),
    lambda m: m["agent_profile"].update(schema_version=1),
])
def test_resealed_manifest_cannot_override_profile_per_slot(prepared, change):
    run = prepared["run"]
    manifest = read(run / "manifest.json")
    change(manifest)
    put(run / "manifest.json", manifest)
    put(run / "manifest.sha256", (coordinator.sha((run / "manifest.json").read_bytes()) + "\n").encode())
    with pytest.raises(ValueError):
        coordinator.load_manifest(run)


def test_postcommit_receipt_loss_probe_requires_explicit_v2_contract(prepared):
    cases = read(prepared["cases"])
    cases[0]["api_fault"] = {"kind": "response_lost", "command": "take_off", "occurrence": 1}
    with pytest.raises(ValueError):
        coordinator.validate_cases(cases)
    assert coordinator.validate_cases(cases, execution_contract=True) == cases
    put(prepared["cases"], cases)
    manifest = coordinator.prepare(prepared["profile"], prepared["study"], prepared["cases"],
                                  prepared["run"].parent / "fault", workspace=prepared["workspace"])
    assert manifest["trials"][0]["api_fault"] == cases[0]["api_fault"]
    for field, value in (("occurrence", True), ("command", "add_obstacle"), ("kind", "pretend_success")):
        invalid = copy.deepcopy(cases)
        invalid[0]["api_fault"][field] = value
        with pytest.raises(ValueError):
            coordinator.validate_cases(invalid, execution_contract=True)


def test_plan_binding_requires_real_public_control_call_and_uses_latest_receipt():
    messages = [{"role": "assistant", "tool_calls": [
        {"id": "control-1", "function": {"name": "run_harness"}},
        {"id": "read-1", "function": {"name": "get_drone_status"}}]},
        {"role": "tool", "tool_call_id": "control-1", "content": json.dumps({"plan_id": "plan-one"})},
        {"role": "user", "content": json.dumps({"plan_id": "private-user-id"})},
        {"role": "tool", "tool_call_id": "read-1", "content": json.dumps({"plan_id": "unrelated-tool-id"})}]
    assert local.resolve_public({"plan_id": "$plan:latest"}, messages) == {"plan_id": "plan-one"}
    messages += [{"role": "assistant", "tool_calls": [{"id": "control-2", "function": {"name": "run_harness"}}]},
                 {"role": "tool", "tool_call_id": "control-2", "content": json.dumps({"plan_id": "plan-two"})}]
    assert local.resolve_public("$plan:latest", messages) == "plan-two"
    with pytest.raises(ValueError, match="No plan_id"):
        local.resolve_public("$plan:latest", messages[1:4])


@pytest.mark.parametrize("secret", ["PRIVATE-CANARY", "Local controlled response loss", "synthetic_fault", "after_public_api_return"])
def test_independent_policy_audit_rejects_canary_and_fault_metadata(secret):
    wire = json.dumps({"messages": [{"role": "tool", "content": json.dumps({"error": secret})}]}).encode()
    with pytest.raises(ValueError):
        auditor.no_private_input(wire, "PRIVATE-CANARY")
    auditor.no_private_input(b'{"messages":[{"role":"tool","content":"Request timed out before response was received"}]}', "PRIVATE-CANARY")


def test_independent_audit_rejects_changed_bytes_and_duplicate_json_fields(tmp_path):
    path = put(tmp_path / "scorer.json", {"passed": True})
    digest = auditor.sha(path.read_bytes())
    auditor.hashes(tmp_path, {"scorer.json": digest})
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Hash mismatch"):
        auditor.hashes(tmp_path, {"scorer.json": digest})
    with pytest.raises(ValueError, match="Duplicate JSON"):
        auditor.parse(b'{"passed":true,"passed":false}')


def test_audit_output_cannot_be_written_into_original_run(prepared, monkeypatch):
    output = prepared["run"] / "unsealed-audit.json"
    before = (prepared["run"] / "manifest.json").read_bytes()
    monkeypatch.setattr(sys, "argv", ["audit_agent_workflow.py", "--run", str(prepared["run"]), "--output", str(output)])
    with pytest.raises(SystemExit) as caught:
        auditor.main()
    assert caught.value.code == 2 and not output.exists()
    assert (prepared["run"] / "manifest.json").read_bytes() == before


def worker_record(manifest, trial, *, stop=None, unknown=False):
    usage = {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}
    value = {"status": "completed", "trial_id": trial["trial_id"], "profile_sha256": manifest["profile_sha256"],
        "network_attempts": [], "drained": True,
        "limits": {"closed": True, "needs_process_termination": False, "threads_still_alive": [],
            "stop_reason": stop, "admitted_http_attempts": 1, "delegated_http_attempts": 1,
            "known_total_tokens": 0 if unknown else 26, "unknown_usage_attempts": int(unknown),
            "unknown_usage_attempt_ids": [1] if unknown else [],
            "attempts": [{"attempt_id": 1, "delegated": True, "usage": None if unknown else usage,
                          "usage_status": "unknown" if unknown else "known"}]},
        "isolation": {key: True for key in ("archive_unchanged_after_drain", "world_unchanged_after_drain",
            "late_action_rejected", "canary_absent", "checker_definitions_absent")},
        "agent": {"success": stop is None, "error_type": None, "harness_closed": True,
            "archive_info": {"closed": True, "complete": stop is None, "path": "/sealed/model.jsonl"},
            "archive_verify": {"valid": True, "complete": stop is None}},
        "snapshot": {"path": "private/score-world.pkl", "sha256": "a" * 64,
                     "state_sha256": "b" * 64, "score_seconds": 1.0},
        "files": {"private/score-world.pkl": "a" * 64, "policy/modelio/model.jsonl": "c" * 64},
        "lifecycle": {"cancelled": stop == "cancelled"}, "runtime": manifest["agent_profile"]["runtime"],
        "elapsed": 1.0, "clock": "time.monotonic", "timer_enabled": True}
    return value


def install_children(prepared, monkeypatch, *, stop=None, unknown=False, killed=False, corrupt=False):
    run, manifest = prepared["run"], prepared["manifest"]
    monkeypatch.setattr(coordinator, "ROOT", run / "source")
    calls, reports = [], []
    def launch(root, mode, trial, *, on_start=None):
        calls.append((trial["trial_id"], mode))
        pid, start = len(calls) + 100, len(calls) * 10
        if on_start:
            on_start({"event": "start", "mode": mode, "trial_id": trial["trial_id"], "pid": pid, "started_ns": start})
        folder = root / "trials" / trial["trial_id"] / "policy"
        put(folder / "task-state.json", {"command": "Original complete command", "task_evidence": {}})
        put(folder / "agent-result.json", {"output": "Claim only"})
        return {"trial_id": trial["trial_id"], "mode": mode, "pid": pid, "started_ns": start,
            "ended_ns": start + 1, "returncode": -9 if killed else 0, "exited": True,
            "kill_reason": "transport_not_drained" if killed else None}
    def validate(root, trial, process, **kwargs):
        if corrupt:
            raise ValueError("Exit seal hash mismatch")
        record = worker_record(manifest, trial, stop=stop if trial == manifest["trials"][0] else None,
                               unknown=unknown and trial == manifest["trials"][0])
        reports.append(record)
        return record
    monkeypatch.setattr(coordinator, "launch", launch)
    monkeypatch.setattr(coordinator, "validate_worker", validate)
    monkeypatch.setattr(coordinator, "validate_score", lambda *a: {"network_attempts": [], "checker": {"passed": False}})
    return calls, reports


@pytest.mark.parametrize("stop", [None, "token_limit", "http_attempt_limit"])
def test_known_episode_end_continues_only_remaining_prepared_slot(prepared, monkeypatch, stop):
    calls, reports = install_children(prepared, monkeypatch, stop=stop)
    summary = coordinator.run_prepared(prepared["run"])
    assert calls == [("task-1", "worker"), ("task-1", "score"), ("task-2", "worker"), ("task-2", "score")]
    assert summary["status"] == "completed" and summary["planned_trials"] == 2
    assert summary["unstarted_trials"] == [] and summary["worker_count"] == summary["scorer_count"] == 2
    rows = read(prepared["run"] / "results.json")
    assert rows[0]["termination"]["batch_action"] == "continue"
    assert rows[0]["termination"]["archive_training_eligible"] is (stop is None)
    assert rows[0]["result"]["independent_evaluation"]["passed"] is False
    assert all("termination_context" not in record for record in reports)
    assert coordinator.verify(prepared["run"])["valid"]
    journal = [json.loads(line) for line in (prepared["run"] / "process-ledger.jsonl").read_text().splitlines()]
    assert [row["event"] for row in journal] == ["start", "exit"] * 4
    with pytest.raises(FileExistsError):
        coordinator.run_prepared(prepared["run"])
    assert len(calls) == 4


@pytest.mark.parametrize("stop,unknown", [("unknown_usage", True), ("cancelled", False), ("request_timeout", False)])
def test_uncertainty_or_cancel_stops_batch_after_sealed_independent_scoring(prepared, monkeypatch, stop, unknown):
    calls, _ = install_children(prepared, monkeypatch, stop=stop, unknown=unknown)
    summary = coordinator.run_prepared(prepared["run"])
    assert calls == [("task-1", "worker"), ("task-1", "score")]
    assert summary["status"] == "stopped" and summary["planned_trials"] == 2
    assert summary["worker_count"] == summary["scorer_count"] == 1
    assert summary["unstarted_trials"] == ["task-2"]
    rows = read(prepared["run"] / "results.json")
    assert len(rows) == 1 and rows[0]["termination"]["batch_action"] == "stop"
    assert not rows[0]["termination"]["archive_training_eligible"]
    assert coordinator.verify(prepared["run"])["valid"]


def test_undrained_killed_child_is_joined_unscored_and_stops_remaining_slots(prepared, monkeypatch):
    calls, _ = install_children(prepared, monkeypatch, killed=True)
    summary = coordinator.run_prepared(prepared["run"])
    assert calls == [("task-1", "worker")]
    assert summary["status"] == "stopped" and summary["scorer_count"] == 0
    assert summary["unstarted_trials"] == ["task-2"]
    row = read(prepared["run"] / "results.json")[0]
    assert row["worker"] is row["score"] is None
    assert row["result"]["independent_evaluation"]["passed"] is None
    assert not row["termination"]["score_eligible"]
    assert coordinator.verify(prepared["run"])["valid"]


def test_failed_worker_seal_never_reaches_classifier_score_or_next_slot(prepared, monkeypatch):
    calls, _ = install_children(prepared, monkeypatch, corrupt=True)
    seen = []
    monkeypatch.setattr(coordinator, "termination_decision", lambda worker: seen.append(worker))
    with pytest.raises(ValueError, match="seal hash"):
        coordinator.run_prepared(prepared["run"])
    assert calls == [("task-1", "worker")] and seen == []
    summary = read(prepared["run"] / "summary.json")
    assert summary["status"] == "stopped" and summary["unstarted_trials"] == ["task-2"]
    assert summary["scorer_count"] == 0
