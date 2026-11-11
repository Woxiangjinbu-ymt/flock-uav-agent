#!/usr/bin/env python3
"""Freeze public-only multi-UAV sensing routes for the three R2 timeout cases.

This generator reads only job/exception traces and their model-visible tool
payloads. It contains no .before/.after/scene-data read path and no target
coordinates in its planner inputs. Offline oracle evaluation is separate.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from zipfile import ZipFile

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403"
SCENE = "823c15d5"
TASK_IDS = ("5e743d21", "24ac1838", "7cdefb56")
OUTPUT = ROOT / "docs/audit/r2-public-aggregate-search-plan-20261009/plans.json"
GRID_STEP_M = 70.0
SCORE_CELL_STEP_M = 25.0
SAFETY_BUFFER_M = 1.0
DISTANCE_SCALE_M = 250.0
ROUNDS_PER_DRONE = 16
PREFIX_ROUNDS = (4, 8, 16)
SEARCH_MARGIN_M = 200.0  # inherited from frozen observation-worker public envelope

sys.path.insert(0, str(ROOT / "agent4drone"))
from path_planning.path_finder import _obstacle_to_shape  # noqa: E402


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def trace_for(task_id: str):
    folder = RUN / SCENE
    created = datetime.fromisoformat(read(folder / f"{task_id}.job.json")["created_at"])
    matches = []
    for path in (folder / "agent-traces/toolchains").glob("*_exception.json"):
        trace = read(path)
        if abs((datetime.fromisoformat(trace["started_at"]) - created).total_seconds()) < 5:
            matches.append((path, trace))
    if len(matches) != 1:
        raise ValueError(f"Expected one frozen trace for {task_id}")
    return matches[0]


def public_inputs(trace: dict):
    events = trace["task_progress"]["events"]
    listed = next(event["payload"] for event in events if event["tool_name"] == "list_drones"
                  and isinstance(event.get("payload"), list))
    first_sense = next(event for event in events
                       if event["tool_name"] == "sense_nearby_entities"
                       and event["status"] == "observed"
                       and isinstance(event.get("payload"), dict))
    if (len(listed) != 30 or len(first_sense["payload"].get("sensed_drone_ids", [])) != 30
            or first_sense["args"].get("detail") != "full"):
        raise ValueError("Expected an initial full-fleet public sense")
    obstacles = first_sense["payload"]["blackboard"]["obstacles"]
    radius = min(float(drone["perceived_radius"]) for drone in listed)
    if radius != 50 or any(float(drone["battery_level"]) < 15 for drone in listed):
        raise ValueError("Unexpected public sensor radius/battery")
    xs = [float(drone["position"]["x"]) for drone in listed]
    ys = [float(drone["position"]["y"]) for drone in listed]
    bounds = [max(0.0, min(xs) - SEARCH_MARGIN_M), max(xs) + SEARCH_MARGIN_M,
              max(0.0, min(ys) - SEARCH_MARGIN_M), max(ys) + SEARCH_MARGIN_M]
    return listed, obstacles, bounds, radius, first_sense["end_sequence"]


def lattice(bounds, step: float):
    x0, x1, y0, y1 = bounds
    x = x0 + step / 2
    points = []
    while x <= x1:
        y = y0 + step / 2
        while y <= y1:
            points.append((round(x, 3), round(y, 3)))
            y += step
        x += step
    return points


def plan_one(task_id: str):
    trace_path, trace = trace_for(task_id)
    drones, obstacles, bounds, radius, first_sense_sequence = public_inputs(trace)
    known_shapes = [_obstacle_to_shape(item) for item in obstacles]
    blocked = unary_union(known_shapes).buffer(SAFETY_BUFFER_M)
    candidates = [point for point in lattice(bounds, GRID_STEP_M)
                  if not blocked.intersects(Point(*point))]
    score_cells = lattice(bounds, SCORE_CELL_STEP_M)
    cell_coverage: list[tuple[int, ...]] = []
    for x, y in candidates:
        cell_coverage.append(tuple(index for index, cell in enumerate(score_cells)
                                   if math.hypot(cell[0] - x, cell[1] - y) <= radius))
    current = {str(drone["id"]): (float(drone["position"]["x"]),
                                   float(drone["position"]["y"])) for drone in drones}
    ids = sorted(current)
    covered_cells = {index for index, cell in enumerate(score_cells)
                     if any(math.hypot(cell[0] - xy[0], cell[1] - xy[1]) <= radius
                            for xy in current.values())}
    initial_cells = len(covered_cells)
    selected: set[int] = set()
    endpoints = []
    for round_number in range(1, ROUNDS_PER_DRONE + 1):
        for drone_id in ids:
            start = current[drone_id]
            rankings = []
            for index, destination in enumerate(candidates):
                if index in selected:
                    continue
                novel = sum(cell not in covered_cells for cell in cell_coverage[index])
                if not novel:
                    continue
                travel = math.dist(start, destination)
                score = novel / (1.0 + travel / DISTANCE_SCALE_M)
                rankings.append((-score, travel, index))
            rankings.sort()
            choice = None
            for _, travel, index in rankings:
                destination = candidates[index]
                if not blocked.intersects(LineString([start, destination])):
                    choice = (index, destination, travel)
                    break
            if choice is None:
                continue
            index, destination, travel = choice
            selected.add(index)
            new_cells = sum(cell not in covered_cells for cell in cell_coverage[index])
            covered_cells.update(cell_coverage[index])
            endpoints.append({"round": round_number, "drone_id": drone_id,
                              "from_xy": list(start), "to_xy": list(destination),
                              "straight_segment_m": round(travel, 3),
                              "new_public_score_cells": new_cells})
            current[drone_id] = destination
    result = {
        "task_id": task_id, "session_id": SCENE,
        "trace": str(trace_path.relative_to(ROOT)),
        "first_full_sense_end_sequence": first_sense_sequence,
        "public_input": {"fleet_size": len(drones), "observed_obstacle_count": len(obstacles),
                         "sensor_radius_m": radius, "soft_envelope_bounds_xyxy": bounds,
                         "candidate_lattice_points_after_public_obstacle_filter": len(candidates),
                         "score_lattice_cells": len(score_cells),
                         "initial_covered_score_cells": initial_cells},
        "planned_endpoints": len(endpoints),
        "last_covered_score_cells": len(covered_cells),
        "endpoints": endpoints,
    }
    return result


def main():
    with ZipFile(RUN / "source.zip") as archive:
        path = "agent4drone/path_planning/path_finder.py"
        if archive.read(path) != (ROOT / path).read_bytes():
            raise ValueError("Public obstacle shape parsing differs from frozen R2")
    result = {
        "schema": 1,
        "public_only_generator": True,
        "strategy": "70m square sensing lattice, public known-obstacle buffer 1m, 25m score cells; round-robin 30 UAVs, choose highest new-cell/(1+travel/250m) direct-clear candidate, max 16 endpoints per UAV.",
        "fixed_prefix_rounds": list(PREFIX_ROUNDS),
        "limits": ["This is a static public-only route proposal, not an executed action trace.",
                   "Unknown obstacles, battery use and elapsed time are not available to this generator.",
                   "Only direct segments clear of observed obstacles are proposed; no hidden target or complete scene file is read."],
        "plans": [plan_one(task_id) for task_id in TASK_IDS],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    OUTPUT.write_text(serialized, encoding="utf-8")
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    print(json.dumps({"plan_sha256": digest,
                      "plans": [{"task_id": plan["task_id"],
                                 "endpoints": plan["planned_endpoints"],
                                 "candidate_points": plan["public_input"]["candidate_lattice_points_after_public_obstacle_filter"],
                                 "observed_obstacles": plan["public_input"]["observed_obstacle_count"]}
                                for plan in result["plans"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
