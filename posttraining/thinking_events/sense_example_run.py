"""Run the frozen single-turn sense description pairs with durable, at-most-once dispatch."""
from collections import Counter
import json
import math
import os
from pathlib import Path
import platform
import signal
import time

from .campaign_storage import atomic_write, digest, json_bytes, locked

MODEL = "Qwen3.5-2B"
CAMPAIGN = "ba8b8e2d10bdc54af7c5ff97c6919f5b2727a50de7e68db80efb9c7f67f5d2d6"
T37 = "565ec937a768fcefa8a6b21afaddd3985e8668fbf559448aff79c6a010377515"
LOAD_SECONDS, REQUEST_SECONDS, SHUTDOWN_SECONDS = 180, 120, 60
GPU_CAP = 14400
SAMPLING = dict(temperature=0.6, top_p=0.95, top_k=20, repetition_penalty=1.0)


def _write(path, data):
    atomic_write(path, json_bytes(data))


def _read(path):
    return json.loads(path.read_bytes())


def _admission_stop(used, now, lease_until, *, load):
    reserve = REQUEST_SECONDS + SHUTDOWN_SECONDS + (LOAD_SECONDS if load else 0)
    if used + reserve > GPU_CAP:
        return "gpu_time_cap"
    if now + reserve > lease_until:
        return "prepaid_window"
    return None


def _process_token(pid):
    # The Linux start tick distinguishes a surviving renderer from a reused PID.
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except FileNotFoundError:
        return None


def _close_orphan(segment):
    owner = segment.get("renderer")
    if not owner or owner["start_tick"] is None:
        return
    pid = owner["pid"]
    if _process_token(pid) != owner["start_tick"]:
        return
    if os.getpgid(pid) != pid:
        raise RuntimeError("Recorded renderer no longer owns its process group")
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + SHUTDOWN_SECONDS
    while _process_token(pid) == owner["start_tick"]:
        if time.monotonic() >= deadline:
            raise RuntimeError("Interrupted renderer still holds its process group")
        time.sleep(0.05)


def _recover_state(work, rows):
    for row in rows:
        key = row["slot_key"]
        state, receipt = work / "states" / (key + ".json"), work / "receipts" / (key + ".json")
        if not state.exists():
            if receipt.exists():
                raise ValueError(f"Unclaimed receipt for {key}")
            continue
        claim = _read(state)
        if claim["slot_key"] != key:
            raise ValueError(f"Claim identity changed for {key}")
        if claim["state"] == "terminal":
            if digest(receipt.read_bytes()) != claim["receipt_sha256"]:
                raise ValueError(f"Receipt changed for {key}")
            continue
        if claim["state"] != "claimed":
            raise ValueError(f"Invalid claim for {key}")
        if not receipt.exists():
            _write(receipt, {"slot_key": key, "status": "missing", "response": None,
                             "request_seconds": None, "reason": "interrupted_without_receipt",
                             "service_sha256": claim["service_sha256"]})
        _finish(state, receipt, claim)


def _finish(state, receipt, claim):
    saved = _read(receipt)
    if (saved["slot_key"] != claim["slot_key"] or saved["service_sha256"] != claim["service_sha256"]
            or saved["status"] not in {"completed", "missing", "timeout"}):
        raise ValueError("Receipt identity or outcome does not match claim")
    _write(state, {**claim, "state": "terminal", "status": saved["status"],
                   "receipt_sha256": digest(receipt.read_bytes())})


def _request(service, row, body, monotonic):
    # Only the archived public model inputs cross the service boundary.
    return service.request(slot=row["slot_id"], turn=row["turn"], thinking=row["thinking"],
                           seed=row["seed"], spent_tokens=row["spent_before"], tools=body["tools"],
                           messages=body["messages"], request_timeout=REQUEST_SECONDS,
                           episode_deadline=monotonic() + REQUEST_SECONDS + 1)


