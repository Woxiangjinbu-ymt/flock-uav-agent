#!/usr/bin/env python3
"""Own the two M1 study arms outside a terminal, preserving stage exit codes.

This only sequences the existing study CLI. The study's global runner lock,
owned service supervisors, failure policy and scoring remain authoritative.
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

# An archived run copies this file back to its original scripts directory.
_source_dir = Path(__file__).resolve().parent
if _source_dir.name == "history":
    sys.path[:0] = [str(_source_dir.parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]


ROOT = _source_dir.parents[2] if _source_dir.name == "history" else _source_dir.parent
JOBS = ROOT / "experiments/m1-progress-v1/jobs"


def launcher_path(root, logical_name):
    """Read a live compatibility entry or a detached run's own source file."""
    original = root / logical_name
    if original.is_file():
        return original
    return root / "compat" / logical_name


def now():
    return datetime.now().astimezone().isoformat()


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def run_job(directory, commands, cwd=ROOT):
    """Stop at the first failed arm; no automatic rerun or recovery."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    state = {"status": "running", "pid": os.getpid(), "started_at": now(),
             "heartbeat_at": now(), "finished_at": None, "received_signal": None,
             "stages": [], "automatic_retries": 0}
    child = None

    def stop(signum, _frame):
        state["received_signal"] = signum
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    save(directory / "status.json", state)
    try:
        for arm, argv in commands:
            if state["received_signal"] is not None:
                break
            stage = {"arm": arm, "started_at": now(), "exit_code": None}
            state["stages"].append(stage)
            with (directory / f"{arm}.log").open("ab", buffering=0) as output:
                child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=subprocess.STDOUT,
                                         start_new_session=True, close_fds=True)
                if state["received_signal"] is not None and child.poll() is None:
                    child.send_signal(signal.SIGTERM)
                stage["pid"] = child.pid
                save(directory / "status.json", state)
                last_update = time.monotonic()
                while child.poll() is None:
                    if time.monotonic() - last_update >= 10:
                        state["heartbeat_at"] = now()
                        save(directory / "status.json", state)
                        last_update = time.monotonic()
                    time.sleep(.2)
                stage.update(exit_code=child.returncode, finished_at=now())
                child = None
            if stage["exit_code"] != 0:
                state["status"] = "failed"
                break
        else:
            state["status"] = "completed"
        if state["received_signal"] is not None:
            state["status"] = "interrupted"
    except BaseException as exc:
        state.update(status="failed", error_type=type(exc).__name__)
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            # The runner owns shutdown/drain and saves its own evidence.
            child.wait()
    finally:
        state.update(finished_at=now(), heartbeat_at=now())
        save(directory / "status.json", state)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0 if state["status"] == "completed" else 2


def worker(directory):
    request = json.loads((directory / "request.json").read_text())
    driver = launcher_path(ROOT, "scripts/m1_progress_pilot.py")
    if hashlib.sha256(driver.read_bytes()).hexdigest() != request["driver_sha256"]:
        save(directory / "status.json", {"status": "failed", "reason": "driver_changed", "finished_at": now()})
        return 2
    with (JOBS / ".job.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            save(directory / "status.json", {"status": "failed", "reason": "another_m1_job_active", "finished_at": now()})
            return 2
        commands = [(arm, [str(ROOT / "controller/.venv/bin/python"), "-B", str(driver),
                           "run", "--arm", arm, "--method-commit", request["method_commit"]])
                    for arm in ("m0", "m1")]
        return run_job(directory, commands)


def start(method_commit):
    commit = subprocess.check_output(["git", "rev-parse", "--verify", f"{method_commit}^{{commit}}"],
                                     cwd=ROOT, text=True).strip()
    directory = JOBS / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, mode=0o700)
    save(directory / "request.json", {"method_commit": commit, "arms": ["m0", "m1"],
         "created_at": now(), "driver_sha256": hashlib.sha256((launcher_path(ROOT, "scripts/m1_progress_pilot.py")).read_bytes()).hexdigest()})
    save(directory / "status.json", {"status": "queued", "created_at": now()})
    with (directory / "owner.log").open("ab", buffering=0) as output:
        owner = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                  "worker", str(directory)], cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
    print(json.dumps({"job": str(directory), "owner_pid": owner.pid, "status": "queued"}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("start")
    launch.add_argument("--method-commit", required=True)
    run = sub.add_parser("worker")
    run.add_argument("directory", type=Path)
    args = parser.parse_args()
    return start(args.method_commit) if args.command == "start" else worker(args.directory)


if __name__ == "__main__":
    raise SystemExit(main())
