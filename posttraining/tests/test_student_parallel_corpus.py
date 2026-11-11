"""Prompt corpus extraction never promotes failed historical episodes to labels."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import student_parallel_corpus as corpus


def wire_body(value):
    raw = corpus.wire.canonical(value)
    return {"base64": base64.b64encode(raw).decode(), "length": len(raw), "sha256": corpus.wire.sha(raw)}


def archive_rows():
    body = {"model": "synthetic-fixture", "messages": [
        {"role": "system", "content": "Public fixture system"}, {"role": "user", "content": "Fixture task"},
        {"role": "assistant", "content": None, "reasoning_content": "PRIVATE-PROVIDER-REASONING",
         "tool_calls": [{"id": "call-one", "index": 0, "type": "function", "function": {
             "name": "fixture_tool", "arguments": '{"thinking":"business-argument"}'}}]},
        {"role": "tool", "tool_call_id": "call-one", "name": "fixture_tool",
         "content": '{"status":"error","reasoning_content":"business-receipt"}'}],
        "tools": [{"type": "function", "function": {"name": "fixture_tool", "parameters": {"type": "object",
                   "properties": {"thinking": {"type": "string"}}}}}]}
    data = [("archive_start", {}), ("model_start", {"logical_call_id": "model-1", "input": "NOT_ACTUAL_WIRE"}),
        ("http_request", {"logical_call_id": "model-1", "attempt_id": "attempt-1", "body_scope": "serialized_http_request_content",
                          "error_type": None, "body": wire_body(body)}),
        ("model_end", {"logical_call_id": "model-1", "status": "partial", "output": "NEVER_EXPORT_TARGET"}),
        ("archive_end", {"complete": False, "reason": "budget_stop"})]
    return [{"schema_version": 1, "sequence": i, "archive_id": "archive-1", "episode_id": "episode-1",
             "command_id": "command-1", "kind": kind, "data": payload} for i, (kind, payload) in enumerate(data, 1)]


def serialized(rows):
    rows = deepcopy(rows)
    for sequence, row in enumerate(rows, 1):
        row["sequence"] = sequence
    return b"".join(corpus.wire.canonical(row) + b"\n" for row in rows)


SOURCE = {"run_manifest_sha256": "a" * 64, "run": "synthetic-fixture", "trial_id": "synthetic-trial", "task_id": "synthetic-task"}


@pytest.fixture(scope="module")
def requests():
    return corpus.load_requests()


@pytest.fixture(scope="module")
def samples():
    return corpus.load_samples()


def test_partial_failed_episode_is_prompt_only_without_target():
    requests, metadata = corpus.extract_archive_requests(serialized(archive_rows()), SOURCE)
    assert metadata == {"logical_calls": 1, "http_request_records": 1, "logical_calls_without_http_request": [], "archive_closed": True}
    request = requests[0]
    assert request["teacher_eligible"] is False
    assert not {"target", "labels", "supervision"} & set(request)
    text = json.dumps(request)
    assert "NEVER_EXPORT_TARGET" not in text and "NOT_ACTUAL_WIRE" not in text
    assert "PRIVATE-PROVIDER-REASONING" not in text
    assert "business-argument" in text and "business-receipt" in text
    assert request["audit"]["removed_reasoning"][0]["characters"] == len("PRIVATE-PROVIDER-REASONING")
    assert request["source"]["split"] == "development"
    assert request["audit"]["serialized_request_does_not_prove_provider_dispatch"] is True


def test_diagnostic_preserves_multiple_captured_attempts_without_selecting_response():
    rows = archive_rows()
    retry = deepcopy(rows[2]); retry["data"]["attempt_id"] = "attempt-2"
    rows.insert(3, retry)
    requests, metadata = corpus.extract_archive_requests(serialized(rows), SOURCE)
    assert len(requests) == 2 and metadata["logical_calls"] == 1
    assert len({request["request_id"] for request in requests}) == 2
    assert {request["source"]["attempt_id"] for request in requests} == {"attempt-1", "attempt-2"}
    assert all(not request["audit"]["response_used"] for request in requests)


@pytest.mark.parametrize("mutate,reason", [
    (lambda rows: rows[2]["data"]["body"].update(sha256="0" * 64), "wire_bytes_hash_mismatch"),
    (lambda rows: rows[2]["data"]["body"].update(length=0), "wire_bytes_hash_mismatch"),
    (lambda rows: rows[2]["data"].update(logical_call_id="absent"), "diagnostic_request_association"),
    (lambda rows: rows[2]["data"].update(body_scope="outer_middleware_input"), "diagnostic_request_not_wire"),
    (lambda rows: rows[2].update(command_id="another"), "diagnostic_archive_ownership"),
    (lambda rows: rows[2].update(schema_version=2), "diagnostic_archive_sequence"),
])
def test_corrupted_wire_or_association_is_rejected(mutate, reason):
    rows = archive_rows(); mutate(rows)
    with pytest.raises(corpus.wire.Excluded, match=reason):
        corpus.extract_archive_requests(serialized(rows), SOURCE)


def test_duplicate_attempt_and_post_close_append_rejected():
    rows = archive_rows(); rows.insert(3, deepcopy(rows[2]))
    with pytest.raises(corpus.wire.Excluded, match="diagnostic_request_association"):
        corpus.extract_archive_requests(serialized(rows), SOURCE)
    rows = archive_rows(); rows.append(deepcopy(rows[2]))
    with pytest.raises(corpus.wire.Excluded, match="diagnostic_archive_sequence"):
        corpus.extract_archive_requests(serialized(rows), SOURCE)


def test_fixed_98_prompts_include_old_failure_without_becoming_samples(requests, samples):
    assert len(requests) == 98 and len({row["request_id"] for row in requests}) == 98
    assert all(row["purpose"] == "prompt_only_template_diagnostic" and not row["teacher_eligible"] for row in requests)
    assert all(not {"target", "labels", "supervision"} & set(row) for row in requests)
    old_search = [row for row in requests if row["source"]["trial_id"] == "7eba7390-baseline"]
    assert len(old_search) == 13
    assert not any(row["source"]["trial_id"] == "7eba7390-baseline" for row in samples)
    assert len(samples) == 61 and len({row["sample_id"] for row in samples}) == 61
    assert all(row["source"]["split"] == "development" and not row["teacher_eligible"] for row in samples)


def test_samples_are_exact_original_conversion_rows(samples):
    original = [json.loads(line) for line in (corpus.CONVERSION / "samples.jsonl").read_text().splitlines()]
    assert samples == original


def test_actual_same_name_groups_are_distinct_uavs_with_ordered_wire_receipts(requests):
    report = corpus.summarize_parallel_requests(requests)
    assert report["same_name_request_count"] == 10
    assert report["unique_same_name_groups"] == 6
    assert report["reordered_request_count"] == report["unique_reordered_groups"] == 0
    same_name = [row for row in report["groups"] if row["same_name_parallel"]]
    assert {row["trial_id"] for row in same_name} == {"7eba7390-baseline"}
    assert len({row["group_id"] for row in same_name}) == 6
    assert sum(row["names"][0] == "take_off" for row in same_name) == 1
    assert sum(row["names"][0] == "navigate_to_and_sense" for row in same_name) == 5
    for row in same_name:
        assert len(set(row["call_ids"])) == 5 and row["receipt_order_matches_calls"]
        args = [json.loads(call["arguments"]["input_json"]) for call in row["calls"]]
        assert len({item["drone_id"] for item in args}) == 5
        if row["names"][0] == "take_off":
            assert {item["altitude"] for item in args} == {10.0}
        else:
            assert len({(item["x"], item["y"], item["z"]) for item in args}) == 5


def test_source_plan_needs_external_anchor_even_if_self_seal_is_recomputed(tmp_path):
    plan = tmp_path / "source-plan.json"
    value = json.loads(corpus.SOURCE_PLAN.read_text()); value["date"] = "self-resealed"
    plan.write_bytes(corpus.wire.canonical(value))
    plan.with_suffix(".sha256").write_text(corpus.wire.sha(plan.read_bytes()))
    with pytest.raises(corpus.wire.Excluded, match="diagnostic_source_plan_anchor"):
        corpus.load_requests(plan)


def test_source_plan_drift_during_extraction_is_rejected(tmp_path, monkeypatch):
    plan = tmp_path / "source-plan.json"
    plan.write_bytes(corpus.SOURCE_PLAN.read_bytes())
    plan.with_suffix(".sha256").write_text(corpus.SOURCE_PLAN_SHA256)
    original = corpus.extract_archive_requests
    changed = False
    def extracting(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            plan.write_bytes(plan.read_bytes() + b" ")
            changed = True
        return result
    monkeypatch.setattr(corpus, "extract_archive_requests", extracting)
    with pytest.raises(corpus.wire.Excluded, match="diagnostic_source_plan_anchor"):
        corpus.load_requests(plan)


def test_conversion_manifest_needs_external_anchor_even_after_self_reseal(tmp_path):
    package = tmp_path / "package"; package.mkdir()
    value = json.loads((corpus.CONVERSION / "manifest.json").read_text()); value["counts"]["exported_samples"] = 62
    raw = corpus.wire.canonical(value)
    (package / "manifest.json").write_bytes(raw)
    (package / "manifest.sha256").write_text(corpus.wire.sha(raw))
    with pytest.raises(corpus.wire.Excluded, match="diagnostic_conversion_anchor"):
        corpus.load_samples(package)


@pytest.mark.parametrize("mutation,reason", [("payload", "source_hash_mismatch"), ("extra", "diagnostic_conversion_inventory"), ("symlink", "source_symlink")])
def test_conversion_payload_inventory_and_symlinks_are_checked(tmp_path, mutation, reason):
    package = tmp_path / "package"
    shutil.copytree(corpus.CONVERSION, package)
    if mutation == "payload":
        (package / "samples.jsonl").write_bytes(b"{}\n")
    elif mutation == "extra":
        (package / "unsealed.json").write_bytes(b"{}\n")
    else:
        target = tmp_path / "empty"; target.mkdir()
        (package / "unsealed-symlink").symlink_to(target, target_is_directory=True)
    with pytest.raises(corpus.wire.Excluded, match=reason):
        corpus.load_samples(package)


def test_synthetic_cases_are_labelled_invented_and_independent():
    cases = corpus.synthetic_cases()
    assert len(cases) == 12 and sum(case["expected_valid"] for case in cases) == 4
    assert all(case["origin"] == "synthetic_unit_fixture" and case["is_real_model_evidence"] is False
               and case["teacher_eligible"] is False for case in cases)
    by_id = {case["case_id"]: case for case in cases}
    reverse = by_id["same_name_reversed_receipts"]
    assert [row["tool_call_id"] for row in reverse["messages"][-2:]] == ["synthetic-call-2", "synthetic-call-1"]
    same = by_id["same_name_same_args_distinct_ids"]["messages"][2]["tool_calls"]
    assert same[0]["id"] != same[1]["id"] and same[0]["function"] == same[1]["function"]
    cases[0]["messages"][0]["content"] = "mutated"
    assert corpus.synthetic_cases()[0]["messages"][0]["content"] != "mutated"
    assert cases[1]["messages"][0]["content"] != "mutated"


def test_summary_identifies_synthetic_reordered_wire_without_claiming_actual_evidence():
    case = next(case for case in corpus.synthetic_cases() if case["case_id"] == "same_name_reversed_receipts")
    row = {"request_id": "synthetic-request", "messages": case["messages"],
           "source": {"archive_sha256": "fixture-only", "run": "synthetic-fixture", "trial_id": "synthetic"}}
    report = corpus.summarize_parallel_requests([row])
    assert report["unique_reordered_groups"] == 1 and report["reordered_request_count"] == 1
    assert report["groups"][0]["run"] == "synthetic-fixture"
    assert report["teacher_examples"] == report["provider_calls"] == 0


def test_metadata_separates_prompts_from_samples_and_authorizes_no_execution():
    metadata = corpus.source_metadata()
    assert metadata["diagnostic_requests"] == 98 and metadata["original_development_samples"] == 61
    assert metadata["diagnostic_scope"] == "prompt_only_no_targets_no_loss_labels"
    assert metadata["provider_calls"] == metadata["teacher_examples"] == 0
    assert metadata["dependency_hashes"]["scripts/convert_agent_samples.py"] == corpus.wire.sha(Path(corpus.wire.__file__).read_bytes())
