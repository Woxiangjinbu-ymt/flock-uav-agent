"""Admission, at-most-once recovery and provenance of the executed four-arm campaign."""
from copy import deepcopy
import json
from pathlib import Path
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_task_dataset import materialize
from thinking_events import campaign, paired_batch
from thinking_events.campaign_storage import RentalBudget, SlotLedger, atomic_write, digest, json_bytes, read_jsonl
from thinking_events.paired_admission import _linux_cpu, _measurements, _session_cpu, admit_t37, read_admission

G3 = paired_batch.DEFAULT_G3


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    if os.environ.get("THINKING_DEV_STUDY"):
        return Path(os.environ["THINKING_DEV_STUDY"])
    folder = tmp_path_factory.mktemp("dev29") / "study"
    materialize(ROOT / "experiments/task-splits-v1", folder, split="dev", purpose="development")
    return folder


def test_interruption_reconciles_durable_terminal_without_starting_a_model(tmp_path, study, monkeypatch):
    run = tmp_path / "dev"
    campaign.prepare_dev(study, run)
    plan = read_jsonl(run / "plan-slots.jsonl")
    info = campaign.verify_run(run)
    evidence = paired_batch._execution(run, info, G3, "fixture")
    raw = json_bytes(evidence)
    atomic_write(run / "batch-execution.json", raw)
    journal = SlotLedger(run, plan)
    (run / "terminal-results").mkdir()
    for index, slot in enumerate(plan):
        assert journal.claim(slot["slot_id"], digest(raw))
        if index == 0:
            continue  # Lost coordinator immediately after the durable claim.
        receipt = {"status": "unscored", "passed": None, "unscored_reason": "worker_exit",
                   "evidence_kind": "local_fixture", "trial_id": slot["slot_id"], "task_id": slot["task_id"]}
        atomic_write(run / "terminal-results" / (slot["slot_id"] + ".json"), json_bytes(receipt))
        if index > 1:  # For the second slot only the final ledger write was interrupted.
            journal.finish(slot["slot_id"], receipt)
    monkeypatch.setattr(paired_batch, "_open_service", lambda *a, **kw: pytest.fail("re-executed a claimed slot"))
    result = paired_batch.execute_batch(run, G3, backend="fixture", student_python=sys.executable)
    assert result["new_claims"] == 0 and result["slot_statuses"] == {"unscored": 116}
    assert journal.read()[0]["result"]["unscored_reason"] == "interrupted_before_terminal_receipt"
    assert journal.read()[1]["result"]["unscored_reason"] == "worker_exit"
    with pytest.raises((ValueError, FileNotFoundError)):
        paired_batch.execute_batch(run, G3, backend="transformers", student_python=sys.executable)
    assert paired_batch.verify_execution(run, info, G3) == evidence


def test_g3_or_t37_missing_cannot_claim_slots(tmp_path, study):
    run = tmp_path / "dev"
    campaign.prepare_dev(study, run)
    with pytest.raises(ValueError, match="G3"):
        paired_batch.execute_batch(run, tmp_path / "missing", backend="fixture", student_python=sys.executable)
    with pytest.raises(FileNotFoundError):
        paired_batch.execute_batch(run, G3, backend="transformers", student_python=sys.executable)
    assert all(row["status"] == "unstarted" for row in read_jsonl(run / "slots.jsonl"))
    assert not (run / "selected-backend.sha256").exists()


def test_t37_wall_time_and_censoring_boundaries():
    revision = campaign.PAIRED_MODELS["Qwen3.5-2B"]
    bins = {"4096": [20.0] * 3, "8192": [20.0] * 3, "13000": [30.0] * 3}
    full = {"seconds": 24.0, "generated_tokens": 2048}
    samples = {"revision": revision, "functional_smoke": True, "natural_seconds": {"on": bins, "off": bins},
               "full_length": [full] * 19 + [{"seconds": None, "generated_tokens": 0}]}
    _measurements(samples, revision)  # 12-turn estimate is exactly 250, 19/20 have a measured latency.
    invalid = deepcopy(samples)
    invalid["natural_seconds"]["on"]["13000"][0] += 0.01
    with pytest.raises(ValueError, match="250"):
        _measurements(invalid, revision)
    invalid = deepcopy(samples)
    invalid["full_length"][18] = {"seconds": None, "generated_tokens": 0}
    with pytest.raises(ValueError, match="P95"):
        _measurements(invalid, revision)


