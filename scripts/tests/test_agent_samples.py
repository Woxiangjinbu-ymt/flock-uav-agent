"""No provider access: wire fixtures and immutable development archives only."""
import base64
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import convert_agent_samples as converter


def encoded(value):
    raw = converter.canonical(value)
    return {"base64": base64.b64encode(raw).decode(), "length": len(raw), "sha256": converter.sha(raw)}


def archive(*, parallel=False, failed=False, suffix="", reasoning=True):
    rows = []
    def add(kind, data):
        rows.append({"schema_version": 1, "sequence": len(rows) + 1, "archive_id": "archive-a",
                     "episode_id": "episode-a", "command_id": "command-a", "kind": kind, "data": data})
    add("archive_start", {})
    calls = [{"id": "call-a", "type": "function", "index": 0,
              "function": {"name": "move_to", "arguments": '{"input_json":"{\\"x\\":1}","thinking":"business-value"}'}}]
    if parallel:
        calls.append({"id": "call-b", "type": "function", "index": 1,
                      "function": {"name": "run_harness", "arguments": '{"action":"start"}'}})
    target = {"role": "assistant", "content": None, "tool_calls": calls}
    if reasoning:
        target["reasoning_content"] = "SECRET-REASONING"
    tools = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
             for name in ("move_to", "run_harness")]
    def model(index, messages, response):
        logical, attempt = f"model-{index}", f"attempt-{index}"
        add("model_start", {"logical_call_id": logical, "input": {"messages": "NOT THE WIRE"}})
        add("http_request", {"logical_call_id": logical, "attempt_id": attempt,
            "body_scope": "serialized_http_request_content", "error_type": None,
            "body": encoded({"model": "fixture", "stream": False, "messages": messages, "tools": tools})})
        add("http_response_start", {"logical_call_id": logical, "attempt_id": attempt,
            "status_code": 200, "body_scope": "buffered_httpx_content", "content_encoding": "gzip"})
        body = encoded({"id": f"response-{index}", "choices": [{"index": 0, "message": response,
                       "finish_reason": "tool_calls" if response.get("tool_calls") else "stop"}]})
        add("http_response_chunk", {"logical_call_id": logical, "attempt_id": attempt, "chunk_index": 1, "body": body})
        add("http_response_end", {"logical_call_id": logical, "attempt_id": attempt, "complete": True,
            "status": "complete", "error_type": None, "response_observed": True,
            "length": body["length"], "sha256": body["sha256"], "chunk_count": 1})
        add("model_end", {"logical_call_id": logical, "status": "returned", "error_type": None,
            "output": {"structured_response": None, "result": [{"type": "ai", "content": response.get("content") or "",
                "additional_kwargs": {"tool_calls": response.get("tool_calls", [])}, "tool_calls": [], "invalid_tool_calls": []}]}})
    initial = [{"role": "system", "content": "Public tools only"}, {"role": "user", "content": "Move Drone1"}]
    model(1, initial, target)
    for call in calls:
        add("tool_start", {"tool_span_id": "span-" + call["id"], "raw_tool_call_id": call["id"],
            "source_model_id": "model-1", "source_relation": "unique", "source_candidates": ["model-1"],
            "input": {"id": call["id"], "name": call["function"]["name"], "args": json.loads(call["function"]["arguments"])}})
    receipts = []
    for call in reversed(calls):
        receipt = {"status": "error" if failed and call["id"] == "call-a" else "success",
                   "error_budget": 0, "reasoning_content": "BUSINESS-RECEIPT"}
        content = json.dumps(receipt)
        add("tool_end", {"tool_span_id": "span-" + call["id"], "status": "returned", "error_type": None,
            "tool_call_id_mismatch": False, "output": {"type": "tool", "tool_call_id": call["id"],
                "name": call["function"]["name"], "content": content, "status": "success"}})
        receipts.append({"role": "tool", "tool_call_id": call["id"], "name": call["function"]["name"],
                         "content": content + (suffix if call["id"] == "call-a" else "")})
    model(2, initial + [target] + receipts, {"role": "assistant", "content": "Completed"})
    add("archive_end", {"complete": True, "reasons": [], "error_types": []})
    return rows


def raw(rows):
    rows = copy.deepcopy(rows)
    for index, row in enumerate(rows, 1):
        row["sequence"] = index
    return b"".join(converter.canonical(row) + b"\n" for row in rows)


