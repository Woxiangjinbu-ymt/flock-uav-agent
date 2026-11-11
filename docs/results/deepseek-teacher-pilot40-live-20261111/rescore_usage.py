"""Replay private checks and service usage. Run with PYTHONHASHSEED=0, like the original scorer."""

import argparse
from collections import Counter
from contextlib import chdir
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from harness_episode_local import block_network
from flockcue_session_cpu.verify import (
    canonical_sha,
    file_sha,
    original_scene,
    owned,
    process_evidence,
    read,
    require,
)

TS = ZoneInfo("Asia/Shanghai")
MILLION = Decimal(1_000_000)
SCENES = ("04cd6269", "ef36e27a")


def quantiles(samples):
    vals = sorted(samples)
    result = {"n": len(vals), "sum": round(sum(vals), 6), "p50": None, "p95": None}
    if vals:
        for label, pct in (("p50", 0.5), ("p95", 0.95)):
            position = (len(vals) - 1) * pct
            i = int(position)
            result[label] = round(vals[i] + (vals[min(i + 1, len(vals) - 1)] - vals[i]) * (position - i), 6)
    return result


def price_factor(stamp):
    # This specific November 2026 pilot falls outside all PRC public holidays.
    local = datetime.fromisoformat(stamp).astimezone(TS)
    require((local.year, local.month) == (2026, 11), "Recheck the calendar for runs outside November 2026")
    peak = local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18)
    return Decimal("1" if peak else "0.5")


def audit_tree(folder, source, task, row, phase, world, worker, scorer):
    from flockcue_session_cpu.score import evaluate

    boundary = read(folder / "boundary.json")
    if phase == "initial":
        snapshot, score = "initial-world.pkl", "initial-score.json"
        digest = boundary["initial_world_sha256"]
        state_hash = read(folder / "before.json")["policy_start"]["world_state_sha256"]
    else:
        snapshot, score = "world.pkl", "score.json"
        digest = boundary["world_sha256"]
        state_hash = boundary["world_state_sha256"]
    path = folder / score
    saved = read(path)
    checker_hash = canonical_sha(task["execution_check_apis"])
    require(
        digest == worker["files"][row["directory"] + "/" + snapshot] == saved["world_sha256"]
        and file_sha(path) == scorer["files"][row["directory"] + "/" + score]
        and saved["task_id"] == task["id"]
        and saved["worker_pid"] == worker["pid"]
        and saved["scorer_pid"] == scorer["pid"]
        and saved["checker_sha256"] == checker_hash == boundary["checker_sha256"],
        "Original task, snapshot or independent scorer identity changed",
    )
    actual = evaluate(world, source, task, folder / snapshot, digest, state_hash)
    require(
        canonical_sha(actual) == canonical_sha(saved["checker"]),
        "Full check tree or a raw leaf response differs",
    )
    return {
        "passed": actual["passed"],
        "leaf_checks": actual["checks_expected"],
        "leaves_passed": actual["checks_passed"],
        "checker_result_sha256": canonical_sha(actual),
        "snapshot_sha256": digest,
    }


