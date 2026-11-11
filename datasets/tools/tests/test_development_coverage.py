"""Offline identity, exposure, structural selection and frozen-catalog gates."""
import json
from pathlib import Path
import pickle
import shutil
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_development_coverage as coverage


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Coverage preparation forbids network, subprocesses and pickle loads")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("coverage-inputs")
    for name in (coverage.BENCHMARK, coverage.TEMPLATES, coverage.EXPOSURE, coverage.EXPOSURE_SUMS, coverage.PROTOCOL):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return root


@pytest.fixture(scope="module")
def catalog(tmp_path_factory, inputs):
    output = tmp_path_factory.mktemp("coverage-built") / "dataset"
    coverage.build(output, workspace=inputs)
    return output


@pytest.fixture
def copied(tmp_path, catalog):
    output = tmp_path / "copy"
    shutil.copytree(catalog, output)
    return output


def manifest(path):
    return coverage.parse((path / "manifest.json").read_bytes())


def rewrite_manifest(path, value):
    (path / "manifest.json").write_bytes(coverage.encoded(value))
    (path / "manifest.sha256").write_text(coverage.sha((path / "manifest.json").read_bytes()) + "\n")


def test_actual_catalog_covers_structure_and_never_labels_unseen_or_executed(catalog, inputs):
    result = coverage.verify(catalog, workspace=inputs)
    assert result["valid"] and result["planned_tasks"] == 18 and result["scenes"] == 9
    assert result["unseen_tasks"] == result["provider_calls"] == result["materialized_tasks"] == 0
    m = manifest(catalog)
    assert m["coverage"]["families"] == {"F" + str(i): 3 for i in range(1, 7)}
    assert m["coverage"]["selected_available_fleet_sizes"] == [3, 5]
    assert m["coverage"]["selected_explicit_actor_counts"] == [1, 2, 3, 4, 5]
    assert m["coverage"]["raw_only_tasks"] == 15
    assert m["coverage"]["optional_subprogram_candidate_counts"] == {"simple_chain": 2, "public_search": 1}
    assert all(t["split"] == "development" and t["exposure_status"] == "known_development"
               and t["execution_status"] == "not_run" and t["success"] is None for t in m["tasks"])
    assert all(s["split"] == "development" for s in m["scenes"])


def test_original_scene_task_checker_and_full_fleet_are_retained(catalog, inputs):
    templates = coverage.load_templates(inputs / coverage.TEMPLATES)
    scenes, _ = coverage.load_pool(inputs / coverage.BENCHMARK, templates)
    originals = {s["scene_id"]: s for s in scenes}
    m = manifest(catalog)
    for scene in m["scenes"]:
        assert (catalog / scene["scene_file"]).read_bytes() == originals[scene["scene_id"]]["raw"]
    for task in m["tasks"]:
        scene = originals[task["scene_id"]]["scene"]
        original = next(t for t in scene["tasks"] if t["id"] == task["task_id"])
        assert task["original_task"] == original and task["command"] == original["content"]
        assert task["fleet_size"] == len(scene["drones"])
        assert task["checker_sha256"] == coverage.sha(coverage.canonical(original["execution_check_apis"]))
        assert task["n_checks"] == len(coverage.leaves(original["execution_check_apis"]))
        assert task["initial_state"]["snapshot_file"] is None and task["initial_state"]["preparatory_actions"] == []


def test_selection_is_invariant_to_unconsumed_success_tokens_and_cost(inputs):
    templates = coverage.load_templates(inputs / coverage.TEMPLATES)
    _, tasks = coverage.load_pool(inputs / coverage.BENCHMARK, templates)
    before, before_audit = coverage.select(tasks)
    for index, task in enumerate(tasks):
        task["historical_success"] = index % 2 == 0
        task["token_cost"] = 1e30 if index % 3 == 0 else 0
        task["task"]["historical_failure_rank"] = len(tasks) - index
    after, after_audit = coverage.select(list(reversed(tasks)))
    assert [x[1]["task"]["id"] for x in before] == [x[1]["task"]["id"] for x in after]
    assert before_audit == after_audit


