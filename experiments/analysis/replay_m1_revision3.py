#!/usr/bin/env python3
"""Replay frozen R2 observations through R2/R3, without a model or simulator.

Membership supplies IDs/order/paths only. Both arms start empty session indexes;
neither receives a score, world snapshot, old ledger projection or final answer.
Observed actions remain fixed: this checks evidence behavior, not success rate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from task_evidence import encode
from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2
from task_evidence_r3 import TaskEvidenceLedgerR3, receipt_points, _matches


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(output):
    hashes, indexes, totals, replay = {}, {}, Counter(), []

    def read(path):
        data = (ROOT / path).read_bytes()
        hashes[path] = sha(data)
        return json.loads(data)

    member_path = "docs/results/m1-revision2-fast100-20261007/paired-tasks.json"
    rows = [{k: r[k] for k in ("session_id", "task_id", "m1r2_trace")} for r in read(member_path)]
    plan = read("experiments/runs/20261007-211050-m1r2-fast100-f5985403/plan.json")
    assert [(r["session_id"], r["task_id"]) for r in rows] == [(s["id"], t["id"]) for s in plan["sessions"] for t in s["tasks"]]
    del plan
    for row in rows:
        trace = read(row["m1r2_trace"])
        command, cid, events = trace["command"], trace["command_id"], trace["task_progress"]["events"]
        del trace
        before_sha = sha(encode(events).encode())
        ix2, ix3 = indexes.setdefault(row["session_id"], (SessionEvidenceIndexR2(row["session_id"]), SessionEvidenceIndexR2(row["session_id"])))
        arms = (TaskEvidenceLedgerR2(command, cid, index=ix2, max_summary_chars=3900),
                TaskEvidenceLedgerR3(command, cid, index=ix3, max_summary_chars=3900))
        stages, lookup = [], {e["evidence_id"]: e for e in events}

        def checkpoint(sequence):
            r2, r3 = arms
            s2, s3 = r2.summary(), r3.summary()
            assert r2.projection() == r3.projection()
            assert r2.aggregate_projection() == r3.aggregate_projection()
            assert r2.progress_observations == r3.progress_observations
            if not r3.routes:
                assert s2 == s3
            assert len(s2) <= 3900 and len(s3) <= 3900
            totals["max_r3_summary_chars"] = max(totals["max_r3_summary_chars"], len(s3))
            routes = r3.route_projection()
            for route in routes:
                actor = route["actor_id"]
                previous = None
                for goal in route["goals"]:
                    text = command[slice(*goal["source_span"])]
                    assert list(map(float, text[1:-1].split(","))) == goal["point"]
                    for field in ("observed_evidence", "ordered_evidence"):
                        proof = goal[field]
                        if not proof:
                            continue
                        source = lookup[proof["evidence_id"]]
                        assert proof["command_id"] == cid and proof["session_id"] == row["session_id"]
                        assert source["end_sequence"] == proof["end_sequence"] <= sequence
                        assert str(source["args"].get("drone_id")) == proof["actor_id"] == actor
                        assert proof["tool_call_id"] == source["tool_call_id"]
                        assert not r3._actor_conflict(source, actor)
                        candidates = receipt_points(source)
                        assert any(all(proof[k] == v for k, v in p.items()) and _matches(p, goal["point"]) for p in candidates)
                        if field == "ordered_evidence":
                            position = next(i for i, p in enumerate(candidates) if all(proof[k] == v for k, v in p.items()))
                            order = (source["start_sequence"], position)
                            assert previous is None or previous < order
                            previous = order
            stages.append({"sequence": sequence, "summary_equal": s2 == s3,
                           "r2_summary_sha256": sha(s2.encode()), "r3_summary_sha256": sha(s3.encode()),
                           "r2_summary_chars": len(s2), "r3_summary_chars": len(s3),
                           "routes": [{"actor": r["required_actor"], "ordered_prefix": r["ordered_prefix"],
                                       "receipt_coverage": r["coordinates_with_receipt"],
                                       "next_point": r["next_unconfirmed"]["point"] if r["next_unconfirmed"] else None} for r in routes]})

        checkpoint(0)
        timeline = [(e["start_sequence"], "start", e) for e in events]
        timeline += [(e["end_sequence"], "end", e) for e in events if e["end_sequence"] is not None]
        for sequence, kind, e in sorted(timeline, key=lambda v: v[0]):
            for g in arms:
                if kind == "start":
                    assert g.begin_tool(e["tool_name"], e["args"], e["tool_call_id"]) == e["evidence_id"]
                elif isinstance(e["payload"], dict) and set(e["payload"]) == {"tool_exception"}:
                    g.end_tool(e["evidence_id"], error=e["payload"]["tool_exception"])
                else:
                    g.end_tool(e["evidence_id"], e["payload"])
            if kind == "end" and not arms[0]._active:
                checkpoint(sequence)
        for g in arms:
            snapshot = g.snapshot()
            assert not snapshot["observer_errors"] and len(snapshot["events"]) == len(events)
            for before, after in zip(events, snapshot["events"]):
                for key in ("args", "payload", "start_sequence", "end_sequence", "tool_name", "tool_call_id", "overlaps"):
                    assert before[key] == after[key], (row, key)
        assert before_sha == sha(encode(events).encode())
        totals.update(tasks=1, tool_events=len(events), checkpoints=len(stages), observer_errors=0,
                      aggregate_tasks=bool(arms[1].aggregate), route_tasks=bool(arms[1].routes),
                      changed_summary_tasks=any(not s["summary_equal"] for s in stages),
                      pending_events=len(arms[1]._active))
        replay.append({**row, "command_id": cid, "command": command, "event_sha256": before_sha,
                       "route_parse_status": arms[1].route_parse_status, "checkpoints": stages,
                       "final_routes": arms[1].route_projection(), "final_r3_summary": arms[1].summary(),
                       "aggregate_unchanged": arms[0].aggregate_projection() == arms[1].aggregate_projection()})

    # Historical outcome labels are used only after both methods have finished.
    pairs = read(member_path)
    preserved = [{"task_id": p["task_id"], "session_id": p["session_id"],
                  "all_replayed_summaries_equal": all(s["summary_equal"] for s in r["checkpoints"])}
                 for p, r in zip(pairs, replay) if p["old_pair_m1r_vs_m0"] == "W"]
    assert len(preserved) == 4 and all(r["all_replayed_summaries_equal"] for r in preserved)
    skipped = next(r for r in replay if r["task_id"] == "cf8ee51f")
    drone7 = next(r for r in skipped["final_routes"] if r["required_actor"] == "Drone 7")
    assert drone7["next_unconfirmed"]["point"] == [292., 920., 27.]
    assert drone7["ordered_prefix"] == 10 and drone7["coordinates_with_receipt"] == 11
    assert drone7["goals"][-1]["observed_evidence"] and not drone7["goals"][-1]["ordered_evidence"]
    skipped_trace = read(skipped["m1r2_trace"])
    endpoint_event = next(e for e in skipped_trace["task_progress"]["events"]
                          if e["evidence_id"] == drone7["goals"][-1]["observed_evidence"]["evidence_id"])
    before_skip = [s for s in skipped["checkpoints"] if s["sequence"] < endpoint_event["start_sequence"]][-1]
    assert next(r for r in before_skip["routes"] if r["actor"] == "Drone 7")["next_point"] == [292., 920., 27.]
    assert totals["tasks"] == 100 and totals["route_tasks"] == totals["changed_summary_tasks"] == 3
    assert all(sha((ROOT / p).read_bytes()) == digest for p, digest in hashes.items())
    summary = {"schema": "m1-r3-offline-replay", "totals": dict(totals), "scenes": len(indexes),
               "unaffected_tasks": 97, "old_positive_cases": preserved,
               "r2_named_contact_and_aggregate_projections_unchanged": True,
               "raw_receipts_and_order_preserved": True, "route_evidence_actor_source_order_verified": True,
               "paid_model_calls": 0, "performance_claim": None,
               "gap_visible_before_saved_skip": {"checkpoint_sequence": before_skip["sequence"],
                    "later_endpoint_event": endpoint_event["evidence_id"],
                    "later_endpoint_start_sequence": endpoint_event["start_sequence"],
                    "missing_point": [292., 920., 27.]},
               "route_cases": [{"task_id": r["task_id"], "session_id": r["session_id"],
                                "total_goals": sum(len(v["goals"]) for v in r["final_routes"]),
                                "receipt_coverage": sum(v["coordinates_with_receipt"] for v in r["final_routes"]),
                                "ordered_prefix_sum": sum(v["ordered_prefix"] for v in r["final_routes"]),
                                "final_views": json.loads(r["final_r3_summary"])["routes"]} for r in replay if r["final_routes"]],
               "limitations": ["Fixed R2-generated actions, not R3 execution or success-rate measurement",
                               "Indexes start empty; replay equality does not certify original live input equality",
                               "Receipt gap is not algorithm failure; public arrival contract differs from task checker",
                               "R3 cannot force a model to obey the advisory reminder or complete a feasible route",
                               "Fast100 is a five-scene development set with carried world and blackboard state"]}
    sources = [Path(__file__), *(ROOT / "agent4drone" / p for p in (
        "task_evidence.py", "task_evidence_context.py", "task_evidence_r2.py", "task_evidence_r3.py",
        "task_progress.py", "command_results.py", "uav_agent.py", "model_request_audit.py",
        "tests/test_task_evidence_r3.py")), ROOT / "scripts/tests/test_m1_revision3_runtime.py"]
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("replay.json", replay), ("summary.json", summary), ("input-sha256.json", hashes),
                       ("source-sha256.json", {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sources})):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/results/m1-revision3-offline-20261008")
    run(parser.parse_args().output)
