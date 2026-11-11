"""Score the revised 29-task G1/G2 study from an authenticated plan and slot evidence.

The caller verifies the provenance of the ledger (synthetic or independently scored).
This module joins only on identities from that plan. It does not admit any experiment.
"""
from collections import Counter
from math import ceil, isfinite
import re

from .campaign import turn_seed


PRIMARY = "Qwen3.5-2B"
REFERENCE = "Qwen3-4B"
MODELS = (PRIMARY, REFERENCE)
TIME_STOPS = ("episode_deadline", "request_timeout", "context_length")
COST_STOPS = ("token_budget", "request_limit", "generation_truncated")
INFRA_STOPS = {"infra_error", "student_transport_error", "student_backend_error", "http_error", "service_error"}
CELLS = ("both_success", "only_on", "only_off", "both_failure")
DENOMINATOR = 29


def _index(plan, rows):
    """Check plan topology and every supplied row against its own frozen slot."""
    frozen = {}
    tasks = {}
    combinations = set()
    for slot in plan:
        sid, model, task = slot["slot_id"], slot["model"], slot["task_id"]
        arm = slot["arm"]
        key = (model, task, arm)
        if (sid in frozen or key in combinations or model not in MODELS or arm not in {"on", "off"}
                or slot["thinking"] is not (arm == "on") or slot["trial_id"] != sid):
            raise ValueError(f"Invalid or duplicate planned slot: {sid}")
        if slot["turn_seeds"] != [turn_seed(task, turn) for turn in range(1, 13)]:
            raise ValueError(f"Different paired seeds: {sid}")
        # Hashes present in the materialized plan must agree across both arms and models.
        identity = tuple(slot.get(k) for k in ("scene_id", "task_family", "fixture_sha256", "snapshot_sha256",
                                                "timeline_sha256", "command_sha256", "checker_sha256"))
        if not identity[0] or tasks.setdefault(task, identity) != identity:
            raise ValueError(f"Task crosses scenes or has different paired inputs: {task}")
        combinations.add(key)
        frozen[sid] = slot
    if (len(tasks) != DENOMINATOR or len({v[0] for v in tasks.values()}) != 5
            or len(frozen) != 4 * DENOMINATOR):
        raise ValueError("Expected four arms over the same 29 tasks in five parent scenes")
    actual = {}
    for row in rows:
        sid = row["slot_id"]
        if sid in actual or sid not in frozen:
            raise ValueError(f"Duplicate or unplanned result: {sid}")
        if any(k not in row or type(row[k]) is not type(v) or row[k] != v for k, v in frozen[sid].items()):
            raise ValueError(f"Result identity differs from frozen plan: {sid}")
        actual[sid] = row
    return frozen, actual


def _record(slot, row):
    status = row["status"] if row is not None else "missing"
    result = (row or {}).get("result")
    if status in ("scored", "unscored"):
        if (not isinstance(result, dict) or result.get("trial_id") != slot["trial_id"]
                or result.get("task_id") != slot["task_id"] or result.get("status") != status):
            raise ValueError(f"Terminal result identity/status mismatch: {slot['slot_id']}")
        if status == "scored":
            seal = result.get("scorer_seal_sha256")
            if (type(result.get("passed")) is not bool or not isinstance(seal, str)
                    or re.fullmatch(r"[0-9a-f]{64}", seal) is None):
                raise ValueError(f"Expected independent checker result and seal: {slot['slot_id']}")
        elif result.get("passed") is not None:
            raise ValueError(f"Unscored slot claims a checker result: {slot['slot_id']}")
    elif (row is not None and status not in ("unstarted", "started")) or result is not None:
        raise ValueError(f"Invalid nonterminal slot: {slot['slot_id']}")
    result = result or {}
    stop = result.get("stop_reason")
    if stop is not None and (not isinstance(stop, str) or not stop):
        raise ValueError(f"Invalid first stop reason: {slot['slot_id']}")
    if stop == "task_deadline" or (stop == "request_timeout" and result.get("request_limited_by_episode") is True):
        stop = "episode_deadline"
    service = result.get("service_ok")
    context = result.get("context_fits")
    if any(value is not None and type(value) is not bool for value in (service, context)):
        raise ValueError(f"Invalid service or context status: {slot['slot_id']}")
    score = result.get("passed")
    infrastructure = service is False or stop in INFRA_STOPS
    attribution = None
    if score is False:
        evidence = result.get("failure_evidence") or {}
        if infrastructure or context is False or stop in (*TIME_STOPS, *COST_STOPS):
            attribution = "outside_task_attribution"
        elif (service is True and context is True and evidence.get("kind") in {"decision_error", "tool_format"}
              and all(isinstance(evidence.get(k), str) and evidence[k].strip() for k in ("detail", "reference"))):
            attribution = "attributable"
        else:
            attribution = "unknown"
    return {"slot_id": slot["slot_id"], "task_id": slot["task_id"], "scene_id": slot["scene_id"],
            "status": status, "score": score, "stop": stop, "attribution": attribution,
            "pair_eligible": score is not None and not infrastructure, "result": result}


