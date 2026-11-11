"""Run the two registered teacher sessions locally with fixed DeepSeek-format replies."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import zipfile

from flockcue_session_cpu.__main__ import POOL, ROOT, read, run_session_cli, sha, verify_files, write
from thinking_events.campaign_storage import atomic_write, json_bytes


PILOT = ROOT / "docs/specs/04-deepseek-pilot40/pilot40.json"
PILOT_SHA = "6d7af90aa614d78c79b45fdeb35f88992b436a07bd46d21a956c32d3ca2fbc1e"
CASES = ("normal", "budget-stop", "missing-usage")


def load_pilot():
    if sha(PILOT) != PILOT_SHA:
        raise ValueError("The registered 40-task pilot changed")
    plan = read(PILOT)
    verify_files(POOL, plan["source_sha256"])
    with zipfile.ZipFile(POOL / "sessions-600.zip") as scenes:
        for spec in plan["sessions"]:
            data = scenes.read(spec["scene_zip_member"])
            if hashlib.sha256(data).hexdigest() != spec["scene_sha256"]:
                raise ValueError("Unexpected frozen pilot scene")
            scene = json.loads(data)
            orders = [{"index": i, "task_id": t["id"],
                       "command_sha256": hashlib.sha256(t["content"].encode()).hexdigest()}
                      for i, t in enumerate(scene["tasks"], 1)]
            if spec["session_id"] != scene["id"] or spec["tasks"] != orders or len(orders) != 20:
                raise ValueError("Scene, task order or command bytes differ from the pilot manifest")
    return plan


def status_rows(plan):
    return [{"session_id": s["session_id"], "task_id": t["task_id"], "index": t["index"],
             "status": "unstarted", "reason": "pending"}
            for s in plan["sessions"] for t in s["tasks"]]


def run(output, case):
    from harness_episode_local import block_network
    from flockcue_session_cpu.teacher_transport import create_ledger, ledger_snapshot

    attempts = block_network()
    plan = load_pilot()
    limits = plan["pilot_limits"]
    prices = limits["accounts"]["CNY"]
    batch_cap = prices["request_reservation"] if case == "budget-stop" else prices["batch_cap"]
    base = output.resolve()
    base.mkdir(parents=True, exist_ok=False)
    (base / "sessions").mkdir()
    (base / "pilot40.json").write_bytes(PILOT.read_bytes())
    config = {"case": case, "currency": "CNY", "batch_cap": batch_cap, "per_task_cap": prices["per_task_cap"],
              "pilot_sha256": PILOT_SHA, "missing_usage": case == "missing-usage",
              "max_http_attempts_total": limits["max_http_attempts_total"],
              "sessions": [s["session_id"] for s in plan["sessions"]], "transport": "local_fixed_replies",
              "eligible_for_training": False, "timing_required": True}
    write(base / "batch-input.json", config)
    ledger = base / "cost-ledger.jsonl"
    create_ledger(ledger, limits, currency="CNY", batch_cap=batch_cap)
    started = time.monotonic()
    status = {"case": case, "sessions": [], "tasks": status_rows(plan),
              "start_utc": datetime.now(timezone.utc).isoformat(), "complete": False}
    stop = None
    for spec in plan["sessions"]:
        session = spec["session_id"]
        directory = base / "sessions" / session
        planned = [t for t in status["tasks"] if t["session_id"] == session]
        if stop is not None:
            for entry in planned:
                entry["reason"] = stop
            status["sessions"].append({"session_id": session, "status": "unstarted", "reason": stop})
            continue
        record = {"session_id": session, "status": "running", "reason": None}
        status["sessions"].append(record)
        atomic_write(base / "batch-status.json", json_bytes(status))
        print(f"Starting session {session} with {len(planned)} frozen tasks", flush=True)
        teacher = {**config, "manifest": "../../pilot40.json", "batch_root": "../..",
                   "session_id": session, "seed": spec["runtime_seed"]}
        try:
            run_session_cli(directory, session, "none", teacher=teacher)
        except BaseException as exc:
            record.update(status="unverified", reason=type(exc).__name__)
            for entry in planned:
                entry.update(status="unverified" if
                             (directory / "tasks" / f"{entry['index']:02}-{entry['task_id']}").exists()
                             else "unstarted", reason="worker_or_scorer_interrupted")
            atomic_write(base / "batch-status.json", json_bytes(status))
            raise
        session_data = read(directory / "session.json")
        for entry, actual in zip(planned, session_data["tasks"], strict=True):
            if entry["task_id"] != actual["task_id"] or entry["index"] != actual["index"]:
                raise ValueError("Teacher session did not retain the frozen task order")
            entry.update(status=actual["status"], reason=actual["reason"])
        snapshot = ledger_snapshot(ledger, limits, currency="CNY", batch_cap=batch_cap)
        stop = snapshot["stop_reason"] if snapshot["blocked"] else None
        record.update(status="stopped" if stop else "finished", reason=stop)
        print(f"Closed session {session}: {dict(Counter(t['status'] for t in planned))}", flush=True)
    if attempts:
        raise RuntimeError("External network attempt in local teacher coordinator")
    status.update(complete=True, stop_reason=stop, network_attempts=attempts,
                  elapsed_seconds=time.monotonic()-started, end_utc=datetime.now(timezone.utc).isoformat())
    atomic_write(base / "batch-status.json", json_bytes(status))
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run", help="run the fixed-reply rehearsal in a fresh directory")
    execute.add_argument("--case", choices=CASES, default="normal")
    execute.add_argument("--output", type=Path, required=True)
    audit = commands.add_parser("verify", help="verify a completed run from its saved evidence")
    audit.add_argument("--run", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True, help="verification JSON, outside the run")
    args = parser.parse_args()
    if args.command == "run":
        run(args.output, args.case)
    else:
        from harness_episode_local import block_network
        attempts = block_network()
        from flockcue_session_cpu.teacher_verify import verify_batch
        if args.output.resolve().is_relative_to(args.run.resolve()):
            parser.error("Write the audit outside the sealed run")
        result = verify_batch(args.run.resolve())
        if attempts:
            raise RuntimeError("External network attempt in offline verification")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(args.output, json_bytes(result))
        print(f"Verified {args.run} and wrote {args.output}")


if __name__ == "__main__":
    main()
