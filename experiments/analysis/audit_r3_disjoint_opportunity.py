#!/usr/bin/env python3
"""Count R3's syntax-recognized routes in B0 scenes disjoint from R2.

Task commands and scene membership select the cohort. B0 checker results are
read only afterward to describe historical outcomes, never to select tasks or
claim an R3 counterfactual.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from task_evidence_r3 import explicit_routes  # noqa: E402

FULL = ROOT / "docs/results/full-round1/results.json"
R2_PLAN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403/plan.json"
OUTPUT = ROOT / "docs/audit/r3-next-step-20261009/disjoint-opportunity.json"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    full = read(FULL)
    assert full["status"] == "complete_verified"
    attempt = ROOT / full["attempt"]
    plan = read(attempt / "plan.json")
    r2_scene_ids = {scene["id"] for scene in read(R2_PLAN)["sessions"]}
    directories = {scene["session_id"]: scene["directory"] for scene in full["scenes"]}
    rows = []
    task_count = 0
    status_count = Counter()
    for scene in plan["sessions"]:
        if scene["id"] in r2_scene_ids:
            continue
        directory = attempt / directories[scene["id"]]
        for ordinal, task in enumerate(scene["tasks"], 1):
            command = read(directory / f"{task['id']}.job.json")["command"]
            routes, status = explicit_routes(command)
            status_count[status] += 1
            task_count += 1
            if not routes:
                continue
            # Outcomes enter only after the parser has selected the row.
            leaves = [item for item in read(directory / f"{task['id']}.checks.json")
                      if item.get("type") == "leaf"]
            failed = [item for item in leaves if not item.get("result")]
            rows.append({"session_id": scene["id"], "family": scene["family"],
                         "difficulty": task["difficulty"], "ordinal": ordinal,
                         "task_id": task["id"], "route_actors": len(routes),
                         "route_coordinate_obligations": sum(len(route["goals"]) for route in routes),
                         "checks": len(leaves), "failed_checks": len(failed),
                         "failed_endpoints": sorted({item["endpoint"] for item in failed})})
    assert task_count == 1400 and len(rows) == 34
    result = {"method": "strict-r3-parser-on-scene-disjoint-b0-commands",
              "r2_development_scenes_excluded": len(r2_scene_ids),
              "scenes": len(plan["sessions"]) - len(r2_scene_ids),
              "tasks": task_count,
              "parse_status_counts": dict(status_count),
              "recognized_route_tasks": len(rows),
              "recognized_route_scenes": len({row["session_id"] for row in rows}),
              "recognized_route_tasks_with_any_failed_b0_check": sum(
                  row["failed_checks"] > 0 for row in rows),
              "recognized_route_tasks_by_family": dict(Counter(row["family"] for row in rows)),
              "rows": rows,
              "limitations": [
                  "B0 behavior and R3 behavior are different; B0 failures are not R3 recoveries",
                  "The cohort is disjoint from R2 development scenes but not a blind prospective test",
                  "Parser recognition is syntax only and does not prove any route is feasible",
                  "Final-attempt scene directories from complete_verified B0 are used",
              ]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "scenes", "tasks", "recognized_route_tasks", "recognized_route_scenes",
        "recognized_route_tasks_with_any_failed_b0_check",
        "recognized_route_tasks_by_family")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