def event(rows, kind, index=0):
    return [row["data"] for row in rows if row["kind"] == kind][index]


def request_edit(rows, index, mutate):
    request = event(rows, "http_request", index)
    body = converter.parse(converter.decode(request["body"]))
    mutate(body)
    request["body"] = encoded(body)


def response_edit(rows, index, mutate):
    chunk = event(rows, "http_response_chunk", index)
    body = converter.parse(converter.decode(chunk["body"]))
    mutate(body)
    chunk["body"] = encoded(body)
    end = event(rows, "http_response_end", index)
    end.update({key: chunk["body"][key] for key in ("length", "sha256")})


def sample(models, owner, index=0):
    return converter._sample(models[index], owner, {"run_manifest_sha256": "a" * 64, "trial_id": "trial-a",
        "archive_sha256": "b" * 64, "run": "run-a", "task_id": "task-a", "split": "development"})


def test_actual_wire_only_and_last_assistant_masked():
    owner, models = converter.read_archive(raw(archive(parallel=True)))
    current = sample(models, owner, 1)
    assert current["messages"][0]["content"] == "Public tools only"
    assert "NOT THE WIRE" not in json.dumps(current)
    assert current["supervision"]["message_loss_mask"] == [0] * 5 + [1]
    assert current["supervision"]["target_message_index"] == 5
    assert current["messages"][-1] == {"role": "assistant", "content": "Completed"}
    assert current["teacher_eligible"] is False
    assert {proof["tool_call_id"] for proof in current["audit"]["history_receipt_proofs"]} == {"call-a", "call-b"}


def test_reasoning_is_hash_only_business_fields_are_preserved():
    owner, models = converter.read_archive(raw(archive()))
    current = sample(models, owner, 1)
    rendered = json.dumps(current)
    assert "SECRET-REASONING" not in rendered
    assert "BUSINESS-RECEIPT" in rendered and "business-value" in rendered
    assert current["audit"]["removed_reasoning"][0]["characters"] == len("SECRET-REASONING")
    assert current["audit"]["removed_reasoning"][0]["sha256"] == converter.sha(converter.canonical("SECRET-REASONING"))
    target = sample(models, owner)["messages"][-1]
    assert target["content"] is None and "index" not in target["tool_calls"][0]


def test_parallel_tool_order_matches_ids_and_retains_run_harness():
    owner, models = converter.read_archive(raw(archive(parallel=True)))
    target = sample(models, owner)
    assert [link["tool_call_id"] for link in target["audit"]["tool_links"]] == ["call-a", "call-b"]
    assert [c["function"]["name"] for c in target["messages"][-1]["tool_calls"]] == ["move_to", "run_harness"]
    assert len(target["messages"][-1]["tool_calls"]) == 2  # No internal program actions invented.


@pytest.mark.parametrize("kind,field,value,reason", [
    ("tool_start", "source_relation", "ambiguous", "ambiguous_tool_source"),
    ("tool_start", "source_model_id", "model-2", "ambiguous_tool_source"),
    ("tool_end", "status", "error", "tool_span_not_complete"),
    ("tool_end", "tool_call_id_mismatch", True, "tool_span_not_complete"),
    ("http_response_start", "status_code", 500, "response_not_unique_success"),
    ("http_response_end", "complete", False, "response_incomplete"),
    ("http_response_chunk", "chunk_index", 2, "invalid_response_chunk_order"),
    ("archive_end", "complete", False, "archive_incomplete"),
])
def test_invalid_wire_and_ownership_rejected(kind, field, value, reason):
    rows = archive()
    event(rows, kind)[field] = value
    with pytest.raises(converter.Excluded, match=reason):
        converter.read_archive(raw(rows))


@pytest.mark.parametrize("change,reason", [
    (lambda rows: event(rows, "tool_start")["input"].update(args={}), "tool_wire_arguments_mismatch"),
    (lambda rows: event(rows, "tool_end")["output"].update(tool_call_id="unknown"), "tool_receipt_identity_mismatch"),
    (lambda rows: event(rows, "model_end")["output"].update(structured_response={"x": 1}), "unsupported_structured_response"),
    (lambda rows: event(rows, "model_end")["output"]["result"][0].update(content="different"), "wire_handler_output_mismatch"),
    (lambda rows: event(rows, "http_response_chunk")["body"].update(sha256="0" * 64), "wire_bytes_hash_mismatch"),
    (lambda rows: rows[1].update(command_id="another"), "mixed_archive_ownership"),
])
def test_wire_model_and_tool_consistency(change, reason):
    rows = archive()
    change(rows)
    with pytest.raises(converter.Excluded, match=reason):
        converter.read_archive(raw(rows))


