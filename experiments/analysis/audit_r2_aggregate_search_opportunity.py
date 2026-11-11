#!/usr/bin/env python3
"""Audit explicit sensing footprints and local geometry in R2 progress timeouts.

The footprint is reconstructed solely from completed public tool receipts.
Full target and obstacle snapshots enter only after the footprint is frozen,
for retrospective coverage validation and local geometry checks.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import sys
from zipfile import ZipFile

from shapely.geometry import Point, box
from shapely.ops import unary_union

from audit_r2_aggregate_timeout_knowledge import (ROOT, RUN, TASK_IDS,
                                                   add_visible_targets, read, trace_for)
from audit_public_sensing_coverage import actual_endpoint
from audit_unknown_target_search_space import public_fleet_envelope

sys.path.insert(0, str(ROOT / "server"))
from controllers.obstacle_controller import ObstacleController  # noqa: E402

OUTPUT = ROOT / "docs/audit/r2-aggregate-search-opportunity-20261009/rows.json"
MOVES = {"navigate_to", "move_to", "move_towards", "move_along_path",
         "navigate_to_and_sense", "move_to_and_sense", "move_towards_and_sense",
         "move_along_path_and_sense"}
SENSING_MOVES = {name for name in MOVES if name.endswith("_and_sense")}


def frozen_server_match() -> bool:
    source_names = (
        "server/controllers/drone_controller.py", "server/controllers/obstacle_controller.py",
        "server/models/obstacle.py", "server/models/target.py",
        "agent4drone/uav_langchain_tools.py",
    )
    with ZipFile(RUN.parents[0] / "source.zip") as archive:
        return all(archive.read(name) == (ROOT / name).read_bytes() for name in source_names)


def target_ids(payload: dict) -> set[str]:
    return {str(target["id"]) for target in payload.get("targets", [])
            if isinstance(target, dict) and target.get("id")}


def visible_samples(events: list, stop_sequence: int):
    """Freeze XY samples from tool receipts before reading scene target positions."""
    positions: dict[str, tuple[float, float] | None] = {}
    radii: dict[str, float] = {}
    listed = None
    known: dict = {}
    samples = []
    skipped = []
    sensing_events = []
    for event in events:
        sequence = event.get("end_sequence")
        if not isinstance(sequence, int) or sequence > stop_sequence:
            continue
        name = event["tool_name"]
        payload = event.get("payload")
        add_visible_targets(event, known)
        if name == "list_drones" and isinstance(payload, list):
            listed = payload
            positions = {str(drone["id"]): (
                float(drone["position"]["x"]), float(drone["position"]["y"]))
                for drone in payload}
            radii = {str(drone["id"]): float(drone["perceived_radius"])
                     for drone in payload}
        args = event.get("args")
        actor = str(args.get("drone_id")) if isinstance(args, dict) and args.get("drone_id") else None
        if name in MOVES and actor in positions:
            endpoint = actual_endpoint(event)
            if endpoint is not None:
                positions[actor] = endpoint
            elif event["status"] in {"completed", "partial", "unknown"}:
                positions[actor] = None
        if name == "sense_nearby_entities" and isinstance(payload, dict):
            for drone_id, nearby in payload.get("per_drone", {}).items():
                if drone_id not in positions or positions[drone_id] is None:
                    skipped.append({"sequence": sequence, "drone_id": drone_id})
                    continue
                samples.append({"sequence": sequence, "drone_id": drone_id,
                                "xy": list(positions[drone_id]), "radius_m": radii[drone_id],
                                "returned_target_ids": sorted(target_ids(nearby))})
            sensing_events.append(sequence)
        elif name == "get_nearby_entities" and isinstance(payload, dict) and actor in positions:
            if positions[actor] is None:
                skipped.append({"sequence": sequence, "drone_id": actor})
            else:
                samples.append({"sequence": sequence, "drone_id": actor,
                                "xy": list(positions[actor]), "radius_m": radii[actor],
                                "returned_target_ids": sorted(target_ids(payload))})
                sensing_events.append(sequence)
        elif name in SENSING_MOVES and isinstance(payload, dict) and actor in positions:
            nearby = payload.get("nearby")
            if positions[actor] is None or not isinstance(nearby, dict):
                skipped.append({"sequence": sequence, "drone_id": actor})
            else:
                samples.append({"sequence": sequence, "drone_id": actor,
                                "xy": list(positions[actor]), "radius_m": radii[actor],
                                "returned_target_ids": sorted(target_ids(nearby))})
                sensing_events.append(sequence)
    if listed is None or not samples:
        raise ValueError("No public initial fleet or sensing samples")
    return listed, samples, skipped, sorted(set(sensing_events)), known, positions


def local_free_area(target: dict, blocked) -> float:
    center = target["position"]
    # The frozen task uses 2D reach distance and every drone task_radius=20.
    return Point(float(center["x"]), float(center["y"])).buffer(20, quad_segs=128).difference(blocked).area


def audit(task_id: str) -> dict:
    trace_path, trace = trace_for(task_id)
    events = sorted(trace["task_progress"]["events"],
                    key=lambda event: event.get("end_sequence") or float("inf"))
    progress = [event for event in events if event["tool_name"] == "get_mission_progress"
                and isinstance(event.get("payload"), dict)]
    stop = progress[-1]
    listed, samples, skipped, sense_sequences, known, last_positions = visible_samples(
        events, stop["end_sequence"])
    session_calls = [call for call in trace["api_calls"] if call["endpoint"] == "/sessions/current"]
    if len(session_calls) != len(progress) + 1:
        raise ValueError("Progress timestamp alignment changed")
    stop_time = datetime.fromisoformat(session_calls[-1]["completed_at"])
    window = read(RUN / f"{task_id}.window.json")
    cutoff = datetime.fromtimestamp(window["cutoff_at"])
    if stop_time > cutoff or len(listed) != 30 or any(float(d["task_radius"]) != 20 for d in listed):
        raise ValueError("Unexpected fleet or budget")
    roi = public_fleet_envelope(listed)
    bounds = roi["bounds_xyxy"]
    region = box(bounds[0], bounds[2], bounds[1], bounds[3])
    disks = [Point(*sample["xy"]).buffer(sample["radius_m"], quad_segs=32)
             for sample in samples]
    first_sequence = min(sense_sequences)
    first_disks = [disk for sample, disk in zip(samples, disks)
                   if sample["sequence"] == first_sequence]
    covered = unary_union(disks).intersection(region)
    initially_covered = unary_union(first_disks).intersection(region)
    # At this point public positions, the ROI, and all sensing disks are frozen.
    before = read(RUN / f"{task_id}.before.json")
    after = read(RUN / f"{task_id}.after.json")
    if before["is_distance_3d"] is not False:
        raise ValueError("Unexpected 3D distance mode")
    targets = {str(target["id"]): target for target in after["targets"]}
    if len(targets) != 100 or any(target["type"] != "fixed" for target in targets.values()):
        raise ValueError("Unexpected target cohort")
    controller = ObstacleController()
    for obstacle in before["obstacles"]:
        controller.add_obstacle(obstacle)
    if any(obstacle.height != 0 for obstacle in controller.obstacles.values()):
        raise ValueError("Unexpected altitude-dependent obstacle")
    blocked = unary_union([o.to_geometry() for o in controller.obstacles.values()
                           if o.to_geometry() is not None])
    free_region = region.difference(blocked)
    free_components = list(free_region.geoms) if hasattr(free_region, "geoms") else [free_region]
    drone_components = {index for index, component in enumerate(free_components)
                        if any(position is not None and component.covers(Point(*position))
                               for position in last_positions.values())}
    mismatches = []
    for sample in samples:
        x, y = sample["xy"]
        # Validate reconstructed origins against the exact fixed-target
        # visibility relation, after freezing the positions.
        predicted = {tid for tid, target in targets.items()
                     if math.hypot(float(target["position"]["x"]) - x,
                                   float(target["position"]["y"]) - y)
                     - float(target["radius"]) <= sample["radius_m"] + 1e-7}
        actual = set(sample["returned_target_ids"])
        if predicted != actual:
            mismatches.append({"sequence": sample["sequence"], "drone_id": sample["drone_id"],
                               "predicted_only": sorted(predicted - actual),
                               "returned_only": sorted(actual - predicted)})
    if mismatches or skipped:
        raise ValueError(f"Sensing origin reconstruction uncertain: {task_id} {mismatches[:2]} {skipped[:2]}")
    raw_seen: dict[str, list] = {}
    for call in trace["api_calls"]:
        if (call["endpoint"].endswith("/nearby") and call["status_code"] == 200
                and datetime.fromisoformat(call["completed_at"]) <= stop_time):
            for tid in target_ids(call.get("response") or {}):
                raw_seen.setdefault(tid, []).append(call["endpoint"])
    residual = []
    for tid, target in targets.items():
        if target["is_reached"]:
            continue
        point = target["position"]
        x, y = float(point["x"]), float(point["y"])
        gap = min(math.hypot(x - sample["xy"][0], y - sample["xy"][1])
                  - sample["radius_m"] - float(target["radius"])
                  for sample in samples)
        free = local_free_area(target, blocked)
        same_free_component = any(index in drone_components and component.covers(Point(x, y))
                                  for index, component in enumerate(free_components))
        residual.append({"id": tid, "name": target["name"],
                         "publicly_localized": tid in known,
                         "raw_nearby_seen_before_last_progress": tid in raw_seen,
                         "raw_nearby_query_count": len(raw_seen.get(tid, [])),
                         "min_gap_to_explicit_sensing_m": round(max(gap, 0.0), 2),
                         "inside_public_fleet_envelope": bounds[0] <= x <= bounds[1]
                                                        and bounds[2] <= y <= bounds[3],
                         "center_blocked_offline": bool(controller.check_point_collision(x, y, 20, margin=0)["result"]),
                         "local_20m_reach_free_area_m2": round(free, 2),
                         "locally_reachable_endpoint_exists": free > 0.01,
                         "same_static_free_component_as_drone": same_free_component})
    residual.sort(key=lambda item: item["name"])
    return {"session_id": "823c15d5", "task_id": task_id,
            "trace": str(trace_path.relative_to(ROOT)),
            "last_public_progress_percent": stop["payload"]["progress_percentage"],
            "last_progress_sequence": stop["end_sequence"],
            "explicit_sensing_tool_events": len(sense_sequences),
            "explicit_sensing_samples": len(samples),
            "sensing_position_validation_mismatches": len(mismatches),
            "sensing_position_skipped_samples": len(skipped),
            "soft_public_fleet_envelope_area_m2": round(region.area, 1),
            "first_sense_covered_area_m2": round(initially_covered.area, 1),
            "last_progress_covered_area_m2": round(covered.area, 1),
            "new_covered_area_after_first_sense_m2": round(covered.area - initially_covered.area, 1),
            "last_progress_covered_fraction_of_soft_envelope": round(covered.area / region.area, 5),
            "raw_api_nearby_unique_targets_before_last_progress": len(raw_seen),
            "model_visible_localized_targets_at_last_progress": len(known),
            "static_free_components_in_soft_envelope": len(free_components),
            "known_last_drone_positions": sum(position is not None for position in last_positions.values()),
            "post_run_unreached_targets": residual,
            "summary": {
                "post_run_unreached": len(residual),
                "not_publicly_localized": sum(not x["publicly_localized"] for x in residual),
                "outside_explicit_sensing": sum(x["min_gap_to_explicit_sensing_m"] > 0 for x in residual),
                "raw_seen_but_not_publicly_localized": sum(
                    x["raw_nearby_seen_before_last_progress"] and not x["publicly_localized"]
                    for x in residual),
                "outside_soft_fleet_envelope": sum(not x["inside_public_fleet_envelope"] for x in residual),
                "local_reach_endpoint_exists": sum(x["locally_reachable_endpoint_exists"] for x in residual),
                "same_static_free_component_as_drone": sum(
                    x["same_static_free_component_as_drone"] for x in residual),
                "center_blocked": sum(x["center_blocked_offline"] for x in residual),
            }}


def main():
    if not frozen_server_match():
        raise ValueError("Current sensing/collision source differs from frozen R2 run")
    rows = [audit(task_id) for task_id in TASK_IDS]
    result = {
        "schema": 1,
        "source_matches_frozen_r2": True,
        "method": "Freeze public explicit sensing XY disks at the last progress event, verify each sample against frozen server visibility, then use complete scene snapshots for post-run target and local obstacle evaluation only.",
        "limits": [
            "Soft fleet envelope (initial public drone XY bounds + 200m) is not a proven target distribution or world boundary.",
            "Only explicit completed sensing tool outputs count as model-visible sensing. Raw navigation-internal nearby API queries are reported separately.",
            "Coverage disks represent endpoint sensing; ordinary flight does not expose a continuous sensing swath.",
            "Local free area within target reach radius does not prove a collision-free path from any drone, adequate battery, or completion within 500s.",
            "Connected free-space geometry is a topological screen only; it does not verify the platform planner, route length or flight budget.",
            "Post-run unreached target geometry is diagnostic oracle data, never an input to the candidate search region or sensing samples.",
        ],
        "rows": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(row["task_id"], "samples", row["explicit_sensing_samples"],
              "covered_fraction", row["last_progress_covered_fraction_of_soft_envelope"],
              "summary", json.dumps(row["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
