"""Explicit v2 online wiring and same-API probes, exclusively local fixtures."""
import copy
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "agent4drone/tests")]
from harness_natural_runner import (
    ONLINE_CONTRACT, ONLINE_FAULT_CONTRACT, ONLINE_IMPLEMENTATION_BUDGET,
    EpisodeLifecycle, SuccessfulTakeoffReceiptLoss, admitted_client_class,
    assert_online_implementation_budget, online_arm_configuration, online_environment,
    public_execution_state, validate_online_fault,
)
from test_harness_online_arms import Model, arm
from test_task_progress_integration import call
from langchain_core.messages import AIMessage

RUNTIME = {"task_seconds": 500, "drain_seconds": 5.0, "request_limits": {
    "max_http_attempts": 12, "max_total_tokens": 200000, "max_output_tokens": 8192,
    "per_request_timeout_seconds": 60.0}}
PROBE = {"kind": "response_lost_after_success", "method": "POST",
         "endpoint": "/drones/drone-1/command/take_off", "occurrence": 1}
MARKER = "[Execution recovery contract: advisory public evidence, not task completion]"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Online-contract tests forbid all external network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def manifest():
    trials = [{**arm(name), "trial_id": name, "runtime": copy.deepcopy(RUNTIME)}
              for name in ("baseline", "integrated")]
    return {"online_pilot": {}, "model": "deepseek-flash", "online_contract": ONLINE_CONTRACT,
            "online_common_runtime": copy.deepcopy(RUNTIME), "trials": trials,
            "online_implementation_budget": copy.deepcopy(ONLINE_IMPLEMENTATION_BUDGET)}


@pytest.mark.parametrize("name", ["baseline", "integrated"])
def test_explicit_contract_selects_prompt_only_for_integrated_but_both_artifacts(name):
    value = manifest()
    trial = next(t for t in value["trials"] if t["arm"] == name)
    selected = online_arm_configuration(value, trial, lambda: None)
    assert selected["execution_contract"] == ONLINE_CONTRACT
    assert selected["execution_state_artifact"] is True
    assert selected["execution_state_prompt"] is (name == "integrated")
    env = online_environment(selected)
    assert env["UAV_TASK_EXECUTION_STATE"] == ("1" if name == "integrated" else "0")
    assert env["UAV_HARNESS_STATIC_OBSTACLES"] == "1"
    assert env["UAV_HARNESS_FIXED_K"] == "4"


@pytest.mark.parametrize("damage", [
    "missing_common", "missing_budget", "unknown_contract", "mixed_runtime", "false_runtime",
    "invalid_limit", "budget_drift", "empty_trials", "unlisted_trial", "fault_without_contract",
    "unknown_fault_contract", "legacy_fault", "contract_without_online",
])
def test_explicit_contract_rejects_drift_and_implicit_fault_enablement(damage):
    value = manifest()
    trial = value["trials"][0]
    if damage == "missing_common": value.pop("online_common_runtime")
    elif damage == "missing_budget": value.pop("online_implementation_budget")
    elif damage == "unknown_contract": value["online_contract"] = "future"
    elif damage == "mixed_runtime": value["trials"][1]["runtime"]["task_seconds"] = 499
    elif damage == "false_runtime": value["online_common_runtime"]["drain_seconds"] = True
    elif damage == "invalid_limit": value["online_common_runtime"]["request_limits"]["max_http_attempts"] = True
    elif damage == "budget_drift": value["online_implementation_budget"]["repair_budget"] = 15
    elif damage == "empty_trials": value["trials"] = []
    elif damage == "unlisted_trial": trial = {**trial, "trial_id": "elsewhere"}
    elif damage == "fault_without_contract": trial["api_fault"] = PROBE
    elif damage == "unknown_fault_contract": value["online_fault_contract"] = "unbounded"
    elif damage == "legacy_fault":
        value = {"online_pilot": {}, "model": "deepseek-flash"}
        trial["api_fault"] = PROBE
    else: value.pop("online_pilot")
    with pytest.raises(ValueError):
        online_arm_configuration(value, trial, lambda: None)


def test_legacy_online_environment_has_no_new_projection_or_budget_fields():
    value = online_arm_configuration({"online_pilot": {}, "model": "deepseek-flash"}, arm("integrated"), lambda: None)
    assert "execution_contract" not in value
    assert "UAV_TASK_EXECUTION_STATE" not in online_environment(value)


def test_legacy_local_zero_online_authorization_is_not_an_online_contract():
    assert online_arm_configuration({"online_authorized_executions": 0,
        "local_model": "local-scripted-sdk"}, {}, None) is None


