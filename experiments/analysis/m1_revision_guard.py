#!/usr/bin/env python3
"""External budget guard for the frozen, single-arm M1-R job.

No model/scoring code changes. Owns the existing job worker as a direct child;
SIGTERM follows that worker's existing graceful shutdown path. Never resumes.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import m1_revision_job as job

POLICY = {
    "schema": 1, "minimum_scored_pairs": 40, "drop_percentage_points": 10,
    "poll_seconds": 5, "comparison": "same scored tasks against frozen historical M0",
    "boundary": "(m0_passes - m1r_passes) * 100 >= 10 * scored_pairs",
    "action": "SIGTERM owned job worker; preserve partial results; no retry",
    "unscored": "excluded from paired rates; retain all 100 ledger entries",
    "purpose": "budget futility safeguard, not a statistical test",
}
LAUNCHERS = ("scripts/m1_revision_pilot.py", "scripts/m1_revision_job.py", "scripts/m1_progress_job.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def paired_snapshot(rows, reference):
    """Original scorer's finalized outcomes only; missing outcomes are not failures."""
    keys = [(t["session_id"], t["task_id"]) for t in reference["tasks"]]
    if len(keys) != 100 or len(set(keys)) != 100:
        raise ValueError("Reference must have 100 unique tasks")
    if [(t["session_id"], t["task_id"]) for t in rows] != keys:
        raise ValueError("Current ledger must preserve the original 100-task order")
    pairs = []
    for row, baseline in zip(rows, reference["tasks"]):
        if type(baseline["passed"]) is not bool:
            raise ValueError("Invalid historical score")
        if row["status"] != "scored":
            continue
        if type(row.get("passed")) is not bool or row.get("task_pass") != row["passed"]:
            raise ValueError("Invalid finalized score")
        pairs.append({"session_id": row["session_id"], "task_id": row["task_id"],
                      "m0_pass": baseline["passed"], "m1r_pass": row["passed"]})
    n = len(pairs)
    m0, m1r = sum(p["m0_pass"] for p in pairs), sum(p["m1r_pass"] for p in pairs)
    return {"scored_pairs": n, "unscored": 100 - n, "m0_passes": m0, "m1r_passes": m1r,
            "m0_rate": m0 / n if n else None, "m1r_rate": m1r / n if n else None,
            "drop_pp": (m0 - m1r) * 100 / n if n else None,
            "wins": sum(p["m1r_pass"] and not p["m0_pass"] for p in pairs),
            "losses": sum(p["m0_pass"] and not p["m1r_pass"] for p in pairs),
            "triggered": n >= POLICY["minimum_scored_pairs"] and
                         (m0 - m1r) * 100 >= POLICY["drop_percentage_points"] * n,
            "pairs": pairs}


def snapshot(study_dir, study, reference):
    registry = study_dir / "runs.json"
    if not registry.exists():
        return None
    records = read(registry)
    if set(records) != {"m1r"}:
        raise ValueError("Only the M1-R arm is allowed")
    attempt = Path(records["m1r"]["attempt"]).resolve()
    if attempt.parent != Path(study["runs_root"]).resolve():
        raise ValueError("Unexpected attempt directory")
    plan = read(attempt / "plan.json")
    if plan["study"]["study_directory"] != str(study_dir) or plan["study"]["arm"] != "m1r":
        raise ValueError("Attempt belongs to another study")
    path = attempt / "results.json"
    if not path.exists():
        return None
    data = path.read_bytes()
    return {**paired_snapshot(json.loads(data), reference), "attempt": str(attempt),
            "results_sha256": hashlib.sha256(data).hexdigest(), "observed_at": job.now()}


