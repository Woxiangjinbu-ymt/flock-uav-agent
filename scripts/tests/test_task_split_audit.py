"""Adversarial split-audit checks with independent inert scene fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_task_splits as audit


def hashed(body):
    return hashlib.sha256(body).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def read(path):
    return json.loads(path.read_text())


def seal(root, manifest):
    manifest["files"] = {str(p.relative_to(root)): hashed(p.read_bytes()) for p in (root / "scenes").glob("*.json")}
    write(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(hashed((root / "manifest.json").read_bytes()) + "\n")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A dataset auditor must never call a service")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "dataset"
    manifest = {"schema_version": 1, "dataset_id": "independent-unit-fixture", "generator_version": "inert-v1",
        "phase": "pilot", "master_seed": 12345, "evaluation_scope": audit.SCOPE,
        "sources": {"inert.py": hashed(b"unit fixture, never executed")}, "scenes": [], "tasks": [],
        "counts": {"scenes": 30, "tasks": 174},
        "test_policy": {"training_allowed": False, "agent_tuning_allowed": False, "audit_only": True},
        "exposure": {"classification": "development_only", "old_1500_role": "exposed_development_only",
                     "formal_test_used_for_agent_tuning": False}}
    for fleet in range(1, 6):
        for replicate, split in enumerate(("train", "train", "train", "train", "dev", "test")):
            serial = fleet * 10 + replicate
            ident = f"new-scene-{serial}"
            scene = {"id": ident, "name": f"Synthetic scene {serial}", "task_type": "target_assignment",
                "canvas_width": 2000, "canvas_height": 2000, "environment": {"wind_speed": 0},
                "drones": [{"id": f"{ident}-d{d}", "name": f"Pilot {d}",
                    "position": {"x": 100 + d * 30, "y": 150, "z": 0},
                    "home_position": {"x": 100 + d * 30, "y": 150, "z": 0}, "battery_level": 100}
                    for d in range(fleet)],
                "targets": [{"id": f"{ident}-target", "name": "Destination A", "type": "circle",
                    "position": {"x": 450 + serial * 13, "y": 500 + serial * 7, "z": 0}, "radius": 15 + serial * 2}],
                "obstacles": [], "tasks": []}
            filename = f"scenes/{ident}.json"
            scene_row = {"scene_id": ident, "scene_file": filename, "seed": 9990 + serial,
                         "lineage_id": "lineage-" + ident, "fleet_size": fleet, "split": split}
            manifest["scenes"].append(scene_row)
            for family in ("F1", "F2", "F3", "F4", "F5", "F6"):
                if family == "F5" and fleet == 1:
                    continue
                task_id = ident + "-" + family
                command = f"Pilot 0 performs instruction {family[1:]} at altitude {serial + 10}."
                task = {"id": task_id, "content": command, "is_done": False, "is_passed": False,
                    "execution_check_apis": {"logic": "and", "checks": [
                        {"endpoint": "/check/drone_altitude", "parameters": {
                            "drone_id": scene["drones"][0]["id"], "expected_altitude": serial + 10, "tolerance": 1}}]}}
                scene["tasks"].append(task)
                manifest["tasks"].append({**{k: scene_row[k] for k in ("scene_id", "scene_file", "fleet_size", "split")},
                    "task_id": task_id, "command": command, "command_sha256": hashed(command.encode()),
                    "original_task": copy.deepcopy(task), "task_family": family, "template_id": "template-" + family,
                    "quality": {"solver_recipe": "private inert unit metadata"}})
            write(root / filename, scene)
    seal(root, manifest)
    return root


def selected(manifest, split, fleet=2):
    return next(row for row in manifest["scenes"] if row["split"] == split and row["fleet_size"] == fleet)


def reuse_layout(root, *, shift=0, perturb=0):
    manifest = read(root / "manifest.json")
    source, target = selected(manifest, "train"), selected(manifest, "test")
    initial, replacement = read(root / source["scene_file"]), read(root / target["scene_file"])
    replacement.update({k: copy.deepcopy(initial[k]) for k in ("drones", "targets", "obstacles")})
    for group in ("drones", "targets", "obstacles"):
        for index, entity in enumerate(replacement[group]):
            entity.update(id=f"renamed-{group}-{index}", name=f"Completely different {group} {index}")
            for name in ("position", "home_position"):
                if name in entity:
                    entity[name]["x"] += shift
                    entity[name]["y"] += shift
    replacement["targets"][0]["position"]["x"] += perturb
    for task in replacement["tasks"]:
        task["execution_check_apis"]["checks"][0]["parameters"]["drone_id"] = replacement["drones"][0]["id"]
        row = next(t for t in manifest["tasks"] if t["task_id"] == task["id"])
        row["original_task"] = copy.deepcopy(task)
    write(root / target["scene_file"], replacement)
    seal(root, manifest)


def test_valid_in_template_partition_discloses_shared_skeletons_and_exposed_legacy(dataset):
    before = {p: p.read_bytes() for p in dataset.rglob("*") if p.is_file()}
    report = audit.audit_dataset(dataset)
    assert report["passed"] and report["provider_calls"] == 0
    assert report["counts"]["tasks_by_split"] == {"train": 116, "dev": 29, "test": 29}
    assert report["shared_command_skeleton_clusters"]
    assert all(set(v) == {"train", "dev", "test"} for v in report["template_counts_by_split"].values())
    assert report["historical_exposure"]["task_count"] == 1500
    assert report["historical_exposure"]["role"] == "exposed_development_only"
    assert report["same_split_near_geometry_pairs"] == []
    assert {p: p.read_bytes() for p in dataset.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("shift,perturb", [(0, 0), (200, 0), (200, 0.75)])
def test_cross_split_renamed_translated_and_near_layout_reuse_is_rejected(dataset, shift, perturb):
    reuse_layout(dataset, shift=shift, perturb=perturb)
    with pytest.raises(audit.AuditError, match="Cross-split"):
        audit.audit_dataset(dataset)


def test_same_layout_with_different_id_name_entity_order_has_same_geometry(dataset):
    manifest = read(dataset / "manifest.json")
    original = read(dataset / selected(manifest, "train", 4)["scene_file"])
    changed = copy.deepcopy(original)
    changed.update(id="different-scene", name="different-scene-name")
    for group in ("drones", "targets", "obstacles"):
        changed[group].reverse()
        for index, entity in enumerate(changed[group]):
            entity.update(id=f"new-{group}-{index}", name="renamed")
    assert audit.geometry(original) == audit.geometry(changed)
    assert audit.canonical_state(original) == audit.canonical_state(changed)


def test_z_is_not_translation_invariant_and_near_distance_is_explicit(dataset):
    manifest = read(dataset / "manifest.json")
    original = read(dataset / selected(manifest, "train")["scene_file"])
    changed = copy.deepcopy(original)
    changed["targets"][0]["position"]["z"] += 2
    assert audit.geometry(original) != audit.geometry(changed)
    assert not audit.geometry_near(audit.geometry(original), audit.geometry(changed))
    changed["targets"][0]["position"]["z"] -= 1.5
    assert audit.geometry_near(audit.geometry(original), audit.geometry(changed))


@pytest.mark.parametrize("field", ["scene_id", "seed", "lineage_id", "scene_file"])
def test_cross_split_identity_or_seed_reuse_fails_even_with_new_manifest_hash(dataset, field):
    manifest = read(dataset / "manifest.json")
    selected(manifest, "test")[field] = selected(manifest, "train")[field]
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError):
        audit.audit_dataset(dataset)


def test_payload_tamper_and_unlisted_file_fail_closed(dataset):
    scene = next((dataset / "scenes").glob("*.json"))
    original = scene.read_bytes()
    scene.write_bytes(original + b" ")
    with pytest.raises(audit.AuditError, match="hash mismatch"):
        audit.audit_dataset(dataset)
    scene.write_bytes(original)
    (dataset / "unlisted.json").write_text("{}")
    with pytest.raises(audit.AuditError, match="Unsealed"):
        audit.audit_dataset(dataset)


def test_manifest_tamper_duplicate_json_and_symlink_fail(dataset):
    raw = (dataset / "manifest.json").read_bytes()
    (dataset / "manifest.json").write_bytes(raw + b" ")
    with pytest.raises(audit.AuditError, match="Manifest hash"):
        audit.audit_dataset(dataset)
    (dataset / "manifest.json").write_bytes(raw)
    (dataset / "alias").symlink_to(dataset / "manifest.json")
    with pytest.raises(audit.AuditError, match="symlink"):
        audit.audit_dataset(dataset)
    with pytest.raises(audit.AuditError, match="Duplicate"):
        audit.parse('{"split":"train","split":"test"}')


def test_task_relabeling_and_row_payload_mismatch_fail(dataset):
    manifest = read(dataset / "manifest.json")
    next(t for t in manifest["tasks"] if t["split"] == "test")["split"] = "train"
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="Task split/source"):
        audit.audit_dataset(dataset)


def test_private_checker_binding_is_validated_without_claiming_solvability(dataset):
    manifest = read(dataset / "manifest.json")
    row = manifest["tasks"][0]
    scene = read(dataset / row["scene_file"])
    scene["tasks"][0]["execution_check_apis"]["checks"][0]["parameters"]["drone_id"] = "absent"
    row["original_task"] = copy.deepcopy(scene["tasks"][0])
    write(dataset / row["scene_file"], scene)
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="absent entity"):
        audit.audit_dataset(dataset)


@pytest.mark.parametrize("field,value", [("training_allowed", True), ("agent_tuning_allowed", True), ("audit_only", False)])
def test_formal_test_policy_cannot_be_relaxed(dataset, field, value):
    manifest = read(dataset / "manifest.json")
    manifest["test_policy"][field] = value
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="Test use policy"):
        audit.audit_dataset(dataset)


def test_training_selection_reads_sealed_split_not_a_caller_relabel(dataset):
    manifest = read(dataset / "manifest.json")
    train = next(t["task_id"] for t in manifest["tasks"] if t["split"] == "train")
    test = next(t["task_id"] for t in manifest["tasks"] if t["split"] == "test")
    assert audit.select_records_for_training(dataset, [train])[0]["task_id"] == train
    with pytest.raises(audit.AuditError, match="Only frozen train"):
        audit.select_records_for_training(dataset, [train, test])
    manifest["training_task_ids"] = [test]
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="Non-training task"):
        audit.audit_dataset(dataset)


def test_historical_data_cannot_be_reclassified_as_clean_test(dataset):
    manifest = read(dataset / "manifest.json")
    manifest["exposure"]["old_1500_role"] = "independent_test"
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="exposure"):
        audit.audit_dataset(dataset)


def test_formal_requires_registered_exposure_and_cannot_relabel_a_pilot(dataset, tmp_path):
    import shutil
    exposed = tmp_path / "exposed-pilot"
    shutil.copytree(dataset, exposed)
    manifest = read(dataset / "manifest.json")
    manifest.update(phase="formal", dataset_id="formal-candidate")
    manifest["exposure"]["classification"] = "new_generated_instances"
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="requires explicitly registered"):
        audit.audit_dataset(dataset)
    with pytest.raises(audit.AuditError, match="master seed reused"):
        audit.audit_dataset(dataset, exposed_datasets=[exposed])
    manifest["master_seed"] += 1
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="Scene seed reused"):
        audit.audit_dataset(dataset, exposed_datasets=[exposed])


def test_source_snapshots_are_verified_but_never_miscounted_as_scenes(dataset):
    manifest = read(dataset / "manifest.json")
    target = dataset / "source/inert.py"
    target.parent.mkdir(parents=True)
    target.write_text("# sealed generator source copy\n")
    manifest["files"]["source/inert.py"] = hashed(target.read_bytes())
    manifest["sources"]["inert.py"] = hashed(target.read_bytes())
    write(dataset / "manifest.json", manifest)
    (dataset / "manifest.sha256").write_text(hashed((dataset / "manifest.json").read_bytes()) + "\n")
    report = audit.audit_dataset(dataset)
    assert report["counts"]["scenes"] == 30 and report["counts"]["tasks"] == 174
    assert report["source_snapshot"]["provenance_matches"]
    manifest["sources"]["inert.py"] = hashed(b"different generator source")
    write(dataset / "manifest.json", manifest)
    (dataset / "manifest.sha256").write_text(hashed((dataset / "manifest.json").read_bytes()) + "\n")
    with pytest.raises(audit.AuditError, match="generator provenance"):
        audit.audit_dataset(dataset)
    target.write_text("# changed\n")
    with pytest.raises(audit.AuditError, match="hash mismatch"):
        audit.audit_dataset(dataset)


def test_f1_task_duplicate_is_rejected_even_when_scene_geometry_differs(dataset):
    manifest = read(dataset / "manifest.json")
    first = next(t for t in manifest["tasks"] if t["split"] == "train" and t["task_family"] == "F1")
    second = next(t for t in manifest["tasks"] if t["split"] == "test" and t["task_family"] == "F1")
    scene = read(dataset / second["scene_file"])
    task = next(t for t in scene["tasks"] if t["id"] == second["task_id"])
    task["content"] = first["command"]
    task["execution_check_apis"] = copy.deepcopy(first["original_task"]["execution_check_apis"])
    task["execution_check_apis"]["checks"][0]["parameters"]["drone_id"] = scene["drones"][0]["id"]
    second.update(command=task["content"], command_sha256=hashed(task["content"].encode()), original_task=copy.deepcopy(task))
    write(dataset / second["scene_file"], scene)
    seal(dataset, manifest)
    with pytest.raises(audit.AuditError, match="F1 task semantic duplicate"):
        audit.audit_dataset(dataset)


def test_exact_command_normalization_preserves_numeric_obligations(dataset):
    manifest = read(dataset / "manifest.json")
    scene = read(dataset / manifest["scenes"][0]["scene_file"])
    one = audit.command_skeleton("Pilot 0 take off to 12 meters.", scene, normalize_numbers=False)
    two = audit.command_skeleton("Pilot 0 take off to 24 meters.", scene, normalize_numbers=False)
    assert one != two and "12" in one and "Pilot" not in one
    assert audit.command_skeleton("Pilot 0 take off to 12 meters.", scene) == audit.command_skeleton("Pilot 0 take off to 24 meters.", scene)


def test_independent_formal_fixture_passes_exposure_comparison_and_reference_tamper_fails(dataset, tmp_path):
    # Inert fixture transformation only; never generate or inspect real formal seeds.
    import shutil
    exposed = tmp_path / "exposed-pilot"
    shutil.copytree(dataset, exposed)
    manifest = read(dataset / "manifest.json")
    mapping = {}
    old_scenes = {}
    for row in manifest["scenes"]:
        scene = read(dataset / row["scene_file"])
        old_scenes[row["scene_id"]] = scene
        mapping[row["scene_file"]] = "scenes/fresh-" + row["scene_id"] + ".json"
        mapping[row["lineage_id"]] = "fresh-" + row["lineage_id"]
        for value in [scene, *scene["drones"], *scene["targets"], *scene["obstacles"], *scene["tasks"]]:
            mapping[value["id"]] = "fresh-" + value["id"]
    def rename(value):
        if isinstance(value, dict):
            return {k: rename(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        return mapping.get(value, value) if isinstance(value, str) else value
    updated = rename(manifest)
    updated.update(phase="formal", dataset_id="inert-independent-formal", master_seed=999999)
    updated["exposure"]["classification"] = "new_generated_instances"
    for original, row in zip(manifest["scenes"], updated["scenes"]):
        row["seed"] += 100000
        scene = rename(old_scenes[original["scene_id"]])
        scene["targets"][0]["radius"] += 100
        for drone in scene["drones"]:
            drone["battery_level"] = 99  # Changed relevant F1 actor initial state.
        write(dataset / row["scene_file"], scene)
        (dataset / original["scene_file"]).unlink()
    seal(dataset, updated)
    result = audit.audit_dataset(dataset, exposed_datasets=[exposed])
    assert result["passed"] and result["exposed_dataset_comparisons"][0]["disjoint"]
    assert result["exposed_dataset_comparisons"][0]["train_f1_semantic_overlaps"] == 0
    (exposed / manifest["scenes"][0]["scene_file"]).write_text("{}")
    with pytest.raises(audit.AuditError, match="Artifact hash mismatch"):
        audit.audit_dataset(dataset, exposed_datasets=[exposed])