def _classify(response, row):
    from .sense_example_prepare import ids_sha256
    reason = response.get("stop_reason")
    if response.get("slot") != row["slot_id"] or response.get("turn") != row["turn"]:
        return "missing", "response_identity_mismatch"
    if reason == "request_timeout":
        return "timeout", reason
    if response.get("status") not in {"ok", "format_error"}:
        return "missing", reason or "no_response"
    if (ids_sha256(response["input_ids"]) != row["input_ids_sha256"]
            or response["seed"] != row["seed"] or response["thinking"] != row["thinking"]
            or response["max_new_tokens"] != row["max_new_tokens"] or response["sampling"] != SAMPLING):
        return "missing", "render_or_sampling_mismatch"
    return "completed", reason


def _execute(work, slots, identity, lease_until, open_service, *, wall=time.time, monotonic=time.monotonic):
    if not math.isfinite(lease_until):
        raise ValueError("Prepaid window must have a finite end")
    with locked(work, ".coordinator.lock"):
        identity_path, ledger_path = work / "execution.json", work / "ledger.json"
        if identity_path.exists() and _read(identity_path) != identity:
            raise ValueError("Changed plan, source, model or T37 environment")
        _write(identity_path, identity)
        for name in ("services", "states", "receipts"):
            (work / name).mkdir(exist_ok=True)
        ledger = _read(ledger_path) if ledger_path.exists() else {
            "gpu_seconds": 0, "incremental_cny": 0, "gpu_seconds_cap": GPU_CAP,
            "billing_mode": "prepaid_week", "teacher_calls": 0, "training_seconds": 0,
            "segments": [], "stop_reason": None}
        segments = ledger["segments"]
        if segments and segments[-1]["end_unix"] is None:
            previous = segments[-1]
            _close_orphan(previous)
            now = wall()
            # Actual release is unknown after a hard kill. Charge the whole gap conservatively.
            previous.update(end_unix=now, gpu_seconds=max(0, now - previous["start_unix"]),
                            release="recovered_after_interruption",
                            unobserved_seconds=max(0, now - previous["checkpoint_unix"]))
            ledger["gpu_seconds"] = sum(s["gpu_seconds"] for s in segments)
            _write(ledger_path, ledger)
        _recover_state(work, slots)
        prior = ledger["gpu_seconds"]
        segment = None
        service = None
        origin = None

        def checkpoint():
            if segment is not None:
                segment["checkpoint_unix"] = wall()
                segment["gpu_seconds"] = monotonic() - origin
                ledger["gpu_seconds"] = prior + segment["gpu_seconds"]
            _write(ledger_path, ledger)

        try:
            for row in slots:
                key = row["slot_key"]
                state = work / "states" / (key + ".json")
                if state.exists():
                    continue
                now = wall()
                used = prior + (monotonic() - origin if segment is not None else 0)
                stop = _admission_stop(used, now, lease_until, load=service is None)
                if stop:
                    ledger["stop_reason"] = stop
                    break
                if segment is None:
                    origin = monotonic()
                    segment = {"start_unix": now, "end_unix": None, "gpu_seconds": 0,
                               "window_end_unix": lease_until, "loads": [], "renderer": None}
                    segments.append(segment)
                    ledger["stop_reason"] = "running"
                    checkpoint()
                if service is None:
                    loading = {"start_unix": wall(), "ready_unix": None}
                    segment["loads"].append(loading)
                    checkpoint()
                    service = open_service()
                    live = json_bytes(service.metadata)
                    sha = digest(live)
                    atomic_write(work / "services" / (sha + ".json"), live)
                    loading.update(ready_unix=wall(), service_sha256=sha)
                    pid = getattr(getattr(service, "process", None), "pid", None)
                    segment["renderer"] = {"pid": pid, "start_tick": _process_token(pid)} if pid else None
                    checkpoint()
                    stop = _admission_stop(prior + monotonic() - origin, wall(), lease_until, load=False)
                    if stop:
                        ledger["stop_reason"] = stop
                        break
                raw = (work / row["request_path"]).read_bytes()
                if digest(raw) != row["request_sha256"]:
                    raise ValueError("Prepared public request changed")
                body = json.loads(raw)
                receipt = work / "receipts" / (key + ".json")
                claim = {"slot_key": key, "state": "claimed", "claimed_unix": wall(), "service_sha256": sha}
                _write(state, claim)
                started = monotonic()
                response = _request(service, row, body, monotonic)
                elapsed = monotonic() - started
                status, reason = _classify(response, row)
                _write(receipt, {"slot_key": key, "status": status, "reason": reason, "response": response,
                                 "service_sha256": sha, "request_seconds": response.get("request_seconds", elapsed),
                                 "started_unix": claim["claimed_unix"], "returned_unix": wall()})
                _finish(state, receipt, claim)
                if status in {"missing", "timeout"}:
                    service.cancel()
                    service = None
                    segment["renderer"] = None
                    loading["released_unix"] = wall()
                checkpoint()
                if reason == "render_or_sampling_mismatch":
                    raise ValueError("Live request differs from the prepared input or sampling")
            else:
                ledger["stop_reason"] = "finished"
        except BaseException as exc:
            ledger["stop_reason"] = f"interrupted:{type(exc).__name__}"
            raise
        finally:
            if service is not None:
                service.close()
                loading["released_unix"] = wall()
            if segment is not None:
                segment.update(end_unix=wall(), renderer=None, release="service_closed")
            ledger["status_counts"] = dict(Counter(
                _read(work / "states" / (r["slot_key"] + ".json")).get("status", "claimed")
                if (work / "states" / (r["slot_key"] + ".json")).exists() else "unstarted" for r in slots))
            checkpoint()
        return ledger


