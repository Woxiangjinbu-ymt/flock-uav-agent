"""First-attempt-only, paired balanced mechanism queue. Never dispatches full100."""
from __future__ import annotations
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import subprocess
import threading
import time

from harness_v2_world import sha, write_json


def read(path):
    return json.loads(Path(path).read_text())


def verify(study):
    manifest = read(study / "manifest.json")
    for name, expected in manifest["files"].items():
        if sha((study / name).read_bytes()) != expected:
            raise ValueError(f"Frozen artifact changed: {name}")
    schedule = read(study / "schedule.json")
    if schedule["executions"] != 96 or len(schedule["blocks"]) != 48:
        raise ValueError("Only the authorized 96 mechanism executions are allowed")
    if any(b["stage"] != "mechanism" for b in schedule["blocks"]):
        raise ValueError("Unauthorized study stage")
    worker_ids(schedule)
    return schedule


def worker_ids(schedule):
    ids = sorted({b["worker"] for b in schedule["blocks"]})
    if ids != [0, 1]:
        raise ValueError("Unexpected worker labels in frozen schedule")
    return ids


def summarize(study):
    runs = read(study / "runs.json") if (study / "runs.json").exists() else {}
    results = []
    seals_valid = True
    for trial in runs.values():
        out = study / "runs" / trial["trial_id"]
        if trial["status"] == "completed":
            seal = read(out / "seal.json")
            valid = all(sha((out / name).read_bytes()) == value for name, value in seal["files"].items())
            seals_valid = seals_valid and valid
            results.append(read(out / "result.json"))
    pairs = []
    for block in read(study / "schedule.json")["blocks"]:
        group = {r["arm"]: r for r in results if r["case_id"] == block["case_id"] and r["repeat"] == block["repeat"]}
        if len(group) == 2:
            a, f = group["applicability"], group["failure"]
            pairs.append({"block_id": block["block_id"], "case_id": block["case_id"],
                "a_pass": a["checker"]["passed"], "f_pass": f["checker"]["passed"],
                "call_delta": a["logical_calls_within_500"] - f["logical_calls_within_500"],
                "token_delta": a["usage"]["total_tokens"] - f["usage"]["total_tokens"],
                "ready_seconds_delta": a["ready_seconds"] - f["ready_seconds"],
                "same_initial_state": a["initial_state_sha256"] == f["initial_state_sha256"]})
    report = {"updated_unix": time.time(), "planned": 96, "completed": len(results),
        "failed": sum(r["status"] == "failed" for r in runs.values()),
        "running": sum(r["status"] == "running" for r in runs.values()), "seals_valid": seals_valid,
        "actual_tokens_so_far": sum(r["usage"]["total_tokens"] for r in results),
        "logical_calls_so_far": sum(r["logical_calls"] for r in results),
        "http_attempts_so_far": sum(r["http_attempts"] for r in results),
        "complete_pairs": len(pairs), "pairs": pairs, "results": results,
        "interpretation": "Conditional controlled mechanism study, no full100 or skill-selection claim"}
    write_json(study / "summary.json", report)
    return report


