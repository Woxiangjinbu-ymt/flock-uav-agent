#!/usr/bin/env python3
"""Offline replay of the user-stopped DeepSeek pilot; never contacts a service.

Run from any directory with controller/.venv/bin/python. The current
scripts/reproduction.py, scripts/reproduction_metrics.py and original controller
checker must match source.zip. Only the three named analysis outputs are written.
The sealed run stays immutable; interruption is not a scored model failure.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import reproduction as r
from reproduction_metrics import terminal_provider_errors

DEFAULT_ATTEMPT = ROOT / "experiments/runs/20261006-115922-harness-pilot-3f71f966"
DEFAULT_OUTPUT = ROOT / "docs/results/harness-pilot-deepseek-20261006"
BASELINE = ROOT / "experiments/subsets/representative-v1/baseline-tasks.json"
METHODS = ("b1", "harness-v2")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return str(path.resolve().relative_to(ROOT))


def verify_frozen_code(attempt):
    """Use exactly the frozen verifier, not the runner (which can write files)."""
    dependencies = ("scripts/reproduction.py", "scripts/reproduction_metrics.py",
                    "controller/check_ui/agent_checker.py")
    with zipfile.ZipFile(attempt / "source.zip") as archive:
        for name in dependencies:
            require(archive.read(name) == (ROOT / name).read_bytes(), f"Changed replay dependency: {name}")
        source = archive.read("scripts/harness_experiment.py").decode()
    parsed = ast.parse(source)
    function = next(node for node in parsed.body
                    if isinstance(node, ast.FunctionDef) and node.name == "verify_seal")
    namespace = {"r": r}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "frozen_verify_seal", "exec"), namespace)
    namespace["verify_seal"](attempt)
    return namespace["verify_seal"]


def verify_scored(attempt, method, scene, task, row, perf):
    directory = attempt / method / scene["id"]
    prefix = directory / task["id"]
    read = lambda suffix: r.read_json(Path(str(prefix) + f".{suffix}.json"))
    worker, barrier, window, checks = map(read, ("agent-state", "barrier", "window", "checks"))
    require(row["continuation_safe"] is True and row["world_quiescent"] is True,
            f"Unsafe scored row: {method}/{task['id']}")
    require(worker.get("worker_finished") is True and worker.get("busy") is False
            and worker["job_id"] == row["job_id"] == worker["job"]["job_id"], "Worker evidence mismatch")
    require(barrier.get("quiescent") is True and barrier.get("inflight") == 0
            and barrier.get("simulation_paused") is True
            and barrier["lease"] == window["lease"] and barrier["cutoff_at"] == window["cutoff_at"],
            "Frozen-world barrier mismatch")
    if row["agent_timed_out"]:
        require(Path(str(prefix) + ".timeout.json").is_file(), "Missing timeout evidence")
    leaves = [item for item in checks if item.get("type") == "leaf"]
    remaining = iter(leaves)

    class SavedAPI:
        def api_check_execution(self, endpoint, parameters, show_error=False):
            leaf = next(remaining)
            require(endpoint == leaf["endpoint"] and parameters == {**leaf["parameters"], "session_id": scene["id"]},
                    "Saved response does not match original request")
            return leaf["response"]

    passed, checked = r.upstream_evaluate(SavedAPI(), task["execution_check_apis"], scene["id"])
    actual = [item for item in checked if item.get("type") == "leaf"]
    require(not any(item.get("error") for item in checked), "Original checker replay error")
    require(passed == row["passed"] == row["task_pass"], "Task score mismatch")
    require(len(actual) == len(leaves) == row["expected_checks"], "Leaf count mismatch")
    pairs = lambda items: [(item.get("result"), item.get("actual")) for item in items]
    require(pairs(actual) == pairs(leaves) == pairs(row["leaves"]), "Leaf score mismatch")
    require(r.leaves(task["execution_check_apis"]) ==
            [{"path": leaf["path"], "definition": leaf["definition"]} for leaf in row["leaves"]],
            "Original checker definitions mismatch")
    result = worker["job"]["result"]
    expected_version = "b1-navigation-contract-v1" if method == "b1" else "evidence-repair-harness-v2"
    require(result["method_version"] == expected_version, "Wrong method identity")
    require(result["success"] == row["agent_success"] or
            (row["agent_timed_out"] is True and row["agent_success"] is None
             and worker["job"]["status"] == "cancelled" and result["success"] is False),
            "Self-report mismatch")
    if method == "harness-v2":
        for field in ("trace_file", "report_file"):
            path = Path(result[field])
            require(path.is_absolute() and path.resolve().is_relative_to(directory.resolve()) and path.is_file(),
                    f"Missing/unsafe {field}")
    job_perf = perf["by_job"][row["job_id"]]
    require(job_perf["llm"]["started_calls"] > 0, "Missing model-call telemetry")
    errors = terminal_provider_errors(directory / "metrics/reproduction-metrics.jsonl", row["job_id"])
    usage = row.get("observed_token_usage") or {}
    require(usage == job_perf["llm"]["usage"], "Usage does not match job telemetry")
    return {
        "status": "scored", "task_pass": passed,
        "deployment_pass": passed and not errors,
        "agent_success": result["success"], "row_agent_success": row["agent_success"],
        "agent_timed_out": row["agent_timed_out"],
        "completion_status": result.get("completion_status"),
        "false_completion": row["agent_success"] is True and not passed,
        "external_pass_self_not_success": passed and row["agent_success"] is not True,
        "terminal_provider_errors": len(errors), "http_errors": job_perf["http"]["errors"],
        "http_status_counts": job_perf["http"]["status_counts"],
        "window_seconds": barrier["frozen_at"] - window["opened_at"],
        "observed_total_tokens": usage.get("total_tokens"),
        "observed_prompt_tokens": usage.get("prompt_tokens"),
        "observed_completion_tokens": usage.get("completion_tokens"),
        "calls_with_usage": usage.get("calls_with_usage"),
        "passed_checks": sum(leaf["result"] is True for leaf in leaves),
        "expected_checks": row["expected_checks"], "worker_and_checker_verified": True,
        "evidence_directory": relative(directory),
    }


def summarize(rows, method):
    values = [row[method] for row in rows]
    scored = [value for value in values if value["status"] == "scored"]
    counters = Counter(value["status"] for value in values)
    numeric = lambda key: [value[key] for value in scored if isinstance(value.get(key), (int, float))]
    windows = numeric("window_seconds")
    return {
        "planned_tasks": len(values), "status_counts": dict(counters),
        "scored_tasks": len(scored), "task_passes": sum(value["task_pass"] for value in scored),
        "scored_only_pass_fraction": sum(value["task_pass"] for value in scored) / len(scored) if scored else None,
        "completed_run_success_rate": None,
        "deployment_passes": sum(value["deployment_pass"] for value in scored),
        "agent_self_successes": sum(value["agent_success"] is True for value in scored),
        "false_completions": sum(value["false_completion"] for value in scored),
        "external_pass_self_not_success": sum(value["external_pass_self_not_success"] for value in scored),
        "timed_out_tasks": sum(value["agent_timed_out"] is True for value in scored),
        "terminal_provider_error_tasks": sum(value["terminal_provider_errors"] > 0 for value in scored),
        "terminal_provider_errors": sum(value["terminal_provider_errors"] for value in scored),
        "http_errors": sum(value["http_errors"] for value in scored),
        "passed_checks": sum(value["passed_checks"] for value in scored),
        "scored_checks": sum(value["expected_checks"] for value in scored),
        "window_seconds": {"count": len(windows), "sum": sum(windows),
                           "median": statistics.median(windows) if windows else None},
        "observed_total_tokens": sum(numeric("observed_total_tokens")),
        "tasks_with_observed_tokens": len(numeric("observed_total_tokens")),
        "calls_with_usage": sum(numeric("calls_with_usage")),
    }


def paired(rows):
    common = [row for row in rows if all(row[method]["status"] == "scored" for method in METHODS)]
    pairs = Counter((row["b1"]["task_pass"], row["harness-v2"]["task_pass"]) for row in common)
    return {"common_scored_tasks": len(common), "both_pass": pairs[True, True],
            "both_fail": pairs[False, False], "regressions": pairs[True, False], "new_passes": pairs[False, True],
            "baseline_historical_passes": sum(row["b0_task_pass"] for row in common),
            "methods": {method: summarize(common, method) for method in METHODS}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, default=DEFAULT_ATTEMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    attempt, output = args.attempt.resolve(), args.output.resolve()
    require(not output.is_relative_to(attempt), "Analysis must not write into sealed evidence")
    verify = verify_frozen_code(attempt)
    suite = r.read_json(attempt / "suite.json")
    require(suite["status"] == "invalid" and suite.get("finished_at"), "Expected stopped, finalized suite")
    require(all(error["error"] == "InterruptedError: Experiment interrupted" for error in suite["errors"]),
            "Unexpected terminal error: inspect before attributing stop")
    plan = r.read_json(attempt / "plan.json")["plans"]["pilot"]
    baseline = {(row["session_id"], row["task_id"]): row for row in r.read_json(BASELINE)
                if row["model"] == "deepseek" and row["cohort"] == "first_attempt_no_replay"}
    results = {method: {(row["session_id"], row["task_id"]): row
                        for row in r.read_json(attempt / method / "results.json")} for method in METHODS}
    require(sha(r.ZIP) == plan["benchmark_sha256"], "Benchmark archive changed")
    rows, scene_hashes = [], {}
    with zipfile.ZipFile(r.ZIP) as benchmark:
        for scene in plan["sessions"]:
            payload = benchmark.read(Path(scene["file"]).name)
            scene_hashes[scene["id"]] = hashlib.sha256(payload).hexdigest()
            require(scene_hashes[scene["id"]] == scene["sha256"], "Scene changed")
            tasks = {task["id"]: task for task in json.loads(payload)["tasks"]}
            for index, selected in enumerate(scene["tasks"], 1):
                key = scene["id"], selected["id"]
                task, old = tasks[key[1]], baseline[key]
                require(old["worker_confirmed"] is True and not old["provider_errors"], "Invalid B0 reference")
                require(r.leaves(task["execution_check_apis"]) ==
                        [{"path": leaf["path"], "definition": leaf["definition"]} for leaf in old["original_leaves"]],
                        "B0 checker differs")
                row = {"session_id": key[0], "scene_name": scene["name"], "family": scene["family"],
                       "task_index": index, "task_id": key[1], "task_name": selected["name"],
                       "difficulty": selected["difficulty"], "b0_task_pass": old["task_pass"],
                       "b0_deployment_pass": old["deployment_pass"], "b0_historical_evidence": old["evidence_dir"]}
                for method in METHODS:
                    result = results[method][key]
                    directory = attempt / method / key[0]
                    if result["status"] == "scored":
                        row[method] = verify_scored(attempt, method, scene, task, result,
                                                  r.read_json(directory / "performance.json"))
                    elif result["status"] == "invalid":
                        require(r.read_json(directory / "scene.json")["error"] == "InterruptedError: Experiment interrupted",
                                "Invalid row is not associated with interruption")
                        require((directory / f"{key[1]}.job.json").is_file()
                                and not (directory / f"{key[1]}.checks.json").exists(), "Ambiguous interrupted task")
                        row[method] = {"status": "user_interrupted", "raw_status": "invalid", "task_pass": None}
                    else:
                        require(result["status"] == "not_run", "Unexpected row status")
                        row[method] = {"status": "not_run", "task_pass": None}
                rows.append(row)
    require(len(rows) == plan["expected_tasks"] == 60, "Wrong frozen denominator")
    require(all(len(results[method]) == 60 for method in METHODS), "Extra/missing result rows")
    summary = {"schema_version": 1, "attempt": relative(attempt), "suite_status": suite["status"],
               "finished_at": suite["finished_at"], "protocol": suite["protocol"], "model": suite["model"],
               "git_head": suite["git_head"], "completed_experiment": False,
               "stop_context": "User explicitly requested stop; persisted scene errors are InterruptedError.",
               "denominator_policy": "60 planned tasks per arm remain listed. User-interrupted and not-run tasks are neither successes nor observed model failures. Scored-only fractions describe incomplete prefixes, not final 60-task success rates.",
               "comparison_limits": "Common scored tasks share one scene and original task order; not independent samples. Later world state can differ across methods. B0 is historical, not concurrently rerun.",
               "duration_definition": "barrier.frozen_at - window.opened_at; includes polling/drain, excludes preparation and worker exit wait",
               "token_definition": "Observed job usage only; no token/cost imputation; incomplete tasks excluded from scored summaries",
               "verification": {"frozen_verify_seal": "passed", "sealed_files": len(r.read_json(attempt / "suite-seal.json")["files"]),
                                "scored_worker_barrier_checker_replays": sum(row[method]["status"] == "scored" for row in rows for method in METHODS),
                                "baseline_checker_definitions_matched": len(rows), "scene_sha256": scene_hashes,
                                "suite_audits": suite["audits"]},
               "methods": {method: summarize(rows, method) for method in METHODS},
               "baseline_historical": {"planned_tasks": 60, "task_passes": sum(row["b0_task_pass"] for row in rows)},
               "paired": paired(rows)}
    for group in ("session_id", "family", "difficulty"):
        groups = defaultdict(list)
        for row in rows:
            groups[row[group]].append(row)
        summary["by_" + group] = {name: {"methods": {method: summarize(items, method) for method in METHODS},
                                               "baseline_historical_passes": sum(row["b0_task_pass"] for row in items),
                                               "paired": paired(items)} for name, items in groups.items()}
    flat = []
    for row in rows:
        record = {key: value for key, value in row.items() if key not in METHODS}
        for method in METHODS:
            for key, value in row[method].items():
                record[method + "_" + key] = json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
        record["common_scored"] = all(row[method]["status"] == "scored" for method in METHODS)
        record["paired_outcome"] = ({(True, True): "both_pass", (False, False): "both_fail",
                                      (True, False): "regression", (False, True): "new_pass"}
                                     [row["b1"]["task_pass"], row["harness-v2"]["task_pass"]]
                                     if record["common_scored"] else "not_comparable_incomplete")
        flat.append(record)
    verify(attempt)
    inputs = {relative(attempt / name): digest for name, digest in r.read_json(attempt / "suite-seal.json")["files"].items()}
    for path in (attempt / "suite-seal.json", BASELINE, r.ZIP, Path(__file__)):
        inputs[relative(path)] = sha(path)
    # Include all local Python modules loaded by the upstream checker import.
    for module in list(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name:
            path = Path(name).resolve()
            if (path.is_file() and path.is_relative_to(ROOT) and path.suffix == ".py"
                    and not {".venv", ".runtimes"}.intersection(path.parts)):
                inputs[relative(path)] = sha(path)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    fields = list(dict.fromkeys(key for row in flat for key in row))
    with (output / "paired-tasks.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(flat)
    (output / "input-sha256.json").write_text(json.dumps({"algorithm": "sha256", "paths_relative_to": "repository root",
                                                        "files": dict(sorted(inputs.items()))}, indent=2) + "\n")
    print(json.dumps({"verified_scored": summary["verification"]["scored_worker_barrier_checker_replays"],
                      "common_scored": summary["paired"]["common_scored_tasks"],
                      "b0_common_passes": summary["paired"]["baseline_historical_passes"],
                      "b1_common_passes": summary["paired"]["methods"]["b1"]["task_passes"],
                      "harness_common_passes": summary["paired"]["methods"]["harness-v2"]["task_passes"],
                      "csv_rows": len(flat), "output": relative(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