def test_missing_receipt_and_duplicate_execution_rejected():
    rows = archive()
    with pytest.raises(converter.Excluded, match="tool_call_missing_receipt"):
        converter.read_archive(raw([row for row in rows if row["kind"] not in {"tool_start", "tool_end"}]))
    pair = copy.deepcopy([row for row in rows if row["kind"] in {"tool_start", "tool_end"}])
    for row in pair:
        row["data"]["tool_span_id"] = "second-span"
    index = next(i for i, row in enumerate(rows) if row["kind"] == "model_start" and row["data"]["logical_call_id"] == "model-2")
    rows[index:index] = pair
    with pytest.raises(converter.Excluded, match="tool_call_executed_twice"):
        converter.read_archive(raw(rows))


def test_multiple_attempts_never_select_last_response():
    rows = archive()
    duplicate = copy.deepcopy([row for row in rows if row["kind"].startswith("http_")][:4])
    for row in duplicate:
        row["data"]["attempt_id"] = "attempt-retry"
    index = next(i for i, row in enumerate(rows) if row["kind"] == "model_end")
    rows[index:index] = duplicate
    with pytest.raises(converter.Excluded, match="multiple_or_missing_attempts"):
        converter.read_archive(raw(rows))


def test_unadvertised_tools_and_unproven_history_rejected():
    rows = archive()
    request_edit(rows, 0, lambda body: body.update(tools=[]))
    with pytest.raises(converter.Excluded, match="duplicate_or_unadvertised_tool_call"):
        converter.read_archive(raw(rows))
    rows = archive()
    request_edit(rows, 1, lambda body: body["messages"][-1].update(content="fabricated receipt"))
    with pytest.raises(converter.Excluded, match="history_tool_content_mismatch"):
        converter.read_archive(raw(rows))


def test_advisory_requires_exact_sealed_injection_and_unique_id():
    suffix = converter.MARKER + "Public ledger observation"
    rows = archive(suffix=suffix)
    injection = {"tool_call_id": "call-a", "text": suffix, "message_index": 3, "segment": 1}
    _, models = converter.read_archive(raw(rows), injections=[injection])
    assert models[1]["history_receipt_proofs"][0]["transformation"] == "sealed_public_evidence_suffix"
    for injections in ([], [{**injection, "tool_call_id": "another"}], [injection, injection]):
        with pytest.raises(converter.Excluded, match="unproven_public_advisory"):
            converter.read_archive(raw(rows), injections=injections)
    with pytest.raises(converter.Excluded, match="unproven_public_advisory"):
        converter.read_archive(raw(archive(suffix="arbitrary appendix")), injections=[{"tool_call_id": "call-a", "text": "arbitrary appendix"}])


@pytest.mark.parametrize("content,reason", [
    ("<think>hidden</think>answer", "reasoning_tags"),
    ([{"type": "text", "text": "<reasoning>hidden</reasoning>"}], "reasoning_tags"),
    ([{"type": "text", "text": "answer", "reasoning_content": "hidden"}], "unsupported_content_block"),
    ([{"type": "image_url", "image_url": "data:image/png;base64,x"}], "unsupported_content_block"),
])
def test_unknown_content_and_embedded_reasoning_fail_closed(content, reason):
    with pytest.raises(converter.Excluded, match=reason):
        converter.clean_message({"role": "assistant", "content": content}, "test", [])


def test_known_reasoning_blocks_removed_with_audit():
    removed = []
    cleaned = converter.clean_message({"role": "assistant", "content": [{"type": "thinking", "text": "hidden"},
              {"type": "text", "text": "answer"}]}, "response", removed)
    assert cleaned["content"] == [{"type": "text", "text": "answer"}]
    assert len(removed) == 1 and "hidden" not in json.dumps(removed)


@pytest.mark.parametrize("payload", [{"status": "partial"}, {"status": "unknown"}, {"status": "validation_error"}, {"success": False},
    {"navigation_result": {"status": "error"}}, {"move_result": {"outcome": "blocked"}}])
