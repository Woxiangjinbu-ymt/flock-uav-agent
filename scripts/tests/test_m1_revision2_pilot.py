"""Single-arm budget and frozen-method gates; no online execution."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import m1_revision2_pilot as pilot
import m1_revision2_job as job

COMMIT = "3" * 40
REAL_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    study = workspace / "experiments/m1-revision2-v1"
    history = workspace / "history"
    real_prior = REAL_ROOT / "experiments/m1-progress-v1/frozen"
    for folder in ("data", "assets"):
        shutil.copytree(real_prior / folder, history / "frozen" / folder)
    shutil.copyfile(real_prior / "fast100.json", history / "frozen/fast100.json")
    freeze = "fixture-package==1.0\n"
    (history / "frozen/m0").mkdir()
    for component in ("server", "controller", "agent4drone"):
        (history / "frozen/m0" / f"{component}-freeze.txt").write_text(freeze)
        python = workspace / component / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
    settings = {"selected_provider": "deepseek_official", "provider_configs": {"deepseek_official": {
        "type": "openai-compatible", "base_url": "https://api.deepseek.com", "default_model": "deepseek-flash",
        "api_key": "fixture-secret"}}, "uav_api_key": "", "share_blackboard_by_session": True,
        "toolchain_json_recording": True}
    (workspace / "agent4drone/llm_settings.json").write_text(json.dumps(settings))
    common = {"scripts/reproduction.py": b"# frozen common\n", pilot.DRIVER: Path(pilot.__file__).read_bytes(),
              "scripts/m1_revision2_job.py": Path(job.__file__).read_bytes()}
    method = {"agent4drone/uav_agent.py": b'MODE="evidence_r2"\n', **{name: b"# fixture method\n" for name in pilot.REQUIRED}}
    prior = pilot.r.read_json(real_prior / "study.json")
    prior["common_source_sha256"] = {"scripts/reproduction.py": pilot.r.digest(common["scripts/reproduction.py"])}
    prior["configuration"] = pilot.r.public_settings(settings)
    for name, data in common.items():
        p = workspace / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    original_ref = pilot.historical_reference()
    monkeypatch.setattr(pilot, "ROOT", workspace)
    monkeypatch.setattr(pilot, "HISTORICAL", history)
    monkeypatch.setattr(pilot.r, "RUNS", workspace / "experiments/runs")
    monkeypatch.setattr(pilot.original, "load_study", lambda _: copy.deepcopy(prior))
    monkeypatch.setattr(pilot, "historical_reference", lambda: copy.deepcopy(original_ref))
    monkeypatch.setattr(pilot, "git_commit", lambda ref: COMMIT)
    monkeypatch.setattr(pilot, "agent_sources", lambda ref: method.copy())
    monkeypatch.setattr(pilot, "common_sources", lambda: common.copy())
    monkeypatch.setattr(pilot, "dependency_freezes", lambda _: {c: freeze for c in ("server", "controller", "agent4drone")})
    return {"workspace": workspace, "study": study, "common": common, "method": method, "prior": prior}


def ready(campaign):
    study = pilot.prepare(COMMIT, campaign["study"])
    pilot.r.save_json(campaign["study"] / "preflight.json", {
        "ready_to_start": True, "study_sha256": pilot.r.digest((campaign["study"] / "frozen/study.json").read_bytes())})
    return study


def attempt(campaign, monkeypatch):
    captured = {}
    monkeypatch.setattr(pilot.os, "execve", lambda path, argv, env: captured.update(path=path, argv=argv, env=env.copy()))
    pilot.run(campaign["study"], COMMIT)
    return Path(captured["argv"][-1]), captured


def test_preparation_is_single_arm_100_tasks_with_verified_historical_80(campaign):
    study = ready(campaign)
    assert study["arm_order"] == ["m1r2"] and set(study["methods"]) == {"m1r2"}
    assert study["plan"]["expected_tasks"] == 100 and study["plan"]["expected_checks"] == 605
    assert study["plan"]["protocol"]["timeout_seconds"] == 500 and study["workers"] == 2
    assert study["model_calls_during_prepare"] == 0
    assert study["methods"]["m1r2"]["runtime_env"] == pilot.ENV
    assert study["historical_reference"]["passed_tasks"] == 80
    frozen = campaign["study"] / "frozen"
    assert not (frozen / "m0").exists() and not (campaign["study"] / "runs.json").exists()
    with zipfile.ZipFile(frozen / "m1r2/source.zip") as archive:
        assert set(pilot.REQUIRED) <= set(archive.namelist())
        assert archive.read("agent4drone/uav_agent.py") == campaign["method"]["agent4drone/uav_agent.py"]
    assert "fixture-secret" not in "".join(p.read_text() for p in frozen.rglob("*.json"))
    assert pilot.load_study(campaign["study"]) == study


@pytest.mark.parametrize("kind", ["legacy", "missing_module", "common_drift", "unexpected_extra"])
def test_prepare_rejects_method_or_common_drift(campaign, monkeypatch, kind):
    if kind == "legacy":
        monkeypatch.setattr(pilot, "git_commit", lambda _: pilot.original.M0_COMMIT)
    elif kind == "missing_module":
        del campaign["method"][pilot.REQUIRED[0]]
    elif kind == "common_drift":
        campaign["common"]["scripts/reproduction.py"] = b"changed"
    else:
        campaign["common"]["server/new_checker.py"] = b"unexpected"
    with pytest.raises(ValueError):
        pilot.prepare(COMMIT, campaign["study"])
    assert not (campaign["study"] / "frozen").exists()


def test_freeze_never_overwrites_existing_study(campaign):
    ready(campaign)
    with pytest.raises(ValueError, match="already frozen"):
        pilot.prepare(COMMIT, campaign["study"])


@pytest.mark.parametrize("changed_after_review", [False, True])
def test_only_exact_reviewed_resource_seal_fix_is_allowed(campaign, changed_after_review):
    name = "scripts/reproduction_metrics.py"
    content = (REAL_ROOT / name).read_bytes()
    assert pilot.r.digest(content) == pilot.APPROVED_COMMON[name]
    campaign["prior"]["common_source_sha256"][name] = "0" * 64
    campaign["common"][name] = content + (b"# unreviewed drift\n" if changed_after_review else b"")
    if changed_after_review:
        with pytest.raises(ValueError):
            pilot.prepare(COMMIT, campaign["study"])
    else:
        frozen = pilot.prepare(COMMIT, campaign["study"])
        assert frozen["approved_common_changes"][name]["current_sha256"] == pilot.APPROVED_COMMON[name]


@pytest.mark.parametrize("kind", ["arm", "mode", "budget", "source"])
def test_load_rejects_changed_identity_or_archive(campaign, kind):
    ready(campaign)
    path = campaign["study"] / "frozen/study.json"
    study = pilot.r.read_json(path)
    if kind == "arm":
        study["arm_order"].insert(0, "m0")
    elif kind == "mode":
        study["methods"]["m1r2"]["runtime_env"][pilot.MODE_VARIABLE] = "summary"
    elif kind == "budget":
        study["plan"]["protocol"]["timeout_seconds"] = 501
    else:
        (campaign["study"] / "frozen/m1r2/source.zip").write_bytes(b"changed")
    pilot.r.save_json(path, study)
    with pytest.raises(ValueError):
        pilot.load_study(campaign["study"])


def test_launch_uses_frozen_revision_only_without_new_m0(campaign, monkeypatch):
    ready(campaign)
    monkeypatch.setenv(pilot.MODE_VARIABLE, "summary")
    run, captured = attempt(campaign, monkeypatch)
    assert captured["argv"][-2] == "_execute"
    assert captured["env"][pilot.MODE_VARIABLE] == "evidence_r2"
    assert captured["env"]["UAV_MODEL_REQUEST_AUDIT"] == "1"
    assert "fixture-secret" not in str(captured["argv"])
    assert set(pilot.r.read_json(campaign["study"] / "runs.json")) == {"m1r2"}
    pilot.assert_method_identity(run)
    assert (run / "historical-m0.json").exists()
    with pytest.raises(ValueError, match="first attempt"):
        pilot.run(campaign["study"], COMMIT)


def test_launch_requires_matching_preflight_and_no_common_drift(campaign, monkeypatch):
    ready(campaign)
    path = campaign["study"] / "preflight.json"
    value = pilot.r.read_json(path)
    value["study_sha256"] = "different"
    pilot.r.save_json(path, value)
    with pytest.raises(ValueError, match="preflight"):
        attempt(campaign, monkeypatch)
    assert not (campaign["study"] / "runs.json").exists()
    value["study_sha256"] = pilot.r.digest((campaign["study"] / "frozen/study.json").read_bytes())
    pilot.r.save_json(path, value)
    campaign["common"]["scripts/reproduction.py"] = b"changed"
    with pytest.raises(ValueError, match="changed after"):
        attempt(campaign, monkeypatch)


def test_global_lock_and_internal_entry_protect_against_overlap(campaign, monkeypatch):
    ready(campaign)
    with pilot.rp.runner_lock(pilot.r):
        with pytest.raises(ValueError, match="Another experiment runner"):
            attempt(campaign, monkeypatch)
    with pytest.raises(ValueError, match="archived runtime"):
        pilot.execute(campaign["workspace"])


def test_method_environment_restored_even_on_exception(monkeypatch):
    monkeypatch.setenv(pilot.MODE_VARIABLE, "summary")
    monkeypatch.delenv("UAV_MODEL_REQUEST_AUDIT", raising=False)
    with pytest.raises(RuntimeError):
        with pilot.method_environment():
            assert os.environ[pilot.MODE_VARIABLE] == "evidence_r2"
            assert os.environ["UAV_MODEL_REQUEST_AUDIT"] == "1"
            raise RuntimeError("fixture")
    assert os.environ[pilot.MODE_VARIABLE] == "summary" and "UAV_MODEL_REQUEST_AUDIT" not in os.environ


def test_historical_pairing_keeps_all_tasks_and_rejects_unscored(campaign, monkeypatch):
    ready(campaign)
    run, _ = attempt(campaign, monkeypatch)
    rows = pilot.r.read_json(run / "historical-m0.json")["tasks"]
    for row in rows:
        row["status"] = "scored"
    loser = next(r for r in rows if r["passed"])
    winners = [r for r in rows if not r["passed"]][:3]
    loser["passed"] = False
    for winner in winners:
        winner["passed"] = True
    result = pilot.historical_comparison(run, rows)
    assert result["m0_fixed_passed"] == 80 and result["m1r2_passed"] == 82
    assert result["paired"]["W"] == 3 and result["paired"]["L"] == 1 and result["net_tasks"] == 2
    assert result["kind"] == "historical_reference_not_contemporaneous_control"
    rows[-1]["status"] = "unscored"
    with pytest.raises(ValueError, match="unscored"):
        pilot.historical_comparison(run, rows)


def test_preflight_never_executes_run_or_dispatches_model(campaign, monkeypatch):
    ready(campaign)
    calls = []
    def local_test(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "42 passed\n", "")
    monkeypatch.setattr(pilot.subprocess, "run", local_test)
    result = pilot.preflight(campaign["study"])
    assert result["paid_model_calls"] == 0 and result["network_connections_forbidden"]
    assert len(calls) == 1 and "network forbidden" in calls[0][0][-1]
    assert "pytest.main" in calls[0][0][-1]
    assert calls[0][1]["env"][pilot.MODE_VARIABLE] == "evidence_r2"
    assert not (campaign["study"] / "runs.json").exists()


def test_detached_owner_has_exactly_one_stage():
    request = {"arms": ["m1r2"], "study_directory": "/tmp/study", "method_commit": COMMIT}
    cmds = job.commands(request)
    assert len(cmds) == 1 and cmds[0][0] == "m1r2"
    assert "m0" not in cmds[0][1] and "summary" not in cmds[0][1]
    request["arms"] = ["m0", "m1r2"]
    with pytest.raises(ValueError, match="Only one"):
        job.commands(request)


@pytest.mark.parametrize("outcome", ["completed", "interrupted", "audit_failed"])
def test_archived_executor_finalization_without_restarting(campaign, monkeypatch, outcome):
    ready(campaign)
    run, _ = attempt(campaign, monkeypatch)
    runtime = run / "runtime"
    monkeypatch.setattr(pilot, "ROOT", runtime)
    monkeypatch.setattr(pilot.r, "ROOT", runtime)
    monkeypatch.setattr(pilot.r, "DATA", runtime / "data")
    monkeypatch.setattr(pilot.r, "load_plan", pilot.r.load_plan)
    monkeypatch.setattr(pilot.rp, "verify_provenance", lambda *a: None)
    monkeypatch.setattr(pilot.rp, "verify_scene", lambda *a: None)
    calls = []
    def coordinate(r, attempt, plan, settings, key, manifest, rows):
        calls.append(1)
        assert os.environ[pilot.MODE_VARIABLE] == "evidence_r2"
        assert os.environ["UAV_MODEL_REQUEST_AUDIT"] == "1"
        assert r.load_plan(plan["name"]) == plan
        assert key == "fixture-secret"
        manifest["status"] = "interrupted" if outcome == "interrupted" else "completed"
        saved = r.initial_results(plan)
        if outcome != "interrupted":
            for scene in plan["sessions"]:
                manifest["scenes"][scene["id"]].update(status="completed", generation=1)
            for row in saved:
                row.update(status="scored", task_pass=True, passed=True, world_quiescent=True,
                           continuation_safe=True, agent_timed_out=False)
                for leaf in row["leaves"]:
                    leaf.update(status="scored", result=True)
        r.save_json(attempt / "manifest.json", manifest)
        r.save_json(attempt / "results.json", saved)
        return outcome != "interrupted"
    monkeypatch.setattr(pilot.rp, "coordinate", coordinate)
    def audit(*a):
        if outcome == "audit_failed":
            raise ValueError("fixture activation failure")
        return {"traces": 100, "mode": "evidence_r2", "observer_errors": 0}
    monkeypatch.setattr(pilot, "audit_traces", audit)
    if outcome == "audit_failed":
        with pytest.raises(ValueError, match="activation failure"):
            pilot.execute(run)
    else:
        assert pilot.execute(run) is (outcome == "completed")
    record = pilot.r.read_json(campaign["study"] / "runs.json")["m1r2"]
    assert record["status"] == {"completed": "completed_and_verified", "interrupted": "interrupted",
                                "audit_failed": "verification_failed"}[outcome]
    assert calls == [1] and (run / "suite-seal.json").exists()
    if outcome == "completed":
        result = pilot.verify_saved(run)
        assert result["historical_comparison"]["m1r2_passed"] == 100
        assert result["historical_comparison"]["paired"]["W"] == 20


@pytest.mark.parametrize("mutation", ["none", "old_mode", "inactive", "observer_error", "missing_wire", "duplicate"])
def test_trace_audit_is_separate_from_task_grading(tmp_path, mutation):
    command = "Drone 1 visit Fixed Target 1"
    plan = {"sessions": [{"id": "s1", "tasks": [{"content_sha256": pilot.r.digest(command.encode())}]}]}
    trace = {"command": command, "runtime": {"task_progress_mode": "evidence_r2", "task_progress_active": True},
             "task_progress": {"revision": "m1-r2", "observer_errors": [], "mission_completion": "not_evaluated",
                               "task_evidence": {"model_requests": [{"request_index": 0}]}},
             "model_request_audit_errors": [], "model_requests_wire": [{"scope": "serialized_http_request_body"}]}
    if mutation == "old_mode":
        trace["runtime"]["task_progress_mode"] = "summary"
    elif mutation == "inactive":
        trace["runtime"]["task_progress_active"] = False
    elif mutation == "observer_error":
        trace["task_progress"]["observer_errors"] = ["fixture"]
    elif mutation == "missing_wire":
        trace["model_requests_wire"] = []
    path = tmp_path / "s1/agent-traces/toolchains/trace.json"
    pilot.r.save_json(path, trace)
    if mutation == "duplicate":
        pilot.r.save_json(path.with_name("duplicate.json"), trace)
    if mutation == "none":
        assert pilot.audit_traces(tmp_path, plan)["traces"] == 1
    else:
        with pytest.raises(ValueError):
            pilot.audit_traces(tmp_path, plan)
    assert pilot.r.read_json(path) == trace  # audit never changes task results
