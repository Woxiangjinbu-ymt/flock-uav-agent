"""CPU fixtures for teacher supervision and the pre-registered fixed-interval comparator."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
from thinking_events.labels import LOGIC_MODEL, label_turns


def turn(number, state, tokens=24, text=True, episode="a"):
    return dict(turn=number, event_state=state, reasoning_tokens=tokens, has_reasoning_text=text, episode=episode)


def test_examples_keep_zero_distinct_from_missing_and_keep_action_rows():
    rows = [turn(1, "start"), turn(5, "event"), turn(8, "event", 0, False),
            turn(9, "indeterminate", None), turn(10, "E0", 0)]
    labeled, _ = label_turns(rows)
    assert len(labeled) == len(rows) and all("labels" not in row for row in rows)
    positive, empty, missing, discrepancy = [r["labels"] for r in labeled[1:]]
    assert positive["event"]["requested_think"] is positive["teacher"]["requested_think"] is True
    assert empty["event"]["requested_think"] is True and empty["event"]["effective_think"] is False
    assert empty["event"]["reason"] == "requested_think_without_text"
    assert empty["teacher"]["requested_think"] is False
    assert missing["teacher"]["requested_think"] is missing["teacher"]["effective_think"] is None
    assert missing["teacher"]["reason"] == "missing_reasoning_tokens"
    assert missing["event"]["requested_think"] is None
    assert labeled[-1]["usage_text_discrepancy"] == "text_with_zero_tokens"
    assert discrepancy["keep_all"]["effective_think"] and not discrepancy["drop_all"]["effective_think"]
    for r in labeled:
        logic = r["labels"]["logic"]
        assert logic["model"] == LOGIC_MODEL and logic["epsilon_nat_per_token"] == 0.001
        assert logic["reason"] == "deferred" and logic["requested_think"] is logic["effective_think"] is None


def test_common_denominator_original_rounds_reset_and_smallest_tie():
    rows = [turn(i, state) for i, state in enumerate(("start", "indeterminate", "event", "E0"), 1)]
    rows.extend([turn(1, "start", episode="b"), turn(2, "E0", episode="b")])
    labeled, schedule = label_turns(rows)
    assert schedule["k"] == 2 and schedule["common_turns"] == 5
    assert [c["requested"] for c in schedule["candidates"]] == [5, 3, 3, 2]
    assert [r["labels"]["fixed_interval"]["requested_think"] for r in labeled] == [True, False, True, False, True, False]
    assert schedule["event"]["retained_reasoning_tokens_known_sum"] == 72
    assert schedule["absolute_effective_difference_pp"] == 0 and schedule["within_2pp"]


@pytest.mark.parametrize(("n", "matched"), [(50, True), (49, False)])
def test_two_percentage_point_boundary_in_effective_labels(n, matched):
    # Exchange rounds 2 and 3 relative to k=2. Only round 2 has no teacher text.
    rows = []
    for i in range(1, n + 1):
        event = i == 2 or (i % 2 == 1 and i != 3)
        state = "start" if i == 1 else ("event" if event else "E0")
        rows.append(turn(i, state, 0 if i == 2 else 24, i != 2))
    _, schedule = label_turns(rows)
    assert schedule["k"] == 2 and schedule["event"]["requested"] == schedule["fixed_interval"]["requested"]
    assert abs(schedule["event"]["effective"] - schedule["fixed_interval"]["effective"]) == 1
    assert schedule["absolute_effective_difference_pp"] == pytest.approx(100 / n)
    assert schedule["within_2pp"] is matched


def test_positive_usage_without_text_is_reported_and_retained_missing_counts_stay_unknown():
    labeled, schedule = label_turns([turn(1, "start", 3, False), turn(2, "event", None, True)])
    assert labeled[0]["usage_text_discrepancy"] == "positive_tokens_without_text"
    assert labeled[0]["labels"]["teacher"]["reason"] == "requested_think_without_text"
    assert labeled[1]["labels"]["teacher"]["effective_think"] is None
    assert schedule["event"]["retained_turns_missing_token_count"] == 1


@pytest.mark.parametrize("tokens", [False, -1, 1.5])
def test_reject_invalid_usage(tokens):
    with pytest.raises(ValueError, match="nonnegative"):
        label_turns([turn(1, "start", tokens)])
