#!/usr/bin/env python3
"""Evaluate a previously frozen public-only search plan against offline truth.

The plan checksum is fixed here before this script reads any complete scene.
This is a deterministic direct-segment replay, not an agent/server trial.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
from zipfile import ZipFile

from shapely.geometry import LineString, Point

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403/823c15d5"
PLAN = ROOT / "docs/audit/r2-public-aggregate-search-plan-20261009/plans.json"
OUTPUT = ROOT / "docs/audit/r2-public-aggregate-search-plan-20261009/evaluation.json"
FROZEN_PLAN_SHA256 = "8ab8cd3f7bc381b67d8edd54ccd860eafb6090d635a29991036d9239f80e0d45"

sys.path.insert(0, str(ROOT / "server"))
from config.battery_config import BatteryConfig  # noqa: E402
from controllers.obstacle_controller import ObstacleController  # noqa: E402


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def frozen_source_match() -> bool:
    names = ("server/api/server.py", "server/config/battery_config.py", "server/controllers/drone_controller.py",
             "server/controllers/obstacle_controller.py", "server/models/target.py",
             "server/models/obstacle.py", "server/models/session.py")
    with ZipFile(RUN.parent / "source.zip") as archive:
        return all(archive.read(name) == (ROOT / name).read_bytes() for name in names)


def initial_public_known(trace_path: str) -> set[str]:
    trace = read(ROOT / trace_path)
    event = next(item for item in trace["task_progress"]["events"]
                 if item["tool_name"] == "sense_nearby_entities"
                 and item["status"] == "observed" and isinstance(item.get("payload"), dict))
    return {str(target["id"]) for target in event["payload"]["blackboard"]["targets"]}


def public_query_returned(first_sense_trace_path: str) -> set[str]:
    trace = read(ROOT / first_sense_trace_path)
    event = next(item for item in trace["task_progress"]["events"]
                 if item["tool_name"] == "sense_nearby_entities"
                 and item["status"] == "observed" and isinstance(item.get("payload"), dict))
    return {str(target["id"])
            for nearby in event["payload"].get("per_drone", {}).values()
            for target in nearby.get("targets", [])}


def initial_public_obstacle_ids(trace_path: str) -> set[str]:
    trace = read(ROOT / trace_path)
    event = next(item for item in trace["task_progress"]["events"]
                 if item["tool_name"] == "sense_nearby_entities"
                 and item["status"] == "observed" and isinstance(item.get("payload"), dict))
    return {str(obstacle["id"]) for obstacle in event["payload"]["blackboard"]["obstacles"]}


def target_shape(target: dict):
    return Point(float(target["position"]["x"]), float(target["position"]["y"])).buffer(
        float(target["radius"]))


def run_one(plan: dict) -> dict:
    task_id = plan["task_id"]
    scored = next(row for row in read(RUN.parent / "results.json") if row["task_id"] == task_id)
    leaves = scored["leaves"]
    if (len(leaves) != 1 or leaves[0]["definition"]["endpoint"] != "/check/task_progress"
            or float(leaves[0]["definition"]["parameters"]["expected_progress"]) != 0.95):
        raise ValueError("Original external checker changed")
    before = read(RUN / f"{task_id}.before.json")
    target_by_id = {str(target["id"]): target for target in before["targets"]}
    targets = [(tid, target_shape(target)) for tid, target in target_by_id.items()]
    drone_by_id = {str(drone["id"]): drone for drone in before["drones"]}
    if len(targets) != 100 or len(drone_by_id) != 30 or before["is_distance_3d"] is not False:
        raise ValueError("Unexpected task geometry")
    first_progress = next(item["payload"]["progress_percentage"]
                          for item in read(ROOT / plan["trace"])["task_progress"]["events"]
                          if item["tool_name"] == "get_mission_progress"
                          and isinstance(item.get("payload"), dict))
    reached_before = {tid for tid, target in target_by_id.items() if target["is_reached"]}
    if first_progress != len(reached_before):
        raise ValueError("Task-start public progress and snapshot disagree")
    initial_known = initial_public_known(plan["trace"])
    initially_returned = public_query_returned(plan["trace"])
    public_obstacles = initial_public_obstacle_ids(plan["trace"])
    controller = ObstacleController()
    for obstacle in before["obstacles"]:
        controller.add_obstacle(obstacle)
    state = {}
    for drone_id, drone in drone_by_id.items():
        pos = drone["position"]
        state[drone_id] = {"xy": (float(pos["x"]), float(pos["y"])),
                           "battery": float(drone["battery_level"]),
                           "heading": float(drone["heading"]),
                           "airborne": drone["status"] in {"hovering", "flying", "moving"},
                           "stopped": None, "blocker_id": None}
    if any(s["airborne"] for s in state.values()):
        raise ValueError("Expected all drones idle at original task start")
    discovered = set(initially_returned)
    reached = set(reached_before)
    accepted = []
    rejected = []
    checkpoints = []
    optimistic_discovered = set(initially_returned)
    def save_checkpoint(round_number: int):
        if round_number not in (4, 8, 16):
            return
        checkpoints.append({"rounds_per_drone": round_number,
                            "planned_endpoints": sum(item["round"] <= round_number for item in plan["endpoints"]),
                            "accepted_endpoints": len(accepted),
                            "stopped_drone_count": sum(bool(item["stopped"]) for item in state.values()),
                            "travel_km": round(sum(item["segment_m"] for item in accepted) / 1000, 2),
                            "new_before_unreached_discovered": len((discovered - initial_known) - reached_before),
                            "new_before_unreached_reached": len(reached - reached_before),
                            "geometric_progress_percent": len(reached),
                            "original_checker_threshold_met": len(reached) >= 95,
                            "task_text_strict_threshold_met": len(reached) >= 96,
                            "optimistic_new_before_unreached_discovered": len(
                                (optimistic_discovered - initial_known) - reached_before)})

    current_round = None
    for endpoint in plan["endpoints"]:
        round_number = endpoint["round"]
        if current_round is not None and round_number != current_round:
            save_checkpoint(current_round)
        current_round = round_number
        actor = endpoint["drone_id"]
        s = state[actor]
        proposed = tuple(float(value) for value in endpoint["to_xy"])
        for tid, shape in targets:
            if shape.distance(Point(*proposed)) <= 50:
                optimistic_discovered.add(tid)
        if s["stopped"]:
            rejected.append({"round": round_number, "drone_id": actor,
                             "reason": "after_first_" + s["stopped"]})
            continue
        if any(abs(a - b) > 1e-6 for a, b in zip(s["xy"], endpoint["from_xy"])):
            raise ValueError(f"Frozen public route origin mismatch: {task_id} {actor}")
        if not s["airborne"]:
            takeoff_collision = controller.check_point_collision(*s["xy"], 20, margin=0)
            if takeoff_collision["result"]:
                s["stopped"] = "takeoff_collision"
                s["blocker_id"] = takeoff_collision["inside_obstacles"][0]["id"]
            elif s["battery"] - 0.5 < BatteryConfig.CRITICAL_BATTERY_LEVEL:
                s["stopped"] = "takeoff_battery"
            else:
                s["battery"] -= 0.5
                s["airborne"] = True
        if s["stopped"] is None:
            start = {"x": s["xy"][0], "y": s["xy"][1], "z": 20.0}
            goal = {"x": proposed[0], "y": proposed[1], "z": 20.0}
            cost = BatteryConfig.calculate_movement_cost(
                "move_to", start, goal, weather="clear",
                environment=before["environment"], drone_heading=s["heading"])
            if cost > s["battery"] - BatteryConfig.CRITICAL_BATTERY_LEVEL:
                s["stopped"] = "battery"
            else:
                endpoint_collision = controller.check_point_collision(*proposed, 20, margin=0)
                path_collision = controller.check_path_collision(*s["xy"], *proposed, 20, 20,
                                                                 safety_margin=0)
                if endpoint_collision["result"]:
                    s["stopped"] = "endpoint_collision"
                    s["blocker_id"] = endpoint_collision["inside_obstacles"][0]["id"]
                elif path_collision["collision"]:
                    s["stopped"] = "path_collision"
                    s["blocker_id"] = path_collision["obstacle"]["id"]
        if s["stopped"]:
            rejected.append({"round": round_number, "drone_id": actor, "reason": s["stopped"]})
            continue
        path = LineString([s["xy"], proposed])
        point = Point(*proposed)
        for tid, shape in targets:
            if shape.distance(path) <= 20:
                reached.add(tid)
            if shape.distance(point) <= 50:
                discovered.add(tid)
        s["battery"] -= cost
        dx, dy = proposed[0] - s["xy"][0], proposed[1] - s["xy"][1]
        heading = math.degrees(math.atan2(dx, dy))
        s["heading"] = heading + 360 if heading < 0 else heading
        s["xy"] = proposed
        accepted.append({"round": round_number, "drone_id": actor,
                         "segment_m": endpoint["straight_segment_m"],
                         "battery_cost_percent": round(cost, 3)})
    if current_round is not None:
        save_checkpoint(current_round)
    if len(checkpoints) != 3:
        raise ValueError("Missing fixed budget prefixes")
    return {"task_id": task_id, "start_progress_percent": first_progress,
            "initial_public_known_targets": len(initial_known),
            "initial_before_unreached_known": len(initial_known - reached_before),
            "full_plan_endpoints": len(plan["endpoints"]),
            "checkpoint_rows": checkpoints,
            "first_stop_reasons": dict(Counter(item["stopped"] for item in state.values() if item["stopped"])),
            "collision_stops_on_initially_public_obstacle": sum(
                item["blocker_id"] in public_obstacles for item in state.values()
                if item["blocker_id"] is not None),
            "collision_stops_on_initially_unobserved_obstacle": sum(
                item["blocker_id"] not in public_obstacles for item in state.values()
                if item["blocker_id"] is not None),
            "accepted_segments": len(accepted),
            "rejected_or_unattempted_segments": len(rejected),
            "final_min_battery_percent": round(min(item["battery"] for item in state.values()), 2),
            "independent_from_original_sequential_scenario": True}


def main():
    blob = PLAN.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != FROZEN_PLAN_SHA256:
        raise ValueError("Public-only plan changed after freezing")
    if not frozen_source_match():
        raise ValueError("Current collision/reach/battery source differs from frozen R2")
    plan = json.loads(blob)
    if plan["fixed_prefix_rounds"] != [4, 8, 16]:
        raise ValueError("Plan budget schedule changed")
    rows = [run_one(item) for item in plan["plans"]]
    result = {"schema": 1, "frozen_plan_sha256": digest,
              "method": "Independent deterministic replay from each original task-start snapshot: direct move_to XY segments at z=20, first takeoff, frozen server collision/battery equations, exact fixed-target sensing and along-path reach geometry. Stop each UAV after its first rejected segment; no replanning.",
              "limits": ["This is not the live server endpoint, an agent run, or a paired counterfactual with the original R2 trajectory.",
                         "Three tasks share one original scene but are replayed independently from their own original task-start states.",
                         "No provider scheduling or 500s wall-clock budget is simulated.",
                         "Optimistic discovery assumes every planned point is reached; direct-prefix replay rejects hidden-obstacle and battery failures.",
                         "Original checker accepts >=95%, while the task wording says >95%; both are reported separately."],
              "rows": rows}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{"task_id": row["task_id"], "start": row["start_progress_percent"],
                       "prefixes": row["checkpoint_rows"], "stops": row["first_stop_reasons"]}
                      for row in rows], ensure_ascii=False))


if __name__ == "__main__":
    main()
