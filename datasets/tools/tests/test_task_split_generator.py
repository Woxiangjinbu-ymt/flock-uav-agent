"""Generator tests use only the pilot namespace; no formal holdout generation."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_split_generator as generator


@pytest.fixture(scope="module")
def pilot():
    return generator.build_dataset(phase="pilot")


def scenes(pilot):
    manifest, blobs = pilot
    return [(row, json.loads(blobs[row["scene_file"]])) for row in manifest["scenes"]]


def distance(a, b):
    return math.dist([a[key] for key in ("x", "y", "z")], [b[key] for key in ("x", "y", "z")])


def segment_distance_xy(point, start, end):
    vx, vy = end["x"] - start["x"], end["y"] - start["y"]
    norm = vx * vx + vy * vy
    weight = 0 if norm == 0 else max(0, min(1, ((point["x"] - start["x"]) * vx + (point["y"] - start["y"]) * vy) / norm))
    return math.hypot(point["x"] - start["x"] - weight * vx, point["y"] - start["y"] - weight * vy)


def test_fixed_counts_and_scene_grouped_split(pilot):
    manifest, blobs = pilot
    assert manifest["counts"] == {"scenes": 30, "tasks": 174, "by_split": {
        "train": {"scenes": 20, "tasks": 116}, "dev": {"scenes": 5, "tasks": 29}, "test": {"scenes": 5, "tasks": 29}}}
    assert len([name for name in blobs if name.startswith("scenes/")]) == 30
    by_id = {row["scene_id"]: row for row in manifest["scenes"]}
    assert len(by_id) == len({row["seed"] for row in by_id.values()}) == len({row["lineage_id"] for row in by_id.values()}) == 30
    for task in manifest["tasks"]:
        scene = by_id[task["scene_id"]]
        assert task["split"] == scene["split"] and task["lineage_id"] == scene["lineage_id"]
        assert task["scene_file"] == scene["scene_file"] and task["fleet_size"] == scene["fleet_size"]


def test_all_families_and_fleet_scales_have_explicit_coverage(pilot):
    manifest, _ = pilot
    for row, scene in scenes(pilot):
        tasks = [task for task in manifest["tasks"] if task["scene_id"] == row["scene_id"]]
        expected = {"F1", "F2", "F3", "F4", "F6"} | ({"F5"} if row["fleet_size"] > 1 else set())
        assert {task["task_family"] for task in tasks} == expected
        assert len(scene["drones"]) == row["fleet_size"] and len(scene["tasks"]) == len(expected)
    for fleet in range(1, 6):
        assert [row["split"] for row in manifest["scenes"] if row["fleet_size"] == fleet] == list(generator.SPLIT_ORDER)


def test_bytes_reproducible_without_global_random_or_hash_iteration_dependency(pilot):
    random.seed(991)
    before = random.getstate()
    again = generator.build_dataset(phase="pilot")
    assert random.getstate() == before
    assert generator.canonical_bytes(again[0]) == generator.canonical_bytes(pilot[0])
    assert again[1] == pilot[1]


def test_reproducibility_across_python_hash_seeds():
    command = "import json,sys; sys.path.insert(0,sys.argv[1]); import task_split_generator as g; m,b=g.build_dataset(); print(g.sha256(g.canonical_bytes(m)))"
    results = [subprocess.check_output([sys.executable, "-S", "-c", command, str(Path(generator.__file__).parent)], env={**os.environ, "PYTHONHASHSEED": str(seed)}) for seed in (1, 987)]
    assert results[0] == results[1]


def test_scene_and_task_bytes_are_bound_and_original_tasks_match(pilot):
    manifest, blobs = pilot
    assert set(manifest["files"]) == set(blobs)
    for name, expected in manifest["sources"].items():
        assert hashlib.sha256(blobs[f"source/{name}"]).hexdigest() == expected
        assert manifest["files"][f"source/{name}"] == expected
    for row, scene in scenes(pilot):
        assert manifest["files"][row["scene_file"]] == hashlib.sha256(blobs[row["scene_file"]]).hexdigest()
        originals = {task["id"]: task for task in scene["tasks"]}
        for task in [task for task in manifest["tasks"] if task["scene_id"] == row["scene_id"]]:
            assert task["original_task"] == originals[task["task_id"]]
            assert task["command"] == task["original_task"]["content"]
            assert task["command_sha256"] == hashlib.sha256(task["command"].encode()).hexdigest()


def test_all_entity_ids_unique_and_initial_state_clean(pilot):
    seen = set()
    for row, scene in scenes(pilot):
        ids = [scene["id"], scene["environment"]["id"]] + [entity["id"] for name in ("drones", "targets", "obstacles", "tasks") for entity in scene[name]]
        assert len(ids) == len(set(ids)) and not seen.intersection(ids)
        seen.update(ids)
        assert scene["history"] == {"command_history": [], "status_history": {}, "area_coverage": {}, "target_reaches": {}, "moving_target_tracking": {}, "path_history": {}}
        assert all(value == 0 for value in scene["statistics"].values())
        assert all(drone["status"] == "idle" and drone["position"]["z"] == 0 and drone["battery_level"] == 100 for drone in scene["drones"])
        assert all(not target["is_reached"] and target["reached_by"] == [] for target in scene["targets"])
        assert all(not task["is_done"] and not task["is_passed"] for task in scene["tasks"])


def test_visible_targets_are_not_initially_reached_and_3d_visits_are_feasible(pilot):
    for _, scene in scenes(pilot):
        assert scene["is_distance_3d"] is True
        for drone in scene["drones"]:
            assert 20 < drone["task_radius"] < drone["perceived_radius"]
            for target in scene["targets"]:
                center_distance = distance(drone["position"], target["position"])
                assert center_distance < drone["perceived_radius"]
                assert center_distance - target["radius"] > drone["task_radius"]
        assert all(drone["max_altitude"] >= 24 for drone in scene["drones"])


def test_f6_final_target_cannot_be_reached_by_prior_witness_paths(pilot):
    for _, scene in scenes(pilot):
        actor = scene["drones"][0]
        a, b, circle = scene["targets"]
        path = [actor["position"], a["position"], b["position"]]
        for start, end in zip(path, path[1:]):
            assert segment_distance_xy(circle["position"], start, end) > circle["radius"] + actor["task_radius"]


def test_f4_and_f6_match_aggregate_and_threshold_semantics(pilot):
    for task in pilot[0]["tasks"]:
        checks = task["original_task"]["execution_check_apis"]["checks"]
        if task["task_family"] == "F4":
            assert "cumulative across all drones" in task["command"]
            assert len(checks) == 1 and checks[0]["endpoint"] == "/check/target_is_fully_searched"
            assert checks[0]["parameters"]["coverage_threshold"] == 0.95
            assert "drone_id" not in checks[0]["parameters"]
        elif task["task_family"] == "F6":
            assert "at least 100%" in task["command"] and "simultaneous occupancy is not required" in task["command"]
            assert checks == [{"endpoint": "/check/task_progress", "parameters": {"expected_progress": 1.0}}]
            assert "exceeds 95%" not in task["command"]


def test_hover_is_explicitly_checked_and_waypoint_order_not_claimed(pilot):
    for task in pilot[0]["tasks"]:
        checks = task["original_task"]["execution_check_apis"]["checks"]
        if "hover command" in task["command"]:
            assert any(check["endpoint"] == "/check/drone_has_hovered" for check in checks)
        if task["task_family"] == "F2":
            assert "in any order" in task["command"]
            assert len([check for check in checks if check["endpoint"] == "/check/drone_has_visited_position"]) == 3
            assert any(check["endpoint"] == "/check/drone_position" for check in checks)
        assert "then" not in task["command"]


def test_f5_roles_are_distinct_and_cover_every_fleet_actor(pilot):
    for row, scene in scenes(pilot):
        tasks = [task for task in pilot[0]["tasks"] if task["scene_id"] == row["scene_id"] and task["task_family"] == "F5"]
        if row["fleet_size"] == 1:
            assert tasks == []
            continue
        checks = tasks[0]["original_task"]["execution_check_apis"]["checks"]
        assigned = [check["parameters"]["drone_id"] for check in checks if check["endpoint"] == "/check/target_is_reached_by_drone"]
        assert len(assigned) == len(set(assigned)) == row["fleet_size"]
        assert set(assigned) == {drone["id"] for drone in scene["drones"]}


def test_witness_uses_public_discovery_not_private_target_coordinates(pilot):
    for task in pilot[0]["tasks"]:
        recipe = task["quality"]["solver_recipe"]
        assert recipe["version"] == 1 and "no_private_target_coordinates" in recipe["limits"]
        assert "GET /drones/{resolved_drone_id}/nearby" in recipe["public_preflight"][1]
        assert "GET /targets/{observed_target_id}" in recipe["public_preflight"][2]
        for step in recipe["steps"]:
            assert "drone_id" not in step and "target_id" not in step
            if step["op"] in {"visit_target", "cover_target"}:
                assert "position" not in step and not {"x", "y", "z"}.intersection(step)
            if "position" in step:
                assert step["source"] == "command"
                assert all(str(value) in task["command"] for value in step["position"].values())


def test_each_recipe_has_predeclared_necessary_omission_control(pilot):
    expected = {"F1": "rotate", "F2": "move_to", "F3": "take_photo", "F4": "cover_target", "F5": "visit_target", "F6": "visit_target"}
    for task in pilot[0]["tasks"]:
        recipe = task["quality"]["solver_recipe"]
        index = recipe["negative_omit_step_index"]
        assert 0 <= index < len(recipe["steps"])
        assert recipe["steps"][index]["op"] == expected[task["task_family"]]


def test_explicit_exposure_and_unrun_quality_status(pilot):
    manifest, _ = pilot
    assert manifest["phase"] == "pilot" and manifest["exposure"]["classification"] == "development_only"
    assert manifest["evaluation_scope"] == "in_template_instance_generalization"
    assert manifest["exposure"]["old_1500_role"] == "exposed_development_only"
    assert manifest["test_policy"] == {"training_allowed": False, "agent_tuning_allowed": False, "audit_only": True}
    assert manifest["quality_scope"]["solver_validation"] == "not_run"
    assert manifest["quality_scope"]["provider_calls"] == manifest["quality_scope"]["teacher_examples"] == 0
    assert generator.FORMAL_MASTER_SEED != generator.PILOT_MASTER_SEED


def test_writer_seals_bytes_and_refuses_existing_directory(tmp_path):
    output = tmp_path / "new-pilot"
    manifest = generator.write_dataset(output)
    raw = (output / "manifest.json").read_bytes()
    assert (output / "manifest.sha256").read_text().strip() == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw) == manifest
    assert {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()} == set(manifest["files"]) | {"manifest.json", "manifest.sha256"}
    with pytest.raises(FileExistsError):
        generator.write_dataset(output)
    assert (output / "manifest.json").read_bytes() == raw


def test_frozen_source_copy_can_reproduce_dataset_without_current_workspace(tmp_path):
    output = tmp_path / "frozen-pilot"
    original = generator.write_dataset(output)
    reproduced, blobs = generator.build_dataset(phase="pilot", workspace=output / "source")
    assert generator.canonical_bytes(reproduced) == generator.canonical_bytes(original)
    assert all((output / name).read_bytes() == data for name, data in blobs.items())


def test_writer_rejects_symlink_destination_or_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(FileExistsError):
        generator.write_dataset(alias)
    with pytest.raises(ValueError, match="symlinks"):
        generator.write_dataset(alias / "child")


@pytest.mark.parametrize("kwargs", [{"phase": "test"}, {"master_seed": True}, {"master_seed": 0}, {"phase": "pilot", "master_seed": generator.FORMAL_MASTER_SEED}])
def test_frozen_version_configuration_rejects_seed_changes(kwargs):
    with pytest.raises(ValueError):
        generator.build_dataset(**kwargs)


@pytest.mark.parametrize("changes", [{"seed": -1}, {"seed": True}, {"fleet_size": 0}, {"fleet_size": 6}, {"fleet_size": True}, {"scene_id": "../unsafe"}])
def test_scene_inputs_are_bounded(changes):
    with pytest.raises(ValueError):
        generator.generate_scene(**{"seed": 10, "fleet_size": 2, "scene_id": "pilot-scene", **changes})
