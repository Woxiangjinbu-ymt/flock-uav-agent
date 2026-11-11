#!/usr/bin/env python3
"""Offline R1/R2 replay of saved M1-R actions, never an outcome evaluation.

The membership manifest supplies only order, IDs and trace paths. No score,
world snapshot, final old ledger or old answer is supplied to either method.
Both arms start empty indexes and see exactly the same original tool timeline.
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
from task_evidence import SessionEvidenceIndex, TaskEvidenceLedger, encode
from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2


def sha(value):
    return hashlib.sha256(value).hexdigest()


def run(output):
    hashes, indexes, lookup, command_order, command_scene = {}, {}, {}, {}, {}
    totals, replayed = Counter(), []

    def read(path):
        data = (ROOT / path).read_bytes()
        hashes[path] = sha(data)
        return json.loads(data)

    path = "docs/results/m1-revision-fast100-20261006/paired-tasks.json"
    rows = [{k: row[k] for k in ("session_id", "task_id", "m1r_trace")} for row in read(path)]
    plan = read("experiments/runs/20261006-214358-m1r-fast100-b4e9b6f3/plan.json")
    assert [(r["session_id"], r["task_id"]) for r in rows] == [
        (s["id"], t["id"]) for s in plan["sessions"] for t in s["tasks"]]
    del plan

    for order, row in enumerate(rows):
        trace = read(row["m1r_trace"])
        command, cid, events = trace["command"], trace["command_id"], trace["task_progress"]["events"]
        del trace
        original_hash = sha(encode(events).encode())
        lookup[cid] = {e["evidence_id"]: e for e in events}
        command_order[cid], command_scene[cid] = order, row["session_id"]
        index1, index2 = indexes.setdefault(row["session_id"], (
            SessionEvidenceIndex(row["session_id"]), SessionEvidenceIndexR2(row["session_id"])))
        arms = (TaskEvidenceLedger(command, cid, index=index1, max_summary_chars=3900),
                TaskEvidenceLedgerR2(command, cid, index=index2, max_summary_chars=3900))
        stages = []

        def checkpoint(sequence):
            views, summaries = [g.projection() for g in arms], [g.summary() for g in arms]
            for g, view, summary in zip(arms, views, summaries):
                assert len(summary) <= 3900 and len(json.loads(summary)["items"]) <= 4
                totals["max_summary_chars"] = max(totals["max_summary_chars"], len(summary))
                for ref in view:
                    report = ref["report"]
                    if report:
                        source_cid = report["command_id"]
                        assert report["session_id"] == command_scene[source_cid] == row["session_id"]
                        assert command_order[source_cid] <= order
                        source = lookup[source_cid][report["evidence_id"]]
                        if source_cid == cid:
                            assert source["end_sequence"] is not None and source["end_sequence"] <= sequence
                        message = source["payload"]
                        for key in report["message_path"]:
                            message = message[key]
                        assert message == report["message"] and source["tool_call_id"] == report["tool_call_id"]
                        assert str(source["args"].get("drone_id")) == report["actor_id"]
                    if ref["execution_state"] == "evidenced":
                        source = lookup[cid][ref["execution_evidence_id"]]
                        assert str(source["args"].get("drone_id")) == ref["actor_id"]
                if isinstance(g, TaskEvidenceLedgerR2):
                    # Frozen R1 has no new tool readings. Never backfill them
                    # from checker output or reconstructed world statistics.
                    assert not g.progress_observations
                    assert all(a["threshold_state"] == "unknown" for a in g.aggregate_projection())
            # Semantic evidence/actor state remains unchanged by a search hint.
            semantic = lambda v: [{k: x for k, x in r.items() if k not in {"report", "next_step"}} for r in v]
            assert semantic(views[0]) == semantic(views[1])
            stages.append({"sequence": sequence, "projection_equal": views[0] == views[1],
                           "summary_equal": summaries[0] == summaries[1],
                           "r2_visible_hint_targets": [r["target_name"] for r in json.loads(summaries[1])["items"] if "next_step" in r],
                           "r1_summary_sha256": sha(summaries[0].encode()), "r2_summary_sha256": sha(summaries[1].encode())})
            return views

        initial = checkpoint(0)
        timeline = [(e["start_sequence"], "start", e) for e in events]
        timeline += [(e["end_sequence"], "end", e) for e in events if e["end_sequence"] is not None]
        for sequence, kind, event in sorted(timeline, key=lambda v: v[0]):
            for g in arms:
                if kind == "start":
                    assert g.begin_tool(event["tool_name"], event["args"], event["tool_call_id"]) == event["evidence_id"]
                else:
                    payload = event["payload"]
                    if isinstance(payload, dict) and set(payload) == {"tool_exception"}:
                        g.end_tool(event["evidence_id"], error=payload["tool_exception"])
                    else:
                        g.end_tool(event["evidence_id"], payload)
            if kind == "end" and not arms[0]._active:
                checkpoint(sequence)
        snapshots = [g.snapshot() for g in arms]
        for snapshot in snapshots:
            assert not snapshot["observer_errors"], (row, snapshot["observer_errors"])
            assert len(events) == len(snapshot["events"])
            for before, after in zip(events, snapshot["events"]):
                for key in ("args", "payload", "start_sequence", "end_sequence", "tool_name", "tool_call_id", "overlaps"):
                    assert before[key] == after[key], (row, key, before["evidence_id"])
        assert sha(encode(events).encode()) == original_hash
        totals.update(tasks=1, tool_events=len(events), pending_events=len(arms[0]._active),
                      observer_errors=0, checkpoints=len(stages),
                      aggregate_tasks=bool(arms[1].aggregate),
                      changed_summary_tasks=any(not s["summary_equal"] for s in stages),
                      changed_contact_projection_tasks=any(not s["projection_equal"] for s in stages))
        replayed.append({**row, "command_id": cid, "command": command, "raw_events_sha256": original_hash,
                         "event_count": len(events), "pending_events": len(arms[0]._active), "checkpoints": stages,
                         "r1_initial": initial[0], "r2_initial": initial[1],
                         "r1_final": snapshots[0]["task_evidence"]["requirements"],
                         "r2_final": snapshots[1]["task_evidence"]["requirements"],
                         "r2_final_summary": arms[1].summary(), "aggregate_requirements": arms[1].aggregate_projection()})

    # Select previously diagnosed W/L only AFTER all methods have finished.
    pairs = read(path)
    positive = [{"session_id": r["session_id"], "task_id": r["task_id"], "checkpoints": len(r["checkpoints"]),
                 "all_projections_equal": all(s["projection_equal"] for s in r["checkpoints"]),
                 "all_summaries_equal": all(s["summary_equal"] for s in r["checkpoints"]),
                 "adds_progress_tool": bool(r["aggregate_requirements"])}
                for p, r in zip(pairs, replayed) if p["pair_outcome_diagnostic"] == "W"]
    assert len(positive) == 4
    assert all(p["all_projections_equal"] and p["all_summaries_equal"] and not p["adds_progress_tool"] for p in positive)
    sweep = next(r for r in replayed if r["task_id"] == "20742d37")
    assert {"Fixed Target 10", "Fixed Target 11"} <= set(sweep["checkpoints"][0]["r2_visible_hint_targets"])
    for name in ("Fixed Target 10", "Fixed Target 11"):
        ref = next(r for r in sweep["r2_initial"] if r["target_name"] == name)
        assert ref["execution_state"] == "unattempted" and ref["knowledge_state"] == "reported_only"
        assert ref["target_id"] is None and ref["position"] is None
        assert ref["report"]["actor_id"] != ref["actor_id"]
        assert ref["report"]["localization_hint"]["points"] == [[154., 115., 18.], [154., 36., 18.]]
    sequential = next(r for r in replayed if r["task_id"] == "c6e28e3b")
    assert all(s["projection_equal"] and s["summary_equal"] for s in sequential["checkpoints"])
    aggregate = next(r for r in replayed if r["task_id"] == "b1ad4f3e")
    assert aggregate["aggregate_requirements"][0]["threshold_percent"] == 95
    assert aggregate["aggregate_requirements"][0]["threshold_state"] == "unknown"
    summary = {"schema": "m1-r2-offline-replay", "totals": dict(totals), "scenes": len(indexes),
               "positive_cases": positive, "raw_receipts_and_order_preserved": True,
               "evidence_semantics_preserved": True, "displayed_sources_resolve_without_future_or_cross_session_evidence": True,
               "performance_claim": None,
               "limitations": ["Fixed old actions, not R2-generated actions or success-rate estimates",
                               "Both replay indexes start empty; cannot certify exact original live inputs",
                               "No aggregate API observation existed in old traces; all new thresholds remain unknown",
                               "Pending interrupted calls remain pending; old 81/100 is unaccepted diagnostic evidence"]}
    sources = [Path(__file__), *(ROOT / "agent4drone" / p for p in (
        "task_evidence.py", "task_evidence_context.py", "task_evidence_r2.py", "task_progress.py",
        "model_request_audit.py", "command_results.py", "uav_agent.py", "blackboard.py", "tests/test_task_evidence_r2.py"))]
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("replay.json", replayed), ("summary.json", summary), ("input-sha256.json", hashes),
                       ("source-sha256.json", {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sources})):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/results/m1-revision2-offline-20261007")
    run(parser.parse_args().output)
