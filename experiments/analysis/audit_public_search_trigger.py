#!/usr/bin/env python3
"""Shadow the opt-in R2 search gate on six matching public trajectories.

Trigger inputs are task text, timestamp, and progress values visible to the
agent. Original task checks are joined only after trigger decisions are fixed.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from coordinated_search_trigger_r2 import (  # noqa: E402
    EARLIEST_ELAPSED_S, MIN_REMAINING_S, decide_public_search_trigger,
)

RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403"
OUT = ROOT / "docs/audit/r2-public-search-trigger-20261009/shadow.json"
IDS = ("9c0b52a0", "7eba7390", "b1ad4f3e",
       "5e743d21", "24ac1838", "7cdefb56")
SENSITIVITY_STARTS = (300.0, 350.0, 400.0, 450.0)


def read(path: Path):
    return json.loads(path.read_text())


def trace_for(folder: Path, task_id: str):
    created = datetime.fromisoformat(read(folder / f"{task_id}.job.json")["created_at"])
    matches = []
    for path in (folder / "agent-traces/toolchains").glob("*.json"):
        if not (path.name.endswith("_completed.json") or
                path.name.endswith("_exception.json")):
            continue
        trace = read(path)
        if abs((datetime.fromisoformat(trace["started_at"]) - created).total_seconds()) < 5:
            matches.append((path, trace))
    if len(matches) != 1:
        raise ValueError(f"Expected one tool trace for {task_id}, got {len(matches)}")
    return matches[0]


def query_receipts(trace: dict, opened: datetime, cutoff: datetime):
    events = sorted((event for event in trace["task_progress"]["events"]
                     if event["tool_name"] == "get_mission_progress"
                     and isinstance(event.get("payload"), dict)),
                    key=lambda event: event["end_sequence"])
    calls = [call for call in trace["api_calls"]
             if call["endpoint"] == "/sessions/current"]
    if len(calls) != len(events) + 1:
        raise ValueError("Public progress calls do not align with tool receipts")
    receipts = []
    for event, call in zip(events, calls[1:]):
        end = datetime.fromisoformat(call["completed_at"])
        if end > cutoff:
            continue  # no gate may act after the original 500s action window
        receipts.append({"elapsed_s": round((end - opened).total_seconds(), 3),
                         "remaining_s": round((cutoff - end).total_seconds(), 3),
                         "progress_percent": event["payload"]["progress_percentage"],
                         "end_sequence": event["end_sequence"]})
    return receipts


def row_for(task_id: str) -> dict:
    matches = list(RUN.glob(f"*/{task_id}.before.json"))
    if len(matches) != 1:
        raise ValueError(f"Expected one task start for {task_id}")
    folder = matches[0].parent
    before = read(matches[0])
    task = next(item for item in before["tasks"] if item["id"] == task_id)
    window = read(folder / f"{task_id}.window.json")
    opened = datetime.fromtimestamp(window["opened_at"])
    cutoff = datetime.fromtimestamp(window["cutoff_at"])
    if abs((cutoff - opened).total_seconds() - 500.0) > 0.001:
        raise ValueError("Expected original 500s window")
    trace_path, trace = trace_for(folder, task_id)
    receipts = query_receipts(trace, opened, cutoff)
    if not receipts:
        raise ValueError(f"No model-visible progress receipt for {task_id}")
    decisions = [decide_public_search_trigger(
        enabled=True, task_content=task["content"],
        progress_percent=receipt["progress_percent"],
        elapsed_s=receipt["elapsed_s"]) for receipt in receipts]
    first_trigger = next((receipt for receipt, decision in zip(receipts, decisions)
                          if decision.activate), None)
    first_sense = next((event for event in trace["task_progress"]["events"]
                        if event["tool_name"] == "sense_nearby_entities"
                        and event.get("status") == "observed"
                        and isinstance(event.get("payload"), dict)), None)
    if first_trigger is not None and (first_sense is None or
                                      first_sense["end_sequence"] >= first_trigger["end_sequence"]):
        raise ValueError("Gate fired before the first public sensing receipt")
    checks = read(folder / f"{task_id}.checks.json")
    leaves = [item for item in checks if item["type"] == "leaf"]
    if len(leaves) != 1 or leaves[0]["endpoint"] != "/check/task_progress":
        raise ValueError(f"Unexpected original outcome check for {task_id}")
    sensitivity = {}
    for earliest in SENSITIVITY_STARTS:
        candidate = next((receipt for receipt in receipts
                          if earliest <= receipt["elapsed_s"] <= 500 - MIN_REMAINING_S
                          and receipt["progress_percent"] <= 95), None)
        sensitivity[str(int(earliest))] = candidate["elapsed_s"] if candidate else None
    return {"task_id": task_id, "scene_id": before["id"],
            "trace": str(trace_path.relative_to(ROOT)),
            "original_job_status": read(folder / f"{task_id}.job.json")["status"],
            "original_r2_check_passed": bool(leaves[0]["result"]),
            "public_task_text": task["content"],
            "first_public_sense_end_sequence": (first_sense["end_sequence"]
                                                 if first_sense else None),
            "progress_receipts": receipts,
            "first_trigger": first_trigger,
            "sensitivity_first_trigger_elapsed_s": sensitivity}


def main():
    all_starts = list(RUN.glob("*/*.before.json"))
    matching_ids = set()
    for path in all_starts:
        task_id = path.name.removesuffix(".before.json")
        task = next(item for item in read(path)["tasks"] if item["id"] == task_id)
        if decide_public_search_trigger(enabled=True, task_content=task["content"],
                                        progress_percent=0, elapsed_s=EARLIEST_ELAPSED_S).activate:
            matching_ids.add(task_id)
    if len(all_starts) != 100 or matching_ids != set(IDS):
        raise ValueError("Public task-text cohort changed")
    rows = [row_for(task_id) for task_id in IDS]
    output = {"schema": 1, "rule": {
        "explicit_enable_required": True,
        "public_task_text": "all targets AND session task progress exceeds 95%",
        "public_progress_percent_max": 95,
        "earliest_elapsed_s": EARLIEST_ELAPSED_S,
        "minimum_remaining_s": MIN_REMAINING_S,
        "evaluated_only_at_completed_public_progress_receipts": True},
        "frozen_run_task_count": len(all_starts),
        "public_text_matching_task_ids": sorted(matching_ids),
        "development_cohort_only": True,
        "rows": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUT), "rows": [{
        "task_id": row["task_id"],
        "original_passed": row["original_r2_check_passed"],
        "first_trigger_elapsed_s": (row["first_trigger"] or {}).get("elapsed_s"),
        "first_trigger_progress_percent": (row["first_trigger"] or {}).get("progress_percent"),
        "query_count": len(row["progress_receipts"])} for row in rows]},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
