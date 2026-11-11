#!/usr/bin/env python3
"""Cross-check R3 route receipts against frozen original checks after the pilot."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from task_evidence_r3 import receipt_points  # noqa: E402

SUMMARY = ROOT / "docs/results/m1r3-route-prefix-20261009/summary.json"
OUTPUT = ROOT / "docs/results/m1r3-route-prefix-20261009/mechanism.json"
ROUTE_TASKS = {"f777f4e3", "cf8ee51f"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def arm_trace(summary, arm, task_id):
    row = next(row for row in summary["arms"][arm]["tasks"] if row["task_id"] == task_id)
    return row, read(ROOT / row["trace"])


def failed_visit_checks(attempt, task_id):
    leaves = read(Path(attempt) / "35ce64d8" / f"{task_id}.checks.json")
    return [leaf for leaf in leaves if leaf.get("type") == "leaf"
            and leaf.get("endpoint") == "/check/drone_has_visited_position"
            and not leaf.get("result")]


def visit_gap(leaf, events):
    params = leaf["parameters"]
    goal = [params["x"], params["y"], params["z"]]
    actor = params["drone_id"]
    proofs = [(math.dist(goal, proof["actual_point"]), event["evidence_id"],
               proof["actual_point"])
              for event in events if event["args"].get("drone_id") == actor
              for proof in receipt_points(event)]
    nearest = min(proofs) if proofs else None
    blocked = []
    for event in events:
        args = event["args"]
        if (event["status"] != "blocked" or args.get("drone_id") != actor
                or [args.get("x"), args.get("y"), args.get("z")] != goal):
            continue
        payload = event["payload"]
        reply = payload.get("navigation_result", payload.get("move_result", payload))
        blocked.append({"evidence_id": event["evidence_id"],
                        "message": reply.get("message") if isinstance(reply, dict) else None})
    return {"actor_id": actor, "goal": goal, "checker_tolerance_m": params["tolerance"],
            "nearest_public_receipt_distance_m": round(nearest[0], 2) if nearest else None,
            "nearest_public_receipt_evidence_id": nearest[1] if nearest else None,
            "nearest_public_receipt_point": nearest[2] if nearest else None,
            "blocked_exact_goal_attempts": blocked}


def main():
    summary = read(SUMMARY)
    result = {"study": summary["study"], "route_tasks": {}, "nonroute_win": {}}
    for task_id in sorted(ROUTE_TASKS):
        per_arm = {}
        for arm in ("control", "r3"):
            row, trace = arm_trace(summary, arm, task_id)
            evidence = trace["task_progress"]["task_evidence"]
            failures = failed_visit_checks(summary["arms"][arm]["attempt"], task_id)
            per_arm[arm] = {"passed": row["passed"], "passed_checks": row["passed_checks"],
                            "checks": row["checks"], "route_parse_status": row["route_parse_status"],
                            "task_evidence_injections": len(evidence.get("injections", [])),
                            "route_prefixes": [{"actor": route["required_actor"],
                                                "ordered_prefix": route["ordered_prefix"],
                                                "total": len(route["goals"]),
                                                "next_unconfirmed": route["next_unconfirmed"]["point"]
                                                if route["next_unconfirmed"] else None}
                                               for route in row["final_coordinate_routes"]],
                            "failed_visit_gaps": [visit_gap(leaf, trace["task_progress"]["events"])
                                                  for leaf in failures]}
        result["route_tasks"][task_id] = per_arm
    task_id = "b1ad4f3e"
    states = {}
    for arm in ("control", "r3"):
        row, trace = arm_trace(summary, arm, task_id)
        before = read(Path(summary["arms"][arm]["attempt"]) /
                      "35ce64d8" / f"{task_id}.before.json")
        states[arm] = {"task_passed": row["passed"], "trace_status": trace["status"],
                       "route_parse_status": row["route_parse_status"],
                       "targets_reached_before": sum(bool(t.get("is_reached"))
                                                     for t in before["targets"]),
                       "target_count": len(before["targets"]),
                       "drone_positions_before": {drone["id"]: drone["position"]
                                                  for drone in before["drones"]}}
    positions_a = states["control"].pop("drone_positions_before")
    positions_b = states["r3"].pop("drone_positions_before")
    result["nonroute_win"] = {"task_id": task_id, "arms": states,
                              "different_drone_positions_before": sum(
                                  positions_a[actor] != positions_b[actor] for actor in positions_a)}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "route_tasks": list(result["route_tasks"]),
                      "nonroute_prestate_reached": {arm: state["targets_reached_before"]
                                                  for arm, state in states.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
