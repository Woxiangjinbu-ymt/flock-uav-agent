"""Offline, source-frozen real-simulator comparison of public progress confirmation before no-candidate exit.

No language model is created. The driver explicitly selects a skill and calls
start; these results cannot establish autonomous skill choice or generalization.
Each trial is a fresh subprocess with socket connections blocked. Original
full100 fixtures and their manifest are read only and hash checked before pickle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import platform
import re
import shutil
import socket
import subprocess
import sys
import time


TASKS = (
    ("7eba7390", "public_search"),
    ("24ac1838", "public_search"),
    ("b1ad4f3e", "public_search"),
    ("b1eb0270", "simple_chain"),
    ("898cfdca", "simple_chain"),
    ("784d71f8", "public_search"),
    ("1905e0ad", "public_search"),
)
REVISIONS = ("shared_endpoint_v5", "terminal_progress_v6")
RUNTIME_SECONDS = 500
WATCHDOG_SECONDS = 90
HISTORICAL_RUN = "docs/results/harness-shared-endpoint-20261023/run-20261023-01"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def check_hashes(root, expected):
    bad = [name for name, checksum in expected.items()
           if not (root / name).is_file() or sha((root / name).read_bytes()) != checksum]
    if bad:
        raise ValueError("Artifact hash mismatch: " + ", ".join(bad[:8]))


def block_network():
    """TestClient uses ASGI transport; it does not require socket connections."""
    attempts = []

    def denied(*args, **kwargs):
        attempts.append("socket_connection_blocked")
        raise RuntimeError("Network is forbidden in terminal-progress validation")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    return attempts


def fixture_inputs(workspace, study):
    manifest_path = study / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # Check the complete original manifest without altering its allowed online IDs.
    check_hashes(study, manifest["files"])
    selected = {"manifest.json": sha(manifest_path.read_bytes())}
    fixtures = {}
    for task_id, skill in TASKS:
        relative = f"fixtures/{task_id}.json"
        if relative not in manifest["files"]:
            raise ValueError(f"Fixture is not frozen in original manifest: {relative}")
        fixture = json.loads((study / relative).read_text())
        blob_name = f"fixtures/{task_id}.pkl"
        timeline_name = "fixtures/" + fixture["timeline_file"]
        for name in (relative, blob_name, timeline_name):
            if name not in manifest["files"]:
                raise ValueError(f"Unmanifested fixture dependency: {name}")
            selected[name] = manifest["files"][name]
        if selected[blob_name] != fixture["snapshot_sha256"]:
            raise ValueError("Fixture snapshot hash disagrees with manifest")
        if selected[timeline_name] != fixture["timeline_sha256"]:
            raise ValueError("Fixture timeline hash disagrees with manifest")
        if sha(fixture["command"].encode()) != fixture["command_sha256"]:
            raise ValueError("Task command hash mismatch")
        if sha((workspace / fixture["source"]).read_bytes()) != fixture["source_sha256"]:
            raise ValueError("Original task source hash mismatch")
        fixtures[task_id] = fixture
    if len({fixtures[t]["problem_cluster"] for t, _ in TASKS[:3]}) != 3:
        raise ValueError("Search representatives must cover three distinct problem clusters")
    return selected, fixtures


def source_snapshot(workspace, destination):
    sources = []
    for directory in ("agent4drone", "server"):
        sources.extend(p for p in (workspace / directory).rglob("*.py")
                       if not any(part in {".venv", "__pycache__", "tests"}
                                  for part in p.relative_to(workspace).parts))
    sources.extend(workspace / "scripts" / name for name in (
        "validate_harness_terminal_progress.py", "harness_full100_world.py",
        "harness_v2_world.py", "harness_v2_online.py"))
    checksums = {}
    for path in sorted(set(sources)):
        name = str(path.relative_to(workspace))
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(data)
        checksums[name] = sha(data)
    return checksums


def public_flight_diagnostics(requests):
    """Join only skill public reads to subsequent actions, after execution ends."""
    latest, pending_takeoff, seen_trigger = {}, {}, set()
    actions, triggers, takeoffs, idle_moves, idle_errors = [], [], [], [], []
    for row in requests:
        if (row.get("phase") != "task" or row.get("admin")
                or row.get("origin", {}).get("kind") != "skill"):
            continue
        tool = row["origin"].get("tool")
        response = row.get("response")
        observed = response if tool == "list_drones" and isinstance(response, list) else (
            [response] if tool == "get_drone_status" and isinstance(response, dict) else [])
        for drone in observed:
            if not isinstance(drone, dict) or not drone.get("id"):
                continue
            actor = str(drone["id"])
            state = {"request_id": row["request_id"], "tool": tool, "actor": actor,
                     **{k: drone.get(k) for k in ("name", "status", "position", "battery_level")}}
            latest[actor] = state
            if actor in pending_takeoff:
                pending_takeoff.pop(actor)["next_public_state"] = state
        if not row.get("action"):
            continue
        parts = row["endpoint"].strip("/").split("/")
        actor = parts[1] if len(parts) >= 2 and parts[0] == "drones" else None
        before = latest.get(actor)
        event = {"action_index": len(actions) + 1, "request_id": row["request_id"],
                 "actor": actor, "tool": tool,
                 "action": {k: row.get(k) for k in ("method", "endpoint", "params", "body")},
                 "public_state_before": before, "receipt": response,
                 "committed": row.get("committed")}
        actions.append(event)
        z = (before or {}).get("position") or {}
        z = z.get("z")
        nonflight = before is not None and before.get("status") in {"idle", "ready"}
        if nonflight and (before["request_id"], actor) not in seen_trigger:
            seen_trigger.add((before["request_id"], actor))
            triggers.append({**event, "positive_altitude": isinstance(z, (int, float))
                             and not isinstance(z, bool) and z > 0})
        if tool == "take_off":
            takeoff = {**event, "next_public_state": None}
            takeoffs.append(takeoff)
            pending_takeoff[actor] = takeoff
        if tool == "move_to" and nonflight:
            idle_moves.append(event)
        if tool == "move_to" and isinstance(response, dict) and "DroneStatus.IDLE" in str(response.get("message", "")):
            idle_errors.append(event)
    positive = [event for event in triggers if event["positive_altitude"]]
    return {"summary": {
        "physical_action_count": len(actions), "nonflight_trigger_count": len(triggers),
        "nonflight_trigger_actors": sorted({event["actor"] for event in triggers}),
        "positive_altitude_trigger_count": len(positive),
        "positive_altitude_trigger_actors": sorted({event["actor"] for event in positive}),
        "positive_altitude_takeoff_count": sum(event["tool"] == "take_off" for event in positive),
        "takeoff_count": len(takeoffs),
        "takeoff_success_count": sum(isinstance(event["receipt"], dict)
            and event["receipt"].get("status") == "success" for event in takeoffs),
        "move_with_nonflight_public_state_count": len(idle_moves),
        "move_idle_error_count": len(idle_errors)},
        "state_triggers": triggers, "takeoffs": takeoffs, "nonflight_moves": idle_moves,
        "idle_move_errors": idle_errors, "actions": actions}


def public_blocked_diagnostics(requests):
    """Offline labels from public receipts, separate from candidate policy logic.

    A destination conflict is not a path conflict. Different start positions
    remain separate path edges; no private obstacle map is consulted here.
    Counts describe attempted moves (including errors), not successful effects.
    """
    flight = public_flight_diagnostics(requests)
    by_id = {row["request_id"]: row for row in requests}
    moves, endpoint_groups, path_groups, failures = [], {}, {}, {}
    all_path_destinations = {}
    classification = {"success": 0, "endpoint_conflict": 0, "path_conflict": 0,
                      "other_error": 0, "partial": 0, "unknown": 0}
    missing_starts = 0

    def point(position):
        if not isinstance(position, dict):
            return None
        values = [position.get(k) for k in ("x", "y", "z")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            return None
        return values

    for action in flight["actions"]:
        if action["tool"] != "move_to":
            continue
        row = by_id[action["request_id"]]
        receipt = action["receipt"]
        message = str(receipt.get("message", "")) if isinstance(receipt, dict) else ""
        status = receipt.get("status") if isinstance(receipt, dict) else None
        lowered = message.lower()
        if status == "error" and lowered.startswith("target position conflicts with obstacle:"):
            kind = "endpoint_conflict"
        elif status == "error" and "flight path intersects with" in lowered:
            kind = "path_conflict"
        elif status == "error":
            kind = "other_error"
        elif status == "success":
            kind = "success"
        elif status == "partial_success":
            kind = "partial"
        else:
            kind = "unknown"
        classification[kind] += 1
        start = point((action.get("public_state_before") or {}).get("position"))
        destination = point(row.get("params"))
        if start is None:
            missing_starts += 1
        event = {**action, "plan_id": row.get("origin", {}).get("plan_id"),
                 "kind": kind, "public_start_xyz": start,
                 "requested_endpoint_xyz": destination, "message": message}
        if kind == "endpoint_conflict":
            # Independently audit exact public evidence after the run. The
            # broad historical error category above remains unchanged.
            after_row = None
            for later in requests:
                if (later["request_id"] <= row["request_id"] or later.get("phase") != "task"
                        or later.get("admin") or later.get("origin", {}).get("kind") != "skill"
                        or later.get("origin", {}).get("plan_id") != event["plan_id"]):
                    continue
                if later.get("action") and later.get("endpoint", "").startswith(f"/drones/{event['actor']}/"):
                    break  # Never validate an old failure using a later action's state.
                if (later.get("origin", {}).get("tool") == "get_drone_status"
                        and later.get("endpoint") == f"/drones/{event['actor']}"):
                    after_row = later
                    break
            after_state = (after_row or {}).get("response")
            after_xyz = point(after_state.get("position")) if isinstance(after_state, dict) else None
            exact_message = re.fullmatch(
                r"Target position conflicts with obstacle: Position conflicts with (?:circle|ellipse|polygon) obstacle '[^\n]+'",
                message) is not None
            contradictory_flags = any(receipt.get(key) is not None and receipt.get(key) is not False
                                      for key in ("success", "partial_success"))
            error_text = " ".join(receipt[key] for key in (
                "message", "error", "reason", "reason_code", "error_code", "blocked_reason")
                if isinstance(receipt.get(key), str))
            nongeometry = re.search(r"\bbattery\b|\bcurrent status\b|\b(?:invalid|missing|required)\b"
                                    r"|\baltitude\b[^.\n]*\b(?:maximum|negative|valid)\b",
                                    error_text, flags=re.IGNORECASE) is not None
            checks = {"exact_endpoint_message": exact_message,
                      "receipt_error": status == "error",
                      "no_conflicting_status_or_nongeometric_error": not contradictory_flags and not nongeometry,
                      "receipt_move_to": isinstance(receipt, dict) and receipt.get("command") == "move_to",
                      "receipt_actor_matches": isinstance(receipt, dict) and receipt.get("drone_id") == event["actor"],
                      "requested_xyz_known": destination is not None,
                      "post_state_actor_matches": isinstance(after_state, dict) and after_state.get("id") == event["actor"],
                      "public_xyz_unchanged": start is not None and after_xyz is not None
                          and all(abs(a - b) <= 1e-6 for a, b in zip(start, after_xyz))}
            event["shared_evidence_qualification"] = {
                "qualified": all(checks.values()), "checks": checks,
                "post_state_request_id": (after_row or {}).get("request_id"),
                "post_state_xyz": after_xyz, "source_file": "public-api.jsonl"}
        moves.append(event)
        if kind in {"endpoint_conflict", "path_conflict", "other_error"}:
            signature = {"kind": kind, "actor": event["actor"], "endpoint_xyz": destination}
            if kind != "endpoint_conflict":
                signature["public_start_xyz"] = start
            if kind == "other_error":
                signature["message"] = message
            if destination is None or kind != "endpoint_conflict" and start is None:
                signature["unresolved_request_id"] = event["request_id"]
            key = json.dumps(signature, sort_keys=True)
            failures.setdefault(key, {"signature": signature, "request_ids": []})["request_ids"].append(event["request_id"])
        if destination is None:
            continue  # Do not group missing endpoints under an invented location.
        key = (event["plan_id"], event["actor"], tuple(destination))
        if kind == "endpoint_conflict":
            endpoint_groups.setdefault(key, []).append(event)
        elif kind == "path_conflict":
            path_groups.setdefault(key, []).append(event)
            all_path_destinations.setdefault((event["plan_id"], tuple(destination)), []).append(event)

    def group_rows(groups, *, actor_scoped=True):
        rows = []
        for key, events in groups.items():
            starts = []
            for event in events:
                if event["public_start_xyz"] is not None and event["public_start_xyz"] not in starts:
                    starts.append(event["public_start_xyz"])
            # Only exact actor + observed start + endpoint duplicates are same
            # edge repeats. Missing starts never count as a known repeated edge.
            seen_edges, same_edges, new_starts = set(), 0, 0
            for i, event in enumerate(events):
                start = event["public_start_xyz"]
                if start is None:
                    continue
                edge = (event["actor"], tuple(start))
                if edge in seen_edges:
                    same_edges += 1
                elif seen_edges:
                    new_starts += 1
                seen_edges.add(edge)
            rows.append({"plan_id": key[0], "actor": key[1] if actor_scoped else None,
                         "actors": sorted({e["actor"] for e in events}),
                         "endpoint_xyz": list(key[-1]), "count": len(events),
                         "repeats_after_first": max(0, len(events) - 1),
                         "distinct_public_starts": starts,
                         "same_actor_start_endpoint_repeats": same_edges,
                         "later_new_actor_or_start_count": new_starts,
                         "request_ids": [e["request_id"] for e in events]})
        return rows

    endpoints = group_rows(endpoint_groups)
    paths = group_rows(path_groups)
    cross_actor_paths = group_rows(all_path_destinations, actor_scoped=False)
    repeated_endpoints = [r for r in endpoints if r["count"] > 1]
    repeated_paths = [r for r in paths if r["count"] > 1]
    return {"summary": {
        "move_attempt_count": len(moves), "classification": classification,
        "all_move_error_count": sum(classification[k] for k in ("endpoint_conflict", "path_conflict", "other_error")),
        "unique_failure_count": len(failures),
        "unique_actor_endpoint_conflict_count": len(endpoints),
        "same_actor_endpoint_conflict_repeat_count": sum(r["repeats_after_first"] for r in endpoints),
        "same_actor_endpoint_conflict_repeat_group_count": len(repeated_endpoints),
        "same_actor_path_edge_repeat_count": sum(r["same_actor_start_endpoint_repeats"] for r in paths),
        "same_actor_path_endpoint_changed_start_count": sum(r["later_new_actor_or_start_count"] for r in paths),
        "same_endpoint_path_multi_actor_or_start_group_count": sum(len(r["actors"]) > 1 or len(r["distinct_public_starts"]) > 1 for r in cross_actor_paths),
        "missing_public_start_count": missing_starts,
        "missing_requested_endpoint_count": sum(e["requested_endpoint_xyz"] is None for e in moves)},
        "endpoint_conflict_groups": endpoints,
        "repeated_same_actor_endpoint_conflicts": repeated_endpoints,
        "path_conflict_groups": paths,
        "repeated_same_actor_path_endpoints": repeated_paths,
        "path_conflicts_by_destination_across_actors": cross_actor_paths,
        "failure_signatures": list(failures.values()),
        "moves": moves,
        "scope": "same trial/plan and exact public actor/start/endpoint; public receipts only; diagnostic labels are not policy inputs"}


def public_shared_endpoint_diagnostics(blocked):
    """Cross-actor same-XYZ conflicts with overlap and evidence validity visible.

    This is an after-run diagnostic, not a policy input or a safety judgement
    about sharing. Every earlier source must have a smaller request_id in the
    same plan, a different actor, and exactly the same requested XYZ.
    """
    prior = {}
    events, overlap, missing_scope, unqualified = [], [], [], []
    for move in blocked["moves"]:
        if move["kind"] != "endpoint_conflict":
            continue
        if not move.get("shared_evidence_qualification", {}).get("qualified", False):
            unqualified.append(move)
        plan, actor, xyz = move.get("plan_id"), move.get("actor"), move.get("requested_endpoint_xyz")
        if plan is None or actor is None or xyz is None:
            missing_scope.append(move)
            continue
        key = (plan, tuple(xyz))
        earlier = [event for event in prior.get(key, []) if event["request_id"] < move["request_id"]]
        different_actor = [event for event in earlier if event["actor"] != actor]
        same_actor = [event for event in earlier if event["actor"] == actor]
        if different_actor:
            record = {"later_conflict": move, "earlier_different_actor_conflicts": different_actor,
                      "qualified_earlier_different_actor_conflicts": [event for event in different_actor
                          if event.get("shared_evidence_qualification", {}).get("qualified", False)],
                      "source_file": "public-api.jsonl"}
            record["qualified_source_and_consumer"] = bool(record["qualified_earlier_different_actor_conflicts"]) and move.get(
                "shared_evidence_qualification", {}).get("qualified", False)
            events.append(record)
            if same_actor:
                overlap.append({**record, "earlier_same_actor_conflicts": same_actor})
        prior.setdefault(key, []).append(move)
    broad_groups = [{"plan_id": plan, "endpoint_xyz": list(xyz),
               "actors": sorted({event["actor"] for event in records}),
               "request_ids": [event["request_id"] for event in records],
               "conflicts": records}
              for (plan, xyz), records in prior.items() if len({event["actor"] for event in records}) > 1]
    qualified_events = [event for event in events if event["qualified_source_and_consumer"]]
    qualified_overlap = [event for event in overlap if event["qualified_source_and_consumer"]]
    groups = []
    for group in broad_groups:
        records = [event for event in group["conflicts"]
                   if event.get("shared_evidence_qualification", {}).get("qualified", False)]
        actors = sorted({event["actor"] for event in records})
        if len(actors) > 1:
            groups.append({**group, "actors": actors, "conflicts": records,
                           "request_ids": [event["request_id"] for event in records]})
    return {"summary": {"cross_actor_endpoint_repeat_event_count": len(qualified_events),
                        "cross_actor_endpoint_repeat_group_count": len(groups),
                        "cross_actor_repeat_without_same_actor_overlap_count": len(qualified_events) - len(qualified_overlap),
                        "same_actor_overlap_count": len(qualified_overlap),
                        "broad_cross_actor_endpoint_repeat_event_count": len(events),
                        "broad_cross_actor_endpoint_repeat_group_count": len(broad_groups),
                        "broad_same_actor_overlap_count": len(overlap),
                        "unqualified_cross_actor_endpoint_repeat_event_count": len(events) - len(qualified_events),
                        "unqualified_endpoint_conflict_count": len(unqualified),
                        "missing_plan_actor_or_xyz_count": len(missing_scope)},
            "events": qualified_events, "groups": groups,
            "same_actor_overlap": qualified_overlap,
            "broad_events": events, "broad_groups": broad_groups,
            "broad_same_actor_overlap": overlap,
            "unqualified_endpoint_conflicts": unqualified,
            "missing_scope": missing_scope,
            "definition": "Main count is qualified inclusive: each later qualified endpoint-conflict request counts once when an earlier qualified different actor in the same plan failed at exact XYZ; same-actor overlap is separately reported, not subtracted. All path conflicts and same-actor-only sources are excluded. Broad prefix-based counts and invalid exact receipt/actor/command/post-state qualifications remain visible separately. Counts are recorded events/groups, not independent statistical samples."}


def historical_cost_comparison(previous, current):
    """Report actual cost/time deltas without making byte equality a run gate."""
    before, after = previous["cost"], current["cost"]
    changed = {}
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key), after.get(key)
        if left != right:
            row = {"historical": left, "current": right}
            if (isinstance(left, (int, float)) and not isinstance(left, bool)
                    and isinstance(right, (int, float)) and not isinstance(right, bool)):
                row["delta"] = right - left
            changed[key] = row
    energy = {"consumed_mah", "charged_mah"}
    return {"cost_exactly_equal": not changed,
            "same_cost_except_energy": {k: v for k, v in before.items() if k not in energy}
                == {k: v for k, v in after.items() if k not in energy},
            "changed_cost_fields": changed,
            "historical_elapsed_seconds": previous.get("elapsed_seconds"),
            "current_elapsed_seconds": current.get("elapsed_seconds"),
            "interpretation": "Same initial state/timeline/budgets do not force per-action simulation lockstep: advance(elapsed) uses wall time. Keep actual energy/time differences; exact cost equality is diagnostic, not a validation gate."}


def public_terminal_progress_diagnostics(requests, snapshot):
    """Read-only raw public evidence audit, independent of candidate decisions.

    The evaluator's later session read is kept separate from skill evidence.
    No simulator, production parser, private target state or checker is used.
    """
    expected_scene = snapshot["scene_id"]
    events = snapshot["events"]
    starts = [event for event in events if event["kind"] == "public_call_start"
              and event.get("call", {}).get("tool") == "get_mission_progress"]
    ends = {event["call_id"]: event for event in events if event["kind"] == "public_call_end"}
    skill_rows = [row for row in requests if row.get("phase") == "task" and not row.get("admin")
                  and row.get("origin", {}).get("kind") == "skill"]
    raw_progress = [row for row in skill_rows if row.get("origin", {}).get("tool") == "get_mission_progress"]
    actions = [row for row in skill_rows if row.get("action")]
    evaluator = [row for row in requests if row.get("phase") == "task" and not row.get("admin")
                 and row.get("origin", {}).get("kind") == "evaluator_public_observation"
                 and row.get("endpoint") == "/sessions/current"]
    issues, reads = [], []
    if len(raw_progress) != len(starts):
        issues.append("raw_skill_progress_count_differs_from_harness_call_starts")

    def public_value(row):
        metadata = row.get("response")
        stats = metadata.get("statistics") if isinstance(metadata, dict) else None
        value = stats.get("task_progress") if isinstance(stats, dict) else None
        percent = value.get("progress_percentage") if isinstance(value, dict) else None
        details = value.get("details") if isinstance(value, dict) else None
        counts = {key: details[key] for key in ("total_targets", "visited_targets", "unvisited_targets")
                  if isinstance(details, dict) and isinstance(details.get(key), int)
                  and not isinstance(details[key], bool) and details[key] >= 0}
        request_valid = (row.get("method") == "GET" and row.get("endpoint") == "/sessions/current"
                         and row.get("action") is False and row.get("committed") is True)
        valid = (request_valid and isinstance(metadata, dict) and str(metadata.get("id")) == str(expected_scene)
                 and not isinstance(percent, bool) and isinstance(percent, (int, float))
                 and math.isfinite(percent) and 0 <= percent <= 100)
        if "total_targets" in counts and "visited_targets" in counts:
            valid = valid and counts["visited_targets"] <= counts["total_targets"] and (
                "unvisited_targets" not in counts
                or counts["total_targets"] - counts["visited_targets"] == counts["unvisited_targets"])
            if value.get("task_type") == "target_assignment" and counts["total_targets"] > 0:
                valid = valid and int(counts["visited_targets"] / counts["total_targets"] * 100) == percent
        normalized = {"session_id": str(expected_scene), "task_type": value.get("task_type"),
                      "progress_percentage": percent, "details": counts,
                      "source_endpoint": "/sessions/current", "scope": "public_session_aggregate_not_full_task_verdict"} if valid else None
        return {"request_id": row["request_id"], "plan_id": row.get("origin", {}).get("plan_id"),
                "session_id": metadata.get("id") if isinstance(metadata, dict) else None,
                "progress_percentage": percent, "details": counts, "valid_public_progress": bool(valid),
                "public_read_request_contract_valid": request_valid,
                "normalized_public_receipt_sha256": sha(json.dumps(normalized, sort_keys=True, default=str).encode()) if valid else None,
                "source": "public-api.jsonl", "source_line": row["request_id"]}

    for index, row in enumerate(raw_progress):
        read = public_value(row)
        call = starts[index] if index < len(starts) else None
        end = ends.get(call["call_id"]) if call else None
        read.update(harness_call_id=(call or {}).get("call_id"), evidence_ref=(call or {}).get("evidence_id"),
                    call_start_sequence=(call or {}).get("sequence"), call_end_sequence=(end or {}).get("sequence"),
                    logged_receipt_sha256=(end or {}).get("receipt_sha256"),
                    skill_actions_before=sum(action["request_id"] < row["request_id"] for action in actions),
                    skill_actions_after=sum(action["request_id"] > row["request_id"] for action in actions))
        if (call is None or end is None or call.get("plan_id") != read["plan_id"]
                or end.get("plan_id") != read["plan_id"] or end.get("evidence_id") != read["evidence_ref"]):
            issues.append(f"progress_call_reference_mismatch:{row['request_id']}")
        if read["valid_public_progress"] and read["normalized_public_receipt_sha256"] != read["logged_receipt_sha256"]:
            issues.append(f"progress_public_receipt_hash_mismatch:{row['request_id']}")
        reads.append(read)
    final_public = public_value(evaluator[-1]) if evaluator else None
    last = reads[-1] if reads else None
    last_action_id = actions[-1]["request_id"] if actions else None
    frames = []
    for frame in snapshot["frames"]:
        if frame["skill"] != "public_search":
            continue
        state = frame["state"]
        recorded = state.get("terminal_progress_check")
        matched = next((read for read in reads if recorded and read["evidence_ref"] == recorded.get("evidence_ref")), None)
        if recorded and recorded.get("status") == "confirmed" and (
                matched is None or not matched["valid_public_progress"]
                or matched["plan_id"] != frame["plan_id"] or matched["skill_actions_after"] != 0
                or matched["progress_percentage"] != recorded.get("progress_percent")):
            issues.append("confirmed_terminal_progress_record_has_no_matching_public_evidence")
        plan_reads = [read for read in reads if read["plan_id"] == frame["plan_id"]]
        plan_last = plan_reads[-1] if plan_reads else None
        if frame["status"] == "completed" and (plan_last is None or not plan_last["valid_public_progress"]
                                                or plan_last["progress_percentage"] <= 95):
            issues.append("completed_search_without_skill_public_progress_over_95")
        frames.append({"plan_id": frame["plan_id"], "status": frame["status"], "reason": frame["reason"],
                       "cursor": frame["cursor"], "units": frame["units"], "api_calls": frame["api_calls"],
                       "cached_progress_percent": state.get("progress_percent"),
                       "public_stop_condition": state.get("public_stop_condition"),
                       "recorded_terminal_progress_check": recorded,
                       "matched_terminal_public_read": matched,
                       "runtime_completed_vs_final_public_threshold_agree": (
                           (frame["status"] == "completed") == (final_public["progress_percentage"] > 95)
                           if final_public and final_public["valid_public_progress"] else None)})
    return {"summary": {"skill_api_request_count": len(skill_rows), "skill_progress_read_count": len(reads),
                        "skill_action_count": len(actions), "last_skill_progress_request_id": last["request_id"] if last else None,
                        "last_skill_progress_percent": last["progress_percentage"] if last else None,
                        "skill_actions_after_last_progress": last["skill_actions_after"] if last else None,
                        "skill_progress_reads_after_last_action": sum(last_action_id is not None and read["request_id"] > last_action_id for read in reads),
                        "evaluator_public_progress_percent": final_public["progress_percentage"] if final_public else None,
                        "terminal_check_record_count": sum(frame["recorded_terminal_progress_check"] is not None for frame in frames),
                        "issues_count": len(issues)},
            "skill_progress_reads": reads, "last_skill_progress": last,
            "last_skill_action_request_id": last_action_id,
            "final_evaluator_public_read": final_public, "search_frames": frames, "issues": issues,
            "scope": "public progress and runtime reporting; evaluator read is never skill evidence or a full-task verdict"}


def first_public_request_divergence(left_requests, right_requests):
    def rows(requests):
        return [{"request_id": row["request_id"], "request": {**{key: row.get(key) for key in (
                    "method", "endpoint", "params", "body")}, "origin_kind": row.get("origin", {}).get("kind"),
                    "tool": row.get("origin", {}).get("tool")}}
                for row in requests if row.get("phase") == "task" and not row.get("admin")]
    left, right = rows(left_requests), rows(right_requests)
    index = 0
    while index < min(len(left), len(right)) and left[index]["request"] == right[index]["request"]:
        index += 1
    same = index == len(left) == len(right)
    return {"identical": same, "common_prefix_requests": index,
            "baseline_request_count": len(left), "candidate_request_count": len(right),
            "candidate_minus_baseline_requests": len(right) - len(left),
            "baseline_first_different": None if same or index == len(left) else left[index],
            "candidate_first_different": None if same or index == len(right) else right[index]}


def validate_static_timeline(timeline):
    """Evaluator-only schema verification; nothing is supplied to the policy."""
    required = {"second", "targets", "environments", "rng"}
    if not isinstance(timeline, list) or not timeline:
        raise ValueError("Static-obstacle contract needs a nonempty frozen timeline")
    for index, row in enumerate(timeline, 1):
        if (not isinstance(row, dict) or set(row) != required
                or row["second"] != index or isinstance(row["second"], bool)
                or not isinstance(row["targets"], dict)
                or not isinstance(row["environments"], dict)):
            raise ValueError("Timeline outside frozen target/environment-only schema")
    return {"events": len(timeline), "top_level_fields": sorted(required),
            "mutable_world_entities": ["targets", "environments"],
            "obstacle_events": 0,
            "advance_source": "source/scripts/harness_v2_world.py:World.advance"}


def obstacle_geometry_audit(world, blob):
    """Hash private evaluator state; never expose geometry to a skill/ledger."""
    state = world.inspect_blob(blob)
    obstacles = state["controllers"]["obstacle_controller"]["obstacles"]
    if not isinstance(obstacles, dict):
        raise ValueError("Unknown evaluator obstacle schema")
    keys = ("type", "position", "coordinates", "radius", "width", "length", "vertices", "height", "dimensions")
    geometry = {oid: {key: obstacle.get(key) for key in keys}
                for oid, obstacle in obstacles.items()}
    data = json.dumps(geometry, sort_keys=True, ensure_ascii=False, default=str).encode()
    return {"sha256": sha(data), "obstacle_count": len(obstacles),
            "fields": list(keys), "scope": "evaluator_only_not_policy_input"}


def task_request_allowed(method, endpoint, admin=False):
    if admin:
        return False
    if method.upper() == "GET":
        return endpoint in {"/drones", "/sessions/current"} or re.fullmatch(r"/drones/[^/]+(?:/nearby)?", endpoint) is not None
    return (method.upper() == "POST" and re.fullmatch(
        r"/drones/[^/]+/command/(?:take_off|change_altitude|rotate|hover|move_to)", endpoint) is not None)


def failure_comparison(baseline, candidate):
    """Pair-level new/removed failures, not a claim that exploration regressed."""
    groups = [{json.dumps(row["signature"], sort_keys=True): row
               for row in item["failure_signatures"]} for item in (baseline, candidate)]
    left, right = (set(rows) for rows in groups)
    return {"baseline_unique_failure_count": len(left), "candidate_unique_failure_count": len(right),
            "shared_unique_failure_count": len(left & right),
            "new_unique_failure_count": len(right - left),
            "removed_unique_failure_count": len(left - right),
            "new_unique_failures": [groups[1][key] for key in sorted(right - left)],
            "removed_unique_failures": [groups[0][key] for key in sorted(left - right)],
            "identity": "endpoint conflict: actor+XYZ; path conflict: actor+public start XYZ+endpoint XYZ; other error adds message; plan IDs omitted only for this same-task paired comparison"}


def first_action_divergence(baseline, candidate):
    left, right = baseline["actions"], candidate["actions"]
    index = 0
    while index < min(len(left), len(right)) and left[index]["action"] == right[index]["action"]:
        index += 1
    same = index == len(left) == len(right)
    return {"identical": same, "common_prefix_actions": index,
            "first_different_action_index": None if same else index + 1,
            "baseline_action": None if same or index == len(left) else left[index],
            "candidate_action": None if same or index == len(right) else right[index],
            "baseline_action_count": len(left), "candidate_action_count": len(right)}


def result_outline(result):
    response = result.get("response") or {}
    return {"status": response.get("status"), "reason": response.get("reason"),
            "cursor": response.get("cursor"), "checker": result.get("checker"),
            "public_progress": result.get("public_progress"), "cost": result.get("cost"),
            "flight_state_metrics": result.get("flight_state_metrics"),
            "blocked_candidate_metrics": result.get("blocked_candidate_metrics"),
            "shared_endpoint_metrics": result.get("shared_endpoint_metrics"),
            "terminal_progress_metrics": result.get("terminal_progress_metrics"),
            "static_obstacle_contract": result.get("static_obstacle_contract"),
            "plan_counters": result.get("plan_counters")}


def load_requests(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def worker(args):
    campaign = args.campaign.resolve()
    manifest = json.loads((campaign / "manifest.json").read_text())
    source = campaign / "source"
    study = Path(manifest["study"])
    check_hashes(source, manifest["sources"])
    check_hashes(study, manifest["inputs"])
    if args.task_id not in dict(TASKS) or args.revision not in REVISIONS:
        raise ValueError("Trial outside fixed protocol")
    # Runtime sources are copied before execution; live implementation changes
    # cannot modify a running or later trial within this campaign.
    sys.path[:0] = [str(source / "scripts"), str(source / "agent4drone"), str(source / "server")]
    sys.path.extend(str(p) for p in (args.workspace / "server/.venv/lib").glob("python*/site-packages"))
    network_attempts = block_network()
    from harness_full100_world import FullWorld, origin
    from harness_v2_world import digest
    from harness_v2_online import client_class
    from blackboard import PerceptionBlackboard
    from harness_core import HarnessRuntime
    from harness_skills import make_skill_factory
    from coordinated_search_r2 import MAX_ROUNDS
    from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2, public_progress

    trial_id = f"{args.task_id}-{args.revision}"
    output = campaign / "trials" / trial_id
    output.mkdir(parents=True, exist_ok=False)
    fixture = json.loads((study / "fixtures" / f"{args.task_id}.json").read_text())
    blob = (study / "fixtures" / f"{args.task_id}.pkl").read_bytes()
    timeline = (study / "fixtures" / fixture["timeline_file"]).read_bytes()
    if sha(blob) != fixture["snapshot_sha256"] or sha(timeline) != fixture["timeline_sha256"]:
        raise ValueError("Fixture hash mismatch before loading pickle")
    class StaticWriterWorld(FullWorld):
        """Same writer isolation for both arms; no task map/admin mutations."""
        def __init__(self, *values, **options):
            super().__init__(*values, **options)
            self.denied_task_requests = []

        def request(self, method, endpoint, *, admin=False, **options):
            if self.phase == "task" and not task_request_allowed(method, endpoint, admin):
                self.denied_task_requests.append({"method": method, "endpoint": endpoint, "admin": admin})
                raise PermissionError("Task request outside isolated static-obstacle writer contract")
            return super().request(method, endpoint, admin=admin, **options)

    world = StaticWriterWorld(output=output, deadline_seconds=RUNTIME_SECONDS)
    world.restore(blob, fixture["snapshot_sha256"])
    world.timeline = pickle.loads(timeline)
    timeline_contract = validate_static_timeline(world.timeline)
    transport = getattr(world.client, "_transport", None)
    if (type(transport).__module__ != "starlette.testclient"
            or type(transport).__name__ != "_TestClientTransport"
            or getattr(transport, "app", None) is not world.server.app
            or getattr(world.client, "portal", None) is not None):
        raise ValueError("Static writer contract requires local TestClient without lifespan/background server")
    geometry_before = obstacle_geometry_audit(world, blob)
    if obstacle_geometry_audit(world, world.snapshot()) != geometry_before:
        raise ValueError("Restored obstacle geometry differs from original fixture")
    contract = {"contract": "static_obstacles_no_external_writers", "validated_before_start": True,
                "runtime_parameter": True,
                "source": "this isolated local ASGI runner, not inferred from empty perception",
                "new_process_per_trial": True, "socket_connections_blocked": True,
                "transport": "starlette.testclient._TestClientTransport", "lifespan_started": False,
                "task_writer_guard": "task_request_allowed; admin/map writes rejected before server",
                "writer_guard_scope": "task only; public final session read allowed; hidden checker runs later in scoring phase",
                "isolation_source_hashes": {name: manifest["sources"][name] for name in (
                    "scripts/validate_harness_terminal_progress.py", "scripts/harness_full100_world.py",
                    "scripts/harness_v2_world.py")},
                "timeline": timeline_contract, "geometry_before": geometry_before,
                "private_geometry_sent_to_policy": False}
    write(output / "static-obstacle-contract.json", contract)
    initial_hash = world.state_digest()
    if initial_hash != fixture["state_sha256"]:
        raise ValueError("Restored state differs from original fixture")
    # Public-state hash check is evaluator preparation only; never ingest it.
    seed_hash = digest(world.public_seed())
    if seed_hash != fixture["public_startup_sha256"]:
        raise ValueError("Public initial state differs from original fixture")
    world.restore(blob, fixture["snapshot_sha256"])
    board = PerceptionBlackboard(fixture["scene_id"])
    index = SessionEvidenceIndexR2(fixture["scene_id"])
    board.task_evidence_index = index
    ledger = TaskEvidenceLedgerR2(fixture["command"], trial_id, index=index,
                                  inherited_blackboard=None, max_summary_chars=3900)
    client = client_class(world)(base_url="http://testserver")
    allowed = {"list_drones", "get_drone_status", "take_off", "change_altitude",
               "rotate", "hover", "move_to", "get_nearby_entities"}
    runtime = None

    def invoke(call):
        frame = next((f for f in runtime.frames.values() if f.status == "running"), None)
        with origin(kind="skill", tool=call.tool, plan_id=frame.plan_id if frame else None):
            if call.tool == "get_mission_progress":
                return public_progress(client.get_current_session(), board.session_id)
            if call.tool not in allowed:
                raise PermissionError("Tool outside fixed public allowlist")
            return getattr(client, call.tool)(**call.args)

    world.start(timer=True)
    client._harness_deadline = world.started + RUNTIME_SECONDS
    original_factory = make_skill_factory(board)

    def checked_factory(*factory_args):
        frame, generator = original_factory(*factory_args)
        if (frame.api_budget != 2000 or frame.repair_budget != 16 or MAX_ROUNDS != 16
                or frame.deadline != world.started + RUNTIME_SECONDS):
            raise AssertionError("Runtime budgets differ from fixed protocol")
        return frame, generator

    runtime = HarnessRuntime(task_id=trial_id, command=fixture["command"],
        scene_id=board.session_id, scene_epoch=index.epoch, ledger=ledger,
        invoke=invoke, skill_factory=checked_factory,
        scope=lambda: (board.session_id, index.epoch), started=world.started,
        scheduler="applicability", revision=args.revision,
        static_obstacle_contract=True)
    write(output / "start.json", {"trial_id": trial_id, "pid": os.getpid(),
        "revision": args.revision, "model": None, "scripted_start": True,
        "initial_state_sha256": initial_hash, "public_startup_sha256": seed_hash,
        "board_initial": board.full(), "task_id": args.task_id,
        "scene_id": fixture["scene_id"], "command": fixture["command"],
        "deadline_seconds": RUNTIME_SECONDS, "watchdog_seconds": WATCHDOG_SECONDS})
    error = None
    controls = []
    response = None
    try:
        response = runtime.control("start", skill=dict(TASKS)[args.task_id])
        controls.append({"operation": "scripted_start", "response": response})
        # Applicability executes until terminal; do not add a recovery policy.
        if response.get("status") in {"ready", "paused", "running"}:
            raise AssertionError("Unexpected nonterminal state under applicability")
        before = {"world": world.state_digest(), "frames": digest(runtime.snapshot()["frames"]),
                  "api_requests": len(world.requests), "events": len(world.physical_events)}
        for operation in ("repeat_start", "terminal_resume"):
            if operation == "repeat_start":
                reply = runtime.control("start", skill=dict(TASKS)[args.task_id])
            elif response.get("plan_id"):
                reply = runtime.control("resume", plan_id=response["plan_id"])
            else:
                reply = runtime.control("resume", plan_id="absent-plan-negative-control")
            after = {"world": world.state_digest(), "frames": digest(runtime.snapshot()["frames"]),
                     "api_requests": len(world.requests), "events": len(world.physical_events)}
            controls.append({"operation": operation, "response": reply,
                             "before": before, "after": after, "no_effect": before == after})
            if before != after:
                raise AssertionError("Repeated or terminal control altered plan/world or made API calls")
            if response.get("plan_id"):
                if reply.get("status") != "rejected" or reply.get("resumable") is not False:
                    raise AssertionError("Terminal control was not explicitly rejected")
        with origin(kind="evaluator_public_observation"):
            final_progress = public_progress(client.get_current_session(), board.session_id)
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        final_progress = None
    finally:
        world.freeze_score(reason="offline_skill_ended" if error is None else "offline_error")
    geometry_after = obstacle_geometry_audit(world, world.score_blob)
    contract.update(geometry_after=geometry_after,
                    geometry_unchanged=geometry_after == geometry_before,
                    denied_task_requests=world.denied_task_requests)
    write(output / "static-obstacle-contract.json", contract)
    if (geometry_after != geometry_before or world.denied_task_requests) and error is None:
        error = {"type": "StaticObstacleContractViolation", "message": "Evaluator geometry or task writer guard changed"}
    # Only the independent evaluator sees the original hidden check tree.
    checked = world.evaluate(args.task_id, world.score_blob)
    check_hashes(source, manifest["sources"])
    check_hashes(study, manifest["inputs"])
    write(output / "controls.json", controls)
    harness_snapshot = runtime.snapshot()
    write(output / "harness.json", harness_snapshot)
    write(output / "score-world.json", world.inspect_blob(world.score_blob))
    write(output / "board-final.json", board.full())
    diagnostics = public_flight_diagnostics(world.requests)
    write(output / "flight-state-diagnostics.json", diagnostics)
    blocked_diagnostics = public_blocked_diagnostics(world.requests)
    write(output / "blocked-candidate-diagnostics.json", blocked_diagnostics)
    shared_diagnostics = public_shared_endpoint_diagnostics(blocked_diagnostics)
    write(output / "shared-endpoint-diagnostics.json", shared_diagnostics)
    terminal_diagnostics = public_terminal_progress_diagnostics(world.requests, harness_snapshot)
    write(output / "terminal-progress-diagnostics.json", terminal_diagnostics)
    if terminal_diagnostics["issues"] and error is None:
        error = {"type": "TerminalProgressAuditMismatch", "message": terminal_diagnostics["issues"]}
    result = {"trial_id": trial_id, "task_id": args.task_id, "scene_id": fixture["scene_id"],
        "revision": args.revision, "skill": dict(TASKS)[args.task_id], "model": None,
        "status": "verified" if error is None and not checked["errors"] and not network_attempts else "error",
        "error": error, "network_attempts": network_attempts, "model_calls": 0,
        "initial_state_sha256": initial_hash, "public_startup_sha256": seed_hash,
        "end_state_sha256": digest(world.inspect_blob(world.score_blob)),
        "snapshot_sha256": sha(world.score_blob), "response": response,
        "controls": controls, "public_progress": final_progress, "checker": checked,
        "flight_state_metrics": diagnostics["summary"],
        "blocked_candidate_metrics": blocked_diagnostics["summary"],
        "shared_endpoint_metrics": shared_diagnostics["summary"],
        "terminal_progress_metrics": terminal_diagnostics["summary"],
        "static_obstacle_contract": {"runtime_parameter": contract["runtime_parameter"],
            "validated_before_start": True, "geometry_unchanged": contract["geometry_unchanged"],
            "denied_task_request_count": len(world.denied_task_requests)},
        "plan_counters": [{key: getattr(frame, key) for key in (
            "skill", "status", "reason", "cursor", "units", "api_calls", "api_budget", "repairs", "repair_budget")}
            for frame in runtime.frames.values()],
        "cost": world.cost(), "elapsed_seconds": world.score_seconds,
        "source_hashes_verified_after": True, "fixture_hashes_verified_after": True}
    write(output / "result.json", result)
    write(output / "seal.json", {"files": {str(p.relative_to(output)): sha(p.read_bytes())
        for p in output.rglob("*") if p.is_file()}})
    world.client.close()
    print(json.dumps({k: result[k] for k in ("trial_id", "status", "error", "elapsed_seconds")}))
    return 0 if result["status"] == "verified" else 1


def campaign(args):
    workspace = args.workspace.resolve()
    study = workspace / "experiments/minimal-harness-full100-v1"
    protocol = workspace / "docs/results/harness-terminal-progress-20261023/PROTOCOL_CN.md"
    if not protocol.is_file():
        raise ValueError("Write protocol before execution")
    inputs, fixtures = fixture_inputs(workspace, study)
    historical_root = workspace / HISTORICAL_RUN
    historical_seal = historical_root / "campaign-seal.json"
    historical_hashes = json.loads(historical_seal.read_text())["files"]
    check_hashes(historical_root, historical_hashes)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = source_snapshot(workspace, output / "source")
    shutil.copyfile(protocol, output / "PROTOCOL_CN.md")
    manifest = {"study": str(study), "created_unix": time.time(), "sources": sources,
        "inputs": inputs, "protocol_sha256": sha(protocol.read_bytes()),
        "task_skill_pairs": TASKS, "revisions": REVISIONS, "expected_trials": 14,
        "network": "socket connections blocked in every trial; local ASGI only",
        "runtime_seconds": RUNTIME_SECONDS, "worker_watchdog_seconds": WATCHDOG_SECONDS,
        "api_budget": 2000, "repair_budget": 16, "search_max_rounds": 16,
        "historical_run": HISTORICAL_RUN, "historical_seal_sha256": sha(historical_seal.read_bytes()),
        "python": sys.version, "python_executable": sys.executable, "platform": platform.platform(),
        "model": None, "scripted_start": True, "scope": "conditional offline functionality"}
    write(output / "manifest.json", manifest)
    results = []
    # Fixed order, alternating which revision executes first for each task.
    for index, (task_id, _) in enumerate(TASKS):
        for revision in REVISIONS[::1 if index % 2 == 0 else -1]:
            trial_id = f"{task_id}-{revision}"
            cmd = [sys.executable, str(output / "source/scripts/validate_harness_terminal_progress.py"),
                   "--worker", "--workspace", str(workspace), "--campaign", str(output),
                   "--task-id", task_id, "--revision", revision]
            with (output / f"{trial_id}.log").open("w") as log:
                try:
                    completed = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                        cwd=workspace, timeout=WATCHDOG_SECONDS, env={**os.environ,
                        "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"})
                    returncode = completed.returncode
                except subprocess.TimeoutExpired:
                    returncode = "watchdog_timeout"
            result_path = output / "trials" / trial_id / "result.json"
            result = json.loads(result_path.read_text()) if result_path.exists() else {
                "trial_id": trial_id, "task_id": task_id, "revision": revision,
                "status": "incomplete", "checker": None}
            result["process_returncode"] = returncode
            results.append(result)
            write(output / "results.json", results)
            print(json.dumps({"trial_id": trial_id, "status": result["status"],
                "process_returncode": returncode, "checker_passed": (result.get("checker") or {}).get("passed"),
                "public_progress": (result.get("public_progress") or {}).get("progress_percentage")}), flush=True)
            if returncode != 0:
                break
        if results[-1]["process_returncode"] != 0:
            break
    check_hashes(study, inputs)
    check_hashes(study, json.loads((study / "manifest.json").read_text())["files"])
    check_hashes(output / "source", sources)
    check_hashes(historical_root, historical_hashes)
    paired = []
    for task_id, _ in TASKS:
        pair = [r for r in results if r["task_id"] == task_id]
        comparison = {"task_id": task_id, "same_initial_hash": len(pair) == 2 and
            pair[0].get("initial_state_sha256") == pair[1].get("initial_state_sha256") == fixtures[task_id]["state_sha256"]}
        by_revision = {r["revision"]: r for r in pair}
        if len(pair) == 2 and all(r["status"] == "verified" for r in pair):
            baseline, candidate = (by_revision[revision] for revision in REVISIONS)
            diagnostics = [json.loads((output / "trials" / r["trial_id"] / "flight-state-diagnostics.json").read_text())
                           for r in (baseline, candidate)]
            blocked_diagnostics = [json.loads((output / "trials" / r["trial_id"] / "blocked-candidate-diagnostics.json").read_text())
                                   for r in (baseline, candidate)]
            contracts = [json.loads((output / "trials" / r["trial_id"] / "static-obstacle-contract.json").read_text())
                         for r in (baseline, candidate)]
            terminal = [json.loads((output / "trials" / r["trial_id"] / "terminal-progress-diagnostics.json").read_text())
                        for r in (baseline, candidate)]
            requests = [load_requests(output / "trials" / r["trial_id"] / "public-api.jsonl")
                        for r in (baseline, candidate)]
            comparison.update(baseline=result_outline(baseline), candidate=result_outline(candidate),
                              same_initial_obstacle_geometry=contracts[0]["geometry_before"] == contracts[1]["geometry_before"],
                              both_static_obstacle_contracts_verified=all(c["validated_before_start"]
                                  and c["geometry_unchanged"] and not c["denied_task_requests"] for c in contracts),
                              action_comparison=first_action_divergence(*diagnostics),
                              public_request_comparison=first_public_request_divergence(*requests),
                              terminal_progress_comparison={
                                  "baseline_search_frames": terminal[0]["search_frames"],
                                  "candidate_search_frames": terminal[1]["search_frames"],
                                  "candidate_minus_baseline_skill_progress_reads": terminal[1]["summary"]["skill_progress_read_count"] - terminal[0]["summary"]["skill_progress_read_count"],
                                  "candidate_minus_baseline_skill_api": terminal[1]["summary"]["skill_api_request_count"] - terminal[0]["summary"]["skill_api_request_count"],
                                  "candidate_minus_baseline_total_task_api": candidate["cost"]["api_requests"] - baseline["cost"]["api_requests"]},
                              failure_comparison=failure_comparison(*blocked_diagnostics))
        paired.append(comparison)
    historical = []
    for result in results:
        if result["revision"] != REVISIONS[0] or result["status"] != "verified":
            continue
        previous = workspace / HISTORICAL_RUN / "trials" / result["trial_id"]
        old_result = json.loads((previous / "result.json").read_text())
        old_diagnostic = public_flight_diagnostics(load_requests(previous / "public-api.jsonl"))
        old_blocked = public_blocked_diagnostics(load_requests(previous / "public-api.jsonl"))
        old_terminal = public_terminal_progress_diagnostics(load_requests(previous / "public-api.jsonl"),
            json.loads((previous / "harness.json").read_text()))
        new_diagnostic = json.loads((output / "trials" / result["trial_id"] / "flight-state-diagnostics.json").read_text())
        historical.append({"task_id": result["task_id"], "historical_trial": str(previous.relative_to(workspace)),
            "source_file_hashes": {name: sha((previous / name).read_bytes()) for name in ("result.json", "public-api.jsonl", "harness.json")},
            "same_initial_hash": result["initial_state_sha256"] == old_result["initial_state_sha256"],
            "same_checker": result["checker"] == old_result["checker"],
            "same_cost": result["cost"] == old_result["cost"],
            "cost_comparison": historical_cost_comparison(old_result, result),
            "same_public_progress": result["public_progress"] == old_result["public_progress"],
            "same_stop_reason": result["response"].get("reason") == old_result["response"].get("reason"),
            "same_blocked_candidate_metrics": result["blocked_candidate_metrics"] == old_blocked["summary"],
            "same_shared_endpoint_metrics": result["shared_endpoint_metrics"] == public_shared_endpoint_diagnostics(old_blocked)["summary"],
            "same_terminal_progress_metrics": result["terminal_progress_metrics"] == old_terminal["summary"],
            "action_comparison": first_action_divergence(old_diagnostic, new_diagnostic)})
    write(output / "historical-baseline-comparison.json", historical)
    summary = {"expected_trials": 14, "attempted_trials": len(results),
        "verified_trials": sum(r["status"] == "verified" for r in results),
        "unstarted_trials": 14 - len(results), "pairs": paired,
        "all_original_fixture_hashes_unchanged": True, "frozen_sources_unchanged": True,
        "live_source_changes_since_freeze": [name for name, checksum in sources.items()
            if sha((workspace / name).read_bytes()) != checksum],
        "interpretation": "Scripted skill selection, no model calls or raw-tool takeover; not online task success or generalization."}
    write(output / "summary.json", summary)
    write(output / "campaign-seal.json", {"files": {str(p.relative_to(output)): sha(p.read_bytes())
        for p in output.rglob("*") if p.is_file()}})
    return 0 if summary["verified_trials"] == 14 and all(
        p["same_initial_hash"] and p.get("same_initial_obstacle_geometry")
        and p.get("both_static_obstacle_contracts_verified") for p in paired) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output", type=Path)
    p.add_argument("--worker", action="store_true")
    p.add_argument("--campaign", type=Path)
    p.add_argument("--task-id")
    p.add_argument("--revision", choices=REVISIONS)
    args = p.parse_args()
    if args.worker:
        if not all((args.campaign, args.task_id, args.revision)):
            p.error("worker requires campaign, task-id and revision")
        return worker(args)
    if args.output is None:
        p.error("campaign requires a new --output directory")
    return campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