def _number(value, integer=False):
    allowed = (int,) if integer else (int, float)
    if value is not None and (type(value) not in allowed or not isfinite(value) or value < 0):
        raise ValueError(f"Invalid measured value: {value!r}")
    return value


def _quantiles(data):
    known = sorted(x for x in data if x is not None)
    size = len(known)
    return {"n": size, "missing": len(data) - size,
            **{f"p{q}": known[ceil(size * q / 100) - 1] if size else None for q in (50, 95)}}


def _measurements(records):
    usage = [r["result"].get("usage") or {} for r in records]
    totals = {}
    for field in ("prompt_tokens", "reasoning_tokens", "completion_tokens", "total_tokens"):
        values = [_number(u.get(field), integer=True) for u in usage]
        known = [v for v in values if v is not None]
        partial = [_number(u.get("known_" + field), integer=True)
                   for u, value in zip(usage, values) if value is None]
        totals[field] = {"known_sum": sum(known) + sum(v for v in partial if v is not None),
                         "measured_slots": len(known), "unknown_slots": len(records) - len(known)}
    for u in usage:
        p, c, r, t = (u.get(key) for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"))
        if ((p is not None and c is not None and t is not None and p + c != t)
                or (r is not None and c is not None and r > c)):
            raise ValueError("Inconsistent token usage")
    latency = []
    for u in usage:
        timings = u.get("request_timings", [])
        n = _number(u.get("requests", len(timings)), integer=True)
        if n is None or n < len(timings):
            raise ValueError("More request timings than requests")
        latency.extend(_number(entry.get("round_trip_seconds")) for entry in timings)
        latency.extend([None] * (n - len(timings)))
    totals["request_seconds"] = _quantiles(latency)
    totals["episode_seconds"] = _quantiles([_number(u.get("elapsed_seconds")) for u in usage])
    for field in ("peak_prompt", "peak_context", "peak_vram_bytes"):
        known = [_number(u[field], integer=True) for u in usage if u.get(field) is not None]
        totals[field] = {"maximum": max(known, default=None), "measured_slots": len(known)}
    history = [v for u in usage for v in u.get("history_reasoning_tokens_by_request", [])]
    if len(history) > len(latency):
        raise ValueError("More historical reasoning measurements than requests")
    totals["history_reasoning_tokens"] = {
        "known_sum": sum(_number(v, integer=True) for v in history if v is not None),
        "measured_requests": sum(v is not None for v in history), "unknown_requests": len(latency) - sum(
            v is not None for v in history)}
    return totals


def _arm(records):
    stops = Counter(r["stop"] for r in records if r["stop"])
    statuses = Counter(r["status"] for r in records)
    failures = [r["slot_id"] for r in records if r["attribution"] == "attributable"]
    return {"planned": len(records), "scored": statuses["scored"],
            "success": sum(r["score"] is True for r in records), "attributable_failures": len(failures),
            "failure_slot_ids": failures, "attribution_unknown": sum(r["attribution"] == "unknown" for r in records),
            "status_counts": dict(statuses), "stop_counts": {**dict.fromkeys((*TIME_STOPS, *COST_STOPS), 0), **stops,
                                                           "any": sum(stops[k] for k in TIME_STOPS)},
            "agent_claimed_done": sum(r["result"].get("agent_claimed_done") is True for r in records),
            "harness_completed": sum(r["result"].get("harness_completed") is True for r in records),
            "usage": _measurements(records)}


def _g1(arm):
    success, failures = arm["success"], arm["attributable_failures"]
    missing = arm["planned"] - arm["scored"]
    unresolved = arm["attribution_unknown"]
    # Each unscored task can add at most one success OR one attributable failure.
    missing_needed = max(0, 3 - success) + max(0, 3 - failures - unresolved)
    if success >= 3 and failures >= 3:
        status, reasons = "pass", ["observed_successes_and_attributable_failures"]
    elif missing_needed > missing:
        status, reasons = "fail", ["threshold_unreachable"]
    else:
        status, reasons = "indeterminate", ["missing_score_or_failure_attribution"]
    return {"status": status, "reasons": reasons, "denominator": arm["planned"], "success": success,
            "attributable_failures": failures, "attribution_unknown": unresolved, "unscored": missing,
            "success_bounds": [success, success + missing],
            "attributable_failure_bounds": [failures, failures + unresolved + missing]}


def _cell(on, off):
    if not on["pair_eligible"] or not off["pair_eligible"]:
        return "missing"
    return {(True, True): "both_success", (True, False): "only_on",
            (False, True): "only_off", (False, False): "both_failure"}[on["score"], off["score"]]


def _cell_counts(pairs):
    counts = Counter(p["cell"] for p in pairs)
    return {"four_cells": {k: counts[k] for k in CELLS}, "missing_pairs": counts["missing"],
            "net_gain": counts["only_on"] - counts["only_off"]}


def _pair_summary(on, off):
    return {"task_id": on["task_id"], "scene_id": on["scene_id"], "cell": _cell(on, off),
            "on_slot": on["slot_id"], "off_slot": off["slot_id"],
            **{arm: {"status": r["status"], "checker_passed": r["score"], "stop_reason": r["stop"],
                     "attribution": r["attribution"], "scorer_seal_sha256": r["result"].get("scorer_seal_sha256"),
                     "failure_evidence": r["result"].get("failure_evidence"),
                     "unscored_reason": r["result"].get("unscored_reason"),
                     "peak_context": (r["result"].get("usage") or {}).get("peak_context")}
               for arm, r in (("on", on), ("off", off))}}


def _model(records):
    arms = {arm: _arm([value for (task, key), value in records.items() if key == arm]) for arm in ("on", "off")}
    pairs = [_pair_summary(on, records[task, "off"]) for (task, arm), on in records.items() if arm == "on"]
    pairs.sort(key=lambda p: (p["scene_id"], p["task_id"]))
    by_scene = [{"scene_id": scene, **_cell_counts([p for p in pairs if p["scene_id"] == scene])}
                for scene in sorted({p["scene_id"] for p in pairs})]
    stop_pairs = {}
    for name in (*TIME_STOPS, "any"):
        codes = set(TIME_STOPS if name == "any" else (name,))
        # With no observation on the opposite side a recorded cutoff is an arm
        # count, but its exclusive attribution within the pair is unknown.
        flags = [(p["on"]["stop_reason"] in codes, p["off"]["stop_reason"] in codes) for p in pairs
                 if all(p[k]["status"] == "scored" or p[k]["stop_reason"] is not None for k in ("on", "off"))]
        counts = Counter(flags)
        stop_pairs[name] = {"only_on": counts[True, False], "only_off": counts[False, True],
                            "both": counts[True, True], "unknown": len(pairs) - len(flags)}
    return {"arms": arms, "pairs": pairs, "by_scene": by_scene, **_cell_counts(pairs),
            "improved_scenes": sum(row["net_gain"] > 0 for row in by_scene), "stop_pairs": stop_pairs}


def score_pairs(plan, rows):
    """Return descriptive results for both models and gate decisions from the primary only."""
    frozen, actual = _index(plan, rows)
    groups = {model: {} for model in MODELS}
    for slot_id, slot in frozen.items():
        groups[slot["model"]][slot["task_id"], slot["arm"]] = _record(slot, actual.get(slot_id))
    summaries = {key: _model(group) for key, group in groups.items()}
    main = summaries[PRIMARY]
    reasons = []
    if main["missing_pairs"]:
        reasons.append("incomplete_pairs")
    bias = [name for name, stats in main["stop_pairs"].items() if stats["only_on"] > stats["only_off"]]
    if bias:
        reasons.append("open_arm_disproportionate_stops")
    if reasons:
        status = "indeterminate"
    elif main["net_gain"] >= 3 and main["improved_scenes"] >= 2:
        status, reasons = "pass", ["net_gain_and_scene_thresholds_met"]
    else:
        status, reasons = "fail", ["net_gain_or_scene_threshold_not_met"]
    return {"primary_model": PRIMARY, "reference_model": REFERENCE, "by_model": summaries,
            "G1": _g1(main["arms"]["on"]),
            "G2": {"status": status, "reasons": reasons, "open_arm_excess": bias,
                   "net_gain": main["net_gain"], "improved_scenes": main["improved_scenes"],
                   "missing_pairs": main["missing_pairs"]}}