def test_explicit_tool_outcomes_exclude_current_decision(payload):
    rows = archive()
    event(rows, "tool_end")["output"]["content"] = json.dumps(payload)
    request_edit(rows, 1, lambda body: body["messages"][-1].update(content=json.dumps(payload)))
    _, models = converter.read_archive(raw(rows))
    assert models[0]["tool_spans"][0]["feedback"]["status"] == "excluded_target"
    assert not models[1]["tool_spans"]


@pytest.mark.parametrize("status", [None, "unknown", "partial", "returned", "validation_error"])
def test_tool_message_transport_status_must_be_known(status):
    rows = archive()
    event(rows, "tool_end")["output"]["status"] = status
    with pytest.raises(converter.Excluded, match="unsupported_tool_message_status"):
        converter.read_archive(raw(rows))


def test_tool_message_error_excludes_only_current_target():
    rows = archive()
    event(rows, "tool_end")["output"]["status"] = "error"
    _, models = converter.read_archive(raw(rows))
    assert models[0]["tool_spans"][0]["feedback"]["reasons"] == ["tool_message_error"]
    assert not models[1]["tool_spans"]


def test_error_word_is_not_a_failure_and_ledger_unknown_is():
    rows = archive()
    _, models = converter.read_archive(raw(rows))
    assert models[0]["tool_spans"][0]["feedback"]["status"] == "returned"
    _, models = converter.read_archive(raw(rows), ledger_events=[{"tool_call_id": "call-a", "status": "unknown"}])
    assert models[0]["tool_spans"][0]["feedback"]["reasons"] == ["public_ledger_unknown"]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(converter.canonical(value) + b"\n")


def fake_run(tmp_path, *, failed=False, parallel=False):
    run = tmp_path / "run"
    folder = run / "trials/trial-a"
    modelio = folder / "policy/modelio/archive.jsonl"
    modelio.parent.mkdir(parents=True)
    modelio.write_bytes(raw(archive(failed=failed, parallel=parallel)))
    snapshot = folder / "private/score-world.pkl"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"opaque-private-snapshot-never-unpickled")
    snapshot_sha = converter.sha(snapshot.read_bytes())
    write_json(folder / "policy/task-state.json", {"command": "Move Drone1", "task_evidence": {"injections": []}, "events": []})
    worker = {"status": "completed", "trial_id": "trial-a", "task_id": "task-a", "pid": 101,
        "drained": True, "limits": {"threads_still_alive": [], "needs_process_termination": False, "stop_reason": None},
        "agent": {"success": True, "error_type": None, "archive_verify": {"valid": True, "complete": True}, "archive_info": {"archive_id": "archive-a"}},
        "snapshot": {"path": "private/score-world.pkl", "sha256": snapshot_sha}, "isolation": {"canary_absent": True, "checker_definitions_absent": True},
        "entry": {"command_id": "command-a"}}
    write_json(folder / "worker-result.json", worker)
    score = {"trial_id": "trial-a", "worker_pid": 101, "pid": 102,
        "worker_result_sha256": converter.sha((folder / "worker-result.json").read_bytes()), "snapshot_sha256": snapshot_sha,
        "checker_tree_matches_original": True, "agent_imported": False, "network_attempts": [],
        "checker": {"passed": True, "checks_expected": 1, "checks_passed": 1, "errors": []}}
    write_json(folder / "evaluation/result.json", score)
    for group, names in (("worker", ["worker-result.json", "policy/task-state.json", "policy/modelio/archive.jsonl", "private/score-world.pkl"]),
                         ("scorer", ["evaluation/result.json"])):
        write_json(run / (group + "-exit-seals/trial-a.json"), {name: converter.sha((folder / name).read_bytes()) for name in names})
    write_json(run / "inputs/fixtures/task-a.json", {"command": "Move Drone1", "scene_id": "scene-a"})
    write_json(run / "manifest.json", {"scope": "general_agent_v2_execution_contract", "online_authorized_executions": 0,
        "trials": [{"trial_id": "trial-a", "task_id": "task-a"}, {"trial_id": "unrun", "task_id": "task-b"}]})
    write_json(run / "results.json", [{"trial_id": "trial-a", "processes": [
        {"mode": mode, "pid": pid, "trial_id": "trial-a", "exited": True, "returncode": 0, "kill_reason": None}
        for mode, pid in (("worker", 101), ("score", 102))]}])
    seal = {str(file.relative_to(run)): converter.sha(file.read_bytes()) for file in run.rglob("*") if file.is_file()}
    write_json(run / "campaign-seal.json", {"files": seal})
    planned = {"run": "run", "manifest_sha256": converter.sha((run / "manifest.json").read_bytes()),
        "campaign_seal_sha256": converter.sha((run / "campaign-seal.json").read_bytes()), "planned_trials": 2,
        "purpose": "development_format_and_input_validation_only"}
    return run, planned


