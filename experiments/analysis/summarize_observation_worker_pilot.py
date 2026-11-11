#!/usr/bin/env python3
"""Summarize the frozen R2 versus observation-worker prefix pilot."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "experiments/m1r2-observation-worker-prefix-v2"
OUTPUT = ROOT / "docs/results/m1r2-observation-worker-prefix-20261009/summary.json"
sys.path.insert(0, str(ROOT / "agent4drone"))

from observation_worker_r2 import TaskEvidenceWorkerLedger, TaskEvidenceWorkerMiddleware
from task_evidence_r2 import SessionEvidenceIndexR2
import time


def read(path):
    return json.loads(Path(path).read_text())


def policy_replay(trace, index):
    ledger = TaskEvidenceWorkerLedger(trace["command"], trace["command_id"], index=index)
    middleware = TaskEvidenceWorkerMiddleware(ledger, lambda *_: None, time.monotonic())
    events = trace["task_progress"]["events"]
    timeline = [(event["start_sequence"], "start", event) for event in events]
    timeline.extend((event["end_sequence"], "end", event)
                    for event in events if event["end_sequence"] is not None)
    candidates = []
    for _sequence, kind, event in sorted(timeline, key=lambda item: item[0]):
        if kind == "start":
            assert ledger.begin_tool(event["tool_name"], event["args"], event["tool_call_id"]) == event["evidence_id"]
        elif isinstance(event["payload"], dict) and set(event["payload"]) == {"tool_exception"}:
            ledger.end_tool(event["evidence_id"], error=event["payload"]["tool_exception"])
        else:
            ledger.end_tool(event["evidence_id"], event["payload"])
        if kind == "end" and not ledger._active:
            candidate = middleware._candidate()
            if candidate:
                candidates.append(candidate)
                middleware._fired = True
    if ledger.snapshot()["observer_errors"]:
        raise ValueError("Replay observer failed")
    return candidates


def worker_call(trace, metrics_path):
    wire = [item for item in trace["model_requests_wire"]
            if item.get("agent_role") == "observation_worker"]
    if not wire:
        return []
    metrics = [json.loads(line) for line in Path(metrics_path).read_text().splitlines()]
    starts = [item for item in metrics if item.get("event") == "llm_start"
              and item.get("command_id") == trace["command_id"]]
    ends = {item["run_id"]: item for item in metrics if item.get("event") == "llm_end"}
    result = []
    for item in wire:
        index = item["request_index"]
        if index >= len(starts) or starts[index]["run_id"] not in ends:
            raise ValueError("Worker model call has no matching metrics event")
        start = starts[index]
        end = ends[start["run_id"]]
        result.append({"request_index": index,
                       "duration_seconds": round(end["timestamp"] - start["timestamp"], 3),
                       "usage": end.get("usage")})
    return result


def arm_rows(attempt, replay):
    plan = read(attempt / "plan.json")
    raw_rows = read(attempt / "results.json")
    by_id = {(row["session_id"], row["task_id"]): row for row in raw_rows}
    result = []
    for scene in plan["sessions"]:
        source = read(attempt / "runtime/data" / scene["file"])
        commands = {task["id"]: task["content"] for task in source["tasks"]}
        files = list((attempt / scene["id"] / "agent-traces/toolchains").glob("*_completed.json"))
        traces = [read(path) for path in files]
        replay_index = SessionEvidenceIndexR2(scene["id"])
        for task in scene["tasks"]:
            key = scene["id"], task["id"]
            row = by_id[key]
            matches = [trace for trace in traces if trace.get("command") == commands[task["id"]]]
            if len(matches) != 1 or row["status"] != "scored":
                raise ValueError("Missing, repeated, or unscored task trace")
            trace = matches[0]
            duration = (datetime.fromisoformat(trace["completed_at"])
                        - datetime.fromisoformat(trace["started_at"])).total_seconds()
            runs = trace["task_progress"]["task_evidence"].get("observation_worker_runs", [])
            if len(runs) > 1:
                raise ValueError("Worker one-shot bound violated")
            item = {"session_id": scene["id"], "task_id": task["id"],
                    "passed": row["passed"], "passed_checks": sum(leaf["result"] for leaf in row["leaves"]),
                    "checks": len(row["leaves"]), "agent_timed_out": row["agent_timed_out"],
                    "duration_seconds": round(duration, 3), "token_usage": trace["token_usage"],
                    "command_id": trace["command_id"], "worker_runs": runs,
                    "worker_model_calls": worker_call(trace, attempt / scene["id"] / "agent-traces/reproduction-metrics.jsonl")}
            if replay:
                item["replayed_worker_candidates"] = policy_replay(trace, replay_index)
            result.append(item)
    return result


def main():
    registry = read(STUDY / "runs.json")
    if set(registry) != {"control", "worker"} or any(
            item["status"] != "completed_and_verified" for item in registry.values()):
        raise ValueError("Both first-attempt arms must be complete and verified")
    attempts = {arm: Path(item["attempt"]) for arm, item in registry.items()}
    plans = {arm: read(path / "plan.json") for arm, path in attempts.items()}
    if [{key: value for key, value in plan.items() if key != "study"} for plan in plans.values()][0] != [
            {key: value for key, value in plan.items() if key != "study"} for plan in plans.values()][1]:
        raise ValueError("Arm plans differ")
    hashes = {arm: read(STUDY / "frozen" / arm / "source_hashes.json") for arm in registry}
    changed = sorted(name for name in set(hashes["control"]) | set(hashes["worker"])
                     if hashes["control"].get(name) != hashes["worker"].get(name))
    if changed != ["agent4drone/observation_worker_r2.py", "agent4drone/uav_agent.py"]:
        raise ValueError("Unexpected treatment source difference")
    arms = {}
    for arm, attempt in attempts.items():
        rows = arm_rows(attempt, replay=arm == "control")
        summary = read(attempt / "summary.json")
        if not summary["valid_complete_run"] or len(rows) != 12:
            raise ValueError("Invalid arm result")
        arms[arm] = {"attempt": str(attempt), "summary": summary, "tasks": rows,
                     "worker_run_count": sum(len(row["worker_runs"]) for row in rows),
                     "worker_model_call_count": sum(len(row["worker_model_calls"]) for row in rows),
                     "control_replay_candidate_count": sum(len(row.get("replayed_worker_candidates", [])) for row in rows)}
    control = {(row["session_id"], row["task_id"]): row for row in arms["control"]["tasks"]}
    worker = {(row["session_id"], row["task_id"]): row for row in arms["worker"]["tasks"]}
    paired = [{"session_id": sid, "task_id": tid, "control_pass": row["passed"],
               "worker_pass": worker[(sid, tid)]["passed"],
               "control_duration_seconds": row["duration_seconds"],
               "worker_duration_seconds": worker[(sid, tid)]["duration_seconds"]}
              for (sid, tid), row in control.items()]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    value = {"study": str(STUDY), "plan": plans["control"], "changed_source_files": changed,
             "arms": arms, "paired": paired,
             "worker_wins": sum(row["worker_pass"] and not row["control_pass"] for row in paired),
             "worker_losses": sum(row["control_pass"] and not row["worker_pass"] for row in paired),
             "ties": sum(row["control_pass"] == row["worker_pass"] for row in paired)}
    OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUTPUT), "worker_wins": value["worker_wins"],
                      "worker_losses": value["worker_losses"], "ties": value["ties"],
                      "worker_runs": arms["worker"]["worker_run_count"],
                      "control_replay_candidates": arms["control"]["control_replay_candidate_count"]}))


if __name__ == "__main__":
    main()
