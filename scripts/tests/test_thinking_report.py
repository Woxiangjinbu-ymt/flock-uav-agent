"""Counting, provenance and fail-closed G3 gates on isolated fixtures."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinking_events.labels import label_turns
from thinking_events.report import EXPECTED, g3_bounds, summarize_g3


@pytest.mark.parametrize(("counts", "status"), [
    ((1, 9, 0), "pass"), ((9, 1, 0), "pass"), ((1, 1, 8), "pass"),
    ((1, 10, 0), "fail"), ((10, 1, 0), "fail"),
    ((0, 10, 1), "fail"), ((0, 9, 1), "indeterminate"), ((9, 0, 1), "indeterminate"),
    ((0, 0, 10), "indeterminate"), ((0, 0, 0), "indeterminate"),
])
def test_three_way_exact_bounds(counts, status):
    gate = g3_bounds(*counts)
    assert gate["status"] == status
    n = sum(counts)
    assert gate["interval"] == ([counts[0]/n, (counts[0]+counts[2])/n] if n else [None, None])


def fixture_population():
    rows, attempts = [], []
    for batch, (episodes, requests, n_rows, missing) in EXPECTED.items():
        for episode_index in range(episodes):
            n = n_rows // episodes + int(episode_index < n_rows % episodes)
            for i in range(1, n + 1):
                state = "start" if i == 1 else ("event" if i == 2 else "E0")
                common = {
                    "batch": batch, "episode": f"trial-{episode_index}", "attempt_id": f"attempt-{i}",
                    "logical_call_id": f"model-{i}", "request_sequence": i,
                    "condition": "injected" if episode_index == 0 else "natural",
                    "arm": "candidate" if episode_index % 2 == 0 else "control",
                }
                clause = {"clause_id": "s0-20", "supported": True, "kind": "checkpoint", "status": "bound"}
                events = [{"type": "E1", "call_id": "same"}, {"type": "E1", "call_id": "other"},
                          {"type": "E6", "call_id": "same", "clause_id": "s0-20"}] if state == "event" else []
                tokens = 0 if len(rows) < 69 else 1
                rows.append({**common, "turn": i, "event_state": state, "events": events, "clause_coverage": [clause],
                             "gaps": [], "reasoning_tokens": tokens, "has_reasoning_text": bool(tokens)})
                attempts.append({**common, "status": "response"})
            if episode_index < missing:
                attempts.append({**common, "request_sequence": n+1, "attempt_id": "timeout", "status": "no_response"})
    rows[-1]["reasoning_tokens"] += 53319 - sum(r["reasoning_tokens"] for r in rows)
    return rows, attempts


def report(rows, attempts):
    labeled, schedule = label_turns(rows)
    return summarize_g3(labeled, attempts, schedule, source_sha256="a"*64, turns_sha256="b"*64, attempts_sha256="c"*64)


def test_full_gate_strata_deduplication_token_missingness_and_provenance():
    rows, attempts = fixture_population()
    g = report(rows, attempts)
    assert g["status"] == "pass" and g["integrity_issues"] == []
    assert g["counts"] == {"A": 31, "B": 152, "U": 0}
    assert g["overall"]["events"]["E1"] == {"turns": 31, "calls": 62, "episodes": 31}
    assert g["overall"]["events"]["E6"] == {"turns": 31, "calls": 31, "episodes": 31}
    assert g["overall"]["clauses"]["total"] == g["overall"]["clauses"]["triggered"] == 31
    assert set(g["inputs_sha256"]) == {"sources.json", "attempts.jsonl", "turns.jsonl"}
    for groups in g["strata"].values():
        assert sum(s["A"] for s in groups) == 31 and sum(s["turns"] for s in groups) == 214
    assert g["overall"]["teacher_reasoning_tokens"]["event"]["n"] == 31
    assert g["teacher_usage_all_turns"]["sum"] == 53319 and g["teacher_usage_all_turns"]["p50"] == 1
    assert g["keep_all_equals_teacher_effective"]
    event = next(r for r in rows if r["event_state"] == "event")
    event["reasoning_tokens"] = None
    changed = report(rows, attempts)
    assert changed["overall"]["teacher_reasoning_tokens"]["event"]["missing"] == 1
    assert "teacher_usage_mismatch" in changed["integrity_issues"] and changed["status"] == "indeterminate"
    assert changed["overall"]["event_state_x_teacher_request"]["event"]["unknown"] == 1


def test_no_positive_decision_for_corrupt_missing_or_misjoined_attempt():
    rows, attempts = fixture_population()
    for alteration, issue in (("wrong_status", "invalid_attempt"), ("wrong_identity", "response_attempt_join"),
                              ("duplicate_id", "duplicate_attempt"), ("misnumbered", "nonconsecutive_decisions")):
        ra, aa = deepcopy(rows), deepcopy(attempts)
        if alteration == "wrong_status":
            aa[0]["status"] = "invalid"
        elif alteration == "wrong_identity":
            aa[0]["logical_call_id"] = "foreign"
        elif alteration == "duplicate_id":
            aa[1]["attempt_id"] = aa[0]["attempt_id"]
        else:
            ra[0]["turn"] = 77
        gate = report(ra, aa)
        assert gate["status"] == "indeterminate" and issue in gate["integrity_issues"]
    gate = report(rows[:-1], attempts)
    assert gate["status"] == "indeterminate" and "expected_183_post_initial_decisions" in gate["integrity_issues"]
