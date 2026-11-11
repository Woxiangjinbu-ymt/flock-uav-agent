"""Parent coordinator contracts only: synthetic files and joined fake children.

These tests never start an Agent, model provider, FullWorld, scorer, or campaign.
The fixed nine-worker acceptance is a separate denominator and execution.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_harness_episode as episode


CANARY = "PRIVATE_SCORE_CANARY_PARENT_TEST_ONLY"


@pytest.fixture(autouse=True)
def no_network_or_real_children(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Parent unit tests forbid real network or child processes")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(episode.subprocess, "Popen", forbidden)
    monkeypatch.setattr(episode.subprocess, "run", forbidden)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")
    return path


def body(value):
    return {"base64": base64.b64encode(value).decode(), "sha256": hashlib.sha256(value).hexdigest(),
            "length": len(value)}


def test_safe_path_accepts_only_ordinary_descendants(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    target = root / "trials" / "reference-B" / "policy" / "model.jsonl"
    assert episode.safe_path(root, "trials/reference-B/policy/model.jsonl") == target
    target.parent.mkdir(parents=True)
    target.write_text("public fixture")
    assert episode.safe_path(root, "trials/reference-B/policy/model.jsonl").read_text() == "public fixture"


@pytest.mark.parametrize("relative", ["../outside.json", "inside/../../outside.json", "/tmp/absolute-artifact.json"])
def test_safe_path_rejects_escape_before_read(tmp_path, relative):
    root = tmp_path / "run"
    root.mkdir()
    with pytest.raises((ValueError, RuntimeError)):
        episode.safe_path(root, relative)


@pytest.mark.parametrize("kind", ["leaf", "directory", "internal_leaf"])
def test_safe_path_rejects_symbolic_links(tmp_path, kind):
    root = tmp_path / "run"
    root.mkdir()
    secret = tmp_path / "private.json"
    secret.write_text(CANARY)
    if kind == "leaf":
        (root / "alias.json").symlink_to(secret)
        relative = "alias.json"
    elif kind == "directory":
        (root / "alias").symlink_to(tmp_path, target_is_directory=True)
        relative = "alias/private.json"
    else:
        public = root / "public.json"
        public.write_text("public")
        (root / "alias.json").symlink_to(public)
        relative = "alias.json"
    with pytest.raises((ValueError, RuntimeError)):
        episode.safe_path(root, relative)
    assert secret.read_text() == CANARY


def test_policy_scan_allows_public_progress_and_tool_identity(tmp_path):
    path = write_json(tmp_path / "public.json", {"progress_percentage": 95, "status": "success",
        "tool_call_id": "observed-public-call", "message": "公开任务状态与动作回执"})
    result = episode.policy_scan([path], CANARY)
    assert result["canary_absent"] and result["checker_definitions_absent"]


@pytest.mark.parametrize("encoding", ["plain", "request_base64", "response_split"])
def test_policy_scan_checks_visible_decoded_bytes_not_just_json_text(tmp_path, encoding):
    if encoding == "plain":
        records = [{"content": CANARY}]
    elif encoding == "request_base64":
        records = [{"kind": "http_request", "data": {"attempt_id": "attempt-a",
                    "body": body(json.dumps({"messages": [{"role": "user", "content": CANARY}]}).encode())}}]
    else:
        cut = len(CANARY) // 2
        records = [{"kind": "http_response_chunk", "archive_id": "archive-a",
                    "data": {"attempt_id": "attempt-a", "chunk_index": index + 1,
                             "body": body(part.encode())}}
                   for index, part in enumerate((CANARY[:cut], CANARY[cut:]))]
    path = tmp_path / "policy.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    before = path.read_bytes()
    assert episode.policy_scan([path], CANARY)["canary_absent"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["execution_check_apis", "related_apis"])
def test_policy_scan_rejects_hidden_checker_definitions_in_encoded_body(tmp_path, field):
    payload = {"messages": [{"role": "tool", "content": json.dumps({field: {"private": "definition"}})}]}
    path = write_json(tmp_path / "request.json", {"body": body(json.dumps(payload).encode())})
    assert episode.policy_scan([path], CANARY)["checker_definitions_absent"] is False


@pytest.mark.parametrize("damage", ["hash", "missing_request_body", "missing_response_body"])
def test_policy_scan_does_not_certify_missing_or_invalid_body(tmp_path, damage):
    record = {"archive_id": "a", "kind": "http_request", "data": {"attempt_id": "p", "body": body(b"public")}}
    if damage == "hash":
        record["data"]["body"]["sha256"] = "0" * 64
    else:
        record["data"]["body"] = None
        if damage == "missing_response_body":
            record["kind"] = "http_response_chunk"
    path = tmp_path / "missing.jsonl"
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises((ValueError, TypeError, KeyError)):
        episode.policy_scan([path], CANARY)


def workspace_fixture(tmp_path):
    """Freeze metadata only: these inert pickle bytes are never deserialized."""
    workspace = tmp_path / "workspace"
    study = workspace / "experiments/minimal-harness-full100-v1"
    fixture = {"timeline_file": "timeline.json", "command": "Synthetic public command", "source": "original-task.json"}
    files = {"fixtures/b1eb0270.pkl": b"SYNTHETIC-NOT-A-PICKLE", "fixtures/timeline.json": b"[]\n"}
    fixture["snapshot_sha256"] = episode.sha(files["fixtures/b1eb0270.pkl"])
    fixture["timeline_sha256"] = episode.sha(files["fixtures/timeline.json"])
    fixture["command_sha256"] = episode.sha(fixture["command"].encode())
    original = write_json(workspace / fixture["source"], {"public": "original fixture"})
    fixture["source_sha256"] = episode.sha(original.read_bytes())
    files["fixtures/b1eb0270.json"] = (json.dumps(fixture) + "\n").encode()
    for name, content in files.items():
        path = study / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    write_json(study / "manifest.json", {"files": {name: episode.sha(content) for name, content in files.items()}})
    protocol = workspace / "docs/results/harness-full-episode-20261025/PROTOCOL_CN.md"
    protocol.parent.mkdir(parents=True)
    protocol.write_text("Synthetic unit fixture; never a campaign.\n")
    for name in ("validate_harness_episode.py", "harness_episode_local.py", "harness_full100_world.py",
                 "harness_full100_online.py", "harness_v2_world.py", "harness_v2_online.py"):
        path = workspace / "scripts" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("# frozen synthetic source; not executable\n")
    for directory in ("agent4drone", "server"):
        path = workspace / directory / "fixture.py"
        path.parent.mkdir()
        path.write_text("# synthetic source\n")
    return workspace


@pytest.fixture
def prepared(tmp_path):
    workspace = workspace_fixture(tmp_path)
    run = tmp_path / "run"
    manifest = episode.prepare(workspace, run)
    return run, manifest


def process(trial, *, mode="worker", pid=101):
    return {"trial_id": trial["trial_id"], "mode": mode, "pid": pid, "returncode": 0,
            "started_ns": 10, "ended_ns": 20, "exited": True, "watchdog_timeout": False}


def worker_fixture(run, trial, *, pid=101):
    folder = run / "trials" / trial["trial_id"]
    write_json(folder / "policy/public.json", {"public": "observed"})
    (folder / "private").mkdir()
    (folder / "private/score-world.pkl").write_bytes(b"INERT-SCORING-SNAPSHOT-NOT-A-PICKLE")
    (folder / "private/worker-console.log").write_text("worker private diagnostic\n")
    data = {"status": "completed", **trial, "pid": pid, "budget": copy.deepcopy(episode.BUDGET),
            "agent": {"success": trial["condition"] == "success", "harness_closed": True,
                      "archive_info": {"closed": True, "complete": trial["condition"] == "success"},
                      "archive_verify": {"valid": True, "complete": trial["condition"] == "success"}},
            "network_attempts": [], "isolation": {"late_world_rejected": True, "late_archive_unchanged": True,
                "private_endpoint_denied": True, "policy_canary_absent": True, "policy_checker_definition_absent": True,
                "canary_violations": [], "checker_definition_violations": [], "request_body_sha256": ["synthetic"]},
            "snapshot": {"path": "private/score-world.pkl", "sha256": episode.sha((folder / "private/score-world.pkl").read_bytes())}}
    data["files"] = {str(p.relative_to(folder)): episode.sha(p.read_bytes()) for p in folder.rglob("*")
                     if p.is_file() and p.name != "worker-console.log"}
    write_json(folder / "worker-result.json", data)
    return data


def score_fixture(run, trial, worker, *, pid=201):
    folder = run / "trials" / trial["trial_id"]
    data = {"trial_id": trial["trial_id"], "pid": pid, "worker_pid": worker["pid"],
            "worker_result_sha256": episode.sha((folder / "worker-result.json").read_bytes()),
            "snapshot_sha256": worker["snapshot"]["sha256"], "private_canary_present": True,
            "checker_tree_matches_original": True, "agent_imported": False, "network_attempts": [],
            "checker": {"passed": trial["condition"] == "success", "errors": []}}
    write_json(folder / "evaluation/result.json", data)
    return data


def test_prepare_freezes_declared_nine_trial_protocol(prepared):
    run, manifest = prepared
    assert episode.load_manifest(run) == manifest
    assert [trial["trial_id"] for trial in manifest["trials"]] == ["reference-B", "success-A", "success-B",
        "failure-A", "failure-B", "timeout-A", "timeout-B", "cancel-A", "cancel-B"]
    assert all(trial["condition"] == "success" for trial in manifest["trials"] if trial["role"] in {"B", "reference"})
    assert manifest["fixture_id"] == "b1eb0270" and manifest["budget"] == episode.BUDGET
    assert manifest["budget"]["deadline_seconds"] == 500
    assert not (run / "trials").exists()


def test_prepare_refuses_existing_attempt_without_mutation(prepared):
    run, manifest = prepared
    before = {str(p.relative_to(run)): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="overwrit"):
        episode.prepare(Path(manifest["workspace"]), run)
    assert {str(p.relative_to(run)): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("target", ["manifest", "protocol", "source", "input"])
def test_load_manifest_rejects_frozen_input_tamper(prepared, target):
    run, manifest = prepared
    path = {"manifest": run / "manifest.json", "protocol": run / "PROTOCOL_CN.md",
            "source": run / "source" / next(iter(manifest["sources"])),
            "input": Path(manifest["study"]) / next(iter(manifest["inputs"]))}[target]
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError):
        episode.load_manifest(run)


@pytest.fixture
def skip_wire_only(monkeypatch):
    # Worker metadata unit cases do not invent an entire model trace. The actual
    # independent byte/oracle validator has separate direct tests below.
    monkeypatch.setattr(episode, "validate_wire", lambda folder, worker: None)
    monkeypatch.setattr(episode, "validate_boundary", lambda folder, worker: None)


@pytest.mark.parametrize("condition", ["success", "failure", "timeout", "cancel"])
def test_completed_process_can_score_four_command_outcomes(prepared, skip_wire_only, condition):
    run, _ = prepared
    trial = next(item for item in episode.TRIALS if item["trial_id"] == condition + "-A")
    worker = worker_fixture(run, trial)
    validated = episode.validate_worker(run, trial, process(trial))
    assert validated["agent"]["success"] is (condition == "success")
    score_fixture(run, trial, worker)
    scored = episode.validate_score(run, trial, worker, process(trial, mode="score", pid=201))
    assert scored["checker"]["passed"] is (condition == "success")


@pytest.mark.parametrize("boundary", ["not_exited", "returncode", "watchdog"])
def test_worker_never_scores_before_clean_process_exit(prepared, skip_wire_only, boundary):
    run, _ = prepared
    trial = episode.TRIALS[0]
    worker_fixture(run, trial)
    child = process(trial)
    child.update({"not_exited": {"exited": False}, "returncode": {"returncode": 1},
                  "watchdog": {"watchdog_timeout": True}}[boundary])
    with pytest.raises(ValueError, match="exit"):
        episode.validate_worker(run, trial, child)
    assert not (run / "worker-exit-seals").exists()


@pytest.mark.parametrize("boundary", ["harness_open", "archive_open", "invalid_archive", "complete_mismatch",
                                      "snapshot_tamper", "unlisted_file", "policy_leak", "wrong_trial", "network"])
def test_worker_rejects_invalid_or_misattributed_artifacts(prepared, skip_wire_only, boundary):
    run, manifest = prepared
    trial = episode.TRIALS[0]
    data = worker_fixture(run, trial)
    folder = run / "trials" / trial["trial_id"]
    if boundary == "harness_open": data["agent"]["harness_closed"] = False
    elif boundary == "archive_open": data["agent"]["archive_info"]["closed"] = False
    elif boundary == "invalid_archive": data["agent"]["archive_verify"]["valid"] = False
    elif boundary == "complete_mismatch": data["agent"]["archive_info"]["complete"] = False
    elif boundary == "snapshot_tamper": (folder / "private/score-world.pkl").write_bytes(b"tampered")
    elif boundary == "unlisted_file": (folder / "policy/unlisted.json").write_text("{}")
    elif boundary == "policy_leak":
        path = write_json(folder / "policy/public.json", {"content": manifest["private_canary"]})
        data["files"]["policy/public.json"] = episode.sha(path.read_bytes())
    elif boundary == "wrong_trial": data["trial_id"] = "other-episode"
    elif boundary == "network": data["network_attempts"] = ["blocked socket"]
    write_json(folder / "worker-result.json", data)
    with pytest.raises(ValueError):
        episode.validate_worker(run, trial, process(trial))
    assert not (run / "worker-exit-seals").exists()


@pytest.mark.parametrize("boundary", ["worker_hash", "snapshot_hash", "private_control", "tree_control", "agent_imported",
                                      "worker_console_tamper", "checker_error", "scorer_pid"])
def test_score_requires_bound_snapshot_and_private_controls(prepared, skip_wire_only, boundary):
    run, _ = prepared
    trial = episode.TRIALS[0]
    worker = worker_fixture(run, trial)
    episode.validate_worker(run, trial, process(trial))
    data = score_fixture(run, trial, worker)
    folder = run / "trials" / trial["trial_id"]
    if boundary == "worker_hash": data["worker_result_sha256"] = "0" * 64
    elif boundary == "snapshot_hash": data["snapshot_sha256"] = "0" * 64
    elif boundary == "private_control": data["private_canary_present"] = False
    elif boundary == "tree_control": data["checker_tree_matches_original"] = False
    elif boundary == "agent_imported": data["agent_imported"] = True
    elif boundary == "worker_console_tamper": (folder / "private/worker-console.log").write_text("late write")
    elif boundary == "checker_error": data["checker"]["errors"] = ["checker did not execute"]
    elif boundary == "scorer_pid": data["pid"] = 999
    write_json(folder / "evaluation/result.json", data)
    with pytest.raises(ValueError):
        episode.validate_score(run, trial, worker, process(trial, mode="score", pid=201))
    assert not (run / "scorer-exit-seals").exists()


def wire_fixture(tmp_path):
    folder = tmp_path / "wire"
    request, response = b'{"messages":[{"content":"public"}]}', b'{"answer":"public"}'
    records = [
        {"kind": "http_request", "data": {"attempt_id": "p1", "body": body(request)}},
        {"kind": "http_response_start", "data": {"attempt_id": "p1", "status_code": 200}},
        {"kind": "http_response_chunk", "data": {"attempt_id": "p1", "body": body(response[:8])}},
        {"kind": "http_response_chunk", "data": {"attempt_id": "p1", "body": body(response[8:])}},
        {"kind": "http_response_end", "data": {"attempt_id": "p1", "complete": True}},
    ]
    archive = folder / "policy/model.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text("".join(json.dumps(row) + "\n" for row in records))
    oracle = {"requests": [{"length": len(request), "sha256": episode.sha(request)}],
              "responses": [{"length": len(response), "sha256": episode.sha(response), "status_code": 200}]}
    write_json(folder / "policy/transport-oracle.json", oracle)
    return folder, {"agent": {"archive_info": {"path": str(archive)}}}, records, oracle


@pytest.mark.parametrize("damage", [None, "missing_request", "missing_chunk", "response_mismatch", "no_end", "partial"])
def test_wire_validator_requires_transport_oracle_and_all_bytes(tmp_path, damage):
    folder, worker, records, oracle = wire_fixture(tmp_path)
    if damage == "missing_request": records[0]["data"]["body"] = None
    elif damage == "missing_chunk": records.pop(2)
    elif damage == "response_mismatch": oracle["responses"][0]["sha256"] = "0" * 64
    elif damage == "no_end": records.pop()
    elif damage == "partial": records[-1]["data"]["complete"] = False
    Path(worker["agent"]["archive_info"]["path"]).write_text("".join(json.dumps(row) + "\n" for row in records))
    write_json(folder / "policy/transport-oracle.json", oracle)
    if damage is None:
        episode.validate_wire(folder, worker)
    else:
        with pytest.raises(ValueError):
            episode.validate_wire(folder, worker)


def boundary_fixture(tmp_path, condition):
    folder = tmp_path / "boundary"
    actions = [{"phase": "task", "admin": False, "action": True, "committed": True,
                "error": None, "response": {"status": "success"}, "origin": {"tool": tool},
                "history_events": ["physical-fixture"], "request_id": index + 1}
               for index, tool in enumerate(["take_off", "rotate", "hover"] if condition == "success" else ["take_off"])]
    path = folder / "policy/worldlogs/public-api.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in actions))
    codes = [200, 200, 500] if condition == "failure" else [200, 200, 200] if condition == "success" else [200, 200]
    write_json(folder / "policy/transport-oracle.json", {"responses": [{"status_code": code} for code in codes]})
    worker = {"condition": condition, "physical_cost": {"vertical_distance": 14},
        "agent": {"archive_verify": {"complete": condition == "success"}},
        "terminal": {"triggered": True, "trigger_request_id": 1, "frame_status": "interrupted",
                     "frame_reason": "deadline" if condition == "timeout" else "cancelled",
                     "virtual_elapsed": 500, "cancel_flag": condition == "cancel"}}
    return folder, worker, actions


@pytest.mark.parametrize("condition", ["success", "failure", "timeout", "cancel"])
def test_boundary_accepts_four_declared_committed_effects(tmp_path, condition):
    folder, worker, _ = boundary_fixture(tmp_path, condition)
    episode.validate_boundary(folder, worker)


@pytest.mark.parametrize("damage", ["trigger", "deadline", "wrong_request", "uncommitted", "no_effect", "late_action", "private_read"])
def test_boundary_rejects_apparent_timeout_without_actual_contract(tmp_path, damage):
    folder, worker, actions = boundary_fixture(tmp_path, "timeout")
    if damage == "trigger": worker["terminal"]["triggered"] = False
    elif damage == "deadline": worker["terminal"]["virtual_elapsed"] = 499
    elif damage == "wrong_request": worker["terminal"]["trigger_request_id"] = 99
    elif damage == "uncommitted": actions[0]["committed"] = False
    elif damage == "no_effect": worker["physical_cost"]["vertical_distance"] = 0
    elif damage == "late_action": actions.append(copy.deepcopy(actions[0]))
    elif damage == "private_read": actions[0]["phase"], actions[0]["admin"] = "evaluation", True
    (folder / "policy/worldlogs/public-api.jsonl").write_text("".join(json.dumps(row) + "\n" for row in actions))
    with pytest.raises(ValueError):
        episode.validate_boundary(folder, worker)


def mock_validators(monkeypatch, events, *, reject=None, private_score=False):
    def worker(run, trial, child):
        events.append(("validated_worker", trial["trial_id"]))
        if reject == "worker": raise ValueError("synthetic invalid worker")
        return {**trial, "pid": child["pid"], "snapshot": {"sha256": "synthetic"}}
    def score(run, trial, worker_result, child):
        events.append(("validated_score", trial["trial_id"]))
        assert worker_result["trial_id"] == trial["trial_id"]
        if reject == "score": raise ValueError("synthetic invalid scorer")
        return {"trial_id": trial["trial_id"], "checker": {"passed": private_score}, "private_value": CANARY}
    def summary(run, manifest, workers, scores, children):
        assert len(workers) == len(scores) == 9
        assert len(children) == 18
        return {"status": "passed", "unit_fixture": True}
    monkeypatch.setattr(episode, "validate_worker", worker)
    monkeypatch.setattr(episode, "validate_score", score)
    monkeypatch.setattr(episode, "summarize", summary)


def fake_launch(events):
    def launch(run, mode, ident):
        events.append((mode, ident))
        order = sum(event[0] in {"worker", "score"} for event in events)
        return {"mode": mode, "trial_id": ident, "pid": 100 + order, "returncode": 0,
                "started_ns": order * 10, "ended_ns": order * 10 + 5, "exited": True}
    return launch


@pytest.mark.parametrize("private_score", [False, True])
def test_fixed_schedule_never_adapts_b_to_private_score(prepared, monkeypatch, private_score):
    run, manifest = prepared
    events = []
    mock_validators(monkeypatch, events, private_score=private_score)
    result = episode.run_prepared(run, launcher=fake_launch(events))
    assert result["status"] == "passed"
    expected = [(mode, trial["trial_id"]) for trial in manifest["trials"] for mode in ("worker", "score")]
    assert [event for event in events if event[0] in {"worker", "score"}] == expected
    assert events[:4] == [("worker", "reference-B"), ("validated_worker", "reference-B"),
                          ("score", "reference-B"), ("validated_score", "reference-B")]
    journal = [json.loads(line) for line in (run / "process-ledger.jsonl").read_text().splitlines()]
    assert [(row["mode"], row["trial_id"]) for row in journal] == expected
    assert CANARY not in json.dumps(journal)
    assert (run / "campaign-seal.json").exists()


@pytest.mark.parametrize("reject", ["worker", "score", "launch"])
def test_invalid_child_stops_without_retry_score_or_next_trial(prepared, monkeypatch, reject):
    run, _ = prepared
    events = []
    mock_validators(monkeypatch, events, reject=reject)
    launch = fake_launch(events)
    def bounded(run, mode, ident):
        if reject == "launch":
            events.append((mode, ident))
            raise episode.subprocess.TimeoutExpired("synthetic child", 1)
        return launch(run, mode, ident)
    with pytest.raises((ValueError, episode.subprocess.TimeoutExpired)):
        episode.run_prepared(run, launcher=bounded)
    invoked = [event for event in events if event[0] in {"worker", "score"}]
    assert invoked == [("worker", "reference-B")] + ([("score", "reference-B")] if reject == "score" else [])
    incomplete = json.loads((run / "incomplete.json").read_text())
    assert incomplete["status"] == "incomplete" and incomplete["automatic_retry"] is False
    assert not (run / "campaign-seal.json").exists()
    before = list(events)
    with pytest.raises(ValueError, match="resumed|retried"):
        episode.run_prepared(run, launcher=bounded)
    assert events == before


def test_parent_does_not_start_score_or_b_while_worker_is_still_joining(prepared, monkeypatch):
    run, _ = prepared
    events = []
    mock_validators(monkeypatch, events)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    launch = fake_launch(events)
    errors = []
    def held(run, mode, ident):
        if mode == "worker" and ident == "reference-B":
            entered.set()
            assert release.wait(5), "Unit-test join was not released"
        return launch(run, mode, ident)
    def coordinate():
        try: episode.run_prepared(run, launcher=held)
        except BaseException as exc: errors.append(exc)
        finally: finished.set()
    thread = threading.Thread(target=coordinate, daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        assert events == [] and not finished.is_set()
        assert not (run / "results.json").exists()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors


@pytest.mark.parametrize("timeout", [False, True])
def test_launcher_waits_and_kills_then_joins_on_watchdog(prepared, monkeypatch, timeout):
    run, _ = prepared
    events = []
    class Child:
        pid = 401
        returncode = None
        def wait(self, timeout):
            events.append(("wait", timeout))
            if len([event for event in events if event[0] == "wait"]) == 1 and timeout_case:
                raise episode.subprocess.TimeoutExpired("synthetic", timeout)
            self.returncode = -9 if timeout_case else 0
            return self.returncode
        def kill(self): events.append(("kill",))
        def poll(self): return self.returncode
    timeout_case = timeout
    def popen(command, **kwargs):
        assert command[-2:] == ["--mode", "worker"]
        assert "--trial" in command and CANARY not in str(command)
        events.append(("spawn",))
        return Child()
    monkeypatch.setattr(episode.subprocess, "Popen", popen)
    result = episode.launch(run, "worker", "reference-B")
    assert result["exited"] and result["watchdog_timeout"] is timeout
    assert [event[0] for event in events] == (["spawn", "wait", "kill", "wait"] if timeout else ["spawn", "wait"])
    before = list(events)
    with pytest.raises(FileExistsError):
        episode.launch(run, "worker", "reference-B")
    assert events == before
