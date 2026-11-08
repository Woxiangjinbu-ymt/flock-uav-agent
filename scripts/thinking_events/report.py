"""Read-only summaries for the first-stage G3 development screen."""
from collections import Counter, defaultdict
from math import ceil

from . import VERSION
from .labels import summarize_labels

# Frozen T01/T36 development population, in campaign manifest order.
# Values are (episodes, requests, returned decisions, terminal unanswered attempts).
EXPECTED = {
    "deepseek-behavior-pilot-20261102": (5, 38, 37, 1),
    "deepseek-contract-pilot-20261103": (8, 58, 56, 2),
    "deepseek-cost-pilot-20261103": (10, 69, 68, 1),
    "deepseek-hover-pilot-20261103": (8, 53, 53, 0),
}
EVENT_TYPES = tuple(f"E{n}" for n in range(1, 7))


def g3_bounds(a, b, u):
    """Classify the entire possible interval with exact, inclusive 10%/90% bounds."""
    if any(type(n) is not int or n < 0 for n in (a, b, u)):
        raise ValueError("G3 counts must be nonnegative integers")
    n = a + b + u
    status = "indeterminate"
    if a + b:
        if 10 * a >= n and 10 * (a + u) <= 9 * n:
            status = "pass"
        elif 10 * (a + u) < n or 10 * a > 9 * n:
            status = "fail"
    return {
        "status": status,
        "counts": {"A": a, "B": b, "U": u},
        "denominator": n,
        "event_fraction_among_decidable": a / (a + b) if a + b else None,
        "interval": [a / n, (a + u) / n] if n else [None, None],
    }


def _usage(rows):
    values = sorted(row["reasoning_tokens"] for row in rows if row["reasoning_tokens"] is not None)
    n = len(values)
    zeros = values.count(0)
    return {
        "turns": len(rows), "n": n, "missing": len(rows) - n,
        "zero": zeros, "zero_fraction": zeros / n if n else None,
        "sum": sum(values),
        **{f"p{q}": values[ceil(q * n / 100) - 1] if n else None for q in (25, 50, 75, 95)},
    }


def _coverage(rows):
    turns = Counter()
    calls = defaultdict(set)
    episodes = defaultdict(set)
    clauses = {}
    triggered = set()
    for row in rows:
        episode = (row["batch"], row["episode"])
        turns.update({event["type"] for event in row["events"]})
        for event in row["events"]:
            code = event["type"]
            if code not in EVENT_TYPES:
                raise ValueError(f"Unknown event type: {code}")
            calls[code].add((*episode, row["turn"], event["call_id"]))
            episodes[code].add(episode)
            if code == "E6":
                triggered.add((*episode, event["clause_id"]))
        for clause in row["clause_coverage"]:
            key = (*episode, clause["clause_id"])
            entry = clauses.setdefault(key, {"supported": False, "kind": clause["kind"], "statuses": set()})
            entry["supported"] |= clause["supported"]
            entry["statuses"].add(clause["status"])

    by_kind = defaultdict(Counter)
    status_ever = Counter()
    for key, clause in clauses.items():
        group = by_kind[clause["kind"]]
        group["total"] += 1
        group["supported"] += int(clause["supported"])
        group["triggered"] += int(key in triggered)
        status_ever.update(clause["statuses"])
    if not triggered.issubset(clauses):
        raise ValueError("E6 event has no traceable clause")
    return {
        "events": {code: {"turns": turns[code], "calls": len(calls[code]), "episodes": len(episodes[code])}
                   for code in EVENT_TYPES},
        "clauses": {
            "total": len(clauses), "supported": sum(c["supported"] for c in clauses.values()),
            "triggered": len(triggered), "by_kind": dict(by_kind), "status_ever": dict(status_ever),
        },
    }


def _stratum(rows):
    states = Counter(row["event_state"] for row in rows)
    cross = defaultdict(Counter)
    for row in rows:
        teacher = row["labels"]["teacher"]["requested_think"]
        category = "unknown" if teacher is None else ("think" if teacher else "skip")
        cross[row["event_state"]][category] += 1
    return {
        "turns": len(rows), "episodes": len({(r["batch"], r["episode"]) for r in rows}),
        "start": states["start"], "A": states["event"], "B": states["E0"], "U": states["indeterminate"],
        "turns_with_gaps": sum(bool(row["gaps"]) for row in rows),
        "teacher_reasoning_tokens": {state: _usage([r for r in rows if r["event_state"] == state])
                                     for state in ("event", "E0")},
        "event_state_x_teacher_request": dict(cross),
        **_coverage(rows),
    }