def test_failed_parallel_decision_excluded_whole_and_recovery_kept(tmp_path):
    run, planned = fake_run(tmp_path, failed=True, parallel=True)
    result = converter.convert_run(run, planned)
    assert [e["status"] for e in result["episodes"]] == ["eligible", "unrun"]
    assert len(result["samples"]) == 1
    assert [d["status"] for d in result["decisions"]] == ["excluded", "exported"]
    assert len(result["decisions"][0]["tool_feedback"]) == 2
    assert result["samples"][0]["source"]["logical_call_id"] == "model-2"
    assert result["samples"][0]["supervision"]["message_loss_mask"] == [0] * 5 + [1]
    assert '"status": "error"' in result["samples"][0]["messages"][-2]["content"]


def test_sealed_source_mutation_rejected(tmp_path):
    run, planned = fake_run(tmp_path)
    (run / "trials/trial-a/policy/modelio/archive.jsonl").write_bytes(b"tampered\n")
    with pytest.raises(converter.Excluded, match="source_hash_mismatch"):
        converter.convert_run(run, planned)


def test_export_seals_complete_inventory_and_never_overwrites(tmp_path):
    _, planned = fake_run(tmp_path, failed=True)
    plan = {"schema_version": 1, "historical_source_role": "development", "provider_calls_authorized": 0,
            "teacher_training_examples_expected": 0, "planned_trials": 2, "runs": [planned]}
    plan_path = tmp_path / "experiments/agent-samples-v1/source-plan.json"
    write_json(plan_path, plan)
    plan_path.with_suffix(".sha256").write_text(converter.sha(plan_path.read_bytes()) + "\n")
    protocol = tmp_path / "docs/AGENT_SAMPLE_CONTRACT_CN.md"
    protocol.parent.mkdir()
    protocol.write_text("Fixture protocol: development only\n")
    output = tmp_path / "export"
    manifest = converter.export_dataset(plan_path, output, workspace=tmp_path)
    assert manifest["counts"] == {"planned_episodes": 2, "eligible_episodes": 1, "excluded_episodes": 0,
        "unrun_episodes": 1, "logical_decisions": 2, "exported_samples": 1, "excluded_decisions": 1}
    assert manifest["true_teacher_samples"] == 0 and manifest["purpose"] == "development"
    assert converter.sha((output / "manifest.json").read_bytes()) == (output / "manifest.sha256").read_text().strip()
    converter.verify_hashes(output, manifest["files"])
    actual = {str(file.relative_to(output)) for file in output.rglob("*") if file.is_file()}
    assert actual == set(manifest["files"]) | {"manifest.json", "manifest.sha256"}
    assert not any("SECRET-REASONING" in (output / name).read_text() for name in manifest["files"])
    with pytest.raises(converter.Excluded, match="output_exists"):
        converter.export_dataset(plan_path, output, workspace=tmp_path)


def test_export_rechecks_source_payload_before_write(tmp_path, monkeypatch):
    run, planned = fake_run(tmp_path)
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, {"schema_version": 1, "historical_source_role": "development", "provider_calls_authorized": 0,
        "teacher_training_examples_expected": 0, "planned_trials": 2, "runs": [planned]})
    plan_path.with_suffix(".sha256").write_text(converter.sha(plan_path.read_bytes()))
    protocol = tmp_path / "docs/AGENT_SAMPLE_CONTRACT_CN.md"
    protocol.parent.mkdir(); protocol.write_text("Fixture")
    original = converter.convert_run
    def mutated_after_read(*args):
        result = original(*args)
        (run / "trials/trial-a/policy/task-state.json").write_bytes(b"{}\n")
        return result
    monkeypatch.setattr(converter, "convert_run", mutated_after_read)
    output = tmp_path / "export"
    with pytest.raises(converter.Excluded, match="source_hash_mismatch"):
        converter.export_dataset(plan_path, output, workspace=tmp_path)
    assert not output.exists()