def campaign(study, workspace, *, continue_unstarted=False):
    study, workspace = study.resolve(), workspace.resolve()
    lock_file = (study / "campaign.lock").open("a")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    schedule = verify(study)
    if not continue_unstarted and ((study / "runs.json").exists() or (study / "campaign.json").exists()):
        raise ValueError("An attempt already exists; automatic resume/replay is forbidden")
    runs = read(study / "runs.json") if continue_unstarted else {}
    if any(r["status"] != "completed" for r in runs.values()):
        raise ValueError("Cannot continue with unfinished/failed first attempts")
    for ident in runs:
        output = study / "runs" / ident
        if not all(sha((output / name).read_bytes()) == value for name, value in read(output / "seal.json")["files"].items()):
            raise ValueError("Existing first attempt seal changed")
    mutex = threading.RLock()
    stop = threading.Event()
    state = {"status": "running", "pid": os.getpid(), "started_unix": time.time(), "planned": 96,
             "workers": 2, "token_soft_stop_before_block": 100_000_000, "allowed_stage": "mechanism",
             "model": "deepseek-flash", "auto_rerun": False}
    if continue_unstarted:
        previous = read(study / "campaign.json")
        state.update(started_unix=previous["started_unix"], queue_repair_unix=time.time(),
                     queue_repair="Worker-label correction; sealed completed trials skipped, never replayed")
    write_json(study / "campaign.json", state)
    write_json(study / "runs.json", runs)
    runtime = study / "frozen/runtime"
    (study / "dispatch").mkdir(exist_ok=True)
    (study / "logs").mkdir(exist_ok=True)

    def worker(blocks):
        try:
            for block in blocks:
                with mutex:
                    if stop.is_set():
                        return
                    used = sum(r.get("total_tokens", 0) for r in runs.values())
                    if used >= state["token_soft_stop_before_block"]:
                        state.update(stop_reason="token_soft_stop_before_next_pair", status="stopped")
                        write_json(study / "campaign.json", state)
                        return
                # Finish an already-started pair unless a terminal failure occurs.
                for arm in block["trials"]:
                    with mutex:
                        if stop.is_set():
                            return
                    ident = f"{block['case_id']}-r{block['repeat']}-{arm['arm']}"
                    with mutex:
                        if ident in runs:
                            if not continue_unstarted or runs[ident]["status"] != "completed":
                                raise ValueError("Duplicate execution refused")
                            continue
                    trial = {"trial_id": ident, "case_id": block["case_id"], "repeat": block["repeat"],
                             "worker": block["worker"], "arm": arm["arm"], "position": arm["position"],
                             "block_id": block["block_id"], "status": "running", "started_unix": time.time()}
                    path = study / "dispatch" / f"{ident}.json"
                    write_json(path, trial)
                    with mutex:
                        if ident in runs:
                            raise ValueError("Duplicate execution refused")
                        runs[ident] = trial
                        write_json(study / "runs.json", runs)
                    env = os.environ.copy()
                    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", MPLBACKEND="Agg",
                               PYGAME_HIDE_SUPPORT_PROMPT="1", LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
                    cmd = [str(workspace / "agent4drone/.venv/bin/python"),
                        str(runtime / "scripts/harness_v2_online.py"), "--study", str(study),
                        "--trial", str(path), "--output", str(study / "runs" / ident),
                        "--workspace", str(workspace)]
                    with (study / "logs" / f"{ident}.log").open("x") as logfile:
                        process = subprocess.Popen(cmd, cwd=study / "logs", env=env, stdout=logfile,
                                                   stderr=subprocess.STDOUT)
                        with mutex:
                            trial["pid"] = process.pid
                            write_json(study / "runs.json", runs)
                        try:
                            code = process.wait(timeout=630)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill(); process.wait()
                            code = -999
                    with mutex:
                        trial.update(exit_code=code, ended_unix=time.time(), status="failed")
                        output = study / "runs" / ident
                        if code == 0 and (output / "seal.json").exists():
                            result = read(output / "result.json")
                            seal = read(output / "seal.json")
                            if result["status"] == "completed" and all(sha((output / name).read_bytes()) == value
                                    for name, value in seal["files"].items()):
                                trial.update(status="completed", total_tokens=result["usage"]["total_tokens"])
                        if trial["status"] != "completed":
                            if output.exists():
                                write_json(output / "failure-seal.json", {"exit_code": code, "files": {
                                    str(p.relative_to(output)): sha(p.read_bytes())
                                    for p in output.rglob("*") if p.is_file()}})
                            state.update(status="failed", stop_reason=f"First attempt failed: {ident}")
                            stop.set()
                        write_json(study / "runs.json", runs)
                        write_json(study / "campaign.json", state)
                        report = summarize(study)
                        print(json.dumps({"trial": ident, "status": trial["status"],
                             "completed": report["completed"], "tokens": report["actual_tokens_so_far"]}), flush=True)
        except Exception as exc:
            with mutex:
                state.update(status="failed", stop_reason=f"Queue error: {type(exc).__name__}: {exc}")
                stop.set()
                write_json(study / "campaign.json", state)
            raise

    try:
        for repeat in (1, 2):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker, [b for b in schedule["blocks"]
                           if b["repeat"] == repeat and b["worker"] == w]) for w in worker_ids(schedule)]
                for future in futures:
                    future.result()
            if stop.is_set():
                break
    finally:
        report = summarize(study)
        if report["completed"] == 96 and report["seals_valid"]:
            state["status"] = "completed_pending_mechanism_audit"
        elif state["status"] == "running":
            state.update(status="failed", stop_reason="Incomplete queue")
        state["ended_unix"] = time.time()
        write_json(study / "campaign.json", state)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["run", "continue-unstarted", "summarize"])
    p.add_argument("--study", type=Path, required=True)
    p.add_argument("--workspace", type=Path)
    args = p.parse_args()
    if args.action in {"run", "continue-unstarted"}:
        campaign(args.study, args.workspace, continue_unstarted=args.action == "continue-unstarted")
    else:
        r = summarize(args.study)
        print(json.dumps({k: r[k] for k in ["completed", "failed", "running", "actual_tokens_so_far", "seals_valid"]}))
