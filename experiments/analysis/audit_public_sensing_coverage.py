#!/usr/bin/env python3
"""Reconstruct endpoint sensing footprints from frozen public R2 tool receipts.

Coverage is clipped to the public fleet envelope for comparability. Unknown or
ambiguous movement endpoints are skipped and counted, never replaced by the
requested waypoint. Hidden target geometry is evaluated only after footprints
are frozen; no target position influences coverage reconstruction.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from shapely.geometry import GeometryCollection, Point, box

from audit_unknown_target_search_space import (CASES, DECISIONS, ROOT,
                                                public_fleet_envelope, read,
                                                target_geometry)

OUTPUT = ROOT / "docs/audit/unknown-target-search-space-20261009/coverage.json"
SENSING_MOVES = {"navigate_to_and_sense", "move_to_and_sense",
                 "move_towards_and_sense", "move_along_path_and_sense"}
MOVES = SENSING_MOVES | {"navigate_to", "move_to", "move_towards", "move_along_path"}
MOVE_MESSAGE = re.compile(r"to position \(([-+\d.]+), ([-+\d.]+), ([-+\d.]+)\)")


def xy(value):
    if isinstance(value, dict) and "x" in value and "y" in value:
        return float(value["x"]), float(value["y"])
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    return None


def actual_endpoint(event):
    """Return only a position certified by a completed/public movement reply."""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    nav = payload.get("navigation_result")
    if isinstance(nav, dict) and xy(nav.get("final_position")):
        return xy(nav["final_position"])
    receipt = payload.get("move_result", payload)
    if not isinstance(receipt, dict):
        return None
    if xy(receipt.get("final_position")):
        return xy(receipt["final_position"])
    points = receipt.get("successful_points")
    if isinstance(points, list) and points and xy(points[-1]):
        return xy(points[-1])
    message = receipt.get("message", "")
    matched = MOVE_MESSAGE.search(message) if isinstance(message, str) else None
    if matched and event["status"] in {"completed", "partial"}:
        return float(matched.group(1)), float(matched.group(2))
    if (event["tool_name"] in {"move_to", "move_to_and_sense"}
            and event["status"] == "completed" and "x" in event["args"]
            and "y" in event["args"]):
        return float(event["args"]["x"]), float(event["args"]["y"])
    return None


def checkpoint(decision, trace):
    for threshold in ("8", "5", "3"):
        fired = decision["firings"]["stall"][threshold]
        if fired:
            return {"kind": f"first_k{threshold}_trigger", "end_sequence": fired["end_sequence"]}
    return {"kind": "task_end_no_stall_trigger",
            "end_sequence": max((item["end_sequence"] or 0)
                                for item in trace["task_progress"]["events"])}


def audit_one(decision):
    trace_path = ROOT / decision["trace"]
    trace = read(trace_path)
    events = trace["task_progress"]["events"]
    listed = next(item["payload"] for item in events if item["tool_name"] == "list_drones")
    drones = {item["id"]: item for item in listed}
    current = {actor: xy(drone["position"]) for actor, drone in drones.items()}
    roi = public_fleet_envelope(listed)
    bounds = roi["bounds_xyxy"]
    region = box(bounds[0], bounds[2], bounds[1], bounds[3])
    stop = checkpoint(decision, trace)
    covered = GeometryCollection()
    samples = []
    increments = []
    skipped_unknown_endpoint = []
    for event in sorted(events, key=lambda item: item["end_sequence"] or float("inf")):
        sequence = event["end_sequence"]
        if sequence is None or sequence > stop["end_sequence"]:
            continue
        name = event["tool_name"]
        payload = event["payload"]
        actor = event["args"].get("drone_id")
        if name in MOVES and actor in drones:
            endpoint = actual_endpoint(event)
            if endpoint:
                current[actor] = endpoint
            elif event["status"] in {"completed", "partial", "unknown"}:
                current[actor] = None
        sensing_actors = []
        if name == "sense_nearby_entities" and isinstance(payload, dict):
            sensing_actors = [str(item) for item in payload.get("sensed_drone_ids", [])]
        elif name in SENSING_MOVES or name == "get_nearby_entities":
            if actor:
                sensing_actors = [str(actor)]
        before = covered.area
        valid_count = 0
        for sensing_actor in sensing_actors:
            position = current.get(sensing_actor)
            if sensing_actor not in drones or position is None:
                skipped_unknown_endpoint.append(event["evidence_id"])
                continue
            valid_count += 1
            radius = float(drones[sensing_actor]["perceived_radius"])
            samples.append({"evidence_id": event["evidence_id"],
                            "actor_id": sensing_actor, "xy": list(position),
                            "sensor_radius_m": radius})
            covered = covered.union(Point(*position).buffer(radius).intersection(region))
        if valid_count:
            increments.append({"evidence_id": event["evidence_id"], "tool": name,
                               "status": event["status"], "samples": valid_count,
                               "new_area_m2": round(covered.area - before, 1)})
    snapshot = read(trace_path.parents[2] / f"{decision['task_id']}.before.json")
    targets_by_name = {item["name"]: item for item in snapshot["targets"]}
    missing = [item for item in trace["task_progress"]["task_evidence"]["requirements"]
               if item["relation"] == "visit" and item["knowledge_state"] != "localized"]
    missing_distances = []
    for item in missing:
        geometry = target_geometry(targets_by_name[item["target_name"]])
        nearest_gap = min((Point(*sample["xy"]).distance(geometry) - sample["sensor_radius_m"]
                           for sample in samples), default=float("inf"))
        missing_distances.append({"target_name": item["target_name"],
                                  "minimum_uncovered_gap_m": round(max(0.0, nearest_gap), 1)})
    return {"session_id": decision["session_id"], "task_id": decision["task_id"],
            "checkpoint": stop, "public_sensing_samples": len(samples),
            "skipped_unknown_endpoint_events": sorted(set(skipped_unknown_endpoint)),
            "covered_area_m2": round(covered.area, 1),
            "covered_fraction_of_soft_fleet_envelope": round(covered.area / roi["area_m2"], 5),
            "last_three_completed_sensing_move_new_area_m2": [item["new_area_m2"]
                for item in increments if item["tool"] in SENSING_MOVES and item["status"] == "completed"][-3:],
            "missing_target_eval_only": missing_distances,
            "sensing_increments": increments}


def main():
    decisions = read(DECISIONS)
    cases = [audit_one(item) for item in decisions if item["task_id"] in CASES]
    assert len(cases) == 6
    output = {"method": "public-receipt-endpoint-sensing-footprint-audit",
              "search_region_source": "public list_drones XY envelope plus frozen 200m margin",
              "cases": cases,
              "limitations": ["Only actual sensing endpoints are counted; no continuous path sensing is inferred",
                              "Unknown movement endpoints are skipped, so coverage may be underestimated",
                              "The fleet envelope is a soft proxy, not a proven target support",
                              "Target-to-footprint gaps use SYSTEM geometry only after coverage is frozen for offline evaluation",
                              "For two tasks without a qualifying trigger, checkpoint is task end and not an intervention opportunity"]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{"task_id": item["task_id"], "checkpoint": item["checkpoint"]["kind"],
                       "coverage_fraction": item["covered_fraction_of_soft_fleet_envelope"],
                       "last_three_marginal_m2": item["last_three_completed_sensing_move_new_area_m2"],
                       "skipped": len(item["skipped_unknown_endpoint_events"])} for item in cases],
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
