"""Synthetic, offline evidence only: no live attempt, checker service or model."""
import csv
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "analyze_b1_remedies_pilot.py"
spec = importlib.util.spec_from_file_location("remedies_analysis", SOURCE)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def load(path):
    return json.loads(path.read_text())


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "ROOT", tmp_path)
    attempt, output = tmp_path / "attempt", tmp_path / "analysis-output"
    rows, current, ledger, sessions = [], [], [], []
    manifest = {"status": "completed", "agent_commit": analysis.METHOD_COMMIT, "scenes": {}}
    for scene_number, scene in enumerate(analysis.SCENES):
        tasks, old_performance, new_performance = [], {"by_job": {}}, {"by_job": {}}
        historical = Path("history") / scene
        directory = Path("scenes") / scene
        manifest["scenes"][scene] = {"directory": str(directory), "generation": 1}
        for index in range(20):
            number = 20 * scene_number + index
            task_id, old_job, new_job = f"task-{number}", f"old-{number}", f"new-{number}"
            task_pass = number < 47
            new_pass = task_pass if number not in (0, 47) else not task_pass
            count = 6 if number < 20 else 5
            tasks.append({"id": task_id})
            old_window = historical / f"{task_id}.window.json"
            old_barrier = historical / f"{task_id}.barrier.json"
            rows.append({"scene_id": scene, "scene_name": f"Scene {scene_number}", "family": "shared-family",
                         "task_index": index + 1, "task_id": task_id, "task_name": f"Task {number}",
                         "category": "Navigation" if number % 2 else "Search", "template": "Template",
                         "difficulty": "easy" if number < 20 else "hard", "task_pass": task_pass,
                         "deployment_pass": task_pass, "worker_confirmed": True, "timeout": False,
                         "provider_errors": [], "checks_count": count, "checks_passed": count if task_pass else count - 1,
                         "job_id": old_job, "evidence": {"window": str(old_window), "barrier": str(old_barrier)}})
            ledger.append({"model": "deepseek", "cohort": "first_attempt_no_replay", "generation": 1,
                           "session_id": scene, "task_id": task_id, "job_id": old_job, "original_status": "scored",
                           "task_pass": task_pass, "deployment_pass": task_pass, "agent_success": True,
                           "evidence_dir": str(historical), "scoring_ready_seconds": 10 + number,
                           "observed_usage": {"total_tokens": 10} if number == 2 else {}})
            leaves = [{"type": "leaf", "result": new_pass or leaf > 0, "actual": new_pass or leaf > 0,
                       "endpoint": "/check/synthetic", "parameters": {"index": leaf},
                       "definition": {"endpoint": "/check/synthetic", "parameters": {"index": leaf}}}
                      for leaf in range(count)]
            current.append({"session_id": scene, "task_id": task_id, "status": "scored", "passed": new_pass,
                            "task_pass": new_pass, "expected_checks": count, "leaves": leaves, "job_id": new_job,
                            "agent_timed_out": number == 59, "agent_success": number != 1,
                            "observed_token_usage": {"total_tokens": 7} if number == 2 else {}})
            save(attempt / directory / f"{task_id}.checks.json", leaves)
            save(attempt / directory / f"{task_id}.agent-state.json",
                 {"job_id": new_job, "worker_finished": True, "busy": False, "job": {"job_id": new_job, "result": {"success": number != 1}}})
            for root, relative, duration in ((tmp_path, historical, 10 + number), (attempt, directory, 20 + number)):
                save(root / relative / f"{task_id}.window.json", {"lease": task_id, "opened_at": 100, "cutoff_at": 600})
                save(root / relative / f"{task_id}.barrier.json", {"lease": task_id, "quiescent": True,
                     "simulation_paused": True, "inflight": 0, "frozen_at": 100 + duration, "cutoff_at": 600})
            if number not in (2, 3):
                old_performance["by_job"][old_job] = {"llm": {"usage": {"total_tokens": 1000 + number, "calls_with_usage": 2}}}
                new_performance["by_job"][new_job] = {"llm": {"usage": {"total_tokens": 100 + number, "calls_with_usage": 1}}}
        save(tmp_path / historical / "performance.json", old_performance)
        save(attempt / directory / "performance.json", new_performance)
        sessions.append({"id": scene, "tasks": tasks})
    # Wrong-cohort data and attempt-wide aggregates must never supply a row's usage.
    ledger.append({**ledger[0], "cohort": "best_available", "generation": 2, "observed_usage": {"total_tokens": 999999}})
    save(tmp_path / "history/performance.json", {"by_job": {"old-0": {"llm": {"usage": {"total_tokens": 999999}}}}})
    save(tmp_path / "ledger.json", ledger)
    baseline = {"status": "frozen", "baseline_source": {"agent_commit": "1794e45e421fb5de03094f0b63f9ca95f86ab42f",
                "ledger": "ledger.json", "cohort": "first_attempt_no_replay", "generation": 1, "model": "deepseek-flash",
                "provider": "deepseek_official", "workers": 8, "attempt": "history"}, "rows": rows}
    save(attempt / "baseline.json", baseline)
    history_hashes = {str(path.relative_to(tmp_path)): analysis.sha(path.read_bytes()) for path in (tmp_path / "history").rglob("*.json")}
    history_hashes["ledger.json"] = analysis.sha((tmp_path / "ledger.json").read_bytes())
    save(attempt / "baseline-audit.json", {"readiness": {"ready": True}, "baseline_sha256": analysis.sha((attempt / "baseline.json").read_bytes()),
                                          "input_evidence_sha256": history_hashes})
    save(attempt / "plan.json", {"expected_tasks": 60, "expected_checks": 320, "sessions": sessions,
         "study": {"agent_commit": analysis.METHOD_COMMIT, "comparison_limitations": ["Historical B0 workers=8; current workers=2."],
                   "baseline_inputs": {name: analysis.sha((attempt / name).read_bytes()) for name in ("baseline.json", "baseline-audit.json")}}})
    save(attempt / "manifest.json", manifest)
    save(attempt / "results.json", current)
    save(attempt / "suite-seal.json", {"files": {str(path.relative_to(attempt)): analysis.sha(path.read_bytes())
                                                for path in attempt.rglob("*.json")}})
    monkeypatch.setattr(analysis, "verify_completed", lambda path: {"valid_complete_run": True})
    return attempt, output