@pytest.mark.parametrize("worker_change,score_change,expected", [
    ({"drained": False}, {}, "worker_not_drained"),
    ({"limits": {"stop_reason": "token_budget"}}, {}, "episode_not_normal_return"),
    ({"limits": {"unknown_usage_attempts": 1}}, {}, "unknown_provider_usage"),
    ({"limits": {"unknown_usage_attempt_ids": ["attempt-hidden"]}}, {}, "unknown_provider_usage"),
    ({}, {"worker_pid": 999}, "scorer_not_independent"),
    ({}, {"checker": {"passed": False}}, "task_not_independently_passed"),
    ({}, {"agent_imported": True}, "invalid_independent_scorer"),
])
def test_episode_gates_refuse_ineligible_records(tmp_path, worker_change, score_change, expected):
    run, planned = fake_run(tmp_path)
    trial = converter.read(run, "manifest.json")["trials"][0]
    result = converter.read(run, "results.json")[0]
    folder = run / "trials/trial-a"
    worker = converter.read(folder, "worker-result.json")
    score = converter.read(folder, "evaluation/result.json")
    worker.update(worker_change); score.update(score_change)
    with pytest.raises(converter.Excluded, match=expected):
        converter._episode_gate(run, trial, result, worker, score)


def test_future_training_metadata_uses_actual_bytes_and_never_enables_training():
    manifest = {"phase": "formal", "generator_version": "task-coverage-splits-v1", "tasks": [
        {"task_id": "task", "scene_id": "scene", "command_sha256": "d" * 64, "split": "train"}]}
    raw_manifest = json.dumps(manifest, indent=2).encode()
    record = {"task_id": "task", "scene_id": "scene", "command_sha256": "d" * 64, "split": "train", "source_kind": "real_provider",
              "initial_state_sha256": "e" * 64, "archive_sha256": "f" * 64, "dataset_manifest_sha256": converter.sha(raw_manifest)}
    review = {"status": "approved", **{key: record[key] for key in ("task_id", "initial_state_sha256", "archive_sha256", "dataset_manifest_sha256")}}
    assert converter.validate_future_training_identity(record, raw_manifest, review) == {"metadata_checks_passed": True, "production_training_export_supported": False}
    for changes, reason in (({"split": "test"}, "training_requires_train_split"), ({"source_kind": "synthetic"}, "training_requires_real_provider"),
                            ({"initial_state_sha256": "z" * 64}, "training_initial_state_missing")):
        with pytest.raises(converter.Excluded, match=reason):
            converter.validate_future_training_identity({**record, **changes}, raw_manifest, review)
    with pytest.raises(converter.Excluded, match="training_quality_review"):
        converter.validate_future_training_identity(record, converter.canonical(manifest), review)
    with pytest.raises(converter.Excluded, match="production_training_export_not_implemented"):
        converter.export_dataset("unused", "unused", purpose="training")


def test_fixed_source_plan_preserves_all_22_slots():
    plan = converter.read(converter.ROOT, "experiments/agent-samples-v1/source-plan.json")
    assert tuple(row["run"] for row in plan["runs"]) == converter.RUNS
    results = [converter.convert_run(converter.ROOT / row["run"], row) for row in plan["runs"]]
    episodes = [episode for result in results for episode in result["episodes"]]
    assert len(episodes) == plan["planned_trials"] == 22
    assert sum(row["status"] == "unrun" for row in episodes) == 2
    assert sum(row["status"] == "eligible" for row in episodes) == 12
    samples = [sample for result in results for sample in result["samples"]]
    assert all(not sample["teacher_eligible"] and sample["source"]["split"] == "development" for sample in samples)
    real = [result for result in results if result["source"]["source_kind"] == "real_provider_development"][0]
    nav = [decision for decision in real["decisions"] if decision["trial_id"] == "b4714028-integrated"]
    assert nav[0]["reason"] == "current_decision_has_failed_partial_or_unknown_tool"
    assert len(nav[0]["tool_feedback"]) == 3 and nav[1]["status"] == "exported"
