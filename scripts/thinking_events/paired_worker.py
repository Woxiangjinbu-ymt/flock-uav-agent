"""Run a claimed paired task against its frozen simulator and original checker."""

import json
from pathlib import Path

from harness_episode_local import digest as checker_tree_digest

from .campaign import PAIRED_PROTOCOL, ROOT, verify_run
from .campaign_storage import digest, read_jsonl
from .student import MAX_CONTEXT
from . import worker


def admitted_batch(run, trial_id, manifest, *, model, thinking, backend, fingerprint):
    """Bind a service and one owned slot to the same immutable batch identity."""
    campaign = verify_run(run)
    raw = (run / "batch-execution.json").read_bytes()
    batch = json.loads(raw)
    if (campaign["schema"] != PAIRED_PROTOCOL or digest(raw) != fingerprint
            or (run / "selected-backend.sha256").read_text().strip() != fingerprint
            or batch.get("campaign_sha256") != (run / "campaign.sha256").read_text().strip()
            or batch.get("plan_sha256") != campaign["plan_slots_sha256"]
            or batch.get("backend") != backend
            or (batch.get("scope"), backend) not in {
                ("local_fixture", "fixture"), ("formal", "transformers"), ("formal", "vllm")}):
        raise ValueError("Paired slot does not match its selected batch and backend")
    g3 = batch.get("g3") or {}
    if (g3.get("verified") is not True or g3.get("status") != "pass"
            or g3.get("sha256") != campaign["g3_reference_sha256"]):
        raise ValueError("Paired worker requires the verified G3 result")
    if batch["scope"] == "formal":
        from .paired_admission import read_admission

        _, admitted = read_admission(run, batch["campaign_sha256"], backend)
        if batch.get("admission_sha256") != admitted:
            raise ValueError("Paired worker lacks its T37 admission")
    if (manifest.get("scope") != "thinking_events_dev_local_backend"
            or manifest.get("campaign_sha256") != batch["campaign_sha256"]
            or manifest.get("workspace") != str(ROOT)
            or manifest.get("study") != str(run / "study")
            or manifest.get("sources") != campaign["sources"]
            or manifest.get("inputs") != campaign["inputs"]):
        raise ValueError("Worker manifest differs from the frozen paired campaign")
    claim, = [row for row in read_jsonl(run / "slots.jsonl") if row["slot_id"] == trial_id]
    if ((claim["status"], claim.get("backend_sha256"), claim["model"]) != ("started", fingerprint, model)
            or claim["thinking"] is not thinking):
        raise ValueError("Paired worker requires a matching started slot")
    trial, = [trial for trial in manifest["trials"] if trial["trial_id"] == trial_id]
    if trial != {"trial_id": trial_id, "task_id": claim["task_id"], "scheduler": "applicability",
                 "runtime": campaign["runtime"], "watchdog_seconds": 515}:
        raise ValueError("Paired worker task or runtime differs from the plan")
    return batch


def _diagnostics(outcome, audits, passed):
    """Keep failure attribution unknown without positive, sealed evidence."""
    limits = outcome["limits"]
    attempts = limits["attempts"]
    stop = limits["stop_reason"]
    aliases = {"http_attempt_limit": "request_limit", "token_limit": "token_budget",
               "task_deadline": "episode_deadline", "transport_error": "student_transport_error"}
    episode_limited = bool(stop == "request_timeout" and attempts
                           and attempts[-1]["admitted_timeout_seconds"]
                           < outcome["runtime"]["request_limits"]["per_request_timeout_seconds"])
    result = {"raw_stop_reason": stop, "stop_reason": "episode_deadline" if episode_limited else aliases.get(stop, stop),
              "request_limited_by_episode": episode_limited, "service_ok": None, "context_fits": None}
    complete = bool(attempts) and [r["turn"] for r in audits] == [a["attempt_id"] for a in attempts]
    if complete and all(r["status"] in {"ok", "format_error"} for r in audits):
        result["service_ok"] = True
    service_errors = {"transport_error", "student_transport_error", "student_backend_error", "http_error",
                      "service_error", "response_identity_mismatch", "unknown_usage", "input_rejected",
                      "invalid_request_body", "streaming_unsupported", "invalid_output_limit",
                      "cancelled", "interrupted", "concurrent_request"}
    if stop in service_errors or any(r.get("stop_reason") in service_errors for r in audits):
        result["service_ok"] = False
    if stop == "context_length" or any(r.get("stop_reason") == "context_length" for r in audits):
        result["context_fits"] = False
    elif complete and all(isinstance(r.get("input_ids"), list) and r["input_ids"] for r in audits):
        result["context_fits"] = all(len(r["input_ids"]) + len(r.get("generated_ids") or []) <= MAX_CONTEXT
                                     for r in audits)
    if passed or result["service_ok"] is not True or result["context_fits"] is not True:
        return result
    last = audits[-1]
    if (last["status"] == "format_error" and stop == last.get("stop_reason") and stop
            and stop not in {"generation_truncated", "generation_limit"}):
        result["failure_evidence"] = {"kind": "tool_format", "detail": f"Generation parser rejected turn {last['turn']}: {stop}",
                                      "reference": f"private/student-requests.jsonl#L{len(audits)}"}
    elif not stop and not outcome["actions"] and last["status"] == "ok":
        message = last.get("message") or {}
        text = message.get("content") or ""
        if not message.get("tool_calls") and "[TASK DONE]" in text and outcome["agent"]["success"]:
            result["failure_evidence"] = {"kind": "decision_error",
                                          "detail": "The student explicitly claimed completion without any flight action; the original checker failed.",
                                          "reference": f"private/student-requests.jsonl#L{len(audits)}"}
    return result


def execute_slot(run, slot, *, service, evidence_kind="local_fixture"):
    """Execute a single owned slot with the warm service chosen by the coordinator."""
    run = Path(run).resolve()
    frozen, = [row for row in read_jsonl(run / "plan-slots.jsonl") if row["slot_id"] == slot["slot_id"]]
    if any(slot.get(key) != value for key, value in frozen.items()):
        raise ValueError("Requested slot differs from the frozen task")
    raw = (run / "batch-execution.json").read_bytes()
    batch = json.loads(raw)
    if (batch.get("scope") != evidence_kind or not service or not isinstance(service.metadata, dict)
            or service.metadata.get("backend") != batch.get("backend")
            or service.metadata.get("weights_loaded") is not (evidence_kind == "formal")):
        raise ValueError("The requested evidence kind differs from the batch or service")
    result = worker.run_slot(run, frozen["slot_id"], model=frozen["model"], thinking=frozen["thinking"],
                             service=service, backend=batch["backend"], admitted_backend_sha256=digest(raw))
    result.update(evidence_kind=evidence_kind, service_ok=None, context_fits=None)
    if result["status"] != "scored":
        return result
    folder = run / "trials" / frozen["slot_id"]
    outcome = json.loads((folder / "worker-result.json").read_bytes())
    fixture = json.loads((run / "study/fixtures" / (frozen["task_id"] + ".json")).read_bytes())
    if outcome["initial"]["original_checker_tree_sha256"] != checker_tree_digest(
            fixture["original_task"]["execution_check_apis"]):
        raise ValueError("Independent checker differs from the frozen task's original tree")
    audit_path = folder / "private/student-requests.jsonl"
    audits = read_jsonl(audit_path) if audit_path.exists() else []
    result.update(_diagnostics(outcome, audits, result["passed"]))
    return result
