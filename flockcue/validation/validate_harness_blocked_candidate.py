"""Offline, source-frozen real-simulator comparison of blocked-candidate feedback handling.

No language model is created. The driver explicitly selects a skill and calls
start; these results cannot establish autonomous skill choice or generalization.
Each trial is a fresh subprocess with socket connections blocked. Original
full100 fixtures and their manifest are read only and hash checked before pickle.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import hashlib
import json
import math
import os
import pickle
import platform
import shutil
import socket
import subprocess
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
REVISIONS = ("flight_state_v3", "blocked_candidate_v4")
RUNTIME_SECONDS = 500
WATCHDOG_SECONDS = 90
HISTORICAL_RUN = "docs/results/harness-flight-state-20261023/run-20261023-01"


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
        raise RuntimeError("Network is forbidden in blocked-candidate validation")

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
        "validate_harness_blocked_candidate.py", "harness_full100_world.py",
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
    world = FullWorld(output=output, deadline_seconds=RUNTIME_SECONDS)
    world.restore(blob, fixture["snapshot_sha256"])
    world.timeline = pickle.loads(timeline)
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
        scheduler="applicability", revision=args.revision)
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
    # Only the independent evaluator sees the original hidden check tree.
    checked = world.evaluate(args.task_id, world.score_blob)
    check_hashes(source, manifest["sources"])
    check_hashes(study, manifest["inputs"])
    write(output / "controls.json", controls)
    write(output / "harness.json", runtime.snapshot())
    write(output / "score-world.json", world.inspect_blob(world.score_blob))
    write(output / "board-final.json", board.full())
    diagnostics = public_flight_diagnostics(world.requests)
    write(output / "flight-state-diagnostics.json", diagnostics)
    blocked_diagnostics = public_blocked_diagnostics(world.requests)
    write(output / "blocked-candidate-diagnostics.json", blocked_diagnostics)
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
    protocol = workspace / "docs/results/harness-blocked-candidate-20261023/PROTOCOL_CN.md"
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
            cmd = [sys.executable, str(output / "source/scripts/validate_harness_blocked_candidate.py"),
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
            comparison.update(baseline=result_outline(baseline), candidate=result_outline(candidate),
                              action_comparison=first_action_divergence(*diagnostics),
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
        new_diagnostic = json.loads((output / "trials" / result["trial_id"] / "flight-state-diagnostics.json").read_text())
        historical.append({"task_id": result["task_id"], "historical_trial": str(previous.relative_to(workspace)),
            "source_file_hashes": {name: sha((previous / name).read_bytes()) for name in ("result.json", "public-api.jsonl")},
            "same_initial_hash": result["initial_state_sha256"] == old_result["initial_state_sha256"],
            "same_checker": result["checker"] == old_result["checker"],
            "same_cost": result["cost"] == old_result["cost"],
            "same_public_progress": result["public_progress"] == old_result["public_progress"],
            "same_stop_reason": result["response"].get("reason") == old_result["response"].get("reason"),
            "same_blocked_candidate_metrics": result["blocked_candidate_metrics"] == old_blocked["summary"],
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
    return 0 if summary["verified_trials"] == 14 and all(p["same_initial_hash"] for p in paired) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, default=next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir()))
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
