#!/usr/bin/env python3
"""Audit and summarize four first-scored valid-route R2/R3 attempts."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "experiments/r3-valid-route-v1"
OUT = ROOT / "docs/results/r3-valid-route-20261009/summary.json"
EXPECTED_CHANGED = ["agent4drone/task_evidence_r3.py",
                    "agent4drone/tests/test_task_evidence_r3.py", "agent4drone/uav_agent.py"]


def read(path: Path):
    return json.loads(path.read_text())


def route_points(scene: dict) -> dict[str, list[list[float]]]:
    result: dict[str, list[list[float]]] = {}
    for api in scene["tasks"][0]["related_apis"]:
        if api["endpoint"].endswith("/command/move_to"):
            parameters = api["parameters"]
            result.setdefault(parameters["id"], []).append([parameters[axis] for axis in ("x", "y", "z")])
    if len(result) != 2 or any(len(points) != 12 for points in result.values()):
        raise ValueError("Task route definition changed")
    return result


def actual_route(trace: dict, actor_ids: set[str]) -> dict[str, list[list[float]]]:
    result: dict[str, list[list[float]]] = {actor: [] for actor in actor_ids}
    for step in trace["tool_chain"]:
        args = step["tool_input"]
        actor = args.get("drone_id")
        if actor not in result:
            continue
        if step["tool"] == "navigate_to_and_sense":
            result[actor].append([args[axis] for axis in ("x", "y", "z")])
        elif step["tool"] in {"move_along_path", "move_along_path_and_sense"}:
            result[actor].extend([[point[axis] for axis in ("x", "y", "z")]
                                  for point in args["waypoints"]])
    return result


def result_for(key: str, record: dict) -> dict:
    scene_id, arm = key.split(":")
    attempt = Path(record["attempt"])
    if record["status"] != "completed_and_verified" or not read(attempt / "summary.json")["valid_complete_run"]:
        raise ValueError(f"Incomplete attempt: {key}")
    plan = read(attempt / "plan.json")
    if (len(plan["sessions"]), plan["sessions"][0]["id"], plan["expected_checks"]) != (1, scene_id, 26):
        raise ValueError(f"Plan mismatch: {key}")
    task_id = plan["sessions"][0]["tasks"][0]["id"]
    rows = read(attempt / "results.json")
    if len(rows) != 1 or rows[0]["task_id"] != task_id or rows[0]["status"] != "scored":
        raise ValueError(f"Expected one scored task: {key}")
    row = rows[0]
    scene = read(attempt / "runtime/data" / plan["sessions"][0]["file"])
    if scene["id"] != scene_id or scene["tasks"][0]["id"] != task_id:
        raise ValueError(f"Frozen task data mismatch: {key}")
    trace_files = list((attempt / scene_id / "agent-traces/toolchains").glob("*_completed.json"))
    if len(trace_files) != 1:
        raise ValueError(f"Expected one completed trace: {key}")
    trace = read(trace_files[0])
    if trace["command"] != scene["tasks"][0]["content"]:
        raise ValueError(f"Trace task text changed: {key}")
    before = read(attempt / scene_id / f"{task_id}.before.json")
    expected = route_points(scene)
    actual = actual_route(trace, set(expected))
    evidence = trace["task_progress"]["task_evidence"]
    route_views = evidence.get("coordinate_routes", [])
    if arm == "r3" and (evidence.get("route_parse_status") != "recognized_coordinate_routes"
                        or len(route_views) != 2
                        or any(route["ordered_prefix"] != 12 or route["state"] != "coordinate_sequence_evidenced"
                               for route in route_views)):
        raise ValueError(f"R3 route evidence not complete: {key}")
    if arm == "control" and route_views:
        raise ValueError(f"Control unexpectedly contains R3 route tracking: {key}")
    duration = (datetime.fromisoformat(trace["completed_at"])
                - datetime.fromisoformat(trace["started_at"])).total_seconds()
    return {"scene_id": scene_id, "task_id": task_id, "arm": arm,
            "passed": row["passed"], "passed_checks": sum(x["result"] is True for x in row["leaves"]),
            "checks": len(row["leaves"]), "agent_timed_out": row["agent_timed_out"],
            "agent_success": row["agent_success"], "duration_seconds": round(duration, 3),
            "token_usage": trace["token_usage"], "tool_names": trace["tool_names"],
            "exact_ordered_route_from_tool_inputs": {actor: actual[actor] == points
                                                     for actor, points in expected.items()},
            "route_parse_status": evidence.get("route_parse_status"),
            "r3_route_prefixes": {route["required_actor"]: route["ordered_prefix"]
                                  for route in route_views},
            "evidence_injections": len(evidence["injections"]),
            "injected_route_summaries": sum('"routes"' in item.get("text", "")
                                            for item in evidence["injections"]),
            "wire_model_requests": len(trace["model_requests_wire"]),
            "model_request_audit_errors": trace["model_request_audit_errors"],
            "initial_drones": [{"id": drone["id"], "position": drone["position"],
                                "battery_level": drone["battery_level"], "status": drone["status"]}
                               for drone in before["drones"]],
            "initial_environment": {key: before["environment"][key] for key in
                                    ("weather", "temperature", "wind_speed", "wind_direction", "visibility")},
            "attempt": str(attempt.relative_to(ROOT)), "trace": str(trace_files[0].relative_to(ROOT))}


def main():
    study = read(STUDY / "frozen/study.json")
    registry = read(STUDY / "runs.json")
    order = study["arm_order"]
    if set(registry) != {f"{scene}:{arm}" for scene, arm in order}:
        raise ValueError("Expected exactly the four predeclared arm attempts")
    hashes = {arm: read(STUDY / "frozen" / arm / "source_hashes.json")
              for arm in ("control", "r3")}
    changed = sorted(name for name in hashes["control"].keys() | hashes["r3"].keys()
                     if hashes["control"].get(name) != hashes["r3"].get(name))
    if changed != EXPECTED_CHANGED:
        raise ValueError(f"Unexpected treatment source difference: {changed}")
    items = {key: result_for(key, registry[key]) for key in registry}
    pairs = []
    for scene in study["plan"]["sessions"]:
        scene_id = scene["id"]
        control, treatment = items[f"{scene_id}:control"], items[f"{scene_id}:r3"]
        if (control["task_id"] != treatment["task_id"]
                or control["initial_drones"] != treatment["initial_drones"]):
            raise ValueError("Paired task or drone prestate differs")
        pair = {"scene_id": scene_id, "task_id": control["task_id"],
                "control_pass": control["passed"], "r3_pass": treatment["passed"],
                "control_checks": control["passed_checks"], "r3_checks": treatment["passed_checks"],
                "control_duration_seconds": control["duration_seconds"],
                "r3_duration_seconds": treatment["duration_seconds"],
                "control_tokens": control["token_usage"]["total_tokens"],
                "r3_tokens": treatment["token_usage"]["total_tokens"],
                "initial_drones_equal": True,
                "initial_environment_equal": (control["initial_environment"]
                                              == treatment["initial_environment"])}
        pairs.append(pair)
    output = {"schema": 1, "study": study["id"], "arm_order": order,
              "changed_source_files": changed, "runs": items, "pairs": pairs,
              "totals": {"control_passed_tasks": sum(pair["control_pass"] for pair in pairs),
                         "r3_passed_tasks": sum(pair["r3_pass"] for pair in pairs),
                         "control_passed_checks": sum(pair["control_checks"] for pair in pairs),
                         "r3_passed_checks": sum(pair["r3_checks"] for pair in pairs),
                         "r3_wins": sum(pair["r3_pass"] and not pair["control_pass"] for pair in pairs),
                         "r3_losses": sum(pair["control_pass"] and not pair["r3_pass"] for pair in pairs),
                         "ties": sum(pair["control_pass"] == pair["r3_pass"] for pair in pairs),
                         "control_tokens": sum(pair["control_tokens"] for pair in pairs),
                         "r3_tokens": sum(pair["r3_tokens"] for pair in pairs)}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUT), "totals": output["totals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
