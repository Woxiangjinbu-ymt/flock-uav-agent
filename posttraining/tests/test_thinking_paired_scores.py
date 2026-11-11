"""Boundary and provenance checks for the revised 29-task scoring rule."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from thinking_events.campaign import planned_slots
from thinking_events.paired_scores import PRIMARY, REFERENCE, score_pairs


@pytest.fixture
def plan():
    manifest = json.loads((ROOT / "experiments/task-splits-v1/manifest.json").read_bytes())
    tasks = [{k: t[k] for k in ("task_id", "scene_id", "task_family", "command_sha256")}
             for t in manifest["tasks"] if t["split"] == "dev"]
    return planned_slots(sorted(tasks, key=lambda t: (t["scene_id"], t["task_id"])))


@pytest.fixture
def rows(plan):
    scene_seen = {}
    winners = set()
    for slot in plan:
        if slot["model"] == PRIMARY and slot["thinking"]:
            scene_seen.setdefault(slot["scene_id"], []).append(slot["task_id"])
    first, second, *_ = scene_seen.values()
    winners.update([*first[:2], second[0]])
    output = []
    for slot in plan:
        passed = slot["task_id"] in winners and slot["thinking"]
        evidence = {"kind": "decision_error", "detail": "unvisited checkpoint",
                    "reference": "synthetic-ledger#" + slot["slot_id"]}
        result = {"status": "scored", "trial_id": slot["slot_id"], "task_id": slot["task_id"],
                  "passed": passed, "service_ok": True, "context_fits": True,
                  "agent_claimed_done": True, "harness_completed": True, "stop_reason": None,
                  "scorer_seal_sha256": "a" * 64, "failure_evidence": None if passed else evidence}
        output.append({**slot, "status": "scored", "result": result})
    return output


def lookup(rows, *, model=PRIMARY, arm="on", passed=False):
    return [r for r in rows if r["model"] == model and r["arm"] == arm and r["result"]["passed"] is passed]


def test_exact_threshold_independent_of_order_and_claims(plan, rows):
    report = score_pairs(plan, reversed(rows))
    assert report["G1"]["status"] == report["G2"]["status"] == "pass"
    main = report["by_model"][PRIMARY]
    assert main["four_cells"] == {"only_on": 3, "only_off": 0, "both_success": 0, "both_failure": 26}
    assert main["arms"]["off"]["agent_claimed_done"] == 29 and main["arms"]["off"]["success"] == 0
    assert main["improved_scenes"] == 2 and sum(x["net_gain"] for x in main["by_scene"]) == 3
    keep = [r for r in lookup(rows, passed=True) if r["scene_id"] == lookup(rows, passed=True)[0]["scene_id"]]
    gains = lookup(rows, passed=True)
    outside = next(r for r in gains if r["scene_id"] != keep[0]["scene_id"])
    outside["result"]["passed"] = False
    next(r for r in lookup(rows) if r["scene_id"] == keep[0]["scene_id"])["result"]["passed"] = True
    report = score_pairs(plan, rows)
    assert report["G2"]["net_gain"] == 3 and report["G2"]["improved_scenes"] == 1
    assert report["G2"]["status"] == "fail"
    assert report["by_model"][REFERENCE]["improved_scenes"] == 2


@pytest.mark.parametrize("reason", ["episode_deadline", "request_timeout", "context_length"])
def test_relative_bias_overrides_success_rule(plan, rows, reason):
    source = lookup(rows)[0]
    source["result"]["stop_reason"] = reason
    report = score_pairs(plan, rows)
    assert report["G2"]["open_arm_excess"] == [reason, "any"] and report["G2"]["status"] == "indeterminate"
    source["result"]["request_limited_by_episode"] = reason == "request_timeout"
    pair = next(r for r in rows if r["model"] == PRIMARY and r["arm"] == "off" and r["task_id"] == source["task_id"])
    pair["result"]["stop_reason"] = "task_deadline" if reason == "request_timeout" else reason
    report = score_pairs(plan, rows)
    assert report["G2"]["status"] == "pass"
    assert report["by_model"][PRIMARY]["stop_pairs"]["any"]["both"] == 1


def test_pooled_balance_does_not_hide_specific_stop_bias(plan, rows):
    lookup(rows)[0]["result"]["stop_reason"] = "context_length"
    lookup(rows, arm="off")[0]["result"]["stop_reason"] = "request_timeout"
    report = score_pairs(plan, rows)
    assert report["G2"]["open_arm_excess"] == ["context_length"]
    assert report["G2"]["status"] == "indeterminate"


def test_missing_inputs_and_infrastructure_keep_denominator(plan, rows):
    target = lookup(rows, arm="off")[0]
    rows.remove(target)
    main = score_pairs(plan, rows)
    assert main["G2"]["status"] == "indeterminate" and main["G2"]["missing_pairs"] == 1
    off = main["by_model"][PRIMARY]["arms"]["off"]
    assert off["planned"] == 29 and off["status_counts"]["missing"] == 1
    target["result"].update(service_ok=False, stop_reason="student_backend_error")
    rows.append(target)
    result = score_pairs(plan, rows)
    assert result["G2"]["missing_pairs"] == 1
    assert result["by_model"][PRIMARY]["arms"]["off"]["scored"] == 29


def test_reference_cutoff_and_missing_observation_are_descriptive(plan, rows):
    lookup(rows, model=REFERENCE)[0]["result"]["stop_reason"] = "context_length"
    absent = lookup(rows, model=REFERENCE, arm="off")[0]
    rows.remove(absent)
    report = score_pairs(plan, rows)
    assert report["G1"]["status"] == report["G2"]["status"] == "pass"
    ref = report["by_model"][REFERENCE]
    assert ref["missing_pairs"] == 1
    assert ref["arms"]["on"]["stop_counts"]["context_length"] == 1
    assert ref["stop_pairs"]["context_length"]["unknown"] == 1


def test_g1_attribution_bounds_and_excluded_resource_stops(plan, rows):
    negative = lookup(rows)
    for row in negative[2:]:
        row["result"]["stop_reason"] = "token_budget"
    result = score_pairs(plan, rows)
    assert result["G1"]["status"] == "fail" and result["G1"]["attributable_failures"] == 2
    assert result["G2"]["status"] == "pass"
    negative[2]["result"].update(stop_reason=None, failure_evidence=None)
    result = score_pairs(plan, rows)
    assert result["G1"]["status"] == "indeterminate" and result["G1"]["attributable_failure_bounds"] == [2, 3]
    negative[2]["result"]["failure_evidence"] = negative[0]["result"]["failure_evidence"]
    assert score_pairs(plan, rows)["G1"]["status"] == "pass"
    # One unscored task cannot supply both a missing success and a missing attributable failure.
    rows.remove(lookup(rows, passed=True)[0])
    negative[2]["result"]["stop_reason"] = "request_limit"
    assert score_pairs(plan, rows)["G1"]["status"] == "fail"


@pytest.mark.parametrize("field,value", [("model", "1.7B"), ("scene_id", "wrong-parent"),
                                        ("turn_seeds", [3] * 12), ("thinking", 1)])
def test_mixed_identity_rejected(plan, rows, field, value):
    rows[0][field] = value
    with pytest.raises(ValueError, match="identity differs"):
        score_pairs(plan, rows)


def test_duplicate_missing_scores_and_plan_mismatch(plan, rows):
    with pytest.raises(ValueError, match="Duplicate"):
        score_pairs(plan, rows + rows[:1])
    rows[0]["result"]["scorer_seal_sha256"] = None
    with pytest.raises(ValueError, match="checker result"):
        score_pairs(plan, rows)
    mutated = deepcopy(plan)
    mutated[1]["command_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="different paired inputs"):
        score_pairs(mutated, [])


def test_usage_omissions_and_latency_keep_counts(plan, rows):
    one, two, *_ = lookup(rows)
    one["result"]["usage"] = {"requests": 2, "prompt_tokens": 20, "completion_tokens": 3,
                              "reasoning_tokens": 2, "total_tokens": 23,
                              "request_timings": [{"round_trip_seconds": 1}, {"round_trip_seconds": 4}],
                              "history_reasoning_tokens_by_request": [0, 2], "elapsed_seconds": 5}
    two["result"]["usage"] = {"requests": 1, "known_total_tokens": 8, "peak_context": 42}
    report = score_pairs(plan, rows)
    metrics = report["by_model"][PRIMARY]["arms"]["on"]["usage"]
    assert metrics["request_seconds"] == {"n": 2, "missing": 1, "p50": 1, "p95": 4}
    assert metrics["total_tokens"] == {"known_sum": 31, "measured_slots": 1, "unknown_slots": 28}
    assert metrics["peak_context"] == {"maximum": 42, "measured_slots": 1}
    one["result"]["usage"]["total_tokens"] = 999
    with pytest.raises(ValueError, match="Inconsistent token usage"):
        score_pairs(plan, rows)
