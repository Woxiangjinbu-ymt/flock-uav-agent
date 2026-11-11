#!/usr/bin/env python3
"""Offline, non-causal audit of a lead-first observation worker.

The planner receives only frozen public tool replies available at the trigger.
Original scene targets are loaded *after* plan selection solely to evaluate
sensor geometry; they are never inputs to the proposed planner.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
sys.path.insert(0, str(ROOT / "server"))

from observation_worker_r2 import _valid_plan
from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2
from uav_langchain_tools import _compact_nearby
from controllers.drone_controller import DroneController
from controllers.obstacle_controller import ObstacleController
from controllers.target_controller import TargetController
from models.drone import Drone, DroneCommand
from shapely.geometry import GeometryCollection, Point, Polygon, box
from shapely.ops import nearest_points

OLD_DECISIONS = ROOT / "docs/audit/m1r2-observation-worker-shadow-20261009/decisions.json"
NEW_SUMMARY = ROOT / "docs/results/m1r2-observation-worker-prefix-20261009/summary.json"
NEW_WORKER_RUN = ROOT / "experiments/runs/20261009-144145-worker-prefix-worker-e35843d6"
OLD_SCENES = ROOT / "experiments/m1-revision2-v1/frozen/data/scenes"
OUTPUT = ROOT / "docs/audit/observation-worker-next-step-20261009/geometry.json"


def read(path):
    return json.loads(Path(path).read_text())


def event(trace, evidence_id):
    return next(item for item in trace["task_progress"]["events"]
                if item["evidence_id"] == evidence_id)


def last_public_blackboard(trace, end_sequence):
    values = [item for item in trace["task_progress"]["events"]
              if item["end_sequence"] is not None and item["end_sequence"] <= end_sequence
              and isinstance(item["payload"], dict)
              and isinstance(item["payload"].get("blackboard"), dict)]
    return values[-1]["payload"]["blackboard"] if values else {}


def select_hint_plan(unresolved, required_actor, drones, blackboard, prior_sources):
    """One-hop, no-reach scout of a known area-target hint; else abstain."""
    hints = []
    for name in unresolved:
        matches = [item for item in blackboard.get("targets", []) if item.get("name") == name]
        if len(matches) != 1:
            continue
        item = matches[0]
        if item.get("entity_type") not in {"circle", "polygon"}:
            continue
        # Full blackboard entries keep facts.position; compact tool replies
        # expose the same retained value at the top level.
        facts = item.get("facts", item)
        position = facts.get("position")
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            continue
        try:
            x, y = float(position[0]), float(position[1])
        except (ValueError, TypeError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        geometry = None
        if item["entity_type"] == "circle" and isinstance(facts.get("radius"), (int, float)):
            geometry = Point(x, y).buffer(float(facts["radius"]))
        elif item["entity_type"] == "polygon" and isinstance(facts.get("vertices"), list):
            try:
                geometry = Polygon([(float(v[0]), float(v[1])) for v in facts["vertices"]])
            except (ValueError, TypeError, IndexError):
                continue
        if geometry is not None and geometry.is_valid and not geometry.is_empty:
            source = prior_sources.get(item["id"])
            shape_matches = bool(source and source["entity_type"] == item["entity_type"]
                                 and source["position_xy"] == [x, y])
            if shape_matches and item["entity_type"] == "polygon":
                shape_matches = source["vertices_xy"] == [
                    [float(v[0]), float(v[1])] for v in facts["vertices"]]
            if shape_matches and item["entity_type"] == "circle":
                shape_matches = source["radius"] == float(facts["radius"])
            if shape_matches:
                hints.append((name, item["id"], geometry, source))
    if not hints:
        return None
    candidates = []
    for name, target_id, geometry, source in hints:
        for drone in drones:
            if (str(drone.get("id")) == required_actor or drone.get("status") not in {"idle", "ready", "hovering"}
                    or not isinstance(drone.get("battery_level"), (int, float))
                    or drone["battery_level"] < 15):
                continue
            position = drone.get("position", {})
            if not isinstance(position, dict) or "x" not in position or "y" not in position:
                continue
            origin = Point(float(position["x"]), float(position["y"]))
            sensor_radius = float(drone.get("perceived_radius", 0))
            task_radius = float(drone.get("task_radius", 0))
            standoff = task_radius + 10.0
            # A useful no-reach observation ring requires a real gap between
            # task reach and sensing range. Static sensing already ran first.
            if sensor_radius - standoff < 5 or origin.distance(geometry) <= sensor_radius:
                continue
            boundary = nearest_points(origin, geometry)[1]
            dx, dy = origin.x - boundary.x, origin.y - boundary.y
            length = math.hypot(dx, dy)
            if length <= 0:
                continue
            x, y = boundary.x + standoff * dx / length, boundary.y + standoff * dy / length
            plan = {"drone_id": drone["id"], "waypoints": [{"x": x, "y": y}]}
            validated = _valid_plan(plan, drones, required_actor)
            if validated:
                distance = math.hypot(x - origin.x, y - origin.y)
                candidates.append((distance, name, target_id, validated, standoff, source))
    if not candidates:
        return None
    distance, name, target_id, validated, standoff, source = min(candidates,
                                                key=lambda item: (item[0], item[1], item[3][0]))
    actor, points = validated
    return {"target_name": name, "source_target_id": target_id, "scout_actor_id": actor,
            "waypoints": points, "straight_line_distance_m": round(distance, 3),
            "target_boundary_standoff_m": round(standoff, 3),
            "source_observation": source,
            "status": "unverified_public_hint_not_completion_evidence"}


def prior_local_observations(all_decisions, decision):
    """Extract only successful local receipts from earlier tasks in this scene."""
    sources = {}
    for prior in sorted(all_decisions, key=lambda item: item["trace"]):
        if prior["session_id"] != decision["session_id"] or prior["trace"] >= decision["trace"]:
            continue
        trace = read(ROOT / prior["trace"])
        for item in trace["task_progress"]["events"]:
            payload = item.get("payload")
            if not isinstance(payload, dict) or item["tool_name"] != "move_towards_and_sense":
                continue
            receipt = payload.get("move_result")
            nearby = payload.get("nearby")
            if (not isinstance(receipt, dict) or receipt.get("status") != "success"
                    or not isinstance(nearby, dict)):
                continue
            for target in nearby.get("targets", []):
                pos = target.get("position")
                if not isinstance(pos, dict) or not {"x", "y"} <= pos.keys():
                    continue
                prior_entity = next((entity for entity in payload.get("blackboard", {}).get("targets", [])
                                     if entity.get("id") == target["id"]), None)
                if not prior_entity or prior_entity.get("entity_type") not in {"circle", "polygon"}:
                    continue
                sources[target["id"]] = {
                    "source_task_id": prior["task_id"], "evidence_id": item["evidence_id"],
                    "command_id": receipt.get("command_id"),
                    "source_actor_id": payload.get("drone_id"),
                    "position_xy": [float(pos["x"]), float(pos["y"])],
                    "entity_type": prior_entity["entity_type"],
                    "radius": prior_entity.get("radius"),
                    "vertices_xy": [[float(v[0]), float(v[1])]
                                    for v in prior_entity.get("vertices", [])],
                }
    return sources


def scene_for(session_id):
    matches = list(OLD_SCENES.glob(f"*_{session_id}.json"))
    if len(matches) != 1:
        raise ValueError("Original scene not found")
    return read(matches[0])


def sensor_names_at(scene, actor_id, waypoint):
    """Evaluate a frozen plan using the actual server sensing implementation."""
    targets = TargetController()
    for item in scene["targets"]:
        targets.add_target(item)
    drones = DroneController(target_controller=targets)
    drones.set_is_3d(scene["is_distance_3d"])
    source = next(item for item in scene["drones"] if item["id"] == actor_id)
    drone = Drone.from_dict(source)
    drone.position = dict(waypoint)
    drones.drones[actor_id] = drone
    return [item["name"] for item in drones.get_other_targets(actor_id)]


def simulate_one_hop(scene, public_drone, plan):
    """Offline controller feasibility; full scene enters only after planning."""
    obstacles = ObstacleController()
    for item in scene["obstacles"]:
        obstacles.add_obstacle(item)
    targets = TargetController(obstacle_controller=obstacles)
    for item in scene["targets"]:
        targets.add_target(item)
    drones = DroneController(obstacle_controller=obstacles, target_controller=targets)
    drones.set_is_3d(scene["is_distance_3d"])
    actor = plan["scout_actor_id"]
    # Public list_drones is a compact state reply, not a full Drone model.
    # Restore immutable model capabilities from the scene only in evaluation;
    # the planner above has already selected and frozen the public-state plan.
    source = next(item for item in scene["drones"] if item["id"] == actor)
    replay_drone = dict(source)
    replay_drone.update(public_drone)
    drones.add_drone(replay_drone)
    before_reached_by = list(targets.get_target(plan["source_target_id"]).get("reached_by", []))
    commands = []
    if public_drone["position"]["z"] < 1:
        commands.append(drones.send_command(actor, DroneCommand.TAKE_OFF,
                                            {"altitude": plan["waypoints"][0]["z"]})["status"])
    if all(status == "success" for status in commands):
        commands.append(drones.send_command(actor, DroneCommand.MOVE_TO,
                                            plan["waypoints"][0])["status"])
    nearby_targets = drones.get_other_targets(actor)
    detected = [item["name"] for item in nearby_targets]
    target = targets.get_target(plan["source_target_id"])
    return {"command_statuses": commands, "target_detected": plan["target_name"] in detected,
            "target_reached_by_scout": actor in target.get("reached_by", []),
            "reached_by_before": before_reached_by,
            "reached_by_after": list(target.get("reached_by", [])),
            "nearby_targets": nearby_targets,
            "final_position": drones.get_drone(actor)["position"]}


def replay_ledger_until(all_decisions, decision, end_sequence):
    """Rebuild the original R2 index and stop at the public trigger boundary."""
    index = SessionEvidenceIndexR2(decision["session_id"])
    current = None
    for row in sorted(all_decisions, key=lambda item: item["trace"]):
        if row["session_id"] != decision["session_id"] or row["trace"] > decision["trace"]:
            continue
        trace = read(ROOT / row["trace"])
        ledger = TaskEvidenceLedgerR2(trace["command"], trace["command_id"], index=index)
        timeline = [(event["start_sequence"], "start", event)
                    for event in trace["task_progress"]["events"]]
        timeline += [(event["end_sequence"], "end", event)
                     for event in trace["task_progress"]["events"]
                     if event["end_sequence"] is not None]
        stop = end_sequence if row["trace"] == decision["trace"] else float("inf")
        for sequence, kind, item in sorted(timeline, key=lambda value: value[0]):
            if sequence > stop:
                break
            if kind == "start":
                ledger.begin_tool(item["tool_name"], item["args"], item["tool_call_id"])
            elif isinstance(item["payload"], dict) and set(item["payload"]) == {"tool_exception"}:
                ledger.end_tool(item["evidence_id"], error=item["payload"]["tool_exception"])
            else:
                ledger.end_tool(item["evidence_id"], item["payload"])
        if row["trace"] == decision["trace"]:
            current = ledger
            break
    if current is None or current._active:
        raise ValueError("No complete model-request boundary at trigger")
    return current


def simulate_ledger_handoff(all_decisions, decision, plan, controller_result):
    """Pass controller-backed sensing through the unchanged R2 evidence ledger."""
    ledger = replay_ledger_until(all_decisions, decision, decision["firings"]["stall"]["8"]["end_sequence"])
    matching = lambda: next(item for item in ledger.projection()
                            if item["relation"] == "visit" and item["target_name"] == plan["target_name"])
    before = matching()
    actor = plan["scout_actor_id"]
    waypoint = plan["waypoints"][0]
    args = {"drone_id": actor, **waypoint, "detail": "summary"}
    eid = ledger.begin_tool("navigate_to_and_sense", args, "offline-controller-backed-scout")
    payload = {
        "drone_id": actor,
        "navigation_result": {
            "drone_id": actor, "success": True, "outcome": "completed",
            "arrival_verified": True, "distance_remaining": 0.0,
            "final_position": controller_result["final_position"],
            "last_move_result": {"command": "move_to", "drone_id": actor,
                                 "status": "success", "outcome": "completed"},
        },
        "nearby": _compact_nearby({"drone_id": actor,
                                   "targets": controller_result["nearby_targets"],
                                   "drones": [], "obstacles": []}),
    }
    ledger.end_tool(eid, payload)
    after = matching()
    return {"before_knowledge": before["knowledge_state"],
            "after_knowledge": after["knowledge_state"],
            "after_execution": after["execution_state"],
            "required_actor_id": after["actor_id"],
            "scout_actor_id": actor,
            "observed_position_evidence_id": after["position"]["evidence_id"]
            if after.get("position") else None,
            "injected_controller_backed_evidence_id": eid,
            "observer_errors": ledger.snapshot()["observer_errors"]}


def audit_historical(decision, all_decisions):
    trace = read(ROOT / decision["trace"])
    firing = decision["firings"]["stall"]["8"]
    end = firing["end_sequence"]
    drones = next(item["payload"] for item in trace["task_progress"]["events"]
                  if item["tool_name"] == "list_drones" and item["end_sequence"] <= end)
    blackboard = last_public_blackboard(trace, end)
    prior_sources = prior_local_observations(all_decisions, decision)
    plan = select_hint_plan(firing["unresolved_targets"], firing["actor_id"], drones,
                            blackboard, prior_sources)
    result = {"session_id": decision["session_id"], "task_id": decision["task_id"],
              "trigger_sequence": end, "unresolved_targets": firing["unresolved_targets"],
              "public_blackboard_target_names": [item.get("name") for item in blackboard.get("targets", [])],
              "plan": plan}
    if plan:
        # Task-start SYSTEM snapshot is evaluation-only and is loaded after
        # selection. With no extra actor action before the trigger, the scout
        # begins at the same position in this task-start state.
        scene = read((ROOT / decision["trace"]).parents[2] / f"{decision['task_id']}.before.json")
        source_drone = next(item for item in drones if item["id"] == plan["scout_actor_id"])
        result["offline_controller_simulation"] = simulate_one_hop(scene, source_drone, plan)
        scout_actions = [item["evidence_id"] for item in trace["task_progress"]["events"]
                         if item["end_sequence"] is not None and item["end_sequence"] <= end
                         and item["args"].get("drone_id") == plan["scout_actor_id"]
                         and item["tool_name"] in {"take_off", "move_to", "move_towards",
                                                    "move_along_path", "navigate_to",
                                                    "navigate_to_and_sense", "move_to_and_sense",
                                                    "move_towards_and_sense", "move_along_path_and_sense"}]
        result["scout_action_receipts_before_trigger"] = scout_actions
        result["offline_ledger_handoff"] = simulate_ledger_handoff(
            all_decisions, decision, plan, result["offline_controller_simulation"])
        negative_result = dict(result["offline_controller_simulation"])
        negative_result["nearby_targets"] = []
        result["offline_negative_search_control"] = simulate_ledger_handoff(
            all_decisions, decision, plan, negative_result)
    return result


def audit_actual_worker(summary):
    item = next(row for row in summary["arms"]["worker"]["tasks"] if row["task_id"] == "22e1e1c5")
    run = item["worker_runs"][0]
    scene = scene_for(item["session_id"])
    target = next(t for t in scene["targets"] if t["name"] == "Fixed Target 9")
    tx, ty = target["position"]["x"], target["position"]["y"]
    points = run["planned_waypoints"]
    sensed = [sensor_names_at(scene, run["worker_actor_id"], point) for point in points]
    width, height = float(scene["canvas_width"]), float(scene["canvas_height"])
    canvas = box(0, 0, width, height)
    nominal_coverage = canvas.intersection(
        Point(points[0]["x"], points[0]["y"]).buffer(50).union(
            Point(points[1]["x"], points[1]["y"]).buffer(50)).union(
            Point(points[2]["x"], points[2]["y"]).buffer(50))).area
    trace_paths = list(NEW_WORKER_RUN.rglob(f"*{item['command_id']}*completed.json"))
    if len(trace_paths) != 1:
        raise ValueError("Expected one saved worker task trace")
    trace = read(trace_paths[0])
    events = trace["task_progress"]["events"]
    listed = next(event["payload"] for event in events if event["tool_name"] == "list_drones")
    drones = {drone["id"]: drone for drone in listed}
    current_xy = {drone_id: (drone["position"]["x"], drone["position"]["y"])
                  for drone_id, drone in drones.items()}
    covered = GeometryCollection()
    area_events = []
    for event in events:
        sequence = event["end_sequence"]
        if sequence is None or sequence > run["trigger"]["trigger_sequence"]:
            continue
        payload = event["payload"]
        samples = []
        if isinstance(payload, dict) and isinstance(payload.get("per_drone"), dict):
            samples = [(drone_id, *current_xy[drone_id])
                       for drone_id in payload.get("sensed_drone_ids", [])
                       if drone_id in current_xy]
        elif event["tool_name"] == "navigate_to_and_sense":
            position = payload.get("navigation_result", {}).get("final_position")
            actor = event["args"].get("drone_id")
            if actor in drones and isinstance(position, dict):
                current_xy[actor] = (position["x"], position["y"])
                samples = [(actor, position["x"], position["y"])]
        if not samples:
            continue
        before = covered.area
        for actor, x, y in samples:
            covered = covered.union(Point(x, y).buffer(
                float(drones[actor]["perceived_radius"])).intersection(canvas))
        area_events.append({"evidence_id": event["evidence_id"],
                            "tool_name": event["tool_name"], "status": event["status"],
                            "new_nominal_canvas_area_m2": round(covered.area - before, 1),
                            "samples": len(samples)})
    main_recent = [event for event in area_events
                   if event["tool_name"] == "navigate_to_and_sense"
                   and event["status"] == "completed"][-3:]
    return {"task_id": item["task_id"], "trigger_elapsed_seconds": run["trigger"]["elapsed_seconds"],
            "worker_actor_id": run["worker_actor_id"], "points": points,
            "nominal_canvas_xy": [width, height],
            "points_outside_nominal_canvas": [not (0 <= p["x"] <= width and 0 <= p["y"] <= height)
                                              for p in points],
            "three_sensor_disks_union_inside_nominal_canvas_m2": round(nominal_coverage, 1),
            "three_sensor_disks_union_inside_nominal_canvas_fraction": round(nominal_coverage / canvas.area, 5),
            "main_sensing_coverage_at_trigger_nominal_canvas_fraction": round(covered.area / canvas.area, 5),
            "main_last_three_completed_sensing_moves_new_area_m2": [
                event["new_nominal_canvas_area_m2"] for event in main_recent],
            "main_sensing_area_events": area_events,
            "distance_to_missing_target_m": [round(math.hypot(point["x"]-tx, point["y"]-ty), 3)
                                              for point in points],
            "server_sensor_detected_target_at_points": ["Fixed Target 9" in names for names in sensed],
            "worker_llm_call": item["worker_model_calls"][0],
            "three_point_max_nonoverlap_disk_area_m2": round(3 * math.pi * 50**2, 1)}


def audit_trigger_leads(decisions):
    result = {}
    for k in (3, 5, 8):
        cases = []
        for decision in decisions:
            firing = decision["firings"]["stall"][str(k)]
            if not firing:
                continue
            trace = read(ROOT / decision["trace"])
            end = firing["end_sequence"]
            drones = next(item["payload"] for item in trace["task_progress"]["events"]
                          if item["tool_name"] == "list_drones" and item["end_sequence"] <= end)
            board = last_public_blackboard(trace, end)
            plan = select_hint_plan(firing["unresolved_targets"], firing["actor_id"],
                                    drones, board, prior_local_observations(decisions, decision))
            cases.append({"task_id": decision["task_id"],
                          "historically_passed": decision["r2_pass_offline_label"],
                          "has_provenanced_safe_lead_plan": bool(plan),
                          "lead_target": plan["target_name"] if plan else None})
        result[str(k)] = cases
    return result


def main():
    decisions = read(OLD_DECISIONS)
    summary = read(NEW_SUMMARY)
    selected = [item for item in decisions if item["task_id"] in {"529c0622", "22e1e1c5"}]
    if len(selected) != 2:
        raise ValueError("Expected the two frozen historical trigger cases")
    historical = [audit_historical(item, decisions) for item in selected]
    trigger_funnel = {str(k): {
        "triggered_tasks": sum(bool(item["firings"]["stall"][str(k)]) for item in decisions),
        "historically_passed": sum(bool(item["firings"]["stall"][str(k)])
                                   and item["r2_pass_offline_label"] for item in decisions),
    } for k in (3, 5, 8)}
    result = {"method": "lead-first-v0-offline-only",
              "planner_inputs": "Public completed tool replies at the trigger only; no original scene targets",
              "oracle_evaluation": "Task-start SYSTEM snapshot loaded only after plan selection; actual server controller takeoff, move_to, and get_other_targets, followed by an R2 ledger replay with a controller-backed synthetic sensing reply",
              "trigger_funnel_old_100_tasks": trigger_funnel,
              "trigger_lead_cases_old_100_tasks": audit_trigger_leads(decisions),
              "historical": historical,
              "k8_tasks_with_provenanced_lead_plan": sum(bool(item["plan"]) for item in historical),
              "actual_worker": audit_actual_worker(summary),
              "limitations": ["Controller simulation starts from a task-start snapshot, not the exact paused task world; task-score effects untested",
                              "Two development cases are insufficient for a success-rate estimate",
                              "An inherited position is a planning hypothesis until refreshed by local sensing",
                              "Ledger handoff uses a controller-backed synthetic tool reply, not an actual online worker request",
                              "Production should fetch equivalent source command/evidence provenance from the live R2 index, not scan offline trace files"]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUTPUT),
                      "plans": [item["plan"] for item in result["historical"]],
                      "actual_worker_detected_target": result["actual_worker"]["server_sensor_detected_target_at_points"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
