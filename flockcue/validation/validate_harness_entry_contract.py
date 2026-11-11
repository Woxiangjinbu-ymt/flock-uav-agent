"""Offline, source-frozen real-simulator comparison of two entry contracts.

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
import os
import pickle
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
REVISIONS = ("legacy", "entry_contract_v2")
RUNTIME_SECONDS = 500
WATCHDOG_SECONDS = 90


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
        raise RuntimeError("Network is forbidden in entry-contract validation")

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
        "validate_harness_entry_contract.py", "harness_full100_world.py",
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
    runtime = HarnessRuntime(task_id=trial_id, command=fixture["command"],
        scene_id=board.session_id, scene_epoch=index.epoch, ledger=ledger,
        invoke=invoke, skill_factory=make_skill_factory(board),
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
            if args.revision == "entry_contract_v2" and response.get("plan_id"):
                if reply.get("status") != "rejected" or reply.get("resumable") is not False:
                    raise AssertionError("V2 terminal control was not explicitly rejected")
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
    result = {"trial_id": trial_id, "task_id": args.task_id, "scene_id": fixture["scene_id"],
        "revision": args.revision, "skill": dict(TASKS)[args.task_id], "model": None,
        "status": "verified" if error is None and not checked["errors"] and not network_attempts else "error",
        "error": error, "network_attempts": network_attempts, "model_calls": 0,
        "initial_state_sha256": initial_hash, "public_startup_sha256": seed_hash,
        "end_state_sha256": digest(world.inspect_blob(world.score_blob)),
        "snapshot_sha256": sha(world.score_blob), "response": response,
        "controls": controls, "public_progress": final_progress, "checker": checked,
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
    protocol = workspace / "docs/results/harness-entry-contract-20261023/PROTOCOL_CN.md"
    if not protocol.is_file():
        raise ValueError("Write protocol before execution")
    inputs, fixtures = fixture_inputs(workspace, study)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = source_snapshot(workspace, output / "source")
    shutil.copyfile(protocol, output / "PROTOCOL_CN.md")
    manifest = {"study": str(study), "created_unix": time.time(), "sources": sources,
        "inputs": inputs, "protocol_sha256": sha(protocol.read_bytes()),
        "task_skill_pairs": TASKS, "revisions": REVISIONS, "expected_trials": 14,
        "network": "socket connections blocked in every trial; local ASGI only",
        "runtime_seconds": RUNTIME_SECONDS, "worker_watchdog_seconds": WATCHDOG_SECONDS,
        "model": None, "scripted_start": True, "scope": "conditional offline functionality"}
    write(output / "manifest.json", manifest)
    results = []
    # Fixed order, alternating which revision executes first for each task.
    for index, (task_id, _) in enumerate(TASKS):
        for revision in REVISIONS[::1 if index % 2 == 0 else -1]:
            trial_id = f"{task_id}-{revision}"
            cmd = [sys.executable, str(output / "source/scripts/validate_harness_entry_contract.py"),
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
    check_hashes(output / "source", sources)
    paired = []
    for task_id, _ in TASKS:
        pair = [r for r in results if r["task_id"] == task_id]
        paired.append({"task_id": task_id, "same_initial_hash": len(pair) == 2 and
            pair[0].get("initial_state_sha256") == pair[1].get("initial_state_sha256") == fixtures[task_id]["state_sha256"]})
    summary = {"expected_trials": 14, "attempted_trials": len(results),
        "verified_trials": sum(r["status"] == "verified" for r in results),
        "unstarted_trials": 14 - len(results), "pairs": paired,
        "all_original_fixture_hashes_unchanged": True, "frozen_sources_unchanged": True,
        "live_source_changes_since_freeze": [name for name, checksum in sources.items()
            if sha((workspace / name).read_bytes()) != checksum],
        "interpretation": "Scripted skill selection, no model calls or raw-tool takeover; not online task success or generalization."}
    write(output / "summary.json", summary)
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