def supervise(directory, argv, observe, poll_seconds=5):
    """Direct Popen ownership prevents signaling an unrelated/reused saved PID."""
    state = {"status": "starting", "pid": os.getpid(), "started_at": job.now(),
             "heartbeat_at": job.now(), "stop_reason": None, "automatic_retries": 0}
    child = None
    signaled = False
    latest = None

    def receive(signum, _frame):
        state["stop_reason"] = state["stop_reason"] or "external_signal"
        state["received_signal"] = signum

    previous = {s: signal.signal(s, receive) for s in (signal.SIGTERM, signal.SIGINT)}
    job.save(directory / "guard-status.json", state)
    try:
        with (directory / "owner.log").open("ab", buffering=0) as output:
            child = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                     stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            state.update(status="monitoring", worker_pid=child.pid)
            while child.poll() is None:
                if state["stop_reason"] is None:
                    latest = observe()
                    if latest is not None:
                        state["latest"] = {k: v for k, v in latest.items() if k != "pairs"}
                        if latest["scored_pairs"] != state.get("last_logged_pairs"):
                            with (directory / "observations.jsonl").open("a") as log:
                                log.write(json.dumps(latest, ensure_ascii=False) + "\n")
                            state["last_logged_pairs"] = latest["scored_pairs"]
                        # Once all 100 are scored, let the frozen verifier finish.
                        if latest["triggered"] and latest["scored_pairs"] < 100:
                            state["stop_reason"] = "performance_drop"
                            job.save(directory / "stop-trigger.json", {"policy": POLICY, **latest})
                if state["stop_reason"] is not None and not signaled and child.poll() is None:
                    state.update(status="stopping", stop_requested_at=job.now())
                    job.save(directory / "guard-status.json", state)
                    child.send_signal(signal.SIGTERM)
                    signaled = True
                state["heartbeat_at"] = job.now()
                job.save(directory / "guard-status.json", state)
                time.sleep(poll_seconds)
            state["worker_exit_code"] = child.returncode
            final = observe()
            if final is not None:
                job.save(directory / "final-paired-snapshot.json", final)
                state["latest"] = {k: v for k, v in final.items() if k != "pairs"}
            state["status"] = ("stopped" if state["stop_reason"] else
                               "completed" if child.returncode == 0 else "failed")
    except BaseException as error:
        state.update(status="failed", error_type=type(error).__name__)
        state["stop_reason"] = state["stop_reason"] or "guard_error"
        if child is not None and child.poll() is None:
            job.save(directory / "guard-status.json", state)
            child.send_signal(signal.SIGTERM)
            child.wait()  # Existing runner owns its services and shutdown/drain.
        if child is not None:
            state["worker_exit_code"] = child.returncode
    finally:
        state.update(finished_at=job.now(), heartbeat_at=job.now())
        job.save(directory / "guard-status.json", state)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0 if state["status"] == "completed" else 2


def worker(directory):
    directory = Path(directory).resolve()
    try:
        request = read(directory / "request.json")
        for name, checksum in request["launcher_sha256"].items():
            if digest(ROOT / name) != checksum:
                raise ValueError("Launcher changed")
        if digest(__file__) != request["guard_sha256"] or read(directory / "guard-policy.json") != POLICY:
            raise ValueError("Guard changed")
        study_dir = Path(request["study_directory"])
        study = job.pilot.load_study(study_dir)
        if digest(study_dir / "frozen/study.json") != request["study_sha256"]:
            raise ValueError("Study changed")
        reference = read(study_dir / "frozen/historical-m0.json")
        with (directory.parent / ".guard.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return supervise(directory,
                [sys.executable, "-B", str(ROOT / "scripts/m1_revision_job.py"), "worker", str(directory)],
                lambda: snapshot(study_dir, study, reference), POLICY["poll_seconds"])
    except Exception as error:
        job.save(directory / "guard-status.json", {"status": "failed", "error_type": type(error).__name__,
                                                   "finished_at": job.now(), "automatic_retries": 0})
        return 2


def start(study_dir, method_commit):
    study_dir = Path(study_dir).resolve()
    study = job.pilot.load_study(study_dir)
    commit = job.pilot.git_commit(method_commit)
    if commit != study["methods"]["m1r"]["agent_commit"] or study["arm_order"] != ["m1r"]:
        raise ValueError("Method differs from single-arm freeze")
    pre = read(study_dir / "preflight.json")
    if not pre["ready_to_start"] or pre["study_sha256"] != digest(study_dir / "frozen/study.json"):
        raise ValueError("Missing or stale preflight")
    if (study_dir / "runs.json").exists() and read(study_dir / "runs.json"):
        raise ValueError("Existing attempt: no rerun")
    job.pilot.check_environment(study)
    directory = study_dir / "jobs" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-guard-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, mode=0o700)
    job.save(directory / "request.json", {"method_commit": commit, "study_directory": str(study_dir),
             "arms": ["m1r"], "created_at": job.now(), "launcher_sha256": {p: digest(ROOT / p) for p in LAUNCHERS},
             "guard_sha256": digest(__file__), "study_sha256": pre["study_sha256"],
             "historical_m0_sha256": digest(study_dir / "frozen/historical-m0.json")})
    job.save(directory / "guard-policy.json", POLICY)
    job.save(directory / "status.json", {"status": "queued", "created_at": job.now()})
    job.save(directory / "guard-status.json", {"status": "queued", "created_at": job.now()})
    with (directory / "guard.log").open("ab", buffering=0) as output:
        guard = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "worker", str(directory)],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
    result = {"job": str(directory), "guard_pid": guard.pid, "arms": ["m1r"], "status": "queued"}
    job.save(directory / "launch.json", result)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("start", help="ONLINE paid single-arm test with early-stop guard")
    launch.add_argument("--study-dir", type=Path, default=job.pilot.SPEC)
    launch.add_argument("--method-commit", required=True)
    sub.add_parser("worker").add_argument("directory", type=Path)
    args = parser.parse_args()
    sys.exit(start(args.study_dir, args.method_commit) if args.command == "start" else worker(args.directory))
