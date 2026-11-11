"""Two local real-ASGI partial-recovery episodes with scripted model responses.

No provider or hidden-state policy input. The negative control omits recovery.
The public receipt resolver is a fixture helper, not a new Agent algorithm.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import base64
import json

from validate_harness_episode import workspace_source, sha, write, check_hashes


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
FIXTURES = ROOT / "experiments/harness-method-v1"
TASK_ID = "partial-route-task-v1"
A = {"x": 200, "y": 100, "z": 20}
B = {"x": 400, "y": 100, "z": 20}
MARKER = "[Execution recovery contract: advisory public evidence, not task completion]\n"
INPUT_NAMES = ("partial-scene.json", "partial-profile.json", "partial-cases.json",
               "partial-positive-trace.json", "partial-negative-trace.json", "partial-PROTOCOL_CN.md")


def dataset_from_scene(raw):
    scene = json.loads(raw)
    if scene["id"] != "partial-recovery-development-v1" or len(scene["tasks"]) != 1 or scene["tasks"][0]["id"] != TASK_ID:
        raise ValueError("Outside the fixed development partial fixture")
    task = scene["tasks"][0]
    name = "scenes/" + scene["id"] + ".json"
    return {"schema_version": 1, "phase": "development", "scope": "scripted_partial_recovery_contract_only",
        "online_authorized_executions": 0, "files": {name: sha(raw)},
        "scenes": [{"scene_id": scene["id"], "scene_file": name, "seed": 2026110306, "split": "development"}],
        "tasks": [{"task_id": TASK_ID, "scene_id": scene["id"], "scene_file": name, "task_family": "F2",
            "fleet_size": 1, "split": "development", "command": task["content"],
            "command_sha256": sha(task["content"].encode()), "original_task": task}]}, name


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def wire_requests(rows):
    result = []
    for row in rows:
        if row["kind"] != "http_request":
            continue
        body = row["data"]["body"]
        raw = base64.b64decode(body["base64"], validate=True)
        if len(raw) != body["length"] or sha(raw) != body["sha256"]:
            raise ValueError("Actual wire request hash mismatch")
        result.append(json.loads(raw))
    return result


def analyze_run(run):
    """Post-execution-only audit; hidden score data never reaches model input."""
    from general_agent import verify
    from general_agent_local import _public_receipt, partial_remaining_point
    verify(run)
    results = json.loads((run / "results.json").read_text())
    checks, reports = [], []
    def check(name, condition, **details):
        checks.append({"check": name, "passed": bool(condition), **details})
    check("fixed_two_predeclared_slots", [r["trial_id"] for r in results] == ["partial-positive", "partial-negative"])
    initial = []
    for result in results:
        trial = result["trial_id"]
        positive = trial == "partial-positive"
        folder = run / "trials" / trial
        worker = json.loads((folder / "worker-result.json").read_text())
        score = json.loads((folder / "evaluation/result.json").read_text())
        state = json.loads((folder / "policy/execution-state.json").read_text())
        api = jsonl(folder / "policy/worldlogs/public-api.jsonl")
        physical = jsonl(folder / "policy/worldlogs/physical-events.jsonl")
        archive = next((folder / "policy/modelio").glob("*.jsonl"))
        requests = wire_requests(jsonl(archive))
        initial.append(worker["initial"]["original_state_sha256"])
        partial = [row for row in api if row["endpoint"].endswith("/command/move_along_path")
                   and row.get("response", {}).get("status") == "partial_success"]
        check(trial + ":one_real_partial_api", len(partial) == 1)
        if len(partial) != 1:
            continue
        partial = partial[0]
        receipt = partial["response"]
        check(trial + ":faithful_point_partition", receipt.get("successful_points") == [[200.0, 100.0, 20.0]]
              and receipt.get("unsuccessful_points") == [[400.0, 100.0, 20.0]]
              and receipt.get("successful_points_count") == receipt.get("unsuccessful_points_count") == 1)
        prefix_events = [event for event in physical if event["request_id"] == partial["request_id"]
                         and event["length_xyz"] > 0 and event["history"].get("type") == "waypoint"]
        check(trial + ":actual_partial_effect_only_A", len(prefix_events) == 1
              and prefix_events[0]["history"]["start_position"] == {"x": 100, "y": 100, "z": 20}
              and prefix_events[0]["history"]["position"] == A and partial["committed"])
        actions = [row for row in state["actions"] if row["tool_name"] == "move_along_path"]
        check(trial + ":public_state_keeps_partial", len(actions) == 1 and actions[0]["effect_status"] == "partial"
              and actions[0]["applied"]["successful_points_count"] == 1
              and actions[0]["remaining"]["unsuccessful_points_count"] == 1
              and state["mission_completion"] == "not_evaluated")
        after_partial = requests[3]
        projection = json.loads(after_partial["messages"][0]["content"].split(MARKER, 1)[1])
        check(trial + ":wire_partial_advice", any(item["category"] == "partial_completion"
              and item["recommendation"] == "preserve_confirmed_effects_and_plan_only_remaining_work"
              for item in projection["items"]))
        after_status = requests[4]
        remainder = partial_remaining_point("Drone 1", after_status["messages"])
        check(trial + ":public_receipt_and_status_resolve_only_B", remainder == B)
        statuses = [row for row in api if row["request_id"] > partial["request_id"]
                    and row["method"] == "GET" and row["endpoint"].endswith("/drones/partial-drone-1")]
        check(trial + ":explicit_public_status_at_A", bool(statuses) and statuses[0]["response"]["position"] == A
              and statuses[0]["origin"].get("tool") == "get_drone_status")
        later_events = [event for event in physical if event["request_id"] > partial["request_id"]
                        and event["length_xyz"] > 0 and event["history"].get("type") == "waypoint"]
        check(trial + ":already_applied_A_not_replayed", not any(event["history"]["position"] == A for event in later_events))
        check(trial + ":takeoff_once", len([row for row in api if row["endpoint"].endswith("/command/take_off")]) == 1)
        if positive:
            starts = [row["data"] for row in jsonl(archive) if row["kind"] == "tool_start"
                      and row["data"]["input"].get("name") == "navigate_to"]
            params = json.loads(starts[0]["input"]["args"]["input_json"]) if len(starts) == 1 else {}
            check(trial + ":recovery_targets_only_remaining_B", len(starts) == 1
                  and {key: params.get(key) for key in ("x", "y", "z")} == B and "waypoints" not in params)
            check(trial + ":actual_recovery_from_A_to_B", bool(later_events)
                  and later_events[0]["history"]["start_position"] == A and later_events[-1]["history"]["position"] == B)
        else:
            check(trial + ":negative_omits_recovery", not later_events
                  and not any(row["endpoint"].endswith("/command/move_to") for row in api))
        expected = 4 if positive else 2
        check(trial + ":independent_expected_score", score["checker"]["passed"] is positive
              and score["checker"]["checks_passed"] == expected and score["checker"]["checks_expected"] == 4
              and score["pid"] != worker["pid"] and score["agent_imported"] is False)
        check(trial + ":normal_drained_isolated", worker["drained"] and worker["limits"]["stop_reason"] is None
              and worker["agent"]["success"] and worker["agent"]["archive_verify"]["complete"] and worker["network_attempts"] == []
              and worker["isolation"]["canary_absent"] and worker["isolation"]["checker_definitions_absent"])
        reports.append({"trial_id": trial, "model": "local-scripted-sdk", "provider_calls": 0,
            "archive_sha256": sha(archive.read_bytes()), "partial_request_id": partial["request_id"],
            "partial_receipt": receipt, "applied": actions[0]["applied"], "remaining": actions[0]["remaining"],
            "public_remaining_point": remainder, "model_http_requests": len(requests),
            "initial_state_sha256": initial[-1], "worker_pid": worker["pid"], "scorer_pid": score["pid"],
            "checker_passed": score["checker"]["passed"], "checks_passed": score["checker"]["checks_passed"],
            "agent_returned_normally": worker["agent"]["success"],
            "checks_expected": score["checker"]["checks_expected"], "positive_movement_events_after_partial": len(later_events),
            "script_helper_scope": "fixture_only_public_receipt_and_status_no_production_recovery_algorithm"})
    check("same_independently_restored_initial_state", len(initial) == 2 and len(set(initial)) == 1)
    check("distinct_worker_and_scorer_processes", len({p["pid"] for r in results for p in r["processes"]}) == 4)
    return {"schema": "harness-partial-recovery-local-v1", "passed": all(row["passed"] for row in checks),
            "provider_calls": 0, "teacher_examples": 0, "scene_split": "development", "tasks": 1,
            "planned_episodes": 2, "checks": checks, "episodes": reports,
            "scope": "real Agent SDK ASGI with scripted decisions; contract validation, not natural recovery capability"}


def run_validation(output):
    from harness_episode_local import block_network
    attempts = block_network()
    from prepare_task_dataset import materialize
    import general_agent
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Existing partial-recovery validation cannot be overwritten")
    inputs = {name: (FIXTURES / name).read_bytes() for name in INPUT_NAMES}
    source_names = ("scripts/general_agent_local.py", "scripts/validate_harness_partial_recovery.py",
                    "scripts/tests/test_harness_partial_recovery.py")
    sources = {name: workspace_source(ROOT, name).read_bytes() for name in source_names}
    output.mkdir(parents=True)
    for name, raw in {**{"experiments/harness-method-v1/" + n: b for n, b in inputs.items()}, **sources}.items():
        destination = output / "source" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    dataset, scene_name = dataset_from_scene(inputs["partial-scene.json"])
    scene_path = output / "dataset" / scene_name
    scene_path.parent.mkdir(parents=True)
    scene_path.write_bytes(inputs["partial-scene.json"])
    write(output / "dataset/manifest.json", dataset)
    (output / "dataset/manifest.sha256").write_text(sha((output / "dataset/manifest.json").read_bytes()) + "\n")
    materialize(output / "dataset", output / "study", timeline_seconds=31)
    preflight = json.loads((output / "study/preflight.json").read_text())
    if preflight["initially_satisfied"]:
        raise AssertionError("Partial recovery task was already complete initially")
    frozen = output / "source/experiments/harness-method-v1"
    general_agent.prepare(frozen / "partial-profile.json", output / "study", frozen / "partial-cases.json", output / "run-01")
    general_agent.run_prepared(output / "run-01")
    report = analyze_run(output / "run-01")
    report["preparation_network_attempts"] = attempts
    report["passed"] &= attempts == []
    write(output / "validation.json", report)
    for name, raw in sources.items():
        if workspace_source(ROOT, name).read_bytes() != raw:
            raise ValueError("Partial validation source drift")
    for name, raw in inputs.items():
        if (FIXTURES / name).read_bytes() != raw:
            raise ValueError("Partial fixture source drift")
    files = {str(path.relative_to(output)): sha(path.read_bytes()) for path in sorted(output.rglob("*"))
             if path.is_file() and "__pycache__" not in path.parts}
    write(output / "manifest.json", {"schema": "harness-partial-recovery-validation-package-v1", "provider_calls": 0,
        "teacher_examples": 0, "files": files, "source_hashes": {name: sha(raw) for name, raw in sources.items()},
        "passed": report["passed"], "python_executable": sys.executable})
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return report


def verify_package(output):
    output = Path(output).resolve()
    manifest_raw = (output / "manifest.json").read_bytes()
    if sha(manifest_raw) != (output / "manifest.sha256").read_text().strip():
        raise ValueError("Partial package manifest changed")
    manifest = json.loads(manifest_raw)
    check_hashes(output, manifest["files"])
    actual = {str(path.relative_to(output)) for path in output.rglob("*")
              if path.is_file() and "__pycache__" not in path.parts}
    if actual != set(manifest["files"]) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("Partial package inventory changed")
    report = analyze_run(output / "run-01")
    saved = json.loads((output / "validation.json").read_text())
    if {key: value for key, value in saved.items() if key != "preparation_network_attempts"} != report or saved["preparation_network_attempts"]:
        raise ValueError("Partial validation no longer matches sealed evidence")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = verify_package(args.output) if args.verify else run_validation(args.output)
    print(json.dumps({key: report[key] for key in ("passed", "planned_episodes", "tasks", "provider_calls")}, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
