"""Comparison gates must not erase failures or accept incomparable pairs."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
from validate_search_progress import TASKS, compare_rows


def rows():
    return [{"task_id": t, "revision": r, "passed": True, "initial": {"state": t},
        "runtime": {"task_seconds": 500}, "trace_sha256": "same",
        "physical_cost": {"action_attempts": 10, "api_requests": 40}}
        for t in TASKS for r in ("observed_target_v7", "search_progress_v8")]


def test_reduced_actions_cannot_hide_lost_success():
    data = rows()
    data[1]["passed"] = False
    data[1]["physical_cost"] = {"action_attempts": 2, "api_requests": 8}
    result = compare_rows(data)
    assert result["candidate_preserves_measured_successes"] is False
    assert result["pairs"][0]["success_lost"] is True
    assert result["v7_passed"] == 8 and result["v8_passed"] == 7


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "initial", "runtime", "trace_sha256"])
def test_denominator_and_pair_identity_are_required(mutation):
    data = rows()
    if mutation == "omit":
        data.pop()
    elif mutation == "duplicate":
        data[-1] = copy.deepcopy(data[1])
    else:
        data[1][mutation] = {"changed": True}
    with pytest.raises(ValueError):
        compare_rows(data)


def test_existing_negatives_retained_without_becoming_regressions():
    data = rows()
    for row in data:
        if row["task_id"] in {"784d71f8", "1905e0ad"}:
            row["passed"] = False
    result = compare_rows(data)
    assert result["candidate_preserves_measured_successes"]
    assert result["denominator_per_arm"] == 8
    assert result["v7_passed"] == result["v8_passed"] == 6
    assert not result["natural_model_or_token_gain_measured"]


def test_v9_followup_rejects_partial_loss_even_when_both_checker_fail():
    data=rows()
    for row in data:
        if row["revision"]=="search_progress_v8":row["revision"]="search_confirm_v9"
    data[0]["passed"]=data[1]["passed"]=False
    data[0]["frames"]=[{"state":{"progress_percent":66}}]
    data[1]["frames"]=[{"state":{"progress_percent":33}}]
    result=compare_rows(data,"search_confirm_v9")
    assert result["candidate_preserves_measured_successes"]
    assert not result["candidate_preserves_measured_partial_progress"]
    assert not result["passes_followup_acceptance_gate"]
