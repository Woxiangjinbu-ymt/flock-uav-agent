"""Bounded live cost-pair selectors; all tests use local scripted models."""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "agent4drone/tests")]
from harness_natural_runner import (
    ONLINE_CONTRACT, ONLINE_FAULT_CONTRACT, ONLINE_IMPLEMENTATION_BUDGET,
    ONLINE_VARIANT_CONTRACT, ONLINE_VARIANT_SCOPE, ONLINE_COST_VARIANTS,
    EpisodeLifecycle, SuccessfulTakeoffReceiptLoss, admitted_client_class,
    online_arm_configuration, online_environment, public_execution_state,
)
from test_harness_online_contract import RUNTIME, PROBE, MARKER
from test_harness_online_arms import Model, arm as legacy_arm
from test_task_progress_integration import call
from langchain_core.messages import AIMessage


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Cost-variant tests forbid all external network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def manifest():
    return {"scope": ONLINE_VARIANT_SCOPE, "online_pilot": {}, "model": "deepseek-flash",
        "online_contract": ONLINE_CONTRACT, "online_variant_contract": ONLINE_VARIANT_CONTRACT,
        "online_common_runtime": copy.deepcopy(RUNTIME),
        "online_implementation_budget": copy.deepcopy(ONLINE_IMPLEMENTATION_BUDGET),
        "trials": [{"trial_id": name, "arm": name, "task_progress_mode": mode,
                    "harness_revision": revision, "input_layout": layout,
                    "scheduler": "applicability", "runtime": copy.deepcopy(RUNTIME)}
                   for name, (mode, revision, layout) in ONLINE_COST_VARIANTS.items()]}


@pytest.mark.parametrize("name", ["control", "candidate"])
def test_both_variants_keep_harness_execution_state_and_explicit_layout(name):
    value = manifest()
    trial = next(t for t in value["trials"] if t["arm"] == name)
    selected = online_arm_configuration(value, trial, lambda: None)
    assert selected["harness_enabled"] is selected["execution_state_prompt"] is True
    assert selected["execution_state_artifact"] is True
    assert selected["input_layout"] == ONLINE_COST_VARIANTS[name][2]
    assert selected["harness_revision"] == ONLINE_COST_VARIANTS[name][1]
    assert selected["variant_contract"] == ONLINE_VARIANT_CONTRACT
    assert selected["implementation_budget"] == ONLINE_IMPLEMENTATION_BUDGET
    env = online_environment(selected)
    assert env["UAV_AGENT_INPUT_LAYOUT"] == selected["input_layout"]
    assert env["UAV_HARNESS_REVISION"] == selected["harness_revision"]
    assert env["UAV_TASK_EXECUTION_STATE"] == "1"


@pytest.mark.parametrize("damage", [
    "missing_variant", "null_variant", "wrong_variant", "missing_scope", "old_scope",
    "missing_execution_contract", "wrong_execution_contract", "missing_online",
    "local_profile", "baseline_arm", "integrated_arm", "wrong_mode", "mixed_revision",
    "stagnation_revision", "mixed_layout", "missing_layout", "invalid_layout",
    "wrong_scheduler", "other_trial_mixed", "other_trial_old_arm", "unlisted_trial",
    "mixed_runtime", "budget_drift",
])
def test_variant_requires_exact_explicit_contract_and_all_arm_bindings(damage):
    value = manifest()
    trial = value["trials"][0]
    if damage == "missing_variant": value.pop("online_variant_contract")
    elif damage == "null_variant": value["online_variant_contract"] = None
    elif damage == "wrong_variant": value["online_variant_contract"] = "automatic_latest"
    elif damage == "missing_scope": value.pop("scope")
    elif damage == "old_scope": value["scope"] = "deepseek_contract_pilot_v2"
    elif damage == "missing_execution_contract": value.pop("online_contract")
    elif damage == "wrong_execution_contract": value["online_contract"] = "future"
    elif damage == "missing_online": value.pop("online_pilot")
    elif damage == "local_profile": value["agent_profile"] = {}
    elif damage == "baseline_arm": trial["arm"] = "baseline"
    elif damage == "integrated_arm": trial["arm"] = "integrated"
    elif damage == "wrong_mode": trial["task_progress_mode"] = "off"
    elif damage == "mixed_revision": trial["harness_revision"] = "search_confirm_v9"
    elif damage == "stagnation_revision": trial["harness_revision"] = "search_progress_v8"
    elif damage == "mixed_layout": trial["input_layout"] = "compact_tools_v1"
    elif damage == "missing_layout": trial.pop("input_layout")
    elif damage == "invalid_layout": trial["input_layout"] = "anything"
    elif damage == "wrong_scheduler": trial["scheduler"] = "every_step"
    elif damage == "other_trial_mixed": value["trials"][1]["input_layout"] = "full_v1"
    elif damage == "other_trial_old_arm": value["trials"][1]["arm"] = "integrated"
    elif damage == "unlisted_trial": trial = {**trial, "trial_id": "outside"}
    elif damage == "mixed_runtime": value["trials"][1]["runtime"]["task_seconds"] = 501
    elif damage == "budget_drift": value["online_implementation_budget"]["search_rounds"] = 15
    with pytest.raises(ValueError):
        online_arm_configuration(value, trial, lambda: None)


