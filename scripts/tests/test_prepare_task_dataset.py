import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_task_dataset import load_dataset, materialize, select_tasks, sha, write, parse_json


def make_dataset(root):
    original = {"id": "t1", "content": "A public task", "execution_check_apis": {
        "endpoint": "/check/drone_altitude", "parameters": {"drone_id": "d1", "expected_altitude": 15}}}
    scene = {"id": "s1", "drones": [{"id": "d1"}], "tasks": [original]}
    write(root / "scenes/s1.json", scene)
    manifest = {"files": {"scenes/s1.json": sha((root / "scenes/s1.json").read_bytes())},
                "scenes": [{"scene_id": "s1", "scene_file": "scenes/s1.json", "split": "train"}],
                "tasks": [{"task_id": "t1", "scene_id": "s1", "scene_file": "scenes/s1.json",
                           "command": original["content"], "command_sha256": sha(original["content"].encode()),
                           "original_task": original, "fleet_size": 1, "split": "train"}]}
    seal(root, manifest)
    return manifest


def seal(root, manifest):
    write(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(sha((root / "manifest.json").read_bytes()) + "\n")


def test_frozen_dataset_loads_and_preserves_source(tmp_path):
    expected = make_dataset(tmp_path)
    actual, scenes = load_dataset(tmp_path)
    assert actual == expected
    assert scenes["s1"][1]["tasks"][0] == actual["tasks"][0]["original_task"]


@pytest.mark.parametrize("field,value", [("split", "test"), ("scene_file", "other.json"),
    ("command", "changed"), ("command_sha256", "0" * 64), ("fleet_size", 2), ("task_id", "unknown")])
def test_resealed_manifest_cannot_rebind_task(tmp_path, field, value):
    manifest = make_dataset(tmp_path)
    manifest["tasks"][0][field] = value
    seal(tmp_path, manifest)
    with pytest.raises(ValueError):
        load_dataset(tmp_path)


@pytest.mark.parametrize("which", ["tasks", "scenes"])
def test_duplicate_identity_is_rejected(tmp_path, which):
    manifest = make_dataset(tmp_path)
    manifest[which].append(copy.deepcopy(manifest[which][0]))
    seal(tmp_path, manifest)
    with pytest.raises(ValueError, match="Duplicate"):
        load_dataset(tmp_path)


@pytest.mark.parametrize("filename", ["manifest.json", "scenes/s1.json"])
def test_source_tampering_is_rejected(tmp_path, filename):
    make_dataset(tmp_path)
    with (tmp_path / filename).open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError):
        load_dataset(tmp_path)


def test_symlink_is_rejected_even_with_matching_bytes(tmp_path):
    dataset = tmp_path / "dataset"
    make_dataset(dataset)
    content = (dataset / "scenes/s1.json").read_bytes()
    (tmp_path / "outside.json").write_bytes(content)
    (dataset / "scenes/s1.json").unlink()
    (dataset / "scenes/s1.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(ValueError, match="Symlink"):
        load_dataset(dataset)


def test_manifest_path_cannot_escape(tmp_path):
    manifest = make_dataset(tmp_path / "dataset")
    (tmp_path / "outside.json").write_text("{}")
    manifest["files"]["../outside.json"] = sha(b"{}")
    seal(tmp_path / "dataset", manifest)
    with pytest.raises(ValueError, match="Unsafe"):
        load_dataset(tmp_path / "dataset")


@pytest.mark.parametrize("split", ["dev", "development", "test"])
def test_training_export_cannot_use_nontrain(split):
    with pytest.raises(ValueError):
        select_tasks({"tasks": [{"split": split}]}, split, "teacher_training")


@pytest.mark.parametrize("purpose", ["development", "teacher_training"])
def test_test_never_materializes_through_training_or_development_entry(tmp_path, purpose):
    manifest = make_dataset(tmp_path / "dataset")
    for row in manifest["tasks"] + manifest["scenes"]:
        row["split"] = "test"
    seal(tmp_path / "dataset", manifest)
    with pytest.raises(ValueError, match="Test is reserved"):
        materialize(tmp_path / "dataset", tmp_path / "output", split="test", purpose=purpose)
    assert not (tmp_path / "output").exists()


def test_teacher_selection_is_train_only_and_rejects_empty():
    rows = [{"split": "train", "task_id": "a"}, {"split": "dev", "task_id": "b"},
            {"split": "test", "task_id": "c"}]
    assert select_tasks({"tasks": rows}, "train", "teacher_training") == rows[:1]
    with pytest.raises(ValueError, match="No tasks"):
        select_tasks({"tasks": []}, "train", "teacher_training")


def test_existing_attempt_cannot_be_overwritten(tmp_path):
    with pytest.raises(FileExistsError):
        materialize(tmp_path / "missing", tmp_path)


def test_extra_input_file_invalidates_inventory(tmp_path):
    make_dataset(tmp_path)
    (tmp_path / "private-payload.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        load_dataset(tmp_path)


def test_output_cannot_contaminate_frozen_dataset(tmp_path):
    make_dataset(tmp_path)
    with pytest.raises(ValueError, match="frozen dataset"):
        materialize(tmp_path, tmp_path / "inside")
    assert not (tmp_path / "inside").exists()


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_ambiguous_or_nonfinite_json_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_json(raw)


@pytest.mark.parametrize("field,value", [("split", "unexpected"), ("seed", True), ("seed", -1), ("seed", "123")])
def test_invalid_scene_contract_is_rejected(tmp_path, field, value):
    manifest = make_dataset(tmp_path)
    manifest["scenes"][0][field] = value
    seal(tmp_path, manifest)
    with pytest.raises(ValueError):
        load_dataset(tmp_path)


@pytest.mark.parametrize("seconds", [0, 502, True, 1.5])
def test_invalid_timeline_fails_before_import_or_output(tmp_path, seconds):
    with pytest.raises(ValueError, match="timeline"):
        materialize(tmp_path / "missing", tmp_path / "new", timeline_seconds=seconds)
    assert not (tmp_path / "new").exists()
