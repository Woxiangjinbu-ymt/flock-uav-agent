#!/usr/bin/env python3
"""Detached owner for exactly one M1-R fast100 arm; start is explicitly ONLINE."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime
import uuid

# An archived run copies this file back to its original scripts directory.
_source_dir = Path(__file__).resolve().parent
if _source_dir.name == "history":
    sys.path.insert(0, str(_source_dir.parents[2] / "scripts"))


import m1_revision_pilot as pilot
from m1_progress_job import run_job, save, now

ROOT = _source_dir.parents[2] if _source_dir.name == "history" else _source_dir.parent
DRIVER = ROOT / "scripts/m1_revision_pilot.py"


def commands(request):
    if request["arms"] != [pilot.ARM]:
        raise ValueError("Only one M1-R arm is allowed")
    return [(pilot.ARM, [str(ROOT / "controller/.venv/bin/python"), "-B", str(DRIVER), "run",
                        "--study-dir", request["study_directory"], "--method-commit", request["method_commit"]])]


def worker(directory):
    directory = Path(directory).resolve()
    request = json.loads((directory / "request.json").read_text())
    for path, value in request["launcher_sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != value:
            save(directory / "status.json", {"status": "failed", "reason": "launcher_changed", "finished_at": now()})
            return 2
    with (directory.parent / ".job.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            save(directory / "status.json", {"status": "failed", "reason": "another_revision_job_active", "finished_at": now()})
            return 2
        return run_job(directory, commands(request), cwd=ROOT)


def start(study_dir, method_commit):
    study_dir = Path(study_dir).resolve()
    study = pilot.load_study(study_dir)
    commit = pilot.git_commit(method_commit)
    if commit != study["methods"][pilot.ARM]["agent_commit"]:
        raise ValueError("Method commit differs from freeze")
    pre = pilot.r.read_json(study_dir / "preflight.json")
    if not pre["ready_to_start"] or pre["study_sha256"] != pilot.r.digest((study_dir / "frozen/study.json").read_bytes()):
        raise ValueError("Missing or stale preflight")
    if (study_dir / "runs.json").exists() and pilot.r.read_json(study_dir / "runs.json"):
        raise ValueError("Existing attempt: no rerun")
    pilot.check_environment(study)
    directory = study_dir / "jobs" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, mode=0o700)
    paths = ("scripts/m1_revision_pilot.py", "scripts/m1_revision_job.py", "scripts/m1_progress_job.py")
    save(directory / "request.json", {"method_commit": commit, "study_directory": str(study_dir), "arms": [pilot.ARM],
         "created_at": now(), "launcher_sha256": {p: pilot.r.digest((ROOT / p).read_bytes()) for p in paths}})
    save(directory / "status.json", {"status": "queued", "created_at": now()})
    with (directory / "owner.log").open("ab", buffering=0) as log:
        owner = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "worker", str(directory)],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
    print(json.dumps({"job": str(directory), "owner_pid": owner.pid, "arms": [pilot.ARM], "status": "queued"}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("start", help="ONLINE: begin paid M1-R only")
    launch.add_argument("--method-commit", required=True)
    launch.add_argument("--study-dir", type=Path, default=pilot.SPEC)
    sub.add_parser("worker").add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        return start(args.study_dir, args.method_commit) if args.command == "start" else worker(args.directory)
    except Exception as error:
        print(f"M1-R job stopped: {type(error).__name__}; inspect local evidence", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
