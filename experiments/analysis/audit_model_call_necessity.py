#!/usr/bin/env python3
"""Prefix-only descriptive audit; no provider calls, simulator, or checker access.

This labels observable invalidation and narrowly supported continuation candidates,
not counterfactual necessity of hidden reasoning. See the adjacent audit protocol.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/audit/model-call-necessity-20261015"
PAIR = ROOT / "docs/results/m1-revision2-fast100-20261007/paired-tasks.json"
STUDY = ROOT / "experiments/m1r2-public-search-prefix-v2"
MOTION = {"take_off", "change_altitude", "land", "move_to", "move_to_and_sense",
          "move_towards", "move_towards_and_sense", "move_along_path",
          "move_along_path_and_sense", "navigate_to", "navigate_to_and_sense"}
REASSESS = {"blocked", "partial", "resource", "unsupported", "lookup_error", "argument_missing", "other_error"}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def receipt_text(event):
    payload = event.get("payload")
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    # Do not interpret historical notes / blackboard / entity names as errors.
    text = " ".join(str(payload.get(k, "")) for k in ("message", "tool_exception", "error"))
    for key in ("navigation_result", "last_move_result", "move_result", "movement"):
        child = payload.get(key)
        if isinstance(child, dict):
            text += " " + receipt_text({"payload": child})
    return text


def failure_kind(event):
    status = event.get("status")
    text = receipt_text(event).lower()
    if "error invoking tool" in text and "input should be a valid string" in text:
        return "argument_schema"
    if "error invoking tool" in text and "field required" in text:
        return "argument_missing"
    if status == "blocked":
        return "blocked"
    if status == "partial":
        return "partial"
    if status in {"completed", "observed"}:
        return None
    if status == "unknown":
        return "unknown_effect"
    if status == "error":
        payload = event.get("payload")
        navigation = payload.get("navigation_result", payload) if isinstance(payload, dict) else {}
        last_move = navigation.get("last_move_result") or {}
        if (navigation.get("arrival_verified") is False and navigation.get("final_position") and
                last_move.get("outcome") == "completed"):
            return "arrival_unverified"
        if "insufficient battery" in text:
            return "resource"
        if "from current status" in text or "not at a valid charging station" in text:
            return "state_conflict"
        if "unsupported type" in text:
            return "unsupported"
        if "404" in text or "not found" in text:
            return "lookup_error"
        return "other_error"
    return "unknown_effect"


def actor(event):
    return (event.get("args") or {}).get("drone_id")


def public_targets(payload):
    """Extract only entities actually present in public receipts, not task/checker data."""
    found = {}

    def visit(value, context=""):
        if isinstance(value, list):
            for item in value:
                visit(item, context)
        elif isinstance(value, dict):
            if (value.get("category") == "target" or context == "targets") and value.get("id"):
                if value.get("position") is not None:
                    found[str(value["id"])] = value.get("name", "")
            for key, item in value.items():
                if isinstance(item, (list, dict)):
                    visit(item, key)
    visit(payload)
    return found


def simple_plan(command):
    """Full-string, source-only probe; unsupported semantics remain unsupported."""
    number = r"(\d+(?:\.\d+)?)"
    patterns = [
        (rf"Drone Drone (\d+) takes off to {number} meters altitude, then rotates to face heading {number} degrees and hovers\.", "rotate"),
        (rf"Drone Drone (\d+) should take off to {number} meters, then climb to {number} meters altitude and hover\.", "climb"),
        (rf"Command drone Drone (\d+) to take off to an altitude of {number} meters and hover in place\.", "hover"),
    ]
    for pattern, family in patterns:
        match = re.fullmatch(pattern, command)
        if match:
            values = match.groups()
            steps = [{"tool": "take_off", "altitude": float(values[1])}]
            if family == "rotate":
                steps.append({"tool": "rotate", "heading": float(values[2])})
            if family == "climb":
                steps.append({"tool": "change_altitude", "altitude": float(values[2])})
            steps.append({"tool": "hover"})
            return {"actor_name": f"Drone {values[0]}", "family": family, "steps": steps}
    return None


def simple_state(plan, prefix):
    if not plan:
        return None
    bindings = set()
    for e in prefix:
        if e["tool_name"] == "list_drones" and isinstance(e.get("payload"), list):
            bindings.update(str(d["id"]) for d in e["payload"]
                            if d.get("name") == plan["actor_name"] and d.get("id"))
    if len(bindings) != 1:
        return None
    bound = next(iter(bindings))
    done, last_end = 0, -1
    for e in prefix:
        if e.get("scope") != "action_receipt":
            continue
        if actor(e) != bound or done >= len(plan["steps"]):
            return None
        expected = plan["steps"][done]
        if e["tool_name"] != expected["tool"] or e["status"] != "completed" or e["start_sequence"] <= last_end:
            return None
        for key, wanted in expected.items():
            if key != "tool" and e["args"].get(key) != wanted:
                return None
        done += 1
        last_end = e["end_sequence"]
    # Before any committed successful action, preserve initial planning/guards.
    if not done:
        return None
    return {"actor_id": bound, "family": plan["family"], "completed_steps": done,
            "next_step": plan["steps"][done] if done < len(plan["steps"]) else None}


def align_requests(trace):
    evidence = trace["task_progress"]["task_evidence"]
    requests = evidence["model_requests"]
    all_events = sorted(trace["task_progress"]["events"], key=lambda e: e["start_sequence"])
    workers = [e for e in all_events if e["tool_call_id"].startswith("coordinated-search-")]
    events = [e for e in all_events if not e["tool_call_id"].startswith("coordinated-search-")]
    if not requests or len(requests[0]["message_sha256"]) != 2:
        raise ValueError("Unsupported initial message boundary")
    chain = trace.get("tool_chain", [])
    if chain and len(chain) != len(events):
        raise ValueError("Tool-chain count disagrees with event ledger")
    groups = [[]]
    cursor = 0
    reordered_batches = 0
    for previous, current in zip(requests, requests[1:]):
        old = len(previous["message_sha256"])
        new = len(current["message_sha256"])
        if (current["segment"] != 0 or not current["previous_prefix_preserved"] or new - old < 2 or
                current["message_sha256"][:old] != previous["message_sha256"]):
            raise ValueError("Unsupported graph boundary or no tool-message expansion")
        count = new - old - 1
        batch = events[cursor:cursor + count]
        if len(batch) != count:
            raise ValueError("Missing tool receipts")
        if cursor and max(e["end_sequence"] for e in events[:cursor]) >= min(e["start_sequence"] for e in batch):
            raise ValueError("Events overlap across inferred model boundary")
        if chain:
            chain_batch = chain[cursor:cursor + count]
            if Counter(x["tool"] for x in chain_batch) != Counter(e["tool_name"] for e in batch):
                raise ValueError("Tool-chain batch membership disagrees with event ledger")
            reordered_batches += [x["tool"] for x in chain_batch] != [e["tool_name"] for e in batch]
        groups.append(batch)
        cursor += count
    if cursor and cursor < len(events) and max(e["end_sequence"] for e in events[:cursor]) >= events[cursor]["start_sequence"]:
        raise ValueError("Tail overlaps an already consumed request boundary")
    # Injection is on the final ToolMessage of a completed batch; thus its ID
    # must belong to that batch. Concurrent reply order within it may differ.
    anchors = 0
    for inj in evidence.get("injections", []):
        if inj.get("tool_call_id"):
            idx = inj["message_index"]
            matches = [g for i, g in enumerate(groups) if i and
                       len(requests[i-1]["message_sha256"]) <= idx <= len(requests[i]["message_sha256"]) - 2]
            if len(matches) != 1 or inj["tool_call_id"] not in {e["tool_call_id"] for e in matches[0]}:
                raise ValueError("Injection anchor disagrees with inferred model boundary")
            anchors += 1
    return groups, events[cursor:], workers, {"chain_crosscheck": bool(chain), "injection_anchors": anchors,
                                             "concurrent_tool_order_differences": reordered_batches}


def classify_boundary(command, prefix, batch, index):
    kinds = sorted({k for e in batch if (k := failure_kind(e))})
    state = simple_state(simple_plan(command), prefix)
    if index == 0:
        label = "INITIAL_PLAN"
    elif set(kinds) & REASSESS:
        label = "REASSESS"
    elif kinds:
        label = "OBSERVE_OR_REPAIR"
    elif state and any(e.get("scope") == "action_receipt" for e in batch):
        label = "CONTINUE_CANDIDATE" if state["next_step"] else "CONFIRM_CANDIDATE"
    else:
        label = "UNDETERMINED"
    return {"label": label, "failure_kinds": kinds, "simple_plan_state": state}


def audit_trace(trace):
    groups, tail, workers, alignment = align_requests(trace)
    rows, event_rows, prefix = [], [], []
    seen_targets = set()
    last_progress = None
    motion_versions = Counter()
    failed_signatures = {}
    runs = trace["task_progress"]["task_evidence"].get("coordinated_search_runs", [])
    if len(runs) > 1:
        raise ValueError("Multiple worker runs require explicit run-scoped event mapping")
    worker_trigger = runs[0]["trigger"]["progress_evidence_id"] if runs else None
    worker_seen = False
    for i, batch in enumerate(groups):
        new_targets, progress = {}, []
        # Repeated failures are assessed against *earlier batches*, avoiding
        # the false assertion that parallel same-batch calls saw each other.
        batch_failures = {}
        for e in batch:
            k = failure_kind(e)
            targets = public_targets(e.get("payload"))
            new_targets.update({tid: name for tid, name in targets.items() if tid not in seen_targets})
            seen_targets.update(targets)
            p = e.get("payload")
            if e["tool_name"] == "get_mission_progress" and isinstance(p, dict) and isinstance(p.get("progress_percentage"), (float, int)):
                value = p["progress_percentage"]
                progress.append({"evidence_id": e["evidence_id"], "value": value,
                                 "previous": last_progress,
                                 "delta": value - last_progress if last_progress is not None else None})
                last_progress = value
            sig = compact([actor(e), e["tool_name"], {k: v for k, v in e["args"].items() if k != "detail"}])
            repeated = k in {"blocked", "partial"} and failed_signatures.get(sig) == motion_versions[actor(e)]
            if k in {"blocked", "partial"}:
                batch_failures[sig] = motion_versions[actor(e)]
            event_rows.append({"request_index": i, "evidence_id": e["evidence_id"],
                               "tool_call_id": e["tool_call_id"], "tool": e["tool_name"],
                               "actor": actor(e), "status": e["status"], "failure_kind": k,
                               "repeated_failure_without_recorded_motion": repeated,
                               "args": e["args"], "receipt_excerpt": receipt_text(e)[:450]})
        failed_signatures.update(batch_failures)
        for e in batch:
            if e["status"] == "completed" and e["tool_name"] in MOTION:
                motion_versions[actor(e)] += 1
        prefix.extend(batch)
        value = classify_boundary(trace["command"], prefix, batch, i)
        active_workers = workers if worker_trigger in {e["evidence_id"] for e in batch} else []
        if active_workers:
            worker_seen = True
            prefix.extend(active_workers)
            for e in active_workers:
                seen_targets.update(public_targets(e.get("payload")))
                p = e.get("payload")
                if e["tool_name"] == "get_mission_progress" and isinstance(p, dict):
                    last_progress = p.get("progress_percentage", last_progress)
                if e["status"] == "completed" and e["tool_name"] in MOTION:
                    motion_versions[actor(e)] += 1
            value.update(label="UNDETERMINED", special_boundary="worker_modified_state_before_model_request")
        rows.append({"request_index": i, **value, "event_ids": [e["evidence_id"] for e in batch],
                     "tools": dict(Counter(e["tool_name"] for e in batch)),
                     "affected_actors": sorted({str(actor(e)) for e in batch if failure_kind(e) and actor(e)}),
                     "new_public_target_ids": sorted(new_targets),
                     "new_target_names_mentioned_in_command": sorted({name for name in new_targets.values() if name and
                         re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", trace["command"])}),
                     "progress_observations": progress,
                     "worker_events_before_boundary": [e["evidence_id"] for e in active_workers],
                     "counterfactual_call_removal_verified": False})
    if workers and not worker_seen:
        raise ValueError("Cannot associate worker with its triggering model boundary")
    return rows, event_rows, tail, workers, alignment


def cohorts():
    primary = []
    for row in read(PAIR):
        primary.append({"cohort": "r2_fast100", "scene_id": row["session_id"],
                        "task_id": row["task_id"], "trace": str(Path(row["m1r2_trace"]).resolve()),
                        "outcome_posthoc": {"task_pass": row["m1r2_pass"], "timed_out": row["m1r2_timeout"]}})
    yield "r2_fast100", primary
    for arm, run in read(STUDY / "runs.json").items():
        attempt = Path(run["attempt"])
        if run["status"] != "completed_and_verified":
            raise ValueError("Unsealed comparison cohort")
        result_rows = read(attempt / "results.json")
        by_scene = {}
        for path in attempt.glob("*/agent-traces/toolchains/*.json"):
            t = read(path)
            by_scene.setdefault(path.parts[-4], []).append((path, t["command"], datetime.fromisoformat(t["started_at"]).timestamp()))
        commands = {(p["scene_id"], p["task_id"]): read(p["trace"])["command"] for p in primary}
        selected = []
        used = set()
        for row in result_rows:
            scene, task = row["session_id"], row["task_id"]
            window = read(attempt / scene / f"{task}.window.json")
            candidates = [(abs(ts - window["opened_at"]), path) for path, command, ts in by_scene[scene]
                          if command == commands[(scene, task)]]
            distance, path = min(candidates)
            if distance > 15 or path in used:
                raise ValueError("Nonunique trace/window association")
            used.add(path)
            selected.append({"cohort": f"search_{arm}52", "scene_id": scene, "task_id": task,
                             "trace": str(path.resolve()),
                             "outcome_posthoc": {"task_pass": row["task_pass"], "timed_out": row["agent_timed_out"]}})
        if len(selected) != 52:
            raise ValueError("Comparison denominator changed")
        yield f"search_{arm}52", selected


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    boundary_rows, event_rows, manifest, stats, worker_runs = [], [], [], {}, []
    for name, entries in cohorts():
        counts = Counter()
        labels, failure_counts = Counter(), Counter()
        for entry in entries:
            trace = read(entry["trace"])
            rows, events, tail, workers, alignment = audit_trace(trace)
            identity = {k: entry[k] for k in ("cohort", "scene_id", "task_id", "trace")}
            for row in rows:
                boundary_rows.append({**identity, **row})
                labels[row["label"]] += 1
                counts["boundaries_with_new_named_target"] += bool(row["new_target_names_mentioned_in_command"])
                counts["boundaries_with_any_new_target"] += bool(row["new_public_target_ids"])
            event_rows.extend({**identity, **event} for event in events)
            failure_counts.update(e["failure_kind"] for e in events if e["failure_kind"])
            counts.update(tasks=1, middleware_requests=len(rows), wire_requests=len(trace["model_requests_wire"]),
                          events_consumed=len(events), tail_events=len(tail), worker_events=len(workers),
                          chain_crosschecked_tasks=int(alignment["chain_crosscheck"]),
                          injection_anchors=alignment["injection_anchors"],
                          tasks_with_simple_plan=int(simple_plan(trace["command"]) is not None),
                          repeated_failure_events=sum(e["repeated_failure_without_recorded_motion"] for e in events))
            wire_counts = Counter(w.get("middleware_request_index") for w in trace["model_requests_wire"])
            counts["extra_wire_attempts_same_middleware"] += sum(max(0, n - 1) for n in wire_counts.values())
            counts["wire_missing_middleware_index"] += wire_counts.get(None, 0)
            manifest.append({**entry, "trace_sha256": sha(entry["trace"]), "command": trace["command"],
                             "trace_status": trace["status"], "alignment": alignment,
                             "tail_event_ids": [e["evidence_id"] for e in tail],
                             "tail_failure_counts": dict(Counter(failure_kind(e) for e in tail)),
                             "labels": dict(Counter(r["label"] for r in rows))})
            for run in trace["task_progress"]["task_evidence"].get("coordinated_search_runs", []):
                worker_runs.append({**identity, "run": run, "event_count": len(workers),
                                    "failure_counts": dict(Counter(failure_kind(e) for e in workers if failure_kind(e))),
                                    "blocked_events": [{"evidence_id": e["evidence_id"], "args": e["args"], "receipt": receipt_text(e)}
                                                       for e in workers if failure_kind(e)]})
        stats[name] = {"counts": dict(counts), "labels": dict(labels), "failure_events": dict(failure_counts),
                       "scenes": sorted({e["scene_id"] for e in entries})}
    summary = {"schema": "model-call-audit-v1", "date": "2026-10-15", "scope": "full_trace_not_500s_window",
               "classifier_source_sha256": sha(__file__), "protocol_sha256": sha(OUT / "PROTOCOL_CN.md"),
               "unique_scene_task_pairs": len({(m["scene_id"], m["task_id"]) for m in manifest}),
               "counterfactual_call_removals_verified": 0, "cohorts": stats}
    write_json(args.output / "summary.json", summary)
    write_json(args.output / "manifest.json", manifest)
    write_json(args.output / "worker-runs.json", worker_runs)
    for filename, rows in (("boundaries.jsonl", boundary_rows), ("events.jsonl", event_rows)):
        (args.output / filename).write_text("".join(compact(row) + "\n" for row in rows))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
