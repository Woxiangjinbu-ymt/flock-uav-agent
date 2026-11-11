"""Versioned local Agent entry: prepare -> run -> verify. No live provider path.

The prepared case list selects tasks/initial snapshots and public scripted replies.
It is development contract evidence, not autonomous task-success measurement.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import copy
import json
import os
import re
import subprocess
import time

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
sys.path.insert(0, str(ROOT / "agent4drone"))
from agent_profile import AgentProfile
from validate_harness_episode import agent_source_items, check_hashes, safe_path, sha, write
from general_agent_local import validate_trace
from validate_harness_natural_runner import validate_worker, validate_score

SCOPE = "general_agent_v1_local_integration"
EXECUTION_SCOPE = "general_agent_v2_execution_contract"
SCRIPTS = ("general_agent.py", "general_agent_local.py", "validate_harness_episode.py",
    "harness_episode_local.py", "harness_full100_world.py", "harness_full100_online.py",
    "harness_v2_world.py", "harness_v2_online.py", "harness_natural_runner.py",
    "harness_request_limits.py", "validate_harness_natural_runner.py", "validate_harness_observed_target.py",
    "audit_general_agent.py", "episode_termination.py")
PROTOCOL = "docs/GENERAL_AGENT_SCOPE_CN.md"
EXECUTION_PROTOCOL = "docs/AGENT_EXECUTION_CONTRACT_CN.md"


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("Invalid task/trial identifier")
    return value


def validate_cases(cases, *, execution_contract=False):
    if not isinstance(cases, list) or not 1 <= len(cases) <= 32:
        raise ValueError("Expected 1..32 explicitly selected local cases")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not {"trial_id", "task_id", "trace_file"} <= set(case):
            raise ValueError("Case requires trial_id, task_id and trace_file")
        allowed = {"trial_id", "task_id", "trace_file", "fault"}
        if execution_contract:
            allowed.add("api_fault")
        if set(case) - allowed:
            raise ValueError("Unknown case field")
        ident = identifier(case["trial_id"])
        identifier(case["task_id"])
        if ident in seen:
            raise ValueError("Duplicate trial identifier")
        seen.add(ident)
        if not isinstance(case["trace_file"], str):
            raise ValueError("Invalid trace path")
        fault = case.get("fault")
        if fault is not None:
            kinds = {"http_error", "cancel", "request_timeout", "usage_missing"}
            if execution_contract:
                kinds.add("task_deadline")
            if (not isinstance(fault, dict) or set(fault) != {"kind", "at_response"}
                or fault["kind"] not in kinds
                or type(fault["at_response"]) is not int or fault["at_response"] < 0):
                raise ValueError("Invalid local fault probe")
        if "api_fault" in case:
            probe = case["api_fault"]
            if (not isinstance(probe, dict) or set(probe) != {"kind", "command", "occurrence"}
                    or probe["kind"] != "response_lost" or probe["command"] not in {"take_off", "rotate", "hover"}
                    or type(probe["occurrence"]) is not int or probe["occurrence"] != 1):
                raise ValueError("Unsupported explicit local API fault")
    return cases


def prepare(profile_path, study, cases_path, output, *, workspace=ROOT, sealed_sources=None):
    workspace, study, output = Path(workspace).resolve(), Path(study).resolve(), Path(output).resolve()
    profile = AgentProfile.load(profile_path)
    cases_path = Path(cases_path).resolve()
    cases = validate_cases(json.loads(cases_path.read_text()), execution_contract=bool(profile.execution_contract))
    if output.exists():
        raise FileExistsError("Existing attempts cannot be overwritten")
    original_bytes = (study / "manifest.json").read_bytes()
    original = json.loads(original_bytes)
    inputs, trials = {"origin-manifest.json": original_bytes}, []
    for case in cases:
        name = "fixtures/" + case["task_id"] + ".json"
        fixture = json.loads(safe_path(study, name).read_text())
        selected = [name, "fixtures/" + case["task_id"] + ".pkl", "fixtures/" + fixture["timeline_file"]]
        check_hashes(study, {n: original["files"][n] for n in selected})
        if (fixture["task_id"] != case["task_id"]
            or sha(fixture["command"].encode()) != fixture["command_sha256"]
            or original["files"][selected[1]] != fixture["snapshot_sha256"]
            or original["files"][selected[2]] != fixture["timeline_sha256"]):
            raise ValueError("Fixture identity/hash mismatch")
        for item in selected:
            inputs[item] = safe_path(study, item).read_bytes()
        trace_bytes = safe_path(cases_path.parent, case["trace_file"]).read_bytes()
        trace = validate_trace(json.loads(trace_bytes))
        if (case.get("fault") or {}).get("at_response", 0) >= len(trace):
            raise ValueError("Fault probe is outside the scripted trace")
        trace_name = "traces/" + case["trial_id"] + ".json"
        inputs[trace_name] = trace_bytes
        trials.append({**case, "trace_file": trace_name, "scheduler": profile.to_dict()["harness"]["scheduler"],
            "runtime": profile.runtime(), "watchdog_seconds": profile.watchdog_seconds})
    source_paths = [p for directory in ("agent4drone", "server") for p in (workspace / directory).rglob("*.py")
        if not any(part in {".venv", "tests", "__pycache__"} for part in p.relative_to(workspace).parts)]
    source_paths += [workspace / "scripts" / name for name in SCRIPTS]
    sources = {name: p.read_bytes() for name, p in agent_source_items(workspace, source_paths)}
    if sealed_sources:
        for name, content in sealed_sources.items():
            if name not in sources:
                raise ValueError("Unregistered source override: " + name)
            sources[name] = content
    protocol = (workspace / (EXECUTION_PROTOCOL if profile.execution_contract else PROTOCOL)).read_bytes()
    # Validate everything before creating the immutable attempt directory.
    output.mkdir(parents=True, exist_ok=False)
    for folder, blobs in (("source", sources), ("inputs", inputs)):
        for name, content in blobs.items():
            destination = safe_path(output / folder, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    (output / "PROTOCOL_CN.md").write_bytes(protocol)
    manifest = {"schema_version": 1, "scope": EXECUTION_SCOPE if profile.execution_contract else SCOPE, "online_authorized_executions": 0,
        "workspace": str(workspace), "study": str(output / "inputs"), "origin_study": str(study),
        "origin_cases_sha256": sha(cases_path.read_bytes()), "local_model": profile.local_model,
        "agent_profile": profile.to_dict(), "profile_sha256": profile.fingerprint,
        "sources": {n: sha(b) for n, b in sources.items()}, "inputs": {n: sha(b) for n, b in inputs.items()},
        "protocol_sha256": sha(protocol), "trials": trials,
        "private_canary": "PRIVATE_GENERAL_AGENT_V1_CHECKER_20261102",
        "python": sys.version, "python_executable": sys.executable,
        "evidence_scope": "known development fixtures + synthetic model replies; not natural-model results"}
    write(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return manifest


def load_manifest(run):
    run = Path(run).resolve()
    manifest_bytes = (run / "manifest.json").read_bytes()
    if sha(manifest_bytes) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    manifest = json.loads(manifest_bytes)
    if manifest["scope"] not in {SCOPE, EXECUTION_SCOPE} or type(manifest["online_authorized_executions"]) is not int or manifest["online_authorized_executions"] != 0:
        raise ValueError("This entry permits only local integration")
    profile = AgentProfile.from_dict(manifest["agent_profile"])
    if manifest["scope"] != (EXECUTION_SCOPE if profile.execution_contract else SCOPE):
        raise ValueError("Profile/scope execution contract mismatch")
    if profile.fingerprint != manifest["profile_sha256"] or manifest["local_model"] != profile.local_model:
        raise ValueError("Profile changed")
    if Path(manifest["study"]).resolve() != run / "inputs":
        raise ValueError("Inputs must belong to this frozen run")
    cases = [{k: v for k, v in t.items() if k not in {"runtime", "scheduler", "watchdog_seconds"}} for t in manifest["trials"]]
    validate_cases(cases, execution_contract=bool(profile.execution_contract))
    for trial in manifest["trials"]:
        if (trial["runtime"] != profile.runtime() or trial["watchdog_seconds"] != profile.watchdog_seconds
                or trial["scheduler"] != profile.to_dict()["harness"]["scheduler"]):
            raise ValueError("Runtime diverges from frozen profile")
        if trial["trace_file"] not in manifest["inputs"]:
            raise ValueError("Trace is not sealed")
        trace = validate_trace(json.loads(safe_path(run / "inputs", trial["trace_file"]).read_text()))
        if (trial.get("fault") or {}).get("at_response", 0) >= len(trace):
            raise ValueError("Fault probe is outside the scripted trace")
        fixture_name = "fixtures/" + trial["task_id"] + ".json"
        fixture = json.loads(safe_path(run / "inputs", fixture_name).read_text())
        required = {fixture_name, "fixtures/" + trial["task_id"] + ".pkl", "fixtures/" + fixture["timeline_file"]}
        if not required <= set(manifest["inputs"]):
            raise ValueError("Fixture dependencies are not sealed")
    check_hashes(run / "source", manifest["sources"])
    check_hashes(run / "inputs", manifest["inputs"])
    for directory, expected in (("source", manifest["sources"]), ("inputs", manifest["inputs"])):
        if any(p.is_symlink() for p in (run / directory).rglob("*")):
            raise ValueError("Symlink in frozen " + directory)
        actual = {str(p.relative_to(run / directory)) for p in (run / directory).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}
        if actual != set(expected):
            raise ValueError("Frozen " + directory + " inventory changed")
    if sha((run / "PROTOCOL_CN.md").read_bytes()) != manifest["protocol_sha256"]:
        raise ValueError("Protocol changed")
    return manifest


def launch(run, mode, trial, *, on_start=None):
    manifest = load_manifest(run)
    if mode not in {"worker", "score"} or trial not in manifest["trials"]:
        raise ValueError("Unprepared child request")
    packages = [p for component in ("agent4drone", "server") for p in sorted(
        (Path(manifest["workspace"]) / component / ".venv/lib").glob("python*/site-packages"))]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("UAV_", "LANGCHAIN_", "LANGSMITH_"))}
    env.update(PYTHONPATH=os.pathsep.join(map(str, [run / "source/scripts", run / "source/agent4drone", run / "source/server", *packages])),
        PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1", PYGAME_HIDE_SUPPORT_PROMPT="1")
    path = run / "process-logs" / (trial["trial_id"] + "-" + mode + ".log")
    started, started_ns = time.monotonic(), time.time_ns()
    killed = None
    with path.open("x") as stream:
        child = subprocess.Popen([sys.executable, str(run / "source/scripts/general_agent_local.py"),
            "--run", str(run), "--trial", trial["trial_id"], "--mode", mode],
            cwd=run / "source", env=env, stdout=stream, stderr=subprocess.STDOUT)
        try:
            if on_start is not None:
                on_start({"event": "start", "mode": mode, "trial_id": trial["trial_id"],
                    "pid": child.pid, "started_ns": started_ns})
            while child.poll() is None:
                poison = run / "trials" / trial["trial_id"] / "termination-required.json"
                if mode == "worker" and poison.exists():
                    try:
                        notice = json.loads(poison.read_text())
                    except (ValueError, OSError):
                        notice = {}
                    if (notice.get("trial_id"), notice.get("pid")) == (trial["trial_id"], child.pid):
                        killed = "transport_not_drained"
                if time.monotonic() - started >= trial["watchdog_seconds"]:
                    killed = "watchdog"
                if killed:
                    child.kill()
                    break
                time.sleep(0.02)
            returncode = child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    return {"mode": mode, "trial_id": trial["trial_id"], "pid": child.pid,
        "started_ns": started_ns, "ended_ns": time.time_ns(), "elapsed_seconds": time.monotonic() - started,
        "returncode": returncode, "exited": child.poll() is not None, "kill_reason": killed,
        "log_sha256": sha(path.read_bytes())}


def result_layers(run, trial, worker=None, score=None):
    """Evaluation only: never feed this combined record back to the Agent."""
    if worker is None:
        return {"run": {"status": "incomplete", "agent_returned_normally": None},
            "public_evidence": {"whole_task": "not_evaluated"},
            "independent_evaluation": {"status": "not_scored", "passed": None}}
    folder = run / "trials" / trial["trial_id"]
    state = json.loads((folder / "policy/task-state.json").read_text())
    agent = json.loads((folder / "policy/agent-result.json").read_text())
    stop = worker["limits"]["stop_reason"]
    status = "returned" if worker["agent"]["success"] else "error"
    if stop:
        status = "cancelled" if stop == "cancelled" else "timed_out" if stop in {"task_deadline", "request_timeout"} else "stopped"
    result = {"run": {"status": status, "worker_sealed": True,
                "agent_returned_normally": worker["agent"]["success"], "stop_reason": stop,
                "archive_complete": worker["agent"]["archive_verify"]["complete"]},
        "public_evidence": {"whole_task": "not_evaluated", "original_command": state["command"],
            "uninterpreted_requirements_policy": "retain full original command; clauses are not exhaustive",
            "agent_claim": agent.get("output"), "state_artifact": "policy/task-state.json",
            "requirements": state.get("task_evidence", {}).get("requirements", []),
            "aggregate_requirements": state.get("task_evidence", {}).get("aggregate_requirements", []),
            "observer_errors": state.get("observer_errors", []), "skill_artifact": "policy/harness.json"},
        "independent_evaluation": {"status": "scored" if score is not None else "not_scored",
            "passed": score["checker"]["passed"] if score is not None else None,
            "artifact": "evaluation/result.json" if score is not None else None}}
    execution_state = folder / "policy/execution-state.json"
    if execution_state.exists():
        state = json.loads(execution_state.read_text())
        result["public_evidence"].update(execution_state_artifact="policy/execution-state.json",
            recovery=state["recovery"], mission_completion=state["mission_completion"])
    if "completion_assessment" in agent:
        result["public_evidence"].update(completion_assessment=agent["completion_assessment"],
            task_completed=agent.get("task_completed"), model_output=agent.get("model_output"),
            completion_artifact="policy/agent-result.json")
    return result


def run_prepared(run):
    run = Path(run).resolve()
    manifest = load_manifest(run)
    # Execute only sealed coordinator code as well as sealed worker/scorer code.
    if ROOT != (run / "source").resolve():
        result = subprocess.run([sys.executable, str(run / "source/scripts/general_agent.py"), "run", "--run", str(run)])
        if result.returncode:
            raise RuntimeError("Frozen coordinator failed; inspect incomplete.json/process logs")
        return json.loads((run / "summary.json").read_text())
    if manifest["scope"] == EXECUTION_SCOPE:
        return run_execution_contract(run, manifest)
    for name in ("process-logs", "worker-exit-seals", "scorer-exit-seals"):
        (run / name).mkdir(exist_ok=True)
    rows = []
    # Exclusive journal creation rejects accidental resume/retry.
    with (run / "process-ledger.jsonl").open("x") as journal:
        def dispatch(mode, trial):
            process = launch(run, mode, trial)
            journal.write(json.dumps(process) + "\n"); journal.flush()
            return process
        try:
            for trial in manifest["trials"]:
                process = dispatch("worker", trial)
                row = {"trial_id": trial["trial_id"], "processes": [process], "worker": None, "score": None}
                if process["exited"] and process["returncode"] == 0 and not process["kill_reason"]:
                    worker = validate_worker(run, trial, process, manifest=manifest)
                    if worker["profile_sha256"] != manifest["profile_sha256"] or worker["network_attempts"] != []:
                        raise ValueError("Worker profile/network isolation failed")
                    scoring = dispatch("score", trial)
                    row["processes"].append(scoring)
                    score = validate_score(run, trial, worker, scoring)
                    if score["network_attempts"] != []:
                        raise ValueError("Scorer attempted network")
                    row.update(worker=worker, score=score)
                row["result"] = result_layers(run, trial, row["worker"], row["score"])
                rows.append(row)
                write(run / "results.json", rows)
                if row["worker"] is None:
                    raise RuntimeError("Incomplete worker; batch stopped without scoring or automatic retry")
            summary = {"schema_version": 1, "status": "completed", "profile_sha256": manifest["profile_sha256"],
                "scope": manifest["evidence_scope"], "provider_calls": 0, "online_ready": False,
                "worker_count": len(rows), "scorer_count": len(rows),
                "trials": [{"trial_id": row["trial_id"], **row["result"]} for row in rows]}
            write(run / "summary.json", summary)
            load_manifest(run)
        except BaseException as exc:
            write(run / "incomplete.json", {"error": type(exc).__name__, "reason": str(exc), "finished_cases": len(rows), "automatic_retry": False})
            raise
    write(run / "campaign-seal.json", {"files": {str(p.relative_to(run)): sha(p.read_bytes())
        for p in sorted(run.rglob("*")) if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}})
    return summary


def termination_decision(worker):
    """Call only after validate_worker has verified the joined child's seal."""
    from episode_termination import classify_episode_termination
    checked = copy.deepcopy(worker) if worker is not None else None
    if checked is not None:
        checked["termination_context"] = {"worker_exited": True, "exit_seal_verified": True}
    return classify_episode_termination(checked)