def test_final_counts_original_categories_observations_and_hashes(fixture):
    attempt, output = fixture
    summary = analysis.analyze(attempt, output)
    assert summary["mode"] == "verified_final"
    assert summary["methods"]["b0"]["task_passes"] == summary["methods"]["b1"]["task_passes"] == 47
    assert summary["methods"]["b1"]["scored_checks"] == 320
    assert summary["paired_outcomes"] == {"regression": 1, "both_pass": 46, "new_pass": 1, "both_fail": 12}
    assert summary["methods"]["b1"]["false_completions"] == 13
    assert summary["methods"]["b1"]["external_pass_self_not_success"] == 1
    assert summary["methods"]["b1"]["timed_out_tasks"] == 1
    assert set(summary["by_category"]) == {"Navigation", "Search"}
    assert len(summary["by_family"]) == 1
    assert summary["methods"]["b0"]["window_seconds"]["median"] == 39.5
    assert summary["methods"]["b1"]["window_seconds"]["median"] == 49.5
    assert summary["methods"]["b1"]["usage"]["observed_tasks"] == 59
    assert summary["methods"]["b1"]["usage"]["total_tokens"] == sum(100 + i for i in range(60) if i not in (2, 3)) + 7
    pairs = list(csv.DictReader((output / "paired-tasks.csv").open()))
    assert len(pairs) == 60
    assert pairs[0]["b0_observed_total_tokens"] == "1000"
    assert pairs[2]["b0_observed_total_tokens"] == "10"
    assert pairs[3]["b1_observed_total_tokens"] == ""
    assert set(p.name for p in output.iterdir()) == {"REPORT_CN.md", "summary.json", "paired-tasks.csv", "input-sha256.json"}
    hashes = load(output / "input-sha256.json")["files"]
    for name, expected in hashes.items():
        assert analysis.sha((analysis.ROOT / name).read_bytes()) == expected
    assert "history/performance.json" not in hashes
    assert analysis.PACKAGE_LIMITATION in (output / "REPORT_CN.md").read_text()
    assert "两侧自判均采用评分时快照" in (output / "REPORT_CN.md").read_text()
    snapshot = {p.name: p.read_bytes() for p in output.iterdir()}
    analysis.analyze(attempt, output)
    assert snapshot == {p.name: p.read_bytes() for p in output.iterdir()}