def test_target_preflight_is_bound_to_linux_and_real_device():
    docs = ROOT / "docs/results"
    cpu = json.loads((docs / "thinking-student-cpu-20261108/verification.json").read_bytes())
    session = json.loads((docs / "flockcue-mac-session-20261108/verification.json").read_bytes())
    with pytest.raises(ValueError, match="Linux"):
        _linux_cpu(cpu)
    with pytest.raises(ValueError, match="Linux"):
        _session_cpu(session)
    cpu["platform"]["system"] = "Linux"
    for result in session.values():
        result["configuration"]["system"] = "Linux"
    _linux_cpu(cpu)
    _session_cpu(session)
    session["restart"]["tasks"][0]["checker_leaf_results"] = []
    with pytest.raises(ValueError, match="changed"):
        _session_cpu(session)
    versions = {name: "qualified" for name in ("torch", "tokenizers", "transformers", "cuda")}
    metadata = dict(versions, device="NVIDIA A100-SXM4-40GB", gpu_count=1, gpu_memory_bytes=40*2**30,
                    weights_loaded=True, dtype="bfloat16")
    admission = {"backend": "transformers", "versions": versions}
    paired_batch._qualified_service(metadata, admission)
    for changes in ({"weights_loaded": False}, {"torch": "unqualified"}, {"gpu_memory_bytes": 80*2**30},
                    {"gpu_count": 2}):
        with pytest.raises(ValueError):
            paired_batch._qualified_service({**metadata, **changes}, admission)


def test_prepaid_week_keeps_card_cap_and_immutable_stage_clock(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 100000)
    budget = RentalBudget(tmp_path)
    budget.initialize()
    quote = {"id": "test-week", "billing_mode": "prepaid_week", "stage_started_unix": 30000,
             "prior_stage_seconds": 100, "incremental_cny": 0, "shutdown_seconds": 60}
    reserve = lambda q, work: paired_batch._reserve(tmp_path, {"lease": q}, work)
    assert reserve(quote, 1840) == "admitted"
    assert reserve(quote, 1841) == "gpu_hours_cap"
    assert budget.initialize()["reservation"]["prepaid_terms"] == quote
    with pytest.raises(ValueError, match="inconsistent"):
        reserve({**quote, "stage_started_unix": 29999}, 100)
    with pytest.raises(ValueError, match="prepaid"):
        reserve({**quote, "incremental_cny": 2}, 100)
    with pytest.raises(ValueError, match="prepaid"):
        reserve({**quote, "hourly_cny": 0}, 100)
    with pytest.raises(ValueError, match="prepaid"):
        budget.settle("test-week", billed_seconds=0, charged_cny=0)
    monkeypatch.setattr("time.time", lambda: 100001)
    entry = budget.settle_prepaid()
    assert (entry["billed_seconds"], entry["charged_cny"]) == (70101, "0")
    assert budget.settle_prepaid() == entry
    with pytest.raises(ValueError, match="Duplicate"):
        reserve(quote, 1)


