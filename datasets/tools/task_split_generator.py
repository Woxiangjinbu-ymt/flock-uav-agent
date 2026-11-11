"""Deterministic new scene instances for W2, separate from exposed old tasks.

This generator never loads the old benchmark, contacts a service, executes an
Agent, or labels a model successful. The private solver recipes are mechanical
quality witnesses, not Teacher trajectories. Formal generation is explicit;
development uses the disjoint pilot namespace/seed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import random


MODULE_DIR = Path(__file__).resolve().parent
# Exported source snapshots keep these modules in their original scripts/ directory.
ROOT = MODULE_DIR.parents[1] if MODULE_DIR.name == "tools" else MODULE_DIR.parent
GENERATOR_VERSION = "task-coverage-splits-v1"
PILOT_MASTER_SEED = 2026110301
FORMAL_MASTER_SEED = 2026110302
EPOCH = 1802649600.0
SPLIT_ORDER = ("train", "train", "train", "train", "dev", "test")
SOURCE_FILES = (
    "scripts/task_split_generator.py", "docs/TASK_COVERAGE_AND_SPLITS_CN.md",
    "controller/default_templates.py", "server/models/session.py",
    "server/models/drone.py", "server/models/target.py", "server/api/server.py",
    "server/controllers/drone_controller.py", "scripts/harness_v2_world.py",
    "scripts/audit_task_splits.py", "scripts/prepare_task_dataset.py", "scripts/validate_task_dataset.py",
)


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _derived(label, *parts):
    return sha256(json.dumps([GENERATOR_VERSION, label, *parts], separators=(",", ":")).encode())


def _point(x, y, z=0):
    return {"x": x, "y": y, "z": z}


def _check(name, **parameters):
    return {"endpoint": f"/check/{name}", "parameters": parameters}


def _takeoff_check(drone, altitude):
    return _check("drone_has_taken_off", drone_id=drone["id"], min_altitude=altitude,
                  max_altitude=altitude, tolerance=0.1)


def _hover_check(drone):
    return _check("drone_has_hovered", drone_id=drone["id"], min_count=1)


def _recipe(steps, negative_index):
    return {
        "version": 1, "inputs_allowed": ["command", "agent_role_public_api_responses", "documented_solver_constants"],
        "visibility": "all_named_targets_initially_within_perception_radius",
        "public_preflight": ["GET /drones; resolve exact drone name to returned id",
                             "GET /drones/{resolved_drone_id}/nearby; resolve target name to returned id",
                             "GET /targets/{observed_target_id}; read target position/geometry"],
        "steps": steps, "negative_omit_step_index": negative_index,
        "constants": {"default_flight_altitude": 20, "coverage_spacing": 10},
        "limits": ["quality_witness_not_model_or_teacher", "no_private_target_coordinates",
                   "no_admin_reads_during_execution", "no_policy_selection_on_test"],
    }


def _task(session, family, command, checks, steps, negative_index, *, template):
    task_id = _derived("task", session["id"], family)[:16]
    original = {
        "id": task_id, "name": f"{family} instance {task_id}", "content": command,
        "content_aliases": [], "description": "New bounded static instance; independently reset before execution.",
        "creator": GENERATOR_VERSION, "originated_from": f"{GENERATOR_VERSION}:{template}",
        "difficulty": "medium", "related_apis": [],
        "execution_check_apis": {"logic": "and", "checks": checks},
        "commands": list(dict.fromkeys({"visit_target": "move_to", "cover_target": "move_along_path"}.get(step["op"], step["op"]) for step in steps)),
        "is_done": False, "is_passed": False, "created_at": EPOCH, "last_updated": EPOCH,
    }
    session["tasks"].append(original)
    return {
        "task_id": task_id, "scene_id": session["id"], "scene_file": f"scenes/{session['id']}.json",
        "command": command, "command_sha256": sha256(command.encode("utf-8")),
        "original_task": copy.deepcopy(original), "task_family": family, "template_id": template,
        "fleet_size": len(session["drones"]), "quality": {"solver_recipe": _recipe(steps, negative_index)},
    }


def _tasks(session, rng):
    drones = session["drones"]
    fixed = session["targets"][:2]
    circle = session["targets"][2]
    records = []
    actor = rng.choice(drones)
    altitude, heading = rng.randint(12, 24), rng.randint(15, 345)
    records.append(_task(
        session, "F1", f"{actor['name']} must take off to {altitude} meters, face heading {heading} degrees, and execute a hover command. Finish at that altitude and heading.",
        [_takeoff_check(actor, altitude), _check("drone_altitude", drone_id=actor["id"], expected_altitude=altitude, tolerance=0.1),
         _check("drone_heading", drone_id=actor["id"], expected_heading=heading, tolerance=0.1), _hover_check(actor)],
        [{"op": "take_off", "drone": actor["name"], "altitude": altitude},
         {"op": "rotate", "drone": actor["name"], "heading": heading}, {"op": "hover", "drone": actor["name"]}],
        1, template="w2_f1_flight_heading_hover"))

    actor, altitude = rng.choice(drones), rng.randint(12, 24)
    points = [_point(rng.randint(110, 140), rng.randint(160, 180), altitude),
              _point(rng.randint(250, 275), rng.randint(70, 90), altitude),
              _point(rng.randint(270, 290), rng.randint(165, 180), altitude)]
    point_text = "; ".join(f"({p['x']}, {p['y']}, {p['z']})" for p in points)
    final = points[-1]
    checks = [_takeoff_check(actor, altitude)] + [
        _check("drone_has_visited_position", drone_id=actor["id"], **p, tolerance=0.5) for p in points]
    checks += [_check("drone_position", drone_id=actor["id"], **final, tolerance=0.5), _hover_check(actor)]
    records.append(_task(
        session, "F2", f"{actor['name']} must take off to {altitude} meters and visit these positions in any order: {point_text}. Execute a hover command and finish at ({final['x']}, {final['y']}, {final['z']}).",
        checks, [{"op": "take_off", "drone": actor["name"], "altitude": altitude}]
        + [{"op": "move_to", "drone": actor["name"], "position": p, "source": "command"} for p in points]
        + [{"op": "hover", "drone": actor["name"]}], 3, template="w2_f2_unordered_waypoint_set"))

    actor, target = rng.choice(drones), rng.choice(fixed)
    records.append(_task(
        session, "F3", f"{actor['name']} must visit {target['name']} within its task radius and take at least one photo with that target within its perception radius.",
        [_check("target_is_reached_by_drone", drone_id=actor["id"], target_id=target["id"]),
         _check("target_in_photo_taken_by_drone", drone_id=actor["id"], target_id=target["id"])],
        [{"op": "take_off", "drone": actor["name"], "altitude": 20},
         {"op": "visit_target", "drone": actor["name"], "target": target["name"], "altitude": 20},
         {"op": "take_photo", "drone": actor["name"]}], 2, template="w2_f3_target_visit_visible_photo"))

    actor = rng.choice(drones)
    records.append(_task(
        session, "F4", f"Use the available fleet to cover at least 95% of {circle['name']}'s area. Coverage is cumulative across all drones; any subset of the fleet may perform it.",
        [_check("target_is_fully_searched", target_id=circle["id"], coverage_threshold=0.95)],
        [{"op": "take_off", "drone": actor["name"], "altitude": 20},
         {"op": "cover_target", "drone": actor["name"], "target": circle["name"], "altitude": 20, "spacing": 10}],
        1, template="w2_f4_fleet_aggregate_area_coverage"))

    if len(drones) >= 2:
        assignments, checks, steps = [], [], []
        for index, actor in enumerate(drones):
            target = fixed[index % 2]
            assignments.append(f"{actor['name']} must visit {target['name']} within its task radius and execute a hover command")
            checks += [_check("target_is_reached_by_drone", drone_id=actor["id"], target_id=target["id"]), _hover_check(actor)]
            steps += [{"op": "take_off", "drone": actor["name"], "altitude": 20},
                      {"op": "visit_target", "drone": actor["name"], "target": target["name"], "altitude": 20},
                      {"op": "hover", "drone": actor["name"]}]
        records.append(_task(session, "F5", ". ".join(assignments) + ". Each named drone has its own visit requirement; no inter-drone execution order is required.",
                             checks, steps, len(steps) - 2, template="w2_f5_actor_specific_assignment"))

    actor = drones[0]
    records.append(_task(
        session, "F6", "Use any available drones to visit all non-waypoint targets within task radius until public session task progress is at least 100%. Visits are cumulative; simultaneous occupancy is not required.",
        [_check("task_progress", expected_progress=1.0)],
        [{"op": "take_off", "drone": actor["name"], "altitude": 20}]
        + [{"op": "visit_target", "drone": actor["name"], "target": target["name"], "altitude": 20} for target in session["targets"]],
        3, template="w2_f6_all_target_visit_progress"))
    return records


def generate_scene(*, seed, fleet_size, scene_id):
    """Create one clean standard Session JSON plus its independently reset tasks.

    Geometry separates the final F6 target from prior witness paths. Obstacles
    stay outside the required routes; this v1 tests new visible instances, not
    hidden-target discovery or obstacle recovery. Never resample on model score.
    """
    if type(seed) is not int or seed < 0 or type(fleet_size) is not int or not 1 <= fleet_size <= 5:
        raise ValueError("seed must be nonnegative and fleet_size must be 1..5")
    if not isinstance(scene_id, str) or not scene_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in scene_id):
        raise ValueError("scene_id must be a safe lowercase identifier")
    rng = random.Random(seed)
    drones = []
    for index in range(fleet_size):
        position = _point(rng.randint(42, 58) + index * 5, rng.randint(35, 65))
        drones.append({"id": _derived("drone", scene_id, index)[:16], "name": f"Drone {index + 1}",
                       "model": "W2 static simulator", "max_speed": 10, "max_altitude": 60,
                       "battery_capacity": 1000, "battery_level": 100, "battery_volume": 1000,
                       "perceived_radius": 350, "task_radius": 25, "status": "idle", "position": position,
                       "home_position": copy.deepcopy(position), "heading": 0, "speed": 0,
                       "created_at": EPOCH, "last_updated": EPOCH})
    targets = [
        {"name": "Fixed Target 1", "type": "fixed", "position": _point(rng.randint(145, 175), rng.randint(90, 115)), "radius": 1},
        {"name": "Fixed Target 2", "type": "fixed", "position": _point(rng.randint(215, 245), rng.randint(115, 135)), "radius": 1},
        {"name": "Circle Target 1", "type": "circle", "position": _point(rng.randint(175, 215), rng.randint(225, 250)), "radius": rng.randint(20, 28)},
    ]
    for index, target in enumerate(targets):
        target.update(id=_derived("target", scene_id, index)[:16], is_reached=False, reached_by=[], created_at=EPOCH, last_updated=EPOCH)
    session = {
        "id": scene_id, "name": f"Static instance {scene_id}", "description": "Visible-target, clear-route static instance; no hidden-search or recovery claim.",
        "creator": GENERATOR_VERSION, "status": "active", "task_type": "target_assignment", "task_description": "Cumulative non-waypoint target visits.",
        "is_distance_3d": True, "canvas_width": 400, "canvas_height": 320,
        "created_at": EPOCH, "last_updated": EPOCH, "drones": drones, "targets": targets,
        "obstacles": [{"id": _derived("obstacle", scene_id)[:16], "name": "Obstacle 1", "type": "circle",
                       "position": _point(rng.randint(345, 365), rng.randint(260, 285)), "radius": rng.randint(5, 10),
                       "height": 80, "created_at": EPOCH, "last_updated": EPOCH}],
        "environment": {"id": _derived("environment", scene_id)[:16], "name": "Controlled clear weather",
                        "weather": "clear", "temperature": 22, "humidity": 50, "pressure": 1013.25,
                        "wind_speed": 0, "wind_direction": "north", "visibility": 10000,
                        "created_at": EPOCH, "last_updated": EPOCH},
        "statistics": {"total_commands_executed": 0, "total_flight_time": 0, "total_distance_traveled": 0, "session_time": 0},
        "history": {"command_history": [], "status_history": {}, "area_coverage": {}, "target_reaches": {}, "moving_target_tracking": {}, "path_history": {}},
        "tasks": [],
    }
    return session, _tasks(session, rng)


def build_dataset(*, phase="pilot", master_seed=None, workspace=ROOT):
    """Return manifest and bytes without writing, running, or inspecting a model.

    Formal and pilot seeds are separated by both seed and derivation namespace.
    Exact group/split assignment precedes geometry and task generation.
    """
    if phase not in {"pilot", "formal"}:
        raise ValueError("phase must be pilot or formal")
    expected_seed = PILOT_MASTER_SEED if phase == "pilot" else FORMAL_MASTER_SEED
    if master_seed is None:
        master_seed = expected_seed
    if type(master_seed) is not int or master_seed != expected_seed:
        raise ValueError("Use the frozen phase seed; changing seeds requires a new dataset version")
    workspace = Path(workspace)
    blobs, scenes, tasks = {}, [], []
    for fleet_size in range(1, 6):
        for index, split in enumerate(SPLIT_ORDER):
            lineage = _derived("lineage", phase, master_seed, fleet_size, index)
            seed = int(_derived("scene-seed", phase, master_seed, fleet_size, index)[:13], 16)
            scene_id = _derived("scene", lineage)[:16]
            session, rows = generate_scene(seed=seed, fleet_size=fleet_size, scene_id=scene_id)
            name = f"scenes/{scene_id}.json"
            blobs[name] = canonical_bytes(session)
            scenes.append({"scene_id": scene_id, "scene_file": name, "seed": seed, "split": split,
                           "lineage_id": lineage, "fleet_size": fleet_size})
            for row in rows:
                row.update(split=split, lineage_id=lineage)
                tasks.append(row)
    source_bytes = {}
    for name in SOURCE_FILES:
        path = workspace / name
        if workspace == ROOT and MODULE_DIR.name == "tools" and not path.exists() and name.startswith("scripts/"):
            # The generation, preparation and audit tools live together in this checkout;
            # detached copies retain their published scripts/ names.
            path = MODULE_DIR / path.name
        source_bytes[name] = path.read_bytes()
    blobs.update({f"source/{name}": data for name, data in source_bytes.items()})
    manifest = {
        "schema_version": 1, "dataset_id": f"{GENERATOR_VERSION}-{phase}", "generator_version": GENERATOR_VERSION,
        "phase": phase, "master_seed": master_seed, "evaluation_scope": "in_template_instance_generalization",
        "files": {name: sha256(data) for name, data in sorted(blobs.items())},
        "sources": {name: sha256(data) for name, data in source_bytes.items()},
        "scenes": scenes, "tasks": tasks,
        "counts": {"scenes": len(scenes), "tasks": len(tasks), "by_split": {
            split: {"scenes": sum(row["split"] == split for row in scenes), "tasks": sum(row["split"] == split for row in tasks)}
            for split in ("train", "dev", "test")}},
        "test_policy": {"training_allowed": False, "agent_tuning_allowed": False, "audit_only": True},
        "exposure": {"classification": "development_only" if phase == "pilot" else "new_generated_instances",
                     "old_1500_role": "exposed_development_only", "formal_test_used_for_agent_tuning": False,
                     "notes": ["Pilot is generator-development evidence, including its nominal test rows.",
                               "Formal test may receive only predetermined mechanical quality audits before final evaluation.",
                               "Shared templates and command skeletons across splits are intentional; no template-OOD claim.",
                               "All derived tasks/aliases must retain their scene lineage split."]},
        "quality_scope": {"solver_validation": "not_run", "independent_split_audit": "not_run",
                          "provider_calls": 0, "teacher_examples": 0,
                          "visibility": "all_named_targets_initially_visible", "obstacle_regime": "outside_required_routes",
                          "distance_mode": "simulator_3d_with_25m_task_radius_and_20m_target_visit_altitude",
                          "reset": "fresh_scene_initial_state_per_task", "original_benchmark_read": False},
    }
    return manifest, blobs


def write_dataset(output, *, phase="pilot", master_seed=None, workspace=ROOT):
    """Create a new sealed directory; refuse overwrite and symlink destinations."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Existing dataset destinations cannot be overwritten")
    if any(parent.is_symlink() for parent in output.parents):
        raise ValueError("Dataset destination cannot traverse symlinks")
    manifest, blobs = build_dataset(phase=phase, master_seed=master_seed, workspace=workspace)
    output.mkdir(parents=True, exist_ok=False)
    for name, data in blobs.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    raw = canonical_bytes(manifest)
    with (output / "manifest.json").open("xb") as stream:
        stream.write(raw)
    with (output / "manifest.sha256").open("x") as stream:
        stream.write(sha256(raw) + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("pilot", "formal"), default="pilot")
    args = parser.parse_args(argv)
    manifest = write_dataset(args.output, phase=args.phase)
    print(json.dumps({"dataset_id": manifest["dataset_id"], "phase": manifest["phase"], **manifest["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
