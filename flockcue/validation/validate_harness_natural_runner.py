"""Frozen local wall-clock acceptance, never the proposed online 14-slot queue."""
from __future__ import annotations
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import base64
import copy
import json
import os
import subprocess
import time
from validate_harness_episode import sha, write, safe_path, check_hashes, policy_scan, workspace_source

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
PROTOCOL = "docs/results/harness-natural-runner-20261025/PROTOCOL_CN.md"
ACCEPTED = "docs/results/harness-full-episode-20261025/run-20261025-02"
CANARY = "PRIVATE_NATURAL_RUNNER_CHECKER_20261025_c8ae1f"
SCENARIOS = ("skill_success", "raw_success", "http_error", "request_timeout", "task_deadline", "cancel",
             "http_limit", "usage_missing", "usage_stop", "hung_drain", "watchdog_hang", "post_kill_B")
EXPECTED_KILLS = {"hung_drain": "transport_not_drained", "watchdog_hang": "watchdog"}


def trials():
    values = []
    for scenario in SCENARIOS:
        runtime = {"task_seconds": 500, "drain_seconds": 0.5,
            "request_limits": {"max_http_attempts": 8, "max_total_tokens": 1000,
                "max_output_tokens": 256, "per_request_timeout_seconds": 5.0}}
        watchdog = 15.0  # local test safety fuse, not the future online watchdog
        if scenario in {"request_timeout", "hung_drain"}:
            runtime["request_limits"]["per_request_timeout_seconds"] = 0.1
        if scenario == "hung_drain":
            runtime["drain_seconds"] = 0.15
        if scenario == "task_deadline":
            runtime["task_seconds"] = 1.0  # explicitly a short real-clock boundary test
        if scenario == "http_limit":
            runtime["request_limits"]["max_http_attempts"] = 2
        if scenario == "usage_stop":
            runtime["request_limits"]["max_total_tokens"] = 52
        if scenario == "watchdog_hang":
            runtime["request_limits"]["per_request_timeout_seconds"] = 30.0
            watchdog = 4.0
        values.append({"trial_id": scenario, "scenario": scenario, "task_id": "b1eb0270",
                       "scheduler": "applicability", "runtime": runtime, "watchdog_seconds": watchdog})
    return values


