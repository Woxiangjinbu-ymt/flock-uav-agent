"""Explicit hover-pair wiring, sealed derivatives and actual local SDK wires."""
import asyncio
import copy
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "agent4drone/tests")]
import harness_natural_runner as runner
import harness_deepseek_adapter as sdk
from test_harness_online_contract import RUNTIME, manifest as legacy_manifest
from test_harness_online_cost_variants import manifest as cost_manifest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Hover-variant tests forbid all provider/network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def manifest():
    trials = []
    for index, (condition, variant) in enumerate(runner.ONLINE_HOVER_FIXTURES.items()):
        for arm in (("control", "candidate") if index % 2 == 0 else ("candidate", "control")):
            mode, revision, layout, completion, inputs = runner.ONLINE_HOVER_VARIANTS[arm]
            trials.append({"trial_id": condition + "-" + arm, "task_id": "c74d7020", "condition": condition,
                "arm": arm, "task_progress_mode": mode, "harness_revision": revision,
                "input_layout": layout, "completion_contract": completion, "tool_input_contract": inputs,
                "fixture_variant": variant, "scheduler": "applicability", "runtime": copy.deepcopy(RUNTIME)})
    return {"scope": runner.ONLINE_HOVER_VARIANT_SCOPE, "online_pilot": {}, "model": "deepseek-flash",
        "online_contract": runner.ONLINE_CONTRACT, "online_variant_contract": runner.ONLINE_HOVER_VARIANT_CONTRACT,
        "online_common_runtime": copy.deepcopy(RUNTIME), "trials": trials,
        "online_implementation_budget": copy.deepcopy(runner.ONLINE_IMPLEMENTATION_BUDGET)}


@pytest.mark.parametrize("index", range(8))
def test_new_contract_selects_five_fields_and_matching_public_fixture(index):
    value = manifest(); trial = value["trials"][index]
    selected = runner.online_arm_configuration(value, trial, lambda: None)
    assert selected["harness_enabled"] is selected["execution_state_prompt"] is True
    assert selected["execution_state_artifact"] is True
    assert tuple(selected[k] for k in ("task_progress_mode", "harness_revision", "input_layout",
                                      "completion_contract", "tool_input_contract")) == runner.ONLINE_HOVER_VARIANTS[trial["arm"]]
    assert selected["fixture_variant"] == runner.ONLINE_HOVER_FIXTURES[trial["condition"]]
    env = runner.online_environment(selected)
    assert env["UAV_AGENT_INPUT_LAYOUT"] == "compact_tools_v1"
    assert env["UAV_HARNESS_REVISION"] == "search_confirm_v9"
    assert env["UAV_COMPLETION_CONTRACT"] == trial["completion_contract"]
    assert env["UAV_TOOL_INPUT_CONTRACT"] == trial["tool_input_contract"]
    assert runner.online_variant_agent_options(selected) == {k: trial[k] for k in (
        "input_layout", "completion_contract", "tool_input_contract")}


@pytest.mark.parametrize("field,bad", [
    ("task_progress_mode", "off"), ("harness_revision", "observed_target_v7"),
    ("input_layout", "full_v1"), ("completion_contract", "hover_completion_v1"),
    ("tool_input_contract", "json_object_or_string_v1"), ("fixture_variant", "explicit_hover"),
    ("condition", "arbitrary"), ("fixture_variant", "../outside"), ("scheduler", "every_step"),
])
@pytest.mark.parametrize("other_trial", [False, True])
def test_any_trial_selector_drift_rejected_before_model_construction(field, bad, other_trial):
    value = manifest(); selected = value["trials"][1 if other_trial else 0]
    value["trials"][0][field] = bad
    with pytest.raises(ValueError):
        runner.online_arm_configuration(value, selected, lambda: None)


@pytest.mark.parametrize("field", ["completion_contract", "tool_input_contract", "fixture_variant", "input_layout"])
def test_new_selector_must_be_explicit(field):
    value = manifest(); value["trials"][0].pop(field)
    with pytest.raises(ValueError):
        runner.online_arm_configuration(value, value["trials"][0], lambda: None)


@pytest.mark.parametrize("field,bad", [
    ("scope", runner.ONLINE_VARIANT_SCOPE), ("online_variant_contract", runner.ONLINE_VARIANT_CONTRACT),
    ("online_variant_contract", None), ("online_variant_contract", {}),
    ("online_contract", "future"), ("online_pilot", None),
])
def test_mismatched_contract_scope_or_online_authority_rejected(field, bad):
    value = manifest(); value[field] = bad
    with pytest.raises(ValueError):
        runner.online_arm_configuration(value, value["trials"][0], lambda: None)


