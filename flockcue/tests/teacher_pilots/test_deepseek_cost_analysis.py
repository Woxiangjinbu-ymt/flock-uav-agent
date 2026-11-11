"""Reject false cost/layout attribution; preserve missing recovery denominators."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_deepseek_cost_pilot as m


@pytest.fixture
def templates():
    return (m._string_assignment(ROOT / "agent4drone/template/system_prompt.py", "SYSTEM_PROMPT_TEMPLATE"),
            m._string_assignment(ROOT / "agent4drone/template/parsing_error.py", "PARSING_ERROR_TEMPLATE"))


def schemas(revision="observed_target_v7"):
    return [{"type": "function", "function": {"name": name, "description": "Public " + name + " " + revision,
        "parameters": {"type": "object", "properties": {"input_json": {"type": "string"}}, "required": ["input_json"]}}}
        for name in ("take_off", "run_harness")]


def body(templates, layout="compact_tools_v1"):
    tools = schemas()
    catalog = "\n".join("- " + t["function"]["name"] + (
        ": " + t["function"]["description"] if layout == "full_v1" else "") for t in tools)
    prompt = templates[0].format(parsing_error_template=templates[1], tool_lines=catalog)
    return {"tools": tools, "messages": [{"role": "system", "content": prompt + "\nPublic evidence suffix"},
        {"role": "assistant", "reasoning_content": "DO_NOT_EXPORT_REASONING", "content": None}]}


def test_ten_slots_four_tasks_and_balanced_previous_four_condition_order():
    slots = m.expected_trials()
    assert len(slots) == len({s[0] for s in slots}) == 10
    assert len({s[2] for s in slots}) == len({s[3] for s in slots}) == 4
    assert [s[4] for s in slots] == ["control", "candidate", "candidate", "control", "control", "candidate",
                                    "candidate", "control", "control", "candidate"]
    assert slots[-1][2:4] == ("b1ad4f3e", "35ce64d8")


@pytest.mark.parametrize("layout", ["full_v1", "compact_tools_v1"])
def test_actual_layout_matches_frozen_operating_rules_and_preserves_native_definitions(templates, layout):
    value = body(templates, layout)
    before = copy.deepcopy(value)
    audit = m.audit_wire_layout(value, layout, *templates)
    assert audit["layout"] == layout and audit["catalog_matches_layout"]
    assert audit["native_tool_count"] == 2 and audit["native_parameter_schemas_present"]
    assert value == before and "DO_NOT_EXPORT_REASONING" not in json.dumps(audit)


@pytest.mark.parametrize("layout", ["full_v1", "compact_tools_v1"])
def test_selectors_cannot_claim_other_actual_layout(templates, layout):
    other = "full_v1" if layout == "compact_tools_v1" else "compact_tools_v1"
    with pytest.raises(ValueError, match="Actual input layout"):
        m.audit_wire_layout(body(templates, other), layout, *templates)


@pytest.mark.parametrize("damage", ["missing_description", "missing_parameters", "duplicate_name", "altered_rules", "extra_catalog", "extra_system"])
def test_actual_layout_checks_reject_content_loss(templates, damage):
    value = body(templates)
    if damage == "missing_description":
        value["tools"][0]["function"].pop("description")
    elif damage == "missing_parameters":
        value["tools"][0]["function"].pop("parameters")
    elif damage == "duplicate_name":
        value["tools"][1]["function"]["name"] = "take_off"
    elif damage == "altered_rules":
        value["messages"][0]["content"] = value["messages"][0]["content"].replace("Always end", "Never end")
    elif damage == "extra_catalog":
        value["messages"][0]["content"] += m.MARKER
    else:
        value["messages"].append(value["messages"][0])
    with pytest.raises(ValueError):
        m.audit_wire_layout(value, "compact_tools_v1", *templates)


def test_only_run_harness_description_may_differ_between_arms():
    control, candidate = schemas(), schemas()
    candidate[1]["function"]["description"] += " new confirmation policy"
    audit = m.compare_tool_contracts([control, control], [candidate, candidate])
    assert audit["verified"] and audit["description_only_changes"] == ["run_harness"]
    assert audit["tool_names_order_and_parameters_identical"] and audit["all_requests_checked"] == 4
    assert "description" in control[1]["function"] and "description" in candidate[1]["function"]


@pytest.mark.parametrize("damage", ["raw_tool_description", "input_type", "tool_removed", "tool_order", "within_trial"])
def test_changed_permissions_or_parameters_cannot_be_hidden_by_layout_claim(damage):
    control, candidate = schemas(), schemas()
    series = [candidate]
    if damage == "raw_tool_description":
        candidate[0]["function"]["description"] += " altered"
    elif damage == "input_type":
        candidate[1]["function"]["parameters"]["properties"]["input_json"]["type"] = "object"
    elif damage == "tool_removed":
        candidate.pop()
    elif damage == "tool_order":
        candidate.reverse()
    else:
        series.append(copy.deepcopy(candidate))
        series[1][1]["function"]["description"] += " mid-run drift"
    with pytest.raises(ValueError):
        m.compare_tool_contracts([control], series)


def test_missing_actual_dispatch_cannot_verify_pair_permissions():
    assert m.compare_tool_contracts([], [schemas()])["verified"] is None


def confirmation_frame():
    return {"frames": [{"skill": "public_search", "plan_id": "p", "status": "completed", "state": {
        "progress_percent": 100, "search_confirmation": {"revision": "search_confirm_v9", "stagnation_handoff_enabled": False,
        "extra_reads": [{"cached_progress_percentage": 95, "progress_percentage": 100, "evidence_ref": "e12",
            "trigger": "new_trusted_reach_after_cached_progress_at_least_95"}]}}}]}


def test_v9_trigger_counts_program_read_separately_from_model_decisions():
    audit = m.search_confirmation(confirmation_frame(), {"arm": "candidate"})
    assert audit["search_frames"] == audit["extra_read_count"] == 1
    assert audit["frames"][0]["final_public_progress"] == 100


@pytest.mark.parametrize("damage", ["control_arm", "cached_below_95", "old_v8", "stagnation"])
def test_confirmation_trigger_cannot_claim_unfrozen_policy(damage):
    harness = confirmation_frame()
    trial = {"arm": "candidate"}
    confirmation = harness["frames"][0]["state"]["search_confirmation"]
    if damage == "control_arm":
        trial["arm"] = "control"
    elif damage == "cached_below_95":
        confirmation["extra_reads"][0]["cached_progress_percentage"] = 94
    elif damage == "old_v8":
        confirmation["revision"] = "search_progress_v8"
    else:
        confirmation["stagnation_handoff_enabled"] = True
    with pytest.raises(ValueError):
        m.search_confirmation(harness, trial)


def test_no_search_frame_is_zero_activation_not_proof_of_search_benefit():
    audit = m.search_confirmation({"frames": []}, {"arm": "candidate"})
    assert audit["extra_read_count"] == audit["search_frames"] == 0
    assert m.search_confirmation(None, {"arm": "candidate"})["extra_read_count"] is None


def test_aggregate_missing_cost_usage_and_recovery_are_not_zero_successes():
    r = m.aggregate_reports([{"started": False, "condition": "unknown_takeoff", "checker_passed": None}])
    assert r["total_tokens"] is None and r["conservative_usd"] is None
    assert r["unknown_recovery_opportunities"] == 0 and r["unknown_recovery_rate"] is None
    assert r["normal_conditions"] == {"scored": 0, "passed": 0}


def test_partial_cache_reporting_retains_coverage_and_does_not_add_to_prompt_twice():
    group = [{"started": True, "condition": "short_chain", "checker_passed": True,
        "model_io": {"usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24,
            "known_usage_responses": 2, "delegated_http_attempts": 2, "prompt_cache_hit_tokens": 3,
            "prompt_cache_hit_tokens_reported_responses": 1, "prompt_cache_miss_tokens_reported_responses": 0}},
        "world": {"action_attempts": 2, "api_requests": 8}, "worker_elapsed_seconds": 1.5,
        "unknown_recovery": {"opportunities": 1, "recovery_rate": 1}, "cost": {"settled_conservative_charge": "0.01"}}]
    r = m.aggregate_reports(group)
    assert r["total_tokens"] == 24 and r["prompt_tokens"] == 20
    assert r["prompt_cache_hit_tokens_known_sum"] == 3 and not r["prompt_cache_hit_tokens_complete"]
    assert r["prompt_cache_miss_tokens_known_sum"] is None and not r["prompt_cache_miss_tokens_complete"]
    assert r["unknown_recovery_rate"] == 1


@pytest.mark.parametrize("progress,passed", [(95, False), (100, True)])
def test_added_search_task_uses_same_strict_public_completion_obligation(progress, passed):
    audit = m.obligations({"actions": []}, [{"request_id": 4, "response": {"progress_percentage": progress}}],
                          {"task_id": "b1ad4f3e"}, True, True)
    assert audit["checker_and_additional_obligation_passed"] is passed


@pytest.fixture
def empty_sealed_run(tmp_path):
    import deepseek_cost_pilot as pilot
    run = tmp_path / "empty-local-only"
    pilot.prepare(run, local_smoke=True)
    (run / "results.json").write_text("[]\n")
    (run / "summary.json").write_text(json.dumps({"planned_trials": 10, "started_trials": 0, "scored_trials": 0,
        "unstarted_trials": [s[0] for s in m.expected_trials()], "status": "stopped", "reason": "test_no_dispatch"}))
    reseal(run)
    return run


def reseal(run):
    (run / "manifest.sha256").write_text(m.sha((run / "manifest.json").read_bytes()) + "\n")
    files = {str(p.relative_to(run)): m.sha(p.read_bytes()) for p in run.rglob("*")
             if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
    (run / "campaign-seal.json").write_text(json.dumps({"files": files}))


def test_read_only_verifier_retains_all_unrun_slots_without_model_or_ledger(empty_sealed_run):
    before = {str(p): p.read_bytes() for p in empty_sealed_run.rglob("*") if p.is_file()}
    manifest, results, check = m.verify_run(empty_sealed_run)
    assert manifest["local_smoke"] and results == [] and check["trials"] == {}
    assert {str(p): p.read_bytes() for p in empty_sealed_run.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("damage", ["arm_raw", "layout", "revision", "variant", "order", "authorization"])
def test_resealed_manifest_cannot_disguise_changed_comparison(empty_sealed_run, damage):
    path = empty_sealed_run / "manifest.json"
    manifest = json.loads(path.read_text())
    if damage == "arm_raw":
        manifest["trials"][0]["task_progress_mode"] = "off"
    elif damage == "layout":
        manifest["trials"][0]["input_layout"] = "compact_tools_v1"
    elif damage == "revision":
        manifest["trials"][1]["harness_revision"] = "search_progress_v8"
    elif damage == "variant":
        manifest.pop("online_variant_contract")
    elif damage == "order":
        manifest["trials"].reverse()
    else:
        manifest["authorization"]["episodes"] = 10
    path.write_text(json.dumps(manifest))
    reseal(empty_sealed_run)
    with pytest.raises(ValueError):
        m.verify_run(empty_sealed_run)
