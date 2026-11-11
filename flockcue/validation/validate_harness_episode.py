"""Bounded local episode acceptance: a fresh worker and scorer per trial.

No provider configuration, network model calls, training, or old campaign queue.
This coordinator is deliberately standard-library-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import time

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
TASK_ID = "b1eb0270"
CANARY = "EPISODE_PRIVATE_SCORE_ONLY_20261025_5bcd8129"
BUDGET = {"max_iterations": 600, "recursion_limit": 1800, "deadline_seconds": 500,
          "api_calls_per_plan": 2000, "repair_budget": 16, "search_rounds": 16}
TRIALS = [{"trial_id": "reference-B", "condition": "success", "role": "reference"}]
for _condition in ("success", "failure", "timeout", "cancel"):
    TRIALS.extend([
        {"trial_id": _condition + "-A", "condition": _condition, "role": "A"},
        {"trial_id": _condition + "-B", "condition": "success", "role": "B",
         "predecessor": _condition + "-A"}])


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def safe_path(root, relative):
    root, path = Path(root).resolve(), Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Non-local artifact path")
    candidate = root / path
    for part in (candidate, *candidate.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("Symlink in artifact path")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Artifact path escapes its root")
    return candidate


def workspace_source(workspace, name):
    """Read a live component or the flat source in a sealed, standalone bundle."""
    workspace = Path(workspace)
    if workspace == ROOT and (workspace / "compat/agent4drone").is_dir():
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from flockcue.history import source_path
        return source_path(workspace, name)
    return workspace / name


def agent_source_items(workspace, paths):
    """Yield the published source names together with their physical files."""
    workspace = Path(workspace).resolve()
    logical = {p.relative_to(workspace).as_posix() for p in paths}
    if workspace == ROOT:
        archive = workspace / "compat/agent4drone"
        logical.update("agent4drone/" + p.name for p in archive.glob("*.py"))
    for name in sorted(logical):
        yield name, workspace_source(workspace, name)


def check_hashes(root, expected):
    for name, checksum in expected.items():
        path = safe_path(root, name)
        if not path.is_file() or sha(path.read_bytes()) != checksum:
            raise ValueError("Artifact hash mismatch: " + name)


def load_manifest(run):
    path = run / "manifest.json"
    if sha(path.read_bytes()) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    manifest = json.loads(path.read_text())
    if manifest["trials"] != TRIALS or manifest["budget"] != BUDGET or manifest["fixture_id"] != TASK_ID:
        raise ValueError("Outside the fixed local protocol")
    check_hashes(run / "source", manifest["sources"])
    check_hashes(Path(manifest["study"]), manifest["inputs"])
    if sha((run / "PROTOCOL_CN.md").read_bytes()) != manifest["protocol_sha256"]:
        raise ValueError("Protocol changed")
    return manifest


def prepare(workspace, output):
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Existing attempt must not be overwritten")
    study = workspace / "experiments/minimal-harness-full100-v1"
    original = json.loads((study / "manifest.json").read_text())
    check_hashes(study, original["files"])
    name = f"fixtures/{TASK_ID}.json"
    fixture = json.loads((study / name).read_text())
    selected = [name, f"fixtures/{TASK_ID}.pkl", "fixtures/" + fixture["timeline_file"]]
    inputs = {name: original["files"][name] for name in selected}
    inputs["manifest.json"] = sha((study / "manifest.json").read_bytes())
    assert inputs[f"fixtures/{TASK_ID}.pkl"] == fixture["snapshot_sha256"]
    assert inputs["fixtures/" + fixture["timeline_file"]] == fixture["timeline_sha256"]
    assert sha(fixture["command"].encode()) == fixture["command_sha256"]
    assert sha(safe_path(workspace, fixture["source"]).read_bytes()) == fixture["source_sha256"]
    protocol = workspace / "docs/results/harness-full-episode-20261025/PROTOCOL_CN.md"
    protocol_bytes = protocol.read_bytes()
    source_paths = []
    for directory in ("agent4drone", "server"):
        source_paths.extend(p for p in (workspace / directory).rglob("*.py")
                            if not any(part in {".venv", "__pycache__", "tests"}
                                       for part in p.relative_to(workspace).parts))
    source_paths.extend(workspace / "scripts" / name for name in (
        "validate_harness_episode.py", "harness_episode_local.py", "harness_full100_world.py",
        "harness_full100_online.py", "harness_v2_world.py", "harness_v2_online.py"))
    # Read every source before creating an attempt, so a missing dependency does
    # not leave an apparently prepared campaign that can be silently resumed.
    source_bytes = {name: p.read_bytes() for name, p in agent_source_items(workspace, source_paths)}
    output.mkdir(parents=True, exist_ok=False)
    for name, content in source_bytes.items():
        path = output / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (output / "PROTOCOL_CN.md").write_bytes(protocol_bytes)
    manifest = {"schema_version": 1, "created_unix": time.time(), "workspace": str(workspace),
        "study": str(study), "fixture_id": TASK_ID, "private_canary": CANARY,
        "sources": {n: sha(b) for n, b in source_bytes.items()}, "inputs": inputs,
        "original_manifest_files": len(original["files"]), "trials": TRIALS,
        "budget": BUDGET, "protocol_sha256": sha(protocol_bytes),
        "model": "local scripted OpenAI-compatible MockTransport; not a provider",
        "virtual_clock": "fixed anchor plus controlled offset; deadline remains 500 seconds",
        "watchdog_seconds": 60, "python": sys.version, "python_executable": sys.executable,
        "scope": "nine local lifecycle trials, not benchmark success rate or training"}
    write(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return manifest


def launch(run, mode, trial_id):
    manifest = load_manifest(run)
    if mode not in {"worker", "score"} or trial_id not in {t["trial_id"] for t in TRIALS}:
        raise ValueError("Unknown child request")
    log = run / "process-logs" / f"{trial_id}-{mode}.log"
    log.parent.mkdir(exist_ok=True)
    env = dict(os.environ)
    # Source files are frozen; only installed third-party packages come from the
    # existing local environments. No .env or provider settings are read here.
    packages = [p for component in ("agent4drone", "server")
                for p in sorted((Path(manifest["workspace"]) / component / ".venv/lib").glob("python*/site-packages"))]
    env["PYTHONPATH"] = os.pathsep.join(map(str, [run / "source/scripts", run / "source/agent4drone",
        run / "source/server", *packages]))
    env.update(PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    for key in ("UAV_MODEL_IO_ARCHIVE", "UAV_MODEL_IO_ARCHIVE_DIR", "UAV_TASK_PROGRESS_MODE",
                "UAV_HARNESS_REVISION", "UAV_HARNESS_STATIC_OBSTACLES", "UAV_HARNESS_SCHEDULER"):
        env.pop(key, None)
    command = [sys.executable, str(run / "source/scripts/harness_episode_local.py"),
               "--run", str(run), "--trial", trial_id, "--mode", mode]
    started = time.time_ns()
    timeout = False
    with log.open("x") as stream:
        process = subprocess.Popen(command, cwd=run / "source", env=env,
                                   stdout=stream, stderr=subprocess.STDOUT)
        try:
            returncode = process.wait(timeout=manifest["watchdog_seconds"])
        except subprocess.TimeoutExpired:
            timeout = True
            process.kill()
            returncode = process.wait(timeout=10)
    return {"mode": mode, "trial_id": trial_id, "pid": process.pid,
        "started_ns": started, "ended_ns": time.time_ns(), "returncode": returncode,
        "exited": process.poll() is not None, "watchdog_timeout": timeout,
        "log_sha256": sha(log.read_bytes())}


def policy_scan(paths, canary):
    """Inspect structured records and actual bytes, including split responses."""
    clean_canary, clean_checks = True, True
    chunks = {}

    def inspect(value):
        nonlocal clean_canary, clean_checks
        if isinstance(value, bytes):
            clean_canary &= canary.encode() not in value
            text = value.decode("utf-8", errors="replace")
            try:
                inspect(json.JSONDecoder().raw_decode(text.lstrip())[0])
            except (ValueError, RecursionError):
                pass
        elif isinstance(value, str):
            inspect(value.encode())
        elif isinstance(value, dict):
            for key in ("execution_check_apis", "related_apis"):
                if value.get(key) not in (None, [], {}):
                    clean_checks = False
            if {"base64", "sha256", "length"}.issubset(value):
                body = base64.b64decode(value["base64"], validate=True)
                if sha(body) != value["sha256"] or len(body) != value["length"]:
                    raise ValueError("Invalid archived bytes")
                inspect(body)
            for item in value.values():
                # Base64 is not itself a model-visible string.
                if isinstance(item, (dict, list)):
                    inspect(item)
                elif isinstance(item, str) and item != value.get("base64"):
                    inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)

    for path in paths:
        raw = Path(path).read_bytes()
        clean_canary &= canary.encode() not in raw
        if Path(path).suffix == ".jsonl":
            for line in raw.splitlines():
                row = json.loads(line)
                if row.get("kind") in {"http_request", "http_response_chunk"} and not isinstance(row.get("data", {}).get("body"), dict):
                    raise ValueError("Missing actual wire body")
                inspect(row)
                if row.get("kind") == "http_response_chunk":
                    data = row["data"]
                    key = (str(path), row["archive_id"], data["attempt_id"])
                    chunks.setdefault(key, bytearray()).extend(base64.b64decode(data["body"]["base64"], validate=True))
        elif Path(path).suffix == ".json":
            inspect(json.loads(raw))
        else:
            inspect(raw)
    for value in chunks.values():
        inspect(bytes(value))
    return {"canary_absent": bool(clean_canary), "checker_definitions_absent": bool(clean_checks)}


def validate_worker(run, trial, process):
    if not process["exited"] or process["returncode"] != 0 or process.get("watchdog_timeout"):
        raise ValueError("Worker did not exit cleanly")
    folder = run / "trials" / trial["trial_id"]
    result = json.loads((folder / "worker-result.json").read_text())
    if (result["status"], result["trial_id"], result["condition"], result["pid"]) != (
            "completed", trial["trial_id"], trial["condition"], process["pid"]):
        raise ValueError("Worker identity/status mismatch")
    isolation = result["isolation"]
    required = ("late_world_rejected", "late_archive_unchanged", "private_endpoint_denied",
                "policy_canary_absent", "policy_checker_definition_absent")
    if (result["budget"] != BUDGET or any(isolation.get(key) is not True for key in required)
            or isolation.get("canary_violations") or isolation.get("checker_definition_violations")
            or result.get("network_attempts")):
        raise ValueError("Worker isolation/budget failed")
    agent = result["agent"]
    if (not agent["harness_closed"] or not agent["archive_info"]["closed"]
            or not agent["archive_verify"]["valid"]
            or agent["archive_info"]["complete"] != agent["archive_verify"]["complete"]):
        raise ValueError("Agent/archive lifecycle validation failed")
    check_hashes(folder, result["files"])
    snapshot = safe_path(folder, result["snapshot"]["path"])
    if not snapshot.is_relative_to(folder / "private") or sha(snapshot.read_bytes()) != result["snapshot"]["sha256"]:
        raise ValueError("Untrusted scoring snapshot")
    expected = {str(p.relative_to(folder)) for p in folder.rglob("*")
                if p.is_file() and str(p.relative_to(folder)) not in
                    {"worker-result.json", "private/worker-console.log"}}
    if expected != set(result["files"]):
        raise ValueError("Incomplete worker file manifest")
    policy = [p for p in (folder / "policy").rglob("*") if p.is_file()]
    scan = policy_scan(policy, load_manifest(run)["private_canary"])
    if not policy or not all(scan.values()):
        raise ValueError("Private scoring leaked into policy evidence")
    validate_wire(folder, result)
    validate_boundary(folder, result)
    # A live worker cannot seal its still-open console reliably. Only the parent,
    # after wait() confirms exit, freezes every file including that final console.
    write(run / "worker-exit-seals" / (trial["trial_id"] + ".json"), {
        str(p.relative_to(folder)): sha(p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()})
    return result


def validate_boundary(folder, worker):
    rows = list(map(json.loads, (folder / "policy/worldlogs/public-api.jsonl").read_text().splitlines()))
    if not rows or any(row["phase"] != "task" or row["admin"] for row in rows):
        raise ValueError("Evaluator observations in policy API log")
    actions = [row for row in rows if row["action"]]
    condition = worker["condition"]
    expected = 3 if condition == "success" else 1
    if (len(actions) != expected or any(not row["committed"] or row.get("error")
            or (row.get("response") or {}).get("status") != "success" for row in actions)):
        raise ValueError("Expected physical actions were not committed successfully")
    if (actions[0]["origin"]["tool"] != "take_off" or not actions[0]["history_events"]
            or worker["physical_cost"]["vertical_distance"] <= 0):
        raise ValueError("No physical takeoff effect before termination")
    if condition == "success" and not worker["agent"]["archive_verify"]["complete"]:
        raise ValueError("Successful case requires complete model archive")
    if condition in {"timeout", "cancel"}:
        terminal = worker["terminal"]
        reason = "deadline" if condition == "timeout" else "cancelled"
        if (terminal["triggered"] is not True or terminal["trigger_request_id"] != actions[0]["request_id"]
                or terminal["frame_status"] != "interrupted" or terminal["frame_reason"] != reason):
            raise ValueError("Requested terminal boundary was not actually exercised")
        if condition == "timeout" and terminal["virtual_elapsed"] != BUDGET["deadline_seconds"]:
            raise ValueError("Timeout did not use the original deadline")
        if condition == "cancel" and not terminal["cancel_flag"]:
            raise ValueError("Cancellation signal was not set")
    oracle = json.loads((folder / "policy/transport-oracle.json").read_text())
    expected_codes = [200, 200, 500] if condition == "failure" else ([200, 200, 200] if condition == "success" else [200, 200])
    if [row["status_code"] for row in oracle["responses"]] != expected_codes:
        raise ValueError("Unexpected model calls or fault boundary")


def validate_wire(folder, worker):
    """Compare independent mock-transport byte hashes with the archived wire."""
    archive = Path(worker["agent"]["archive_info"]["path"])
    if not archive.resolve().is_relative_to((folder / "policy").resolve()):
        raise ValueError("Archive outside policy directory")
    rows = list(map(json.loads, archive.read_text().splitlines()))
    requests, responses, response_order = [], {}, []
    for row in rows:
        data = row["data"]
        if row["kind"] == "http_request":
            blob = data["body"]
            if blob is None:
                raise ValueError("Missing actual request body")
            raw = base64.b64decode(blob["base64"], validate=True)
            requests.append({"length": len(raw), "sha256": sha(raw)})
        elif row["kind"] == "http_response_start":
            key = data["attempt_id"]
            if key in responses:
                raise ValueError("Duplicate transport response")
            response_order.append(key)
            responses[key] = {"status_code": data["status_code"], "body": bytearray(), "ended": False}
        elif row["kind"] == "http_response_chunk":
            responses[data["attempt_id"]]["body"].extend(base64.b64decode(data["body"]["base64"], validate=True))
        elif row["kind"] == "http_response_end":
            item = responses[data["attempt_id"]]
            if not data["complete"] or item["ended"]:
                raise ValueError("Incomplete/duplicate local response")
            item["ended"] = True
    oracle = json.loads((folder / "policy/transport-oracle.json").read_text())
    observed = [{"status_code": responses[key]["status_code"], "length": len(responses[key]["body"]),
                 "sha256": sha(responses[key]["body"])} for key in response_order]
    if (not requests or requests != oracle["requests"] or observed != oracle["responses"]
            or len(requests) != len(observed) or not all(item["ended"] for item in responses.values())):
        raise ValueError("Wire archive differs from actual transport bytes")


def validate_score(run, trial, worker, process):
    if not process["exited"] or process["returncode"] != 0 or process.get("watchdog_timeout"):
        raise ValueError("Scorer did not exit cleanly")
    folder = run / "trials" / trial["trial_id"]
    result = json.loads((folder / "evaluation/result.json").read_text())
    if (result["trial_id"], result["pid"]) != (trial["trial_id"], process["pid"]):
        raise ValueError("Scorer identity mismatch")
    if (result["worker_result_sha256"] != sha((folder / "worker-result.json").read_bytes())
            or result["snapshot_sha256"] != worker["snapshot"]["sha256"]):
        raise ValueError("Scorer evaluated another worker/snapshot")
    if (not result["private_canary_present"] or not result["checker_tree_matches_original"]
            or result["agent_imported"] or result.get("network_attempts")):
        raise ValueError("Scorer separation/positive control failed")
    if result["checker"]["errors"]:
        raise ValueError("Original checker failed to execute")
    check_hashes(folder, worker["files"])
    check_hashes(folder, json.loads((run / "worker-exit-seals" / (trial["trial_id"] + ".json")).read_text()))
    write(run / "scorer-exit-seals" / (trial["trial_id"] + ".json"), {
        str(p.relative_to(folder)): sha(p.read_bytes()) for p in sorted((folder / "evaluation").rglob("*")) if p.is_file()})
    return result


def _model_inputs(run, worker):
    folder = run / "trials" / worker["trial_id"]
    archive = Path(worker["agent"]["archive_info"]["path"])
    if not archive.resolve().is_relative_to((folder / "policy").resolve()):
        raise ValueError("Archive outside policy directory")
    harness = json.loads((folder / "policy/harness.json").read_text())
    substitutions = {worker["entry"]["command_id"]: "<COMMAND>"}
    for index, frame in enumerate(harness["frames"]):
        substitutions[frame["plan_id"]] = f"<PLAN-{index}>"
    values = []
    for row in map(json.loads, archive.read_text().splitlines()):
        if row["kind"] == "http_request":
            data = base64.b64decode(row["data"]["body"]["base64"], validate=True).decode()
            for old, new in substitutions.items():
                data = data.replace(old, new)
            values.append(json.loads(data))
    return values


def summarize(run, manifest, workers, scores, processes):
    by_id = {w["trial_id"]: w for w in workers}
    scored = {s["trial_id"]: s for s in scores}
    reference = by_id["reference-B"]
    reference_input = _model_inputs(run, reference)
    checks = {"nine_workers_and_scorers": len(workers) == len(scores) == 9,
        "distinct_worker_processes": len({w["pid"] for w in workers}) == 9,
        "fresh_instance_nonces": len({w["instance_nonce"] for w in workers}) == 9,
        "separate_scoring_processes": len({p["pid"] for p in processes}) == 18,
        "strict_join_before_next": all(a["ended_ns"] <= b["started_ns"] for a, b in zip(processes, processes[1:]))}
    for trial in manifest["trials"]:
        ident = trial["trial_id"]
        worker, score = by_id[ident], scored[ident]
        checks[ident + ":same_initial"] = worker["initial"] == reference["initial"]
        checks[ident + ":expected_checker"] = score["checker"]["passed"] == (trial["condition"] == "success")
        checks[ident + ":physical_effect_before_end"] = worker["physical_cost"]["action_attempts"] >= 1
        checks[ident + ":state_changed"] = worker["snapshot"]["state_sha256"] != worker["initial"]["derived_state_sha256"]
        checks[ident + ":closed"] = worker["agent"]["harness_closed"] and worker["agent"]["archive_info"]["closed"]
        expected_actions = 3 if trial["condition"] == "success" else 1
        checks[ident + ":bounded_actions"] = (len(worker["action_signature"]) == expected_actions
            and worker["physical_cost"]["action_attempts"] == expected_actions)
        checks[ident + ":agent_terminal"] = worker["agent"]["success"] == (trial["condition"] == "success")
        if trial["condition"] in {"timeout", "cancel"}:
            checks[ident + ":triggered_after_action"] = worker["terminal"]["triggered"] is True
        checks[ident + ":within_budget"] = worker["terminal"]["virtual_elapsed"] <= BUDGET["deadline_seconds"]
        if trial["condition"] == "timeout":
            checks[ident + ":original_deadline"] = worker["terminal"]["virtual_elapsed"] == BUDGET["deadline_seconds"]
        check_hashes(run / "trials" / ident, worker["files"])
        for kind in ("worker", "scorer"):
            check_hashes(run / "trials" / ident, json.loads((run / (kind + "-exit-seals") / (ident + ".json")).read_text()))
        if trial["role"] == "B":
            checks[ident + ":model_inputs_match_reference"] = _model_inputs(run, worker) == reference_input
            checks[ident + ":actions_match_reference"] = worker["action_signature"] == reference["action_signature"]
            checks[ident + ":score_matches_reference"] = score["checker"] == scored["reference-B"]["checker"]
            checks[ident + ":entry_clean"] = (worker["entry"]["board"] == reference["entry"]["board"]
                and worker["entry"]["ledger_event_count"] == 0 and not worker["entry"]["harness_frames"])
    failed = [key for key, value in checks.items() if not value]
    return {"status": "passed" if not failed else "failed", "check_count": len(checks),
        "failed_checks": failed, "checks": checks, "trials": [
            {"trial_id": w["trial_id"], "condition": w["condition"], "pid": w["pid"],
             "agent_success": w["agent"]["success"], "checker_passed": scored[w["trial_id"]]["checker"]["passed"],
             "archive_complete": w["agent"]["archive_info"]["complete"]} for w in workers],
        "scope": "predeclared local fault/lifecycle cases, not a benchmark success rate"}


def run_prepared(run, launcher=launch):
    run = Path(run).resolve()
    manifest = load_manifest(run)
    journal = run / "process-ledger.jsonl"
    if journal.exists() or (run / "results.json").exists():
        raise ValueError("Existing attempt must not be resumed/retried")
    workers, scores, processes = [], [], []
    with journal.open("x") as stream:
        try:
            for trial in manifest["trials"]:
                for mode in ("worker", "score"):
                    process = launcher(run, mode, trial["trial_id"])
                    processes.append(process)
                    stream.write(json.dumps(process) + "\n"); stream.flush()
                    if mode == "worker":
                        workers.append(validate_worker(run, trial, process))
                    else:
                        scores.append(validate_score(run, trial, workers[-1], process))
                    write(run / "results.json", {"workers": workers, "scores": scores})
            load_manifest(run)
            summary = summarize(run, manifest, workers, scores, processes)
            write(run / "summary.json", summary)
        except Exception as exc:
            write(run / "incomplete.json", {"status": "incomplete", "error_type": type(exc).__name__,
                "reason": str(exc), "workers_completed": len(workers), "scores_completed": len(scores),
                "process_count": len(processes), "automatic_retry": False})
            raise
    write(run / "campaign-seal.json", {"status": summary["status"], "files": {
        str(p.relative_to(run)): sha(p.read_bytes()) for p in sorted(run.rglob("*"))
        if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.workspace, args.output)
    summary = run_prepared(args.output)
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(0 if summary["status"] == "passed" else 1)
