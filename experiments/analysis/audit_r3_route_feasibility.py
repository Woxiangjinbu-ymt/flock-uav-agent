#!/usr/bin/env python3
"""Audit local geometric feasibility of failed R3 route checks.

Run with server/.venv/bin/python because it contains the simulator's Shapely.
This only tests whether a legal destination exists inside the check tolerance;
it does not establish a collision-free path to that destination.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from zipfile import ZipFile

from shapely.geometry import Point
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from controllers.obstacle_controller import ObstacleController  # noqa: E402

RESULTS = ROOT / "docs/results/m1r3-route-prefix-20261009"
TASK_ID = "f777f4e3"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    summary = read(RESULTS / "summary.json")
    mechanism = read(RESULTS / "mechanism.json")
    frozen_zip = ROOT / "experiments/m1r3-route-prefix-v2/frozen/r3/source.zip"
    source_files = ("server/controllers/obstacle_controller.py",
                    "server/models/obstacle.py", "server/api/server.py")
    with ZipFile(frozen_zip) as archive:
        assert all(archive.read(name) == (ROOT / name).read_bytes()
                   for name in source_files), "Simulator geometry/check source differs from frozen run"
    before = {}
    for arm in ("control", "r3"):
        attempt = Path(summary["arms"][arm]["attempt"])
        before[arm] = read(attempt / "35ce64d8" / f"{TASK_ID}.before.json")
    assert before["control"]["obstacles"] == before["r3"]["obstacles"]
    assert before["control"]["is_distance_3d"] == before["r3"]["is_distance_3d"]

    controller = ObstacleController()
    for obstacle in before["r3"]["obstacles"]:
        controller.add_obstacle(obstacle)

    rows = []
    for gap in mechanism["route_tasks"][TASK_ID]["r3"]["failed_visit_gaps"]:
        x, y, z = gap["goal"]
        radius = float(gap["checker_tolerance_m"])
        point = Point(x, y)
        # Mirrors check_point_collision's altitude rule: height 0 is non-flyable
        # at all altitudes, otherwise only z <= height collides at margin 0.
        active = [obstacle for obstacle in controller.obstacles.values()
                  if obstacle.height == 0 or z <= obstacle.height]
        geometry = unary_union([obstacle.to_geometry() for obstacle in active
                                if obstacle.to_geometry() is not None])
        tolerance_disk = point.buffer(radius, quad_segs=128)
        local_free = tolerance_disk.difference(geometry)
        exact_collision = controller.check_point_collision(x, y, z, margin=0.0)
        rows.append({
            "goal": gap["goal"],
            "checker_tolerance_m": radius,
            "exact_goal_collides": exact_collision["result"],
            "covering_obstacles": [
                {"id": o["id"], "name": o["name"], "height": o["height"]}
                for o in exact_collision["inside_obstacles"]],
            "nearest_free_boundary_distance_m": round(
                point.distance(geometry.boundary), 3) if exact_collision["result"] else 0.0,
            "free_area_in_tolerance_disk_m2": round(local_free.area, 3),
            "locally_free_destination_exists": local_free.area > 1e-8,
            "nearest_successful_receipt_distance_m": gap["nearest_public_receipt_distance_m"],
        })
    result = {
        "task_id": TASK_ID,
        "source": "first valid R2/R3 paired pilot, R3 task pre-state",
        "control_and_r3_obstacles_identical": True,
        "simulator_source_matches_frozen_run": True,
        "geometry_rule": "simulator check_point_collision, height=0 blocks all altitudes; 3m checker disk",
        "scope_limit": "Local destination feasibility only; a free point may still lack a reachable path.",
        "failed_checks": len(rows),
        "locally_infeasible_checks": sum(not row["locally_free_destination_exists"] for row in rows),
        "rows": rows,
    }
    output = RESULTS / "feasibility.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "failed": len(rows),
                      "locally_infeasible": result["locally_infeasible_checks"]}))


if __name__ == "__main__":
    main()
