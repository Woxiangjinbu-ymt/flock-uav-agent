"""Real tokenized SDK traffic, local UAV tools, and independent v5 scoring."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import student_input_v2 as v2
from thinking_events import worker
from thinking_events.worker_smoke import smoke


def read(path):
    return json.loads(path.read_bytes())


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    output = tmp_path_factory.mktemp("student-integration") / "probe"
    python = Path(os.environ.get("FLOCKCUE_TOKENIZER_PYTHON", ROOT / "training/.venv-input/bin/python"))
    summary = smoke(output, python)
    return output / "cpu-run", summary


def test_cpu_worker_and_independent_checker(finished):
    run, report = finished
    rows = report["rows"]
    assert report["formal_slots_started"] == report["gpu_hours"] == report["teacher_calls"] == 0
    assert [row["passed"] for row in rows] == [True, False, False]
    assert len({row[k] for row in rows for k in ("worker_pid", "scorer_pid")}) == 6
    assert all(row["initial"] == rows[0]["initial"] for row in rows)
    for row in rows:
        name = row["trial_id"]
        folder = run / "trials" / name
        policy = read(folder / "worker-result.json")
        check = read(folder / "evaluation/result.json")
        assert not policy["network_attempts"] and not check["network_attempts"]
        assert policy["drained"] and all(policy["isolation"].values())
        assert check["checker_tree_matches_original"] and check["private_canary_present"]
        assert not check["agent_imported"] and not check["checker"]["errors"]
        for role in ("worker", "scorer"):
            seal = run / f"{role}-exit-seals" / (name + ".json")
            assert hashlib.sha256(seal.read_bytes()).hexdigest() == row[f"{role}_seal_sha256"]
        for role in ("worker", "score"):
            imports = read(run / "process-logs" / f"{name}-{role}-modules.json")
            assert len(imports.values()) == len(set(imports.values()))
            assert all((run / "source" / relative).is_file() for relative in imports.values())
            assert imports["api.server"] == "server/api/server.py"
            assert ("uav_agent" in imports) == (role == "worker")
        assert (
            Path(row["student_metadata"]["source"])
            == run / "source/scripts/thinking_events/student_service.py"
        )
    assert report["verified"]["cpu-success"]["reversed_receipt_ids"] == ["call_3", "call_2"]
    assert report["verified"]["cpu-success"]["second_turn_replayed_reasoning"]
    assert [report["verified"][r["trial_id"]]["actions"] for r in rows] == [2, 0, 0]


def test_reconcile_ids_timing_and_late_usage(finished):
    run, report = finished
    for row in report["rows"]:
        folder = run / "trials" / row["trial_id"]
        records = list(
            map(json.loads, (folder / "private/student-requests.jsonl").read_bytes().splitlines())
        )
        usage = row["usage"]
        prompt = sum(len(r["input_ids"]) for r in records)
        produced = sum(len(r["generated_ids"]) for r in records)
        assert (usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]) == (
            prompt,
            produced,
            prompt + produced,
        )
        assert usage["requests"] == report["verified"][row["trial_id"]]["reproduced_token_sequences"]
        assert usage["peak_vram_bytes"] is None and usage["cost_cny"] == 0
        assert len(usage["request_timings"]) == len(records)
        for timing in usage["request_timings"]:
            assert timing["timing_source"] == "cpu_scripted_bulk"
            assert timing["round_trip_seconds"] >= timing["generation_seconds"] >= 0
            assert timing["ttft_seconds"] is timing["decode_tokens_per_second"] is None
            assert timing["unavailable"]["peak_vram_bytes"] == "cpu_fixture_no_gpu"
    success, malformed, cancel = report["rows"]
    assert success["usage"]["history_reasoning_tokens_by_request"] == [0, 4, 4, 4, 4, 4]
    assert malformed["stop_reason"] == "generated_json" and cancel["stop_reason"] == "cancelled"
    assert cancel["usage"]["unknown_at_cutoff_turns"] == [2]
    assert [r["turn"] for r in cancel["usage"]["late_usage"]] == [2]


@pytest.mark.parametrize(
    "fault,diagnostic",
    [
        ("missing_exit_seal", "FileNotFoundError"),
        ("changed_snapshot", "Artifact hash mismatch: private/score-world.pkl"),
        ("undrained", "Only a drained worker may be scored"),
    ],
)
def test_corrupted_or_unclosed_worker_cannot_be_scored(finished, tmp_path, fault, diagnostic):
    original, _ = finished
    run = tmp_path / "copy"
    run.mkdir()
    name = "cpu-success"
    for filename in ("manifest.json", "manifest.sha256"):
        shutil.copyfile(original / filename, run / filename)
    shutil.copytree(original / "source", run / "source")
    folder = run / "trials" / name
    shutil.copytree(original / "trials" / name, folder, ignore=shutil.ignore_patterns("evaluation"))
    (run / "process-logs").mkdir()
    seal_name = Path("worker-exit-seals") / (name + ".json")
    (run / seal_name).parent.mkdir()
    if fault != "missing_exit_seal":
        shutil.copyfile(original / seal_name, run / seal_name)
    if fault == "changed_snapshot":
        (folder / "private/score-world.pkl").write_bytes(b"wrong snapshot")
    if fault == "undrained":
        result = folder / "worker-result.json"
        result.write_text(json.dumps({**read(result), "drained": False}))
    manifest = read(run / "manifest.json")
    trial = next(row for row in manifest["trials"] if row["trial_id"] == name)
    process = worker._launch(run, manifest, trial, "score")
    assert process["exited"] and process["returncode"] != 0
    assert diagnostic in (run / "process-logs/cpu-success-score.log").read_text()
    assert not (folder / "evaluation").exists()


@pytest.mark.parametrize("changed", [{"revision": "wrong"}, {"model_manifest_sha256": "0" * 64}])
def test_real_service_matches_frozen_weights(tmp_path, changed):
    filename = "training/model-manifests/1.7B.json"
    file = tmp_path / "source" / filename
    file.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / filename, file)
    manifest = {"sources": {filename: hashlib.sha256(file.read_bytes()).hexdigest()}}
    weights = read(file)
    metadata = dict(
        backend="transformers",
        model="1.7B",
        weights_loaded=True,
        revision=weights["revision"],
        model_manifest_sha256=hashlib.sha256(v2.canonical(weights)).hexdigest(),
    )
    worker._check_student_identity(tmp_path, manifest, metadata, "1.7B", "transformers")
    with pytest.raises(ValueError, match="frozen model manifest"):
        worker._check_student_identity(tmp_path, manifest, {**metadata, **changed}, "1.7B", "transformers")


def test_scripted_service_cannot_use_the_formal_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        worker,
        "_manifest",
        lambda _: {"scope": "thinking_events_dev_local_backend", "trials": [{"trial_id": "formal-slot"}]},
    )
    with pytest.raises(ValueError, match="separate CPU fixture"):
        worker.run_slot(tmp_path, "formal-slot", model="1.7B", thinking=True)
    assert not list(tmp_path.iterdir())


def test_timeout_with_missing_usage_retains_known_partials(finished, tmp_path):
    run, _ = finished
    original = run / "trials/cpu-malformed"
    policy = read(original / "worker-result.json")
    records = list(map(json.loads, (original / "private/student-requests.jsonl").read_bytes().splitlines()))
    (tmp_path / "private").mkdir()
    records[1]["usage"] = None
    (tmp_path / "private/student-requests.jsonl").write_text("\n".join(map(json.dumps, records)))
    usage = worker._metrics(tmp_path, policy, "fixture")
    assert usage["total_tokens"] is usage["reasoning_tokens"] is usage["remaining_tokens"] is None
    assert usage["unknown_usage_turns"] == [2]
    assert usage["known_total_tokens"] == records[0]["usage"]["total_tokens"]
