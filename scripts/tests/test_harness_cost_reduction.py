"""Adversarial checks for the fixed local comparison's evidence boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_harness_cost_reduction as cost


def history():
    raw = [{"type": "human", "content": "original complete command"},
           {"type": "ai", "content": "", "tool_calls": [{"id": "call-1", "name": "take_off", "args": {"input_json": "{}"}}]},
           {"type": "tool", "content": '{"status":"unknown"}', "tool_call_id": "call-1"}]
    wire = [{"role": "user", "content": raw[0]["content"]},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "take_off", "arguments": '{"input_json":"{}"}'}}]},
            {"role": "tool", "content": raw[2]["content"], "tool_call_id": "call-1"}]
    return raw, wire


def test_complete_history_kept_with_only_sdk_null_convention():
    raw, wire = history()
    before = deepcopy((raw, wire))
    assert cost.preserved_graph_messages(raw, wire, [])
    assert (raw, wire) == before


@pytest.mark.parametrize("mutation", [
    lambda w: w.pop(0),
    lambda w: w.append(deepcopy(w[-1])),
    lambda w: w[0].update(content="abbreviated command"),
    lambda w: w[2].update(content='{"status":"success"}'),
    lambda w: w[2].update(tool_call_id="call-2"),
    lambda w: w[1]["tool_calls"][0]["function"].update(arguments='{"input_json":"{\\"altitude\\":99}"}'),
    lambda w: w.reverse(),
    lambda w: w[1]["tool_calls"][0]["function"].update(name="hover"),
])
def test_no_history_cropping_relabeling_or_argument_mutation(mutation):
    raw, wire = history()
    mutation(wire)
    assert not cost.preserved_graph_messages(raw, wire, [])


def test_advisory_requires_exact_sealed_text_position_and_id():
    raw, wire = history()
    suffix = cost.MARKER + "public unknown effect; do not replay"
    wire[-1]["content"] += suffix
    injection = {"message_index": 2, "tool_call_id": "call-1", "text": suffix}
    assert cost.preserved_graph_messages(raw, wire, [injection])
    for changes in ({"message_index": 0}, {"tool_call_id": "call-2"}, {"text": suffix + "fake"}):
        assert not cost.preserved_graph_messages(raw, wire, [{**injection, **changes}])
    assert not cost.preserved_graph_messages(raw, wire, [])
    assert not cost.preserved_graph_messages(raw, wire, [injection, injection])


def pair():
    return ({"search": {"status": "needs_replan", "reason": "round_limit_below_threshold", "public_progress": 66},
             "physical_cost": {"action_attempts": 85, "api_requests": 345}},
            {"search": {"status": "needs_replan", "reason": "search_progress_stalled", "public_progress": 66},
             "physical_cost": {"action_attempts": 65, "api_requests": 266}})


def test_stalled_handoff_is_not_credited_as_task_completion():
    assert all(cost.search_pair_checks(*pair()).values())


@pytest.mark.parametrize("change,failed", [
    ({"status": "completed"}, "search_candidate_stop_has_evidence"),
    ({"status": "completed", "public_progress": 95}, "search_candidate_stop_has_evidence"),
    ({"reason": "task_impossible"}, "search_candidate_stop_has_evidence"),
    ({"public_progress": 33}, "search_progress_retained"),
    ({"public_progress": None}, "search_progress_retained"),
])
def test_wrong_stop_and_lost_progress_fail_acceptance(change, failed):
    a, b = pair(); b["search"].update(change)
    assert not cost.search_pair_checks(a, b)[failed]


def test_more_actions_cannot_be_presented_as_a_cost_reduction():
    a, b = pair(); b["physical_cost"] = deepcopy(a["physical_cost"])
    checks = cost.search_pair_checks(a, b)
    assert not checks["search_fewer_actions"] and not checks["search_fewer_public_requests"]


def test_exact_file_inventory_rejects_tamper_and_extra_files(tmp_path):
    path = tmp_path / "evidence.json"; path.write_bytes(b'{}')
    files = {path.name: cost.sha(path.read_bytes())}
    cost.verify_inventory(tmp_path, files)
    (tmp_path / "extra").write_bytes(b"added")
    with pytest.raises(ValueError, match="inventory"):
        cost.verify_inventory(tmp_path, files)
    (tmp_path / "extra").unlink(); path.write_bytes(b'{"changed":true}')
    with pytest.raises(ValueError):
        cost.verify_inventory(tmp_path, files)


def test_plan_has_fixed_fourteen_slots_same_scripts_and_only_two_changes():
    plan = cost.read(cost.CONFIG / "plan.json")
    assert plan["planned_episodes"] == 14 and plan["provider_calls"] == 0
    cases = [case for name in plan["groups"].values() for case in cost.read(cost.CONFIG / name)]
    assert tuple(c["trial_id"] for c in cases) == cost.EXPECTED_IDS
    assert sum(c["task_id"] == "c74d7020" for c in cases) == 3
    for case in cases:
        trace = cost.read(cost.CONFIG / case["trace_file"])
        assert "[TASK DONE]" in trace[-1]["final"]
    a = cost.read(cost.CONFIG / "control-profile.json")
    b = cost.read(cost.CONFIG / "candidate-profile.json")
    b.update(schema_version=2, profile_id="general-agent-v2-local")
    assert b["agent"].pop("input_layout") == "compact_tools_v1"
    assert b["harness"]["revision"] == "search_progress_v8"
    b["harness"]["revision"] = "observed_target_v7"
    assert b == a
    for record in plan["studies"].values():
        assert cost.sha((ROOT / record["path"] / "manifest.json").read_bytes()) == record["manifest_sha256"]


def test_existing_sealed_partial_archive_passes_raw_history_join():
    folder = ROOT / "docs/results/harness-method-validation-20261103/partial-recovery-02/run-01/trials/partial-positive"
    archive = next((folder / "policy/modelio").glob("*.jsonl"))
    report, decisions = cost.wire_audit(archive, cost.read(folder / "policy/task-state.json"))
    assert report["all_original_graph_messages_preserved"] and len(decisions) == 6
    assert all(r["recovery_projection_present"] for r in report["requests"])
    assert report["wire_bytes"] > 0


def unknown_fixture(tmp_path):
    private = tmp_path / "private"; private.mkdir()
    endpoint = "/drones/actor-public/command/take_off"
    (private / "local-api-fault.json").write_text(json.dumps({"endpoint": endpoint, "after_public_api_return": True}))
    api = [{"request_id": 1, "endpoint": endpoint, "committed": True, "response": {"status": "success"}},
           {"request_id": 2, "endpoint": "/drones/actor-public", "origin": {"kind": "model_tool", "tool": "get_drone_status"}},
           {"request_id": 3, "endpoint": "/drones/actor-public/command/hover", "committed": True, "response": {"status": "success"}}]
    state = {"mission_completion": "not_evaluated", "actions": [{"tool_name": "take_off", "effect_status": "unknown"}]}
    physical = [{"request_id": 1, "history": {"position": {"z": 12}}}]
    decisions = [{"calls": [("1", "get_drone_status", {})], "start_sequence": 4},
                 {"calls": [("2", "hover", {})], "start_sequence": 5}]
    return state, api, physical, decisions


def test_unknown_requires_real_success_and_later_model_opportunity(tmp_path):
    evidence = unknown_fixture(tmp_path)
    assert all(cost.unknown_checks(tmp_path, *evidence).values())


@pytest.mark.parametrize("mutation,failed", [
    (lambda s, a, p, d: a[0].update(committed=False), "real_committed_success_before_loss"),
    (lambda s, a, p, d: a[0]["response"].update(status="error"), "real_committed_success_before_loss"),
    (lambda s, a, p, d: p[0]["history"]["position"].update(z=0), "real_committed_success_before_loss"),
    (lambda s, a, p, d: a.append(deepcopy(a[0])), "exactly_one_takeoff"),
    (lambda s, a, p, d: s["actions"][0].update(effect_status="success"), "unknown_history_not_rewritten"),
    (lambda s, a, p, d: a[1]["origin"].update(kind="skill"), "new_model_status_then_hover"),
    (lambda s, a, p, d: a[2].update(endpoint="/drones/other/command/hover"), "new_model_status_then_hover"),
    (lambda s, a, p, d: d[1].update(start_sequence=3), "new_model_status_then_hover"),
])
def test_unknown_false_recovery_is_not_accepted(tmp_path, mutation, failed):
    evidence = unknown_fixture(tmp_path); mutation(*evidence)
    assert not cost.unknown_checks(tmp_path, *evidence)[failed]


def test_v9_selection_changes_only_revision_and_preserves_v8_source():
    original = cost.read(cost.CONFIG / "candidate-profile.json")
    before = deepcopy(original)
    selected = cost.profile_for_revision(original, "search_confirm_v9")
    assert original == before and selected == cost.read(cost.CONFIG / "recommended-profile.json")
    selected["harness"]["revision"] = "search_progress_v8"
    assert selected == original
    assert cost.profile_for_revision(original, "search_progress_v8") == original


@pytest.mark.parametrize("revision", ["observed_target_v7", "search_confirm_v10", None])
def test_arbitrary_candidate_revision_cannot_enter_the_fixed_comparison(revision):
    with pytest.raises(ValueError):
        cost.profile_for_revision(cost.read(cost.CONFIG / "candidate-profile.json"), revision)


def test_v9_fixed_low_progress_fixture_must_preserve_all_exploration():
    a, _ = pair(); a["actions"] = [{"tool": "move_to", "params": {"x": 200}}]
    a["search"]["cursor"] = 80
    assert all(cost.search_pair_checks(a, deepcopy(a), "search_confirm_v9").values())
    b = deepcopy(a); b["search"].update(reason="search_progress_stalled", cursor=60)
    assert not cost.search_pair_checks(a, b, "search_confirm_v9")["search_same_terminal_state_without_stall"]
    b = deepcopy(a); b["physical_cost"]["api_requests"] += 1
    assert not cost.search_pair_checks(a, b, "search_confirm_v9")["search_same_physical_cost_below_95"]
    b = deepcopy(a); b["actions"] = []
    assert not cost.search_pair_checks(a, b, "search_confirm_v9")["search_same_action_sequence_below_95"]


def test_original_v8_sealed_bundle_remains_read_only_verifiable():
    folder = ROOT / "docs/results/harness-cost-reduction-20261103/contract-run-01"
    before = (folder / "manifest.json").read_bytes()
    report = cost.verify_package(folder)
    assert report["passed"] and report["reported_episodes"] == 14
    assert "candidate_selection" not in report
    assert (folder / "manifest.json").read_bytes() == before
