"""Prepare one frozen pilot and advance a named teacher scene with an auditable cost ledger."""
import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from flockcue_session_cpu.__main__ import read, run_session_cli, sha
from flockcue_session_cpu.teacher_transport import create_ledger, ledger_snapshot
from flockcue_session_cpu.verify import require
from run_teacher_pilot40 import PILOT, PILOT_SHA, load_pilot, status_rows
from thinking_events.campaign_storage import atomic_write, json_bytes, locked


def prepare(base, transport, currency, loopback_url=None):
    pilot = load_pilot()
    if transport == "loopback_http":
        url = urlsplit(loopback_url or "")
        require(url.scheme == "http" and url.hostname == "127.0.0.1" and url.port and
                url.netloc == f"127.0.0.1:{url.port}" and not (url.path or url.query or url.fragment),
                "Supply only http://127.0.0.1:<port> for a local rehearsal")
    else:
        require(transport == "deepseek" and loopback_url is None, "Live service endpoint is fixed")
    limits = pilot["pilot_limits"]
    rates = limits["accounts"][currency]
    config = {"pilot_sha256": PILOT_SHA, "transport": transport, "currency": currency,
              "per_task_cap": rates["per_task_cap"], "batch_cap": rates["batch_cap"],
              "synthetic": transport == "loopback_http",
              "sessions": [s["session_id"] for s in pilot["sessions"]]}
    if loopback_url:
        config["loopback_url"] = loopback_url
    base.mkdir(parents=True, exist_ok=False)
    (base / "sessions").mkdir()
    (base / "checkpoints").mkdir()
    atomic_write(base / "pilot40.json", PILOT.read_bytes())
    atomic_write(base / "batch-input.json", json_bytes(config))
    atomic_write(base / "batch-input.sha256", sha(base / "batch-input.json").encode())
    create_ledger(base / "cost-ledger.jsonl", limits, currency=currency, batch_cap=config["batch_cap"])
    write_status(base, pilot, config, snapshot(base, pilot, config))


def snapshot(base, pilot, config):
    return ledger_snapshot(base / "cost-ledger.jsonl", pilot["pilot_limits"], currency=config["currency"],
                           batch_cap=config["batch_cap"])


def write_status(base, pilot, config, ledger):
    rows = status_rows(pilot)
    for row in rows:
        sid, tid = row["session_id"], row["task_id"]
        checkpoint = base / "checkpoints" / f"{sid}.json"
        claim = checkpoint.with_suffix(".claim")
        if checkpoint.exists():
            report = read(checkpoint)
            task = report["tasks"][row["index"] - 1]
            require(task["task_id"] == tid, "Incorrect task in checkpoint")
            row.update(status=task["status"], reason=task["reason"])
        elif tid in ledger["trials"]:
            row.update(status="unverified", reason=ledger["stop_reason"] or "unsealed_task")
        elif (base / "sessions" / sid / "tasks" / f"{row['index']:02}-{tid}").exists():
            row.update(status="unverified", reason="unsealed_task")
        elif ledger["blocked"] or claim.exists():
            row["reason"] = ledger["stop_reason"] or "unsealed_scene"
    status = {"transport": config["transport"], "currency": config["currency"], "tasks": rows,
              "reservations": len(ledger["attempts"]), "settled": ledger["settled_conservative_charge"],
              "locked_reservation": ledger["locked_reservation"], "blocked": ledger["blocked"],
              "stop_reason": ledger["stop_reason"]}
    atomic_write(base / "batch-status.json", json_bytes(status))
    return status


