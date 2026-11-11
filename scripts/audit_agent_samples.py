"""Independent, read-only audit of the W3 development decision export.

Uses only the standard library and never imports the converter, a provider SDK,
the simulator, or a pickle. The external source plan is the trust anchor. This
auditor cannot approve training data, model quality, or a student's tokenizer.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "experiments/agent-samples-v1/source-plan.json"
MARKER = "\n\n[M1-R task evidence; derived from actual tool receipts]\n"
REASONING = {"reasoning_content", "reasoning", "thinking"}
VERSION = "independent-agent-sample-audit-v1"


class AuditError(ValueError):
    pass


def demand(value, reason):
    if not value:
        raise AuditError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def parse(raw):
    def unique(items):
        result = {}
        for key, value in items:
            demand(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(AuditError("nonfinite_json")))


def file_at(root, name):
    root, relative = Path(root), Path(name)
    demand(not relative.is_absolute() and ".." not in relative.parts, "unsafe_relative_path")
    path = root / relative
    demand(not any(p.is_symlink() for p in (path, *path.parents)), "symlink_source")
    demand(path.resolve().is_relative_to(root.resolve()), "escaped_source")
    return path


def read(root, name):
    return parse(file_at(root, name).read_bytes())


def lines(raw):
    demand(not raw or raw.endswith(b"\n"), "unterminated_jsonl")
    return [parse(line) for line in raw.splitlines()]


def hashes(root, inventory):
    demand(isinstance(inventory, dict) and inventory, "empty_file_inventory")
    for name, expected in inventory.items():
        demand(digest(file_at(root, name).read_bytes()) == expected, "file_hash_mismatch:" + name)


def blob(record):
    raw = base64.b64decode(record["base64"], validate=True)
    demand(type(record["length"]) is int and len(raw) == record["length"] and digest(raw) == record["sha256"], "wire_blob_mismatch")
    return raw


def wire_calls(message):
    calls = message.get("tool_calls", [])
    demand(isinstance(calls, list), "invalid_wire_tool_list")
    normalized = []
    for call in calls:
        demand(isinstance(call, dict) and set(call) <= {"id", "type", "function", "index"}, "unknown_call_field")
        function = call.get("function", {})
        demand(call.get("type") == "function" and isinstance(call.get("id"), str) and call["id"], "invalid_call_identity")
        demand(set(function) == {"name", "arguments"} and isinstance(function["name"], str)
               and isinstance(function["arguments"], str), "invalid_function")
        arguments = parse(function["arguments"])
        demand(isinstance(arguments, dict), "non_object_arguments")
        normalized.append((call["id"], function["name"], arguments))
    return normalized


def visible_message(message, location, removed):
    """Only remove provider reasoning fields, never parse business JSON strings."""
    demand(isinstance(message, dict), "message_not_object")
    demand(message.get("role") in {"system", "developer", "user", "assistant", "tool"}, "unsupported_role")
    demand(set(message) <= {"role", "content", "tool_calls", "tool_call_id", "name", "refusal"} | REASONING,
           "unsupported_message_field")
    result = {}
    for key, value in message.items():
        if key in REASONING:
            removed.append({"path": location + "." + key, "sha256": digest(canonical(value)),
                            "characters": len(value) if isinstance(value, str) else None})
        elif key == "refusal":
            demand(value is None, "nonempty_refusal")
        else:
            result[key] = value
    if "tool_calls" in result:
        wire_calls(result)
        result["tool_calls"] = [{key: call[key] for key in ("id", "type", "function")} for call in result["tool_calls"]]
    content = result.get("content")
    texts = []
    if isinstance(content, list):
        retained = []
        for index, item in enumerate(content):
            demand(isinstance(item, dict), "unsupported_content_block")
            if item.get("type") in REASONING:
                raw = canonical(item)
                removed.append({"path": f"{location}.content[{index}]", "sha256": digest(raw), "characters": len(raw.decode())})
            else:
                demand(set(item) == {"type", "text"} and item["type"] == "text" and isinstance(item["text"], str),
                       "unsupported_text_block")
                texts.append(item["text"])
                retained.append(item)
        result["content"] = retained
    else:
        demand(content is None or isinstance(content, str), "unsupported_content")
        texts.append(content or "")
    if message["role"] == "assistant":
        demand(not any(re.search(r"</?(?:think|thinking|reasoning)(?:\s|>)", text, re.I) for text in texts),
               "reasoning_tags_in_assistant_content")
    return result


def tool_feedback(call, end, public_events):
    ident, name, _ = call
    output = end["output"]
    reasons = set()
    if output.get("status") == "error":
        reasons.add("tool_message_error")
    try:
        payload = parse(output["content"]) if isinstance(output.get("content"), str) else output.get("content")
    except (ValueError, TypeError):
        payload = None
    pending = [payload]
    failed = {"error", "failed", "failure", "validation_error", "blocked", "rejected", "unsupported", "partial", "partial_success", "unknown"}
    while pending:
        item = pending.pop()
        if not isinstance(item, dict):
            continue
        for field in ("status", "outcome"):
            if isinstance(item.get(field), str) and item[field] in failed:
                reasons.add(f"receipt_{field}_{item[field]}")
        if item.get("success") is False or item.get("partial_success") is True:
            reasons.add("receipt_not_complete_success")
        pending.extend(item.get(field) for field in ("navigation_result", "move_result"))
    for event in public_events:
        if event.get("tool_call_id") == ident and event.get("status") in {"error", "blocked", "partial", "unknown"}:
            reasons.add("public_ledger_" + event["status"])
    return {"tool_call_id": ident, "name": name, "status": "excluded_target" if reasons else "returned", "reasons": sorted(reasons)}


def inspect_archive(raw, injections=(), public_events=()):
    """Reconstruct ordered wire decisions and independently join receipts by ID."""
    rows = lines(raw)
    demand(rows and rows[0]["kind"] == "archive_start" and rows[-1]["kind"] == "archive_end", "archive_boundaries")
    owner = {key: rows[0][key] for key in ("archive_id", "episode_id", "command_id")}
    demand(owner["episode_id"] == owner["archive_id"], "archive_episode_identity")
    models, attempts, spans = {}, {}, {}
    for seq, row in enumerate(rows, 1):
        demand(row.get("schema_version") == 1 and row["sequence"] == seq and all(row.get(k) == v for k, v in owner.items()),
               "archive_sequence_or_identity")
        kind, data = row["kind"], row["data"]
        if kind == "archive_start":
            demand(seq == 1, "repeated_archive_start")
        elif kind == "archive_end":
            demand(seq == len(rows) and data.get("complete") is True and not data.get("reasons") and not data.get("error_types"),
                   "archive_not_complete")
        elif kind == "model_start":
            ident = data["logical_call_id"]
            demand(ident not in models, "repeated_model_id")
            models[ident] = {"id": ident, "start_sequence": seq, "attempt_ids": [], "end": None}
        elif kind == "model_end":
            model = models[data["logical_call_id"]]
            demand(model["end"] is None and data.get("status") == "returned" and not data.get("error_type"), "model_not_returned")
            model.update(end=data, end_sequence=seq)
        elif kind == "http_request":
            ident, logical = data["attempt_id"], data["logical_call_id"]
            demand(ident not in attempts and logical in models and models[logical]["end"] is None, "request_ownership")
            demand(data.get("body_scope") == "serialized_http_request_content" and not data.get("error_type"), "unsupported_request_capture")
            request = blob(data["body"])
            attempts[ident] = {"id": ident, "logical": logical, "request": parse(request), "request_body_sha256": digest(request),
                               "request_sequence": seq, "start": None, "parts": [], "end": None}
            models[logical]["attempt_ids"].append(ident)
        elif kind.startswith("http_response_"):
            attempt = attempts[data["attempt_id"]]
            demand(attempt["logical"] == data["logical_call_id"] and attempt["end"] is None, "response_ownership")
            if kind == "http_response_start":
                demand(attempt["start"] is None and 200 <= data.get("status_code", 0) < 300, "http_not_success")
                demand(data.get("body_scope") == "buffered_httpx_content" or
                       (data.get("body_scope") == "httpx_stream_before_content_decoding" and data.get("content_encoding") == "identity"),
                       "unsupported_wire_encoding")
                attempt["start"] = data
            elif kind == "http_response_chunk":
                demand(attempt["start"] is not None and data["chunk_index"] == len(attempt["parts"]) + 1, "chunk_order")
                attempt["parts"].append(blob(data["body"]))
            elif kind == "http_response_end":
                body = b"".join(attempt["parts"])
                demand(attempt["start"] is not None and data.get("status") == "complete" and data.get("complete") is True
                       and data.get("response_observed") is True and not data.get("error_type"), "incomplete_response")
                demand((data["length"], data["sha256"], data["chunk_count"]) == (len(body), digest(body), len(attempt["parts"])), "response_hash")
                attempt.update(end=data, end_sequence=seq, response=parse(body), response_body_sha256=digest(body))
            else:
                raise AuditError("unsupported_response_event")
        elif kind == "tool_start":
            ident = data["tool_span_id"]
            demand(ident not in spans and data.get("source_relation") == "unique"
                   and data.get("source_candidates") == [data.get("source_model_id")], "tool_source_ambiguous")
            spans[ident] = {"id": ident, "start": data, "start_sequence": seq, "end": None}
        elif kind == "tool_end":
            span = spans[data["tool_span_id"]]
            demand(span["end"] is None and data.get("status") == "returned" and not data.get("error_type")
                   and data.get("tool_call_id_mismatch") is False, "tool_not_closed")
            span.update(end=data, end_sequence=seq)
        else:
            raise AuditError("unsupported_archive_event:" + kind)
    demand(models, "no_decisions")
    all_calls = {}
    for model in models.values():
        demand(model["end"] and len(model["attempt_ids"]) == 1, "multiple_or_missing_attempts")
        attempt = attempts[model["attempt_ids"][0]]
        demand(attempt["end"] and attempt["end_sequence"] < model["end_sequence"], "response_after_handler")
        request, response = attempt["request"], attempt["response"]
        demand(request.get("stream") is False and isinstance(request.get("messages"), list), "unsupported_wire_protocol")
        choices = response.get("choices")
        demand(isinstance(choices, list) and len(choices) == 1 and choices[0].get("index") == 0, "ambiguous_choice")
        choice = choices[0]
        target = choice["message"]
        demand(target.get("role") == "assistant" and not target.get("refusal"), "unsupported_target")
        calls = wire_calls(target)
        demand(choice.get("finish_reason") == ("tool_calls" if calls else "stop") and (calls or target.get("content")), "unfinished_target")
        output = model["end"]["output"]
        demand(output.get("structured_response") is None, "structured_response_not_supported")
        result = output.get("result")
        demand(isinstance(result, list) and len(result) == 1 and result[0].get("type") == "ai"
               and not result[0].get("invalid_tool_calls"), "invalid_handler_output")
        structured = result[0]
        if structured.get("additional_kwargs", {}).get("tool_calls"):
            handler_calls = wire_calls(structured["additional_kwargs"])
        else:
            handler_calls = [(item["id"], item["name"], item["args"]) for item in structured.get("tool_calls", [])]
        demand(calls == handler_calls and (target.get("content") or "") == (structured.get("content") or ""), "wire_handler_disagreement")
        tools = request.get("tools", [])
        demand(isinstance(tools, list), "invalid_tool_schemas")
        names = [tool["function"]["name"] for tool in tools if tool.get("type") == "function"]
        demand(len(names) == len(tools) == len(set(names)), "invalid_or_duplicate_tool_schema")
        for call in calls:
            demand(call[0] not in all_calls and call[1] in names, "duplicate_or_unadvertised_tool")
            all_calls[call[0]] = {"model": model, "call": call, "span": None}
        model.update(attempt=attempt, target=target, calls=calls, finish_reason=choice["finish_reason"])
    for span in spans.values():
        start, end = span["start"], span["end"]
        ident = start["raw_tool_call_id"]
        demand(ident in all_calls and end is not None, "orphan_receipt")
        linked = all_calls[ident]
        model, call = linked["model"], linked["call"]
        demand(linked["span"] is None, "duplicate_tool_execution")
        demand(start["source_model_id"] == model["id"] and span["start_sequence"] > model["end_sequence"], "tool_model_mismatch")
        received = start["input"]
        demand((received.get("id"), received.get("name"), received.get("args")) == call, "tool_arguments_mismatch")
        receipt = end["output"]
        demand(receipt.get("type") == "tool" and receipt.get("tool_call_id") == ident
               and receipt.get("name") in (None, call[1]), "tool_receipt_mismatch")
        demand(receipt.get("status") in {"success", "error"}, "unknown_tool_message_status")
        span.update(call=call, feedback=tool_feedback(call, end, public_events))
        linked["span"] = span
    demand(all(item["span"] for item in all_calls.values()), "unexecuted_tool_call")
    for model in models.values():
        history_calls, replied, proofs = set(), set(), []
        attempt = model["attempt"]
        for message in attempt["request"]["messages"]:
            if message.get("role") == "assistant":
                for call in wire_calls(message):
                    demand(call[0] in all_calls and call == all_calls[call[0]]["call"] and call[0] not in history_calls
                           and all_calls[call[0]]["model"]["end_sequence"] < model["start_sequence"], "history_call_not_prior_wire")
                    history_calls.add(call[0])
            if message.get("role") == "tool":
                ident = message.get("tool_call_id")
                demand(ident in history_calls and ident not in replied, "unmatched_history_receipt")
                linked = all_calls[ident]
                span = linked["span"]
                demand(span["end_sequence"] < attempt["request_sequence"] and message.get("name") in (None, linked["call"][1]), "history_receipt_order")
                raw_content, content = span["end"]["output"].get("content"), message.get("content")
                proof = {"tool_call_id": ident, "raw_content_sha256": digest(canonical(raw_content)),
                         "wire_content_sha256": digest(canonical(content)), "transformation": "none"}
                if raw_content != content:
                    demand(isinstance(raw_content, str) and isinstance(content, str) and content.startswith(raw_content), "history_receipt_content")
                    suffix = content[len(raw_content):]
                    matching = [item for item in injections if item.get("tool_call_id") == ident and item.get("text") == suffix]
                    demand(suffix.startswith(MARKER) and len(matching) == 1, "unproven_advisory")
                    proof.update(transformation="sealed_public_evidence_suffix", suffix_sha256=digest(suffix.encode()),
                                 injection_message_index=matching[0].get("message_index"), injection_segment=matching[0].get("segment"))
                proofs.append(proof)
                replied.add(ident)
        demand(replied == history_calls, "missing_history_receipt")
        removed = []
        messages = [visible_message(message, f"request.messages[{index}]", removed)
                    for index, message in enumerate(attempt["request"]["messages"])]
        target = visible_message(model["target"], "response.message", removed)
        demand(set(target) <= {"role", "content", "tool_calls"}, "unsupported_target_field")
        tool_spans = [all_calls[call[0]]["span"] for call in model["calls"]]
        links = [{"tool_call_id": span["call"][0], "tool_span_id": span["id"], "source_model_id": model["id"],
                  "name": span["call"][1], "arguments_sha256": digest(canonical(span["call"][2])),
                  "receipt_content_sha256": digest(canonical(span["end"]["output"].get("content"))), "feedback": span["feedback"]}
                 for span in tool_spans]
        model.update(messages=messages + [target], removed_reasoning=removed, tool_links=links,
                     history_receipt_proofs=proofs, tool_feedback=[span["feedback"] for span in tool_spans])
    return owner, list(models.values())


def episode_gate(run, trial, result, worker, score):
    """A final assistant assertion never substitutes for an independent score."""
    ident = trial["trial_id"]
    folder = file_at(run, "trials/" + ident)
    for kind, report in (("worker", worker), ("score", score)):
        processes = [p for p in result.get("processes", []) if p.get("mode") == kind]
        demand(len(processes) == 1, "missing_independent_process")
        process = processes[0]
        demand(process.get("trial_id") == ident and process.get("pid") == report["pid"] and process.get("exited") is True
               and process.get("returncode") == 0 and process.get("kill_reason") is None, "unclean_process_exit")
    demand(worker.get("status") == "completed" and worker["trial_id"] == score["trial_id"] == ident
           and worker["task_id"] == trial["task_id"], "worker_task_mismatch")
    demand(score.get("worker_pid") == worker["pid"] and score["pid"] != worker["pid"], "scorer_not_independent")
    limits, agent = worker.get("limits", {}), worker.get("agent", {})
    demand(worker.get("drained") is True and not limits.get("threads_still_alive") and not limits.get("needs_process_termination"), "undrained_episode")
    demand(limits.get("unknown_usage_attempts", 0) == 0 and not limits.get("unknown_usage_attempt_ids"), "unknown_usage")
    demand(agent.get("success") is True and not agent.get("error_type") and limits.get("stop_reason") is None
           and not worker.get("lifecycle", {}).get("cancelled"), "abnormal_episode")
    demand(agent.get("archive_verify", {}).get("valid") is True and agent["archive_verify"].get("complete") is True, "archive_gate_failed")
    for group in ("worker-exit-seals", "scorer-exit-seals"):
        hashes(folder, read(run, f"{group}/{ident}.json"))
    demand(score.get("worker_result_sha256") == digest(file_at(folder, "worker-result.json").read_bytes())
           and score.get("snapshot_sha256") == worker.get("snapshot", {}).get("sha256"), "score_binding")
    demand(digest(file_at(folder, worker["snapshot"]["path"]).read_bytes()) == score["snapshot_sha256"], "snapshot_file_binding")
    demand(score.get("checker_tree_matches_original") is True and score.get("agent_imported") is False and score.get("network_attempts") == [], "invalid_scorer")
    checker = score.get("checker", {})
    demand(checker.get("passed") is True and not checker.get("errors") and type(checker.get("checks_expected")) is int
           and checker["checks_expected"] > 0 and checker.get("checks_passed") == checker["checks_expected"], "checker_not_passed")
    demand(worker.get("isolation", {}).get("canary_absent") is True and worker["isolation"].get("checker_definitions_absent") is True, "private_contamination")


def _key(row):
    return row["run"], row["trial_id"]


def _audit(dataset, source_plan, workspace, checks):
    dataset, source_plan, workspace = Path(dataset), Path(source_plan), Path(workspace)
    plan_raw = source_plan.read_bytes()
    demand(digest(plan_raw) == source_plan.with_suffix(".sha256").read_text().strip(), "source_plan_sidecar")
    plan = parse(plan_raw)
    demand(plan.get("historical_source_role") == "development" and plan.get("provider_calls_authorized") == 0
           and plan.get("teacher_training_examples_expected") == 0, "plan_not_development")
    demand(len({r["run"] for r in plan["runs"]}) == len(plan["runs"]), "duplicate_planned_run")
    manifest_raw = file_at(dataset, "manifest.json").read_bytes()
    demand(digest(manifest_raw) == file_at(dataset, "manifest.sha256").read_text().strip(), "manifest_sidecar")
    manifest = parse(manifest_raw)
    demand(manifest.get("schema") == "agent-sample-export-v1" and manifest.get("converter_version") == "agent-decision-converter-v1"
           and manifest.get("purpose") == "development" and manifest.get("teacher_eligible") is False
           and type(manifest.get("true_teacher_samples")) is int and manifest["true_teacher_samples"] == 0, "training_promotion_forbidden")
    demand(manifest.get("source_plan_sha256") == digest(plan_raw), "export_source_plan_mismatch")
    hashes(dataset, manifest["files"])
    actual = {str(p.relative_to(dataset)) for p in dataset.rglob("*") if p.is_file()}
    demand(actual == set(manifest["files"]) | {"manifest.json", "manifest.sha256"}, "export_inventory")
    for name, expected in manifest["sources"].items():
        demand(manifest["files"].get("source/" + name) == expected, "source_copy_hash")
    demand(file_at(dataset, "source/experiments/agent-samples-v1/source-plan.json").read_bytes() == plan_raw, "source_plan_copy")
    policies = manifest["policies"]
    demand(policies.get("split") == "development" and policies.get("episode_success_required") is True
           and policies.get("single_attempt_required") is True and policies.get("failed_tool_decision_excluded") is True
           and policies.get("recovery_history_retained_masked") is True and policies.get("source_internal_api_actions_are_labels") is False
           and policies.get("production_training_export_supported") is False, "unsafe_policy")
    data = {name: lines(file_at(dataset, name + ".jsonl").read_bytes()) for name in ("samples", "episodes", "decision_inventory", "exclusions")}
    episodes = {_key(row): row for row in data["episodes"]}
    decisions = {(*_key(row), row["logical_call_id"]): row for row in data["decision_inventory"]}
    samples = {row["sample_id"]: row for row in data["samples"]}
    demand(len(episodes) == len(data["episodes"]) and len(decisions) == len(data["decision_inventory"])
           and len(samples) == len(data["samples"]), "duplicate_export_identity")
    exported_ids, expected_episode_keys, expected_decision_keys = set(), set(), set()
    expected_sources, summaries = [], []
    reasoning_count = 0
    for planned in plan["runs"]:
        run = file_at(workspace, planned["run"])
        demand(planned.get("purpose") == "development_format_and_input_validation_only", "source_not_development")
        demand(digest(file_at(run, "manifest.json").read_bytes()) == planned["manifest_sha256"]
               and digest(file_at(run, "campaign-seal.json").read_bytes()) == planned["campaign_seal_sha256"], "source_pin")
        source_manifest, seal = read(run, "manifest.json"), read(run, "campaign-seal.json")
        hashes(run, seal["files"])
        actual = {str(p.relative_to(run)) for p in run.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != "campaign-seal.json"}
        demand(actual == set(seal["files"]), "source_run_inventory")
        scope = source_manifest.get("scope")
        if scope == "deepseek_behavior_pilot_v1":
            demand(source_manifest.get("local_smoke") is False, "provider_smoke_not_real")
            source_kind = "real_provider_development"
        else:
            demand(scope == "general_agent_v2_execution_contract" and source_manifest.get("online_authorized_executions") == 0, "unknown_run_scope")
            source_kind = "synthetic_sdk_development"
        trials = source_manifest["trials"]
        demand(len(trials) == planned["planned_trials"] and len({t["trial_id"] for t in trials}) == len(trials), "source_trial_denominator")
        results_rows = read(run, "results.json")
        results = {r["trial_id"]: r for r in results_rows}
        demand(len(results) == len(results_rows) and set(results) <= {t["trial_id"] for t in trials}, "source_result_identity")
        expected_sources.append({**planned, "source_kind": source_kind, "split": "development"})
        checks.append({"check": "source_seal_and_inventory", "run": planned["run"], "passed": True, "files": len(actual)})
        for trial in trials:
            key = planned["run"], trial["trial_id"]
            expected_episode_keys.add(key)
            demand(key in episodes, "missing_planned_episode")
            episode = episodes[key]
            demand(episode.get("run_manifest_sha256") == planned["manifest_sha256"] and episode.get("task_id") == trial["task_id"]
                   and episode.get("source_kind") == source_kind and episode.get("split") == "development"
                   and episode.get("teacher_eligible") is False, "episode_source_or_split")
            folder = file_at(run, "trials/" + trial["trial_id"])
            archives = sorted(folder.glob("policy/modelio/*.jsonl")) if folder.exists() else []
            rows, raw = [], None
            if len(archives) == 1:
                raw = archives[0].read_bytes()
                rows = lines(raw)
                demand(episode.get("archive_file") == str(archives[0].relative_to(run)) and episode.get("archive_sha256") == digest(raw)
                       and episode.get("archive_id") == rows[0]["archive_id"], "episode_archive_binding")
            else:
                demand(episode.get("archive_file") is None and episode.get("archive_sha256") is None, "unexpected_archive_reference")
            starts = [row["data"]["logical_call_id"] for row in rows if row["kind"] == "model_start"]
            demand(len(set(starts)) == len(starts) and episode.get("logical_decisions") == len(starts), "logical_denominator")
            for logical in starts:
                expected_decision_keys.add((*key, logical))
            models, owner, failure = None, None, None
            if trial["trial_id"] not in results:
                status, failure = "unrun", "planned_not_run"
            else:
                status = "excluded"
                try:
                    worker, score = read(folder, "worker-result.json"), read(folder, "evaluation/result.json")
                    episode_gate(run, trial, results[trial["trial_id"]], worker, score)
                    demand(raw is not None, "missing_archive")
                    ledger = read(folder, "policy/task-state.json")
                    owner, models = inspect_archive(raw, ledger.get("task_evidence", {}).get("injections", []), ledger.get("events", []))
                    demand(owner["command_id"] == worker["entry"]["command_id"] and owner["archive_id"] == worker["agent"]["archive_info"]["archive_id"], "archive_worker_binding")
                    fixture = read(run, "inputs/fixtures/" + trial["task_id"] + ".json")
                    demand(ledger.get("command") == fixture.get("command"), "task_command_binding")
                    status = "eligible"
                except (AuditError, KeyError, ValueError, TypeError, OSError) as exc:
                    failure = str(exc) if isinstance(exc, AuditError) else type(exc).__name__
            demand(episode.get("status") == status, "episode_eligibility_disagreement:" + trial["trial_id"])
            local_count = 0
            for logical in starts:
                decision = decisions.get((*key, logical))
                demand(decision is not None and decision.get("task_id") == trial["task_id"] and decision.get("archive_id") == episode["archive_id"], "missing_or_wrong_decision")
                if status != "eligible":
                    demand(decision.get("status") == "excluded" and decision.get("sample_id") is None and decision.get("reason"), "excluded_episode_labeled")
                    continue
                model = next(model for model in models if model["id"] == logical)
                attempt = model["attempt"]
                demand(decision.get("tool_feedback") == model["tool_feedback"] and decision.get("attempt_id") == attempt["id"]
                       and all(decision.get(field) == attempt[field] for field in ("request_body_sha256", "response_body_sha256")), "decision_wire_or_feedback")
                failed_tool = any(item["status"] == "excluded_target" for item in model["tool_feedback"])
                if failed_tool:
                    demand(decision.get("status") == "excluded" and decision.get("sample_id") is None
                           and decision.get("reason") == "current_decision_has_failed_partial_or_unknown_tool", "failed_tool_labeled")
                    continue
                demand(decision.get("status") == "exported" and decision.get("reason") is None, "eligible_decision_omitted")
                sample_id = decision["sample_id"]
                demand(sample_id in samples and sample_id not in exported_ids, "missing_or_reused_sample")
                exported_ids.add(sample_id)
                sample = samples[sample_id]
                demand(set(sample) == {"schema", "sample_id", "source", "teacher_eligible", "messages", "tools", "supervision", "audit"}, "unexpected_sample_field")
                demand(sample.get("schema") == "agent-decision-sample-v1" and sample.get("teacher_eligible") is False, "sample_training_promotion")
                expected_source = {"run": key[0], "trial_id": key[1], "task_id": trial["task_id"], "run_manifest_sha256": planned["manifest_sha256"],
                    "scene_id": fixture.get("scene_id"), "archive_file": episode["archive_file"], "archive_sha256": digest(raw),
                    "source_kind": source_kind, "split": "development", **owner, "logical_call_id": logical,
                    "attempt_id": attempt["id"], "request_sequence": attempt["request_sequence"], "response_id": attempt["response"].get("id"),
                    "request_body_sha256": attempt["request_body_sha256"], "response_body_sha256": attempt["response_body_sha256"],
                    "model": attempt["request"].get("model"), "harness_config": {
                        "arm": trial.get("arm", "integrated"),
                        "task_progress_mode": trial.get("task_progress_mode", source_manifest.get("agent_profile", {}).get("agent", {}).get("task_progress_mode")),
                        "harness_revision": trial.get("harness_revision", source_manifest.get("agent_profile", {}).get("harness", {}).get("revision")),
                        "scheduler": trial.get("scheduler", source_manifest.get("agent_profile", {}).get("harness", {}).get("scheduler"))}}
                demand(sample.get("source") == expected_source, "sample_source_identity")
                identity = {field: expected_source[field] for field in ("run_manifest_sha256", "trial_id", "archive_sha256", "logical_call_id", "attempt_id")}
                demand(sample_id == digest(canonical(identity)), "sample_id_hash")
                demand(sample.get("messages") == model["messages"] and sample.get("tools") == attempt["request"].get("tools", []), "sample_not_actual_wire")
                target_index = len(model["messages"]) - 1
                demand(isinstance(sample.get("supervision"), dict)
                       and isinstance(sample["supervision"].get("message_loss_mask"), list)
                       and all(type(value) is int for value in sample["supervision"]["message_loss_mask"])
                       and type(sample["supervision"].get("target_message_index")) is int, "loss_mask_integer_contract")
                demand(sample.get("supervision") == {"message_loss_mask": [0] * target_index + [1], "target_message_index": target_index,
                       "supervised_fields": ["content", "tool_calls"], "reasoning_supervised": False}, "loss_mask_or_label_scope")
                demand(sample.get("audit") == {"removed_reasoning": model["removed_reasoning"], "tool_links": model["tool_links"],
                       "finish_reason": model["finish_reason"], "history_receipt_proofs": model["history_receipt_proofs"]}, "audit_proof_mismatch")
                reasoning_count += len(model["removed_reasoning"])
                local_count += 1
            demand(episode.get("exported_samples") == local_count, "episode_sample_denominator")
            demand((episode.get("reason") is None) == (status == "eligible"), "episode_reason_missing")
            summaries.append({"run": key[0], "trial_id": key[1], "status": status, "logical_decisions": len(starts),
                              "exported_samples": local_count, "independent_exclusion": failure})
            checks.append({"check": "episode_inventory_wire_labels", "run": key[0], "trial_id": key[1], "passed": True})
    demand(set(episodes) == expected_episode_keys and set(decisions) == expected_decision_keys and set(samples) == exported_ids, "extra_or_missing_denominator_member")
    demand(manifest["source_runs"] == expected_sources, "source_run_inventory_metadata")
    expected_exclusions = [{"level": "episode", **row} for row in data["episodes"] if row["status"] != "eligible"]
    expected_exclusions += [{"level": "decision", **row} for row in data["decision_inventory"] if row["status"] != "exported"]
    demand(data["exclusions"] == expected_exclusions, "exclusions_inventory")
    counts = {"planned_episodes": len(episodes), "eligible_episodes": sum(row["status"] == "eligible" for row in episodes.values()),
              "excluded_episodes": sum(row["status"] == "excluded" for row in episodes.values()), "unrun_episodes": sum(row["status"] == "unrun" for row in episodes.values()),
              "logical_decisions": len(decisions), "exported_samples": len(samples), "excluded_decisions": sum(row["status"] == "excluded" for row in decisions.values())}
    demand(counts["planned_episodes"] == plan["planned_trials"] and manifest["counts"] == counts
           and all(type(value) is int for value in manifest["counts"].values()), "aggregate_denominator")
    demand(manifest.get("exclusion_reason_counts") == dict(Counter(row["reason"] for row in decisions.values() if row["reason"])), "exclusion_reason_counts")
    return {"counts": counts, "episodes": summaries, "reasoning_removal_records": reasoning_count,
            "true_teacher_samples": 0, "manifest_sha256": digest(manifest_raw), "source_plan_sha256": digest(plan_raw)}


def audit_export(dataset, *, source_plan=PLAN, workspace=ROOT):
    checks = []
    report = {"schema": "agent-sample-independent-audit-v1", "auditor_version": VERSION, "passed": False, "checks": checks,
              "limits": ["development evidence only; no Teacher approval", "no model/tokenizer/provider execution", "private scoring metadata is audited, never converted into labels"]}
    try:
        report.update(_audit(dataset, source_plan, workspace, checks))
        report["passed"] = True
    except (AuditError, KeyError, ValueError, TypeError, OSError, IndexError) as exc:
        report["error"] = str(exc) if isinstance(exc, AuditError) else type(exc).__name__
        checks.append({"check": "audit_failed", "passed": False, "reason": report["error"]})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--source-plan", type=Path, default=PLAN)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output:
        demand(not args.output.resolve().is_relative_to(args.dataset.resolve()), "audit_output_inside_export")
        plan = parse(args.source_plan.read_bytes())
        demand(all(not args.output.resolve().is_relative_to((args.workspace / item["run"]).resolve()) for item in plan["runs"]), "audit_output_inside_source_run")
        demand(not args.output.exists(), "audit_output_exists")
    report = audit_export(args.dataset, source_plan=args.source_plan, workspace=args.workspace)
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
    print(raw)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