def test_running_refuses_final_and_partial_keeps_all_sixty(fixture):
    attempt, output = fixture
    manifest = load(attempt / "manifest.json")
    manifest["status"] = "running"
    save(attempt / "manifest.json", manifest)
    current = load(attempt / "results.json")
    for row in current[4:]:
        row.update(status="not_run", passed=None, task_pass=None, job_id=None, agent_success=None, agent_timed_out=False)
        for suffix in ("checks", "window", "barrier", "agent-state"):
            (attempt / manifest["scenes"][row["session_id"]]["directory"] / f"{row['task_id']}.{suffix}.json").unlink()
    save(attempt / "results.json", current)
    with pytest.raises(ValueError, match="not completed"):
        analysis.analyze(attempt, output)
    assert not output.exists()
    summary = analysis.analyze(attempt, output, partial=True)
    assert not summary["is_final"]
    assert summary["methods"]["b1"]["final_task_pass_rate"] is None
    assert summary["methods"]["b1"]["planned_tasks"] == 60
    assert summary["common_scored_comparison"]["tasks"] == 4
    assert summary["paired_outcomes"]["unscored_or_invalid"] == 56
    assert len(list(csv.DictReader((output / "paired-tasks.csv").open()))) == 60


def test_failed_verification_never_becomes_final(fixture, monkeypatch):
    attempt, output = fixture
    def fail(_):
        raise ValueError("Sealed attempt files changed")
    monkeypatch.setattr(analysis, "verify_completed", fail)
    with pytest.raises(ValueError, match="Sealed"):
        analysis.analyze(attempt, output)
    assert not output.exists()
    summary = analysis.analyze(attempt, output, partial=True)
    assert summary["verification"]["status"] == "failed"
    assert summary["methods"]["b1"]["final_task_pass_rate"] is None
    assert "仅未完成/未核验诊断" in (output / "REPORT_CN.md").read_text()


@pytest.mark.parametrize("corruption", ["baseline", "history", "barrier", "leaf", "output_inside"])
def test_evidence_mismatch_is_not_silently_scored(fixture, corruption):
    attempt, output = fixture
    if corruption == "baseline":
        with (attempt / "baseline.json").open("a") as stream:
            stream.write(" ")
    elif corruption == "history":
        path = analysis.ROOT / "history" / analysis.SCENES[0] / "performance.json"
        data = load(path)
        data["by_job"]["old-0"]["llm"]["usage"]["total_tokens"] += 1
        save(path, data)
    elif corruption == "barrier":
        path = attempt / "scenes" / analysis.SCENES[0] / "task-0.barrier.json"
        data = load(path)
        data["cutoff_at"] += 1
        save(path, data)
    elif corruption == "leaf":
        path = attempt / "scenes" / analysis.SCENES[0] / "task-0.checks.json"
        data = load(path)
        data[0]["actual"] = not data[0]["actual"]
        save(path, data)
    else:
        output = attempt / "output"
    with pytest.raises(ValueError):
        analysis.analyze(attempt, output)
    assert not output.exists()


