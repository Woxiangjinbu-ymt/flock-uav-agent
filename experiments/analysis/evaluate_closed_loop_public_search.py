#!/usr/bin/env python3
"""Explore public-feedback replanning on the three frozen R2 progress tasks.

The policy receives only the first public fleet/sense and simulated public
movement/sense receipts. The oracle alone reads complete task snapshots.
This is a geometric mechanism probe, not a server or timed agent run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from config.battery_config import BatteryConfig  # noqa: E402
from controllers.obstacle_controller import ObstacleController  # noqa: E402

from build_public_aggregate_search_plan import (  # noqa: E402
    DISTANCE_SCALE_M, GRID_STEP_M, ROUNDS_PER_DRONE, SAFETY_BUFFER_M,
    SCORE_CELL_STEP_M, SCENE, TASK_IDS, lattice, public_inputs, trace_for,
    _obstacle_to_shape,
)
from evaluate_public_aggregate_search_plan import (  # noqa: E402
    FROZEN_PLAN_SHA256, PLAN, RUN, frozen_source_match,
    initial_public_known, public_query_returned, read, target_shape,
)

OUTPUT = ROOT / "docs/audit/r2-closed-loop-search-20261009/evaluation.json"
ABLATION_OUTPUT = ROOT / "docs/audit/r2-closed-loop-search-20261009/ablation-no-new-obstacles.json"
PREFIX_ROUNDS = (4, 8, 16)


class PublicPolicy:
    """No complete scene, hidden target, or checker is accepted here."""

    def __init__(self, drones: list[dict], obstacles: list[dict], bounds: list[float], radius: float,
                 learn_new_obstacles: bool):
        self.ids = sorted(str(drone["id"]) for drone in drones)
        self.xy = {str(drone["id"]): (float(drone["position"]["x"]),
                                           float(drone["position"]["y"])) for drone in drones}
        self.known_obstacles = {str(item["id"]): item for item in obstacles}
        self.obstacle_sources: dict[str, dict] = {}
        self.radius = radius
        self.learn_new_obstacles = learn_new_obstacles
        self.candidates = lattice(bounds, GRID_STEP_M)
        self.score_cells = lattice(bounds, SCORE_CELL_STEP_M)
        self.candidate_cover = [frozenset(i for i, cell in enumerate(self.score_cells)
                                          if math.dist(point, cell) <= radius)
                                for point in self.candidates]
        self.covered = {i for i, cell in enumerate(self.score_cells)
                        if any(math.dist(cell, xy) <= radius for xy in self.xy.values())}
        self.initial_covered_count = len(self.covered)
        self.selected: set[int] = set()
        self.tried_edges: set[tuple] = set()
        self.disabled: set[str] = set()
        self._refresh_blocked()

    def _refresh_blocked(self):
        self.blocked = unary_union([_obstacle_to_shape(item)
                                    for item in self.known_obstacles.values()]).buffer(SAFETY_BUFFER_M)

    def choose(self, actor: str) -> tuple[int, tuple[float, float], float] | None:
        if actor in self.disabled:
            return None
        start = self.xy[actor]
        ranked = []
        for i, destination in enumerate(self.candidates):
            if i in self.selected or (actor, start, i) in self.tried_edges:
                continue
            novel = len(self.candidate_cover[i] - self.covered)
            if not novel:
                continue
            travel = math.dist(start, destination)
            score = novel / (1.0 + travel / DISTANCE_SCALE_M)
            ranked.append((-score, travel, i))
        ranked.sort()
        for _, travel, i in ranked:
            destination = self.candidates[i]
            if self.blocked.intersects(Point(*destination)):
                continue
            if self.blocked.intersects(LineString([start, destination])):
                continue
            return i, destination, travel
        return None

    def accept(self, actor: str, index: int, feedback: dict, round_number: int, sequence: int):
        start = self.xy[actor]
        self.tried_edges.add((actor, start, index))
        if feedback["outcome"] == "completed":
            endpoint = tuple(feedback["position_xy"])
            if endpoint != self.candidates[index]:
                raise ValueError("Simulated public endpoint differs from requested point")
            self.xy[actor] = endpoint
            self.selected.add(index)
            self.covered.update(self.candidate_cover[index])
        if feedback["outcome"] in {"battery", "takeoff_blocked"}:
            self.disabled.add(actor)
        changed = False
        for obstacle in feedback["nearby_obstacles"] if self.learn_new_obstacles else ():
            oid = str(obstacle["id"])
            if oid not in self.known_obstacles:
                self.known_obstacles[oid] = obstacle
                self.obstacle_sources[oid] = {
                    "round": round_number, "sequence": sequence, "observer": actor,
                    "observed_at_xy": feedback["position_xy"],
                    "receipt": "nearby_after_" + feedback["outcome"],
                }
                changed = True
        if changed:
            self._refresh_blocked()


class OfflineOracle:
    """The private simulator never passes truth coordinates to PublicPolicy."""

    def __init__(self, before: dict, initial_public_returned: set[str]):
        self.before = before
        self.targets = {str(item["id"]): target_shape(item) for item in before["targets"]}
        self.reached_before = {str(item["id"]) for item in before["targets"] if item["is_reached"]}
        self.reached = set(self.reached_before)
        self.discovered = set(initial_public_returned)
        self.obstacles = ObstacleController()
        for item in before["obstacles"]:
            self.obstacles.add_obstacle(item)
        self.state = {}
        for item in before["drones"]:
            did = str(item["id"])
            if item["status"] not in {"idle", "ready"}:
                raise ValueError("Expected a grounded task-start fleet")
            self.state[did] = {
                "xy": (float(item["position"]["x"]), float(item["position"]["y"])),
                "battery": float(item["battery_level"]),
                "heading": float(item["heading"]), "airborne": False,
            }
        self.sense_receipts = 0

    def _nearby(self, xy: tuple[float, float]) -> tuple[list[dict], list[str]]:
        point = Point(*xy)
        nearby_obstacles = [item.to_dict() for item in self.obstacles.obstacles.values()
                            if item.to_geometry() is not None and item.to_geometry().distance(point) <= 50]
        nearby_targets = [tid for tid, shape in self.targets.items() if shape.distance(point) <= 50]
        self.discovered.update(nearby_targets)
        self.sense_receipts += 1
        return nearby_obstacles, nearby_targets

    def attempt(self, actor: str, destination: tuple[float, float]) -> tuple[dict, dict]:
        state = self.state[actor]
        start = state["xy"]
        reason = None
        blocker_id = None
        if not state["airborne"]:
            result = self.obstacles.check_point_collision(*start, 20, margin=0)
            if result["result"]:
                reason = "takeoff_blocked"
                blocker_id = str(result["inside_obstacles"][0]["id"])
            elif state["battery"] - 0.5 < BatteryConfig.CRITICAL_BATTERY_LEVEL:
                reason = "battery"
            else:
                state["battery"] -= 0.5
                state["airborne"] = True
        if reason is None:
            begin = {"x": start[0], "y": start[1], "z": 20.0}
            end = {"x": destination[0], "y": destination[1], "z": 20.0}
            cost = BatteryConfig.calculate_movement_cost(
                "move_to", begin, end, weather="clear",
                environment=self.before["environment"], drone_heading=state["heading"])
            if cost > state["battery"] - BatteryConfig.CRITICAL_BATTERY_LEVEL:
                reason = "battery"
            else:
                endpoint = self.obstacles.check_point_collision(*destination, 20, margin=0)
                path = self.obstacles.check_path_collision(*start, *destination, 20, 20, safety_margin=0)
                if endpoint["result"]:
                    reason = "endpoint_collision"
                    blocker_id = str(endpoint["inside_obstacles"][0]["id"])
                elif path["collision"]:
                    reason = "path_collision"
                    blocker_id = str(path["obstacle"]["id"])
        if reason is None:
            line = LineString([start, destination])
            self.reached.update(tid for tid, shape in self.targets.items() if shape.distance(line) <= 20)
            state["xy"] = destination
            state["battery"] -= cost
            dx, dy = destination[0] - start[0], destination[1] - start[1]
            heading = math.degrees(math.atan2(dx, dy))
            state["heading"] = heading + 360 if heading < 0 else heading
        nearby_obstacles, nearby_targets = self._nearby(state["xy"])
        public = {"outcome": "completed" if reason is None else
                  ("blocked" if reason in {"endpoint_collision", "path_collision"} else reason),
                  "position_xy": list(state["xy"]), "battery_percent": round(state["battery"], 5),
                  "nearby_obstacles": nearby_obstacles, "nearby_target_ids": nearby_targets}
        private = {"reason": reason, "blocker_id": blocker_id,
                   "segment_m": round(math.dist(start, destination), 3) if reason is None else 0.0,
                   "battery_cost_percent": round(cost, 5) if reason is None else 0.0}
        return public, private


def run_one(plan: dict, learn_new_obstacles: bool) -> dict:
    task_id = plan["task_id"]
    trace_path, trace = trace_for(task_id)
    drones, obstacles, bounds, radius, first_sense_sequence = public_inputs(trace)
    if (str(trace_path.relative_to(ROOT)) != plan["trace"] or
            first_sense_sequence != plan["first_full_sense_end_sequence"] or
            bounds != plan["public_input"]["soft_envelope_bounds_xyxy"]):
        raise ValueError("Frozen public input differs from original plan")
    before = read(RUN / f"{task_id}.before.json")
    scored = next(row for row in read(RUN.parent / "results.json") if row["task_id"] == task_id)
    leaves = scored["leaves"]
    if (len(leaves) != 1 or leaves[0]["definition"]["endpoint"] != "/check/task_progress" or
            float(leaves[0]["definition"]["parameters"]["expected_progress"]) != 0.95):
        raise ValueError("Original checker definition changed")
    if len(before["targets"]) != 100 or len(before["drones"]) != 30 or before["is_distance_3d"] is not False:
        raise ValueError("Unexpected geometry or fleet")
    policy = PublicPolicy(drones, obstacles, bounds, radius, learn_new_obstacles)
    oracle = OfflineOracle(before, public_query_returned(plan["trace"]))
    for actor in policy.ids:
        if policy.xy[actor] != oracle.state[actor]["xy"]:
            raise ValueError("First public fleet position and task-start snapshot differ")
    initial_known = initial_public_known(plan["trace"])
    initial_obstacles = set(policy.known_obstacles)
    attempts = []
    checkpoints = []
    for round_number in range(1, ROUNDS_PER_DRONE + 1):
        for actor in policy.ids:
            choice = policy.choose(actor)
            if choice is None:
                continue
            index, destination, travel = choice
            start = policy.xy[actor]
            public, private = oracle.attempt(actor, destination)
            sequence = len(attempts) + 1
            policy.accept(actor, index, public, round_number, sequence)
            attempts.append({"sequence": sequence, "round": round_number, "drone_id": actor,
                             "from_xy": list(start), "to_xy": list(destination),
                             "requested_segment_m": round(travel, 3),
                             "outcome": public["outcome"],
                             "sensed_obstacle_ids": [str(item["id"]) for item in public["nearby_obstacles"]],
                             "sensed_target_ids": public["nearby_target_ids"],
                             "public_battery_percent": public["battery_percent"],
                             "geometric_progress_after": len(oracle.reached),
                             "public_discovered_target_count_after": len(oracle.discovered),
                             "offline_diagnostic": private})
        if round_number in PREFIX_ROUNDS:
            completed = [item for item in attempts if item["outcome"] == "completed"]
            new_discovered = (oracle.discovered - initial_known) - oracle.reached_before
            checkpoints.append({"rounds_per_drone": round_number,
                                "attempts": len(attempts), "accepted_endpoints": len(completed),
                                "blocked_attempts": sum(item["outcome"] == "blocked" for item in attempts),
                                "public_new_obstacles": len(policy.known_obstacles.keys() - initial_obstacles),
                                "successful_endpoint_senses": len(completed),
                                "all_post_attempt_senses": oracle.sense_receipts,
                                "covered_public_score_cells": len(policy.covered),
                                "travel_km": round(sum(item["offline_diagnostic"]["segment_m"]
                                                       for item in completed) / 1000, 2),
                                "new_before_unreached_discovered": len(new_discovered),
                                "new_before_unreached_reached": len(oracle.reached - oracle.reached_before),
                                "geometric_progress_percent": len(oracle.reached),
                                "original_checker_threshold_met": len(oracle.reached) >= 95,
                                "task_text_strict_threshold_met": len(oracle.reached) >= 96,
                                "minimum_battery_percent": round(min(s["battery"] for s in oracle.state.values()), 2)})
    for oid, source in policy.obstacle_sources.items():
        event = attempts[source["sequence"] - 1]
        if oid not in event["sensed_obstacle_ids"] or source["observed_at_xy"] != (
                event["to_xy"] if event["outcome"] == "completed" else event["from_xy"]):
            raise ValueError("New obstacle has no public sensing source")
    if any(sum(item["drone_id"] == actor for item in attempts) > ROUNDS_PER_DRONE for actor in policy.ids):
        raise ValueError("Per-drone action budget exceeded")
    return {"task_id": task_id, "start_progress_percent": len(oracle.reached_before),
            "initial_public_known_targets": len(initial_known),
            "initial_public_obstacles": len(initial_obstacles),
            "candidate_lattice_points": len(policy.candidates),
            "initial_covered_score_cells": policy.initial_covered_count,
            "checkpoint_rows": checkpoints,
            "outcomes": dict(Counter(item["outcome"] for item in attempts)),
            "private_failure_reasons": dict(Counter(item["offline_diagnostic"]["reason"]
                                                    for item in attempts if item["outcome"] != "completed")),
            "new_obstacle_sources": policy.obstacle_sources,
            "attempts": attempts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-new-obstacles", action="store_true",
                        help="Diagnostic ablation: replan from receipts but do not learn new obstacle geometry")
    args = parser.parse_args()
    learn_new_obstacles = not args.no_new_obstacles
    digest = hashlib.sha256(PLAN.read_bytes()).hexdigest()
    if digest != FROZEN_PLAN_SHA256 or not frozen_source_match():
        raise ValueError("Original public plan or server source no longer matches frozen R2")
    plans = read(PLAN)
    if [item["task_id"] for item in plans["plans"]] != list(TASK_IDS):
        raise ValueError("Original task order changed")
    rows = [run_one(item, learn_new_obstacles) for item in plans["plans"]]
    result = {"schema": 1, "frozen_open_loop_plan_sha256": digest,
              "policy_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "public_planner_no_hidden_scene_inputs": True,
              "learn_new_obstacles": learn_new_obstacles,
              "method": "One direct XY movement/sensing attempt per UAV per round, at most 16 rounds; after each actual public receipt, recompute remaining public coverage and choose a new direct-clear public lattice point. The main arm learns only nearby obstacles; the diagnostic ablation ignores new obstacle shapes. Private scene is used only by offline geometry simulator and post hoc scoring.",
              "limits": ["This is not a live server run, timed 500-second agent, or a paired success-rate estimate.",
                         "Each original sequential task is replayed independently from its own task-start snapshot.",
                         "A modeled direct move and post-attempt nearby sense may differ from navigate_to_and_sense, which has an internal one-shot route fallback.",
                         "Weather latency, API overhead, planner CPU cost, and model time are not simulated.",
                         "Original checker accepts >=95%; task wording says >95%."],
              "rows": rows}
    output_path = OUTPUT if learn_new_obstacles else ABLATION_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{"task_id": row["task_id"], "checkpoints": row["checkpoint_rows"],
                       "outcomes": row["outcomes"]} for row in rows], ensure_ascii=False))


if __name__ == "__main__":
    main()
