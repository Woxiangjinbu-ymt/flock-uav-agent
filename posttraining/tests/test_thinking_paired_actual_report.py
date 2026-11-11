"""Independent result ingestion must never trust a journal score or promote fixture runs."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from thinking_events.campaign_storage import digest, json_bytes
from thinking_events.paired_actual_report import _cost, _terminal
from thinking_events.paired_worker import _diagnostics
from thinking_events.worker import _metrics


def put(base, name, obj):
    file = base / name
    file.parent.mkdir(parents=True, exist_ok=True)
    data = json_bytes(obj)
    file.write_bytes(data)
    return digest(data)


@pytest.fixture
def sealed(tmp_path):
    run = tmp_path / "run"
    sid, task = "owned-slot", "frozen-task"
    folder = run / "trials" / sid
    tree = {"checks": [], "logic": "and"}
    initial = {"original_state_sha256": "1" * 64, "public_startup_sha256": "2" * 64}
    put(run, "study/fixtures/frozen-task.json", {"state_sha256": initial["original_state_sha256"],
                                              "public_startup_sha256": initial["public_startup_sha256"]})
    snap = put(folder, "private/score-world.pkl", "opaque-snapshot")
    worker = {"status": "completed", "trial_id": sid, "task_id": task, "pid": 123, "drained": True,
              "limits": {"closed": True, "stop_reason": None, "attempts": [], "admitted_http_attempts": 0,
                         "threads_still_alive": [], "needs_process_termination": False},
              "runtime": {"task_seconds": 500, "request_limits": {"max_http_attempts": 12, "max_total_tokens": 100000}},
              "elapsed": 1.5, "initial": initial, "actions": [], "network_attempts": [], "isolation": {"closed": True},
              "snapshot": {"path": "private/score-world.pkl", "sha256": snap}}
    worker_sha = put(folder, "worker-result.json", worker)
    worker_seal = put(run, f"worker-exit-seals/{sid}.json", {"worker-result.json": worker_sha,
                                                            "private/score-world.pkl": snap})
    task_sha = put(folder, "evaluation/private-task.json", {"id": task,
                   "execution_check_apis": {**tree, "_episode_private_canary": "private"}})
    score = {"trial_id": sid, "pid": 234, "worker_pid": worker["pid"], "worker_result_sha256": worker_sha,
             "snapshot_sha256": snap, "private_canary_present": True, "checker_tree_matches_original": True,
             "agent_imported": False, "network_attempts": [], "checker": {"passed": False, "errors": []}}
    score_sha = put(folder, "evaluation/result.json", score)
    score_seal = put(run, f"scorer-exit-seals/{sid}.json", {
        "evaluation/private-task.json": task_sha, "evaluation/result.json": score_sha})
    result = {"status": "scored", "evidence_kind": "local_fixture", "trial_id": sid, "task_id": task,
              "worker_pid": 123, "scorer_pid": 234, "worker_seal_sha256": worker_seal, "scorer_seal_sha256": score_seal,
              "passed": False, "usage": _metrics(folder, worker, "fixture"),
              "student_metadata": {"model": "Qwen3.5-2B", "backend": "fixture", "weights_loaded": False},
              **_diagnostics(worker, [], False)}
    put(run, f"terminal-results/{sid}.json", result)
    row = {"status": "scored", "slot_id": sid, "task_id": task, "model": "Qwen3.5-2B", "thinking": True,
           "turn_seeds": [13], "checker_sha256": digest(json_bytes(tree)), "result": result}
    return run, row, {"scope": "local_fixture", "backend": "fixture"}


def test_recheck_score_after_relocation(sealed):
    run, row, execution = sealed
    dest = run.with_name("copied-campaign")
    run.rename(dest)
    proof = _terminal(dest, row, execution)
    assert (dest / proof["checker_result"]).is_file()
    assert (dest / proof["terminal"]).is_file()
    assert proof["worker_seal_sha256"] != proof["scorer_seal_sha256"]
    assert row["result"]["passed"] is False


@pytest.mark.parametrize("change", ["tamper-score", "same-pid", "promoted", "changed-tree", "fake-usage",
                                    "synthetic", "altered-attribution", "mixed-scope"])
def test_cannot_import_fabricated_scores(sealed, change):
    run, original, execution = sealed
    row = deepcopy(original)
    sid = row["slot_id"]
    result = row["result"]
    if change == "tamper-score":
        put(run, f"trials/{sid}/evaluation/result.json", {"passed": True})
    elif change == "same-pid":
        result["scorer_pid"] = result["worker_pid"]
    elif change == "promoted":
        execution = {"scope": "formal", "backend": "transformers"}
        result["evidence_kind"] = "formal"
    elif change == "mixed-scope":
        result["evidence_kind"] = "synthetic"
    elif change == "changed-tree":
        row["checker_sha256"] = "3" * 64
    elif change == "fake-usage":
        result["usage"]["reasoning_tokens"] = 100
    elif change == "synthetic":
        result["synthetic_checker"] = {"passed": False}
    elif change == "altered-attribution":
        result["service_ok"] = True
    put(run, f"terminal-results/{sid}.json", result)
    with pytest.raises(ValueError):
        _terminal(run, row, execution)


def test_interrupted_or_unscored_slots(sealed):
    run, row, execution = sealed
    sid = row["slot_id"]
    row.update(status="started", result=None)
    with pytest.raises(ValueError, match="finish recovery"):
        _terminal(run, row, execution)
    (run / "terminal-results" / (sid + ".json")).unlink()
    assert _terminal(run, row, execution) is None
    row.update(status="unscored", result={"status": "unscored", "trial_id": sid, "task_id": row["task_id"],
               "passed": None, "evidence_kind": "local_fixture", "unscored_reason": "interrupted_before_terminal_receipt"})
    put(run, f"terminal-results/{sid}.json", row["result"])
    assert "checker_result" not in _terminal(run, row, execution)


def test_cost_matches_settlements_and_scope(tmp_path):
    entry = {"lease": "a", "charged_cny": "5.20", "billed_seconds": 300}
    ledger = {"reservation": None, "gpu_billed_seconds": 300, "paid_cny": "5.30",
              "entries": [entry], "ancillary_fees": {"transfer": "0.10"}}
    put(tmp_path, "cost-ledger.json", ledger)
    assert _cost(tmp_path, "formal")["settled_cny"] == "5.30"
    with pytest.raises(ValueError, match="Local fixture"):
        _cost(tmp_path, "local_fixture")
    put(tmp_path, "cost-ledger.json", {**ledger, "paid_cny": "7.20"})
    with pytest.raises(ValueError, match="totals disagree"):
        _cost(tmp_path, "formal")