@pytest.mark.parametrize("old_manifest", [legacy_manifest, cost_manifest])
@pytest.mark.parametrize("field", ["completion_contract", "tool_input_contract", "fixture_variant"])
@pytest.mark.parametrize("on_other_trial", [False, True])
def test_old_online_contracts_reject_new_selectors(old_manifest, field, on_other_trial):
    value = old_manifest(); selected = value["trials"][0]
    value["trials"][1 if on_other_trial else 0][field] = None
    with pytest.raises(ValueError):
        runner.online_arm_configuration(value, selected, lambda: None)


def test_old_options_and_environment_are_unchanged():
    for value in (legacy_manifest(), cost_manifest()):
        selected = runner.online_arm_configuration(value, value["trials"][0], lambda: None)
        env = runner.online_environment(selected)
        assert "UAV_COMPLETION_CONTRACT" not in env and "UAV_TOOL_INPUT_CONTRACT" not in env
        assert not {"completion_contract", "tool_input_contract"}.intersection(runner.online_variant_agent_options(selected))
    assert runner.online_variant_agent_options(None) == {}


def prepared_inputs(tmp_path, monkeypatch):
    run = tmp_path / "run"; (run / "source").mkdir(parents=True)
    monkeypatch.setattr(runner, "ROOT", run / "source")
    value = manifest(); value.update(study=str(run / "inputs"), sources={}, inputs={})
    for variant in ("original", "explicit_hover", "duration_hover"):
        prefix = "fixtures/" if variant == "original" else "variants/" + variant + "/fixtures/"
        blob, timeline = b"same inert original snapshot", b"same inert original timeline"
        command = "public instruction " + variant
        fixture = {"task_id": "c74d7020", "command": command, "command_sha256": runner.sha(command.encode()),
            "snapshot_sha256": runner.sha(blob), "timeline_file": "scene.timeline.pkl", "timeline_sha256": runner.sha(timeline)}
        for name, content in (("c74d7020.json", json.dumps(fixture).encode()), ("c74d7020.pkl", blob), ("scene.timeline.pkl", timeline)):
            path = run / "inputs" / prefix / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
            value["inputs"][prefix + name] = runner.sha(content)
    save_manifest(run, value)
    return run, value


def save_manifest(run, value):
    (run / "manifest.json").write_text(json.dumps(value))
    (run / "manifest.sha256").write_text(runner.sha((run / "manifest.json").read_bytes()))


@pytest.mark.parametrize("condition", ["state_hover", "explicit_hover", "unknown_takeoff", "duration_hover"])
def test_sealed_derivatives_keep_same_task_identity_and_select_only_declared_command(tmp_path, monkeypatch, condition):
    run, value = prepared_inputs(tmp_path, monkeypatch)
    trial = next(t for t in value["trials"] if t["condition"] == condition)
    _, chosen, fixture, blob, timeline = runner.read_inputs(run, trial["trial_id"])
    assert chosen == trial and fixture["task_id"] == "c74d7020"
    assert fixture["command"] == "public instruction " + trial["fixture_variant"]
    assert blob == b"same inert original snapshot" and timeline == b"same inert original timeline"


@pytest.mark.parametrize("damage", ["old_scope", "old_contract", "wrong_variant", "missing_seal", "tamper"])
def test_fixture_selector_cannot_escape_contract_or_sealed_inputs(tmp_path, monkeypatch, damage):
    run, value = prepared_inputs(tmp_path, monkeypatch)
    trial = next(t for t in value["trials"] if t["condition"] == "explicit_hover")
    key = "variants/explicit_hover/fixtures/c74d7020.pkl"
    if damage == "old_scope": value["scope"] = runner.ONLINE_VARIANT_SCOPE
    elif damage == "old_contract": value["online_variant_contract"] = runner.ONLINE_VARIANT_CONTRACT
    elif damage == "wrong_variant": trial["fixture_variant"] = "duration_hover"
    elif damage == "missing_seal": value["inputs"].pop(key)
    else: (run / "inputs" / key).write_bytes(b"changed")
    save_manifest(run, value)
    with pytest.raises(ValueError): runner.read_inputs(run, trial["trial_id"])


