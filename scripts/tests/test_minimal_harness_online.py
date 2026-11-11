"""Offline launch guards and analysis semantics; never starts a model service."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import minimal_harness_online as launch

spec = importlib.util.spec_from_file_location("harness_online_analysis", ROOT / launch.ANALYSIS_REL)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


@pytest.mark.parametrize("status", ["prepared", "running", "interrupted", "awaiting_verification", "failed"])
def test_no_retry_or_skip_after_incomplete_arm(status):
    study = {"first_exploratory_arm_order": ["a", "b"]}
    with pytest.raises(ValueError, match="no retry"):
        launch.next_arm(study, {"a": {"status": status}})


def test_fixed_order_and_terminal_campaign():
    study = {"first_exploratory_arm_order": ["a", "b"]}
    assert launch.next_arm(study, {}) == "a"
    assert launch.next_arm(study, {"a": {"status": "completed_and_verified"}}) == "b"
    assert launch.next_arm(study, {a: {"status": "completed_and_verified"} for a in ("a", "b")}) is None


def test_environment_does_not_leak_scheduler_into_r2(monkeypatch):
    monkeypatch.setenv("UAV_HARNESS_SCHEDULER", "failure")
    study = {"arms": {"r2": {"UAV_TASK_PROGRESS_MODE": "evidence_r2"},
                      "h": {"UAV_TASK_PROGRESS_MODE": "evidence_harness", "UAV_HARNESS_SCHEDULER": "fixed"}}}
    assert "UAV_HARNESS_SCHEDULER" not in launch.clean_environment(study, "r2")
    assert launch.clean_environment(study, "h")["UAV_HARNESS_SCHEDULER"] == "fixed"


def test_attempt_only_adds_operational_metadata(tmp_path):
    frozen = {"name": "test", "expected_tasks": 100, "protocol": {"timeout_seconds": 500},
              "sessions": [{"id": "scene"}], "study": {"arm": "a", "runtime_env": {"mode": "a"}}}
    launch.save(tmp_path / "frozen/plans/a.json", frozen)
    actual = launch.planned_attempt(ROOT, tmp_path, "a", "hash")
    assert {k: v for k, v in actual.items() if k != "study"} == {k: v for k, v in frozen.items() if k != "study"}
    assert actual["study"]["runtime_env"] == frozen["study"]["runtime_env"]
    assert launch.read(tmp_path / "frozen/plans/a.json") == frozen


def test_logical_calls_retries_and_cutoff_are_separate():
    events = [{"event": "llm_start", "run_id": "a", "timestamp": 100},
              {"event": "llm_start", "run_id": "a", "timestamp": 100},
              {"event": "http_start", "timestamp": 101}, {"event": "http_start", "timestamp": 105},
              {"event": "llm_start", "run_id": "b", "timestamp": 601},
              {"event": "llm_end", "run_id": "a", "timestamp": 110, "duration_seconds": 10}]
    result = analysis.task_metrics(events, {"opened_at": 100, "cutoff_at": 600}, {"closed_at": 601, "frozen_at": 604})
    assert result["logical_model_requests_full_trace"] == 2
    assert result["logical_model_requests_in_window"] == 1
    assert result["http_attempts_in_window"] == 2
    assert result["scoring_ready_seconds"] == 504
    assert result["admission_window_seconds"] == 500
    assert analysis.task_metrics([], None, None)["logical_model_requests_full_trace"] is None


def test_resume_and_raw_takeover_are_observable_not_automatic_success():
    trace = {"harness": {"frames": [{"status": "needs_replan", "reason": "budget"}], "events": [
        {"kind": "model_control_tool", "args": {"action": "start", "skill": "public_search"}},
        {"kind": "plan_commit"}, {"kind": "skill_exit", "status": "paused", "plan_id": "p1"},
        {"kind": "model_control_tool", "args": {"action": "resume", "plan_id": "p1"}},
        {"kind": "skill_enter", "plan_id": "p1"},
        {"kind": "skill_exit", "status": "needs_replan", "plan_id": "p1", "reason": "budget"},
        {"kind": "model_request_start"}, {"kind": "external_tool_start", "action": True, "tool": "move_to"}]}}
    result = analysis.skill_audit(trace, "public_search")
    assert result["matching_grammar_start_attempts"] == 1
    assert result["paused_plans_reentered"] == 1
    assert result["handoffs"][0]["raw_actions_after"] == 1
    assert result["final_plan_statuses"] == {"needs_replan": 1}
    assert analysis.skill_audit(trace, "unsupported")["mismatched_or_unsupported_start_attempts"] == 1


def test_complete_100_pairing_and_missing_time_not_zero():
    rows = [{"session_id": "s", "task_id": str(i), "task_pass": i < 60, "eligibility": "unsupported"} for i in range(100)]
    a = {"arm": "a", "tasks": rows, "passed": 60}
    b = copy.deepcopy(a)
    b.update(arm="b", passed=61)
    b["tasks"][60]["task_pass"] = True
    result = analysis.compare(a, b)
    assert result["counts"]["b_only_pass"] == 1
    assert result["a_minus_b_metric_totals_complete_100_only"]["scoring_ready_seconds"] is None
    b["tasks"].pop()
    with pytest.raises(ValueError, match="complete same-100"):
        analysis.compare(a, b)
