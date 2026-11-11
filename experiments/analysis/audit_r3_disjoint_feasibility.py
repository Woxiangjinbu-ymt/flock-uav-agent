#!/usr/bin/env python3
"""Screen every strict-R3 route in B0 scenes disjoint from R2 development.

Classification uses only original commands, original check definitions, task
pre-state obstacles and simulator collision rules. B0 results are joined after
the geometry classifications solely to describe historical opportunities.
Run with agent4drone/.venv/bin/python (LangChain and Shapely are installed).
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
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

FULL = ROOT / "docs/results/full-round1/results.json"
COHORT = ROOT / "docs/audit/r3-next-step-20261009/disjoint-opportunity.json"
OUTPUT = ROOT / "docs/audit/r3-disjoint-feasibility-20261009/screen.json"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def key(actor_id, xyz):
    return (str(actor_id), *(float(v) for v in xyz))


def main():
    full = read(FULL)
    assert full["status"] == "complete_verified"
    attempt = ROOT / full["attempt"]
    cohort = read(COHORT)
    assert cohort["recognized_route_tasks"] == len(cohort["rows"]) == 34
    with ZipFile(attempt / "source.zip") as archive:
        names = ("server/controllers/obstacle_controller.py", "server/models/obstacle.py",
                 "server/controllers/drone_controller.py", "server/api/server.py",
                 "controller/template_placeholders.py", "controller/task_template_manager.py",
                 "controller/default_templates.py")
        assert all(archive.read(name) == (ROOT / name).read_bytes() for name in names), \
            "Simulator geometry/check source differs from B0 frozen run"
    directories = {scene["session_id"]: scene["directory"] for scene in full["scenes"]}

    rows = []
    for row in cohort["rows"]:
        task_dir = attempt / directories[row["session_id"]]
        task_id = row["task_id"]
        before = read(task_dir / f"{task_id}.before.json")
        assert before["is_distance_3d"] is False
        command = read(task_dir / f"{task_id}.job.json")["command"]
        routes, status = explicit_routes(command)
        assert status == "recognized_coordinate_routes"
        leaves = [leaf for leaf in read(task_dir / f"{task_id}.checks.json")
                  if leaf.get("type") == "leaf"]
        visits = [leaf for leaf in leaves if leaf["endpoint"] == "/check/drone_has_visited_position"]
        finals = [leaf for leaf in leaves if leaf["endpoint"] == "/check/drone_position"]
        assert len(finals) == len(routes), (row["session_id"], task_id, len(finals), len(routes))
        drones_by_name = {drone["name"]: drone for drone in before["drones"]}
        assert len(drones_by_name) == len(before["drones"])
        controller = ObstacleController()
        for obstacle in before["obstacles"]:
            controller.add_obstacle(obstacle)
        geom = [(obstacle, obstacle.to_geometry()) for obstacle in controller.obstacles.values()]
        union_cache = {}

        def active_union(altitude):
            if altitude not in union_cache:
                geometries = [shape for obstacle, shape in geom if shape is not None
                              and (obstacle.height == 0 or altitude <= obstacle.height)]
                union_cache[altitude] = unary_union(geometries)
            return union_cache[altitude]

        def disk_test(xyz, tolerance, altitude):
            x, y, _ = xyz
            position = Point(float(x), float(y))
            disk = position.buffer(float(tolerance), quad_segs=128)
            blocked = active_union(float(altitude))
            free_area = disk.difference(blocked).area
            return {"locally_free": free_area > 1e-8,
                    "free_area_m2": round(free_area, 3),
                    "center_blocked": blocked.covers(position),
                    "distance_to_free_boundary_m": round(position.distance(blocked.boundary), 3)
                    if blocked.covers(position) else 0.0}

        visit_map = Counter(key(leaf["parameters"]["drone_id"],
                                (leaf["parameters"]["x"], leaf["parameters"]["y"],
                                 leaf["parameters"]["z"])) for leaf in visits)
        final_map = Counter((str(leaf["parameters"]["drone_id"]),
                             float(leaf["parameters"]["x"]),
                             float(leaf["parameters"]["y"])) for leaf in finals)
        expected_visits = Counter()
        expected_finals = Counter()
        start_keys = Counter()
        route_end_by_actor = {}
        route_ordinal_by_key = {}
        for route in routes:
            actor = drones_by_name[route["required_actor"]]
            start_keys[key(actor["id"], route["goals"][0]["point"])] += 1
            for goal in route["goals"][:-1]:
                goal_key = key(actor["id"], goal["point"])
                expected_visits[goal_key] += 1
                assert goal_key not in route_ordinal_by_key
                route_ordinal_by_key[goal_key] = goal["ordinal"]
            end = route["goals"][-1]["point"]
            route_end_by_actor[actor["id"]] = end
            expected_finals[(str(actor["id"]), end[0], end[1])] += 1
        assert not visit_map - expected_visits, (row["session_id"], task_id, "visit alignment")
        unscored_starts = expected_visits - visit_map
        assert not unscored_starts - start_keys, (row["session_id"], task_id, "unexpected unscored route goal")
        assert expected_finals == final_map, (row["session_id"], task_id, "final alignment")

        by_id = {drone["id"]: drone for drone in before["drones"]}
        details = []
        for leaf in visits:
            params = leaf["parameters"]
            goal = [float(params[k]) for k in ("x", "y", "z")]
            actor = by_id[params["drone_id"]]
            tolerance = float(params["tolerance"])
            nominal = disk_test(goal, tolerance, goal[2])
            max_altitude = float(actor["max_altitude"])
            checker_permitted = disk_test(goal, tolerance, max_altitude)
            covering = controller.check_point_collision(*goal, margin=0.0)["inside_obstacles"]
            details.append({"kind": "visit", "actor": actor["name"], "actor_id": actor["id"],
                            "route_ordinal": route_ordinal_by_key[key(actor["id"], goal)],
                            "goal": goal, "tolerance_m": tolerance,
                            "actor_max_altitude_m": max_altitude,
                            "nominal": nominal, "checker_permitted_at_max_altitude": checker_permitted,
                            "covering_at_nominal": [{"id": item["id"], "name": item["name"],
                                                     "height": item["height"]} for item in covering],
                            "b0_check_passed": bool(leaf["result"])})
        for start_key, count in unscored_starts.items():
            actor_id, *goal = start_key
            actor = by_id[actor_id]
            for _ in range(count):
                details.append({"kind": "unscored_start", "actor": actor["name"],
                                "actor_id": actor_id, "goal": goal,
                                "route_ordinal": route_ordinal_by_key[start_key],
                                "diagnostic_tolerance_m": 3.0,
                                "nominal": disk_test(goal, 3.0, goal[2]),
                                "checker_permitted_at_max_altitude": disk_test(
                                    goal, 3.0, float(actor["max_altitude"])),
                                "note": "No visit check for this start; 3m is diagnostic only."})
        for leaf in finals:
            params = leaf["parameters"]
            actor = by_id[params["drone_id"]]
            goal = [float(params[k]) for k in ("x", "y", "z")]
            tolerance = float(params["tolerance"])
            ground = disk_test(goal, tolerance, 0.0)
            route_end = route_end_by_actor[actor["id"]]
            nominal = disk_test(route_end, tolerance, route_end[2])
            details.append({"kind": "final_ground_position", "actor": actor["name"],
                            "actor_id": actor["id"], "goal": goal, "tolerance_m": tolerance,
                            "route_ordinal": len(next(route for route in routes
                                                      if route["required_actor"] == actor["name"])["goals"]),
                            "route_end_at_flight_altitude": route_end,
                            "nominal": nominal, "ground": ground,
                            "b0_check_passed": bool(leaf["result"])})
        visit_details = [item for item in details if item["kind"] == "visit"]
        start_details = [item for item in details if item["kind"] == "unscored_start"]
        final_details = [item for item in details if item["kind"] == "final_ground_position"]
        failed_visits = [item for item in visit_details if not item["b0_check_passed"]]
        first_blocked_by_actor = {}
        for item in details:
            blocked = not item["nominal"]["locally_free"] or (
                item["kind"] == "final_ground_position" and not item["ground"]["locally_free"])
            if blocked:
                actor_id = item["actor_id"]
                first_blocked_by_actor[actor_id] = min(
                    first_blocked_by_actor.get(actor_id, item["route_ordinal"]), item["route_ordinal"])
        failed_free = [item for item in failed_visits if item["nominal"]["locally_free"]]
        b0_job = read(task_dir / f"{task_id}.job.json")
        outside_actor_block = []
        for item in failed_free:
            if item["actor_id"] in first_blocked_by_actor:
                continue
            goal = item["goal"]
            after = read(task_dir / f"{task_id}.after.json")
            path_points = after["history"]["path_history"].get(item["actor_id"], [])
            nearest = min(((math.hypot(point["x"] - goal[0], point["y"] - goal[1]), point)
                           for point in path_points), default=None, key=lambda pair: pair[0])
            requested = []
            for index, step in enumerate((b0_job.get("result") or {}).get("intermediate_steps", [])):
                action, observation = step
                args = action.get("tool_input") or {}
                if args.get("drone_id") != item["actor_id"]:
                    continue
                points = args.get("waypoints") if isinstance(args.get("waypoints"), list) else \
                    [args] if all(coord in args for coord in ("x", "y")) else []
                if not any(math.hypot(point["x"] - goal[0], point["y"] - goal[1])
                           <= item["tolerance_m"] for point in points):
                    continue
                try:
                    reply = json.loads(observation)
                except (TypeError, ValueError):
                    reply = {}
                receipt = reply.get("move_result", reply.get("navigation_result", reply))
                requested.append({"step": index, "tool": action.get("tool"),
                                  "receipt_status": receipt.get("status", receipt.get("outcome")),
                                  "receipt_message": receipt.get("message")})
            outside_actor_block.append({"actor": item["actor"], "route_ordinal": item["route_ordinal"],
                                        "goal": goal, "nearest_b0_path_history_distance_m":
                                        round(nearest[0], 3) if nearest else None,
                                        "nearest_b0_path_history_point": nearest[1] if nearest else None,
                                        "matching_b0_tool_requests": requested})
        rows.append({**row,
                     "obstacles": len(before["obstacles"]),
                     "visit_checks": len(visits), "unscored_starts": len(start_details),
                     "final_position_checks": len(finals),
                     "nominal_locally_blocked_visits": sum(not item["nominal"]["locally_free"]
                                                           for item in visit_details),
                     "nominal_locally_blocked_unscored_starts": sum(
                         not item["nominal"]["locally_free"] for item in start_details),
                     "nominal_locally_blocked_route_ends": sum(
                         not item["nominal"]["locally_free"] for item in final_details),
                     "checker_permitted_locally_blocked_visits": sum(
                         not item["checker_permitted_at_max_altitude"]["locally_free"]
                         for item in visit_details),
                     "ground_locally_blocked_finals": sum(not item["ground"]["locally_free"]
                                                         for item in final_details),
                     "b0_failed_visit_checks": len(failed_visits),
                     "b0_failed_visit_checks_with_nominal_free_area": sum(
                         item["nominal"]["locally_free"] for item in failed_visits),
                     "b0_failed_free_visits_after_same_actor_first_block": sum(
                         item["route_ordinal"] > first_blocked_by_actor.get(item["actor_id"], 999)
                         for item in failed_free),
                     "b0_failed_free_visits_on_actor_without_local_block": outside_actor_block,
                     "b0_job_status": b0_job["status"],
                     "all_route_points_locally_legal_at_nominal_altitude": all(
                         item["nominal"]["locally_free"] for item in
                         [*visit_details, *start_details, *final_details])
                         and all(item["ground"]["locally_free"] for item in final_details),
                     "checks_detail": details})

    # B0 outcomes are used here, after classifying geometry, to describe which
    # already-observed failures might be worth a future development experiment.
    clean_failed = [row for row in rows if row["all_route_points_locally_legal_at_nominal_altitude"]
                    and row["b0_failed_visit_checks"] > 0]
    result = {
        "method": "strict-R3 B0 scene-disjoint route geometry screen",
        "input_cohort_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest(),
        "b0_frozen_simulator_source_matches_workspace": True,
        "b0_frozen_generator_source_matches_workspace": True,
        "all_scenes_are_2d_checker_distance": True,
        "cohort_tasks": len(rows), "cohort_scenes": len({row["session_id"] for row in rows}),
        "visit_checks": sum(row["visit_checks"] for row in rows),
        "unscored_starts": sum(row["unscored_starts"] for row in rows),
        "final_position_checks": sum(row["final_position_checks"] for row in rows),
        "nominal_locally_blocked_visits": sum(row["nominal_locally_blocked_visits"] for row in rows),
        "nominal_locally_blocked_unscored_starts": sum(
            row["nominal_locally_blocked_unscored_starts"] for row in rows),
        "nominal_locally_blocked_route_ends": sum(
            row["nominal_locally_blocked_route_ends"] for row in rows),
        "checker_permitted_locally_blocked_visits": sum(
            row["checker_permitted_locally_blocked_visits"] for row in rows),
        "ground_locally_blocked_finals": sum(row["ground_locally_blocked_finals"] for row in rows),
        "tasks_with_any_nominal_locally_blocked_route_point": sum(
            not row["all_route_points_locally_legal_at_nominal_altitude"] for row in rows),
        "tasks_all_route_points_locally_legal_at_nominal_altitude": sum(
            row["all_route_points_locally_legal_at_nominal_altitude"] for row in rows),
        "b0_failed_visit_checks": sum(row["b0_failed_visit_checks"] for row in rows),
        "b0_failed_visit_checks_with_nominal_free_area": sum(
            row["b0_failed_visit_checks_with_nominal_free_area"] for row in rows),
        "b0_failed_free_visits_after_same_actor_first_block": sum(
            row["b0_failed_free_visits_after_same_actor_first_block"] for row in rows),
        "b0_job_status_counts": dict(Counter(row["b0_job_status"] for row in rows)),
        "clean_failed_visit_candidate_ids": [
            {"session_id": row["session_id"], "task_id": row["task_id"],
             "ordinal": row["ordinal"], "failed_visit_checks": row["b0_failed_visit_checks"]}
            for row in clean_failed],
        "partial_credit_probes": [
            {"session_id": row["session_id"], "task_id": row["task_id"],
             "b0_job_status": row["b0_job_status"], "other_route_blocked":
             not row["all_route_points_locally_legal_at_nominal_altitude"],
             **item}
            for row in rows for item in row["b0_failed_free_visits_on_actor_without_local_block"]],
        "interpretation_limits": [
            "A free destination area does not prove a collision-free path or enough battery/time.",
            "The 2D checker can score visits at a different altitude; nominal altitude is the semantic route requirement.",
            "18 route starts have no dedicated visit check; their 3m disks are diagnostic, not scored.",
            "B0 failure is not an R2 failure or an R3 recovery.",
            "This reused B0 development corpus is not a blind test; no tasks are removed from benchmark scoring.",
        ],
        "rows": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "cohort_tasks", "cohort_scenes", "visit_checks", "final_position_checks",
        "unscored_starts", "nominal_locally_blocked_visits",
        "nominal_locally_blocked_unscored_starts", "nominal_locally_blocked_route_ends",
        "checker_permitted_locally_blocked_visits",
        "ground_locally_blocked_finals", "tasks_all_route_points_locally_legal_at_nominal_altitude",
        "b0_failed_visit_checks", "b0_failed_visit_checks_with_nominal_free_area",
        "b0_failed_free_visits_after_same_actor_first_block", "b0_job_status_counts",
        "clean_failed_visit_candidate_ids", "partial_credit_probes")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
