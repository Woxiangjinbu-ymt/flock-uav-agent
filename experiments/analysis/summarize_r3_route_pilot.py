#!/usr/bin/env python3
"""Summarize the first verified R2 versus R3 route-prefix attempts."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "experiments/m1r3-route-prefix-v2"
OUTPUT = ROOT / "docs/results/m1r3-route-prefix-20261009/summary.json"
EXPECTED_CHANGED = ["agent4drone/task_evidence_r3.py",
                    "agent4drone/tests/test_task_evidence_r3.py",
                    "agent4drone/uav_agent.py"]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def arm_rows(attempt):
    plan = read(attempt / "plan.json")
    scored = {(row["session_id"], row["task_id"]): row
              for row in read(attempt / "results.json")}
    result = []
    for scene in plan["sessions"]:
        original = read(attempt / "runtime/data" / scene["file"])
        commands = {task["id"]: task["content"] for task in original["tasks"]}
        traces = [(path, read(path)) for path in
                  (attempt / scene["id"] / "agent-traces/toolchains").glob("*.json")]
        for ordinal, task in enumerate(scene["tasks"], 1):
            key = scene["id"], task["id"]
            row = scored[key]
            found = [(path, trace) for path, trace in traces
                     if trace.get("command") == commands[task["id"]]]
            if row["status"] != "scored" or len(found) != 1:
                raise ValueError(f"Expected one scored task trace: {key}")
            path, trace = found[0]
            evidence = (trace.get("task_progress") or {}).get("task_evidence") or {}
            duration = (datetime.fromisoformat(trace["completed_at"])
                        - datetime.fromisoformat(trace["started_at"])).total_seconds()
            result.append({"session_id": key[0], "task_id": key[1], "ordinal": ordinal,
                           "passed": bool(row["passed"]),
                           "passed_checks": sum(bool(leaf["result"]) for leaf in row["leaves"]),
                           "checks": len(row["leaves"]),
                           "failed_leaves": [{"endpoint": leaf["definition"]["endpoint"],
                                              "parameters": leaf["definition"]["parameters"]}
                                             for leaf in row["leaves"] if not leaf["result"]],
                           "agent_timed_out": bool(row["agent_timed_out"]),
                           "duration_seconds": round(duration, 3),
                           "token_usage": trace.get("token_usage"),
                           "command_id": trace["command_id"],
                           "trace": str(path.relative_to(ROOT)),
                           "route_parse_status": evidence.get("route_parse_status"),
                           "final_coordinate_routes": evidence.get("coordinate_routes", [])})
    if len(result) != plan["expected_tasks"]:
        raise ValueError("Missing planned task rows")
    return result


def main():
    registry = read(STUDY / "runs.json")
    if set(registry) != {"control", "r3"} or any(
            record["status"] != "completed_and_verified" for record in registry.values()):
        raise ValueError("Both first-attempt arms must complete and verify")
    attempts = {arm: Path(record["attempt"]) for arm, record in registry.items()}
    plans = {arm: read(attempt / "plan.json") for arm, attempt in attempts.items()}
    comparable = [{k: v for k, v in plan.items() if k != "study"}
                  for plan in plans.values()]
    if comparable[0] != comparable[1]:
        raise ValueError("Arm plans differ")
    hashes = {arm: read(STUDY / "frozen" / arm / "source_hashes.json") for arm in registry}
    changed = sorted(name for name in set(hashes["control"]) | set(hashes["r3"])
                     if hashes["control"].get(name) != hashes["r3"].get(name))
    if changed != EXPECTED_CHANGED:
        raise ValueError("Unexpected treatment source difference")
    arms = {}
    for arm, attempt in attempts.items():
        summary = read(attempt / "summary.json")
        if not summary["valid_complete_run"]:
            raise ValueError(f"Invalid arm: {arm}")
        rows = arm_rows(attempt)
        arms[arm] = {"attempt": str(attempt), "summary": summary, "tasks": rows}
    control = {row["task_id"]: row for row in arms["control"]["tasks"]}
    treatment = {row["task_id"]: row for row in arms["r3"]["tasks"]}
    paired = [{"task_id": task_id, "ordinal": row["ordinal"],
               "control_pass": row["passed"], "r3_pass": treatment[task_id]["passed"],
               "control_passed_checks": row["passed_checks"],
               "r3_passed_checks": treatment[task_id]["passed_checks"],
               "checks": row["checks"],
               "control_duration_seconds": row["duration_seconds"],
               "r3_duration_seconds": treatment[task_id]["duration_seconds"]}
              for task_id, row in control.items()]
    result = {"study": str(STUDY), "plan": plans["control"],
              "changed_source_files": changed, "arms": arms, "paired": paired,
              "r3_wins": sum(row["r3_pass"] and not row["control_pass"] for row in paired),
              "r3_losses": sum(row["control_pass"] and not row["r3_pass"] for row in paired),
              "ties": sum(row["control_pass"] == row["r3_pass"] for row in paired)}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "r3_wins": result["r3_wins"],
                      "r3_losses": result["r3_losses"], "ties": result["ties"]}))


if __name__ == "__main__":
    main()
