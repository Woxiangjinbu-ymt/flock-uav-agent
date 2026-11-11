#!/usr/bin/env python3
"""Measure local-server request time inside all six R2 500-second timeouts.

This does not equate non-API time with model inference; it is an observed
upper bound for model, local agent work and scheduling combined.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ATTEMPT = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403"
OPPORTUNITIES = ROOT / "docs/audit/m1r2-action-opportunities-20261009/rows.json"
OUTPUT = ROOT / "docs/audit/r3-original-recovery-20261009/r2-timeout-local-api.json"


def read(path: Path):
    return json.loads(path.read_text())


def main():
    scored = [row for row in read(ATTEMPT / "results.json") if row["agent_timed_out"]]
    opportunity_rows = {row["task_id"]: row for row in read(OPPORTUNITIES)}
    if len(scored) != 6:
        raise ValueError("Expected six R2 timeouts")
    rows = []
    for row in scored:
        scene_id, task_id = row["session_id"], row["task_id"]
        directory = ATTEMPT / scene_id
        job = read(directory / f"{task_id}.job.json")
        window = read(directory / f"{task_id}.window.json")
        created_at = datetime.fromisoformat(job["created_at"])
        opened_at = datetime.fromtimestamp(window["opened_at"])
        cutoff_at = datetime.fromtimestamp(window["cutoff_at"])
        if (job["status"] != "cancelled" or abs((opened_at - created_at).total_seconds()) > 5
                or abs((cutoff_at - opened_at).total_seconds() - 500) > 0.001):
            raise ValueError(f"Task clock/status changed: {task_id}")
        candidates = []
        for path in (directory / "agent-traces/toolchains").glob("*_exception.json"):
            trace = read(path)
            if abs((datetime.fromisoformat(trace["started_at"]) - created_at).total_seconds()) < 5:
                candidates.append((path, trace))
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one timeout trace by start time: {task_id}")
        path, trace = candidates[0]
        admitted = []
        after_cutoff = 0
        for call in trace["api_calls"]:
            start = datetime.fromisoformat(call["started_at"])
            end = datetime.fromisoformat(call["completed_at"])
            if start >= cutoff_at:
                after_cutoff += 1
                continue
            admitted.append((start, min(end, cutoff_at), call))
        if not admitted or any(end < start for start, end, _ in admitted):
            raise ValueError(f"Invalid saved local API interval: {task_id}")
        intervals = sorted((start, end) for start, end, _ in admitted)
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(merged[-1][1], end)
            else:
                merged.append((start, end))
        api_wall_seconds = sum((end - start).total_seconds() for start, end in merged)
        interval_sum_seconds = sum((end - start).total_seconds() for start, end, _ in admitted)
        opportunity = opportunity_rows[task_id]
        rows.append({"session_id": scene_id, "task_id": task_id, "name": row["name"],
                     "original_checks_passed": sum(leaf["result"] for leaf in row["leaves"]),
                     "original_checks_total": len(row["leaves"]),
                     "window_seconds": 500.0, "local_api_calls_started_before_cutoff": len(admitted),
                     "local_api_interval_sum_seconds": round(interval_sum_seconds, 3),
                     "local_api_union_wall_seconds": round(api_wall_seconds, 3),
                     "local_api_union_fraction": round(api_wall_seconds / 500, 5),
                     "time_outside_recorded_local_api_union_seconds": round(500 - api_wall_seconds, 3),
                     "recorded_local_api_calls_after_cutoff": after_cutoff,
                     "recorded_wire_model_requests_full_trace": len(trace["model_requests_wire"]),
                     "last_public_progress_percent": opportunity["last_public_progress_percent"],
                     "action_actor_count": len(opportunity["action_actor_ids"]),
                     "trace": str(path.relative_to(ROOT))})
    result = {"schema": 1, "source": "R2 fast100 frozen first run, all six scored timeouts",
              "measurement": "Clip recorded agent-to-local-server API call intervals to the original 500s admission window, then union overlaps",
              "limits": ["Remainder includes model/network waiting, local agent computations and scheduling; cannot split them with this trace alone.",
                         "Wire request count includes the entire saved trace and may include requests after the 500s cutoff.",
                         "A faster local API alone cannot make unreachable targets or blocked coordinates feasible."],
              "rows": rows,
              "summary": {"timeouts": len(rows),
                          "max_local_api_interval_sum_seconds": max(row["local_api_interval_sum_seconds"] for row in rows),
                          "max_local_api_union_wall_seconds": max(row["local_api_union_wall_seconds"] for row in rows),
                          "min_local_api_union_wall_seconds": min(row["local_api_union_wall_seconds"] for row in rows),
                          "aggregate_progress_timeout_tasks": sum(row["last_public_progress_percent"] is not None
                                                                  for row in rows)}}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"summary": result["summary"], "tasks": [
        {key: row[key] for key in ("task_id", "local_api_union_wall_seconds",
                                    "recorded_wire_model_requests_full_trace", "last_public_progress_percent")}
        for row in rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
