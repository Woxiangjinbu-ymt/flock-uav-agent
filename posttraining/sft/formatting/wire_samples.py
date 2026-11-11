"""Read-only wire-episode conversion into development decision-format samples.

Only a current assistant response is supervised. Historical assistant messages,
system/user/tool messages and tool schemas are inputs, with zero loss. Provider
reasoning fields stay in their original archive and only hashes/counts leave it.
This entry does not approve Teacher data or support production training export.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
VERSION = "agent-decision-converter-v1"
SAMPLE_SCHEMA = "agent-decision-sample-v1"
MARKER = "\n\n[M1-R task evidence; derived from actual tool receipts]\n"
REASONING_FIELDS = {"reasoning_content", "reasoning", "thinking"}
RUNS = tuple(f"docs/results/agent-execution-contract-20261102/{name}-run-01" for name in
             ("main", "resume", "budget", "unknown", "cancel", "timeout", "undrained", "deadline")) + (
    "docs/results/task-coverage-splits-20261103/entry-smoke-run-01",
    "docs/results/deepseek-behavior-pilot-20261102/run-01",
)


class Excluded(ValueError):
    """A fixed machine-readable exclusion reason, not arbitrary exception text."""


def require(condition, reason):
    if not condition:
        raise Excluded(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_field")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(Excluded("nonfinite_json_number")))


def safe(root, name):
    name = Path(name)
    require(not name.is_absolute() and ".." not in name.parts, "unsafe_source_path")
    path = root / name
    require(not any(p.is_symlink() for p in (path, *path.parents)), "source_symlink")
    require(path.resolve().is_relative_to(root.resolve()), "source_path_escape")
    return path


def read(root, name):
    return parse(safe(root, name).read_bytes())


def verify_hashes(root, files):
    require(isinstance(files, dict) and bool(files), "missing_file_inventory")
    for name, expected in files.items():
        require(sha(safe(root, name).read_bytes()) == expected, "source_hash_mismatch")


def decode(record):
    raw = base64.b64decode(record["base64"], validate=True)
    require(type(record["length"]) is int and len(raw) == record["length"] and sha(raw) == record["sha256"], "wire_bytes_hash_mismatch")
    return raw


def _calls(message, *, structured=False):
    if structured:
        require(not message.get("invalid_tool_calls"), "structured_invalid_tool_calls")
        extra = message.get("additional_kwargs", {})
        raw = extra.get("tool_calls") if isinstance(extra, dict) else None
        if not raw:
            return [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.get("tool_calls", [])]
    else:
        raw = message.get("tool_calls", [])
    require(isinstance(raw, list), "invalid_tool_calls")
    result = []
    for call in raw:
        require(isinstance(call, dict) and call.get("type") == "function" and isinstance(call.get("id"), str) and call["id"], "invalid_tool_call")
        function = call.get("function", {})
        require(isinstance(function.get("name"), str) and isinstance(function.get("arguments"), str), "invalid_function_call")
        args = parse(function["arguments"])
        require(isinstance(args, dict), "tool_arguments_not_object")
        result.append({"id": call["id"], "name": function["name"], "args": args})
    return result


def clean_message(message, path, removed):
    require(isinstance(message, dict) and message.get("role") in {"system", "developer", "user", "assistant", "tool"}, "unsupported_message_role")
    permitted = {"role", "content", "tool_calls", "tool_call_id", "name"}
    require(not set(message) - permitted - REASONING_FIELDS - {"refusal"}, "unsupported_message_field")
    result = {}
    for key, value in message.items():
        if key in REASONING_FIELDS:
            encoded = canonical(value)
            removed.append({"path": f"{path}.{key}", "sha256": sha(encoded), "characters": len(value) if isinstance(value, str) else None})
        elif key == "refusal":
            require(value is None, "assistant_refusal")
        else:
            result[key] = value
    if "tool_calls" in result:
        normalized = []
        for call in result["tool_calls"]:
            require(not set(call) - {"id", "type", "function", "index"}, "unsupported_tool_call_field")
            require(not set(call["function"]) - {"name", "arguments"}, "unsupported_function_field")
            normalized.append({"id": call["id"], "type": call["type"], "function": dict(call["function"])})
        result["tool_calls"] = normalized
    content = result.get("content")
    if isinstance(content, list):
        blocks = []
        for index, block in enumerate(content):
            require(isinstance(block, dict), "unsupported_content_block")
            if block.get("type") in REASONING_FIELDS:
                encoded = canonical(block)
                removed.append({"path": f"{path}.content[{index}]", "sha256": sha(encoded), "characters": len(encoded.decode())})
            else:
                require(set(block) == {"type", "text"} and block.get("type") == "text" and isinstance(block.get("text"), str), "unsupported_content_block")
                if message["role"] == "assistant":
                    require(not re.search(r"</?(?:think|thinking|reasoning)(?:\s|>)", block["text"], re.I), "reasoning_tags_in_assistant_content")
                blocks.append(block)
        result["content"] = blocks
    else:
        require(content is None or isinstance(content, str), "unsupported_message_content")
        if message["role"] == "assistant" and isinstance(content, str):
            require(not re.search(r"</?(?:think|thinking|reasoning)(?:\s|>)", content, re.I), "reasoning_tags_in_assistant_content")
    # Never parse business strings or tool arguments to remove similarly named
    # application fields. This preserves the actual public tool semantics.
    return result


def _feedback(span, ledger_events):
    output = span["end"]["output"]
    raw = output.get("content")
    try:
        payload = parse(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        payload = None
    reasons = []
    if output.get("status") == "error":
        reasons.append("tool_message_error")
    failures = {"error", "validation_error", "failed", "failure", "blocked", "rejected", "unsupported", "partial", "partial_success", "unknown"}
    def inspect(value):
        if not isinstance(value, dict):
            return
        for field in ("status", "outcome"):
            if isinstance(value.get(field), str) and value[field] in failures:
                reasons.append(f"receipt_{field}_{value[field]}")
        if value.get("success") is False or value.get("partial_success") is True:
            reasons.append("receipt_not_complete_success")
        for key in ("navigation_result", "move_result"):
            inspect(value.get(key))
    inspect(payload)
    for event in ledger_events:
        if event.get("tool_call_id") == span["call"]["id"] and event.get("status") in {"error", "blocked", "partial", "unknown"}:
            reasons.append("public_ledger_" + event["status"])
    return {"tool_call_id": span["call"]["id"], "name": span["call"]["name"],
            "status": "excluded_target" if reasons else "returned", "reasons": sorted(set(reasons))}


def read_archive(raw, *, injections=(), ledger_events=()):
    """Validate ownership, wire/handler agreement and every model/tool relation."""
    require(raw.endswith(b"\n"), "unterminated_archive")
    rows = [parse(line) for line in raw.splitlines()]
    require(bool(rows) and rows[0]["kind"] == "archive_start", "missing_archive_start")
    owner = {key: rows[0][key] for key in ("archive_id", "episode_id", "command_id")}
    models, attempts, spans, ending = {}, {}, {}, None
    allowed = {"archive_start", "model_start", "model_end", "http_request", "http_response_start", "http_response_chunk", "http_response_end", "tool_start", "tool_end", "archive_end"}
    for index, row in enumerate(rows, 1):
        require(row.get("schema_version") == 1 and row["sequence"] == index and ending is None, "archive_sequence_or_close")
        require(all(row.get(key) == value for key, value in owner.items()), "mixed_archive_ownership")
        kind, data = row["kind"], row["data"]
        require(kind in allowed, "unsupported_or_incomplete_archive_event")
        if kind == "archive_start":
            require(index == 1, "duplicate_archive_start")
        elif kind == "model_start":
            ident = data["logical_call_id"]
            require(ident not in models, "duplicate_model_id")
            models[ident] = {"id": ident, "start_sequence": index, "end": None, "attempts": []}
        elif kind == "model_end":
            model = models[data["logical_call_id"]]
            require(model["end"] is None, "duplicate_model_end")
            require(data.get("status") == "returned" and not data.get("error_type"), "model_not_returned")
            model.update(end=data, end_sequence=index)
        elif kind == "http_request":
            ident, logical = data["attempt_id"], data["logical_call_id"]
            require(ident not in attempts and logical in models and models[logical]["end"] is None, "invalid_http_request_association")
            require(data.get("body_scope") == "serialized_http_request_content" and not data.get("error_type"), "request_not_wire")
            body = decode(data["body"])
            attempts[ident] = {"id": ident, "logical": logical, "request": parse(body), "request_sha256": sha(body),
                               "request_sequence": index, "start": None, "chunks": [], "end": None}
            models[logical]["attempts"].append(ident)
        elif kind.startswith("http_response_"):
            attempt = attempts[data["attempt_id"]]
            require(data["logical_call_id"] == attempt["logical"] and attempt["end"] is None, "invalid_response_association")
            if kind == "http_response_start":
                require(attempt["start"] is None and 200 <= data.get("status_code", 0) < 300, "response_not_unique_success")
                # Buffered httpx bytes are already decoded, regardless of gzip
                # headers. Only identity raw streams can be parsed as JSON.
                require(data.get("body_scope") == "buffered_httpx_content" or
                        (data.get("body_scope") == "httpx_stream_before_content_decoding" and data.get("content_encoding") == "identity"), "unsupported_response_encoding")
                attempt["start"] = data
            elif kind == "http_response_chunk":
                require(attempt["start"] is not None and data["chunk_index"] == len(attempt["chunks"]) + 1, "invalid_response_chunk_order")
                attempt["chunks"].append(decode(data["body"]))
            else:
                body = b"".join(attempt["chunks"])
                require(data.get("complete") is True and data.get("status") == "complete" and not data.get("error_type") and data.get("response_observed") is True, "response_incomplete")
                require((data["length"], data["sha256"], data["chunk_count"]) == (len(body), sha(body), len(attempt["chunks"])), "response_aggregate_hash_mismatch")
                attempt.update(end=data, response=parse(body), response_sha256=sha(body), end_sequence=index)
        elif kind == "tool_start":
            ident = data["tool_span_id"]
            require(ident not in spans and data.get("source_relation") == "unique" and data.get("source_candidates") == [data.get("source_model_id")], "ambiguous_tool_source")
            spans[ident] = {"id": ident, "start": data, "start_sequence": index, "end": None}
        elif kind == "tool_end":
            span = spans[data["tool_span_id"]]
            require(span["end"] is None and data.get("status") == "returned" and not data.get("error_type") and data.get("tool_call_id_mismatch") is False, "tool_span_not_complete")
            span.update(end=data, end_sequence=index)
        else:
            ending = data
    require(ending is not None and ending.get("complete") is True and not ending.get("reasons") and not ending.get("error_types"), "archive_incomplete")
    require(bool(models), "no_model_decisions")
    calls = {}
    for model in models.values():
        require(model["end"] is not None and len(model["attempts"]) == 1, "multiple_or_missing_attempts")
        attempt = attempts[model["attempts"][0]]
        require(attempt["end"] is not None and attempt["end_sequence"] < model["end_sequence"], "response_model_order")
        request, response = attempt["request"], attempt["response"]
        require(request.get("stream") is False and isinstance(request.get("messages"), list), "unsupported_request_protocol")
        choices = response.get("choices")
        require(isinstance(choices, list) and len(choices) == 1 and choices[0].get("index") == 0, "ambiguous_response_choices")
        choice = choices[0]
        message = choice.get("message")
        require(isinstance(message, dict) and message.get("role") == "assistant" and not message.get("refusal"), "unsupported_assistant_response")
        wire_calls = _calls(message)
        require(choice.get("finish_reason") == ("tool_calls" if wire_calls else "stop"), "unfinished_assistant_response")
        require(bool(wire_calls) or bool(message.get("content")), "empty_assistant_response")
        require(model["end"].get("output", {}).get("structured_response") is None, "unsupported_structured_response")
        structured = model["end"].get("output", {}).get("result", [])
        require(isinstance(structured, list) and len(structured) == 1 and structured[0].get("type") == "ai", "unsupported_structured_output")
        require((message.get("content") or "") == (structured[0].get("content") or "") and wire_calls == _calls(structured[0], structured=True), "wire_handler_output_mismatch")
        tools = request.get("tools", [])
        require(isinstance(tools, list), "unsupported_tools")
        names = []
        for tool in tools:
            require(isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("function", {}).get("name"), str), "unsupported_tool_schema")
            names.append(tool["function"]["name"])
        require(len(names) == len(set(names)), "duplicate_advertised_tools")
        for call in wire_calls:
            require(call["id"] not in calls and call["name"] in names, "duplicate_or_unadvertised_tool_call")
            calls[call["id"]] = {"model": model, "call": call, "span": None}
        model.update(attempt=attempt, message=message, wire_calls=wire_calls, finish_reason=choice["finish_reason"])
    for span in spans.values():
        start, end = span["start"], span["end"]
        call_id = start.get("raw_tool_call_id")
        require(call_id in calls and end is not None, "orphan_tool_span")
        linked = calls[call_id]
        model, call = linked["model"], linked["call"]
        require(linked["span"] is None, "tool_call_executed_twice")
        require(start["source_model_id"] == model["id"] and span["start_sequence"] > model["end_sequence"], "tool_model_source_mismatch")
        tool_input = start.get("input", {})
        require(tool_input.get("id") == call_id and tool_input.get("name") == call["name"] and tool_input.get("args") == call["args"], "tool_wire_arguments_mismatch")
        output = end.get("output")
        require(isinstance(output, dict) and output.get("type") == "tool" and output.get("tool_call_id") == call_id and output.get("name") in (None, call["name"]), "tool_receipt_identity_mismatch")
        require(output.get("status") in {"success", "error"}, "unsupported_tool_message_status")
        span["call"] = call
        span["feedback"] = _feedback(span, ledger_events)
        linked["span"] = span
    require(all(call["span"] is not None for call in calls.values()), "tool_call_missing_receipt")
    for model in models.values():
        history_calls, history_replies, proofs = set(), set(), []
        for message in model["attempt"]["request"]["messages"]:
            if message.get("role") == "assistant":
                for call in _calls(message):
                    require(call["id"] in calls and call == calls[call["id"]]["call"] and calls[call["id"]]["model"]["end_sequence"] < model["start_sequence"], "history_tool_call_not_prior_wire")
                    require(call["id"] not in history_calls, "duplicate_history_tool_call")
                    history_calls.add(call["id"])
            if message.get("role") == "tool":
                ident = message.get("tool_call_id")
                require(ident in history_calls and ident not in history_replies, "history_tool_receipt_unmatched")
                linked = calls[ident]
                span, raw_content = linked["span"], linked["span"]["end"]["output"].get("content")
                require(span["end_sequence"] < model["attempt"]["request_sequence"] and message.get("name") in (None, linked["call"]["name"]), "history_tool_receipt_order_or_name")
                content = message.get("content")
                proof = {"tool_call_id": ident, "raw_content_sha256": sha(canonical(raw_content)), "wire_content_sha256": sha(canonical(content)), "transformation": "none"}
                if content != raw_content:
                    require(isinstance(content, str) and isinstance(raw_content, str) and content.startswith(raw_content), "history_tool_content_mismatch")
                    suffix = content[len(raw_content):]
                    matching = [item for item in injections if item.get("tool_call_id") == ident and item.get("text") == suffix]
                    require(suffix.startswith(MARKER) and len(matching) == 1, "unproven_public_advisory")
                    proof.update(transformation="sealed_public_evidence_suffix", suffix_sha256=sha(suffix.encode()), injection_message_index=matching[0].get("message_index"), injection_segment=matching[0].get("segment"))
                proofs.append(proof)
                history_replies.add(ident)
        require(history_calls == history_replies, "history_missing_tool_receipt")
        model["history_receipt_proofs"] = proofs
        model["tool_spans"] = [calls[call["id"]]["span"] for call in model["wire_calls"]]
    return owner, list(models.values())


def validate_future_training_identity(record, dataset_manifest_bytes, quality_review):
    """Validate future metadata only; this does not enable a collection/export path."""
    require(isinstance(dataset_manifest_bytes, bytes), "training_manifest_bytes_required")
    dataset_manifest = parse(dataset_manifest_bytes)
    require(dataset_manifest.get("phase") == "formal" and dataset_manifest.get("generator_version") == "task-coverage-splits-v1", "training_requires_formal_w2")
    matches = [task for task in dataset_manifest.get("tasks", []) if task.get("task_id") == record.get("task_id")]
    require(len(matches) == 1, "training_task_not_in_w2")
    task = matches[0]
    require(task["split"] == record.get("split") == "train", "training_requires_train_split")
    require(record.get("scene_id") == task["scene_id"] and record.get("command_sha256") == task["command_sha256"], "training_identity_mismatch")
    require(record.get("source_kind") == "real_provider", "training_requires_real_provider")
    require(isinstance(record.get("initial_state_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", record["initial_state_sha256"]) is not None, "training_initial_state_missing")
    require(quality_review.get("status") == "approved" and quality_review.get("task_id") == record["task_id"]
            and quality_review.get("archive_sha256") == record.get("archive_sha256")
            and quality_review.get("initial_state_sha256") == record["initial_state_sha256"]
            and quality_review.get("dataset_manifest_sha256") == record.get("dataset_manifest_sha256")
            and record.get("dataset_manifest_sha256") == sha(dataset_manifest_bytes), "training_quality_review_missing_or_mismatched")
    return {"metadata_checks_passed": True, "production_training_export_supported": False}


def _verify_run(run, planned):
    require(sha(safe(run, "manifest.json").read_bytes()) == planned["manifest_sha256"]
            and sha(safe(run, "campaign-seal.json").read_bytes()) == planned["campaign_seal_sha256"], "source_plan_pin_mismatch")
    manifest = read(run, "manifest.json")
    seal = read(run, "campaign-seal.json")
    verify_hashes(run, seal["files"])
    actual = {str(p.relative_to(run)) for p in run.rglob("*") if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
    require(actual == set(seal["files"]), "campaign_inventory_mismatch")
    require(len(manifest["trials"]) == planned["planned_trials"], "planned_trial_count_mismatch")
    scope = manifest.get("scope")
    if scope == "deepseek_behavior_pilot_v1":
        require(manifest.get("local_smoke") is False, "unsupported_provider_smoke")
        source_kind = "real_provider_development"
    else:
        require(scope == "general_agent_v2_execution_contract" and manifest.get("online_authorized_executions") == 0, "unsupported_source_scope")
        source_kind = "synthetic_sdk_development"
    trials = manifest["trials"]
    require(len({trial["trial_id"] for trial in trials}) == len(trials), "duplicate_trial_id")
    result_rows = read(run, "results.json")
    require(isinstance(result_rows, list) and len({row["trial_id"] for row in result_rows}) == len(result_rows), "duplicate_run_result")
    results = {row["trial_id"]: row for row in result_rows}
    require(set(results) <= {trial["trial_id"] for trial in trials}, "unplanned_run_result")
    return manifest, source_kind, results


def _episode_gate(run, trial, result, worker, score):
    ident = trial["trial_id"]
    folder = run / "trials" / ident
    processes = result.get("processes", [])
    for mode in ("worker", "score"):
        selected = [process for process in processes if process.get("mode") == mode]
        require(len(selected) == 1, "missing_worker_or_scorer_process")
        process = selected[0]
        require(process.get("exited") is True and process.get("returncode") == 0 and process.get("kill_reason") is None, "unclean_worker_or_scorer_exit")
        expected_pid = worker["pid"] if mode == "worker" else score["pid"]
        require(process["pid"] == expected_pid and process.get("trial_id") == ident, "process_identity_mismatch")
    require(worker.get("status") == "completed" and worker["trial_id"] == score["trial_id"] == ident and worker["task_id"] == trial["task_id"], "worker_identity_mismatch")
    require(score.get("worker_pid") == worker["pid"] and score.get("pid") != worker["pid"], "scorer_not_independent")
    require(worker.get("drained") is True and not worker.get("limits", {}).get("threads_still_alive") and not worker.get("limits", {}).get("needs_process_termination"), "worker_not_drained")
    require(worker["agent"].get("success") is True and not worker["agent"].get("error_type") and worker["limits"].get("stop_reason") is None and not worker.get("lifecycle", {}).get("cancelled"), "episode_not_normal_return")
    require(worker["limits"].get("unknown_usage_attempts", 0) == 0 and not worker["limits"].get("unknown_usage_attempt_ids"), "unknown_provider_usage")
    require(worker["agent"].get("archive_verify", {}).get("valid") is True and worker["agent"]["archive_verify"].get("complete") is True, "episode_archive_incomplete")
    for group in ("worker-exit-seals", "scorer-exit-seals"):
        verify_hashes(folder, read(run, f"{group}/{ident}.json"))
    require(score.get("worker_result_sha256") == sha(safe(folder, "worker-result.json").read_bytes())
            and score.get("snapshot_sha256") == worker.get("snapshot", {}).get("sha256"), "score_worker_snapshot_mismatch")
    require(sha(safe(folder, worker["snapshot"]["path"]).read_bytes()) == score["snapshot_sha256"], "score_snapshot_bytes_mismatch")
    require(score.get("checker_tree_matches_original") is True and score.get("agent_imported") is False and not score.get("network_attempts"), "invalid_independent_scorer")
    checker = score.get("checker", {})
    require(checker.get("passed") is True and not checker.get("errors") and type(checker.get("checks_expected")) is int
            and checker["checks_expected"] > 0 and checker.get("checks_passed") == checker["checks_expected"], "task_not_independently_passed")
    require(worker.get("isolation", {}).get("canary_absent") is True and worker["isolation"].get("checker_definitions_absent") is True, "private_policy_contamination")


def _sample(model, owner, source):
    attempt = model["attempt"]
    removed = []
    messages = [clean_message(message, f"request.messages[{index}]", removed) for index, message in enumerate(attempt["request"]["messages"])]
    target = clean_message(model["message"], "response.message", removed)
    require(not set(target) - {"role", "content", "tool_calls"}, "unsupported_target_field")
    source = {**source, **owner, "logical_call_id": model["id"], "attempt_id": attempt["id"],
              "request_sequence": attempt["request_sequence"], "response_id": attempt["response"].get("id"),
              "request_body_sha256": attempt["request_sha256"], "response_body_sha256": attempt["response_sha256"],
              "model": attempt["request"].get("model")}
    ident = sha(canonical({key: source[key] for key in ("run_manifest_sha256", "trial_id", "archive_sha256", "logical_call_id", "attempt_id")}))
    links = [{"tool_call_id": span["call"]["id"], "tool_span_id": span["id"], "source_model_id": model["id"],
              "name": span["call"]["name"], "arguments_sha256": sha(canonical(span["call"]["args"])),
              "receipt_content_sha256": sha(canonical(span["end"]["output"].get("content"))),
              "feedback": span["feedback"]} for span in model["tool_spans"]]
    return {"schema": SAMPLE_SCHEMA, "sample_id": ident, "source": source, "teacher_eligible": False,
            "messages": messages + [target], "tools": attempt["request"].get("tools", []),
            "supervision": {"message_loss_mask": [0] * len(messages) + [1], "target_message_index": len(messages),
                            "supervised_fields": ["content", "tool_calls"], "reasoning_supervised": False},
            "audit": {"removed_reasoning": removed, "tool_links": links, "finish_reason": model["finish_reason"],
                      "history_receipt_proofs": model["history_receipt_proofs"]}}


def convert_run(run, planned):
    run = Path(run).resolve()
    manifest, source_kind, results = _verify_run(run, planned)
    samples, episodes, decisions = [], [], []
    for trial in manifest["trials"]:
        ident = trial["trial_id"]
        folder = safe(run, "trials/" + ident)
        episode = {"run": planned["run"], "run_manifest_sha256": planned["manifest_sha256"], "trial_id": ident,
                   "task_id": trial["task_id"], "source_kind": source_kind, "split": "development", "teacher_eligible": False,
                   "status": "excluded", "reason": None, "archive_file": None, "archive_sha256": None, "archive_id": None,
                   "logical_decisions": 0, "exported_samples": 0}
        starts, raw, ledger = [], None, {}
        result = results.get(ident)
        archive_files = sorted(folder.glob("policy/modelio/*.jsonl")) if folder.exists() else []
        if len(archive_files) == 1:
            raw = archive_files[0].read_bytes()
            episode.update(archive_file=str(archive_files[0].relative_to(run)), archive_sha256=sha(raw))
            parsed = [parse(line) for line in raw.splitlines()]
            starts = [row for row in parsed if row["kind"] == "model_start"]
            episode["logical_decisions"] = len(starts)
            episode["archive_id"] = parsed[0].get("archive_id") if parsed else None
        try:
            if result is None:
                episode["status"] = "unrun"
                raise Excluded("planned_not_run")
            require((folder / "worker-result.json").exists(), "missing_worker_report")
            worker = read(folder, "worker-result.json")
            require((folder / "evaluation/result.json").exists(), "unscored_episode")
            score = read(folder, "evaluation/result.json")
            _episode_gate(run, trial, result, worker, score)
            require(len(archive_files) == 1 and raw is not None, "missing_or_ambiguous_archive")
            ledger = read(folder, "policy/task-state.json")
            owner, models = read_archive(raw, injections=ledger.get("task_evidence", {}).get("injections", []), ledger_events=ledger.get("events", []))
            require(owner["command_id"] == worker["entry"]["command_id"] and owner["archive_id"] == worker["agent"]["archive_info"]["archive_id"], "archive_worker_identity_mismatch")
            fixture = read(run, "inputs/fixtures/" + trial["task_id"] + ".json")
            require(ledger.get("command") == fixture.get("command"), "command_fixture_mismatch")
            source = {"run": planned["run"], "run_manifest_sha256": planned["manifest_sha256"], "trial_id": ident,
                      "task_id": trial["task_id"], "scene_id": fixture.get("scene_id"), "archive_file": episode["archive_file"],
                      "archive_sha256": episode["archive_sha256"], "source_kind": source_kind, "split": "development",
                      "harness_config": {"arm": trial.get("arm", "integrated"),
                          "task_progress_mode": trial.get("task_progress_mode", manifest.get("agent_profile", {}).get("agent", {}).get("task_progress_mode")),
                          "harness_revision": trial.get("harness_revision", manifest.get("agent_profile", {}).get("harness", {}).get("revision")),
                          "scheduler": trial.get("scheduler", manifest.get("agent_profile", {}).get("harness", {}).get("scheduler"))}}
            local_samples, local_decisions = [], []
            for model in models:
                feedback = [span["feedback"] for span in model["tool_spans"]]
                decision = {"run": planned["run"], "trial_id": ident, "task_id": trial["task_id"], "archive_id": owner["archive_id"],
                            "logical_call_id": model["id"], "attempt_id": model["attempt"]["id"], "status": "excluded",
                            "reason": None, "sample_id": None, "tool_feedback": feedback,
                            "request_body_sha256": model["attempt"]["request_sha256"], "response_body_sha256": model["attempt"]["response_sha256"]}
                # Validate the training view even for a failed-tool decision;
                # its original history is required by subsequent recovery calls.
                candidate = _sample(model, owner, source)
                if any(item["status"] == "excluded_target" for item in feedback):
                    decision["reason"] = "current_decision_has_failed_partial_or_unknown_tool"
                else:
                    local_samples.append(candidate)
                    decision.update(status="exported", sample_id=candidate["sample_id"])
                local_decisions.append(decision)
            samples.extend(local_samples)
            decisions.extend(local_decisions)
            episode.update(status="eligible", exported_samples=len(local_samples))
        except (Excluded, KeyError, ValueError, TypeError) as exc:
            reason = str(exc) if isinstance(exc, Excluded) else "malformed_or_unsupported_episode_structure"
            episode["reason"] = reason
            decisions.extend({"run": planned["run"], "trial_id": ident, "task_id": trial["task_id"],
                              "archive_id": episode["archive_id"], "logical_call_id": row["data"].get("logical_call_id"),
                              "status": "excluded", "reason": "episode_" + reason, "sample_id": None, "tool_feedback": []} for row in starts)
        episodes.append(episode)
    source = {**planned, "source_kind": source_kind, "split": "development"}
    return {"source": source, "samples": samples, "episodes": episodes, "decisions": decisions}


def export_dataset(source_plan, output, *, workspace=ROOT, purpose="development"):
    require(purpose == "development", "production_training_export_not_implemented_old_sources_are_development")
    workspace, source_plan, output = Path(workspace).resolve(), Path(source_plan).resolve(), Path(output)
    require(not output.exists() and not output.is_symlink(), "output_exists")
    require(not any(parent.is_symlink() for parent in output.parents), "output_symlink")
    plan_raw = source_plan.read_bytes()
    require(sha(plan_raw) == source_plan.with_suffix(".sha256").read_text().strip(), "source_plan_hash_mismatch")
    plan = parse(plan_raw)
    require(plan.get("schema_version") == 1 and plan.get("historical_source_role") == "development"
            and plan.get("provider_calls_authorized") == 0 and plan.get("teacher_training_examples_expected") == 0, "unsupported_source_plan")
    require(isinstance(plan.get("runs"), list) and len({r["run"] for r in plan["runs"]}) == len(plan["runs"]), "invalid_source_plan_runs")
    samples, episodes, decisions, sources = [], [], [], []
    for planned in plan["runs"]:
        require(planned.get("purpose") == "development_format_and_input_validation_only", "source_plan_not_development")
        run = safe(workspace, planned["run"])
        require(not output.resolve().is_relative_to(run.resolve()), "output_inside_source_run")
        result = convert_run(run, planned)
        samples.extend(result["samples"]); episodes.extend(result["episodes"]); decisions.extend(result["decisions"]); sources.append(result["source"])
    require(len(episodes) == plan["planned_trials"] and len({s["sample_id"] for s in samples}) == len(samples), "conversion_count_or_identity_mismatch")
    blobs = {}
    for name, rows in (("samples.jsonl", samples), ("episodes.jsonl", episodes), ("decision_inventory.jsonl", decisions),
                       ("exclusions.jsonl", [{"level": "episode", **row} for row in episodes if row["status"] != "eligible"]
                        + [{"level": "decision", **row} for row in decisions if row["status"] != "exported"])):
        blobs[name] = b"".join(canonical(row) + b"\n" for row in rows)
    frozen = {"scripts/convert_agent_samples.py": Path(__file__).read_bytes(),
              "docs/AGENT_SAMPLE_CONTRACT_CN.md": (workspace / "docs/AGENT_SAMPLE_CONTRACT_CN.md").read_bytes(),
              "experiments/agent-samples-v1/source-plan.json": plan_raw}
    blobs.update({"source/" + name: data for name, data in frozen.items()})
    counts = {"planned_episodes": len(episodes), "eligible_episodes": sum(e["status"] == "eligible" for e in episodes),
              "excluded_episodes": sum(e["status"] == "excluded" for e in episodes), "unrun_episodes": sum(e["status"] == "unrun" for e in episodes),
              "logical_decisions": len(decisions), "exported_samples": len(samples), "excluded_decisions": sum(d["status"] == "excluded" for d in decisions)}
    manifest = {"schema": "agent-sample-export-v1", "converter_version": VERSION, "purpose": purpose, "teacher_eligible": False,
                "true_teacher_samples": 0, "source_plan_sha256": sha(plan_raw), "source_runs": sources,
                "sources": {name: sha(data) for name, data in frozen.items()}, "files": {name: sha(data) for name, data in blobs.items()},
                "counts": counts, "exclusion_reason_counts": dict(Counter(d["reason"] for d in decisions if d["reason"])),
                "policies": {"split": "development", "episode_success_required": True, "single_attempt_required": True,
                             "failed_tool_decision_excluded": True, "recovery_history_retained_masked": True,
                             "reasoning_input_and_target": "removed_only_provider_fields_and_blocks_audit_hashes_only",
                             "source_internal_api_actions_are_labels": False, "production_training_export_supported": False}}
    # Recheck immutable source anchors before committing a new output artifact.
    require(source_plan.read_bytes() == plan_raw, "source_plan_changed_during_conversion")
    for planned in plan["runs"]:
        run = safe(workspace, planned["run"])
        _verify_run(run, planned)
    output.mkdir(parents=True, exist_ok=False)
    for name, data in blobs.items():
        file = output / name
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("xb") as stream:
            stream.write(data)
    raw = canonical(manifest) + b"\n"
    (output / "manifest.json").write_bytes(raw)
    (output / "manifest.sha256").write_text(sha(raw) + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path, default=ROOT / "experiments/agent-samples-v1/source-plan.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--purpose", choices=("development", "training"), default="development")
    args = parser.parse_args(argv)
    result = export_dataset(args.source_plan, args.output, purpose=args.purpose)
    print(json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
