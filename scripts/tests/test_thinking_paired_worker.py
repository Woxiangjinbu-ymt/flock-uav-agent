"""Paired worker ownership, simulator isolation, original scores and failure attribution."""
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_task_dataset import materialize, parse_json
from thinking_events.campaign import prepare_dev, verify_run
from thinking_events.campaign_storage import SlotLedger, digest, json_bytes, read_jsonl
from thinking_events.paired_batch import DEFAULT_G3, _execution, _open_service
from thinking_events.paired_worker import _diagnostics, execute_slot


def test_original_checker_and_new_worker_for_both_arms(tmp_path):
    study = os.environ.get("THINKING_DEV_STUDY")
    if not study:
        study = tmp_path / "study"
        materialize(ROOT / "experiments/task-splits-v1", study, split="dev", purpose="development")
    run = tmp_path / "run"
    prepare_dev(study, run)
    campaign = verify_run(run)
    plan = read_jsonl(run / "plan-slots.jsonl")
    ledger = SlotLedger(run, plan)
    raw = json_bytes(_execution(run, campaign, DEFAULT_G3, "fixture"))
    (run / "batch-execution.json").write_bytes(raw)
    on, off = plan[58:60]  # Exercise the reference model with its portable tokenizer bundle.
    assert on["model"] == off["model"] == "Qwen3-4B" and on["thinking"] and not off["thinking"]
    python = os.environ.get("FLOCKCUE_TOKENIZER_PYTHON", ROOT / "training/.venv-input/bin/python")
    results = []
    with _open_service(run, plan, on["model"], "fixture", python, None, None, None) as service:
        for slot in (on, off):
            assert ledger.claim(slot["slot_id"], digest(raw))
            with pytest.raises(ValueError, match="evidence kind"):
                execute_slot(run, slot, service=service, evidence_kind="formal")
            with pytest.raises(ValueError, match="frozen task"):
                execute_slot(run, {**slot, "task_id": "another-task"}, service=service)
            result = execute_slot(run, slot, service=service)
            ledger.finish(slot["slot_id"], result)
            results.append(result)
            folder = run / "trials" / slot["slot_id"]
            assert result["status"] == "scored", result
            assert result["service_ok"] and result["context_fits"] and result["evidence_kind"] == "local_fixture"
            assert result["usage"]["requests"] == 2 and result["usage"]["prompt_tokens"] > 0
            assert result["passed"] is False and result["failure_evidence"]["kind"] == "decision_error"
            requests = read_jsonl(folder / "policy/worldlogs/public-api.jsonl")
            assert any(r["endpoint"] == "/drones" and not r["admin"] for r in requests)
            scorer = parse_json((folder / "evaluation/result.json").read_bytes())
            assert scorer["checker_tree_matches_original"] and scorer["checker"]["passed"] is result["passed"]
            assert not scorer["agent_imported"] and not scorer["network_attempts"]
            for role in ("worker", "scorer"):
                seal = run / f"{role}-exit-seals" / f"{slot['slot_id']}.json"
                assert digest(seal.read_bytes()) == result[f"{role}_seal_sha256"]
    assert results[0]["initial"] == results[1]["initial"]
    assert len({r[k] for r in results for k in ("worker_pid", "scorer_pid")}) == 4
    assert sum(r["status"] == "unstarted" for r in ledger.read()) == 114


@pytest.mark.parametrize("stop,seconds,status,expected,context,service,attributed", [
    ("generated_format", 120, "format_error", "generated_format", True, True, True),
    ("generation_truncated", 120, "format_error", "generation_truncated", True, True, False),
    ("request_timeout", 8.0, "stopped", "episode_deadline", None, None, False),
    ("request_timeout", 120, "stopped", "request_timeout", None, None, False),
    ("context_length", 120, "stopped", "context_length", False, None, False),
    ("student_backend_error", 120, "stopped", "student_backend_error", None, False, False),
    ("input_rejected", 120, "stopped", "input_rejected", None, False, False),
])
def test_diagnostics_keep_budget_and_infrastructure_out_of_task_failures(
        stop, seconds, status, expected, context, service, attributed):
    worker = {"limits": {"attempts": [{"attempt_id": 1, "admitted_timeout_seconds": seconds}],
                         "stop_reason": stop}, "runtime": {"request_limits": {"per_request_timeout_seconds": 120}}}
    audit = {"turn": 1, "stop_reason": stop, "status": status,
             "input_ids": [1] if status == "format_error" else None, "generated_ids": None}
    result = _diagnostics(worker, [audit], False)
    assert result["stop_reason"] == expected and result["raw_stop_reason"] == stop
    assert result["service_ok"] is service and result["context_fits"] is context
    assert ("failure_evidence" in result) is attributed