def _stratify(rows, fields):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in fields)].append(row)
    return [{**dict(zip(fields, key)), **_stratum(group)} for key, group in sorted(groups.items())]


def _account(rows, attempts):
    """An incomplete or misjoined population cannot unlock a paid batch."""
    problems = []
    batches = {}
    for batch, expected in EXPECTED.items():
        br = [r for r in rows if r["batch"] == batch]
        ba = [a for a in attempts if a["batch"] == batch]
        n_episodes = len({r["episode"] for r in br})
        terminal = [a for a in ba if a["status"] == "no_response"]
        observed = (n_episodes, len(ba), len(br), len(terminal))
        batches[batch] = dict(zip(("episodes", "attempts", "responses", "no_response"), observed))
        if observed != expected:
            problems.append(f"{batch}: expected {expected}, observed {observed}")
        for absent in terminal:
            if any(a["episode"] == absent["episode"] and a["request_sequence"] > absent["request_sequence"] for a in ba):
                problems.append(f"{batch}/{absent['episode']}: no_response was not terminal")
    if any(item["batch"] not in EXPECTED for item in [*rows, *attempts]):
        problems.append("unexpected_batch")
    if any(a["status"] not in {"response", "no_response", "retried"} for a in attempts):
        problems.append("invalid_attempt")
    key_fields = ("batch", "episode", "logical_call_id", "attempt_id", "request_sequence")
    def attempt_key(record):
        return tuple(record[name] for name in key_fields)
    decisions = Counter(map(attempt_key, rows))
    accepted = Counter(attempt_key(a) for a in attempts if a["status"] == "response")
    if decisions != accepted:
        problems.append("response_attempt_join")
    if len({(r["batch"], r["episode"], r["logical_call_id"]) for r in rows}) != len(rows):
        problems.append("duplicate_logical_decision")
    if len({(a["batch"], a["episode"], a["attempt_id"]) for a in attempts}) != len(attempts):
        problems.append("duplicate_attempt")
    by_episode = defaultdict(list)
    for row in rows:
        by_episode[(row["batch"], row["episode"])].append(row)
        if (row["event_state"] == "start") != (row["turn"] == 1):
            problems.append("start_turn_mismatch")
        if bool(row["events"]) != (row["event_state"] == "event"):
            problems.append("event_state_mismatch")
    for group in by_episode.values():
        ordered = sorted(group, key=lambda row: row["request_sequence"])
        if [r["turn"] for r in ordered] != list(range(1, len(group) + 1)):
            problems.append("nonconsecutive_decisions")
    return batches, sorted(set(problems))


def summarize_g3(labeled_rows, attempts, schedule, *, source_sha256, turns_sha256, attempts_sha256):
    rows = list(labeled_rows)
    population, problems = _account(rows, attempts)
    overall = _stratum(rows)
    gate = g3_bounds(overall["A"], overall["B"], overall["U"])
    if gate["denominator"] != 183:
        problems.append("expected_183_post_initial_decisions")
    # The independent real-archive probe enumerated 214 explicit counts, 53,319 tokens and 69 zeros.
    usage = _usage(rows)
    if (usage["n"], usage["sum"], usage["zero"]) != (214, 53319, 69):
        problems.append("teacher_usage_mismatch")
    gate.update({
        "schema_version": VERSION, "gate": "G3", "split_usage": "development",
        "status": "indeterminate" if problems else gate["status"],
        "integrity_issues": problems, "population": population,
        "inputs_sha256": {"sources.json": source_sha256, "turns.jsonl": turns_sha256, "attempts.jsonl": attempts_sha256},
        "attempt_status_counts": dict(Counter(a["status"] for a in attempts)),
        "teacher_usage_all_turns": usage,
        "quantile_method": "nearest_rank", "overall": overall,
        "strata": {name: _stratify(rows, fields) for name, fields in {
            "by_batch": ("batch",), "by_arm": ("arm",), "by_condition": ("condition",),
            "by_batch_arm_condition": ("batch", "arm", "condition"),
        }.items()},
        "fixed_schedule": schedule,
        "label_totals": {s: summarize_labels(rows, s)
                         for s in ("event", "teacher", "fixed_interval", "logic", "keep_all", "drop_all")},
        "usage_text_discrepancies": dict(Counter(r["usage_text_discrepancy"] for r in rows if r["usage_text_discrepancy"])),
        "keep_all_equals_teacher_effective": all(r["labels"]["teacher"]["effective_think"] ==
                                                 r["labels"]["keep_all"]["effective_think"] for r in rows) if rows else None,
    })
    return gate
