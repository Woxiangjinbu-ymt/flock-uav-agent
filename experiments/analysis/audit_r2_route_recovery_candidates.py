#!/usr/bin/env python3
"""Geometry screen for every strict-R3 route task in the frozen R2 fast100.

This is an offline diagnostic. The original task, checker and score stay intact.
Run with agent4drone/.venv/bin/python.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
from zipfile import ZipFile

from shapely.geometry import Point
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
sys.path.insert(0, str(ROOT / "server"))
from task_evidence_r3 import explicit_routes  # noqa: E402
from controllers.obstacle_controller import ObstacleController  # noqa: E402

ATTEMPT = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403"
SCREEN = ROOT / "docs/results/m1r2-regression-analysis-20261008/explicit-route-screen.json"
OUTPUT = ROOT / "docs/audit/r3-original-recovery-20261009/r2-route-geometry.json"


def read(path: Path):
    return json.loads(path.read_text())


def main():
    source_names = ("server/controllers/obstacle_controller.py", "server/models/obstacle.py",
                    "server/api/server.py")
    with ZipFile(ATTEMPT / "source.zip") as archive:
        if not all(archive.read(name) == (ROOT / name).read_bytes() for name in source_names):
            raise ValueError("Geometry/check source differs from frozen R2 run")
    plan = read(ATTEMPT / "plan.json")
    results = {(row["session_id"], row["task_id"]): row for row in read(ATTEMPT / "results.json")}
    known = {(row["session_id"], row["task_id"]): row for row in read(SCREEN)}
    scenes = {scene["id"]: scene for scene in plan["sessions"]}
    rows = []
    for scene_id, scene_plan in scenes.items():
        scene = read(ATTEMPT / "runtime/data" / scene_plan["file"])
        for task in scene["tasks"]:
            routes, status = explicit_routes(task["content"])
            if status != "recognized_coordinate_routes":
                continue
            task_id = task["id"]
            key = scene_id, task_id
            if key not in known or key not in results:
                raise ValueError(f"Recognized route missing from prior exhaustive R2 screen: {key}")
            before = read(ATTEMPT / scene_id / f"{task_id}.before.json")
            if before["is_distance_3d"] is not False:
                raise ValueError("Unexpected 3D checker distance")
            controller = ObstacleController()
            for payload in before["obstacles"]:
                controller.add_obstacle(payload)
            obstacles = list(controller.obstacles.values())
            by_name = {drone["name"]: drone for drone in before["drones"]}
            checks = [leaf for leaf in results[key]["leaves"]
                      if leaf["definition"]["endpoint"] in
                      {"/check/drone_has_visited_position", "/check/drone_position"}]
            check_by_key = {}
            for leaf in checks:
                params = leaf["definition"]["parameters"]
                ckey = (params["drone_id"], float(params["x"]), float(params["y"]),
                        float(params["z"]))
                if ckey in check_by_key:
                    raise ValueError(f"Duplicate coordinate check: {key} {ckey}")
                check_by_key[ckey] = leaf
            task_rows = []
            for route in routes:
                actor = by_name[route["required_actor"]]
                actor_id = actor["id"]
                for goal in route["goals"]:
                    xyz = goal["point"]
                    x, y, z = xyz
                    exact = controller.check_point_collision(x, y, z, margin=0.0)
                    ckey = (actor_id, x, y, z)
                    leaf = check_by_key.get(ckey)
                    tolerance = float(leaf["definition"]["parameters"]["tolerance"]) if leaf else 3.0
                    active = [obstacle.to_geometry() for obstacle in obstacles
                              if obstacle.height == 0 or z <= obstacle.height]
                    blocked_geometry = unary_union([shape for shape in active if shape is not None])
                    free_area = Point(x, y).buffer(tolerance, quad_segs=128).difference(blocked_geometry).area
                    task_rows.append({"actor": route["required_actor"], "actor_id": actor_id,
                                      "ordinal": goal["ordinal"], "goal": xyz,
                                      "exact_point_blocked": bool(exact["result"]),
                                      "blocking_obstacles": [{"id": obstacle["id"],
                                                              "name": obstacle["name"],
                                                              "height": obstacle["height"]}
                                                             for obstacle in exact["inside_obstacles"]],
                                      "tolerance_m": tolerance, "locally_free_area_m2": round(free_area, 3),
                                      "locally_free_checker_destination": free_area > 1e-8,
                                      "original_visit_check_present": leaf is not None,
                                      "original_visit_check_passed": leaf["result"] if leaf else None})
                end = route["goals"][-1]["point"]
                ground = controller.check_point_collision(end[0], end[1], 0.0, margin=0.0)
                final_key = (actor_id, float(end[0]), float(end[1]), 0.0)
                final_leaf = check_by_key.get(final_key)
                task_rows.append({"actor": route["required_actor"], "actor_id": actor_id,
                                  "ordinal": len(route["goals"]), "goal": [end[0], end[1], 0.0],
                                  "kind": "ground_at_route_end",
                                  "exact_point_blocked": bool(ground["result"]),
                                  "blocking_obstacles": [{"id": obstacle["id"],
                                                          "name": obstacle["name"],
                                                          "height": obstacle["height"]}
                                                         for obstacle in ground["inside_obstacles"]],
                                  "original_position_check_present": final_leaf is not None,
                                  "original_position_check_passed": final_leaf["result"] if final_leaf else None})
            route_goals = [item for item in task_rows if item.get("kind") != "ground_at_route_end"]
            failed_checks = [leaf for leaf in checks if not leaf["result"]]
            rows.append({"session_id": scene_id, "task_id": task_id, "r2_pass": results[key]["passed"],
                         "r2_checks_passed": sum(leaf["result"] for leaf in results[key]["leaves"]),
                         "r2_checks_total": len(results[key]["leaves"]),
                         "routes": len(routes), "goals": len(route_goals),
                         "exact_blocked_goals": sum(item["exact_point_blocked"] for item in route_goals),
                         "locally_infeasible_goals": sum(not item["locally_free_checker_destination"]
                                                        for item in route_goals),
                         "ground_blocked_route_ends": sum(item["exact_point_blocked"] for item in task_rows
                                                          if item.get("kind") == "ground_at_route_end"),
                         "r2_failed_coordinate_checks": len(failed_checks),
                         "r2_failed_visit_checks_with_local_free_area": sum(
                             not item["original_visit_check_passed"]
                             and item["locally_free_checker_destination"]
                             for item in route_goals if item["original_visit_check_present"]),
                         "all_exact_route_goals_and_landing_legal": not any(
                             item["exact_point_blocked"] for item in task_rows),
                         "prior_probe_missing_goals": len(known[key]["probe"]["missing"]),
                         "points": task_rows})
    if set((row["session_id"], row["task_id"]) for row in rows) != set(known):
        raise ValueError("R2 route cohort changed")
    result = {"schema": 1, "source": "frozen R2 fast100 first run, every strict-R3 route",
              "source_matches_frozen": True,
              "scope": "Exact point and local checker tolerance only; no full route reachability or model counterfactual",
              "rows": rows,
              "summary": {"route_tasks": len(rows), "failed_route_tasks": sum(not row["r2_pass"] for row in rows),
                          "failed_and_exact_route_legal": sum(not row["r2_pass"]
                                                              and row["all_exact_route_goals_and_landing_legal"]
                                                              for row in rows)}}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"summary": result["summary"], "tasks": [
        {k: row[k] for k in ("session_id", "task_id", "r2_checks_passed", "r2_checks_total",
                              "exact_blocked_goals", "locally_infeasible_goals",
                              "ground_blocked_route_ends", "r2_failed_coordinate_checks",
                              "r2_failed_visit_checks_with_local_free_area")}
        for row in rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