def test_publish_t37_only_after_measured_backend_passes(tmp_path, study, monkeypatch):
    from thinking_events import paired_admission

    run = tmp_path / "run"
    campaign.prepare_dev(study, run)
    sha = (run / "campaign.sha256").read_text().strip()
    directory = run / "t37/transformers"
    directory.mkdir(parents=True)
    samples = {}
    for model, revision in campaign.PAIRED_MODELS.items():
        bins = {str(length): [1.0] * 3 for length in (4096, 8192, 13000)}
        samples[model] = {"functional_smoke": True, "revision": revision, "natural_seconds": {"on": bins, "off": bins},
                          "full_length": [{"seconds": 60.0, "generated_tokens": 2048}] * 20}
        metadata = {"revision": revision, "model": model, "backend": "transformers", "python": sys.executable,
                    "gpu_count": 1, "device": "NVIDIA A100 40GB", "gpu_memory_bytes": 40*2**30, "dtype": "bfloat16",
                    "weights_loaded": True, **{p: "test-version" for p in ("torch", "tokenizers", "transformers", "cuda")}}
        (directory / f"metadata-{model}.json").write_bytes(json_bytes(metadata))
    measured = json_bytes({"schema": "paired-t37-measurements-v1", "backend": "transformers",
                           "campaign_sha256": sha, "models": samples})
    upload = directory / "records"
    upload.mkdir()
    inventory = {}
    for number in range(80):
        path = upload / f"{number}.json"
        path.write_bytes(json_bytes({"n": number}))
        inventory["records/" + path.name] = digest(path.read_bytes())
    fixtures = json_bytes({"scope": "synthetic-test"})
    (directory / "fixtures.json").write_bytes(fixtures)
    measured = json_bytes({**json.loads(measured), "raw_files_sha256": inventory, "request_count": len(inventory),
                           "fixtures_sha256": digest(fixtures)})
    (directory / "measurements.json").write_bytes(measured)
    verdict = directory / "verdict.json"
    verdict.write_bytes(json_bytes({"passed": False, "measurement_sha256": digest(measured)}))
    quote = tmp_path / "lease.json"
    quote.write_bytes(json_bytes({"id": "test", "billing_mode": "prepaid_week", "stage_started_unix": 100000,
                                  "prior_stage_seconds": 0, "incremental_cny": 0, "shutdown_seconds": 60}))
    monkeypatch.setattr("time.time", lambda: 100002)
    monkeypatch.setattr(paired_admission.platform, "system", lambda: "Linux")
    docs = ROOT / "docs/results/flockcue-a100-linux-20261110"
    args = dict(backend="transformers", linux_cpu=docs / "student-cpu.json", session_cpu=docs / "session-cpu.json",
                backend_lock=docs / "requirements-linux-cu128.lock", lease=quote, student_python=sys.executable)
    with pytest.raises(ValueError, match="did not pass"):
        admit_t37(run, **args)
    assert not (run / "t37/admission.json").exists()
    assert paired_batch._reserve(run, {"lease": json.loads(quote.read_bytes())}, 10) == "admitted"
    verdict.write_bytes(json_bytes({"passed": True, "measurement_sha256": digest(measured)}))
    admitted = admit_t37(run, **args)
    assert read_admission(run, sha, "transformers")[1] == admitted["admission_sha256"]
    assert admit_t37(run, **args) == admitted
    raw_path = upload / "0.json"
    saved = raw_path.read_bytes()
    raw_path.write_text("{}")
    with pytest.raises(ValueError, match="Changed calibration record"):
        read_admission(run, sha, "transformers")
    raw_path.write_bytes(saved)
    (directory / "metadata-Qwen3.5-2B.json").write_text("{}")
    with pytest.raises(ValueError, match="digest"):
        read_admission(run, sha, "transformers")


def test_report_bound_failed_t37_backends(tmp_path):
    from thinking_events.paired_actual_report import _unstarted_calibration

    sha = "c"*64
    results, reason = _unstarted_calibration(tmp_path, sha, "cpu_ready")
    assert not results and reason == "cpu_ready"
    for backend in ("transformers", "vllm"):
        folder = tmp_path / "t37" / backend
        folder.mkdir(parents=True)
        measurements = json_bytes({"backend": backend, "campaign_sha256": sha})
        (folder / "measurements.json").write_bytes(measurements)
        (folder / "verdict.json").write_bytes(json_bytes({"passed": False, "reasons": {"4B": "P95 indeterminate"},
                                                          "measurement_sha256": digest(measurements)}))
    results, reason = _unstarted_calibration(tmp_path, sha, "cpu_ready")
    assert reason == "t37_no_qualified_backend" and all(not info["passed"] for info in results.values())
    (folder / "measurements.json").write_text("{}")
    with pytest.raises(ValueError, match="binding"):
        _unstarted_calibration(tmp_path, sha, "cpu_ready")