def run(out_dir, run_dir, model_path, *, lease_until):
    """Execute the approved paired study against the verified, frozen T37 service."""
    from . import student_process
    from .paired_admission import read_admission
    from .paired_backend import manifest_path
    from .sense_example_prepare import load_plan

    work, archive, model = (Path(p).resolve() for p in (out_dir, run_dir, model_path))
    plan = load_plan(work)
    campaign_path = archive / "campaign.json"
    if digest(campaign_path.read_bytes()) != CAMPAIGN:
        raise ValueError("Unexpected campaign")
    campaign = _read(campaign_path)
    admission, admission_sha = read_admission(archive, CAMPAIGN, "transformers")
    if admission_sha != T37 or platform.system() != "Linux" or admission["host"]["node"] != platform.node():
        raise ValueError("Use the original T37 host and admission")
    terms = admission["lease"]
    if terms["billing_mode"] != "prepaid_week" or terms["incremental_cny"] != 0:
        raise ValueError("This experiment requires the confirmed zero-increment prepaid order")
    frozen = archive / "source"
    for name, expected in campaign["sources"].items():
        if name.startswith("scripts/") and digest((frozen / name).read_bytes()) != expected:
            raise ValueError(f"Changed frozen source: {name}")
    if digest(Path(student_process.__file__).read_bytes()) != campaign["sources"]["scripts/thinking_events/student_process.py"]:
        raise ValueError("Coordinator must use the frozen StudentProcess")
    descriptor = _read(frozen / manifest_path(MODEL))
    original = _read(archive / "t37" / admission["artifacts"]["metadata_" + MODEL]["path"])
    identity = {"plan_sha256": digest((work / "plan.json").read_bytes()), "admission_sha256": T37,
                "runner_sha256": digest(Path(__file__).read_bytes()), "backend": "transformers",
                "model": MODEL, "revision": original["revision"], "model_manifest_sha256": original["model_manifest_sha256"],
                "campaign_sha256": CAMPAIGN, "generation": campaign["generation"]}

    def open_service():
        service = student_process.StudentProcess(
            admission["interpreters"]["student"], backend="transformers", model=MODEL,
            model_path=model, model_manifest=descriptor, revision=original["revision"],
            scripts_root=frozen / "scripts", startup_timeout=LOAD_SECONDS)
        if service.metadata != original:
            service.close()
            raise ValueError("Live renderer metadata differs from the T37 service")
        return service

    return _execute(work, plan["slots"], identity, lease_until, open_service)