def advance(base, sid):
    from flockcue_session_cpu.teacher_service import recover_ledger
    from flockcue_session_cpu.teacher_service_audit import audit_scene
    # A command names a single scene. Repeating it cannot implicitly start the next.
    with locked(base, ".advance.lock"):
        pilot = load_pilot()
        config = read(base / "batch-input.json")
        require(sha(base / "pilot40.json") == config["pilot_sha256"] == PILOT_SHA and
                (base / "batch-input.sha256").read_text() == sha(base / "batch-input.json"),
                "Changed prepared pilot or service configuration")
        specs = {s["session_id"]: s for s in pilot["sessions"]}
        require(sid in specs and list(specs) == config["sessions"], "Unknown or reordered pilot scene")
        state = snapshot(base, pilot, config)
        checkpoints = base / "checkpoints"
        for scene_id, spec in specs.items():
            scene = base / "sessions" / scene_id
            checkpoint = checkpoints / f"{scene_id}.json"
            claim = checkpoint.with_suffix(".claim")
            expected_claim = {"session_id": scene_id, "batch_sha256": sha(base / "batch-input.json")}
            if claim.exists():
                require(read(claim) == expected_claim, "Scene claim differs from prepared batch")
            worker_exit = scene / "worker-exit.json"
            if (claim.exists() and not checkpoint.exists() and not (scene / "scorer-exit.json").exists()
                    and worker_exit.is_file() and read(worker_exit)["returncode"] == 0):
                from flockcue_session_cpu.teacher_recovery import recover_scores
                recover_scores(scene, base)
            if checkpoint.exists() or (scene / "scorer-exit.json").exists():
                require(claim.exists(), "Scene evidence has no durable claim")
                report = audit_scene(scene, spec, config, pilot["pilot_limits"], state)
                if checkpoint.exists():
                    require(read(checkpoint) == report, "Sealed scene changed since its first acceptance")
                else:
                    atomic_write(checkpoint, json_bytes(report))
                if scene_id == sid:
                    return write_status(base, pilot, config, state)
                if not report["finished_without_batch_stop"]:
                    return write_status(base, pilot, config, state)
                continue
            if claim.exists() or scene.exists() or state["blocked"] or state["active_trial"] is not None:
                state = recover_ledger(base / "cost-ledger.jsonl", pilot["pilot_limits"],
                                       currency=config["currency"], batch_cap=config["batch_cap"],
                                       abandoned_scene=claim.exists() or scene.exists())
                status = write_status(base, pilot, config, state)
                if not state["blocked"]:
                    raise RuntimeError("Scene handoff could not be verified. Its outstanding tasks remain unstarted.")
                return status
            require(scene_id == sid, "The previous scene must be scored and reconciled first")
            if config["transport"] == "deepseek" and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
                raise ValueError("Configure the DeepSeek credential locally before starting a paid scene")
            atomic_write(claim, json_bytes(expected_claim))
            session_config = {**config, "batch_root": "../..", "manifest": "../../pilot40.json",
                              "session_id": sid, "seed": spec["runtime_seed"]}
            try:
                run_session_cli(scene, sid, "none", teacher=session_config)
                state = snapshot(base, pilot, config)
                report = audit_scene(scene, spec, config, pilot["pilot_limits"], state)
                atomic_write(checkpoint, json_bytes(report))
            finally:
                write_status(base, pilot, config, snapshot(base, pilot, config))
            return read(base / "batch-status.json")
        raise AssertionError("Unreachable scene")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    prepare_cmd = sub.add_parser("prepare", help="create a fresh ledger without making requests")
    prepare_cmd.add_argument("--run", type=Path, required=True)
    prepare_cmd.add_argument("--transport", choices=("loopback_http", "deepseek"), required=True)
    prepare_cmd.add_argument("--loopback-url")
    prepare_cmd.add_argument("--currency", choices=("CNY", "USD"), required=True)
    run = sub.add_parser("advance", help="run or verify precisely one named scene")
    run.add_argument("--run", type=Path, required=True)
    run.add_argument("--scene", required=True)
    args = parser.parse_args()
    if args.cmd == "prepare":
        prepare(args.run.resolve(), args.transport, args.currency, args.loopback_url)
    else:
        from harness_episode_local import block_network
        attempts = block_network()  # Only the separately started service worker needs network access.
        status = advance(args.run.resolve(), args.scene)
        require(not attempts, "Coordinator attempted a network connection")
        print(f"reservations={status['reservations']} locked={status['locked_reservation']} stop={status['stop_reason']}")


if __name__ == "__main__":
    main()
