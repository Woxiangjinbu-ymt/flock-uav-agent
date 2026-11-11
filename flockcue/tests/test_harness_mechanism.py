"""Offline real-simulator integration: no provider/network credentials required."""
import copy
import json
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from harness_v2_world import World, digest
from harness_v2_cases import build_case, checker
import harness_v2_online as online

sys.path.insert(0, str(ROOT / "agent4drone/tests"))
from test_task_progress_integration import ScriptedModel, call
from langchain_core.messages import AIMessage


def case(family, number=1):
    return build_case({"case_id": f"{family}-{number:02}", "family": family})


class Model(ScriptedModel):
    max_retries: int = 0


def agent(monkeypatch, tmp_path, c, arm, replies):
    w = World()
    w.import_session(c["scene"])
    w.bake_timeline()
    monkeypatch.setenv("UAV_HARNESS_SCHEDULER", arm)
    cls = online.agent_class(w, c, tmp_path)
    model = Model(replies=replies)
    monkeypatch.setattr(cls, "_create_llm", lambda *a, **k: model)
    a = cls(base_url="http://testserver", llm_provider="offline", llm_model="scripted",
            verbose=False, task_progress_mode="evidence_harness")
    return w, a


def test_complete_restore_includes_private_history_timers_rng_and_controllers():
    w = World()
    c = case("valid_successful_checkpoint")
    w.import_session(c["scene"])
    w.request("POST", "/drones/drone-1/command/take_off", params={"altitude": 25})
    blob, expected = w.snapshot(), w.state_digest()
    w.request("POST", "/drones/drone-1/command/rotate", params={"heading": 90})
    w.server.drone_controller.drones["drone-1"].history_status.clear()
    w.server.session_controller.current_session_id = None
    w.rng.random(); w.id_rng.random(); w.clock.relative = 900
    w.restore(blob)
    assert w.state_digest() == expected
    assert w.server.drone_controller.session_controller is w.server.session_controller
    assert w.server.drone_controller.obstacle_controller is w.server.obstacle_controller
    assert w.server.drone_controller.drones["drone-1"].history_status[0]["event"] == "take_off"
    with pytest.raises(ValueError, match="checksum"):
        w.restore(blob, "wrong")


def test_moving_phase_and_weather_timeline_replay_is_independent_of_request_order():
    from models.target import Target, TargetType
    w = World()
    c = case("valid_successful_checkpoint")
    c["scene"]["targets"] = [Target("Moving", TargetType.MOVING,
        {"x": 500, "y": 500, "z": 0}, velocity={"x": 2, "y": 1, "z": 0},
        moving_duration=7, time_in_direction=3, target_id="moving").to_dict()]
    w.import_session(c["scene"])
    blob = w.snapshot()
    w.bake_timeline()
    w.advance(37.5)
    first = w.state_digest()
    moved = copy.deepcopy(w.server.target_controller.targets["moving"].position)
    assert moved != c["scene"]["targets"][0]["position"]
    w.restore(blob)
    for t in [1, 8, 12, 37.5]:
        w.advance(t)
    assert w.state_digest() == first


@pytest.mark.parametrize("applied", [True, False])
@pytest.mark.parametrize("arm", ["applicability", "failure"])
def test_real_takeover_after_unknown_receipt_requires_actual_goal(monkeypatch, tmp_path, applied, arm):
    c = case("unknown_effect_receipt", 1 if applied else 3)
    replies = ([] if applied else [call("take_off", "take", drone_id="drone-1", altitude=c["checker"]["altitude"])])
    replies += [call("rotate", "rot", drone_id="drone-1", heading=c["checker"]["heading"]),
                call("hover", "hover", drone_id="drone-1"), AIMessage(content="Done [TASK DONE]")]
    w, a = agent(monkeypatch, tmp_path, c, arm, replies)
    result = a.execute(c["command"], step_callback=lambda steps: None)
    assert result["success"], result
    assert a.bootstrap["status"] == "needs_replan"
    assert checker(w, c)["passed"]
    assert len([r for r in w.server.drone_controller.drones["drone-1"].history_status
                if r["event"] == "take_off"]) == 1
    assert a.execution_diagnostics["graph_error_type"] is None


def test_model_resume_uses_returned_plan_and_rejections_are_logged(monkeypatch, tmp_path):
    c = case("recoverable_blocked_candidate")
    w, a = agent(monkeypatch, tmp_path, c, "failure", [])
    # The command id is allocated by execute; script from the actual public result.
    original = a._initialize_task_progress
    def initialize(command, command_id):
        original(command, command_id)
        a.llm.replies = [call("run_harness", "invalid", action="resume", plan_id="unknown"),
            call("run_harness", "resume", action="resume", plan_id=a.bootstrap["plan_id"]),
            AIMessage(content="Done [TASK DONE]")]
    a._initialize_task_progress = initialize
    result = a.execute(c["command"], step_callback=lambda steps: None)
    assert result["success"], result
    assert checker(w, c)["passed"]
    records = [e for e in a._harness.snapshot()["events"] if e["kind"] == "control_result"]
    assert len(records) == 2 and "rejected" in records[0]["content"]
    assert "completed" in records[1]["content"]
    assert a.common_prefix_calls == a.bootstrap_calls


