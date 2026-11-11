"""Run the frozen 116-slot study with a local fixture or a T37-qualified backend."""
from collections import Counter
from contextlib import ExitStack
import math
import os
from pathlib import Path
import platform
import time

from prepare_task_dataset import parse_json

from .campaign import PAIRED_MODELS, PAIRED_PROTOCOL, bind_host, g3_evidence, verify_run
from .campaign_storage import RentalBudget, SlotLedger, atomic_write, digest, json_bytes, locked, read_jsonl
from .paired_admission import read_admission

DEFAULT_G3 = Path(__file__).resolve().parents[2] / "docs/results/thinking-events-g3-20261108/run-01/g3.json"
LOCAL = "local_fixture"
FORMAL = "formal"
STARTUP_SECONDS = 180


def _execution(run, campaign, g3, backend):
    if campaign["schema"] != PAIRED_PROTOCOL or backend not in ("fixture", "transformers", "vllm"):
        raise ValueError("Execution requires the paired Qwen3.5-2B/Qwen3-4B protocol")
    evidence = g3_evidence(g3)
    if not evidence["verified"] or evidence["status"] != "pass":
        raise ValueError("G3 evidence is missing, invalid or has not passed")
    campaign_sha = (run / "campaign.sha256").read_text().strip()
    admission_sha = None
    if backend != "fixture":
        _, admission_sha = read_admission(run, campaign_sha, backend)
    return {"schema": "paired-batch-execution-v1", "scope": LOCAL if backend == "fixture" else FORMAL,
            "backend": backend, "g3": evidence, "admission_sha256": admission_sha,
            "campaign_sha256": campaign_sha, "plan_sha256": campaign["plan_slots_sha256"]}


def verify_execution(run, campaign, g3):
    run = Path(run)
    raw = (run / "batch-execution.json").read_bytes()
    execution = parse_json(raw)
    if execution != _execution(run, campaign, g3, execution["backend"]):
        raise ValueError("Invalid batch scope, G3, plan or T37 binding")
    selected = run / "selected-backend.sha256"
    claims = [r for r in read_jsonl(run / "slots.jsonl") if r["status"] != "unstarted"]
    if selected.exists() or claims:
        sha = digest(raw)
        if selected.read_text().strip() != sha or any(r.get("backend_sha256") != sha for r in claims):
            raise ValueError("Slot claim does not match the selected batch")
    return execution


def _reserve(run, admission, work_seconds):
    quote = admission["lease"]
    budget = RentalBudget(run)
    if quote.get("billing_mode") == "prepaid_week":
        return budget.reserve_prepaid(quote, work_seconds)
    if quote.get("billing_mode", "hourly") != "hourly":
        raise ValueError("Unknown rental billing mode")
    elapsed = math.ceil(time.time() - quote["started_unix"])
    if elapsed < 0 or quote["shutdown_seconds"] <= 0 or float(quote["hourly_cny"]) <= 0:
        raise ValueError("Invalid lease start time, price or shutdown reservation")
    return budget.reserve(
        quote["id"], elapsed_seconds=elapsed, work_seconds=work_seconds,
        shutdown_seconds=quote["shutdown_seconds"], billing_seconds=quote["billing_seconds"],
        hourly_cny=quote["hourly_cny"], extra_cny=quote["extra_cny"])


def _qualified_service(metadata, admission):
    expected = admission["versions"]
    packages = ["transformers", "tokenizers", "torch", "cuda"]
    if admission["backend"] == "vllm":
        packages.append("vllm")
    if any(not expected.get(name) or metadata.get(name) != expected[name] for name in packages):
        raise ValueError("Live inference versions differ from the T37 environment")
    if (metadata.get("gpu_count") != 1 or "A100" not in metadata.get("device", "")
            or not 38 * 2**30 <= metadata.get("gpu_memory_bytes", 0) <= 42 * 2**30
            or metadata.get("dtype") != "bfloat16" or metadata.get("weights_loaded") is not True):
        raise ValueError("Live inference device differs from the T37 A100 40GB backend")


def _open_service(run, plan, model, backend, student_python, model_root, backend_python, tokenizer):
    from .paired_backend import fixture_replies
    from .student_process import StudentProcess

    options = {}
    if backend == "fixture":
        options["fixture_replies"] = {
            r["slot_id"]: fixture_replies(model, r["thinking"]) for r in plan if r["model"] == model
        }
        if model == "Qwen3.5-2B" and tokenizer is not None:
            options["tokenizer_path"] = tokenizer
    else:
        if not model_root:
            raise ValueError("Real execution requires the offline --model-root")
        from .paired_backend import manifest_path
        manifest = run / "source" / manifest_path(model)
        path = Path(model_root) / model
        options.update(model_path=path, tokenizer_path=path, model_manifest=parse_json(manifest.read_bytes()),
                       revision=PAIRED_MODELS[model], backend_python=backend_python)
    return StudentProcess(student_python, backend=backend, model=model, scripts_root=run / "source/scripts",
                          startup_timeout=STARTUP_SECONDS, **options)