def test_new_fault_accepted_only_with_frozen_explicit_contract():
    value = manifest()
    value["online_fault_contract"] = ONLINE_FAULT_CONTRACT
    for trial in value["trials"]:
        trial["api_fault"] = copy.deepcopy(PROBE)
        online_arm_configuration(value, trial, lambda: None)


def test_actual_implementation_budget_checked_independently():
    from harness_v2_online import BUDGET
    from harness_core import DecisionFrame
    from harness_skills import MAX_ROUNDS
    kwargs = {"api_calls_per_plan": DecisionFrame.__dataclass_fields__["api_budget"].default,
              "repair_budget": DecisionFrame.__dataclass_fields__["repair_budget"].default,
              "search_rounds": MAX_ROUNDS}
    assert_online_implementation_budget(BUDGET, **kwargs)
    for key in kwargs:
        changed = {**kwargs, key: kwargs[key] - 1}
        with pytest.raises(ValueError, match="budget drift"):
            assert_online_implementation_budget(BUDGET, **changed)


@pytest.mark.parametrize("changes", [
    {"kind": "response_lost"}, {"method": "GET"}, {"endpoint": "/drones/.*/command/take_off"},
    {"endpoint": "/drones/drone-1/command/rotate"}, {"endpoint": "/drones/drone-1/command/take_off?x=1"},
    {"occurrence": True}, {"occurrence": 2}, {"private": True},
])
def test_fault_is_exact_endpoint_one_success_and_no_unknown_fields(changes):
    with pytest.raises(ValueError):
        validate_online_fault({**PROBE, **changes})


def world_record(response=None):
    return {"method": "POST", "endpoint": PROBE["endpoint"], "phase": "task", "committed": True,
            "admin": False, "action": True, "request_id": 7, "origin": {"kind": "model_tool", "tool_call_id": "take"},
            "response": response or {"status": "success"},
            "history_events": [{"actor": "drone-1", "history": {"event": "take_off", "position": {"z": 25}}}]}


@pytest.mark.parametrize("response", [
    None, {}, {"status": "partial_success"}, {"status": "error"}, {"status": "success", "success": False},
    {"status": "success", "partial_success": True}, {"status": "success", "error": "hidden failure"},
    {"status": "success", "unsuccessful_points_count": 1}, {"outcome": "completed"},
])
def test_fault_does_not_misread_partial_failed_or_unknown_as_applied(tmp_path, response):
    probe = SuccessfulTakeoffReceiptLoss(PROBE, tmp_path / "private.json")
    probe.after_response("POST", PROBE["endpoint"], response, world_record())
    assert probe.successful_submissions == 0 and probe.fault_record is None
    assert not probe.record_path.exists()


@pytest.mark.parametrize("changes", [{"committed": False}, {"admin": True}, {"phase": "preparation"},
    {"endpoint": "/drones/other/command/take_off"}, {"history_events": []}])
def test_fault_requires_matching_real_committed_application(tmp_path, changes):
    probe = SuccessfulTakeoffReceiptLoss(PROBE, tmp_path / "private.json")
    with pytest.raises(ValueError):
        probe.after_response("POST", PROBE["endpoint"], {"status": "success"}, {**world_record(), **changes})
    assert probe.fault_record is None


def test_other_actor_or_method_not_counted_and_fault_occurs_once(tmp_path):
    probe = SuccessfulTakeoffReceiptLoss(PROBE, tmp_path / "private.json")
    response = {"status": "success"}
    probe.after_response("POST", "/drones/other/command/take_off", response, world_record())
    probe.after_response("GET", PROBE["endpoint"], response, world_record())
    assert probe.matched_returns == 0
    with pytest.raises(TimeoutError, match="Request timed out before response was received"):
        probe.after_response("POST", PROBE["endpoint"], response, world_record())
    probe.after_response("POST", PROBE["endpoint"], response, world_record())
    assert probe.successful_submissions == 2
    probe.save()  # Final worker accounting updates the already-durable trigger.
    saved = json.loads(probe.record_path.read_text())
    assert saved["successful_submissions"] == 2
    assert saved["fault_record"]["world_request_id"] == 7
    assert saved["fault_record"]["successful_submission_ordinal"] == 1


def test_legacy_local_probe_still_loses_first_return_even_if_failure(tmp_path):
    class Base:
        def _request(self, method, endpoint, **kwargs):
            return {"status": "error", "message": "legacy test refusal"}
    cls = admitted_client_class(Base, None, SimpleNamespace(check=lambda: None),
        local_fault={"kind": "response_lost", "command": "take_off", "occurrence": 1}, private=tmp_path)
    client = cls()
    with pytest.raises(TimeoutError):
        client._request("POST", PROBE["endpoint"])
    assert client._request("POST", PROBE["endpoint"])["status"] == "error"
    assert json.loads((tmp_path / "local-api-fault.json").read_text())["after_public_api_return"] is True


