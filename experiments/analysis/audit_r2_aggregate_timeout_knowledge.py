#!/usr/bin/env python3
"""Audit model-visible target localization in the three R2 aggregate timeouts.

Only completed tool-event payloads establish what was exposed to the agent.
Full scene snapshots are used afterwards to evaluate coverage/status, never to
add a target to the reconstructed public knowledge set.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403/823c15d5"
OUTPUT = ROOT / "docs/audit/r2-aggregate-timeout-knowledge-20261009/rows.json"
TASK_IDS = ("5e743d21", "24ac1838", "7cdefb56")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def has_position(entity: dict) -> bool:
    value = entity.get("position")
    return isinstance(value, dict) and "x" in value and "y" in value or (
        isinstance(value, (list, tuple)) and len(value) >= 2
    )


def trace_for(task_id: str):
    job = read(RUN / f"{task_id}.job.json")
    created = datetime.fromisoformat(job["created_at"])
    candidates = []
    for path in (RUN / "agent-traces/toolchains").glob("*_exception.json"):
        trace = read(path)
        if abs((datetime.fromisoformat(trace["started_at"]) - created).total_seconds()) < 5:
            candidates.append((path, trace))
    if job["status"] != "cancelled" or len(candidates) != 1:
        raise ValueError(f"Expected one cancelled task with one exception trace: {task_id}")
    return candidates[0]


def add_visible_targets(event: dict, known: dict) -> None:
    """Use only target positions actually present in completed tool output."""
    payload = event.get("payload")
    if not isinstance(payload, dict) or event.get("status") == "error":
        return
    name = event["tool_name"]
    if name == "sense_nearby_entities":
        board = payload.get("blackboard", {})
        for target in board.get("targets", []) if isinstance(board, dict) else []:
            facts = target.get("facts", {})
            if target.get("id") and has_position(facts):
                known[str(target["id"])] = {
                    "name": target.get("name"), "latest_status": facts.get("is_reached"),
                    "latest_status_source": "blackboard", "latest_sequence": event["end_sequence"],
                }
        for nearby in payload.get("per_drone", {}).values():
            if not isinstance(nearby, dict):
                continue
            for target in nearby.get("targets", []):
                if target.get("id") and has_position(target):
                    known[str(target["id"])] = {
                        "name": target.get("name"), "latest_status": target.get("is_reached"),
                        "latest_status_source": "nearby", "latest_sequence": event["end_sequence"],
                    }
    elif name == "get_target_info" and payload.get("id") and has_position(payload):
        known[str(payload["id"])] = {
            "name": payload.get("name"), "latest_status": payload.get("is_reached"),
            "latest_status_source": "target_info", "latest_sequence": event["end_sequence"],
        }
    elif name == "get_nearby_entities":
        for target in payload.get("targets", []):
            if target.get("id") and has_position(target):
                known[str(target["id"])] = {
                    "name": target.get("name"), "latest_status": target.get("is_reached"),
                    "latest_status_source": "nearby", "latest_sequence": event["end_sequence"],
                }


def checkpoint(event: dict, known: dict, after: dict, elapsed: float, remaining: float) -> dict:
    pct = event["payload"]["progress_percentage"]
    if event["payload"]["details"]["total_targets"] != 100:
        raise ValueError("Unexpected denominator")
    visible = set(known)
    post_unreached = {tid for tid, target in after.items() if not target["is_reached"]}
    return {
        "progress_percent": pct, "end_sequence": event["end_sequence"],
        "elapsed_seconds": round(elapsed, 3), "remaining_seconds": round(remaining, 3),
        "localized_target_count": len(visible),
        "memory_status_false_count": sum(v["latest_status"] is False for v in known.values()),
        "memory_status_unknown_count": sum(v["latest_status"] is None for v in known.values()),
        # Post-run oracle evaluation; these fields did not exist for the agent.
        "post_run_unreached_count": len(post_unreached),
        "post_run_unreached_localized_count": len(post_unreached & visible),
        "post_run_unreached_unlocalized_count": len(post_unreached - visible),
        "post_run_unreached_localized_ids": sorted(post_unreached & visible),
    }


def audit(task_id: str) -> dict:
    trace_path, trace = trace_for(task_id)
    before = {t["id"]: t for t in read(RUN / f"{task_id}.before.json")["targets"]}
    after = {t["id"]: t for t in read(RUN / f"{task_id}.after.json")["targets"]}
    if set(before) != set(after) or len(before) != 100:
        raise ValueError("Target identity changed")
    window = read(RUN / f"{task_id}.window.json")
    opened = datetime.fromtimestamp(window["opened_at"])
    cutoff = datetime.fromtimestamp(window["cutoff_at"])
    if abs((cutoff - opened).total_seconds() - 500) > 0.001:
        raise ValueError("Budget changed")
    after_cutoff_actions = [call for call in trace["api_calls"]
                            if call["method"] == "POST"
                            and "/command/" in call["endpoint"]
                            and datetime.fromisoformat(call["started_at"]) >= cutoff]
    if any(call["status_code"] != 403 for call in after_cutoff_actions):
        raise ValueError("An action may have been accepted after the 500s cutoff")
    events = sorted((e for e in trace["task_progress"]["events"]
                     if isinstance(e.get("end_sequence"), int)),
                    key=lambda e: e["end_sequence"])
    progress_events = [e for e in events if e["tool_name"] == "get_mission_progress"
                       and isinstance(e.get("payload"), dict)]
    session_calls = [call for call in trace["api_calls"] if call["endpoint"] == "/sessions/current"]
    if len(session_calls) != len(progress_events) + 1:
        raise ValueError(f"Cannot align progress timestamps: {task_id}")
    if len(progress_events) not in (5, 10):
        raise ValueError("Progress call count changed")
    known: dict[str, dict] = {}
    checkpoints = []
    first_sense = None
    progress_index = 0
    for event in events:
        add_visible_targets(event, known)
        if (first_sense is None and event["tool_name"] == "sense_nearby_entities"
                and isinstance(event.get("payload"), dict) and event.get("status") == "observed"):
            first_sense = dict(known)
        if event["tool_name"] != "get_mission_progress" or not isinstance(event.get("payload"), dict):
            continue
        call = session_calls[progress_index + 1]  # first is command startup lookup
        end = datetime.fromisoformat(call["completed_at"])
        if end > cutoff:
            raise ValueError("Progress after action cutoff")
        checkpoints.append(checkpoint(event, known, after,
                                      (end - opened).total_seconds(),
                                      (cutoff - end).total_seconds()))
        progress_index += 1
    if first_sense is None or progress_index != len(progress_events):
        raise ValueError("Missing initial sensing/progress")
    first_false = {tid for tid, item in first_sense.items() if item["latest_status"] is False}
    stale_false = first_false & {tid for tid, item in before.items() if item["is_reached"]}
    pre_unreached = {tid for tid, item in before.items() if not item["is_reached"]}
    post_unreached = {tid for tid, item in after.items() if not item["is_reached"]}
    last = checkpoints[-1]
    if last["progress_percent"] > 95:
        raise ValueError("Task should be a below-threshold timeout")
    return {
        "session_id": "823c15d5", "task_id": task_id,
        "trace": str(trace_path.relative_to(ROOT)),
        "initial_public_sense": {
            "localized_target_count": len(first_sense),
            "memory_status_false_count": len(first_false),
            "memory_status_false_but_already_reached_before_task_count": len(stale_false),
            "oracle_before_unreached_count": len(pre_unreached),
            "oracle_before_unreached_localized_count": len(pre_unreached & set(first_sense)),
        },
        "checkpoints": checkpoints,
        "post_run_oracle": {
            "reached_count": 100 - len(post_unreached),
            "unreached_count": len(post_unreached),
            "unreached_localized_at_last_public_progress_count": last["post_run_unreached_localized_count"],
            "unreached_unlocalized_at_last_public_progress_count": last["post_run_unreached_unlocalized_count"],
            "unreached_localized_ids": last["post_run_unreached_localized_ids"],
        },
        "model_wire_requests_full_trace": len(trace["model_requests_wire"]),
        "tool_events_full_trace": len(events),
        "rejected_action_calls_after_cutoff": len(after_cutoff_actions),
    }


def main() -> None:
    rows = [audit(task_id) for task_id in TASK_IDS]
    result = {
        "schema": 1,
        "method": "Reconstruct known target IDs/positions from completed, model-visible tool events in end_sequence order; align aggregate-progress events to public session API calls by call order; use before/after snapshots only for retrospective evaluation.",
        "limits": [
            "Snapshot reached flags at command end are retrospective; they do not give the agent a per-target status at each checkpoint.",
            "A target localized in prior prompt text but absent from saved completed tool payloads may be missed; the session blackboard is included when returned by sensing.",
            "A stale false status shows a time-of-observation mismatch; it does not by itself prove the agent acted on it.",
            "No causal runtime improvement or counterfactual task success is inferred from this audit.",
        ],
        "rows": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        first, last = row["initial_public_sense"], row["checkpoints"][-1]
        print(row["task_id"], "start_known", first["localized_target_count"],
              "start_stale_false", first["memory_status_false_but_already_reached_before_task_count"],
              "last_progress", last["progress_percent"],
              "last_known", last["localized_target_count"],
              "post_missing_known", last["post_run_unreached_localized_count"],
              "post_missing_unknown", last["post_run_unreached_unlocalized_count"])


if __name__ == "__main__":
    main()