@pytest.mark.parametrize("change", [
    {"arm": "control"}, {"arm": "candidate"}, {"harness_revision": "search_confirm_v9"},
    {"harness_revision": "search_progress_v8"}, {"input_layout": "compact_tools_v1"},
    {"input_layout": "full_v1"},
])
def test_legacy_online_contract_is_not_widened_by_new_variant_support(change):
    value = {"online_pilot": {}, "model": "deepseek-flash"}
    with pytest.raises(ValueError):
        online_arm_configuration(value, {**legacy_arm("integrated"), **change}, lambda: None)


@pytest.mark.parametrize("name", ["control", "candidate"])
@pytest.mark.parametrize("path", ["raw", "skill"])
def test_actual_graph_layout_preserves_unknown_effect_and_raw_continuation(tmp_path, monkeypatch, name, path):
    import uav_agent
    from harness_full100_online import agent_class
    from harness_full100_world import FullWorld
    from harness_v2_cases import build_case, checker
    from harness_v2_online import client_class
    value = manifest()
    value["online_fault_contract"] = ONLINE_FAULT_CONTRACT
    trial = next(t for t in value["trials"] if t["arm"] == name)
    trial["api_fault"] = copy.deepcopy(PROBE)
    selected = online_arm_configuration(value, trial, lambda: None)
    case = build_case({"case_id": "valid_successful_checkpoint-01", "family": "valid_successful_checkpoint"})
    world = FullWorld(output=tmp_path)
    world.import_session(case["scene"])
    world.bake_timeline()
    for key, content in online_environment(selected).items(): monkeypatch.setenv(key, content)
    monkeypatch.setenv("UAV_MODEL_IO_ARCHIVE", "0")
    monkeypatch.setattr(uav_agent, "UAVAPIClient", uav_agent.UAVAPIClient)
    cls = agent_class(world, tmp_path, harness_enabled=selected["harness_enabled"])
    probe = SuccessfulTakeoffReceiptLoss(PROBE, tmp_path / "private-probe.json")
    monkeypatch.setattr(uav_agent, "UAVAPIClient", admitted_client_class(
        client_class(world), world, EpisodeLifecycle(world), online_probe=probe))
    first = (call("run_harness", "start", action="start", skill="simple_chain") if path == "skill"
             else call("take_off", "take", drone_id="drone-1", altitude=25))
    model = Model(replies=[first, call("get_drone_status", "read", drone_id="drone-1"),
        call("rotate", "rotate", drone_id="drone-1", heading=105),
        call("hover", "hover", drone_id="drone-1"), AIMessage(content="Done [TASK DONE]")])
    monkeypatch.setattr(cls, "_create_llm", lambda *args, **kwargs: model)
    # Explicit construction must win over stale/inherited process layout.
    monkeypatch.setenv("UAV_AGENT_INPUT_LAYOUT", "compact_tools_v1" if name == "control" else "full_v1")
    world.start(timer=False)
    agent = cls(base_url="http://testserver", llm_provider="offline", llm_model="scripted",
        verbose=False, share_blackboard_by_session=False, task_progress_mode=selected["task_progress_mode"],
        input_layout=selected["input_layout"])
    result = agent.execute(case["command"], step_callback=lambda steps: None)
    assert result["success"] and checker(world, case)["passed"]
    assert agent.input_layout == selected["input_layout"]
    assert agent._harness.revision == selected["harness_revision"]
    assert agent._task_progress is not None
    actions = [r for r in world.requests if r["phase"] == "task" and r["action"]]
    takeoffs = [r for r in actions if r["endpoint"] == PROBE["endpoint"]]
    assert len(takeoffs) == 1 and takeoffs[0]["committed"]
    assert probe.fault_record["origin"]["kind"] == ("skill" if path == "skill" else "model_tool")
    dumps = [json.dumps([m.model_dump() for m in request], default=str) for request in model.requests]
    assert MARKER in dumps[0] and "unknown_effect" in dumps[1]
    prompt = str(model.requests[0][0].content)
    catalog = prompt.split("AVAILABLE TOOLS:\n", 1)[1].split("\n\nBe concise", 1)[0]
    assert bool("- list_drones:" in catalog) is (name == "control")
    assert len([line for line in catalog.splitlines() if line.startswith("- ")]) == len(agent.tools)
    assert all(tool.description for tool in agent.tools)
    for forbidden in ("synthetic_fault", "actual_receipt", "successful_submission_ordinal", ONLINE_FAULT_CONTRACT):
        assert forbidden not in "".join(dumps)
    state = public_execution_state(agent, case["command"], agent._task_progress.command_id, {"closed": True})
    takeoff = next(a for a in state["actions"] if a["tool_name"] == "take_off")
    assert takeoff["effect_status"] == "unknown" and takeoff["applied"] is None
    world.freeze_score()