def test_verifier_launches_only_frozen_verify_with_bytecode_disabled(tmp_path):
    # A tiny local verifier process exercises the subprocess boundary without
    # importing any application service, contacting an API or starting a model.
    interpreter = tmp_path / "runtime/controller/.venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    verifier = tmp_path / "runtime/scripts/b1_remedies_pilot.py"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("import sys, json\nassert sys.dont_write_bytecode\nassert sys.argv[1:] == ['verify', "
                        + repr(str(tmp_path)) + "]\nprint(json.dumps({'valid_complete_run': True}))\n")
    assert analysis.verify_completed(tmp_path)["valid_complete_run"] is True
    assert not list(tmp_path.rglob("__pycache__"))


@pytest.mark.parametrize("index,scoring_success,expected_false_completion,expected_pass_without_self", [
    (0, False, False, False),  # Scored failure, but later worker says success.
    (1, True, False, False),   # Scored success, but later worker says failure.
    (2, None, False, True),    # No self-report at scoring time; later success cannot fill it.
])
def test_self_report_uses_scoring_snapshot_not_final_worker(fixture, index, scoring_success,
                                                         expected_false_completion, expected_pass_without_self):
    attempt, _ = fixture
    row = load(attempt / "results.json")[index]
    row["agent_success"] = scoring_success
    item = analysis.current_row(analysis.Inputs(), attempt / "scenes" / row["session_id"], row)
    assert item["agent_success"] is scoring_success
    assert item["false_completion"] is expected_false_completion
    assert item["external_pass_self_not_success"] is expected_pass_without_self
    assert item["worker_confirmed"] is True


