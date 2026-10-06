#!/usr/bin/env python3
"""Replay frozen M1 receipts, with original begin/end ordering, through M1-R.

No model, simulator, checker, hidden world state, or scored result enters the
ledger. The sealed pair manifest supplies membership/order/trace paths only.
Earlier diagnostic refs are evaluated AFTER replay and never supplied as input.
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


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(output):
    hashes = {}
    def read(relative):
        content = (ROOT / relative).read_bytes()
        hashes[relative] = sha(content)
        return json.loads(content)

    pairs_path = "docs/results/m1-progress-fast100-20261006/paired-tasks.json"
    rows = [{key: r[key] for key in ("session_id", "task_id", "m1_trace")} for r in read(pairs_path)]
    plan = read("experiments/runs/20261006-175621-m1-fast100-m1-2d243789/plan.json")
    assert [(r["session_id"], r["task_id"]) for r in rows] == [
        (s["id"], t["id"]) for s in plan["sessions"] for t in s["tasks"]]
    del plan
    indexes, command_order, replayed, receipt_lookup = {}, {}, [], {}
    totals = Counter()
    maximum = 0
    for order, row in enumerate(rows):
        trace = read(row["m1_trace"])
        # Discard output, usage, API history and other final fields before use.
        command, cid = trace["command"], trace["command_id"]
        events = trace["task_progress"]["events"]
        receipt_lookup[cid] = {e["evidence_id"]: e for e in events}
        original_hash = sha(encode(events).encode())
        command_order[cid] = order
        index = indexes.setdefault(row["session_id"], SessionEvidenceIndex(row["session_id"]))
        g = TaskEvidenceLedger(command, cid, index=index, max_summary_chars=3900)
        initial = g.projection()
        timeline = []
        for event in events:
            timeline.append((event["start_sequence"], "start", event))
            if event["end_sequence"] is not None:
                timeline.append((event["end_sequence"], "end", event))
        ids = {}
        for _, kind, event in sorted(timeline, key=lambda v: v[0]):
            if kind == "start":
                ids[event["evidence_id"]] = g.begin_tool(event["tool_name"], event["args"], event["tool_call_id"])
                assert ids[event["evidence_id"]] == event["evidence_id"]
            else:
                payload = event["payload"]
                if isinstance(payload, dict) and set(payload) == {"tool_exception"}:
                    g.end_tool(ids[event["evidence_id"]], error=payload["tool_exception"])
                else:
                    g.end_tool(ids[event["evidence_id"]], payload)
                totals["completed_receipts"] += 1
                if not g._active:
                    text = g.summary()
                    maximum = max(maximum, len(text))
                    assert len(text) <= 3900
                    assert len(json.loads(text)["items"]) <= 4
        snapshot = g.snapshot()
        assert not snapshot["observer_errors"], (row, snapshot["observer_errors"])
        for old, new in zip(events, snapshot["events"]):
            for key in ("args", "payload", "start_sequence", "end_sequence", "tool_name", "tool_call_id", "overlaps"):
                assert old[key] == new[key], (row, key, old["evidence_id"])
        assert len(events) == len(snapshot["events"])
        assert sha(encode(events).encode()) == original_hash
        for stage in (initial, snapshot["task_evidence"]["requirements"]):
            for ref in stage:
                report = ref["report"]
                if report:
                    assert report["session_id"] == row["session_id"]
                    assert command_order[report["command_id"]] <= order
                    source = receipt_lookup[report["command_id"]][report["evidence_id"]]
                    message = source["payload"]
                    for key in report["message_path"]:
                        message = message[key]
                    assert message == report["message"]
                    if stage is initial:
                        assert command_order[report["command_id"]] < order
                if ref["execution_state"] == "evidenced":
                    proof = next(e for e in snapshot["events"] if e["evidence_id"] == ref["execution_evidence_id"])
                    assert str(proof["args"]["drone_id"]) == ref["actor_id"]
        totals.update(tasks=1, tool_events=len(events), pending_events=len(g._active),
                      observer_errors=len(snapshot["observer_errors"]), references=len(initial))
        replayed.append({**row, "command_id": cid, "raw_events_sha256": original_hash,
                         "event_count": len(events), "pending_events": len(g._active),
                         "initial_view": initial, "final_view": snapshot["task_evidence"]["requirements"],
                         "final_summary": snapshot["task_evidence"]["summary"]})
    # Diagnostic comparison only; none of these expected references enter g.
    expected = read("docs/results/m1-redesign-research-20261006/receipt-unresolved.json")
    recovered = []
    for ref in expected:
        if not ref["earlier_task_reach_reports"]:
            continue
        row = next(r for r in replayed if (r["session_id"], r["task_id"]) == (ref["scene"], ref["task"]))
        found = next(v for v in row["initial_view"] if v["target_name"] == ref["name"])
        assert found["knowledge_state"] == "reported_only"
        assert found["execution_state"] == "unattempted"
        assert found["target_id"] is None and found["position"] is None
        recovered.append({"session_id": ref["scene"], "task_id": ref["task"], **found})
    blocked_case = next(r for r in replayed if r["task_id"] == "5aa74653")
    for target in ("Fixed Target 7", "Fixed Target 12"):
        v = next(v for v in blocked_case["final_view"] if v["target_name"] == target)
        assert v["execution_state"] == "blocked"
        assert v["execution_evidence_kind"] == "requested_xy_matches_unique_known_target_not_arrival"
    summary = {"schema": "m1-r1-offline-replay", "totals": dict(totals),
               "scenes": len(indexes), "max_view_chars": maximum,
               "previously_diagnosed_prior_name_refs": len(recovered),
               "prior_refs_with_path_hint": sum(bool(r["report"]["localization_hint"]) for r in recovered),
               "no_future_or_other_session_evidence": True, "raw_receipts_and_order_preserved": True,
               "displayed_report_paths_resolve_to_original_receipts": True,
               "performance_claim": None,
               "limitations": ["Replay holds old actions fixed; no success-rate or cache-hit estimate",
                               "Pending interrupted tool events remain pending; no fabricated result",
                               "No hidden world snapshot or checker supplied to method",
                               "Initial index uses only earlier legal receipts, not final old ledger entities"]}
    source_paths = [Path(__file__), *(ROOT / "agent4drone" / p for p in (
        "task_evidence.py", "task_evidence_context.py", "model_request_audit.py", "task_progress.py", "command_results.py",
        "uav_agent.py", "blackboard.py", "tests/test_task_evidence.py"))]
    sources = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in source_paths}
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("replay.json", replayed), ("recovered-references.json", recovered),
                       ("summary.json", summary), ("input-sha256.json", hashes), ("source-sha256.json", sources)):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/results/m1-revision-offline-20261006")
    run(parser.parse_args().output)
