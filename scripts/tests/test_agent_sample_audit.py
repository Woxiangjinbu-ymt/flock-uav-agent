"""Independent inert archives and adversarial exports; no converter imports."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_agent_samples as audit


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def packed(raw):
    return {"length": len(raw), "sha256": sha(raw), "base64": base64.b64encode(raw).decode()}


def stream(rows):
    rows = copy.deepcopy(rows)
    for index, row in enumerate(rows, 1):
        row["sequence"] = index
    return b"".join(encoded(row) + b"\n" for row in rows)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value) + b"\n")


def load(path):
    return json.loads(path.read_bytes())


def archive(*, failed_tool=False, parallel=False, advisory=None):
    rows = []

    def event(kind, **data):
        rows.append({"schema_version": 1, "archive_id": "archive-A", "episode_id": "archive-A", "command_id": "command-A",
                     "sequence": len(rows) + 1, "kind": kind, "data": data})

    tools = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in ("observe", "weather")]
    calls = [{"id": "call-A", "type": "function", "index": 0,
              "function": {"name": "observe", "arguments": '{"reasoning_content":"public business value","x":2}'}}]
    if parallel:
        calls.append({"id": "call-B", "type": "function", "index": 1, "function": {"name": "weather", "arguments": "{}"}})
    first = {"role": "assistant", "content": None, "reasoning_content": "private first thought", "tool_calls": calls}
    final = {"role": "assistant", "content": "Completed.", "reasoning_content": "private final thought"}
    prompt = [{"role": "system", "content": "Use public observations."}, {"role": "user", "content": "Inspect the scene."}]
    response_bodies = []

    def model(number, messages, target):
        logical, attempt = f"model-{number}", f"attempt-{number}"
        event("model_start", logical_call_id=logical, input={"messages": [{"role": "user", "content": "NOT THE ACTUAL WIRE"}]})
        request = {"stream": False, "model": "inert-model", "messages": messages, "tools": tools}
        event("http_request", attempt_id=attempt, logical_call_id=logical, body_scope="serialized_http_request_content", body=packed(encoded(request)))
        response = {"id": f"response-{number}", "model": "inert-model", "choices": [{"index": 0, "message": target,
                    "finish_reason": "tool_calls" if target.get("tool_calls") else "stop"}]}
        response_bodies.append(response)
        raw = encoded(response)
        event("http_response_start", attempt_id=attempt, logical_call_id=logical, status_code=200,
              body_scope="buffered_httpx_content", content_encoding="gzip")
        event("http_response_chunk", attempt_id=attempt, logical_call_id=logical, chunk_index=1, body=packed(raw))
        event("http_response_end", attempt_id=attempt, logical_call_id=logical, status="complete", complete=True,
              response_observed=True, length=len(raw), sha256=sha(raw), chunk_count=1)
        structured_calls = [{"id": call["id"], "name": call["function"]["name"], "args": json.loads(call["function"]["arguments"])} for call in target.get("tool_calls", [])]
        event("model_end", logical_call_id=logical, status="returned", output={"structured_response": None,
              "result": [{"type": "ai", "content": target["content"] or "", "additional_kwargs": {}, "tool_calls": structured_calls}]})

    event("archive_start")
    model(1, prompt, first)
    for call in calls:
        event("tool_start", tool_span_id="span-" + call["id"], raw_tool_call_id=call["id"], source_model_id="model-1",
              source_relation="unique", source_candidates=["model-1"], input={"id": call["id"], "name": call["function"]["name"],
              "args": json.loads(call["function"]["arguments"]), "type": "tool_call"})
    receipts = []
    # Deliberately return parallel calls in reverse order. Association is by ID.
    for call in reversed(calls):
        content = '{"status":"error","error":"validation failed"}' if failed_tool and call["id"] == "call-A" else '{"status":"success","reasoning":"public observation"}'
        output = {"type": "tool", "name": call["function"]["name"], "tool_call_id": call["id"], "content": content,
                  "status": "error" if failed_tool and call["id"] == "call-A" else "success"}
        event("tool_end", tool_span_id="span-" + call["id"], status="returned", tool_call_id_mismatch=False, output=output)
        receipts.append({"role": "tool", "tool_call_id": call["id"], "content": content + (advisory or "")})
    model(2, prompt + [first] + receipts, final)
    event("archive_end", complete=True, reasons=[], error_types=[])
    return rows


def event(rows, kind, occurrence=0):
    return [row for row in rows if row["kind"] == kind][occurrence]["data"]


def rewrite_response(rows, number, mutate):
    chunk = event(rows, "http_response_chunk", number)
    response = json.loads(base64.b64decode(chunk["body"]["base64"]))
    mutate(response)
    raw = encoded(response)
    chunk["body"] = packed(raw)
    end = event(rows, "http_response_end", number)
    end.update(length=len(raw), sha256=sha(raw))


def rewrite_request(rows, number, mutate):
    data = event(rows, "http_request", number)
    request = json.loads(base64.b64decode(data["body"]["base64"]))
    mutate(request)
    data["body"] = packed(encoded(request))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Audit must not access a provider")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_actual_wire_not_model_start_and_parallel_replies_by_id():
    owner, models = audit.inspect_archive(stream(archive(parallel=True)))
    assert owner["command_id"] == "command-A"
    assert models[0]["messages"][1]["content"] == "Inspect the scene."
    assert [item["tool_call_id"] for item in models[0]["tool_links"]] == ["call-A", "call-B"]
    assert [item["tool_call_id"] for item in models[1]["history_receipt_proofs"]] == ["call-B", "call-A"]


def test_failed_current_decision_but_recovery_history_is_preserved():
    _, models = audit.inspect_archive(stream(archive(failed_tool=True)))
    assert models[0]["tool_feedback"][0]["status"] == "excluded_target"
    assert models[1]["tool_feedback"] == []
    assert json.loads(models[1]["messages"][-2]["content"])["status"] == "error"
    assert "reasoning_content" not in models[1]["messages"][2]
    assert json.loads(models[1]["messages"][2]["tool_calls"][0]["function"]["arguments"])["reasoning_content"] == "public business value"


def test_explicit_validation_error_is_failed_feedback():
    feedback = audit.tool_feedback(("call-A", "observe", {}), {"output": {"status": "success", "content": '{"status":"validation_error"}'}}, [])
    assert feedback["status"] == "excluded_target"
    assert feedback["reasons"] == ["receipt_status_validation_error"]


@pytest.mark.parametrize("mutation,reason", [
    (lambda r: event(r, "model_end")["output"].update(structured_response={"secret": 1}), "structured_response"),
    (lambda r: event(r, "model_end")["output"]["result"][0].update(content="different"), "wire_handler"),
    (lambda r: event(r, "tool_start")["input"].update(args={"x": 3}), "arguments_mismatch"),
    (lambda r: event(r, "tool_start").update(source_model_id="model-2", source_candidates=["model-2"]), "model_mismatch"),
    (lambda r: event(r, "tool_end")["output"].update(tool_call_id="foreign-call"), "receipt_mismatch"),
    (lambda r: event(r, "tool_end").update(tool_call_id_mismatch=True), "tool_not_closed"),
    (lambda r: event(r, "tool_end")["output"].update(status="unknown"), "unknown_tool_message_status"),
    (lambda r: event(r, "archive_end").update(complete=False), "archive_not_complete"),
    (lambda r: event(r, "http_response_end").update(complete=False), "incomplete_response"),
    (lambda r: event(r, "http_response_start").update(status_code=500), "http_not_success"),
    (lambda r: event(r, "http_response_start").update(body_scope="httpx_stream_before_content_decoding"), "unsupported_wire_encoding"),
    (lambda r: r[4].update(command_id="another-command"), "archive_sequence_or_identity"),
])
def test_reject_archive_semantic_corruption(mutation, reason):
    rows = archive()
    mutation(rows)
    with pytest.raises(audit.AuditError, match=reason):
        audit.inspect_archive(stream(rows))


def test_multiple_successful_http_attempts_are_not_extra_labels():
    rows = archive()
    extra = copy.deepcopy([row for row in rows if row["kind"].startswith("http_")][:4])
    for row in extra:
        row["data"]["attempt_id"] = "retry-1"
    at = next(i for i, row in enumerate(rows) if row["kind"] == "model_end")
    rows[at:at] = extra
    with pytest.raises(audit.AuditError, match="multiple_or_missing_attempts"):
        audit.inspect_archive(stream(rows))


def test_one_call_cannot_execute_two_spans():
    rows = archive()
    extra = copy.deepcopy([row for row in rows if row["kind"] in {"tool_start", "tool_end"}])
    for row in extra:
        row["data"]["tool_span_id"] = "second-execution"
    at = next(i for i, row in enumerate(rows) if row["kind"] == "model_start" and row["data"]["logical_call_id"] == "model-2")
    rows[at:at] = extra
    with pytest.raises(audit.AuditError, match="duplicate_tool_execution"):
        audit.inspect_archive(stream(rows))


@pytest.mark.parametrize("change,reason", [
    (lambda r: rewrite_response(r, 0, lambda body: body["choices"][0].update(finish_reason="length")), "unfinished_target"),
    (lambda r: rewrite_response(r, 0, lambda body: body["choices"].append(copy.deepcopy(body["choices"][0]))), "ambiguous_choice"),
    (lambda r: rewrite_request(r, 0, lambda body: body.update(tools=[])), "unadvertised_tool"),
    (lambda r: rewrite_request(r, 1, lambda body: body["messages"][-1].update(content="private final checker: passed")), "history_receipt_content"),
    (lambda r: rewrite_request(r, 1, lambda body: body["messages"][-1].update(tool_call_id="wrong")), "unmatched_history_receipt"),
    (lambda r: rewrite_request(r, 1, lambda body: body["messages"].pop(-1)), "missing_history_receipt"),
])
def test_rehashed_wire_semantic_corruption(change, reason):
    rows = archive()
    change(rows)
    with pytest.raises(audit.AuditError, match=reason):
        audit.inspect_archive(stream(rows))


def test_advisory_requires_exact_sealed_text_and_id():
    suffix = audit.MARKER + '{"progress":"visible"}'
    rows = archive(advisory=suffix)
    proof = {"tool_call_id": "call-A", "text": suffix, "message_index": 2, "segment": 0}
    _, models = audit.inspect_archive(stream(rows), [proof])
    assert models[1]["history_receipt_proofs"][0]["transformation"] == "sealed_public_evidence_suffix"
    for injections in ([], [{**proof, "tool_call_id": "call-B"}], [{**proof, "text": suffix + "extra"}], [proof, proof]):
        with pytest.raises(audit.AuditError, match="unproven_advisory"):
            audit.inspect_archive(stream(rows), injections)


@pytest.mark.parametrize("content", ["<think>private</think>done", [{"type": "text", "text": "<think>private</think>"}],
                                     [{"type": "text", "text": "ok", "reasoning_content": "hidden"}], [{"type": "image_url", "image_url": "x"}]])
def test_reasoning_in_answer_or_extra_blocks_is_rejected(content):
    with pytest.raises(audit.AuditError):
        audit.visible_message({"role": "assistant", "content": content}, "response.message", [])


def test_reasoning_removal_audit_never_contains_reasoning_body():
    removed = []
    visible = audit.visible_message({"role": "assistant", "content": [{"type": "thinking", "text": "PRIVATE-A"}, {"type": "text", "text": "answer"}],
                                     "reasoning_content": "PRIVATE-B", "refusal": None}, "response.message", removed)
    assert visible == {"role": "assistant", "content": [{"type": "text", "text": "answer"}]}
    assert len(removed) == 2 and "PRIVATE" not in json.dumps(removed)


def reseal_export(dataset):
    manifest = load(dataset / "manifest.json")
    manifest["files"] = {name: sha((dataset / name).read_bytes()) for name in manifest["files"]}
    write(dataset / "manifest.json", manifest)
    (dataset / "manifest.sha256").write_text(sha((dataset / "manifest.json").read_bytes()) + "\n")


@pytest.fixture
def export(tmp_path):
    """A manually assembled three-slot export, including failure and unrun."""
    workspace, run_name = tmp_path / "workspace", "runs/inert"
    run = workspace / run_name
    folder = run / "trials/good"
    raw = stream(archive(failed_tool=True))
    archive_name = "policy/modelio/archive.jsonl"
    archive_path = folder / archive_name
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(raw)
    write(folder / "policy/task-state.json", {"command": "Inspect the scene.", "events": []})
    snapshot = b"INERT SNAPSHOT; NEVER UNPICKLE"
    (folder / "private").mkdir()
    (folder / "private/snapshot.bin").write_bytes(snapshot)
    worker = {"status": "completed", "trial_id": "good", "task_id": "task-A", "pid": 41, "drained": True,
              "limits": {"stop_reason": None, "unknown_usage_attempts": 0}, "agent": {"success": True, "archive_verify": {"valid": True, "complete": True},
              "archive_info": {"archive_id": "archive-A"}}, "entry": {"command_id": "command-A"},
              "snapshot": {"path": "private/snapshot.bin", "sha256": sha(snapshot)},
              "isolation": {"canary_absent": True, "checker_definitions_absent": True}}
    write(folder / "worker-result.json", worker)
    score = {"trial_id": "good", "pid": 42, "worker_pid": 41, "worker_result_sha256": sha((folder / "worker-result.json").read_bytes()),
             "snapshot_sha256": sha(snapshot), "checker_tree_matches_original": True, "agent_imported": False, "network_attempts": [],
             "checker": {"passed": True, "checks_expected": 1, "checks_passed": 1, "errors": []}}
    write(folder / "evaluation/result.json", score)
    write(run / "worker-exit-seals/good.json", {"worker-result.json": sha((folder / "worker-result.json").read_bytes()), "private/snapshot.bin": sha(snapshot)})
    write(run / "scorer-exit-seals/good.json", {"evaluation/result.json": sha((folder / "evaluation/result.json").read_bytes())})
    trials = [{"trial_id": ident, "task_id": "task-A"} for ident in ("good", "failed", "unrun")]
    write(run / "manifest.json", {"scope": "general_agent_v2_execution_contract", "online_authorized_executions": 0, "trials": trials})
    processes = [{"mode": kind, "trial_id": "good", "pid": pid, "exited": True, "returncode": 0, "kill_reason": None} for kind, pid in (("worker", 41), ("score", 42))]
    write(run / "results.json", [{"trial_id": "good", "processes": processes}, {"trial_id": "failed", "processes": []}])
    write(run / "inputs/fixtures/task-A.json", {"scene_id": "scene-A", "command": "Inspect the scene."})
    write(run / "campaign-seal.json", {"files": {str(p.relative_to(run)): sha(p.read_bytes()) for p in run.rglob("*") if p.is_file()}})
    planned = {"run": run_name, "manifest_sha256": sha((run / "manifest.json").read_bytes()), "campaign_seal_sha256": sha((run / "campaign-seal.json").read_bytes()),
               "planned_trials": 3, "purpose": "development_format_and_input_validation_only"}
    plan = {"schema_version": 1, "historical_source_role": "development", "provider_calls_authorized": 0, "teacher_training_examples_expected": 0,
            "runs": [planned], "planned_trials": 3}
    plan_path = workspace / "plan.json"
    write(plan_path, plan)
    plan_path.with_suffix(".sha256").write_text(sha(plan_path.read_bytes()) + "\n")
    dataset = tmp_path / "export"
    dataset.mkdir()
    rows = archive(failed_tool=True)
    request = json.loads(base64.b64decode(event(rows, "http_request", 1)["body"]["base64"]))
    response = json.loads(base64.b64decode(event(rows, "http_response_chunk", 1)["body"]["base64"]))
    messages = copy.deepcopy(request["messages"])
    messages[2].pop("reasoning_content")
    messages[2]["tool_calls"][0].pop("index")
    messages.append({"role": "assistant", "content": "Completed."})
    source = {"run": run_name, "trial_id": "good", "task_id": "task-A", "scene_id": "scene-A", "run_manifest_sha256": planned["manifest_sha256"],
              "archive_file": "trials/good/" + archive_name, "archive_sha256": sha(raw), "source_kind": "synthetic_sdk_development", "split": "development",
              "archive_id": "archive-A", "episode_id": "archive-A", "command_id": "command-A", "logical_call_id": "model-2", "attempt_id": "attempt-2",
              "request_sequence": next(row["sequence"] for row in rows if row["kind"] == "http_request" and row["data"]["logical_call_id"] == "model-2"),
              "response_id": "response-2", "request_body_sha256": sha(encoded(request)), "response_body_sha256": sha(encoded(response)), "model": "inert-model",
              "harness_config": {"arm": "integrated", "task_progress_mode": None, "harness_revision": None, "scheduler": None}}
    sample_id = sha(encoded({k: source[k] for k in ("run_manifest_sha256", "trial_id", "archive_sha256", "logical_call_id", "attempt_id")}))
    sample = {"schema": "agent-decision-sample-v1", "sample_id": sample_id, "source": source, "teacher_eligible": False, "messages": messages, "tools": request["tools"],
              "supervision": {"message_loss_mask": [0, 0, 0, 0, 1], "target_message_index": 4, "supervised_fields": ["content", "tool_calls"], "reasoning_supervised": False},
              "audit": {"removed_reasoning": [{"path": "request.messages[2].reasoning_content", "sha256": sha(encoded("private first thought")), "characters": 21},
                                              {"path": "response.message.reasoning_content", "sha256": sha(encoded("private final thought")), "characters": 21}],
                        "tool_links": [], "finish_reason": "stop", "history_receipt_proofs": [{"tool_call_id": "call-A", "raw_content_sha256": sha(encoded(messages[3]["content"])),
                        "wire_content_sha256": sha(encoded(messages[3]["content"])), "transformation": "none"}]}}
    episodes = []
    for ident in ("good", "failed", "unrun"):
        row = {"run": run_name, "run_manifest_sha256": planned["manifest_sha256"], "trial_id": ident, "task_id": "task-A", "source_kind": "synthetic_sdk_development",
               "split": "development", "teacher_eligible": False, "status": {"good": "eligible", "failed": "excluded", "unrun": "unrun"}[ident],
               "reason": {"good": None, "failed": "missing_worker_report", "unrun": "planned_not_run"}[ident], "archive_file": None, "archive_sha256": None,
               "archive_id": None, "logical_decisions": 0, "exported_samples": 0}
        if ident == "good":
            row.update(archive_file=source["archive_file"], archive_sha256=sha(raw), archive_id="archive-A", logical_decisions=2, exported_samples=1)
        episodes.append(row)
    decisions = []
    for number in (1, 2):
        request_record, response_record = event(rows, "http_request", number - 1), event(rows, "http_response_chunk", number - 1)
        decisions.append({"run": run_name, "trial_id": "good", "task_id": "task-A", "archive_id": "archive-A", "logical_call_id": f"model-{number}", "attempt_id": f"attempt-{number}",
            "status": "excluded" if number == 1 else "exported", "reason": "current_decision_has_failed_partial_or_unknown_tool" if number == 1 else None,
            "sample_id": None if number == 1 else sample_id, "tool_feedback": [{"tool_call_id": "call-A", "name": "observe", "status": "excluded_target", "reasons": ["receipt_status_error", "tool_message_error"]}] if number == 1 else [],
            "request_body_sha256": request_record["body"]["sha256"], "response_body_sha256": response_record["body"]["sha256"]})
    payloads = {"samples.jsonl": [sample], "episodes.jsonl": episodes, "decision_inventory.jsonl": decisions,
                "exclusions.jsonl": [{"level": "episode", **row} for row in episodes[1:]] + [{"level": "decision", **decisions[0]}]}
    for name, values in payloads.items():
        (dataset / name).write_bytes(b"".join(encoded(value) + b"\n" for value in values))
    frozen_name = "experiments/agent-samples-v1/source-plan.json"
    frozen = dataset / "source" / frozen_name
    frozen.parent.mkdir(parents=True)
    frozen.write_bytes(plan_path.read_bytes())
    manifest = {"schema": "agent-sample-export-v1", "converter_version": "agent-decision-converter-v1", "purpose": "development", "teacher_eligible": False, "true_teacher_samples": 0,
                "source_plan_sha256": sha(plan_path.read_bytes()), "source_runs": [{**planned, "source_kind": "synthetic_sdk_development", "split": "development"}],
                "sources": {frozen_name: sha(plan_path.read_bytes())}, "files": {str(p.relative_to(dataset)): sha(p.read_bytes()) for p in dataset.rglob("*") if p.is_file()},
                "counts": {"planned_episodes": 3, "eligible_episodes": 1, "excluded_episodes": 1, "unrun_episodes": 1, "logical_decisions": 2, "exported_samples": 1, "excluded_decisions": 1},
                "exclusion_reason_counts": {"current_decision_has_failed_partial_or_unknown_tool": 1},
                "policies": {"split": "development", "episode_success_required": True, "single_attempt_required": True, "failed_tool_decision_excluded": True,
                    "recovery_history_retained_masked": True, "source_internal_api_actions_are_labels": False, "production_training_export_supported": False}}
    write(dataset / "manifest.json", manifest)
    reseal_export(dataset)
    return dataset, plan_path, workspace


def check_export(export):
    dataset, plan, workspace = export
    return audit.audit_export(dataset, source_plan=plan, workspace=workspace)


def test_full_independent_export_keeps_all_denominators(export):
    result = check_export(export)
    assert result["passed"], result
    assert result["counts"] == {"planned_episodes": 3, "eligible_episodes": 1, "excluded_episodes": 1, "unrun_episodes": 1,
                                 "logical_decisions": 2, "exported_samples": 1, "excluded_decisions": 1}
    assert result["true_teacher_samples"] == 0


@pytest.mark.parametrize("mutation,reason", [
    (lambda sample: sample["source"].update(split="train"), "sample_source_identity"),
    (lambda sample: sample["source"].update(split="test"), "sample_source_identity"),
    (lambda sample: sample.update(teacher_eligible=True), "sample_training_promotion"),
    (lambda sample: sample["supervision"].update(message_loss_mask=[1, 1, 1, 1, 1]), "loss_mask"),
    (lambda sample: sample["supervision"].update(message_loss_mask=[False, False, False, False, True]), "loss_mask_integer_contract"),
    (lambda sample: sample["messages"][-1].update(content="{" + '"internal_move_to":true' + "}"), "actual_wire"),
    (lambda sample: sample["messages"][2].update(reasoning_content="private first thought"), "actual_wire"),
    (lambda sample: sample.update(private_checker={"passed": True}), "unexpected_sample_field"),
    (lambda sample: sample["source"].update(logical_call_id="model-1"), "sample_source_identity"),
    (lambda sample: sample["audit"].update(removed_reasoning=[]), "audit_proof"),
])
def test_resealed_sample_tamper_does_not_pass(export, mutation, reason):
    dataset, _, _ = export
    samples = [json.loads(line) for line in (dataset / "samples.jsonl").read_text().splitlines()]
    mutation(samples[0])
    (dataset / "samples.jsonl").write_bytes(b"".join(encoded(row) + b"\n" for row in samples))
    reseal_export(dataset)
    result = check_export(export)
    assert result["passed"] is False and reason in result["error"], result


@pytest.mark.parametrize("name", ["episodes.jsonl", "decision_inventory.jsonl", "samples.jsonl"])
def test_cannot_delete_failures_or_labels_even_after_resealing(export, name):
    dataset, _, _ = export
    rows = (dataset / name).read_bytes().splitlines()
    (dataset / name).write_bytes(b"".join(row + b"\n" for row in rows[:-1]))
    reseal_export(dataset)
    assert check_export(export)["passed"] is False


def test_original_source_tampering_is_detected_without_deserialization(export):
    _, _, workspace = export
    snapshot = workspace / "runs/inert/trials/good/private/snapshot.bin"
    snapshot.write_bytes(b"changed")
    result = check_export(export)
    assert not result["passed"] and "file_hash_mismatch" in result["error"]


def test_final_claim_does_not_override_checker_and_unknown_usage_stops(export):
    _, _, workspace = export
    run = workspace / "runs/inert"
    worker = load(run / "trials/good/worker-result.json")
    score = load(run / "trials/good/evaluation/result.json")
    result = load(run / "results.json")[0]
    score["checker"].update(passed=False, checks_passed=0)
    with pytest.raises(audit.AuditError, match="checker_not_passed"):
        audit.episode_gate(run, {"trial_id": "good", "task_id": "task-A"}, result, worker, score)
    worker["limits"]["unknown_usage_attempts"] = 1
    with pytest.raises(audit.AuditError, match="unknown_usage"):
        audit.episode_gate(run, {"trial_id": "good", "task_id": "task-A"}, result, worker, score)


def test_auditor_never_imports_converter_or_provider():
    import ast
    parsed = ast.parse(Path(audit.__file__).read_text())
    imported = {alias.name for node in ast.walk(parsed) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(parsed) if isinstance(node, ast.ImportFrom)}
    assert all(name.split(".")[0] in {"__future__", "argparse", "base64", "collections", "hashlib", "json", "pathlib", "re"} for name in imported)