def request_cost(folder, span, reservation, rates):
    row = {
        "turn": span["attempt"],
        "dispatched": span["dispatched"],
        "sdk_received": span["sdk_received"],
        "status_code": span["status_code"],
        "stop_reason": span["stop_reason"],
        "request_sha256": span["request_body_sha256"],
        "response_sha256": span["response_body_sha256"],
    }
    timer = span["transport"]
    require(
        timer["end_monotonic_ns"] - timer["start_monotonic_ns"] == timer["elapsed_ns"],
        "Changed request latency",
    )
    row.update(
        elapsed_seconds=timer["elapsed_ns"] / 1e9, start_utc=timer["start_utc"], end_utc=timer["end_utc"]
    )
    stem = f"turn-{row['turn']:02d}"
    require(
        file_sha(folder / "provider-wire" / (stem + ".request.json")) == row["request_sha256"],
        "Changed request wire",
    )
    if reservation is None:
        require(
            not row["dispatched"] and not row["sdk_received"], "Missing reservation for a dispatched request"
        )
        return row
    require(
        row["dispatched"] and reservation["request_body_sha256"] == row["request_sha256"], "Wrong reservation"
    )
    row.update(reservation_id=reservation["reservation_id"], settlement=reservation["status"])
    if reservation["status"] != "settled":
        require(reservation["status"] == "locked", "Unsealed provider reservation")
        row["locked_reservation"] = reservation["reserved_upper_bound"]
        return row
    path = folder / "provider-wire" / (stem + ".response.json")
    require(
        row["sdk_received"]
        and 200 <= row["status_code"] < 300
        and file_sha(path) == row["response_sha256"] == reservation["response_body_sha256"],
        "Changed response wire",
    )
    response = read(path)
    usage = response["usage"]
    require(
        usage == span["usage"] and all(usage[k] == v for k, v in reservation["usage"].items()),
        "Returned usage and settled usage differ",
    )
    pin, pout, total = (usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
    require(
        type(pin) is int and type(pout) is int and min(pin, pout) >= 0 and pin + pout == total,
        "Bad billed token totals",
    )
    hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
    if hit is not None or miss is not None:
        if hit is None:
            hit = pin - miss
        elif miss is None:
            miss = pin - hit
        require(
            type(hit) is int and type(miss) is int and min(hit, miss) >= 0 and hit + miss == pin,
            "Bad cache split",
        )
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    require(reasoning is None or (type(reasoning) is int and 0 <= reasoning <= pout), "Bad reasoning usage")
    hit_rate, miss_rate, out_rate = rates
    conservative = (pin * miss_rate + pout * out_rate) / MILLION
    require(
        conservative == Decimal(reservation["settled_conservative_charge"]),
        "Cost ledger differs from raw usage",
    )
    if hit is None:
        floor = (pin * hit_rate + pout * out_rate) / MILLION
        ceil = conservative
    else:
        floor = ceil = (hit * hit_rate + miss * miss_rate + pout * out_rate) / MILLION
    factors = [price_factor(row[k]) for k in ("start_utc", "end_utc")]
    row.update(
        model=response["model"],
        fingerprint=response.get("system_fingerprint"),
        finish_reason=response["choices"][0]["finish_reason"],
        input_tokens=pin,
        output_tokens=pout,
        total_tokens=total,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        reasoning_tokens=reasoning,
        peak_miss_estimate=str(conservative),
        cache_and_time_low=str(floor * min(factors)),
        cache_and_time_high=str(ceil * max(factors)),
        price_window=("boundary" if factors[0] != factors[1] else "peak" if factors[0] == 1 else "offpeak"),
    )
    return row


def scene_audit(scene, sid, world, state, rates):
    worker, scorer = (process_evidence(scene, r) for r in ("worker", "scorer"))
    require(
        worker["pid"] != scorer["pid"] and worker["finished_ns"] <= scorer["started_ns"],
        "Scorer identity or independent execution interval is invalid",
    )
    source, inputs, session = (
        original_scene(scene, sid),
        read(scene / "run-input.json"),
        read(scene / "session.json"),
    )
    for name, digest in inputs["source_sha256"].items():
        if name.startswith(("server/", "controller/")) or name in {
            "scripts/reproduction.py",
            "scripts/harness_v2_world.py",
            "scripts/flockcue_session_cpu/score.py",
        }:
            require(file_sha(owned(ROOT, name)) == digest, f"Scoring implementation changed: {name}")
    checkpoint_path = scene.parent.parent / "checkpoints" / (sid + ".json")
    checkpoint = read(checkpoint_path)
    require(
        checkpoint["worker_seal_sha256"] == file_sha(scene / "worker-exit.json")
        and checkpoint["scorer_seal_sha256"] == file_sha(scene / "scorer-exit.json"),
        "Checkpoint seal mismatch",
    )
    tasks = []
    for task, row, approved in zip(source["tasks"], session["tasks"], checkpoint["tasks"], strict=True):
        tid = task["id"]
        require(
            tid == row["task_id"] == approved["task_id"]
            and row["status"] == approved["status"]
            and row["reason"] == approved["reason"],
            "Task order or disposition changed",
        )
        record = {k: row[k] for k in ("task_id", "index", "status", "reason")}
        record["scene_id"] = sid
        tasks.append(record)
        if row["status"] == "unstarted":
            require(tid not in state["trials"], "Unstarted task has a cost entry")
            continue
        require(row["status"] == "completed" and tid in state["trials"], "Started task has no sealed result")
        folder = owned(scene, row["directory"])
        boundary = read(folder / "boundary.json")
        record["execution_seconds"] = boundary["score_seconds"]
        record["agent_reported_success"] = boundary["agent_success"]
        record["committed_actions"] = approved["committed_actions"]
        for phase in ("initial", "terminal"):
            record[phase] = audit_tree(folder, source, task, row, phase, world, worker, scorer)
            require(
                record[phase]["passed"] == approved[phase + "_passed"],
                "Restored state and checkpoint disagree",
            )
        attempts = iter(state["trials"][tid]["attempt_ids"])
        record["requests"] = []
        for span_path, turn in zip(
            sorted((folder / "provider-timing").glob("attempt-*.json")), approved["turns"], strict=True
        ):
            span = read(span_path)
            rid = next(attempts) if span["dispatched"] else None
            require(
                rid == turn["reservation_id"]
                and span["task_id"] == tid
                and span["attempt"] == turn["number"],
                "Provider timing and checkpoint identities differ",
            )
            record["requests"].append(request_cost(folder, span, state["attempts"].get(rid), rates))
        require(next(attempts, None) is None, "Unaccounted reservation")
    require(len(tasks) == 20, "Original denominator changed")
    return {
        "scene_id": sid,
        "checkpoint_sha256": file_sha(checkpoint_path),
        "worker_started_monotonic_ns": worker["started_ns"],
        "scorer_finished_monotonic_ns": scorer["finished_ns"],
        "worker_scorer_seconds": (scorer["finished_ns"] - worker["started_ns"]) / 1e9,
        "tasks": tasks,
    }


def unstarted_scene(spec, status_rows, state):
    require(state["blocked"] and state["stop_reason"], "Scene has no checkpoint and batch is still open")
    tasks = []
    for task, row in zip(spec["tasks"], status_rows, strict=True):
        require(
            row["index"] == task["index"]
            and row["task_id"] == task["task_id"]
            and row["status"] == "unstarted"
            and row["reason"] == state["stop_reason"]
            and task["task_id"] not in state["trials"],
            "Missing evidence for an ostensibly unstarted scene",
        )
        tasks.append({k: row[k] for k in ("task_id", "index", "status", "reason")})
        tasks[-1]["scene_id"] = row["session_id"]
    return {
        "scene_id": spec["session_id"],
        "checkpoint_sha256": None,
        "worker_scorer_seconds": None,
        "tasks": tasks,
    }


def aggregate(tasks):
    started = [r for r in tasks if "initial" in r]
    turns = [t for r in started for t in r["requests"]]
    dispatched = [t for t in turns if t["dispatched"]]
    known = [t for t in dispatched if t["settlement"] == "settled"]
    unknown = [t for t in dispatched if t["settlement"] == "locked"]
    initial, final = ([r[phase] for r in started] for phase in ("initial", "terminal"))
    fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "reasoning_tokens",
    )
    return {
        "planned": len(tasks),
        "started_scored": len(started),
        "unstarted": len(tasks) - len(started),
        "stop_reasons": dict(Counter(r["reason"] for r in started)),
        "unstarted_reasons": dict(Counter(r["reason"] for r in tasks if r["status"] == "unstarted")),
        "trees_replayed": len(started) * 2,
        "initial_passed": sum(r["passed"] for r in initial),
        "terminal_passed": sum(r["passed"] for r in final),
        "newly_passed": sum(not a["passed"] and b["passed"] for a, b in zip(initial, final)),
        "already_passed_and_still_passing": sum(a["passed"] and b["passed"] for a, b in zip(initial, final)),
        "initially_passing_now_failing": sum(a["passed"] and not b["passed"] for a, b in zip(initial, final)),
        "initial_and_terminal_failing": sum(not a["passed"] and not b["passed"] for a, b in zip(initial, final)),
        "leaf_responses_replayed": sum(r["leaf_checks"] for r in initial + final),
        "sdk_requests": len(turns),
        "dispatched": len(dispatched),
        "settled": len(known),
        "locked": len(unknown),
        "model_counts": dict(Counter(t["model"] for t in known)),
        "fingerprint_counts": dict(Counter(t["fingerprint"] for t in known)),
        "finish_counts": dict(Counter(t["finish_reason"] for t in known)),
        "price_windows": dict(Counter(t["price_window"] for t in known)),
        "known_usage": {
            field: {
                "sum": sum(t[field] for t in known if t[field] is not None),
                "reporting_responses": sum(t[field] is not None for t in known),
            }
            for field in fields
        },
        "known_cost": {
            key: str(sum((Decimal(t[key]) for t in known), Decimal(0)))
            for key in ("peak_miss_estimate", "cache_and_time_low", "cache_and_time_high")
        },
        "unresolved_reservation": str(sum((Decimal(t["locked_reservation"]) for t in unknown), Decimal(0))),
        "http_dispatch_seconds": quantiles([t["elapsed_seconds"] for t in dispatched]),
        "execution_seconds": quantiles([r["execution_seconds"] for r in started]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene", choices=SCENES, action="append", help="default both registered scenes")
    args = parser.parse_args()
    require(
        os.environ.get("PYTHONHASHSEED") == "0" and not sys.flags.hash_randomization,
        "Set PYTHONHASHSEED=0 before starting Python to reproduce the original scorer's set order.",
    )
    base, output = args.run.resolve(strict=True), args.output.resolve()
    require(not output.is_relative_to(base), "Audit output must be outside the sealed run")
    scenes = args.scene or list(SCENES)
    require(len(set(scenes)) == len(scenes), "Duplicate scene")
    attempts = block_network()
    from flockcue_session_cpu.score import load_world
    from flockcue_session_cpu.teacher_verify import _ledger

    config, pilot = (read(base / p) for p in ("batch-input.json", "pilot40.json"))
    require(
        tuple(config["sessions"]) == SCENES and file_sha(base / "pilot40.json") == config["pilot_sha256"],
        "Pilot manifest changed",
    )
    limits = pilot["pilot_limits"]
    state = _ledger(base, config, limits)
    account = limits["accounts"][config["currency"]]
    rates = [
        Decimal(account[k])
        for k in (
            "rate_peak_input_cache_hit_per_million",
            "rate_peak_input_cache_miss_per_million",
            "rate_peak_output_per_million",
        )
    ]
    status = read(base / "batch-status.json")
    reports = []
    with TemporaryDirectory(prefix="teacher-rescore-") as tmp, chdir(tmp):
        world = load_world()
        for spec in pilot["sessions"]:
            sid = spec["session_id"]
            if sid not in scenes:
                continue
            folder = base / "sessions" / sid
            rows = [r for r in status["tasks"] if r["session_id"] == sid]
            if folder.exists():
                report = scene_audit(folder, sid, world, state, rates)
            else:
                report = unstarted_scene(spec, rows, state)
            for reviewed, registered, disposition in zip(report["tasks"], spec["tasks"], rows, strict=True):
                require(
                    all(reviewed[k] == registered[k] for k in ("index", "task_id"))
                    and all(reviewed[k] == disposition[k] for k in ("index", "task_id", "status", "reason")),
                    "Pilot manifest, batch status and task evidence do not agree",
                )
            reports.append(report)
    for report in reports:
        report["totals"] = aggregate(report["tasks"])
    totals = aggregate([t for r in reports for t in r["tasks"]])
    sealed = [r for r in reports if r["checkpoint_sha256"] is not None]
    totals["worker_to_scorer_span_seconds"] = (
        (sealed[-1]["scorer_finished_monotonic_ns"] - sealed[0]["worker_started_monotonic_ns"]) / 1e9
        if sealed
        else None
    )
    totals["summed_scene_seconds"] = sum(r["worker_scorer_seconds"] for r in sealed)
    if len(scenes) == len(SCENES):
        require(
            totals["dispatched"] == len(state["attempts"]) == status["reservations"]
            and totals["started_scored"] == len(state["trials"])
            and Decimal(totals["known_cost"]["peak_miss_estimate"])
            == Decimal(state["settled_conservative_charge"])
            == Decimal(status["settled"])
            and Decimal(totals["unresolved_reservation"])
            == Decimal(state["locked_reservation"])
            == Decimal(status["locked_reservation"]),
            "Independent totals differ from the batch ledger or status",
        )
    imported = sorted(
        set(sys.modules)
        & {
            "uav_agent",
            "flockcue_session_cpu.runtime",
            "flockcue_session_cpu.teacher_transport",
            "flockcue_session_cpu.teacher_service",
            "harness_deepseek_adapter",
        }
    )
    require(not imported and not attempts, "Rescore imported policy or tried an external connection")
    document = {
        "scope": config["transport"],
        "currency": config["currency"],
        "script_sha256": file_sha(Path(__file__)),
        "pilot_sha256": config["pilot_sha256"],
        "network_attempts": attempts,
        "policy_imports": imported,
        "percentile_method": "linear interpolation rank (n-1)*p",
        "price_basis": "November 2026 working days, Beijing 09-12 and 14-18 peak, otherwise half. "
        "Ranges allow a missing cache split or a request crossing a rate boundary. "
        "Provider debit is separately reconciled, not inferred here.",
        "sessions": reports,
        "totals": totals,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    if output.exists():
        require(output.read_bytes() == raw, "Saved audit differs from reconstructed evidence")
    else:
        output.write_bytes(raw)
    print(json.dumps(totals, ensure_ascii=False))


if __name__ == "__main__":
    main()