def _terminal(run, row, scope):
    receipt = run / "terminal-results" / (row["slot_id"] + ".json")
    if receipt.exists():
        result = parse_json(receipt.read_bytes())
        if (result.get("trial_id"), result.get("task_id"), result.get("evidence_kind")) != (
                row["slot_id"], row["task_id"], scope):
            raise ValueError("Terminal receipt belongs to another task, slot or scope")
        if row["status"] in ("scored", "unscored") and row["result"] != result:
            raise ValueError("Terminal receipt differs from slot journal")
        if row["status"] == "unstarted":
            raise ValueError("Unclaimed slot has terminal evidence")
        return result
    if row["status"] in ("scored", "unscored"):
        raise ValueError("Missing committed terminal receipt")
    if row["status"] == "started":
        return {"trial_id": row["slot_id"], "task_id": row["task_id"], "evidence_kind": scope,
                "status": "unscored", "passed": None, "unscored_reason": "interrupted_before_terminal_receipt"}
    return None


def _verify_source(run, campaign):
    # The coordinator runs in the Agent environment. Prevent resuming with changed
    # code while the subprocesses still execute an older frozen implementation.
    scripts = Path(__file__).resolve().parent
    for path in scripts.glob("*.py"):
        name = "scripts/thinking_events/" + path.name
        if campaign["sources"].get(name) != digest(path.read_bytes()):
            raise ValueError(f"Coordinator version differs from frozen campaign: {name}")


def execute_batch(run, g3, *, backend, student_python, model_root=None, backend_python=None,
                  tokenizer=None, max_new=None):
    from .paired_worker import execute_slot

    if max_new is not None and (type(max_new) is not int or max_new < 1):
        raise ValueError("--max-new must be a positive slot count")
    run = Path(run).resolve()
    with locked(run, ".coordinator.lock"):
        campaign = verify_run(run)
        _verify_source(run, campaign)
        bind_host(run)
        plan = read_jsonl(run / "plan-slots.jsonl")
        execution = _execution(run, campaign, g3, backend)
        scope = execution["scope"]
        admission = None
        if scope == FORMAL:
            if platform.system() != "Linux":
                raise ValueError("Real execution requires the qualified Linux GPU host")
            admission, _ = read_admission(run, execution["campaign_sha256"], backend)
            if admission["host"]["node"] != platform.node():
                raise ValueError("The qualified Linux host differs from this host")
            # Keep the exact renderer and inference environment selected during T37.
            interpreters = {"student": os.path.abspath(student_python),
                            "backend": os.path.abspath(backend_python or student_python)}
            if interpreters != admission["interpreters"] or not model_root:
                raise ValueError("Use the T37-qualified interpreters and offline model root")
        path = run / "batch-execution.json"
        raw = json_bytes(execution)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("Cannot change scope or selected backend in an existing batch")
        if not path.exists():
            atomic_write(path, raw)
        verify_execution(run, campaign, g3)
        fingerprint = digest(raw)
        terminal = run / "terminal-results"
        terminal.mkdir(exist_ok=True)
        ledger = SlotLedger(run, plan)
        started, stop = 0, None
        with ExitStack() as stack:
            service, loaded_model = None, None
            for row in ledger.read():
                slot = row["slot_id"]
                result = _terminal(run, row, scope)
                if row["status"] in ("scored", "unscored"):
                    continue
                if result is None:
                    if max_new is not None and started >= max_new:
                        stop = "max_new"
                        break
                    reload_model = (loaded_model != row["model"] or service is None
                                    or service.process.poll() is not None)
                    if admission is not None:
                        # Reserve model-load time, worker and independent scorer watchdogs, and shutdown.
                        seconds = (STARTUP_SECONDS if reload_model else 0) + 2 * 515
                        reason = _reserve(run, admission, seconds)
                        if reason != "admitted":
                            stop = reason
                            break
                    if reload_model:
                        stack.close()
                        service = stack.enter_context(_open_service(
                            run, plan, row["model"], backend, student_python, model_root, backend_python, tokenizer))
                        loaded_model = row["model"]
                        if admission is not None:
                            _qualified_service(service.metadata, admission)
                            reason = _reserve(run, admission, 2 * 515)
                            if reason != "admitted":
                                stop = reason
                                break
                    if not ledger.claim(slot, fingerprint):
                        raise ValueError(f"Slot was already claimed: {slot}")
                    started += 1
                    result = execute_slot(run, row, service=service, evidence_kind=scope)
                atomic_write(terminal / (slot + ".json"), json_bytes(result))
                ledger.finish(slot, result)
        statuses = Counter(r["status"] for r in ledger.read())
        summary = {"scope": scope, "backend": backend, "planned_slots": len(plan),
                   "slot_statuses": dict(statuses), "stop_reason": stop or "finished",
                   "new_claims": started, "formal_launches_this_invocation": started if scope == FORMAL else 0,
                   "provider_calls": 0, "training_hours": 0}
        atomic_write(run / "batch-status.json", json_bytes(summary))
        return summary
