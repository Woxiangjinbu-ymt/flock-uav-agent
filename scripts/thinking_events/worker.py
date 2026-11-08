"""Coordinate a fresh student worker and an independently sealed scorer."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from validate_harness_episode import check_hashes
from validate_harness_natural_runner import validate_score, validate_worker

from .worker_child import read_frame, write_frame


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(run):
    path = run / "manifest.json"
    if _sha(path) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("worker manifest changed")
    data = json.loads(path.read_bytes())
    check_hashes(run / "source", data["sources"])
    check_hashes(Path(data["study"]), data["inputs"])
    return data


def _launch(run, manifest, trial, mode, options=(), sock=None):
    ident = trial["trial_id"]
    source = run / "source"
    deps = [
        dir
        for component in ("agent4drone", "server")
        for dir in sorted(
            (Path(manifest["workspace"]) / component / ".venv/lib").glob("python*/site-packages")
        )
    ]
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if k in os.environ}
    env.update(
        PYTHONPATH=os.pathsep.join(
            str(p) for p in (source / "scripts", source / "agent4drone", source / "server", *deps)
        ),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        PYTHONNOUSERSITE="1",
        PYGAME_HIDE_SUPPORT_PROMPT="1",
    )
    cmd = [
        sys.executable,
        "-m",
        "thinking_events.worker_child",
        "--run",
        str(run),
        "--trial",
        ident,
        "--mode",
        mode,
        *options,
    ]
    fds = ()
    if sock is not None:
        fds = (sock.fileno(),)
        cmd.extend(["--fd", str(sock.fileno())])
    started = time.monotonic()
    reason = None
    with (run / "process-logs" / f"{ident}-{mode}.log").open("xb") as log:
        child = subprocess.Popen(
            cmd, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, pass_fds=fds
        )
        try:
            while child.poll() is None:
                if mode == "worker" and (run / "trials" / ident / "termination-required.json").exists():
                    reason = "transport_not_drained"
                if time.monotonic() - started >= trial["watchdog_seconds"]:
                    reason = "watchdog"
                if reason:
                    child.kill()
                    break
                time.sleep(0.03)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
    return {
        "pid": child.pid,
        "returncode": child.returncode,
        "kill_reason": reason,
        "exited": True,
        "elapsed_seconds": time.monotonic() - started,
    }


def _proxy(sock, service, slot, thinking):
    expected_turn = 1
    with sock.makefile("rwb") as stream:
        while True:
            try:
                request = read_frame(stream)
            except EOFError:
                return
            if set(request) != {
                "slot",
                "turn",
                "messages",
                "tools",
                "thinking",
                "seed",
                "spent_tokens",
                "episode_deadline",
                "request_timeout",
            } or (request["slot"], request["turn"], request["thinking"]) != (slot, expected_turn, thinking):
                raise ValueError("invalid worker-to-student request")
            result = service.request(**request)
            # Reject mismatched identities supplied by a service; fill the wire
            # envelope only for clients with a verified synchronous request API.
            write_frame(stream, {"slot": slot, "turn": expected_turn, **result})
            expected_turn += 1


def _run_with_service(run, manifest, trial, model, thinking, service, reverse_receipts, cancel_at):
    ident = trial["trial_id"]
    errors = []
    left, right = socket.socketpair()

    def serve():
        try:
            _proxy(left, service, ident, thinking)
        except (EOFError, OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            left.close()

    thread = threading.Thread(target=serve, name="student-pipe")
    thread.start()
    try:
        opts = ["--model", model]
        if thinking:
            opts.append("--thinking")
        if reverse_receipts:
            opts.append("--reverse-receipts")
        if cancel_at is not None:
            opts.extend(["--cancel-at", str(cancel_at)])
        process = _launch(run, manifest, trial, "worker", opts, sock=right)
    finally:
        right.close()
        thread.join(timeout=0.25)
        if thread.is_alive():
            service.cancel()
            thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("student process failed to drain")
    if errors and not process["kill_reason"]:
        process["kill_reason"] = "student_transport_error: " + errors[0]
    return process


def _metrics(folder, worker, backend):
    audit = folder / "private/student-requests.jsonl"
    records = [json.loads(line) for line in audit.read_bytes().splitlines()] if audit.exists() else []
    by_turn = {row["turn"]: row for row in records}
    inputs, outputs, reasoning, peak = 0, 0, 0, 0
    history_tokens, unknown, late, unknown_reasoning, unknown_at_cutoff = [], [], [], [], []
    for attempt in worker["limits"]["attempts"]:
        turn = attempt["attempt_id"]
        row = by_turn.get(turn)
        if row is not None and row["request_body_sha256"] != attempt["request_body_sha256"]:
            raise ValueError("worker request hash differs from audited student request")
        if attempt.get("unknown_at_cutoff"):
            unknown_at_cutoff.append(turn)
        if attempt["usage_status"] == "known_late":
            late.append({"turn": turn, "usage": attempt["usage"]})
        history_tokens.append(row.get("history_reasoning_tokens") if row else None)
        if not row or not isinstance(row.get("usage"), dict):
            unknown.append(turn)
            continue
        usage = row["usage"]
        if usage["prompt_tokens"] != len(row["input_ids"]) or usage["completion_tokens"] != len(
            row["generated_ids"]
        ):
            raise ValueError("student usage differs from the actual ID counts")
        inputs += usage["prompt_tokens"]
        outputs += usage["completion_tokens"]
        thought = usage["completion_tokens_details"]["reasoning_tokens"]
        if thought is None:
            unknown_reasoning.append(turn)
        else:
            reasoning += thought
        peak = max(peak, usage["prompt_tokens"] + usage["completion_tokens"])
    timings = [
        dict(
            turn=r["turn"],
            round_trip_seconds=r["round_trip_seconds"],
            **(r.get("timing") or {"unavailable": {"generation": "no completed generation"}}),
        )
        for r in records
    ]
    peaks = [r["peak_vram_bytes"] for r in records if r.get("peak_vram_bytes") is not None]
    return {
        "request_timings": timings,
        "peak_vram_bytes": max(peaks, default=None),
        "vram_measurement": "cuda" if peaks else "cpu_fixture" if backend == "fixture" else "unavailable",
        "cost_cny": 0 if backend == "fixture" else None,
        "cost_basis": "local CPU fixture" if backend == "fixture" else "campaign billing ledger",
        "peak_prompt": max(
            (len(r["input_ids"]) for r in records if r.get("input_ids") is not None), default=None
        ),
        "stop_reason": worker["limits"]["stop_reason"],
        "remaining_wall_seconds": max(0.0, worker["runtime"]["task_seconds"] - worker["elapsed"]),
        "remaining_requests": max(
            0,
            worker["runtime"]["request_limits"]["max_http_attempts"]
            - worker["limits"]["admitted_http_attempts"],
        ),
        "remaining_tokens": None
        if unknown
        else max(0, worker["runtime"]["request_limits"]["max_total_tokens"] - inputs - outputs),
        "prompt_tokens": None if unknown else inputs,
        "completion_tokens": None if unknown else outputs,
        "reasoning_tokens": None if unknown or unknown_reasoning else reasoning,
        "total_tokens": None if unknown else inputs + outputs,
        "known_prompt_tokens": inputs,
        "known_completion_tokens": outputs,
        "known_reasoning_tokens": reasoning,
        "known_total_tokens": inputs + outputs,
        "peak_context": peak if len(unknown) < len(history_tokens) else None,
        "history_reasoning_tokens_by_request": history_tokens,
        "unknown_reasoning_turns": unknown_reasoning,
        "unknown_at_cutoff_turns": unknown_at_cutoff,
        "unknown_usage_turns": unknown,
        "late_usage": late,
        "requests": len(worker["limits"]["attempts"]),
        "audit": "private/student-requests.jsonl",
        "elapsed_seconds": worker["elapsed"],
    }


def _check_student_identity(run, manifest, metadata, model, backend):
    if (metadata.get("backend"), metadata.get("model")) != (backend, model):
        raise ValueError("student service does not match the admitted model/backend")
    if backend == "fixture":
        return
    import student_input_v2 as v2

    if model not in ("1.7B", "4B"):
        raise ValueError("unknown student model")
    key = f"training/model-manifests/{model}.json"
    path = run / "source" / key
    if _sha(path) != manifest["sources"].get(key):
        raise ValueError("unregistered model manifest")
    pinned = json.loads(path.read_bytes())
    digest = hashlib.sha256(v2.canonical(pinned)).hexdigest()
    if (
        not metadata.get("weights_loaded")
        or pinned.get("model") != model
        or metadata.get("revision") != pinned["revision"]
        or metadata.get("model_manifest_sha256") != digest
    ):
        raise ValueError("loaded weights differ from the frozen model manifest")


def run_slot(
    run,
    trial_id,
    *,
    model,
    thinking,
    student_python=None,
    backend="fixture",
    fixture_replies=None,
    reverse_receipts=False,
    cancel_at=None,
    service=None,
    admitted_backend_sha256=None,
):
    """Execute an already claimed slot. Formal callers supply the admitted warm service."""
    run = Path(run).resolve()
    if not trial_id or Path(trial_id).name != trial_id or trial_id in (".", ".."):
        raise ValueError("invalid slot ID")
    manifest = _manifest(run)
    (trial,) = [t for t in manifest["trials"] if t["trial_id"] == trial_id]
    if backend == "fixture" and manifest["scope"] != "student_cpu_regression":
        raise ValueError("scripted generation requires a separate CPU fixture manifest")
    if backend != "fixture" and (
        service is None or reverse_receipts or cancel_at is not None or fixture_replies is not None
    ):
        raise ValueError("formal runs require a qualified warm service and unmodified receipts")
    if backend != "fixture":
        scope = manifest["scope"]
        if scope == "thinking_events_dev_local_backend":
            from .campaign import verify_run
            from .campaign_storage import read_jsonl

            verify_run(run)
            (claim,) = (row for row in read_jsonl(run / "slots.jsonl") if row["slot_id"] == trial_id)
            selected = run / "selected-backend.sha256"
            if (
                not selected.exists()
                or not admitted_backend_sha256
                or selected.read_text().strip() != admitted_backend_sha256
                or (claim["status"], claim.get("backend_sha256"), claim["model"], claim["thinking"])
                != ("started", admitted_backend_sha256, model, thinking)
            ):
                raise ValueError("formal execution requires an admitted backend and matching slot claim")
        elif scope != "student_t37_smoke":
            raise ValueError("real generation requires a dedicated T37 or admitted dev run")
    for name in ("process-logs", "worker-exit-seals", "scorer-exit-seals"):
        (run / name).mkdir(exist_ok=True)
    from .student import StudentProcess

    manager = (
        contextlib.nullcontext(service)
        if service is not None
        else StudentProcess(
            str(student_python),
            backend=backend,
            model=model,
            fixture_replies=fixture_replies,
            scripts_root=run / "source/scripts",
        )
    )
    row = {
        "trial_id": trial_id,
        "task_id": trial["task_id"],
        "status": "unscored",
        "passed": None,
        "worker_pid": None,
        "scorer_pid": None,
    }
    with manager as client:
        row["student_metadata"] = client.metadata
        _check_student_identity(run, manifest, client.metadata, model, backend)
        worker_process = _run_with_service(
            run, manifest, trial, model, thinking, client, reverse_receipts, cancel_at
        )
    row["worker_pid"] = worker_process["pid"]
    folder = run / "trials" / trial_id
    try:
        worker = validate_worker(run, trial, worker_process, manifest=manifest)
        row.update(
            initial=worker["initial"],
            stop_reason=worker["limits"]["stop_reason"],
            usage=_metrics(folder, worker, backend),
            agent_returned=worker["agent"]["success"],
            worker_seal_sha256=_sha(run / "worker-exit-seals" / (trial_id + ".json")),
        )
        score_process = _launch(run, manifest, trial, "score")
        score = validate_score(run, trial, worker, score_process)
        if score_process["pid"] == worker_process["pid"]:
            raise ValueError("scorer lacks independent process identity")
        row.update(
            status="scored",
            passed=score["checker"]["passed"],
            scorer_pid=score_process["pid"],
            scorer_seal_sha256=_sha(run / "scorer-exit-seals" / (trial_id + ".json")),
        )
    except (ValueError, OSError, KeyError) as exc:
        row["unscored_reason"] = worker_process.get("kill_reason") or str(exc)
    return row


def main():
    from .worker_smoke import smoke

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("smoke")
    check.add_argument("--output", type=Path, required=True)
    check.add_argument("--student-python", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            smoke(args.output, args.student_python, execute_slot=run_slot), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
