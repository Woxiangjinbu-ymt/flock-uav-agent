#!/usr/bin/env python3
"""Frozen 14-task public-receipt replay and separate synthetic v7 fidelity probes.

No model, simulator, or live tool is invoked. Original commands remain intact in
the main replay. Synthetic route-block-only commands are explicitly separate.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from execution_memory import SessionEvidenceIndexMemory
from execution_memory_v5 import ExecutionMemoryLedgerV5
from execution_memory_v7 import ExecutionMemoryLedgerV7, supports_compact_route
from task_evidence import encode
from task_evidence_r3 import explicit_routes

DEFAULT = ROOT / "docs/results/execution-memory-v7-cross-task-20261023"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def no_network(*args, **kwargs):
    raise AssertionError("Network prohibited in the v7 offline fidelity audit")


def parsed(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value  # Preserve an unparseable public reply; never fill it.
    return copy.deepcopy(value)


def original_events(job):
    return [{"tool": action["tool"], "args": copy.deepcopy(action.get("tool_input", {})),
             "output": parsed(reply), "archived_ordinal": i,
             "source_kind": "archived_public_tool_input_and_reply"}
            for i, (action, reply) in enumerate(job["result"]["intermediate_steps"], 1)]


def pair(command, cid, session):
    return [cls(command, cid, index=SessionEvidenceIndexMemory(session), max_summary_chars=3900)
            for cls in (ExecutionMemoryLedgerV5, ExecutionMemoryLedgerV7)]


def refs(value):
    if isinstance(value, dict):
        if set(value) == {"command", "event"}:
            yield value
        for child in value.values():
            yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


def xyz(value):
    if isinstance(value, dict):
        return [value[k] for k in "xyz"]
    return list(value)


def receipt_value(payload, path):
    for key in path:
        payload = payload[key]
    return payload


def source_matches_point(event, proof):
    """Independently inspect the cited raw field, not the route matcher output."""
    try:
        value = receipt_value(event["payload"], proof["receipt_path"])
        if isinstance(value, str):
            coordinates = re.search(r"position\s*\(([^)]+)\)", value)
            if not coordinates:
                return False
            point = [float(v.strip()) for v in coordinates[1].split(",")]
        else:
            point = xyz(value)
        return len(point) == 3 and math.dist(point, proof["actual_point"]) <= 1e-9
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def projection_checks(ledger, context, full, routes):
    issues = []
    events = {e["evidence_id"]: e for e in ledger._events}
    for ref in refs(context):
        if ref["command"] != ledger.command_id or ref["event"] not in events:
            issues.append("visible_source_missing_or_wrong_command")
    for route in routes:
        actor = route["actor_id"]
        start, end = route["actor_source_span"]
        if ledger.command[start:end] != route["required_actor"]:
            issues.append("original_actor_source_changed")
        previous = None
        for goal in route["goals"]:
            start, end = goal["source_span"]
            literal = [float(v.strip()) for v in ledger.command[start + 1:end - 1].split(",")]
            if literal != goal["point"]:
                issues.append("original_coordinate_or_order_changed")
            for key in ("observed_evidence", "ordered_evidence"):
                proof = goal[key]
                if not proof:
                    continue
                event = events.get(proof["evidence_id"])
                if (not event or proof["command_id"] != ledger.command_id
                        or proof["session_id"] != ledger.index.session_id
                        or proof["actor_id"] != actor or str(event["args"].get("drone_id")) != actor
                        or not source_matches_point(event, proof)):
                    issues.append("route_evidence_actor_or_raw_source_mismatch")
                if key == "ordered_evidence":
                    # The source event sequence and its returned point index must
                    # preserve physical point order; no request-only coordinate.
                    path = proof["receipt_path"]
                    point_index = path[-1] if path and isinstance(path[-1], int) else 10**6
                    order = (event["start_sequence"], point_index)
                    if previous is not None and order <= previous:
                        issues.append("ordered_evidence_not_increasing")
                    previous = order
    resume = context.get("resume")
    if resume:
        for category in ("done", "remaining", "unverified"):
            visible = resume.get(category, [])
            base = full[category]
            extras = [r for r in visible if r.get("kind") == "terminal_evidence_conflict"]
            if len(visible) - len(extras) + resume.get("omitted", {}).get(category, 0) != len(base):
                issues.append("resume_omission_accounting_mismatch:" + category)
            for row in visible:
                if row in extras:
                    continue
                if category == "unverified":
                    candidates = [r for r in base if r.get("source_span") == row.get("source_span")]
                else:
                    candidates = [r for r in base if r.get("id") == row.get("id") and r["kind"] == row["kind"]]
                if len(candidates) != 1:
                    issues.append("visible_requirement_not_in_full_view")
                    continue
                truth = candidates[0]
                for key in ("actor", "required_actor", "source", "ordered", "total", "from_ordinal", "state", "after"):
                    if key in row and row[key] != truth.get(key):
                        issues.append("visible_requirement_changed:" + key)
                if "goals" in row:
                    if (row["goals"] != truth["goals"][:len(row["goals"])]
                            or len(row["goals"]) + row.get("goals_omitted", 0) != len(truth["goals"])):
                        issues.append("remaining_goal_order_or_omission_mismatch")
                if category == "unverified" and (row["text"] != truth["text"][:len(row["text"])]
                        or len(row["text"]) + row.get("omitted_chars", 0) != len(truth["text"])):
                    issues.append("unverified_original_text_lost")
    if context.get("mission_completion") != "not_evaluated":
        issues.append("memory_claimed_whole_mission_verdict")
    return sorted(set(issues))


def synthetic_oracle(routes, actor_by_name, inputs):
    """Independent small oracle for this file's declared synthetic schema only.

    It uses raw returned successful_points and exact source coordinates, not
    either ledger implementation, route_projection, or receipt_points.
    """
    expected = {r["required_actor"]: {"actor": actor_by_name[r["required_actor"]],
                "prefix": 0, "observed": set(), "landed": False} for r in routes}
    observations = []
    for event in inputs:
        tool, args, output = event["tool"], event["args"], event.get("output")
        actor = str(args.get("drone_id", ""))
        for route in routes:
            state = expected[route["required_actor"]]
            if actor != state["actor"]:
                continue
            if tool in {"move_along_path", "take_off", "land"}:
                state["landed"] = False
            if tool == "move_along_path" and isinstance(output, dict) and output.get("drone_id") == actor:
                points = output.get("successful_points", [])
                if output.get("status") in {"success", "partial_success"} and output.get("successful_points_count") == len(points):
                    for actual in points:
                        for i, goal in enumerate(route["goals"]):
                            if actual == goal["point"]:
                                state["observed"].add(i + 1)
                        if state["prefix"] < len(route["goals"]) and actual == route["goals"][state["prefix"]]["point"]:
                            state["prefix"] += 1
            if tool == "land" and isinstance(output, dict) and output.get("status") == "success":
                state["landed"] = state["prefix"] == len(route["goals"])
        if tool == "get_nearby_entities":
            observations.append("failed" if event.get("error") or isinstance(output, dict) and output.get("error") else
                                "empty_targets" if isinstance(output, dict) and output.get("targets") == [] else "unknown")
    return expected, observations


def readpoint(ledgers, ordinal, *, expected=None):
    a, b = ledgers
    contexts = [g.decision_context() for g in ledgers]
    full = [g.completion_context() for g in ledgers]
    routes = [g.route_projection() for g in ledgers]
    texts = [encode(c) for c in contexts]
    issues = []
    comparisons = {
        "raw_events_equal": a._events == b._events,
        "routes_equal": routes[0] == routes[1],
        "completion_equal": full[0] == full[1],
        "observation_coverage_equal": a._observation_attempts == b._observation_attempts,
        "aggregate_equal": a.aggregate_projection() == b.aggregate_projection(),
        "under_3900_chars": all(len(t) <= 3900 for t in texts),
    }
    if not supports_compact_route(a.command):
        comparisons["fallback_byte_equal"] = texts[0] == texts[1]
    for name, okay in comparisons.items():
        if not okay:
            issues.append(name)
    for i, ledger in enumerate(ledgers):
        issues += [f"v{5 if i == 0 else 7}:{issue}" for issue in projection_checks(ledger, contexts[i], full[i], routes[i])]
        if expected is not None:
            truth, observations = expected
            for route in routes[i]:
                oracle = truth[route["required_actor"]]
                landed = any(r["kind"] == "landing" and r["actor"] == oracle["actor"] for r in full[i]["done"])
                if (route["actor_id"] != oracle["actor"] or route["ordered_prefix"] != oracle["prefix"]
                        or route["coordinates_with_receipt"] != len(oracle["observed"]) or landed != oracle["landed"]):
                    issues.append(f"v{5 if i == 0 else 7}:synthetic_independent_actor_order_landing_oracle")
            if [r["state"] for r in ledger._observation_attempts] != observations:
                issues.append(f"v{5 if i == 0 else 7}:synthetic_independent_observation_oracle")
            if i == 1:
                terminal = ledger.terminal_observations()
                if any(r["position_state"] != "unknown" or "position" in r for r in terminal):
                    issues.append("v7:synthetic_no_coordinate_land_invented_position")
    return {"ordinal": ordinal, "checks": comparisons, "issues": sorted(set(issues)),
            "chars": {"v5": len(texts[0]), "v7": len(texts[1])},
            "contexts": {"v5": contexts[0], "v7": contexts[1]},
            "full_completion": full[1],
            "route_progress": [{"actor": r["required_actor"], "actor_id": r["actor_id"],
                                "ordered_prefix": r["ordered_prefix"], "total": len(r["goals"]),
                                "receipt_coverage": r["coordinates_with_receipt"]} for r in routes[1]],
            "observations": copy.deepcopy(b._observation_attempts)}


def replay(command, cid, session, events, synthetic=None):
    ledgers = pair(command, cid, session)
    results = [readpoint(ledgers, 0)]
    for i, event in enumerate(events, 1):
        for ledger in ledgers:
            eid = ledger.begin_tool(event["tool"], copy.deepcopy(event["args"]), f"audit-call-{i}")
            ledger.end_tool(eid, copy.deepcopy(event.get("output")), error=event.get("error"))
        expected = synthetic_oracle(*synthetic, events[:i]) if synthetic is not None and i else None
        # Before list_drones the actor is unresolved; synthetic events always bind
        # the public actors first, so independent expectations begin after it.
        results.append(readpoint(ledgers, i, expected=expected))
    return {"command": command, "compact_eligible": supports_compact_route(command),
            "events": len(events), "readpoints": results,
            "observer_errors": [g.snapshot()["observer_errors"] for g in ledgers],
            "final_routes": ledgers[1].route_projection(),
            "final_terminal_observations_v7": ledgers[1].terminal_observations()}


def synthetic_events(routes, names, case):
    events = [{"tool": "list_drones", "args": {}, "output": [{"id": ident, "name": name} for name, ident in names.items()]}]
    def add(tool, args, output=None, error=None):
        events.append({"tool": tool, "args": args, "output": output, "error": error,
                       "source_kind": "synthetic_public_receipt_semantic_probe"})
    def path(route, points, actor=None):
        actor = actor or names[route["required_actor"]]
        add("move_along_path", {"drone_id": actor, "waypoints": [g["point"] for g in route["goals"]]},
            {"command": "move_along_path", "drone_id": actor,
             "status": "success" if len(points) == len(route["goals"]) else "partial_success",
             "successful_points": points, "successful_points_count": len(points)})
    if case == "empty_failed_unknown_observation":
        actor = names[routes[0]["required_actor"]]
        add("get_drone_status", {"drone_id": actor}, {"id": actor, "position": dict(zip("xyz", routes[0]["goals"][0]["point"]))})
        add("get_nearby_entities", {"drone_id": actor}, {"targets": [], "drones": [], "obstacles": []})
        add("get_nearby_entities", {"drone_id": actor}, {"error": "synthetic sensing failure"})
        add("get_nearby_entities", {"drone_id": actor}, {"blackboard": {"targets": []}})
    else:
        for i, route in enumerate(routes):
            points = [g["point"] for g in route["goals"]]
            if case == "partial_prefix":
                path(route, points[:max(1, len(points) // 2)])
            elif case == "wrong_actor":
                path(route, points, names[routes[(i + 1) % len(routes)]["required_actor"]])
            elif case == "out_of_order":
                path(route, points[:1] + points[2:])
            else:
                path(route, points)
                actor = names[route["required_actor"]]
                add("land", {"drone_id": actor}, {"command": "land", "drone_id": actor, "status": "success"})
                if case == "landing_then_unknown_action":
                    add("take_off", {"drone_id": actor, "altitude": points[-1][2]}, error="synthetic outcome unknown")
    return events


def run(output):
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    manifest = json.loads((output / "cohort.json").read_text())
    sealed = {manifest["screen_path"]: manifest["screen_sha256"], manifest["index_path"]: manifest["index_sha256"],
              **manifest["sources"], **{r["job_path"]: r["job_sha256"] for r in manifest["tasks"]}}
    assert len(manifest["tasks"]) == 14
    assert all(sha(ROOT / path) == value for path, value in sealed.items()), "Frozen input/source changed"
    originals, probes, missing = [], [], []
    for row in manifest["tasks"]:
        job = json.loads((ROOT / row["job_path"]).read_text())
        assert job["command"] == row["command"]
        events = original_events(job)
        assert len(events) == row["archived_steps"]
        key = f"{row['session_id']}/{row['task_id']}"
        originals.append({"task": key, "job_path": row["job_path"],
                          **replay(row["command"], f"archive:{key}", row["session_id"], events)})
        routes, _ = explicit_routes(row["command"])
        listing = next((e["output"] for e in events if e["tool"] == "list_drones" and isinstance(e["output"], list)), None)
        names = {d["name"]: d["id"] for d in listing or [] if isinstance(d, dict) and d.get("name") and d.get("id")}
        if not routes or any(r["required_actor"] not in names for r in routes):
            missing.append({"task": key, "reason": "missing_original_route_or_public_actor_binding"})
            continue
        command = "".join(row["command"][slice(*r["source_span"])] for r in routes)
        derived_routes, _ = explicit_routes(command)
        for case in manifest["supplementary_synthetic_cases"]:
            inputs = synthetic_events(derived_routes, names, case)
            result = replay(command, f"synthetic:{key}:{case}", row["session_id"], inputs,
                            synthetic=(derived_routes, names))
            probes.append({"task": key, "case": case, "source_kind": "synthetic_command_and_receipts",
                           "original_intro_removed": row["command"][:routes[0]["source_span"][0]],
                           "inputs": inputs, **result})
        print(f"audited {key}", flush=True)
    def metrics(rows):
        points = [p for row in rows for p in row["readpoints"]]
        issues = [{"task": row["task"], "case": row.get("case"), "ordinal": p["ordinal"], "issues": p["issues"]}
                  for row in rows for p in row["readpoints"] if p["issues"]]
        return {"records": len(rows), "compact_eligible_records": sum(r["compact_eligible"] for r in rows),
                "events": sum(r["events"] for r in rows), "readpoints": len(points),
                "v5_characters": sum(p["chars"]["v5"] for p in points),
                "v7_characters": sum(p["chars"]["v7"] for p in points),
                "max_v5_chars": max((p["chars"]["v5"] for p in points), default=0),
                "max_v7_chars": max((p["chars"]["v7"] for p in points), default=0),
                "visible_resume_v5": sum("resume" in p["contexts"]["v5"] for p in points),
                "visible_resume_v7": sum("resume" in p["contexts"]["v7"] for p in points),
                "readpoints_with_issues": len(issues), "issues": issues,
                "observer_error_count": sum(len(errors) for row in rows for errors in row["observer_errors"])}
    original_metrics, synthetic_metrics = metrics(originals), metrics(probes)
    assert all(sha(ROOT / path) == value for path, value in sealed.items()), "Originals changed during audit"
    summary = {"schema": "execution-memory-v7-cross-task-audit-v1", "date": "2026-10-23",
               "network_blocked": True, "provider_calls": 0, "executed_tools": 0, "simulator_calls": 0,
               "cohort_tasks": 14, "cohort_scenes": len({r["session_id"] for r in manifest["tasks"]}),
               "archived_replay": original_metrics, "synthetic_probes": synthetic_metrics,
               "missing_synthetic_sources": missing, "original_files_unchanged": True,
               "cohort_sha256": sha(output / "cohort.json"), "protocol_sha256": sha(output / "PROTOCOL_CN.md"),
               "script_sha256": sha(Path(__file__)), "frozen_input_and_source_count": len(sealed),
               "limitations": ["Fixed archived actions; no policy behavior or success-rate evaluation",
                               "Archive ordering is serialized; original concurrency and call IDs unavailable",
                               "Each task starts an empty evidence index; inherited live blackboard is not reconstructed",
                               "14 tasks were previously analyzed B0 material, not unseen or blind evaluation",
                               "Local geometry legality is not complete path/energy/time feasibility",
                               "Synthetic route-only commands and receipts are distinct from original tasks",
                               "Character counts are not model tokens, cost, or latency"]}
    write(output / "archived-replay.json", originals)
    write(output / "synthetic-probes.json", probes)
    write(output / "summary.json", summary)
    write(output / "integrity.json", {"files": sealed, "all_unchanged": True,
                                     "cohort_sha256": summary["cohort_sha256"],
                                     "protocol_sha256": summary["protocol_sha256"],
                                     "script_sha256": summary["script_sha256"]})
    print(json.dumps({k: v for k, v in summary.items() if k not in {"limitations"}}, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT)
    args = parser.parse_args()
    run(args.output.resolve())