@pytest.mark.parametrize("steps,recursion,expected", [(2, 1800, "steps"), (600, 3, "graph")])
def test_loop_limits_preserve_partial_actions(monkeypatch, tmp_path, steps, recursion, expected):
    monkeypatch.setitem(online.BUDGET, "max_iterations", steps)
    monkeypatch.setitem(online.BUDGET, "recursion_limit", recursion)
    c = case("valid_successful_checkpoint")
    w, a = agent(monkeypatch, tmp_path, c, "applicability", [
        call("get_drone_status", f"s{i}", drone_id="drone-1") for i in range(10)])
    a.execute(c["command"], step_callback=lambda steps: None)
    d = a.execution_diagnostics
    assert d["tool_steps"] > 0
    if expected == "steps":
        assert d["stopped_due_to_step_limit"] and d["tool_steps"] == 2
    else:
        assert d["graph_error_type"] == "GraphRecursionError"
    assert d["budget"]["max_iterations"] == steps and d["budget"]["recursion_limit"] == recursion


def test_deadline_blocks_public_mutation(monkeypatch, tmp_path):
    w = World(); c = case("valid_successful_checkpoint"); w.import_session(c["scene"])
    client = online.client_class(w)(base_url="http://testserver")
    before = w.state_digest(); client._harness_deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError):
        client.take_off("drone-1", 20)
    assert w.state_digest() == before


def test_original_task_checker_matches_after_full_restore():
    # Existing server task checker, not the controlled fixture's custom checker.
    from models.task import Task
    w = World(); c = case("valid_successful_checkpoint"); w.import_session(c["scene"])
    sc = w.server.session_controller
    task = Task(task_id="checker-parity", content=c["command"], execution_check_apis={
        "logic": "and", "checks": [{"endpoint": "/check/drone_altitude",
                                      "parameters": {"drone_id": "drone-1", "expected_altitude": 25, "tolerance": 0.01}}]})
    sc.get_current_session_ref().tasks[task.id] = task.to_dict()
    blob = w.snapshot()
    before = w.request("GET", f"/sessions/current/tasks/{task.id}/check", admin=True)
    assert before["result"] is False
    w.request("POST", "/drones/drone-1/command/take_off", params={"altitude": 25})
    assert w.request("GET", f"/sessions/current/tasks/{task.id}/check", admin=True)["result"] is True
    w.restore(blob)
    after = w.request("GET", f"/sessions/current/tasks/{task.id}/check", admin=True)
    assert before == after
    assert "result" in before


def test_schedule_scope_and_tamper_guard(tmp_path):
    from harness_v2_campaign import verify
    from harness_v2_world import sha, write_json
    write_json(tmp_path / "schedule.json", {"executions": 96, "blocks": [{"stage": "main100"}] * 48})
    write_json(tmp_path / "manifest.json", {"files": {"schedule.json": sha((tmp_path / "schedule.json").read_bytes())}})
    with pytest.raises(ValueError, match="Unauthorized"):
        verify(tmp_path)
    (tmp_path / "schedule.json").write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        verify(tmp_path)


def test_real_frozen_worker_labels_cover_every_trial_once():
    from harness_v2_campaign import worker_ids
    workspace = ROOT
    schedule = workspace / "experiments/minimal-harness-v2/schedule.json"
    # Test also works in the source archive using a self-contained schedule copy.
    if not schedule.exists():
        pytest.skip("Workspace schedule audited separately before coordinator repair")
    plan = json.loads(schedule.read_text())
    actual = [f"{b['case_id']}-r{b['repeat']}-{t['arm']}"
              for repeat in (1, 2) for worker in worker_ids(plan)
              for b in plan["blocks"] if b["repeat"] == repeat and b["worker"] == worker
              for t in b["trials"]]
    expected = [f"{b['case_id']}-r{b['repeat']}-{t['arm']}" for b in plan["blocks"] for t in b["trials"]]
    assert len(actual) == len(set(actual)) == 96
    assert set(actual) == set(expected)
    assert worker_ids(plan) == [0, 1]


def test_continue_refuses_unfinished_or_failed_attempts(tmp_path):
    from harness_v2_campaign import campaign
    from harness_v2_world import sha, write_json
    blocks = [{"stage": "mechanism", "worker": i % 2} for i in range(48)]
    write_json(tmp_path / "schedule.json", {"executions": 96, "blocks": blocks})
    write_json(tmp_path / "manifest.json", {"files": {"schedule.json": sha((tmp_path / "schedule.json").read_bytes())}})
    write_json(tmp_path / "runs.json", {"a": {"status": "failed"}})
    with pytest.raises(ValueError, match="unfinished/failed"):
        campaign(tmp_path, ROOT, continue_unstarted=True)