@pytest.mark.parametrize("arm", ["control", "candidate"])
@pytest.mark.parametrize("mode", ["state", "missing_status", "explicit", "duration", "invalid_recover", "unknown_raw", "unknown_skill"])
def test_actual_agent_ctor_sdk_schema_object_history_and_completion(tmp_path, monkeypatch, arm, mode):
    import uav_agent
    from harness_full100_online import agent_class
    from harness_full100_world import FullWorld
    from harness_v2_cases import build_case
    from harness_v2_online import client_class
    value = manifest(); trial = next(t for t in value["trials"] if t["arm"] == arm)
    selected = runner.online_arm_configuration(value, trial, lambda: None)
    case = build_case({"case_id": "valid_successful_checkpoint-01", "family": "valid_successful_checkpoint"})
    world = FullWorld(output=tmp_path); world.import_session(case["scene"]); world.bake_timeline()
    for key, content in runner.online_environment(selected).items(): monkeypatch.setenv(key, content)
    monkeypatch.setenv("UAV_MODEL_IO_ARCHIVE", "0")
    monkeypatch.setattr(uav_agent, "UAVAPIClient", uav_agent.UAVAPIClient)
    cls = agent_class(world, tmp_path, harness_enabled=True)
    if mode.startswith("unknown"):
        probe = runner.SuccessfulTakeoffReceiptLoss({"kind": "response_lost_after_success", "method": "POST",
            "endpoint": "/drones/drone-1/command/take_off", "occurrence": 1}, tmp_path / "probe.json")
        monkeypatch.setattr(uav_agent, "UAVAPIClient", runner.admitted_client_class(
            client_class(world), world, runner.EpisodeLifecycle(world), online_probe=probe))
    command = "Command drone Drone 1 to take off to an altitude of 25 meters and hover in place."
    if mode == "explicit": command = "Command drone Drone 1 to take off to an altitude of 25 meters and then execute a hover command."
    if mode == "duration": command = "Command drone Drone 1 to take off to an altitude of 25 meters and hover in place for 5 seconds."
    steps = [("list_drones", {})]
    if mode == "invalid_recover": steps.append(("take_off", {"input_json": []}))
    if mode == "unknown_skill": steps.append(("run_harness", {"input_json": {"action": "start", "skill": "simple_chain"}}))
    else: steps.append(("take_off", {"input_json": {"drone_id": "drone-1", "altitude": 25}}))
    if mode in {"explicit", "duration"}: steps.append(("hover", {"input_json": {"drone_id": "drone-1", **({"duration": 5} if mode == "duration" else {})}}))
    if mode != "missing_status": steps.append(("get_drone_status", {"input_json": {"drone_id": "drone-1"}}))
    requests, responses = [], []
    def respond(request):
        index = len(requests); assert index <= len(steps), "Unexpected retry or extra model request"
        requests.append(json.loads(request.content))
        message = {"role": "assistant", "content": "Done [TASK DONE]", "reasoning_content": "Synthetic local reasoning."}
        if index < len(steps):
            name, args = copy.deepcopy(steps[index])
            if arm == "control" and isinstance(args.get("input_json"), dict): args["input_json"] = json.dumps(args["input_json"])
            message.update(content="", tool_calls=[{"id": "local-" + str(index), "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}])
        responses.append(copy.deepcopy(message))
        return sdk.httpx.Response(200, json={"id": "response-" + str(index), "object": "chat.completion", "created": 1,
            "model": "deepseek-flash", "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if index < len(steps) else "stop"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}})
    model = sdk.make_local_deepseek_candidate(thinking="enabled", reasoning_effort="high", max_tokens=512,
                                             transport=sdk.httpx.MockTransport(respond))
    monkeypatch.setattr(cls, "_create_llm", lambda *args, **kwargs: model)
    # Constructor arguments must win over stale process settings in both arms.
    monkeypatch.setenv("UAV_COMPLETION_CONTRACT", "off" if arm == "candidate" else "hover_completion_v1")
    monkeypatch.setenv("UAV_TOOL_INPUT_CONTRACT", "string_v1" if arm == "candidate" else "json_object_or_string_v1")
    world.start(timer=False)
    try:
        agent = cls(base_url="http://testserver", llm_provider="offline", llm_model="scripted", verbose=False,
            share_blackboard_by_session=False, task_progress_mode=selected["task_progress_mode"],
            **runner.online_variant_agent_options(selected))
        result = agent.execute(command, step_callback=lambda _: None)
    finally:
        model.http_client.close(); asyncio.run(model.http_async_client.aclose())
    assert result["success"] is True and len(requests) == len(steps) + 1
    assert agent.completion_contract == selected["completion_contract"]
    assert agent.tool_input_contract == selected["tool_input_contract"]
    assert agent.input_layout == "compact_tools_v1" and agent._harness.revision == "search_confirm_v9"
    take_schema = next(t["function"]["parameters"]["properties"]["input_json"] for t in requests[0]["tools"] if t["function"]["name"] == "take_off")
    if arm == "candidate":
        assert {s["type"] for s in take_schema["anyOf"]} == {"string", "object"}
        expected = mode not in {"missing_status", "duration"}
        assert result["completion_assessment"]["completion_claim_accepted"] is expected
        assert result["task_completed"] is (True if expected else None)
        if not expected: assert "[TASK DONE]" not in result["output"] and "[TASK DONE]" in result["model_output"]
    else:
        assert take_schema["type"] == "string" and "completion_assessment" not in result
    prior_calls = [m for m in requests[-1]["messages"] if m["role"] == "assistant"]
    for original, restored in zip(responses[:-1], prior_calls):
        assert restored["tool_calls"] == original["tool_calls"]
        assert restored["reasoning_content"] == original["reasoning_content"]
    effects = [r for r in world.requests if r["phase"] == "task" and r["action"]]
    assert sum(r["endpoint"].endswith("/command/take_off") for r in effects) == 1
    if mode.startswith("unknown"):
        state = runner.public_execution_state(agent, command, agent._task_progress.command_id, {"closed": True})
        assert len(effects) == 1 and state["actions"][0]["effect_status"] == "unknown"
    if mode == "invalid_recover":
        errors = [m for m in requests[-1]["messages"] if m["role"] == "tool" and m.get("tool_call_id") == "local-1"]
        assert len(errors) == 1 and "Error" in errors[0]["content"]
    world.freeze_score()


@pytest.mark.parametrize("new_variant", [False, True])
def test_independent_scorer_calls_unified_helper_only_for_explicit_new_variant(tmp_path, monkeypatch, new_variant):
    import harness_full100_world
    run = tmp_path / "run"; ident = "trial"; folder = run / "trials" / ident
    (folder / "private").mkdir(parents=True); (run / "worker-exit-seals").mkdir()
    blob = b"inert sealed score snapshot"; (folder / "private/score-world.pkl").write_bytes(blob)
    tree = {"logic": "and", "checks": []}; expected = {**tree, "_episode_private_canary": "secret"}
    result = {"status": "completed", "trial_id": ident, "task_id": "c74d7020", "pid": 7,
        "snapshot": {"path": "private/score-world.pkl", "sha256": runner.sha(blob)}, "drained": True,
        "limits": {"needs_process_termination": False}, "files": {},
        "initial": {"original_checker_tree_sha256": runner.digest(tree)}}
    (folder / "worker-result.json").write_text(json.dumps(result)); (run / "worker-exit-seals/trial.json").write_text("{}")
    value = manifest() if new_variant else cost_manifest(); value["private_canary"] = "secret"
    trial = {"trial_id": ident, "task_id": "c74d7020"}; fixture = {"command": "public"}
    monkeypatch.setattr(runner, "read_inputs", lambda *args: (value, trial, fixture, None, None))
    monkeypatch.setattr(runner, "setup_paths", lambda *args: None)
    class World:
        def restore(self, *args): pass
        def request(self, *args, **kwargs): return {"execution_check_apis": expected}
        def evaluate(self, *args): return {"passed": True}
    monkeypatch.setattr(harness_full100_world, "FullWorld", World)
    calls = []
    def evaluate(*args): calls.append(args); return {"status": "unverified", "task_completed": None}
    monkeypatch.setitem(sys.modules, "hover_pilot_evaluation", SimpleNamespace(evaluate_snapshot=evaluate))
    report = runner.run_scorer(run, ident, network_attempts=[])
    assert len(calls) == int(new_variant)
    assert ("unified_completion" in report) is new_variant
    if new_variant:
        assert calls[0][1:] == (fixture, trial, run, folder)
        assert report["unified_completion"]["task_completed"] is None
    assert report["checker"] == {"passed": True} and report["network_attempts"] == []
