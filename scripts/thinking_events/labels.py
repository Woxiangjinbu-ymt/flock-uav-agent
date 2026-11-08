"""Decision-time event labels and post-hoc teacher supervision for the sealed dev replay."""

from . import VERSION

LOGIC_MODEL = "Qwen/Qwen3-1.7B@70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
EVENT_REQUEST = {"start": True, "event": True, "E0": False, "indeterminate": None}


def _label(source, requested, has_text, reason=None):
    effective = None if requested is None else requested and has_text
    if requested and not has_text:
        reason = "requested_think_without_text"
    return {
        "version": f"{VERSION}/{source}",
        "requested_think": requested,
        "effective_think": effective,
        "reason": reason,
    }


def summarize_labels(rows, source):
    """Use known labels as each rate denominator and expose all missing counts."""
    labels = [row["labels"][source] for row in rows]
    requested = [label["requested_think"] for label in labels if label["requested_think"] is not None]
    effective = [label["effective_think"] for label in labels if label["effective_think"] is not None]
    retained = [row["reasoning_tokens"] for row in rows if row["labels"][source]["effective_think"]]
    return {
        "turns": len(rows),
        "requested_known": len(requested),
        "requested": sum(requested),
        "requested_rate": sum(requested) / len(requested) if requested else None,
        "effective_known": len(effective),
        "effective": sum(effective),
        "effective_rate": sum(effective) / len(effective) if effective else None,
        "requested_without_text": sum(label["reason"] == "requested_think_without_text" for label in labels),
        "retained_reasoning_tokens_known_sum": sum(value for value in retained if value is not None),
        "retained_turns_missing_token_count": retained.count(None),
    }


def _fixed_schedule(rows):
    decidable = [row for row in rows if EVENT_REQUEST[row["event_state"]] is not None]
    target = sum(EVENT_REQUEST[row["event_state"]] for row in decidable)
    candidates = []
    for k in range(1, 1 + max((row["turn"] for row in rows), default=0)):
        count = sum((row["turn"] - 1) % k == 0 for row in decidable)
        candidates.append({"k": k, "requested": count, "absolute_count_difference": abs(count - target)})
    # k is increasing, so min also resolves equal count differences to the smaller k.
    best = min(candidates, key=lambda c: c["absolute_count_difference"]) if decidable else None
    return {"k": best["k"] if best else None, "common_turns": len(decidable), "candidates": candidates}


def label_turns(turns):
    """Fit one k to all event-decidable dev rounds, then apply it at original round numbers."""
    rows = list(turns)
    schedule = _fixed_schedule(rows)
    k = schedule["k"]
    labeled = []
    for row in rows:
        state = row["event_state"]
        tokens = row["reasoning_tokens"]
        has_text = row["has_reasoning_text"]
        if type(has_text) is not bool or (tokens is not None and (type(tokens) is not int or tokens < 0)):
            raise ValueError("Expected a boolean text indicator and a nonnegative integer token count or null")
        teacher_request = tokens > 0 if tokens is not None else None
        event_request = EVENT_REQUEST[state]
        fixed_request = (row["turn"] - 1) % k == 0 if k else None
        labels = {
            "event": _label("event", event_request, has_text, "indeterminate_evidence" if event_request is None else None),
            "teacher": _label("teacher", teacher_request, has_text, "missing_reasoning_tokens" if tokens is None else None),
            "fixed_interval": {**_label("fixed_interval", fixed_request, has_text, "no_common_turns" if k is None else None), "k": k},
            "logic": {**_label("logic", None, has_text, "deferred"), "model": LOGIC_MODEL, "epsilon_nat_per_token": 0.001},
            "keep_all": _label("keep_all", True, has_text),
            "drop_all": _label("drop_all", False, has_text),
        }
        discrepancy = None
        if tokens is not None and teacher_request != has_text:
            discrepancy = "positive_tokens_without_text" if teacher_request else "text_with_zero_tokens"
        labeled.append({**row, "labels": labels, "usage_text_discrepancy": discrepancy})

    common = [row for row in labeled if row["labels"]["event"]["requested_think"] is not None]
    event = summarize_labels(common, "event")
    fixed = summarize_labels(common, "fixed_interval")
    diff = abs(event["effective"] - fixed["effective"])
    schedule.update({
        "event": event,
        "fixed_interval": fixed,
        "absolute_effective_difference_pp": 100 * diff / len(common) if common else None,
        # Compare integer counts to avoid losing an exact two-percentage-point boundary.
        "within_2pp": 100 * diff <= 2 * len(common) if common else None,
    })
    return labeled, schedule
