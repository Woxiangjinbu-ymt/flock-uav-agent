"""Synthetic preparation contracts; no provider, world, process, or pickle load.

The 7 tasks and 14 slots here are proposals. Test cases are not trial results.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path
import pickle
import runpy
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_harness_natural_agent as preparation


FIXED_TASKS = (
    ("7eba7390", "public_search"), ("24ac1838", "public_search"),
    ("b1ad4f3e", "public_search"), ("b1eb0270", "simple_chain"),
    ("898cfdca", "simple_chain"), ("784d71f8", "public_search"),
    ("1905e0ad", "public_search"),
)
CANARY = "PRIVATE_CHECKER_SYNTHETIC_PREPARATION_ONLY"
PUBLIC_KEYS = {"task_id", "scene_id", "command", "command_hash"}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else
                     (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
    return path


def get(path):
    return json.loads(path.read_bytes())


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preparation tests forbid network, subprocesses, and pickle deserialization")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)


@pytest.fixture
def workspace(tmp_path):
    """All 410/302/80 references are tiny inert files, never real experiment data."""
    root = tmp_path / "workspace"
    root.mkdir()
    put(root / preparation.PROTOCOL, b"Synthetic preparation-only protocol. Zero online authorization.\n")
    put(root / preparation.SCRIPT, b"# synthetic source reference, never executed\n")
    put(root / preparation.TASK_SOURCE,
        ("TASKS = " + repr(FIXED_TASKS) + "\nraise AssertionError('must only AST parse')\n").encode())
    study = root / preparation.STUDY
    original_files = {}
    for index, (task_id, _) in enumerate(FIXED_TASKS):
        command = f"公开原始任务 {index}：使用指定动作。"
        source_name = f"synthetic-tasks/{task_id}.json"
        source = put(root / source_name, {"command": command, "execution_check_apis": CANARY,
                                         "related_apis": {"private": CANARY}})
        snapshot = put(study / f"fixtures/{task_id}.pkl", b"NOT-A-PICKLE\x80\n" + CANARY.encode())
        timeline = put(study / f"fixtures/{task_id}-timeline.json", [{"private": CANARY}])
        fixture = {"task_id": task_id, "scene_id": f"scene-{index}", "command": command,
                   "command_sha256": digest(command.encode()), "problem_cluster": f"cluster-{index}",
                   "eligibility": "public_search" if index < 3 else "simple_chain" if index < 5 else "unsupported",
                   "snapshot_sha256": digest(snapshot.read_bytes()), "timeline_file": timeline.name,
                   "timeline_sha256": digest(timeline.read_bytes()), "source": source_name,
                   "source_sha256": digest(source.read_bytes()), "private_checker": CANARY,
                   "execution_check_apis": [{"hidden": CANARY}], "target_coordinates": [CANARY]}
        fixture_path = put(study / f"fixtures/{task_id}.json", fixture)
        for path in (snapshot, timeline, fixture_path):
            original_files[path.relative_to(study).as_posix()] = digest(path.read_bytes())
    for index in range(410 - len(original_files)):
        name = f"inert-original/{index:03}.txt"
        path = put(study / name, f"synthetic frozen original {index}\n".encode())
        original_files[name] = digest(path.read_bytes())
    put(study / "manifest.json", {"files": original_files})

    accepted = root / preparation.ACCEPTED_RUN
    sources = {}
    for index in range(80):
        name = f"synthetic-code/module_{index:03}.py"
        value = f"# inert accepted source {index}\n".encode()
        put(root / name, value)
        put(accepted / "source" / name, value)
        sources[name] = digest(value)
    manifest_path = put(accepted / "manifest.json", {"sources": sources})
    put(accepted / "manifest.sha256", (digest(manifest_path.read_bytes()) + "\n").encode())
    put(accepted / "summary.json", {"status": "passed", "check_count": 96,
        "checks": {f"synthetic-check-{index:03}": True for index in range(96)}, "failed_checks": []})
    for index in range(302 - 83):
        put(accepted / f"inert-result/{index:03}.json", {"synthetic": index})
    sealed = {path.relative_to(accepted).as_posix(): digest(path.read_bytes())
              for path in accepted.rglob("*") if path.is_file()}
    assert len(original_files) == 410 and len(sources) == 80 and len(sealed) == 302
    put(accepted / "campaign-seal.json", {"status": "passed", "files": sealed})
    return root


@pytest.fixture
def prepared(workspace):
    output = workspace / "experiments/proposed-synthetic"
    manifest = preparation.prepare(workspace, output)
    return workspace, output, manifest


def rehash_output(output, name):
    manifest = get(output / "manifest.json")
    manifest["files"][name] = digest((output / name).read_bytes())
    put(output / "manifest.json", manifest)


def reseal_accepted(workspace, name):
    accepted = workspace / preparation.ACCEPTED_RUN
    seal = get(accepted / "campaign-seal.json")
    seal["files"][name] = digest((accepted / name).read_bytes())
    put(accepted / "campaign-seal.json", seal)


def rehash_fixture(workspace, task_id):
    study = workspace / preparation.STUDY
    manifest = get(study / "manifest.json")
    name = f"fixtures/{task_id}.json"
    manifest["files"][name] = digest((study / name).read_bytes())
    put(study / "manifest.json", manifest)


def test_prepare_and_verify_remain_zero_authorization_and_read_only(prepared):
    workspace, output, manifest = prepared
    before = {p.relative_to(workspace).as_posix(): (digest(p.read_bytes()), p.stat().st_mtime_ns)
              for p in workspace.rglob("*") if p.is_file()}
    assert preparation.verify(workspace, output) == manifest
    assert manifest["authorized_executions"] == 0 and manifest["authorized_trial_ids"] == []
    assert manifest["provider_calls"] == 0 and manifest["online_ready"] is False
    assert manifest["online_budget"] == {"executions": 0, "http_attempts": 0, "tokens": 0, "cost": 0}
    assert get(output / "preflight.json")["provider_calls"] == 0
    assert get(output / "preflight.json")["online_ready"] is False
    assert len(list(output.iterdir())) == 8
    after = {p.relative_to(workspace).as_posix(): (digest(p.read_bytes()), p.stat().st_mtime_ns)
             for p in workspace.rglob("*") if p.is_file()}
    assert before == after


def test_schedule_is_fixed_seven_pairs_with_scheduler_only_difference(prepared):
    _, output, manifest = prepared
    schedule = get(output / "schedule.proposed.json")
    assert preparation.TASKS == FIXED_TASKS
    assert schedule["order_counts"] == {"AF": 4, "FA": 3}
    assert schedule["fully_balanced"] is False
    assert schedule["arms"] == {"A": "applicability", "F": "failure"}
    assert len(schedule["slots"]) == 14 and manifest["proposed_tasks"] == 7
    assert len({slot["proposed_id"] for slot in schedule["slots"]}) == 14
    for index, (task_id, _) in enumerate(FIXED_TASKS):
        pair = schedule["slots"][2 * index:2 * index + 2]
        assert [slot["slot"] for slot in pair] == [2 * index + 1, 2 * index + 2]
        assert {slot["task_id"] for slot in pair} == {task_id}
        expected = ["applicability", "failure"] if index % 2 == 0 else ["failure", "applicability"]
        assert [slot["scheduler"] for slot in pair] == expected
        assert all(set(slot) == {"slot", "task_id", "scheduler", "proposed_id"} for slot in pair)
    assert manifest["common"] == {"revision": "observed_target_v7", "static_obstacles": True,
        "task_progress_mode": "evidence_harness", "scheduler": "per_proposed_slot"}


def test_policy_catalog_uses_only_public_whitelist_and_no_binary_copy(prepared):
    workspace, output, _ = prepared
    catalog = get(output / "tasks.public.json")
    assert len(catalog) == 7 and CANARY not in (output / "tasks.public.json").read_text()
    for (task_id, _), record in zip(FIXED_TASKS, catalog):
        fixture = get(workspace / preparation.STUDY / f"fixtures/{task_id}.json")
        assert set(record) == PUBLIC_KEYS
        assert record["task_id"] == task_id and record["command"] == fixture["command"]
        assert record["command_hash"] == digest(record["command"].encode())
    assert not list(output.rglob("*.pkl"))
    assert all(CANARY not in path.read_text() for path in output.iterdir())
    annotations = get(output / "evaluation-annotations.json")
    assert [record["eligibility"] for record in annotations] == [
        "public_search"] * 3 + ["simple_chain"] * 2 + ["unsupported"] * 2
    assert all(record["scope"] == "evaluator_only_not_policy_input" for record in annotations)
    assert all(record["known_development"] is True for record in annotations)
    assert [record["problem_cluster"] for record in annotations] == [f"cluster-{index}" for index in range(7)]


def test_source_references_distinguish_accepted_code_from_new_preparation(prepared):
    _, output, _ = prepared
    refs = get(output / "source-references.json")
    assert refs["accepted_source_count"] == len(refs["accepted_sources_matching_current"]) == 80
    assert refs["accepted_sealed_file_count"] == 302
    assert set(refs["preparation_only_sources_not_covered_by_episode_acceptance"]) == {
        preparation.SCRIPT, preparation.TASK_SOURCE, preparation.PROTOCOL}
    inputs = get(output / "input-references.json")
    assert len(inputs["original_manifest_files"]) == 410
    assert len(inputs["selected_dependencies"]) == 21
    assert inputs["binary_handling"] == "hash_only_no_copy_no_deserialization"


@pytest.mark.parametrize("target", ["source", "fixture", "snapshot", "timeline", "protocol", "original_source", "accepted_summary"])
def test_changed_input_invalidates_preparation(prepared, target):
    workspace, output, _ = prepared
    paths = {
        "source": workspace / "synthetic-code/module_000.py",
        "fixture": workspace / preparation.STUDY / "fixtures/7eba7390.json",
        "snapshot": workspace / preparation.STUDY / "fixtures/7eba7390.pkl",
        "timeline": workspace / preparation.STUDY / "fixtures/7eba7390-timeline.json",
        "protocol": workspace / preparation.PROTOCOL,
        "original_source": workspace / "synthetic-tasks/7eba7390.json",
        "accepted_summary": workspace / preparation.ACCEPTED_RUN / "summary.json",
    }
    paths[target].write_bytes(paths[target].read_bytes() + b"\nTAMPER\n")
    with pytest.raises((ValueError, KeyError)):
        preparation.verify(workspace, output)


@pytest.mark.parametrize("change", ["authorize", "trial_ids", "online_ready", "provider_calls", "budget", "revision", "static_contract", "progress_mode", "extra_arm"])
def test_manifest_semantic_changes_are_rejected_without_trusting_its_hash(prepared, change):
    workspace, output, _ = prepared
    path = output / "manifest.json"
    manifest = get(path)
    if change == "authorize": manifest["authorized_executions"] = 14
    elif change == "trial_ids": manifest["authorized_trial_ids"] = ["natural-7eba7390-applicability"]
    elif change == "online_ready": manifest["online_ready"] = True
    elif change == "provider_calls": manifest["provider_calls"] = 1
    elif change == "budget": manifest["online_budget"]["tokens"] = 100
    elif change == "revision": manifest["common"]["revision"] = "legacy"
    elif change == "static_contract": manifest["common"]["static_obstacles"] = False
    elif change == "progress_mode": manifest["common"]["task_progress_mode"] = "raw"
    else: manifest["arm_overrides"] = {"applicability": {"max_iterations": 999}}
    put(path, manifest)
    with pytest.raises(ValueError):
        preparation.verify(workspace, output)


@pytest.mark.parametrize("change", ["order", "duplicate_slot", "task", "arm_budget", "scheduler", "count"])
def test_rehashed_schedule_cannot_change_frozen_pair_semantics(prepared, change):
    workspace, output, _ = prepared
    path = output / "schedule.proposed.json"
    schedule = get(path)
    if change == "order": schedule["slots"][0], schedule["slots"][1] = schedule["slots"][1], schedule["slots"][0]
    elif change == "duplicate_slot": schedule["slots"][1] = copy.deepcopy(schedule["slots"][0])
    elif change == "task": schedule["slots"][0]["task_id"] = "b1eb0270"
    elif change == "arm_budget": schedule["slots"][0]["max_iterations"] = 999
    elif change == "scheduler": schedule["slots"][0]["scheduler"] = "scripted"
    else: schedule["order_counts"] = {"AF": 7, "FA": 0}
    put(path, schedule)
    rehash_output(output, path.name)
    with pytest.raises(ValueError):
        preparation.verify(workspace, output)


@pytest.mark.parametrize("field", ["private_checker", "execution_check_apis", "eligibility", "target_coordinates", "problem_cluster", "known_development"])
def test_rehashed_policy_fields_are_rejected(prepared, field):
    workspace, output, _ = prepared
    path = output / "tasks.public.json"
    catalog = get(path)
    catalog[0][field] = CANARY
    put(path, catalog)
    rehash_output(output, path.name)
    with pytest.raises(ValueError):
        preparation.verify(workspace, output)


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate_json_key", "rehashed_preflight_ready"])
def test_incomplete_or_misleading_output_is_rejected(prepared, damage):
    workspace, output, _ = prepared
    if damage == "missing": (output / "evaluation-annotations.json").unlink()
    elif damage == "extra": put(output / "run.py", b"# forbidden execution entry\n")
    elif damage == "duplicate_json_key":
        path = output / "manifest.json"
        path.write_text(path.read_text().replace('"online_ready": false', '"online_ready": false, "online_ready": true'))
    else:
        path = output / "preflight.json"
        preflight = get(path)
        preflight["online_ready"] = True
        put(path, preflight)
        rehash_output(output, path.name)
    with pytest.raises(ValueError):
        preparation.verify(workspace, output)


@pytest.mark.parametrize("field,value", [("status", "failed"), ("check_count", 95), ("failed_checks", ["one"]), ("checks", {"only-one": True})])
def test_resealed_acceptance_still_requires_all_96_checks(workspace, field, value):
    path = workspace / preparation.ACCEPTED_RUN / "summary.json"
    summary = get(path)
    summary[field] = value
    put(path, summary)
    reseal_accepted(workspace, "summary.json")
    output = workspace / "proposed"
    with pytest.raises(ValueError):
        preparation.prepare(workspace, output)
    assert not output.exists()


def test_truthy_non_boolean_check_is_not_accepted(workspace):
    path = workspace / preparation.ACCEPTED_RUN / "summary.json"
    summary = get(path)
    summary["checks"]["synthetic-check-000"] = 1
    put(path, summary)
    reseal_accepted(workspace, "summary.json")
    with pytest.raises(ValueError):
        preparation.prepare(workspace, workspace / "proposed")


@pytest.mark.parametrize("field,value", [("task_id", "wrong"), ("command_sha256", "0" * 64), ("eligibility", "unsupported"), ("scene_id", "")])
def test_rehashed_fixture_still_obeys_identity_and_public_contract(workspace, field, value):
    path = workspace / preparation.STUDY / "fixtures/7eba7390.json"
    fixture = get(path)
    fixture[field] = value
    put(path, fixture)
    rehash_fixture(workspace, "7eba7390")
    with pytest.raises(ValueError):
        preparation.prepare(workspace, workspace / "proposed")


def test_search_cluster_duplicates_are_not_new_representatives(workspace):
    path = workspace / preparation.STUDY / "fixtures/24ac1838.json"
    fixture = get(path)
    fixture["problem_cluster"] = "cluster-0"
    put(path, fixture)
    rehash_fixture(workspace, "24ac1838")
    with pytest.raises(ValueError):
        preparation.prepare(workspace, workspace / "proposed")


@pytest.mark.parametrize("declaration", ["TASKS = ()", "TASKS = tuple()", "TASKS = ()\nTASKS = ()"])
def test_task_declaration_is_parsed_without_import_and_must_match(workspace, declaration):
    put(workspace / preparation.TASK_SOURCE, declaration.encode())
    with pytest.raises(ValueError):
        preparation.prepare(workspace, workspace / "proposed")


@pytest.mark.parametrize("relative", ["../escape", "inner/../../escape", "/tmp/absolute-input", "."])
def test_relative_artifact_paths_reject_escape(tmp_path, relative):
    with pytest.raises(ValueError):
        preparation.local(tmp_path, relative)


@pytest.mark.parametrize("kind", ["workspace_itself", "outside", "parent_traversal"])
def test_output_cannot_escape_workspace(workspace, kind):
    output = workspace if kind == "workspace_itself" else workspace.parent / "outside" if kind == "outside" else workspace / "inner/../../outside"
    with pytest.raises(ValueError):
        preparation.prepare(workspace, output)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_existing_output_is_never_overwritten(workspace, kind):
    output = workspace / "proposed"
    if kind == "file": output.write_text("keep original")
    else:
        output.mkdir()
        (output / "keep.txt").write_text("keep original")
    with pytest.raises(FileExistsError):
        preparation.prepare(workspace, output)
    assert (output if kind == "file" else output / "keep.txt").read_text() == "keep original"


@pytest.mark.parametrize("kind", ["input_file", "input_directory", "output_file", "output_directory", "workspace"])
def test_symlinks_are_rejected_even_when_target_stays_local(prepared, kind):
    workspace, output, _ = prepared
    if kind == "workspace":
        alias = workspace.parent / "workspace-alias"
        alias.symlink_to(workspace, target_is_directory=True)
        with pytest.raises(ValueError): preparation.verify(alias, output)
        return
    if kind == "input_file": path = workspace / preparation.PROTOCOL
    elif kind == "input_directory": path = workspace / "synthetic-code"
    elif kind == "output_file": path = output / "tasks.public.json"
    else: path = output
    renamed = path.with_name(path.name + "-real")
    path.rename(renamed)
    path.symlink_to(renamed, target_is_directory=renamed.is_dir())
    with pytest.raises(ValueError):
        preparation.verify(workspace, output)


@pytest.mark.parametrize("argv", [["run"], ["--run"], ["prepare", "--run"]])
def test_cli_has_no_execution_action(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["prepare_harness_natural_agent.py", *argv])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / preparation.SCRIPT), run_name="__main__")
    assert exc.value.code == 2


def test_preparation_imports_no_runtime_or_execution_modules():
    tree = ast.parse((ROOT / preparation.SCRIPT).read_bytes())
    imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports <= {"__future__", "argparse", "ast", "hashlib", "json", "os", "pathlib"}
