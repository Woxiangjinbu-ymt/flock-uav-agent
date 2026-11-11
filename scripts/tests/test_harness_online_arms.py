"""Same-protocol baseline/integrated adapters; no real provider or credentials."""
import copy
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "agent4drone/tests"))
from harness_natural_runner import online_arm_configuration, public_task_state
from harness_full100_online import agent_class
from harness_full100_world import FullWorld
from harness_v2_cases import build_case, checker
from test_task_progress_integration import ScriptedModel, call
from langchain_core.messages import AIMessage


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Online-arm tests forbid all external network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def arm(name):
    return {"arm": name, "task_progress_mode": "off" if name == "baseline" else "evidence_harness",
        "harness_revision": "none" if name == "baseline" else "observed_target_v7", "scheduler": "applicability"}


@pytest.mark.parametrize("name", ["baseline", "integrated"])
def test_online_arm_requires_explicit_matching_selectors_and_factory(name):
    manifest = {"online_pilot": {}, "model": "deepseek-flash"}
    result = online_arm_configuration(manifest, arm(name), lambda **kwargs: None)
    assert result["harness_enabled"] is (name == "integrated")
    assert result["model"] == "deepseek-flash"
    with pytest.raises(ValueError, match="model factory"):
        online_arm_configuration(manifest, arm(name), None)
    mixed = arm(name)
    mixed["task_progress_mode"] = "evidence_memory_v7_harness"
    with pytest.raises(ValueError, match="disagree"):
        online_arm_configuration(manifest, mixed, lambda: None)


@pytest.mark.parametrize("manifest", [
    {"online_pilot": None, "model": "deepseek-flash"},
    {"online_pilot": {}, "model": "deepseek-flash", "agent_profile": {}},
    {"online_pilot": {}},
    {"online_pilot": {}, "model": ""},
])
def test_incomplete_or_local_profile_online_configuration_is_rejected(manifest):
    with pytest.raises(ValueError):
        online_arm_configuration(manifest, arm("baseline"), lambda: None)


def test_legacy_local_adapter_does_not_infer_an_online_arm():
    assert online_arm_configuration({"local_model": "local-scripted-sdk"}, {}, None) is None


def test_baseline_state_preserves_original_unparsed_instruction_without_success_claim():
    command = "Take off, then satisfy an unparsed requirement."
    state = public_task_state(SimpleNamespace(_task_progress=None), command, "cmd")
    assert state["command"] == state["uninterpreted_requirements"] == command
    assert state["whole_task"] == "not_evaluated"
    assert state["evidence_enabled"] is False
    assert state["task_evidence"] == {"requirements": [], "aggregate_requirements": []}
    snapshot = {"command": command, "real_ledger": True}
    ledger = SimpleNamespace(snapshot=lambda: copy.deepcopy(snapshot))
    assert public_task_state(SimpleNamespace(_task_progress=ledger), command, "cmd") == snapshot


class Model(ScriptedModel):
    max_retries: int = 0


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_graph_keeps_raw_tool_provenance_and_same_budget_in_both_arms(tmp_path, monkeypatch, enabled):
    import uav_agent
    case = build_case({"case_id": "valid_successful_checkpoint-01", "family": "valid_successful_checkpoint"})
    world = FullWorld(output=tmp_path)
    world.import_session(case["scene"])
    world.bake_timeline()
    monkeypatch.setenv("UAV_HARNESS_SCHEDULER", "applicability")
    monkeypatch.setenv("UAV_HARNESS_REVISION", "observed_target_v7")
    monkeypatch.setenv("UAV_HARNESS_STATIC_OBSTACLES", "1")
    # Restore the global factory replacement at test teardown.
    monkeypatch.setattr(uav_agent, "UAVAPIClient", uav_agent.UAVAPIClient)
    cls = agent_class(world, tmp_path, harness_enabled=enabled)
    model = Model(replies=[call("take_off", "take", drone_id="drone-1", altitude=25),
        call("rotate", "rotate", drone_id="drone-1", heading=105),
        call("hover", "hover", drone_id="drone-1"), AIMessage(content="Done [TASK DONE]")])
    monkeypatch.setattr(cls, "_create_llm", lambda *args, **kwargs: model)
    world.start(timer=False)
    agent = cls(base_url="http://testserver", llm_provider="offline", llm_model="scripted",
        verbose=False, share_blackboard_by_session=False,
        task_progress_mode="evidence_harness" if enabled else "off")
    result = agent.execute(case["command"], step_callback=lambda steps: None)
    assert result["success"] and checker(world, case)["passed"]
    actions = [r for r in world.requests if r["phase"] == "task" and r["action"]]
    assert [r["origin"]["tool"] for r in actions] == ["take_off", "rotate", "hover"]
    assert all(r["origin"]["kind"] == "model_tool" and r["origin"]["tool_call_id"] for r in actions)
    assert agent.max_iterations == 600 and agent._recursion_limit_for_steps() == 1800
    assert agent.max_empty_response_retries == model.max_retries == 0
    assert agent.client._harness_deadline == world.started + world.deadline_seconds
    names = {tool.name for tool in agent.tools}
    if enabled:
        assert "run_harness" in names
        assert agent._task_progress is not None and not agent._harness.frames
    else:
        assert "run_harness" not in names
        assert agent._task_progress is None and agent._harness is None
        assert "Minimal committed-plan harness" not in str(model.requests[0])
    world.freeze_score()
