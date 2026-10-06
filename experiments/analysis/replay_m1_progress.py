#!/usr/bin/env python3
"""Replay saved B1 tool replies through M1 without a model, API, or checker.

The plan/results are used only to fix task membership and job identities. The
ledger receives only job.command and tool/tool_input/observation. No API-call
history, check definition/result, before/after world snapshot or model reasoning
is supplied to it. Empty/missing toolchains are reported, never reconstructed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from task_progress import TaskProgressLedger

DEFAULT_ATTEMPT = ROOT / "experiments/runs/20261006-134416-b1-remedies60-1ee84ea7"
DEFAULT_OUTPUT = ROOT / "docs/results/m1-progress-offline-20261006/replay.json"


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(value):
    return digest_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":"), allow_nan=False).encode())


def relative(path):
    return str(path.resolve().relative_to(ROOT))


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_audit(attempt: Path, max_summary_chars: int):
    input_hashes = {}

    def read(path):
        content = path.read_bytes()
        input_hashes[relative(path)] = digest_bytes(content)
        return json.loads(content)

    plan_path = attempt / "plan.json"
    plan = read(plan_path)
    # Deliberately project the cohort metadata before constructing any ledger.
    sessions = [{"id": scene["id"], "tasks": [
        {key: task[key] for key in ("id", "name", "content_sha256")}
        for task in scene["tasks"]
    ]} for scene in plan["sessions"]]
    expected_tasks = plan["expected_tasks"]
    del plan
    source_paths = [Path(__file__), ROOT / "agent4drone/task_progress.py",
                    ROOT / "agent4drone/command_results.py"]
    source_hashes = {relative(path): digest_bytes(path.read_bytes()) for path in source_paths}
    totals = Counter()
    tool_counts = Counter()
    outcome_counts = Counter()
    binding_counts = Counter()
    fact_validities = Counter()
    scene_counts = defaultdict(Counter)
    rows = []
    boundaries = defaultdict(list)
    matched_paths = set()
    global_max_summary = 0
    global_max_steps = 0
    all_receipts_before, all_receipts_after = [], []

    def boundary(kind, row, **detail):
        if len(boundaries[kind]) < 2:
            boundaries[kind].append({"session_id": row["session_id"], "task_id": row["task_id"],
                                     "trace": row.get("trace"), **detail})

    for scene in sessions:
        sid = scene["id"]
        raw_results = read(attempt / sid / "results.json")
        cohort = {row["task_id"]: {key: row.get(key) for key in ("status", "job_id", "session_id")}
                  for row in raw_results}
        del raw_results
        traces = []
        for path in sorted((attempt / sid / "agent-traces/toolchains").glob("*.json")):
            trace = read(path)
            traces.append((path, {key: trace.get(key) for key in (
                "command", "started_at", "command_id", "runtime", "tool_chain", "status"
            )}))
        for task in scene["tasks"]:
            tid = task["id"]
            metadata = cohort.get(tid, {})
            row = {"session_id": sid, "task_id": tid, "name": task["name"],
                   "cohort_status": metadata.get("status", "missing_result")}
            totals["planned_tasks"] += 1
            scene_counts[sid]["planned"] += 1
            if metadata.get("status") != "scored":
                row["replay_status"] = "excluded_unscored"
                rows.append(row)
                totals["unscored_tasks"] += 1
                scene_counts[sid]["unscored"] += 1
                continue
            totals["scored_tasks"] += 1
            scene_counts[sid]["scored"] += 1
            job_path = attempt / sid / f"{tid}.job.json"
            if not job_path.exists():
                row["replay_status"] = "missing_job"
                rows.append(row)
                totals["missing_job"] += 1
                continue
            job_raw = read(job_path)
            job = {key: job_raw.get(key) for key in ("job_id", "command", "created_at", "completed_at")}
            del job_raw
            row.update(job_path=relative(job_path), job_id=job["job_id"],
                       command_sha256=digest_bytes(job["command"].encode()))
            if job["job_id"] != metadata.get("job_id") or row["command_sha256"] != task["content_sha256"]:
                raise ValueError(f"Frozen task/job identity mismatch: {sid}/{tid}")
            candidates = []
            for trace_path, trace in traces:
                if trace["command"] != job["command"]:
                    continue
                delta = abs((timestamp(trace["started_at"]) - timestamp(job["created_at"])).total_seconds())
                if delta < 1 and (trace.get("runtime") or {}).get("session_id") == sid:
                    candidates.append((trace_path, trace, delta))
            if len(candidates) != 1:
                row.update(replay_status="missing_or_ambiguous_trace", matching_candidates=len(candidates))
                rows.append(row)
                totals["missing_or_ambiguous_trace"] += 1
                continue
            trace_path, trace, delta = candidates[0]
            if trace_path in matched_paths:
                raise ValueError(f"A trace was matched to more than one task: {trace_path}")
            matched_paths.add(trace_path)
            row.update(trace=relative(trace_path), trace_sha256=input_hashes[relative(trace_path)],
                       trace_command_id=trace["command_id"], trace_status=trace["status"],
                       started_at_difference_seconds=delta,
                       match_rule="same_scene_exact_command_unique_start_difference_lt_1_second")
            totals["matched_traces"] += 1
            steps = trace.get("tool_chain")
            if not isinstance(steps, list) or not steps:
                row["replay_status"] = "empty_or_missing_tool_chain"
                totals["empty_or_missing_tool_chain"] += 1
                rows.append(row)
                boundary("missing_tool_evidence", row, reason="No saved tool steps; API history deliberately not substituted")
                continue
            projected = [{key: step.get(key) for key in ("index", "tool", "tool_input", "observation")}
                         for step in steps]
            if [step["index"] for step in projected] != list(range(1, len(projected) + 1)):
                raise ValueError(f"Noncontiguous toolchain indices: {sid}/{tid}")
            ledger = TaskProgressLedger(job["command"], command_id=f"replay:{sid}:{tid}",
                                        max_summary_chars=max_summary_chars)
            initial = ledger.snapshot()
            max_length = 0
            summary_count = 0
            summary_omissions = Counter()
            receipt_hashes_before, receipt_hashes_after = [], []
            json_observations = text_observations = empty_observations = 0
            for step in projected:
                before = digest(step)
                receipt_hashes_before.append(before)
                observation = step["observation"]
                if observation in (None, ""):
                    empty_observations += 1
                elif isinstance(observation, str):
                    try:
                        json.loads(observation)
                        json_observations += 1
                    except ValueError:
                        text_observations += 1
                else:
                    json_observations += 1
                ledger.observe_tool(step["tool"], step["tool_input"], observation,
                                    tool_call_id=f"saved-index-{step['index']}")
                after = digest(step)
                receipt_hashes_after.append(after)
                if before != after:
                    raise ValueError(f"Input receipt mutated: {sid}/{tid}/{step['index']}")
                summary = ledger.summary()
                summary_count += 1
                max_length = max(max_length, len(summary))
                if len(summary) > max_summary_chars:
                    raise ValueError(f"Summary budget exceeded: {sid}/{tid}/{step['index']}")
                data = json.loads(summary.split("\n", 1)[1] if "\n" in summary else summary)
                if not isinstance(data, dict):
                    raise ValueError("Summary must contain a complete JSON object")
                omissions = data.get("omitted", {})
                if isinstance(omissions, dict):
                    summary_omissions.update({key: int(value > 0) for key, value in omissions.items()})
                tool_counts[step["tool"]] += 1
            snapshot = ledger.snapshot()
            if snapshot["command"] != job["command"] or snapshot["mission_completion"] != "not_evaluated":
                raise ValueError(f"Original task changed or mission completion asserted: {sid}/{tid}")
            if any(clause["requirement_status"] != "not_evaluated" for clause in snapshot["clauses"]):
                raise ValueError(f"A requirement completion verdict was invented: {sid}/{tid}")
            bindings = Counter(value["state"] for value in snapshot["entity_bindings"].values())
            bindings_initial = Counter(value["state"] for value in initial["entity_bindings"].values())
            outcomes = Counter(event["status"] for event in snapshot["events"])
            validities = Counter(fact["validity"] for entity in snapshot["entities"].values()
                                 for fact in entity["facts"].values())
            row.update(replay_status="replayed", tool_steps=len(projected), summaries_checked=summary_count,
                       summary_max_chars=max_length, summary_step_omissions=dict(summary_omissions),
                       observer_errors=snapshot["observer_errors"],
                       tool_input_observation_hash_before=digest(receipt_hashes_before),
                       tool_input_observation_hash_after=digest(receipt_hashes_after),
                       input_receipts_unchanged=receipt_hashes_before == receipt_hashes_after,
                       original_command_unchanged=True, mission_completion="not_evaluated",
                       clause_count=len(snapshot["clauses"]),
                       clauses_without_explicit_entity_names=sum(not c["entity_names"] for c in snapshot["clauses"]),
                       requirement_status_counts={"not_evaluated": len(snapshot["clauses"])},
                       entity_name_refs=len(snapshot["entity_bindings"]), initial_bindings=dict(bindings_initial),
                       final_bindings=dict(bindings), observed_entity_count=len(snapshot["entities"]),
                       entity_fact_validities=dict(validities), outcome_counts=dict(outcomes),
                       observations={"json": json_observations, "text": text_observations, "empty": empty_observations},
                       steps_with_entity_observations=sum(bool(e["entity_ids"]) for e in snapshot["events"]))
            totals.update(replayed_tasks=1, tool_steps=len(projected), summaries_checked=summary_count,
                          observer_errors=len(snapshot["observer_errors"]), entity_name_refs=len(snapshot["entity_bindings"]),
                          clauses=len(snapshot["clauses"]), clauses_without_explicit_entity_names=row["clauses_without_explicit_entity_names"],
                          json_observations=json_observations, text_observations=text_observations,
                          empty_observations=empty_observations,
                          tasks_without_extracted_entity_names=int(not snapshot["entity_bindings"]))
            binding_counts.update(bindings)
            outcome_counts.update(outcomes)
            fact_validities.update(validities)
            scene_counts[sid]["replayed"] += 1
            scene_counts[sid]["tool_steps"] += len(projected)
            global_max_summary = max(global_max_summary, max_length)
            global_max_steps = max(global_max_steps, len(projected))
            all_receipts_before.extend(receipt_hashes_before)
            all_receipts_after.extend(receipt_hashes_after)
            unresolved = [name for name, binding in snapshot["entity_bindings"].items() if binding["state"] != "exact"]
            if unresolved:
                boundary("unresolved_entity_bindings", row, names=unresolved,
                         interpretation="Not found/bound in consumed evidence, not proof of nonexistence")
            if validities["inherited"]:
                boundary("inherited_last_known_facts", row, fact_count=validities["inherited"],
                         interpretation="Read only from accumulated blackboard returned in actual tools; no prior task state seeded")
            if any(summary_omissions.values()):
                boundary("bounded_summary_omissions", row, summary_max_chars=max_length,
                         summary_steps_with_omissions=dict(summary_omissions))
            for event in snapshot["events"]:
                payload = event["payload"]
                receipt = payload
                if isinstance(payload, dict) and event["tool_name"].endswith("_and_sense"):
                    receipt = payload.get("navigation_result", payload.get("move_result", payload))
                common = {"tool_step": int(event["tool_call_id"].split("-")[-1]),
                          "tool": event["tool_name"], "outcome": event["status"],
                          "observation_sha256": digest(payload)}
                if event["tool_name"] == "take_photo":
                    boundary("photo_subject_not_inferred", row, **common, photo_subject=event.get("photo_subject", "unknown"))
                if isinstance(receipt, dict) and receipt.get("arrival_verified") is False:
                    boundary("navigation_not_arrived", row, **common,
                             arrival_verified=False, final_position=receipt.get("final_position"))
                if event["status"] == "partial":
                    boundary("partial_effect_preserved", row, **common,
                             successful_points_count=receipt.get("successful_points_count") if isinstance(receipt, dict) else None,
                             unsuccessful_points_count=receipt.get("unsuccessful_points_count") if isinstance(receipt, dict) else None)
                if event["status"] == "unknown":
                    boundary("unknown_tool_outcome", row, **common)
            rows.append(row)
    if totals["planned_tasks"] != expected_tasks:
        raise ValueError("Fixed cohort task count changed")
    unchanged = all(digest_bytes((ROOT / name).read_bytes()) == checksum for name, checksum in input_hashes.items())
    source_unchanged = all(digest_bytes((ROOT / name).read_bytes()) == checksum for name, checksum in source_hashes.items())
    if not unchanged or not source_unchanged:
        raise ValueError("Source or saved evidence changed during the replay; rerun against a stable snapshot")
    return {
        "schema_version": 1, "audit": "m1-saved-b1-toolchain-shadow-replay-v1",
        "attempt": relative(attempt), "m0_method_commit": "cb80fde8269a09795782a6ef046d04c381b3f3c9",
        "mode": "offline_fixed_trace_observer", "summary_char_budget": max_summary_chars,
        "ledger_inputs": ["job.command", "tool_chain.tool", "tool_chain.tool_input", "tool_chain.observation"],
        "initial_blackboard": "empty; only blackboards inside actual tool responses are ingested as inherited",
        "excluded_from_ledger": ["checks", "results labels", "plan check definitions", "before/after snapshots",
                                 "model reasoning/log/output", "API-call history", "later task evidence"],
        "replay_order": "saved tool_chain index, without reconstructing unavailable tool start/end overlaps",
        "limits": ["Fixed replies cannot establish online action equivalence or performance improvement.",
                   "No model/API/checker calls; empty toolchains are not reconstructed from API history.",
                   "Entity binding coverage is not semantic parsing accuracy; every requirement remains not_evaluated.",
                   "Per-task empty initial ledger does not reproduce session inheritance before the first tool response.",
                   "Parallel order uncertainty is covered by unit tests, not inferred from this serialized trace."],
        "totals": dict(totals), "per_scene": {key: dict(value) for key, value in scene_counts.items()},
        "final_entity_binding_counts": dict(binding_counts), "tool_outcome_counts": dict(outcome_counts),
        "entity_fact_validity_counts": dict(fact_validities), "tool_counts": dict(tool_counts),
        "summary_max_chars": global_max_summary, "largest_toolchain_steps": global_max_steps,
        "all_summary_json_valid": True, "input_receipts_unchanged": all_receipts_before == all_receipts_after,
        "receipts_hash_before": digest(all_receipts_before), "receipts_hash_after": digest(all_receipts_after),
        "mission_completion_assertions": 0, "source_unchanged_during_replay": source_unchanged,
        "input_files_unchanged_during_replay": unchanged, "source_sha256": source_hashes,
        "runtime_versions": {name: version(name) for name in ("langchain", "langchain-core", "langgraph")},
        "input_sha256": input_hashes, "boundary_cases": dict(boundaries), "tasks": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, default=DEFAULT_ATTEMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-summary-chars", type=int, default=4000)
    parser.add_argument("--check", action="store_true", help="Recompute and compare; do not write")
    args = parser.parse_args()
    report = run_audit(args.attempt.resolve(), args.max_summary_chars)
    if args.check:
        if json.loads(args.output.read_text()) != report:
            raise SystemExit("Replay artifact differs from recomputed audit")
        print("Replay artifact verified")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"output": str(args.output), "totals": report["totals"],
                          "summary_max_chars": report["summary_max_chars"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