def test_exposure_allowlist_never_exports_model_scores_or_usage(inputs):
    templates = coverage.load_templates(inputs / coverage.TEMPLATES)
    _, tasks = coverage.load_pool(inputs / coverage.BENCHMARK, templates)
    keys = {(t["scene_record"]["scene_id"], t["task"]["id"]) for t in tasks}
    raw = (inputs / coverage.EXPOSURE).read_bytes()
    expected = coverage.exposure_index(raw, keys)
    rows = coverage.parse(raw)
    for row in rows:
        row.update(task_pass="POISON_PASS", observed_passed_checks="POISON_CHECKS",
                   observed_usage={"total_tokens": "POISON_TOKENS"}, scoring_ready_seconds="POISON_TIME")
    assert coverage.exposure_index(coverage.encoded(rows), keys) == expected
    assert all("POISON" not in json.dumps(value) for value in expected.values())


def test_repeated_actor_four_drone_template_is_rejected_before_execution(catalog):
    audit = coverage.parse((catalog / "selection-audit.json").read_bytes())
    slot = next(s for s in audit["slots"] if s["required_distinct_named_actors"] == 4)
    rejected = next(r for r in slot["rejected_before_execution"] if r["task_id"] == "664cc6ac")
    assert "distinct_named_actor_count_mismatch" in rejected["reasons"]
    assert slot["selected_task_id"] == "f6b47310"


def test_frozen_selection_retains_checker_weaknesses_and_duplicate_leaves(catalog):
    tasks = {t["task_id"]: t for t in manifest(catalog)["tasks"]}
    assert "hover_not_directly_checked_by_original_endpoint" in tasks["c74d7020"]["checker_review"]["notes"]
    assert "photo_obligation_not_directly_checked" in tasks["bcaf22cf"]["checker_review"]["notes"]
    assert "duplicate_original_checker_leaves_retained" in tasks["afa92e74"]["checker_review"]["notes"]
    assert tasks["afa92e74"]["n_checks"] == 12
    assert all(t["checker_review"]["initial_checker"] == "pending_materialization" for t in tasks.values())


def test_existing_catalog_is_not_overwritten(catalog, inputs):
    before = (catalog / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        coverage.build(catalog, workspace=inputs)
    assert (catalog / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("name", ["selection-spec.json", "PROTOCOL_CN.md", "README_CN.md", "manifest.json"])
def test_any_frozen_byte_change_is_rejected(copied, inputs, name):
    path = copied / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        coverage.verify(copied, workspace=inputs)


@pytest.mark.parametrize("change", [
    lambda m: m.update(split="test"),
    lambda m: m["source_population"].update(unseen_tasks=18),
    lambda m: m["tasks"][0].update(split="train"),
    lambda m: m["tasks"][0].update(success=True),
    lambda m: m["tasks"][0].update(fleet_size=1),
    lambda m: m["tasks"][0]["original_task"].update(content="Altered instruction"),
    lambda m: m["tasks"][0].update(n_checks=999),
    lambda m: m["scenes"][0].update(split="test"),
    lambda m: m["scenes"].pop(),
    lambda m: m["coverage"].update(raw_only_tasks=0),
])
def test_resealed_semantic_change_cannot_relabel_or_shrink_denominator(copied, inputs, change):
    value = manifest(copied)
    change(value)
    rewrite_manifest(copied, value)
    with pytest.raises(ValueError):
        coverage.verify(copied, workspace=inputs)


def test_undeclared_artifact_is_rejected(copied, inputs):
    (copied / "unsealed.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        coverage.verify(copied, workspace=inputs)


def test_scene_symlink_cannot_replace_original_file(copied, inputs, tmp_path):
    value = manifest(copied)
    scene = copied / value["scenes"][0]["scene_file"]
    outside = tmp_path / "outside.json"
    outside.write_bytes(scene.read_bytes())
    scene.unlink()
    scene.symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink"):
        coverage.verify(copied, workspace=inputs)


def test_snapshot_parser_rejects_duplicate_keys_and_nonfinite_numbers():
    for raw in (b'{"id":"one","id":"two"}', b'{"position":NaN}'):
        with pytest.raises(ValueError):
            coverage.parse(raw)


def test_frozen_protocol_can_be_verified_after_live_document_changes(catalog, inputs):
    file = inputs / coverage.PROTOCOL
    old = file.read_bytes()
    try:
        file.write_bytes(old + b"\nLater status update\n")
        assert coverage.verify(catalog, workspace=inputs)["valid"]
    finally:
        file.write_bytes(old)