@pytest.mark.parametrize("primary,fallback,expected_total,expected_prompt,expected_calls,source", [
    ({"total_tokens": None, "prompt_tokens": None, "completion_tokens": None, "calls_with_usage": 0},
     {"total_tokens": 42, "prompt_tokens": 30, "completion_tokens": 12, "calls_with_usage": 2},
     42, 30, 2, "fallback"),
    ({"total_tokens": None, "prompt_tokens": 30, "completion_tokens": None, "calls_with_usage": 1},
     {"total_tokens": 42, "prompt_tokens": 20, "completion_tokens": 22, "calls_with_usage": 2},
     None, 30, 1, "scene performance.json by_job"),
    ({"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "calls_with_usage": 1},
     {"total_tokens": 42, "calls_with_usage": 2}, 0, 0, 1, "scene performance.json by_job"),
    ({"total_tokens": None, "prompt_tokens": None, "completion_tokens": None, "calls_with_usage": 0},
     {"total_tokens": None, "calls_with_usage": 0}, None, None, None, None),
])
def test_null_token_shape_falls_back_but_partial_observations_do_not_mix(primary, fallback, expected_total,
                                                                      expected_prompt, expected_calls, source):
    result = analysis.observed_usage({"by_job": {"job": {"llm": {"usage": primary}}}}, "job", fallback, "fallback")
    assert result["observed_total_tokens"] == expected_total
    assert result["observed_prompt_tokens"] == expected_prompt
    assert result["calls_with_usage"] == expected_calls
    assert result["usage_source"] == source


@pytest.mark.parametrize("location", ["runs", "other_run", "baseline_root", "baseline_scene", "symlink"])
def test_output_cannot_overwrite_any_run_or_historical_baseline(fixture, location):
    attempt, _ = fixture
    paths = {"runs": analysis.ROOT / "experiments/runs",
             "other_run": analysis.ROOT / "experiments/runs/another-sealed-run",
             "baseline_root": analysis.ROOT / "history",
             "baseline_scene": analysis.ROOT / "history" / analysis.SCENES[0] / "analysis"}
    if location == "symlink":
        output = analysis.ROOT / "innocent-looking-output"
        output.symlink_to(analysis.ROOT / "history", target_is_directory=True)
    else:
        output = paths[location]
    sentinel = output / "summary.json"
    save(sentinel, {"preserve": "sealed evidence"})
    before = sentinel.read_bytes()
    with pytest.raises(ValueError, match="Analysis output cannot"):
        analysis.analyze(attempt, output, partial=True)
    assert sentinel.read_bytes() == before
    assert not (output / "REPORT_CN.md").exists()


def scene_snapshot_fixture(attempt):
    manifest = load(attempt / "manifest.json")
    manifest["status"] = "running"
    save(attempt / "manifest.json", manifest)
    rows = load(attempt / "results.json")
    for sid, record in manifest["scenes"].items():
        local = [{**row, "continuation_safe": True} for row in rows if row["session_id"] == sid]
        save(attempt / record["directory"] / "results.json", local)
    rows[1].update(status="running", task_pass=None, passed=None)
    save(attempt / "results.json", rows)
    return rows, manifest


def test_scene_snapshot_only_adds_existing_score_and_preserves_denominator(fixture):
    attempt, output = fixture
    scene_snapshot_fixture(attempt)
    before = {str(p): p.read_bytes() for p in attempt.rglob("*.json")}
    summary = analysis.analyze(attempt, output, partial=True, use_scene_snapshots=True)
    assert summary["methods"]["b1"]["valid_scored_tasks"] == 60
    assert summary["methods"]["b1"]["task_passes"] == 47
    assert summary["methods"]["b1"]["final_task_pass_rate"] is None
    assert not summary["is_final"]
    assert summary["snapshot_reconciliation"]["root_snapshot"] == {"scored_tasks": 59, "passed_tasks": 46}
    assert len(summary["snapshot_reconciliation"]["additional_saved_scores"]) == 1
    assert before == {str(p): p.read_bytes() for p in attempt.rglob("*.json")}
    assert len(list(csv.DictReader((output / "paired-tasks.csv").open()))) == 60


@pytest.mark.parametrize("corruption", ["conflict", "worker", "job", "not_partial"])
def test_scene_snapshot_rejects_conflict_or_missing_evidence(fixture, corruption):
    attempt, output = fixture
    rows, manifest = scene_snapshot_fixture(attempt)
    directory = attempt / manifest["scenes"][rows[1]["session_id"]]["directory"]
    if corruption == "worker":
        (directory / "task-1.agent-state.json").unlink()
    elif corruption in ("conflict", "job"):
        local = load(directory / "results.json")
        if corruption == "conflict":
            local[0]["task_pass"] = not local[0]["task_pass"]
        else:
            local[1]["job_id"] = "different-job"
        save(directory / "results.json", local)
    with pytest.raises(ValueError):
        analysis.analyze(attempt, output, partial=corruption != "not_partial", use_scene_snapshots=True)
    assert not output.exists()


@pytest.mark.parametrize("corruption", ["invalid", "provider_error", "check_parameters", "check_endpoint"])
def test_scene_snapshot_cannot_override_invalid_root_or_different_checker(fixture, corruption):
    attempt, output = fixture
    rows, manifest = scene_snapshot_fixture(attempt)
    if corruption in ("invalid", "provider_error"):
        if corruption == "invalid":
            rows[1]["status"] = "invalid"
        else:
            rows[1]["provider_errors"] = ["service failure"]
        save(attempt / "results.json", rows)
    else:
        directory = attempt / manifest["scenes"][rows[1]["session_id"]]["directory"]
        checks = load(directory / "task-1.checks.json")
        if corruption == "check_parameters":
            checks[0]["parameters"]["index"] = 999
        else:
            checks[0]["endpoint"] = "/check/different"
        save(directory / "task-1.checks.json", checks)
    with pytest.raises(ValueError):
        analysis.analyze(attempt, output, partial=True, use_scene_snapshots=True)
    assert not output.exists()


def test_scene_snapshot_rejects_mismatched_internal_worker_job(fixture):
    attempt, output = fixture
    rows, manifest = scene_snapshot_fixture(attempt)
    path = attempt / manifest["scenes"][rows[1]["session_id"]]["directory"] / "task-1.agent-state.json"
    worker = load(path)
    worker["job"]["job_id"] = "wrong-internal-job"
    save(path, worker)
    with pytest.raises(ValueError, match="internal worker job"):
        analysis.analyze(attempt, output, partial=True, use_scene_snapshots=True)
    assert not output.exists()
