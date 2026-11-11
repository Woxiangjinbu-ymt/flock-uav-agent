"""Pure coordinator and lifecycle tests, with no Agent/world/provider execution.

Every process below is a joined fake; the cancellation test uses only a lock and
an in-memory fake world. FullWorld local acceptance is a separate denominator.
"""
import copy
import json
from pathlib import Path
import pickle
import socket
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_harness_natural_runner as coordinator
import harness_natural_runner as adapter


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else
                     (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
    return path


def read(path):
    return json.loads(path.read_text())


@pytest.fixture(autouse=True)
def no_real_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unit test forbids real sockets, children, and pickle loads")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "source").mkdir()
    study = tmp_path / "synthetic-study"
    study.mkdir()
    put(study / "inert.pkl", b"THIS IS NOT A PICKLE")
    put(root / "PROTOCOL_CN.md", b"Synthetic local-only coordinator protocol\n")
    put(root / "source/inert.py", b"# never executed\n")
    manifest = {"scope": "local_mock_wallclock_only", "online_authorized_executions": 0,
        "trials": coordinator.trials(), "workspace": str(tmp_path), "study": str(study),
        "private_canary": "PRIVATE_SYNTHETIC_COORDINATOR_CANARY",
        "sources": {"inert.py": coordinator.sha((root / "source/inert.py").read_bytes())},
        "inputs": {"inert.pkl": coordinator.sha((study / "inert.pkl").read_bytes())},
        "protocol_sha256": coordinator.sha((root / "PROTOCOL_CN.md").read_bytes())}
    put(root / "manifest.json", manifest)
    put(root / "manifest.sha256", (coordinator.sha((root / "manifest.json").read_bytes()) + "\n").encode())
    return root


def process(trial, mode="worker", **changes):
    result = {"mode": mode, "trial_id": trial["trial_id"], "pid": 101 if mode == "worker" else 102,
              "started_ns": 10 if mode == "worker" else 30, "ended_ns": 20 if mode == "worker" else 40,
              "returncode": 0, "exited": True, "kill_reason": None}
    result.update(changes)
    return result


def minimal_worker():
    return {"pid": 101, "agent": {"archive_verify": {"complete": True}},
            "limits": {"unknown_usage_attempts": []}}


def test_execute_trial_joins_and_validates_worker_before_scoring(run, monkeypatch):
    trial = coordinator.trials()[0]
    events = []
    worker = minimal_worker()
    def launch(root, mode, chosen):
        assert root == run and chosen == trial
        events.append("joined:" + mode)
        return process(chosen, mode)
    def validate_worker(root, chosen, finished):
        assert finished["exited"] and finished["returncode"] == 0
        events.append("validated:worker")
        return worker
    def validate_score(root, chosen, preceding, finished):
        assert preceding is worker and finished["mode"] == "score"
        events.append("validated:score")
        return {"checker": {"passed": True}}
    monkeypatch.setattr(coordinator, "validate_worker", validate_worker)
    monkeypatch.setattr(coordinator, "validate_score", validate_score)
    result = coordinator.execute_trial(run, trial, launch)
    assert events == ["joined:worker", "validated:worker", "joined:score", "validated:score"]
    assert result["status"] == "completed" and result["online_continue_eligible"] is True


@pytest.mark.parametrize("changes", [{"returncode": 1}, {"returncode": -9, "kill_reason": "watchdog"},
                                      {"returncode": -9, "kill_reason": "transport_not_drained"}])
def test_failed_or_killed_worker_is_never_scored(run, monkeypatch, changes):
    calls = []
    def launch(root, mode, trial):
        calls.append(mode)
        return process(trial, mode, **changes)
    monkeypatch.setattr(coordinator, "validate_worker", lambda *args: pytest.fail("Must not validate failed worker"))
    monkeypatch.setattr(coordinator, "validate_score", lambda *args: pytest.fail("Must not score failed worker"))
    result = coordinator.execute_trial(run, coordinator.trials()[0], launch)
    assert calls == ["worker"] and result["status"] == "incomplete"
    assert result["worker"] is result["score"] is None and result["online_continue_eligible"] is False


def test_unjoined_worker_blocks_score_and_next_trial(run, monkeypatch):
    calls = []
    def launch(root, mode, trial):
        calls.append((mode, trial["trial_id"]))
        return process(trial, mode, exited=False)
    with pytest.raises(ValueError, match="joined"):
        coordinator.run_acceptance(run, launch)
    assert calls == [("worker", "skill_success")]
    assert read(run / "incomplete.json")["automatic_retry"] is False
    assert not (run / "results.json").exists()


@pytest.mark.parametrize("error", [ValueError("tampered worker"), FileNotFoundError("missing worker manifest")])
def test_worker_validation_failure_preserves_joined_pid_and_never_scores(run, monkeypatch, error):
    calls = []
    def launch(root, mode, trial):
        calls.append((mode, trial["trial_id"]))
        return process(trial, mode)
    def invalid(*args):
        raise error
    monkeypatch.setattr(coordinator, "validate_worker", invalid)
    with pytest.raises(type(error)):
        coordinator.run_acceptance(run, launch)
    assert calls == [("worker", "skill_success")]
    journal = [json.loads(line) for line in (run / "process-ledger.jsonl").read_text().splitlines()]
    assert len(journal) == 1 and journal[0]["pid"] == 101 and journal[0]["mode"] == "worker"
    assert not (run / "campaign-seal.json").exists()


def test_scorer_validation_failure_preserves_both_pids_and_blocks_next_trial(run, monkeypatch):
    calls = []
    def launch(root, mode, trial):
        calls.append((mode, trial["trial_id"]))
        return process(trial, mode)
    monkeypatch.setattr(coordinator, "validate_worker", lambda *args: minimal_worker())
    def invalid(*args):
        raise ValueError("Scorer identity mismatch")
    monkeypatch.setattr(coordinator, "validate_score", invalid)
    with pytest.raises(ValueError, match="identity"):
        coordinator.run_acceptance(run, launch)
    assert calls == [("worker", "skill_success"), ("score", "skill_success")]
    journal = [json.loads(line) for line in (run / "process-ledger.jsonl").read_text().splitlines()]
    assert [row["pid"] for row in journal] == [101, 102]


@pytest.mark.parametrize("complete,unknown", [(False, []), (True, ["attempt-1"]), (False, ["attempt-1"])])
def test_partial_archive_or_unknown_usage_never_permits_online_continuation(run, monkeypatch, complete, unknown):
    worker = minimal_worker()
    worker["agent"]["archive_verify"]["complete"] = complete
    worker["limits"]["unknown_usage_attempts"] = unknown
    monkeypatch.setattr(coordinator, "validate_worker", lambda *args: worker)
    monkeypatch.setattr(coordinator, "validate_score", lambda *args: {"checker": {"passed": False}})
    result = coordinator.execute_trial(run, coordinator.trials()[0], lambda r, m, t: process(t, m))
    assert result["status"] == "completed" and result["online_continue_eligible"] is False


@pytest.mark.parametrize("damage", ["missing_manifest", "tampered_manifest", "source", "input", "protocol", "rehash_authorization"])
def test_bad_run_manifest_or_dependency_stops_before_any_child(run, monkeypatch, damage):
    manifest = read(run / "manifest.json")
    if damage == "missing_manifest": (run / "manifest.json").unlink()
    elif damage == "tampered_manifest": put(run / "manifest.json", {"bad": True})
    elif damage == "source": put(run / "source/inert.py", b"tampered")
    elif damage == "input": put(Path(manifest["study"]) / "inert.pkl", b"tampered")
    elif damage == "protocol": put(run / "PROTOCOL_CN.md", b"tampered")
    else:
        manifest["online_authorized_executions"] = 14
        put(run / "manifest.json", manifest)
        put(run / "manifest.sha256", (coordinator.sha((run / "manifest.json").read_bytes()) + "\n").encode())
    with pytest.raises((ValueError, FileNotFoundError)):
        coordinator.execute_trial(run, coordinator.trials()[0], coordinator.launch)
    assert not (run / "process-logs").exists()


def install_fake_child(monkeypatch, *, wait_raises=False):
    events = []
    class Child:
        pid = 303
        returncode = None
        def poll(self): return self.returncode
        def kill(self): events.append("kill")
        def wait(self, timeout):
            events.append("wait")
            if wait_raises: raise subprocess.TimeoutExpired("synthetic-child", timeout)
            self.returncode = -9
            return self.returncode
    child = Child()
    def popen(*args, **kwargs):
        events.append("spawn")
        assert all(not name.startswith("UAV_") for name in kwargs["env"])
        return child
    monkeypatch.setattr(coordinator.subprocess, "Popen", popen)
    clock = iter([0.0, 100.0, 101.0, 102.0, 103.0])
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(coordinator.time, "sleep", lambda _: None)
    return child, events


def test_launch_kills_then_waits_before_returning_a_joined_result(run, monkeypatch):
    child, events = install_fake_child(monkeypatch)
    result = coordinator.launch(run, "worker", coordinator.trials()[0])
    assert events == ["spawn", "kill", "wait"]
    assert result["exited"] is True and result["returncode"] == -9 and result["kill_reason"] == "watchdog"
    assert child.returncode == -9


def test_kill_without_successful_wait_raises_and_blocks_every_later_child(run, monkeypatch):
    _, events = install_fake_child(monkeypatch, wait_raises=True)
    with pytest.raises(subprocess.TimeoutExpired):
        coordinator.run_acceptance(run, coordinator.launch)
    assert events == ["spawn", "kill", "wait"]
    assert read(run / "incomplete.json")["automatic_retry"] is False
    assert not (run / "results.json").exists()
    assert not (run / "campaign-seal.json").exists()


def test_existing_attempt_is_not_resumed_or_retried(run):
    put(run / "process-ledger.jsonl", b"keep original\n")
    with pytest.raises(ValueError, match="retry/resume"):
        coordinator.run_acceptance(run, lambda *args: pytest.fail("No child may start"))
    assert (run / "process-ledger.jsonl").read_text() == "keep original\n"


class LockedWorld:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = []
        self.closed = False
    def freeze_score(self, reason):
        assert self.lock._is_owned()
        self.events.append(("freeze", reason))
        self.closed = True
    def check_admission(self):
        self.events.append(("admission", None))
        if self.closed: raise TimeoutError("world closed")


def test_cancel_stops_new_admission_before_effective_freeze_and_allows_locked_action_to_finish():
    world = LockedWorld()
    life = adapter.EpisodeLifecycle(world)
    with world.lock:
        thread = threading.Thread(target=life.request_cancel)
        thread.start()
        assert life.cancel_event.wait(1.0)
        assert life.requested_at is not None and life.effective_at is None and not world.closed
        with pytest.raises(RuntimeError, match="cancelled"):
            life.check()
        world.events.append(("previously_admitted_action_committed", None))
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert world.events == [("previously_admitted_action_committed", None), ("freeze", "cancellation_effective")]
    assert life.requested_at <= life.effective_at and life.snapshot()["cancelled"] is True
    before = life.snapshot()
    life.request_cancel()
    assert life.snapshot() == before and len(world.events) == 2


def test_lifecycle_checks_world_admission_until_cancelled():
    world = LockedWorld()
    life = adapter.EpisodeLifecycle(world)
    life.check()
    assert world.events == [("admission", None)]
    world.closed = True
    with pytest.raises(TimeoutError, match="closed"):
        life.check()


@pytest.mark.parametrize("endpoint", ["/drones", "/drones/actor", "/drones/actor/nearby", "/sessions/current",
    "/sessions/current/data", "/sessions/session-a/task-progress", "/environments/current", "/targets/waypoints",
    "/targets/id", "/obstacles/id"])
def test_public_reads_remain_available(endpoint):
    assert adapter.task_request_allowed("GET", endpoint) is True
    assert adapter.task_request_allowed("GET", endpoint, admin=True) is False


@pytest.mark.parametrize("command", ["take_off", "land", "move_to", "move_along_path", "change_altitude", "hover",
    "rotate", "move_towards", "return_home", "set_home", "calibrate", "charge", "take_photo", "send_message", "broadcast"])
def test_public_uav_commands_remain_available(command):
    endpoint = "/drones/actor/command/" + command
    assert adapter.task_request_allowed("POST", endpoint) is True
    assert adapter.task_request_allowed("POST", endpoint, admin=True) is False


@pytest.mark.parametrize("endpoint", ["/obstacles/collision/check", "/obstacles/collision/path"])
def test_public_collision_reads_remain_available_as_post(endpoint):
    assert adapter.task_request_allowed("POST", endpoint) is True


@pytest.mark.parametrize("method,endpoint", [("POST", "/sessions"), ("POST", "/sessions/current"),
    ("DELETE", "/sessions/current"), ("PUT", "/obstacles/id"), ("POST", "/obstacles"),
    ("PATCH", "/environments/current"), ("POST", "/targets"), ("DELETE", "/drones/actor"),
    ("GET", "/sessions/current/tasks/id/check"), ("GET", "/sessions/current/tasks/id"),
    ("GET", "/check/private"), ("POST", "/drones/actor/command/unknown"),
    ("POST", "/drones/actor/command/move_to/extra"), ("POST", "/admin/reset")])
def test_private_scoring_and_world_writers_are_rejected(method, endpoint):
    assert adapter.task_request_allowed(method, endpoint) is False


@pytest.fixture
def scoring_artifacts(run):
    trial = coordinator.trials()[0]
    folder = run / "trials" / trial["trial_id"]
    snapshot = put(folder / "private/score-world.pkl", b"INERT NEVER UNPICKLED")
    put(folder / "private/worker-console.log", b"synthetic closed console\n")
    worker = {"pid": 101, "snapshot": {"sha256": coordinator.sha(snapshot.read_bytes())}}
    worker_path = put(folder / "worker-result.json", worker)
    exit_seal = {str(p.relative_to(folder)): coordinator.sha(p.read_bytes()) for p in folder.rglob("*") if p.is_file()}
    put(run / "worker-exit-seals" / (trial["trial_id"] + ".json"), exit_seal)
    result = {"trial_id": trial["trial_id"], "pid": 102, "worker_pid": 101,
        "worker_result_sha256": coordinator.sha(worker_path.read_bytes()), "snapshot_sha256": worker["snapshot"]["sha256"],
        "checker": {"passed": False, "errors": []}, "private_canary_present": True,
        "checker_tree_matches_original": True, "agent_imported": False}
    put(folder / "evaluation/result.json", result)
    return run, trial, folder, worker, result


def test_valid_independent_scorer_accepts_false_task_result_and_seals_output(scoring_artifacts):
    run, trial, folder, worker, expected = scoring_artifacts
    assert coordinator.validate_score(run, trial, worker, process(trial, "score")) == expected
    seal = read(run / "scorer-exit-seals" / (trial["trial_id"] + ".json"))
    assert seal["evaluation/result.json"] == coordinator.sha((folder / "evaluation/result.json").read_bytes())


@pytest.mark.parametrize("field,value", [("trial_id", "other"), ("pid", 999), ("worker_pid", 999),
    ("worker_result_sha256", "0" * 64), ("snapshot_sha256", "0" * 64), ("private_canary_present", False),
    ("checker_tree_matches_original", False), ("agent_imported", True), ("checker", {"passed": False, "errors": ["failed"]})])
def test_scorer_identity_and_private_evaluation_contract_are_independent(scoring_artifacts, field, value):
    run, trial, folder, worker, result = scoring_artifacts
    result[field] = value
    put(folder / "evaluation/result.json", result)
    with pytest.raises(ValueError):
        coordinator.validate_score(run, trial, worker, process(trial, "score"))
    assert not (run / "scorer-exit-seals").exists()


@pytest.mark.parametrize("changes", [{"exited": False}, {"returncode": 1}, {"kill_reason": "watchdog"}])
def test_unjoined_or_failed_scorer_is_not_published(scoring_artifacts, changes):
    run, trial, _, worker, _ = scoring_artifacts
    with pytest.raises(ValueError):
        coordinator.validate_score(run, trial, worker, process(trial, "score", **changes))


def test_scorer_rechecks_worker_exit_seal(scoring_artifacts):
    run, trial, folder, worker, _ = scoring_artifacts
    put(folder / "private/worker-console.log", b"late mutation after worker joined")
    with pytest.raises(ValueError):
        coordinator.validate_score(run, trial, worker, process(trial, "score"))
