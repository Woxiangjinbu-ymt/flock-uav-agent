#!/usr/bin/env python3
"""Audit public search-region candidates for R2 unknown-target failures.

The frozen R2 failure cohort is selected from prior outcome analysis. Task text,
public list_drones receipts, and public canvas metadata define candidate search
regions. SYSTEM snapshots are read only afterward to evaluate target inclusion.
No planner gets target positions or checker results.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

from shapely.geometry import Point, Polygon

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs/audit/m1r2-observation-worker-shadow-20261009/decisions.json"
FULL = ROOT / "docs/results/full-round1/results.json"
R2_PLAN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403/plan.json"
OUTPUT = ROOT / "docs/audit/unknown-target-search-space-20261009/audit.json"
CASES = {"529c0622", "ce97a68d", "13a30905", "c6e28e3b", "9e46da10", "22e1e1c5"}
XY_TUPLE = re.compile(r"\(\s*[-+]?\d+(?:\.\d+)?\s*,\s*[-+]?\d+(?:\.\d+)?(?:\s*,\s*[-+]?\d+(?:\.\d+)?)?\s*\)")
SEARCH_MARGIN_M = 200.0  # frozen worker _valid_plan public-drone-envelope rule


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def public_fleet_envelope(drones):
    xs = [float(item["position"]["x"]) for item in drones]
    ys = [float(item["position"]["y"]) for item in drones]
    radius = max(float(item["perceived_radius"]) for item in drones)
    bounds = [max(0.0, min(xs) - SEARCH_MARGIN_M), max(xs) + SEARCH_MARGIN_M,
              max(0.0, min(ys) - SEARCH_MARGIN_M), max(ys) + SEARCH_MARGIN_M]
    area = (bounds[1] - bounds[0]) * (bounds[3] - bounds[2])
    # An optimistic area bound: every endpoint contributes an entire sensing
    # disk without overlap. It ignores paths, obstacles, and edge effects.
    return {"bounds_xyxy": bounds, "area_m2": round(area, 1),
            "largest_sensor_radius_m": radius,
            "full_region_endpoint_area_lower_bound": math.ceil(area / (math.pi * radius**2)),
            "three_endpoint_max_area_fraction": round(3 * math.pi * radius**2 / area, 5)}


def contains(bounds, point):
    return bounds[0] <= point["x"] <= bounds[1] and bounds[2] <= point["y"] <= bounds[3]


def target_geometry(item):
    point = item["position"]
    kind = item.get("type")
    if kind == "polygon" and item.get("vertices"):
        return Polygon([(v["x"], v["y"]) for v in item["vertices"]])
    if kind == "circle":
        return Point(point["x"], point["y"]).buffer(float(item.get("radius", 0)))
    return Point(point["x"], point["y"])


def r2_failure_rows(decisions):
    rows = []
    for decision in decisions:
        if decision["task_id"] not in CASES:
            continue
        trace_path = ROOT / decision["trace"]
        trace = read(trace_path)
        events = trace["task_progress"]["events"]
        public_drones = next(item["payload"] for item in events
                             if item["tool_name"] == "list_drones")
        roi = public_fleet_envelope(public_drones)
        # The task-start SYSTEM state enters only at this evaluation point.
        snapshot = read(trace_path.parents[2] / f"{decision['task_id']}.before.json")
        width, height = float(snapshot["canvas_width"]), float(snapshot["canvas_height"])
        by_name = {target["name"]: target for target in snapshot["targets"]}
        missing = [item for item in trace["task_progress"]["task_evidence"]["requirements"]
                   if item["relation"] == "visit" and item["knowledge_state"] != "localized"]
        targets = []
        for requirement in missing:
            target = by_name[requirement["target_name"]]
            point = target["position"]
            geometry = target_geometry(target)
            initial_distances = [Point(drone["position"]["x"],
                                       drone["position"]["y"]).distance(geometry)
                                 for drone in public_drones]
            targets.append({"name": target["name"],
                            "knowledge_state": requirement["knowledge_state"],
                            "inside_nominal_canvas": 0 <= point["x"] <= width and 0 <= point["y"] <= height,
                            "inside_public_fleet_envelope": contains(roi["bounds_xyxy"], point),
                            "nearest_initial_public_drone_m": round(min(
                                math.hypot(point["x"] - drone["position"]["x"],
                                           point["y"] - drone["position"]["y"])
                                for drone in public_drones), 1),
                            "nearest_initial_public_drone_to_geometry_m": round(
                                min(initial_distances), 1),
                            "initial_public_drone_can_sense_target_2d": any(
                                distance <= drone["perceived_radius"]
                                for distance, drone in zip(initial_distances, public_drones))})
        rows.append({"session_id": decision["session_id"], "task_id": decision["task_id"],
                     "task_text_explicit_xy_tuple_count": len(XY_TUPLE.findall(trace["command"])),
                     "public_fleet_size": len(public_drones),
                     "nominal_canvas_xy": [width, height],
                     "search_region": roi, "unlocalized_target_obligations": targets,
                     "r2_timed_out": decision["r2_timeout_offline_label"]})
    if len(rows) != len(CASES):
        raise ValueError("Missing one of the six frozen R2 localization failures")
    return rows


def scene_disjoint_envelope_check():
    full = read(FULL)
    assert full["status"] == "complete_verified"
    attempt = ROOT / full["attempt"]
    plan = read(attempt / "plan.json")
    by_id = {session["id"]: session for session in plan["sessions"]}
    r2_scenes = {session["id"] for session in read(R2_PLAN)["sessions"]}
    rows = []
    for scene in full["scenes"]:
        session_id = scene["session_id"]
        if session_id in r2_scenes:
            continue
        first_task = by_id[session_id]["tasks"][0]["id"]
        snapshot = read(attempt / scene["directory"] / f"{first_task}.before.json")
        # Drone state in the task-start snapshot is the same public state
        # returned by list_drones at the first task boundary; targets are eval.
        roi = public_fleet_envelope(snapshot["drones"])
        outside = sum(not contains(roi["bounds_xyxy"], target["position"])
                      for target in snapshot["targets"])
        rows.append({"session_id": session_id, "family": scene["family"],
                     "difficulty": scene["difficulty"],
                     "targets": len(snapshot["targets"]),
                     "targets_outside_public_fleet_envelope": outside,
                     "endpoint_area_lower_bound": roi["full_region_endpoint_area_lower_bound"]})
    assert len(rows) == 70
    endpoint_counts = [row["endpoint_area_lower_bound"] for row in rows]
    return {"scenes": len(rows), "targets": sum(row["targets"] for row in rows),
            "targets_outside_fleet_envelope": sum(row["targets_outside_public_fleet_envelope"] for row in rows),
            "scenes_with_outside_target": sum(row["targets_outside_public_fleet_envelope"] > 0 for row in rows),
            "endpoint_area_lower_bound_min": min(endpoint_counts),
            "endpoint_area_lower_bound_median": statistics.median(endpoint_counts),
            "endpoint_area_lower_bound_max": max(endpoint_counts),
            "rows": rows}


def main():
    failures = r2_failure_rows(read(DECISIONS))
    disjoint = scene_disjoint_envelope_check()
    result = {"method": "public-search-region-identifiability-audit-not-a-planner",
              "r2_failures": failures,
              "r2_unlocalized_obligation_count": sum(len(row["unlocalized_target_obligations"])
                                                     for row in failures),
              "r2_unknown_obligation_count": sum(
                  target["knowledge_state"] == "unknown" for row in failures
                  for target in row["unlocalized_target_obligations"]),
              "r2_reported_only_obligation_count": sum(
                  target["knowledge_state"] == "reported_only" for row in failures
                  for target in row["unlocalized_target_obligations"]),
              "r2_unlocalized_obligations_outside_nominal_canvas": sum(
                  not target["inside_nominal_canvas"] for row in failures
                  for target in row["unlocalized_target_obligations"]),
              "r2_unlocalized_obligations_outside_fleet_envelope": sum(
                  not target["inside_public_fleet_envelope"] for row in failures
                  for target in row["unlocalized_target_obligations"]),
              "r2_unlocalized_obligations_initially_senseable_2d": sum(
                  target["initial_public_drone_can_sense_target_2d"] for row in failures
                  for target in row["unlocalized_target_obligations"]),
              "scene_disjoint_B0_fleet_envelope_check": disjoint,
              "limitations": [
                  "Six R2 cases were selected because they failed; this is explanatory, not an unbiased failure estimate",
                  "Nominal canvas describes the UI, not a guaranteed hard target domain",
                  "The fleet envelope is an empirical soft region, misses seven targets in scene-disjoint B0, and is too large for a three-endpoint guarantee",
                  "Area endpoint bound ignores flight-path sensing, overlapping disks, obstacles, and search priors; it is not a wall-clock lower bound",
                  "Hidden target locations and complete snapshots are used only after public search-region construction for evaluation",
              ]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"r2_cases": len(failures),
                      "missing_target_obligations": result["r2_unlocalized_obligation_count"],
                      "outside_nominal_canvas": result["r2_unlocalized_obligations_outside_nominal_canvas"],
                      "outside_fleet_envelope": result["r2_unlocalized_obligations_outside_fleet_envelope"],
                      "scene_disjoint_scenes": disjoint["scenes"],
                      "scene_disjoint_target_outside": disjoint["targets_outside_fleet_envelope"],
                      "scene_disjoint_endpoint_bound_median": disjoint["endpoint_area_lower_bound_median"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