def prepare(workspace, output):
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Existing attempt cannot be overwritten")
    accepted = workspace / ACCEPTED
    previous = json.loads((accepted / "manifest.json").read_text())
    seal = json.loads((accepted / "campaign-seal.json").read_text())
    check_hashes(accepted, seal["files"])
    check_hashes(workspace, previous["sources"])
    study = workspace / "experiments/minimal-harness-full100-v1"
    original = json.loads((study / "manifest.json").read_text())
    check_hashes(study, original["files"])
    check_hashes(study, previous["inputs"])
    names = set(previous["sources"]) | {"scripts/" + name for name in (
        "harness_request_limits.py", "harness_natural_runner.py", "harness_natural_local.py",
        "validate_harness_natural_runner.py", "validate_harness_observed_target.py", "audit_harness_natural_runner.py")}
    sources = {}
    for name in sorted(names):
        source = workspace_source(workspace, name)
        if workspace == ROOT and (source.is_symlink() or source != workspace / name):
            canonical = source.resolve()
            if not canonical.is_relative_to(ROOT / "flockcue"):
                raise ValueError("Source link outside FlockCue: " + name)
            source = safe_path(workspace, canonical.relative_to(workspace))
        else:
            source = safe_path(workspace, name)
        sources[name] = source.read_bytes()
    protocol = (workspace / PROTOCOL).read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    for name, body in sources.items():
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    (output / "PROTOCOL_CN.md").write_bytes(protocol)
    manifest = {"schema_version": 1, "scope": "local_mock_wallclock_only", "workspace": str(workspace),
        "study": str(study), "online_authorized_executions": 0, "local_model": "local-scripted-sdk",
        "private_canary": CANARY, "trials": trials(), "sources": {n: sha(b) for n, b in sources.items()},
        "inputs": previous["inputs"], "protocol_sha256": sha(protocol),
        "previous_seal_sha256": sha((accepted / "campaign-seal.json").read_bytes()),
        "python": sys.version, "queue_policy": "independent_predeclared_local_probes_not_online_continuation"}
    write(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return manifest


def load_manifest(run):
    manifest_path = run / "manifest.json"
    if sha(manifest_path.read_bytes()) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    manifest = json.loads(manifest_path.read_text())
    if manifest["trials"] != trials() or manifest["scope"] != "local_mock_wallclock_only" or manifest["online_authorized_executions"] != 0:
        raise ValueError("Outside local acceptance scope")
    check_hashes(run / "source", manifest["sources"])
    check_hashes(Path(manifest["study"]), manifest["inputs"])
    if sha((run / "PROTOCOL_CN.md").read_bytes()) != manifest["protocol_sha256"]:
        raise ValueError("Protocol changed")
    return manifest


def launch(run, mode, trial):
    manifest = load_manifest(run)
    if trial not in manifest["trials"] or mode not in {"worker", "score"}:
        raise ValueError("Invalid child request")
    logs = run / "process-logs"
    logs.mkdir(exist_ok=True)
    path = logs / (trial["trial_id"] + "-" + mode + ".log")
    packages = [p for c in ("agent4drone", "server") for p in sorted(
        (Path(manifest["workspace"]) / c / ".venv/lib").glob("python*/site-packages"))]
    env = {k: v for k, v in os.environ.items() if not k.startswith("UAV_")}
    env.update(PYTHONPATH=os.pathsep.join(map(str, [run / "source/scripts", run / "source/agent4drone", run / "source/server", *packages])),
               PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1", PYGAME_HIDE_SUPPORT_PROMPT="1")
    command = [sys.executable, str(run / "source/scripts/harness_natural_local.py"), "--run", str(run),
               "--trial", trial["trial_id"], "--mode", mode]
    start_ns, started = time.time_ns(), time.monotonic()
    killed = None
    with path.open("x") as stream:
        child = subprocess.Popen(command, cwd=run / "source", env=env, stdout=stream, stderr=subprocess.STDOUT)
        until = started + (trial["watchdog_seconds"] if mode == "worker" else 15.0)
        while child.poll() is None:
            poison = run / "trials" / trial["trial_id"] / "termination-required.json"
            if mode == "worker" and poison.exists():
                try:
                    notice = json.loads(poison.read_text())
                except (OSError, ValueError):
                    notice = None
                if notice and (notice.get("trial_id"), notice.get("pid")) == (trial["trial_id"], child.pid):
                    killed = "transport_not_drained"
            if time.monotonic() >= until:
                killed = "watchdog"
            if killed:
                child.kill()
                break
            time.sleep(0.01)
        # No next worker or scoring until this succeeds, even after kill().
        returncode = child.wait(timeout=5.0)
    return {"mode": mode, "trial_id": trial["trial_id"], "pid": child.pid,
            "started_ns": start_ns, "ended_ns": time.time_ns(), "elapsed_seconds": time.monotonic() - started,
            "returncode": returncode, "exited": child.poll() is not None, "kill_reason": killed,
            "log_sha256": sha(path.read_bytes())}


def validate_worker(run, trial, process, *, manifest=None):
    if not process["exited"] or process["returncode"] != 0 or process["kill_reason"]:
        raise ValueError("Worker not cleanly exited")
    folder = run / "trials" / trial["trial_id"]
    result = json.loads((folder / "worker-result.json").read_text())
    if (result["status"], result["trial_id"], result["task_id"], result["pid"]) != ("completed", trial["trial_id"], trial["task_id"], process["pid"]):
        raise ValueError("Worker identity mismatch")
    if (not result["drained"] or result["limits"]["needs_process_termination"] or result["limits"]["threads_still_alive"]
            or not result["limits"]["closed"] or not all(result["isolation"].values()) or result["runtime"] != trial["runtime"]):
        raise ValueError("Worker lifecycle invalid")
    agent = result["agent"]
    if not agent["harness_closed"] or not agent["archive_info"]["closed"] or not agent["archive_verify"]["valid"]:
        raise ValueError("Invalid or unclosed model archive")
    if agent["archive_info"]["complete"] != agent["archive_verify"]["complete"]:
        raise ValueError("Archive completeness mismatch")
    archive_path = Path(agent["archive_info"]["path"]).resolve()
    if not archive_path.is_relative_to((folder / "policy").resolve()) or not archive_path.is_file():
        raise ValueError("Archive outside policy directory")
    check_hashes(folder, result["files"])
    expected = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()
                and str(p.relative_to(folder)) not in {"worker-result.json", "private/worker-console.log"}}
    if expected != set(result["files"]):
        raise ValueError("Incomplete worker inventory")
    if result["snapshot"]["path"] != "private/score-world.pkl" or sha((folder / result["snapshot"]["path"]).read_bytes()) != result["snapshot"]["sha256"]:
        raise ValueError("Untrusted scoring snapshot")
    if manifest is None:
        manifest = load_manifest(run)
    if not all(policy_scan([p for p in (folder / "policy").rglob("*") if p.is_file()], manifest["private_canary"]).values()):
        raise ValueError("Private evaluation in policy artifacts")
    write(run / "worker-exit-seals" / (trial["trial_id"] + ".json"), {
        str(p.relative_to(folder)): sha(p.read_bytes()) for p in folder.rglob("*") if p.is_file()})
    return result


def validate_score(run, trial, worker, process):
    if not process["exited"] or process["returncode"] != 0 or process["kill_reason"]:
        raise ValueError("Scorer did not exit cleanly")
    folder = run / "trials" / trial["trial_id"]
    result = json.loads((folder / "evaluation/result.json").read_text())
    if (result["trial_id"], result["pid"], result["worker_pid"]) != (trial["trial_id"], process["pid"], worker["pid"]):
        raise ValueError("Scorer identity mismatch")
    if (result["worker_result_sha256"] != sha((folder / "worker-result.json").read_bytes())
            or result["snapshot_sha256"] != worker["snapshot"]["sha256"] or result["checker"]["errors"]
            or not result["private_canary_present"] or not result["checker_tree_matches_original"] or result["agent_imported"]):
        raise ValueError("Scoring isolation invalid")
    check_hashes(folder, json.loads((run / "worker-exit-seals" / (trial["trial_id"] + ".json")).read_text()))
    write(run / "scorer-exit-seals" / (trial["trial_id"] + ".json"), {
        str(p.relative_to(folder)): sha(p.read_bytes()) for p in (folder / "evaluation").rglob("*") if p.is_file()})
    return result


def execute_trial(run, trial, launcher=launch):
    process = launcher(run, "worker", trial)
    if not process["exited"]:
        raise ValueError("Worker was not joined")
    if process["returncode"] != 0 or process["kill_reason"]:
        return {"trial_id": trial["trial_id"], "status": "incomplete", "worker": None,
                "score": None, "processes": [process], "online_continue_eligible": False}
    worker = validate_worker(run, trial, process)
    score_process = launcher(run, "score", trial)
    score = validate_score(run, trial, worker, score_process)
    return {"trial_id": trial["trial_id"], "status": "completed", "worker": worker,
            "score": score, "processes": [process, score_process],
            "online_continue_eligible": worker["agent"]["archive_verify"]["complete"] and not worker["limits"]["unknown_usage_attempts"]}


def first_input(worker):
    for row in map(json.loads, Path(worker["agent"]["archive_info"]["path"]).read_text().splitlines()):
        if row["kind"] == "http_request":
            return base64.b64decode(row["data"]["body"]["base64"], validate=True).replace(worker["entry"]["command_id"].encode(), b"<COMMAND>")
    raise ValueError("No first model input")


def acceptance_checks(run, manifest, results):
    from audit_harness_natural_runner import audit_trial
    checks = {}
    all_processes = [p for row in results for p in row["processes"]]
    checks["all_cases"] = len(results) == len(trials())
    checks["fresh_processes"] = len({p["pid"] for p in all_processes}) == len(all_processes)
    checks["joined_before_next"] = all(a["ended_ns"] <= b["started_ns"] for a, b in zip(all_processes, all_processes[1:]))
    success_cases = {"skill_success", "raw_success", "post_kill_B"}
    expected_reason = {"http_error": "http_error", "request_timeout": "request_timeout", "task_deadline": "task_deadline",
        "cancel": "cancelled", "http_limit": "http_attempt_limit", "usage_missing": "unknown_usage", "usage_stop": "token_limit"}
    for row in results:
        ident = row["trial_id"]
        trial = next(t for t in manifest["trials"] if t["trial_id"] == ident)
        checks.update({ident + ":oracle:" + k: v for k, v in audit_trial(run, trial, row).items()})
        if ident in EXPECTED_KILLS:
            checks[ident + ":killed_and_unscored"] = (row["status"] == "incomplete" and row["score"] is None
                and len(row["processes"]) == 1 and row["processes"][0]["kill_reason"] == EXPECTED_KILLS[ident]
                and not (run / "trials" / ident / "evaluation").exists())
            continue
        worker, score = row["worker"], row["score"]
        checks[ident + ":no_network"] = worker["network_attempts"] == score["network_attempts"] == []
        checks[ident + ":complete_lifecycle"] = row["status"] == "completed"
        checks[ident + ":expected_task_result"] = score["checker"]["passed"] is (ident in success_cases)
        checks[ident + ":natural_empty_entry"] = worker["entry"]["frames"] == worker["entry"]["ledger_events"] == worker["entry"]["ledger_model_requests"] == 0
        checks[ident + ":real_clock"] = worker["clock"] == "time.monotonic" and worker["timer_enabled"] is True and worker["elapsed"] > 0
        checks[ident + ":actions"] = (len(worker["actions"]) == (3 if ident in success_cases else 1)
            and all(a["committed"] and a["status"] == "success" for a in worker["actions"]))
        checks[ident + ":physical_takeoff"] = worker["physical_cost"]["vertical_distance"] >= 14
        if ident in success_cases:
            checks[ident + ":archive_complete"] = worker["agent"]["archive_verify"]["complete"]
        else:
            checks[ident + ":expected_stop"] = worker["limits"]["stop_reason"] == expected_reason[ident]
        if ident in {"http_limit", "usage_stop"}:
            checks[ident + ":no_third_dispatch"] = worker["limits"]["delegated_http_attempts"] == 2
        if ident == "task_deadline":
            checks[ident + ":real_one_second_boundary"] = worker["snapshot"]["score_seconds"] == 1.0
        if ident == "cancel":
            life = worker["lifecycle"]
            checks[ident + ":cancel_linearized"] = life["cancelled"] and life["requested_at"] <= life["effective_at"]
        check_hashes(run / "trials" / ident, json.loads((run / "worker-exit-seals" / (ident + ".json")).read_text()))
        check_hashes(run / "trials" / ident, json.loads((run / "scorer-exit-seals" / (ident + ".json")).read_text()))
    reference, last = results[0]["worker"], results[-1]["worker"]
    checks["post_kill_initial"] = reference["initial"] == last["initial"]
    checks["post_kill_first_model_input"] = first_input(reference) == first_input(last)
    checks["post_kill_actions"] = reference["actions"] == last["actions"]
    checks["post_kill_score"] = results[0]["score"]["checker"] == results[-1]["score"]["checker"]
    return {"passed": all(checks.values()), "checks": checks, "check_count": len(checks),
            "failed_checks": [k for k, v in checks.items() if not v], "local_worker_count": len(results),
            "scorer_count": sum(r["score"] is not None for r in results), "provider_calls": 0,
            "scope": "local wallclock contract acceptance, not natural-model or benchmark results"}


def run_acceptance(run, launcher=launch):
    run = Path(run).resolve()
    manifest = load_manifest(run)
    journal = run / "process-ledger.jsonl"
    if journal.exists():
        raise ValueError("No automatic retry/resume of an existing attempt")
    results = []
    with journal.open("x") as stream:
        def recorded_launch(root, mode, trial):
            process = launcher(root, mode, trial)
            stream.write(json.dumps(process) + "\n")
            stream.flush()
            return process
        try:
            for trial in manifest["trials"]:
                row = execute_trial(run, trial, recorded_launch)
                results.append(row)
                write(run / "results.json", results)
                if row["status"] == "incomplete" and trial["trial_id"] not in EXPECTED_KILLS:
                    raise ValueError("Unexpected infrastructure failure; no next case")
                if trial["trial_id"] in EXPECTED_KILLS and row["processes"][0]["kill_reason"] != EXPECTED_KILLS[trial["trial_id"]]:
                    raise ValueError("Expected termination probe was not exercised")
            summary = acceptance_checks(run, manifest, results)
            write(run / "summary.json", summary)
            load_manifest(run)
        except Exception as exc:
            write(run / "incomplete.json", {"error": type(exc).__name__, "reason": str(exc), "finished_cases": len(results), "automatic_retry": False})
            raise
    write(run / "campaign-seal.json", {"files": {str(p.relative_to(run)): sha(p.read_bytes())
          for p in sorted(run.rglob("*")) if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.workspace, args.output)
    summary = run_acceptance(args.output)
    print(json.dumps(summary))
    raise SystemExit(0 if summary["passed"] else 1)
