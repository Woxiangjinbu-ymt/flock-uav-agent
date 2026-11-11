"""Offline M0/M1 study gates. No provider, agent service or simulator calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

# These tests also run in detached, materialized legacy workspaces.
REPOSITORY = next(p for p in Path(__file__).resolve().parents if (p / "scripts").is_dir())
sys.path.insert(0, str((REPOSITORY / "scripts")))
import m1_progress_pilot as pilot


M1 = "1" * 40


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    spec = workspace / "experiments/m1-progress-v1"
    settings = workspace / "agent4drone/llm_settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"selected_provider": "unrelated", "provider_configs": {
        "deepseek_official": {"type": "openai-compatible", "base_url": "https://api.deepseek.com",
                              "default_model": "deepseek-flash", "api_key": "fixture-secret"}}}))
    common = {"scripts/m1_progress_pilot.py": Path(pilot.__file__).read_bytes(),
              "scripts/common_probe.py": b"VALUE = 'frozen-common'\n",
              "server/probe.py": b"VALUE = 'frozen-server'\n",
              "controller/probe.py": b"VALUE = 'frozen-checker'\n",
              "agent4drone/requirements.txt": b"# shared dependency declaration\n"}
    sources = {pilot.M0_COMMIT: {"agent4drone/uav_agent.py": b"METHOD = 'm0'\n"},
               M1: {"agent4drone/uav_agent.py": b"METHOD = 'm1'\nMODE_VARIABLE = 'UAV_TASK_PROGRESS_MODE'\n",
                    "agent4drone/progress.py": b"VALUE = 'm1-only'\n"}}
    for name, data in common.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    asset = workspace / "server/config/example/example.json"
    asset.parent.mkdir(parents=True)
    asset.write_text('{"source": "prepared asset"}')
    for component in ("server", "controller", "agent4drone"):
        python = workspace / component / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
    monkeypatch.setattr(pilot, "ROOT", workspace)
    monkeypatch.setattr(pilot.r, "RUNS", workspace / "experiments/runs")
    monkeypatch.setattr(pilot, "git_commit", lambda ref: pilot.M0_COMMIT if ref == pilot.M0_COMMIT else M1)
    monkeypatch.setattr(pilot, "agent_sources", lambda commit: sources[commit].copy())
    monkeypatch.setattr(pilot, "common_sources", lambda: common.copy())
    monkeypatch.setattr(pilot.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "ok", ""))
    monkeypatch.setattr(pilot.subprocess, "check_output", lambda *a, **kw: "fixture-package==1.0\n")
    return {"workspace": workspace, "spec": spec, "common": common, "sources": sources, "settings": settings}


def freeze(campaign):
    return pilot.prepare(M1, campaign["spec"])


def make_attempt(campaign, monkeypatch, arm="m0"):
    captured = {}

    def fake_exec(path, argv, env):
        captured.update(path=path, argv=argv, env=env.copy())

    monkeypatch.setattr(pilot.os, "execve", fake_exec)
    pilot.run(arm, campaign["spec"], M1)
    attempt = Path(captured["argv"][-1])
    return attempt, captured


def test_prepare_freezes_100_605_and_exact_original_order(campaign):
    study = freeze(campaign)
    assert study["plan"]["expected_tasks"] == 100
    assert study["plan"]["expected_checks"] == 605
    assert tuple(s["id"] for s in study["plan"]["sessions"]) == pilot.SCENES
    assert all(len(s["tasks"]) == 20 for s in study["plan"]["sessions"])
    assert study["plan"]["protocol"]["timeout_seconds"] == 500
    assert study["workers"] == 2
    assert study["model_calls_during_prepare"] == 0
    assert "fixture-secret" not in json.dumps(study)
    assert "fixture-secret" not in "".join(p.read_text(errors="ignore") for p in
                                           (campaign["spec"] / "frozen").rglob("*.json"))
    assert pilot.load_study(campaign["spec"]) == study


def test_arms_share_data_common_sources_and_dependencies_but_pin_agent(campaign):
    study = freeze(campaign)
    frozen = campaign["spec"] / "frozen"
    for arm, commit in [("m0", pilot.M0_COMMIT), ("m1", M1)]:
        with zipfile.ZipFile(frozen / arm / "source.zip") as archive:
            names = set(archive.namelist())
            for name, content in campaign["sources"][commit].items():
                assert archive.read(name) == content
            assert ("agent4drone/progress.py" in names) == (arm == "m1")
            for name, content in campaign["common"].items():
                assert archive.read(name) == content
        assert study["methods"][arm]["runtime_env"] == {pilot.MODE_VARIABLE: pilot.MODES[arm]}
    for component in ("server", "controller", "agent4drone"):
        assert (frozen / "m0" / f"{component}-freeze.txt").read_bytes() == (
            frozen / "m1" / f"{component}-freeze.txt").read_bytes()


def test_prepare_never_overwrites_frozen_study(campaign):
    freeze(campaign)
    with pytest.raises(ValueError, match="already frozen"):
        freeze(campaign)


def test_prepare_requires_implemented_m1_mode(campaign):
    campaign["sources"][M1]["agent4drone/uav_agent.py"] = b"METHOD='not-ready'\n"
    with pytest.raises(ValueError, match="does not implement"):
        freeze(campaign)
    assert not (campaign["spec"] / "frozen").exists()


def test_prepare_rejects_same_method_commit(campaign, monkeypatch):
    monkeypatch.setattr(pilot, "git_commit", lambda ref: pilot.M0_COMMIT)
    with pytest.raises(ValueError, match="different method commit"):
        freeze(campaign)


@pytest.mark.parametrize("mutation", ["reorder", "checks", "timeout"])
def test_manifest_changes_are_rejected(campaign, monkeypatch, tmp_path, mutation):
    manifest = pilot.r.read_json(pilot.DATA / "fast100.json")
    if mutation == "reorder":
        manifest["sessions"].reverse()
    elif mutation == "checks":
        manifest["expected_checks"] = 604
    else:
        manifest["execution_protocol"]["timeout_seconds"] = 600
    data = tmp_path / "changed-data"
    data.mkdir()
    (data / "fast100.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(pilot, "DATA", data)
    with pytest.raises(ValueError, match="manifest changed"):
        freeze(campaign)


@pytest.mark.parametrize("file", ["fast100.json", "m0/source.zip", "m1/source_hashes.json",
                                   "data/scenes/Area_Search_Hard_1_ef36e27a.json"])
def test_load_rejects_modified_frozen_inputs(campaign, file):
    freeze(campaign)
    path = campaign["spec"] / "frozen" / file
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="files changed"):
        pilot.load_study(campaign["spec"])


def test_frozen_runtime_ignores_later_live_agent_and_assets(campaign, tmp_path):
    study = freeze(campaign)
    live = campaign["workspace"] / "agent4drone/uav_agent.py"
    live.write_text("raise RuntimeError('live source must never load')\n")
    (campaign["workspace"] / "server/config/example/example.json").write_text('{"changed": true}')
    for arm in pilot.MODES:
        attempt = tmp_path / arm
        attempt.mkdir()
        runtime = pilot.materialize_runtime(attempt, campaign["spec"] / "frozen", arm, campaign["workspace"])
        commit = study["methods"][arm]["agent_commit"]
        assert (runtime / "agent4drone/uav_agent.py").read_bytes() == campaign["sources"][commit]["agent4drone/uav_agent.py"]
        assert json.loads((runtime / "server/config/example/example.json").read_text()) == {"source": "prepared asset"}
        for component in ("server", "controller", "agent4drone"):
            assert (runtime / component / ".venv").resolve() == (campaign["workspace"] / component / ".venv").resolve()


def test_run_executes_archived_launcher_and_forces_m0_off(campaign, monkeypatch):
    freeze(campaign)
    monkeypatch.setenv(pilot.MODE_VARIABLE, "summary")
    attempt, captured = make_attempt(campaign, monkeypatch)
    assert captured["argv"][-2] == "_execute"
    assert Path(captured["argv"][-3]) == attempt / "runtime/scripts/m1_progress_pilot.py"
    assert captured["env"][pilot.MODE_VARIABLE] == "off"
    assert "fixture-secret" not in str(captured["argv"])
    metadata = pilot.r.read_json(attempt / "manifest.json")["metadata"]["freeze"]
    assert metadata["agent_commit"] == pilot.M0_COMMIT
    assert metadata["runtime_env"] == {pilot.MODE_VARIABLE: "off"}
    pilot.assert_method_identity(attempt)
    assert pilot.r.read_json(campaign["spec"] / "runs.json")["m0"]["status"] == "prepared"
    with pytest.raises(ValueError, match="already has"):
        pilot.run("m0", campaign["spec"], M1)


def test_m1_requires_completed_m0_and_uses_own_pinned_source(campaign, monkeypatch):
    freeze(campaign)
    with pytest.raises(ValueError, match="verify M0"):
        pilot.run("m1", campaign["spec"], M1)
    make_attempt(campaign, monkeypatch)
    records_path = campaign["spec"] / "runs.json"
    records = pilot.r.read_json(records_path)
    records["m0"]["status"] = "completed_and_verified"
    pilot.r.save_json(records_path, records)
    attempt, captured = make_attempt(campaign, monkeypatch, "m1")
    assert captured["env"][pilot.MODE_VARIABLE] == "summary"
    assert (attempt / "runtime/agent4drone/progress.py").is_file()
    _, metadata = pilot.assert_method_identity(attempt)
    assert metadata["agent_commit"] == M1


def test_run_rejects_changed_common_sources(campaign):
    freeze(campaign)
    campaign["common"]["scripts/common_probe.py"] = b"changed"
    with pytest.raises(ValueError, match="Common launcher"):
        pilot.run("m0", campaign["spec"], M1)


def test_run_rejects_wrong_requested_method_commit(campaign, monkeypatch):
    freeze(campaign)
    monkeypatch.setattr(pilot, "git_commit", lambda ref: "2" * 40)
    with pytest.raises(ValueError, match="Requested M1 commit"):
        pilot.run("m0", campaign["spec"], "another-commit")
    assert not (campaign["spec"] / "runs.json").exists()


def test_run_rejects_changed_model_configuration(campaign):
    freeze(campaign)
    settings = json.loads(campaign["settings"].read_text())
    settings["provider_configs"]["deepseek_official"]["default_model"] = "changed"
    campaign["settings"].write_text(json.dumps(settings))
    with pytest.raises(ValueError, match="official deepseek"):
        pilot.run("m0", campaign["spec"], M1)


def test_method_mode_is_identity_bound(campaign, monkeypatch):
    freeze(campaign)
    attempt, _ = make_attempt(campaign, monkeypatch)
    metadata = pilot.r.read_json(attempt / "method-freeze.json")
    metadata["runtime_env"][pilot.MODE_VARIABLE] = "summary"
    pilot.r.save_json(attempt / "method-freeze.json", metadata)
    with pytest.raises(ValueError, match="freeze/configuration"):
        pilot.assert_method_identity(attempt)


@pytest.mark.parametrize("kind", ["asset", "scene"])
def test_runtime_json_mutations_are_rejected_before_execution(campaign, monkeypatch, kind):
    freeze(campaign)
    attempt, _ = make_attempt(campaign, monkeypatch)
    path = (attempt / "runtime/server/config/example/example.json" if kind == "asset" else
            next((attempt / "runtime/data/scenes").glob("*.json")))
    path.write_text('{"changed": true}')
    with pytest.raises(ValueError, match="Frozen runtime"):
        pilot.assert_method_identity(attempt)


@pytest.mark.parametrize("previous", [None, "shadow"])
def test_method_environment_overrides_and_restores_even_on_exception(monkeypatch, previous):
    if previous is None:
        monkeypatch.delenv(pilot.MODE_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(pilot.MODE_VARIABLE, previous)
    with pytest.raises(RuntimeError):
        with pilot.method_environment("summary"):
            assert os.environ[pilot.MODE_VARIABLE] == "summary"
            raise RuntimeError("fixture")
    assert os.environ.get(pilot.MODE_VARIABLE) == previous


def test_internal_execute_refuses_live_workspace(tmp_path):
    with pytest.raises(ValueError, match="archived runtime"):
        pilot.execute(tmp_path / "attempt")


def test_global_lock_is_used_for_registration(campaign, monkeypatch):
    freeze(campaign)
    with pilot.rp.runner_lock(pilot.r):
        with pytest.raises(ValueError, match="Another experiment runner"):
            make_attempt(campaign, monkeypatch)
    assert not (campaign["spec"] / "runs.json").exists()


def test_execute_passes_summary_mode_to_runner_and_finalizes(campaign, monkeypatch):
    freeze(campaign)
    make_attempt(campaign, monkeypatch)
    records_path = campaign["spec"] / "runs.json"
    records = pilot.r.read_json(records_path)
    records["m0"]["status"] = "completed_and_verified"
    pilot.r.save_json(records_path, records)
    attempt, _ = make_attempt(campaign, monkeypatch, "m1")
    runtime = attempt / "runtime"
    monkeypatch.setattr(pilot, "ROOT", runtime)
    monkeypatch.setattr(pilot.r, "ROOT", runtime)
    monkeypatch.setattr(pilot.r, "DATA", runtime / "data")
    monkeypatch.setattr(pilot.r, "load_plan", pilot.r.load_plan)
    monkeypatch.setenv(pilot.MODE_VARIABLE, "shadow")
    calls = []
    monkeypatch.setattr(pilot.rp, "verify_provenance", lambda *args: calls.append("provenance"))
    monkeypatch.setattr(pilot.rp, "verify_archive", lambda *args: calls.append("archive"))
    monkeypatch.setattr(pilot.rp, "verify_scene", lambda *args: calls.append("scene"))

    def coordinate(r, attempt, plan, settings, key, manifest, rows):
        assert r.ROOT == runtime
        assert os.environ[pilot.MODE_VARIABLE] == "summary"
        assert key == "fixture-secret"
        assert r.load_plan(plan["name"]) == plan
        manifest["status"] = "completed"
        for selected in plan["sessions"]:
            manifest["scenes"][selected["id"]].update(status="completed", generation=1)
        saved = r.initial_results(plan)
        for row in saved:
            row.update(status="scored", task_pass=True, passed=True, world_quiescent=True,
                       continuation_safe=True, agent_timed_out=False)
            for leaf in row["leaves"]:
                leaf.update(status="scored", result=True)
        r.save_json(attempt / "manifest.json", manifest)
        r.save_json(attempt / "results.json", saved)
        return True

    monkeypatch.setattr(pilot.rp, "coordinate", coordinate)
    assert pilot.execute(attempt) is True
    assert os.environ[pilot.MODE_VARIABLE] == "shadow"
    assert calls == ["provenance", "provenance", "archive", "scene", "scene", "scene", "scene", "scene"]
    assert pilot.r.read_json(records_path)["m1"]["status"] == "completed_and_verified"
    assert pilot.verify_saved(attempt)["metrics"]["passed_tasks"] == 100


def test_execute_stops_dispatch_on_scene_failure_and_records_interruption(campaign, monkeypatch):
    freeze(campaign)
    attempt, _ = make_attempt(campaign, monkeypatch)
    runtime = attempt / "runtime"
    monkeypatch.setattr(pilot, "ROOT", runtime)
    monkeypatch.setattr(pilot.r, "ROOT", runtime)
    monkeypatch.setattr(pilot.r, "DATA", runtime / "data")
    monkeypatch.setattr(pilot.r, "load_plan", pilot.r.load_plan)
    monkeypatch.setattr(pilot.rp, "verify_provenance", lambda *args: None)
    original = lambda *args, **kwargs: {"status": "interrupted"}
    monkeypatch.setattr(pilot.rp, "run_scene", original)

    def coordinate(*args):
        import threading
        stopped = threading.Event()
        pilot.rp.run_scene(None, None, None, None, None, None, stopped, None)
        assert stopped.is_set()
        assert os.environ[pilot.MODE_VARIABLE] == "off"
        manifest = pilot.r.read_json(attempt / "manifest.json")
        manifest["status"] = "interrupted"
        pilot.r.save_json(attempt / "manifest.json", manifest)
        return False

    monkeypatch.setattr(pilot.rp, "coordinate", coordinate)
    assert pilot.execute(attempt) is False
    assert pilot.rp.run_scene is original
    assert pilot.r.read_json(campaign["spec"] / "runs.json")["m0"]["status"] == "interrupted"
    assert (attempt / "suite-seal.json").exists()


def test_malformed_study_mode_is_rejected(campaign):
    freeze(campaign)
    path = campaign["spec"] / "frozen/study.json"
    study = pilot.r.read_json(path)
    study["methods"]["m0"]["runtime_env"][pilot.MODE_VARIABLE] = "summary"
    pilot.r.save_json(path, study)
    with pytest.raises(ValueError, match="method mode"):
        pilot.load_study(campaign["spec"])