def test_baseline_execution_state_has_no_fabricated_evidence_or_completed_claim():
    state = public_execution_state(SimpleNamespace(_task_progress=None, _harness=None), "Original task", "cmd", {"closed": True})
    assert state["actions"] == state["tool_calls"] == state["frames"] == []
    assert state["mission_completion"] == "not_evaluated"
    assert state["sources"]["ledger_present"] is state["sources"]["harness_present"] is False
    assert state["obligations"]["unparsed_obligations"]["source_text"] == "Original task"


@pytest.mark.parametrize("path", ["baseline_raw", "integrated_raw", "integrated_skill"])
def test_real_graph_same_successful_api_fault_raw_and_skill_paths(tmp_path, monkeypatch, path):
    import uav_agent
    from harness_full100_online import agent_class
    from harness_full100_world import FullWorld
    from harness_v2_cases import build_case, checker
    from harness_v2_online import client_class
    enabled = path != "baseline_raw"
    case = build_case({"case_id": "valid_successful_checkpoint-01", "family": "valid_successful_checkpoint"})
    world = FullWorld(output=tmp_path)
    world.import_session(case["scene"])
    world.bake_timeline()
    selected = {"harness_enabled": enabled, "execution_contract": ONLINE_CONTRACT,
                "task_progress_mode": "evidence_harness" if enabled else "off",
                "harness_revision": "observed_target_v7" if enabled else "none"}
    for key, value in online_environment(selected).items(): monkeypatch.setenv(key, value)
    monkeypatch.setenv("UAV_MODEL_IO_ARCHIVE", "0")
    monkeypatch.setattr(uav_agent, "UAVAPIClient", uav_agent.UAVAPIClient)
    cls = agent_class(world, tmp_path, harness_enabled=enabled)
    probe = SuccessfulTakeoffReceiptLoss(PROBE, tmp_path / "private-probe.json")
    monkeypatch.setattr(uav_agent, "UAVAPIClient", admitted_client_class(
        client_class(world), world, EpisodeLifecycle(world), online_probe=probe))
    first = (call("run_harness", "start", action="start", skill="simple_chain")
             if path == "integrated_skill" else call("take_off", "take", drone_id="drone-1", altitude=25))
    replies = [first]
    if path != "integrated_skill": replies.append(call("get_drone_status", "read", drone_id="drone-1"))
    replies += [call("rotate", "rotate", drone_id="drone-1", heading=105),
                call("hover", "hover", drone_id="drone-1"), AIMessage(content="Done [TASK DONE]")]
    model = Model(replies=replies)
    monkeypatch.setattr(cls, "_create_llm", lambda *args, **kwargs: model)
    world.start(timer=False)
    agent = cls(base_url="http://testserver", llm_provider="offline", llm_model="scripted",
        verbose=False, share_blackboard_by_session=False, task_progress_mode=selected["task_progress_mode"])
    result = agent.execute(case["command"], step_callback=lambda steps: None)
    assert result["success"] and checker(world, case)["passed"]
    actions = [r for r in world.requests if r["phase"] == "task" and r["action"]]
    takeoffs = [r for r in actions if r["endpoint"] == PROBE["endpoint"]]
    assert len(takeoffs) == 1 and takeoffs[0]["committed"]
    assert takeoffs[0]["response"]["status"] == "success"
    assert probe.fault_record["world_request_id"] == takeoffs[0]["request_id"]
    assert probe.fault_record["origin"]["kind"] == ("skill" if path == "integrated_skill" else "model_tool")
    dumps = [json.dumps([m.model_dump() for m in request], default=str) for request in model.requests]
    assert (MARKER in dumps[0]) is enabled
    assert "unknown_effect" in dumps[1] if enabled else MARKER not in "".join(dumps)
    for forbidden in ("synthetic_fault", "actual_receipt", "successful_submission_ordinal", ONLINE_FAULT_CONTRACT):
        assert forbidden not in "".join(dumps)
    command_id = agent._task_progress.command_id if enabled else "baseline-public-command"
    state = public_execution_state(agent, case["command"], command_id, {"closed": True})
    if enabled:
        takeoff = next(a for a in state["actions"] if a["tool_name"] == "take_off")
        assert takeoff["effect_status"] == "unknown"
        assert takeoff["applied"] is None and takeoff["remaining"] is None
    else:
        assert state["actions"] == [] and state["sources"]["ledger_present"] is False
    world.freeze_score()
