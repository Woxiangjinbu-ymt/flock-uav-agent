"""Mac and Linux CPU-only campaign admission, relocation, recovery and cost guards."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_task_dataset import materialize
from thinking_events import campaign
from thinking_events.campaign_storage import (
    RentalBudget, SlotLedger, digest, json_bytes, read_jsonl, run_local_slots,
)

G3 = ROOT / "docs/results/thinking-events-g3-20261108/run-01/g3.json"


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    if os.environ.get("THINKING_DEV_STUDY"):
        return Path(os.environ["THINKING_DEV_STUDY"])
    out = tmp_path_factory.mktemp("real-dev") / "study"
    materialize(ROOT / "experiments/task-splits-v1", out, split="dev", purpose="development")
    return out


def test_relocation_freezes_29_tasks_and_four_paired_arms(tmp_path, study):
    run = tmp_path / "mac"
    summary = campaign.prepare_dev(study, run)
    assert summary["tasks"] == 29 and summary["parent_scenes"] == 5 and summary["slots"] == 116
    rows = read_jsonl(run / "plan-slots.jsonl")
    assert [(r["model"], r["thinking"]) for r in rows[:4]] == [
        ("1.7B", True), ("1.7B", False), ("1.7B", False), ("1.7B", True)]
    assert {r["model"] for r in rows[58:]} == {"4B"}
    for i in range(0, 58, 2):
        left, right = rows[i:i + 2]
        ref = hashlib.sha256((left["task_id"] + "/1").encode()).digest()[:4]
        assert left["turn_seeds"][0] == int.from_bytes(ref, "big")
        for other in (right, rows[i + 58], rows[i + 59]):
            for field in ("task_id", "scene_id", "turn_seeds", "fixture_sha256", "command_sha256",
                          "snapshot_sha256", "timeline_sha256", "checker_sha256"):
                assert left[field] == other[field]
    assert len(set(r["slot_id"] for r in rows)) == 116
    seal = (run / "campaign.sha256").read_bytes()
    moved = tmp_path / "linux" / "dev"
    moved.parent.mkdir()
    run.rename(moved)
    actual = campaign.run_dev(moved, G3)
    assert actual["slot_statuses"] == {"unstarted": 116}
    assert (actual["g3"]["verified"], actual["reason"]) == (True, "awaiting_linux_preflight_and_t37")
    assert actual["formal_launches_this_invocation"] == actual["gpu_launches"] == 0
    assert actual["G1"] == actual["G2"] == "indeterminate"
    binding = campaign.bind_host(moved)
    assert binding["study"] == str(moved / "study") and binding["source"] == str(moved / "source")
    assert json.loads((moved / "manifest.json").read_bytes())["study"] == binding["study"]
    assert (moved / "campaign.sha256").read_bytes() == seal
    assert str(tmp_path) not in (moved / "campaign.json").read_text()
    assert str(tmp_path) not in (moved / "study/manifest.json").read_text()
    second = tmp_path / "restored-portable-study"
    campaign.prepare_dev(moved / "study", second)
    assert (second / "campaign.sha256").read_bytes() == seal
    with pytest.raises(FileExistsError):
        campaign.prepare_dev(study, second)
    (moved / "source/scripts/harness_natural_runner.py").write_text("modified")
    with pytest.raises(ValueError, match="harness_natural_runner"):
        campaign.bind_host(moved)


def test_changed_snapshot_and_split_are_rejected(tmp_path, study):
    copy = tmp_path / "study"
    shutil.copytree(study, copy)
    task = next((copy / "fixtures").glob("[!s]*.pkl"))
    task.write_bytes(b"changed")
    with pytest.raises(ValueError, match=task.name):
        campaign.prepare_dev(copy, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()
    manifest = json.loads((copy / "manifest.json").read_bytes())
    # A repacked pickle with recomputed local seals must not reach a worker.
    metadata = task.with_suffix(".json")
    fixture = json.loads(metadata.read_bytes())
    fixture["snapshot_sha256"] = digest(task.read_bytes())
    metadata.write_bytes(json_bytes(fixture))
    for changed in (task, metadata):
        manifest["files"][str(changed.relative_to(copy))] = digest(changed.read_bytes())
    (copy / "manifest.json").write_bytes(json_bytes(manifest))
    (copy / "manifest.sha256").write_text(digest(json_bytes(manifest)))
    with pytest.raises(ValueError, match="frozen dev inputs"):
        campaign.prepare_dev(copy, tmp_path / "invalid")
    manifest["split"] = "test"
    data = json_bytes(manifest)
    (copy / "manifest.json").write_bytes(data)
    (copy / "manifest.sha256").write_text(digest(data))
    with pytest.raises(ValueError, match="dev/development"):
        campaign.prepare_dev(copy, tmp_path / "invalid")


def test_g3_fixture_cannot_authorize_formal_work(tmp_path, study):
    run = tmp_path / "run"
    campaign.prepare_dev(study, run)
    evidence = tmp_path / "evidence"
    shutil.copytree(G3.parent, evidence)
    (evidence / "turns.jsonl").write_text("")
    for path in (evidence / "g3.json", evidence / "missing"):
        result = campaign.run_dev(run, path)
        assert result["reason"] == "g3_evidence_invalid"
        assert result["G1"] == result["G2"] == "indeterminate"
        assert result["slot_statuses"] == {"unstarted": 116}
        assert result["gpu_launches"] == result["provider_calls"] == 0
    for status in ("fail", "indeterminate", "pass"):
        obj = json.loads(G3.read_bytes())
        obj["status"] = status
        (evidence / "turns.jsonl").write_bytes((G3.parent / "turns.jsonl").read_bytes())
        body = json_bytes(obj)
        (evidence / "g3.json").write_bytes(body)
        assert campaign.g3_evidence(evidence / "g3.json", reference_sha=digest(body))["status"] == status
        assert not campaign.g3_evidence(evidence / "g3.json")["verified"]


def _race(run, ready, go, results):
    ledger = SlotLedger(run, [{"slot_id": "race"}])
    ready.put(True)
    go.wait(timeout=10)
    results.put(ledger.claim("race", "a" * 64))


def test_two_processes_cannot_claim_one_slot_twice(tmp_path):
    ledger = SlotLedger(tmp_path, [{"slot_id": "race"}])
    ledger.initialize()
    ctx = multiprocessing.get_context("spawn")
    ready, go, results = ctx.Queue(), ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=_race, args=(tmp_path, ready, go, results)) for _ in range(2)]
    for proc in children:
        proc.start()
    for _ in children:
        assert ready.get(timeout=15) is True
    go.set()
    assert sorted(results.get(timeout=15) for _ in children) == [False, True]
    for proc in children:
        proc.join(timeout=10)
        assert proc.exitcode == 0
    assert ledger.read()[0]["status"] == "started"


def test_slot_recovery_preserves_completion_and_consumed_claims(tmp_path):
    (tmp_path / "manifest.json").write_bytes(json_bytes({"scope": "student_cpu_regression"}))
    plan = [{"slot_id": k, "task_id": k} for k in ("after-claim", "after-result", "new")]
    ledger = SlotLedger(tmp_path, plan)
    ledger.initialize()
    for slot in ("after-claim", "after-result"):
        assert ledger.claim(slot, "a" * 64)
    results_dir = tmp_path / "terminal-results"
    results_dir.mkdir()
    def scored(slot):
        return {"trial_id": slot, "task_id": slot, "status": "scored", "passed": True}
    (results_dir / "after-result.json").write_bytes(json_bytes(scored("after-result")))
    called = []
    def execute(row):
        called.append(row["slot_id"])
        return scored(row["slot_id"])
    results = run_local_slots(tmp_path, plan, backend_sha256="a"*64, execute=execute)
    assert called == ["new"] and [r["passed"] for r in results] == [None, True, True]
    assert run_local_slots(tmp_path, plan, backend_sha256="a"*64, execute=execute) == results
    assert called == ["new"]
    with pytest.raises(ValueError, match="resume a different"):
        run_local_slots(tmp_path, plan, backend_sha256="b"*64, execute=execute)
    with pytest.raises(ValueError):
        ledger.finish("new", {**scored("new"), "passed": False})
    (tmp_path / "manifest.json").write_bytes(json_bytes({"scope": "thinking_events_dev_local_backend"}))
    with pytest.raises(ValueError, match="separate CPU"):
        run_local_slots(tmp_path, plan, backend_sha256="a"*64, execute=execute)


def test_budget_granularity_shutdown_and_ancillary_fees(tmp_path):
    budget = RentalBudget(tmp_path)
    budget.initialize()
    assert budget.record_ancillary("storage", "2.00") == "recorded"
    assert budget.record_ancillary("storage", "2.00") == "recorded"
    quote = {"work_seconds": 50, "shutdown_seconds": 10, "billing_seconds": 60, "hourly_cny": "9.90"}
    assert budget.reserve("lease", elapsed_seconds=71941, **quote) == "gpu_hours_cap"
    assert budget.reserve("lease", elapsed_seconds=71940, **quote) == "admitted"
    assert budget.reserve("lease", elapsed_seconds=71940, extra_cny=".01", **quote) == "total_cost_cap"
    assert budget.record_ancillary("transfer", ".01") == "total_cost_cap"
    budget.settle("lease", billed_seconds=72000, charged_cny="198.00")
    budget.settle("lease", billed_seconds=72000, charged_cny="198.00")
    log = budget.initialize()
    assert log["paid_cny"] == "200.00" and log["gpu_billed_seconds"] == 72000
    assert budget.reserve("extra", elapsed_seconds=0, **quote) == "gpu_hours_cap"


def test_budget_stops_with_only_shutdown_headroom(tmp_path):
    budget = RentalBudget(tmp_path)
    budget.initialize()
    assert budget.record_ancillary("storage", "199.00") == "recorded"
    quote = {"elapsed_seconds": 0, "shutdown_seconds": 60, "billing_seconds": 60, "hourly_cny": 60}
    assert budget.reserve("lease", work_seconds=1, **quote) == "total_cost_cap"
    assert budget.initialize()["reservation"] is None
