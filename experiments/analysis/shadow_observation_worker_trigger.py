#!/usr/bin/env python3
"""Replay M1-R2 fast100 receipts and audit bounded observation-worker triggers.

The shadow policy reads command text and public tool receipts through the same
R2 ledger used online. Outcome labels are joined only after trigger decisions.
It does not call a model, server, checker, or worker.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2


PAIR_FILE = ROOT / "docs/results/m1-revision2-fast100-20261007/paired-tasks.json"
OUT_DIR = ROOT / "docs/audit/m1r2-observation-worker-shadow-20261009"
SENSING_MOVES = {
    "navigate_to_and_sense", "move_along_path_and_sense",
    "move_to_and_sense", "move_towards_and_sense",
}
ACTION_MARKERS = ("move", "navigate", "take_off", "land")
THRESHOLDS = (3, 5, 8)
VARIANTS = ("stall", "stall_no_ready")
KNOWLEDGE_RANK = {"unknown": 0, "reported_only": 1, "localized": 2}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visit_projection(ledger):
    return {item["id"]: item for item in ledger.projection()
            if item["relation"] == "visit" and item.get("actor_id")}


def evidence_progress(before, after):
    """Actor IDs with a newly reported/localized target or completed visit."""
    actors = set()
    for key, now in after.items():
        old = before.get(key)
        if old is None:
            continue
        knowledge_advanced = (KNOWLEDGE_RANK.get(now["knowledge_state"], -1)
                              > KNOWLEDGE_RANK.get(old["knowledge_state"], -1))
        visit_evidenced = (old["execution_state"] != "evidenced"
                           and now["execution_state"] == "evidenced")
        if knowledge_advanced or visit_evidenced:
            actors.add(now["actor_id"])
    return actors


def candidate_state(visits, actor):
    own = [item for item in visits.values() if item["actor_id"] == actor]
    unresolved = [item["target_name"] for item in own
                  if item["knowledge_state"] != "localized"]
    ready = [item["target_name"] for item in own
             if item["knowledge_state"] == "localized"
             and item["execution_state"] != "evidenced"]
    return unresolved, ready


def api_match(event, api):
    """Match one direct movement request, never infer tool completion from it."""
    actor = event["args"].get("drone_id")
    endpoint = api["endpoint"]
    if not actor or f"/drones/{actor}/command/" not in endpoint:
        return False
    tool = event["tool_name"]
    args = event["args"]
    if tool in {"navigate_to_and_sense", "move_to_and_sense"}:
        return endpoint.endswith("/move_to") and all(
            (api.get("params") or {}).get(key) == args.get(key)
            for key in ("x", "y", "z"))
    if tool == "move_towards_and_sense":
        return endpoint.endswith("/move_towards") and all(
            (api.get("params") or {}).get(key) == args.get(key)
            for key in ("distance", "heading"))
    if tool == "move_along_path_and_sense":
        return (endpoint.endswith("/move_along_path")
                and (api.get("json") or {}).get("waypoints") == args.get("waypoints"))
    return False


def time_bounds(trace, event_id):
    """Conservative bracket from logged HTTP commands, if uniquely matchable."""
    events = trace["task_progress"]["events"]
    event = next(e for e in events if e["evidence_id"] == event_id)
    matches = [a for a in trace["api_calls"] if api_match(event, a)]
    if len(matches) != 1 or event["overlaps"]:
        return {"status": "unmatched_or_ambiguous"}
    direct = matches[0]
    begin = datetime.fromisoformat(trace["started_at"])
    lower = datetime.fromisoformat(direct["completed_at"])
    if lower < begin:
        return {"status": "inconsistent_time"}
    later = []
    for next_event in events:
        if (next_event["start_sequence"] <= event["end_sequence"]
                or next_event["args"].get("drone_id") != event["args"].get("drone_id")
                or next_event["tool_name"] not in SENSING_MOVES):
            continue
        later.extend(a for a in trace["api_calls"] if api_match(next_event, a)
                     and datetime.fromisoformat(a["started_at"]) > lower)
    upper = min((datetime.fromisoformat(a["started_at"]) for a in later),
                default=datetime.fromisoformat(trace["completed_at"]))
    return {
        "status": "bounded_by_direct_request_and_next_same_actor_sensing_move",
        "direct_request_index": direct["index"],
        "elapsed_after_direct_command_s": round((lower - begin).total_seconds(), 3),
        "elapsed_before_next_move_or_task_end_s": round((upper - begin).total_seconds(), 3),
        "deadline_s": 500,
    }


def replay(row, index):
    path = ROOT / row["m1r2_trace"]
    trace = read(path)
    events = trace["task_progress"]["events"]
    ledger = TaskEvidenceLedgerR2(trace["command"], trace["command_id"],
                                  index=index, max_summary_chars=3900)
    visits = visit_projection(ledger)
    required_actors = {item["actor_id"] for item in visits.values()}
    counts = defaultdict(int)
    extra_action_seen = False
    snapshots = 0
    firings = {variant: {str(k): None for k in THRESHOLDS}
               for variant in VARIANTS}
    timeline = [(e["start_sequence"], "start", e) for e in events]
    timeline += [(e["end_sequence"], "end", e) for e in events
                 if e["end_sequence"] is not None]
    for sequence, kind, event in sorted(timeline, key=lambda value: value[0]):
        if kind == "start":
            assert ledger.begin_tool(event["tool_name"], event["args"],
                                     event["tool_call_id"]) == event["evidence_id"]
            continue
        before = visit_projection(ledger)
        payload = event["payload"]
        if isinstance(payload, dict) and set(payload) == {"tool_exception"}:
            ledger.end_tool(event["evidence_id"], error=payload["tool_exception"])
        else:
            ledger.end_tool(event["evidence_id"], payload)
        visits = visit_projection(ledger)
        advanced_actors = evidence_progress(before, visits)
        for actor in advanced_actors:
            counts[actor] = 0
        actor = event["args"].get("drone_id")
        if (actor and actor not in required_actors
                and any(part in event["tool_name"] for part in ACTION_MARKERS)):
            extra_action_seen = True
        if (actor in required_actors and event["tool_name"] in SENSING_MOVES
                and event["status"] == "completed"
                and actor not in advanced_actors):
            counts[actor] += 1
        if ledger._active:
            continue  # A model cannot use a partial parallel tool batch.
        snapshots += 1
        for actor in sorted(required_actors):
            unresolved, ready = candidate_state(visits, actor)
            if not unresolved or extra_action_seen:
                continue
            for k in THRESHOLDS:
                if counts[actor] < k:
                    continue
                for variant in VARIANTS:
                    if variant == "stall_no_ready" and ready:
                        continue
                    if firings[variant][str(k)] is None:
                        firings[variant][str(k)] = {
                            "actor_id": actor,
                            "event_id": event["evidence_id"],
                            "end_sequence": sequence,
                            "sensing_moves_since_progress": counts[actor],
                            "unresolved_targets": unresolved,
                            "ready_targets": ready,
                            "later_completed_events": sum(
                                e["end_sequence"] is not None
                                and e["end_sequence"] > sequence for e in events),
                        }
    saved = trace["task_progress"]["task_evidence"]["requirements"]
    actual = ledger.projection()
    assert [(q["id"], q["knowledge_state"], q["execution_state"])
            for q in actual] == [(q["id"], q["knowledge_state"], q["execution_state"])
                              for q in saved], row["task_id"]
    snapshot = ledger.snapshot()
    assert not snapshot["observer_errors"] and len(snapshot["events"]) == len(events)
    for variant in VARIANTS:
        for k in THRESHOLDS:
            fired = firings[variant][str(k)]
            if fired:
                fired["request_time_bounds"] = time_bounds(trace, fired["event_id"])
    return {
        "session_id": row["session_id"],
        "task_id": row["task_id"],
        "trace": row["m1r2_trace"],
        "trace_sha256": sha(path),
        "required_actor_ids": sorted(required_actors),
        "explicit_visit_count": len(visits),
        "checkpoint_count": snapshots,
        "event_count": len(events),
        "extra_action_ever_seen": extra_action_seen,
        "firings": firings,
    }


def main():
    pairs = read(PAIR_FILE)
    assert len(pairs) == 100
    plan_path = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403/plan.json"
    plan = read(plan_path)
    assert [(p["session_id"], p["task_id"]) for p in pairs] == [
        (session["id"], task["id"])
        for session in plan["sessions"] for task in session["tasks"]]
    indexes = {}
    decisions = []
    for pair in pairs:
        index = indexes.setdefault(pair["session_id"],
                                   SessionEvidenceIndexR2(pair["session_id"]))
        identity = {key: pair[key] for key in ("session_id", "task_id", "m1r2_trace")}
        decisions.append(replay(identity, index))
    # Outcome labels are joined after every decision has been computed.
    for decision, pair in zip(decisions, pairs):
        decision["r2_pass_offline_label"] = pair["m1r2_pass"]
        decision["r2_timeout_offline_label"] = pair["m1r2_timeout"]
        decision["m0_pass_offline_label"] = pair["m0_pass"]
    assert sum(d["r2_pass_offline_label"] for d in decisions) == 79
    summary = {"schema": "m1r2-observation-worker-shadow-v1",
               "tasks": len(decisions), "scenes": len(indexes),
               "explicit_visit_tasks": sum(bool(d["explicit_visit_count"]) for d in decisions),
               "thresholds": list(THRESHOLDS), "variants": list(VARIANTS),
               "input_sha256": {str(PAIR_FILE.relative_to(ROOT)): sha(PAIR_FILE),
                                str(plan_path.relative_to(ROOT)): sha(plan_path)},
               "source_sha256": {
                   str(path.relative_to(ROOT)): sha(path)
                   for path in (ROOT / "agent4drone/task_evidence.py",
                                ROOT / "agent4drone/task_evidence_r2.py",
                                ROOT / "agent4drone/task_progress.py")},
               "counts": {}}
    for variant in VARIANTS:
        summary["counts"][variant] = {}
        for k in THRESHOLDS:
            triggered = [d for d in decisions if d["firings"][variant][str(k)]]
            summary["counts"][variant][str(k)] = {
                "total": len(triggered),
                "passed": sum(d["r2_pass_offline_label"] for d in triggered),
                "failed": sum(not d["r2_pass_offline_label"] for d in triggered),
                "task_ids": [d["task_id"] for d in triggered],
            }
    candidate = [d for d in decisions if d["firings"]["stall_no_ready"]["8"]]
    budget_feasible = [d for d in candidate if (
        d["firings"]["stall_no_ready"]["8"]["request_time_bounds"]["status"]
        == "bounded_by_direct_request_and_next_same_actor_sensing_move"
        and d["firings"]["stall_no_ready"]["8"]["request_time_bounds"]
        ["elapsed_before_next_move_or_task_end_s"] <= 320)]
    summary["candidate_k8_180s_budget"] = {
        "shadow_firings": len(candidate),
        "time_bracket_consistent_with_budget": len(budget_feasible),
        "task_ids": [d["task_id"] for d in budget_feasible],
        "worker_results_observed": 0,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