def run_execution_contract(run, manifest):
    """V2: a stopped episode and a stopped campaign have distinct meanings."""
    for name in ("process-logs", "worker-exit-seals", "scorer-exit-seals"):
        (run / name).mkdir(exist_ok=True)
    rows, reason, unexpected = [], None, None
    with (run / "process-ledger.jsonl").open("x") as journal:
        def persist(record):
            journal.write(json.dumps(record) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        def dispatch(mode, trial):
            process = launch(run, mode, trial, on_start=persist)
            persist({"event": "exit", **process})
            return process

        try:
            for trial in manifest["trials"]:
                process = dispatch("worker", trial)
                row = {"trial_id": trial["trial_id"], "processes": [process], "worker": None, "score": None}
                rows.append(row)
                if process["exited"] and process["returncode"] == 0 and not process["kill_reason"]:
                    worker = validate_worker(run, trial, process, manifest=manifest)
                    if worker["profile_sha256"] != manifest["profile_sha256"] or worker["network_attempts"] != []:
                        raise ValueError("Worker profile/network isolation failed")
                    row["worker"] = worker
                row["termination"] = termination_decision(row["worker"])
                if row["termination"]["score_eligible"]:
                    scoring = dispatch("score", trial)
                    row["processes"].append(scoring)
                    row["score"] = validate_score(run, trial, row["worker"], scoring)
                    if row["score"]["network_attempts"] != []:
                        raise ValueError("Scorer attempted network")
                row["result"] = result_layers(run, trial, row["worker"], row["score"])
                write(run / "results.json", rows)
                if row["termination"]["batch_action"] == "stop":
                    reason = row["termination"]["reason"]
                    break
        except BaseException as exc:
            reason, unexpected = type(exc).__name__ + ": " + str(exc), exc
            write(run / "incomplete.json", {"reason": reason, "automatic_retry": False})
    summary = {"schema_version": 2, "status": "stopped" if reason else "completed", "reason": reason,
        "profile_sha256": manifest["profile_sha256"], "scope": manifest["evidence_scope"],
        "provider_calls": 0, "online_ready": False, "planned_trials": len(manifest["trials"]),
        "worker_count": len(rows), "scorer_count": sum(row["score"] is not None for row in rows),
        "unstarted_trials": [t["trial_id"] for t in manifest["trials"][len(rows):]],
        "trials": [{"trial_id": r["trial_id"], "termination": r.get("termination"), **r.get("result", {})} for r in rows]}
    write(run / "results.json", rows)
    write(run / "summary.json", summary)
    load_manifest(run)
    write(run / "campaign-seal.json", {"files": {str(p.relative_to(run)): sha(p.read_bytes())
        for p in sorted(run.rglob("*")) if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}})
    if unexpected:
        raise unexpected
    return summary


def verify(run):
    run = Path(run).resolve()
    manifest = load_manifest(run)
    seal = json.loads((run / "campaign-seal.json").read_text())
    check_hashes(run, seal["files"])
    actual = {str(p.relative_to(run)) for p in run.rglob("*") if p.is_file()
        and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
    if actual != set(seal["files"]):
        raise ValueError("Run file inventory changed")
    rows = json.loads((run / "results.json").read_text())
    execution = manifest["scope"] == EXECUTION_SCOPE
    expected = manifest["trials"][:len(rows)] if execution else manifest["trials"]
    if [r["trial_id"] for r in rows] != [t["trial_id"] for t in expected]:
        raise ValueError("Results do not cover prepared cases")
    for trial, row in zip(manifest["trials"], rows):
        if row["result"] != result_layers(run, trial, row["worker"], row["score"]):
            raise ValueError("Result layers disagree with source records")
        if execution and row["termination"] != termination_decision(row["worker"]):
            raise ValueError("Episode termination decision changed")
    if execution:
        summary = json.loads((run / "summary.json").read_text())
        if summary["unstarted_trials"] != [t["trial_id"] for t in manifest["trials"][len(rows):]]:
            raise ValueError("Unstarted trial denominator changed")
        if len(rows) < len(manifest["trials"]) and (not rows or rows[-1]["termination"]["batch_action"] != "stop"):
            raise ValueError("Unexplained incomplete campaign")
    return {"valid": True, "sealed_files": len(seal["files"]), "trials": len(rows), "provider_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--profile", type=Path, default=ROOT / "experiments/general-agent-v1/profile.json")
    prep.add_argument("--study", type=Path, default=ROOT / "experiments/minimal-harness-full100-v1")
    prep.add_argument("--cases", type=Path, default=ROOT / "experiments/general-agent-v1/cases.json")
    prep.add_argument("--output", type=Path, required=True)
    for action in ("run", "verify"):
        sub.add_parser(action).add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        manifest = prepare(args.profile, args.study, args.cases, args.output)
        output = {"prepared": str(args.output.resolve()), "trials": len(manifest["trials"]), "profile_sha256": manifest["profile_sha256"]}
    elif args.action == "run":
        summary = run_prepared(args.run)
        output = {k: v for k, v in summary.items() if k != "trials"}
    else:
        output = verify(args.run)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
