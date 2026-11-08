"""Authenticate the four pinned development runs and project each accepted request."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from convert_agent_samples import MARKER, Excluded, _calls, canonical, decode, parse, require, safe, sha

BATCHES = (
    "deepseek-behavior-pilot-20261102",
    "deepseek-contract-pilot-20261103",
    "deepseek-cost-pilot-20261103",
    "deepseek-hover-pilot-20261103",
)
SEAL_DIGEST = "112b0e62173bb16b4605d9385b6ef175db3d1d73f4366e8911ad423b911f458b"
IDENTITY = ("archive_id", "episode_id", "command_id")


def _sealed(root, name, inventory):
    require(name in inventory, f"not_sealed: {name}")
    raw = safe(root, name).read_bytes()
    require(sha(raw) == inventory[name], f"seal_hash_mismatch: {name}")
    return raw


def verify_sources(root):
    """Return authenticated archive sources and a portable inventory of all planned slots."""
    root = Path(root).resolve()
    runs = {batch: safe(root, f"docs/results/{batch}/run-01") for batch in BATCHES}
    seals = {batch: safe(run, "campaign-seal.json").read_bytes() for batch, run in runs.items()}
    digests = {batch: sha(raw) for batch, raw in seals.items()}
    combined = sha("".join(f"{digest}\n" for digest in digests.values()).encode("ascii"))
    require(combined == SEAL_DIGEST, "campaign_seals_pin_mismatch")
    slots, sources = [], []
    for batch, run in runs.items():
        files = parse(seals[batch])["files"]
        trials = parse(_sealed(run, "manifest.json", files))["trials"]
        planned = [trial["trial_id"] for trial in trials]
        require(len(set(planned)) == len(planned), "duplicate_manifest_trial")
        recorded = {p.stem for p in (run / "worker-exit-seals").glob("*.json")}
        sealed_workers = {Path(k).stem for k in files if k.startswith("worker-exit-seals/")}
        require(recorded == sealed_workers and recorded <= set(planned), "worker_inventory_mismatch")
        # Only exit metadata is read here. Score snapshots, checkers and terminal ledgers
        # have no role in source authentication or in the event projection.
        known_archives = set()
        for trial in trials:
            name = trial["trial_id"]
            slot = {
                "batch": batch, "episode": name, "task_id": trial["task_id"],
                "arm": trial["arm"], "scenario": trial.get("condition", trial["task_id"]),
                "condition": "injected" if trial.get("api_fault") else "natural",
            }
            slots.append(slot)
            if name not in recorded:
                slot["status"] = "unrun"
                continue
            seal_name = f"worker-exit-seals/{name}.json"
            worker_files = parse(_sealed(run, seal_name, files))
            folder = safe(run, f"trials/{name}")
            archives = [p for p in worker_files if Path(p).match("policy/modelio/model-io-*.jsonl")]
            require(len(archives) == 1, f"missing_or_ambiguous_archive: {name}")
            rel = archives[0]
            archive = safe(folder, rel)
            for path in (rel, "worker-result.json"):
                require(worker_files[path] == files.get(f"trials/{name}/{path}"), f"worker_campaign_hash_mismatch: {name}/{path}")
            worker = parse(_sealed(folder, "worker-result.json", worker_files))
            require(worker["trial_id"] == name and worker["task_id"] == slot["task_id"], f"worker_owner_mismatch: {name}")
            owner = worker["agent"]["archive_info"]
            require(worker["entry"]["command_id"] == owner["command_id"] and archive.name == f"model-io-{owner['archive_id']}.jsonl", "worker_archive_owner_mismatch")
            require(owner["closed"] and worker["agent"]["archive_verify"]["valid"], "unverified_worker_archive")
            require(sha(archive.read_bytes()) == worker_files[rel], f"archive_hash_mismatch: {name}/{rel}")
            known_archives.add(str(archive.relative_to(run)))
            slot.update(status="archived", archive_path=str(archive.relative_to(root)),
                        archive_sha256=worker_files[rel], worker_seal_sha256=files[seal_name],
                        **{k: owner[k] for k in IDENTITY})
            sources.append({**slot, "path": archive})
        actual = {str(p.relative_to(run)) for p in run.glob("trials/*/policy/modelio/model-io-*.jsonl")}
        sealed = {key for key in files if Path(key).match("trials/*/policy/modelio/model-io-*.jsonl")}
        require(known_archives == actual == sealed, "archive_inventory_mismatch")
    return sources, {"campaign_seals_sha256": combined, "campaign_seals": digests, "slots": slots}


def _check_content(raw, wire):
    """Accept only identical content or the exact public M1-R serializer's framing.

    The suffix itself never supplies evidence. Its bytes are authenticated by the wire
    seal, and the retained portion must independently match the original receipt.
    """
    if raw == wire:
        return "none"
    if not isinstance(raw, str) or not isinstance(wire, str) or not wire.startswith(raw + MARKER):
        return None
    suffix = wire[len(raw + MARKER):]
    try:
        obj = parse(suffix)
    except ValueError:
        return None
    if not isinstance(obj, dict) or canonical(obj).decode() != suffix:
        return None
    fields = {"schema", "items", "mission_completion", "omitted", "original_task_controls"}
    if obj.get("schema") == "m1-r2":
        fields.update(("aggregate_omitted", "aggregate_requirements"))
        if not isinstance(obj.get("aggregate_requirements"), list) or type(obj.get("aggregate_omitted")) is not int:
            return None
    elif obj.get("schema") != "m1-r1":
        return None
    if (fields != set(obj) or not isinstance(obj["items"], list) or type(obj["omitted"]) is not int
            or obj["original_task_controls"] is not True or obj["mission_completion"] != "not_evaluated"):
        return None
    return obj["schema"]


def _payload(content):
    if not isinstance(content, str):
        return content
    try:
        return parse(content)
    except json.JSONDecodeError:
        # Validation errors and transport timeouts can be genuine public text.
        return content


def _receipt(call, candidates, model, cutoff, wire, wire_index):
    ident = call["id"]
    # Count only spans already started at the decision boundary.
    spans = [span for span in candidates if span["start_sequence"] < cutoff]
    require(len(spans) == 1, "missing_or_duplicate_span")
    span = spans[0]
    start, end = span["start"], span.get("end", {})
    require(model["end_sequence"] < span["start_sequence"] and span.get("end_sequence", cutoff) < cutoff,
            "receipt_outside_decision_boundary")
    require(start.get("source_relation") == "unique" and start.get("source_model_id") == model["logical_call_id"]
            and start.get("source_candidates") == [model["logical_call_id"]], "ambiguous_receipt_owner")
    given = start.get("input", {})
    require((given.get("id"), given.get("name"), given.get("args")) == (ident, call["name"], call["args"]), "span_call_mismatch")
    out = end.get("output", {})
    require(end.get("status") == "returned" and not end.get("error_type") and end.get("tool_call_id_mismatch") is False
            and out.get("type") == "tool" and out.get("tool_call_id") == ident and out.get("status") in {"success", "error"}
            and out.get("name") in (None, call["name"]) and wire.get("name") in (None, call["name"]), "receipt_identity_mismatch")
    require("content" in out and "content" in wire, "missing_receipt_content")
    transform = _check_content(out["content"], wire["content"])
    require(transform is not None, "unknown_receipt_rewrite")
    return {"call_id": ident, "tool": call["name"], "arguments": call["args"], "result": _payload(out["content"]),
            "sequence": span["end_sequence"], "wire_message_index": wire_index, "transformation": transform,
            "raw_content_sha256": sha(canonical(out["content"])), "wire_content_sha256": sha(canonical(wire["content"]))}


def _prefix(messages, task, previous, spans, turn, cutoff):
    """Project only the history visible on this specific wire request."""
    known = {call["id"]: model for model in previous for call in model["calls"]}
    batches, owners, problems = [], [], []
    globally_seen, users = set(), []
    outside = []
    group = None
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "user":
            users.append(msg.get("content"))
        elif role == "assistant":
            calls = _calls(msg)
            matches = [known.get(call["id"]) for call in calls]
            owner = None
            if matches and all(item is matches[0] for item in matches) and matches[0] and matches[0]["calls"] == calls:
                owner = matches[0]
            elif not calls:
                # Identify a visible text-only decision by its public text and ordering.
                unique = [m for m in previous if not m["calls"] and m["text"] == msg.get("content") and m not in owners]
                if len(unique) == 1:
                    owner = unique[0]
            group = {"calls": {c["id"]: c for c in calls}, "seen": set(), "receipts": [], "gaps": [], "owner": owner}
            if owner is None or len(group["calls"]) != len(calls) or owner in owners:
                group["gaps"].append("unverified_history_call_group")
                group["owner"] = None
            owners.append(group["owner"])
            batches.append(group)
        elif role == "tool":
            ident = msg.get("tool_call_id")
            if group is None:
                outside.append(f"orphan_visible_receipt:{ident}")
                continue
            if ident not in group["calls"] or ident in globally_seen:
                group["gaps"].append(f"unmatched_or_duplicate_receipt:{ident}")
                group["receipts"] = [x for x in group["receipts"] if x["call_id"] != ident]
                continue
            globally_seen.add(ident)
            group["seen"].add(ident)
            if group["owner"] is not None:
                try:
                    group["receipts"].append(_receipt(group["calls"][ident], spans.get(ident, []), group["owner"], cutoff, msg, i))
                except Excluded as error:
                    group["gaps"].append(f"{error}:{ident}")
    if len(users) != 1 or task is None or not _check_content(task, users[0]):
        # Do not resurrect original task text if it was removed from the wire.
        task = ""
        outside.append("unverified_task_text")
    for group in batches:
        for ident in sorted(group["calls"].keys() - group["seen"]):
            group["gaps"].append(f"missing_visible_receipt:{ident}")
    if previous and (not owners or owners[-1] is not previous[-1]):
        batches.append({"receipts": [], "gaps": ["previous_decision_missing_from_wire"]})
    if owners != previous:
        problems.append("visible_history_incomplete_or_reordered")
    current = batches[-1]["gaps"] if batches else []
    history_gaps = [gap for group in batches[:-1] for gap in group["gaps"]]
    return {"turn": turn, "task": task, "batches": [group["receipts"] for group in batches],
            "gaps": outside + current, "history_gaps": problems + history_gaps}


def _response(attempt):
    chunks = attempt["chunks"]
    end = attempt.get("end")
    require(end is not None, "response_end_missing")
    raw = b"".join(chunks)
    require((len(chunks), len(raw), sha(raw)) == (end["chunk_count"], end["length"], end["sha256"]), "response_aggregate_hash_mismatch")
    if not end["complete"] or not end["response_observed"]:
        require(end["status"] != "complete", "inconsistent_response_end")
        return None
    require(end["status"] == "complete" and not end.get("error_type") and attempt.get("start"), "inconsistent_response_status")
    start = attempt["start"]
    require(200 <= start["status_code"] < 300, "unsuccessful_http_status")
    require(start["body_scope"] == "buffered_httpx_content" or
            (start["body_scope"] == "httpx_stream_before_content_decoding" and start["content_encoding"] == "identity"), "unsupported_response_encoding")
    body = parse(raw)
    choices = body.get("choices", [])
    require(len(choices) == 1 and choices[0]["index"] == 0, "ambiguous_response_choice")
    message = choices[0]["message"]
    require(message["role"] == "assistant" and not message.get("refusal"), "unsupported_response")
    usage = body.get("usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    require(reasoning is None or (type(reasoning) is int and reasoning >= 0), "invalid_reasoning_token_count")
    content = message.get("reasoning_content")
    require(content is None or isinstance(content, str), "invalid_reasoning_content")
    return {"calls": _calls(message), "text": message.get("content") or "", "reasoning_tokens": reasoning,
            "has_reasoning_text": bool(content and content.strip())}


def _index(raw, source):
    require(raw.endswith(b"\n"), "unterminated_archive")
    rows = [parse(line) for line in raw.splitlines()]
    require(rows and rows[0]["kind"] == "archive_start" and rows[-1]["kind"] == "archive_end", "missing_archive_boundary")
    models, attempts, spans, by_call = {}, {}, {}, defaultdict(list)
    detached = {}
    for seq, row in enumerate(rows, 1):
        require(row["schema_version"] == 1 and row["sequence"] == seq and all(row[k] == source[k] for k in IDENTITY), "archive_sequence_or_owner")
        kind, data = row["kind"], row["data"]
        if kind == "archive_start" or kind == "archive_end":
            require(seq == (1 if kind == "archive_start" else len(rows)), "misplaced_archive_boundary")
        elif kind == "model_start":
            logical = data["logical_call_id"]
            require(logical not in models, "duplicate_model_id")
            users = [m.get("content") for m in data["input"]["messages"] if m.get("type") == "human"]
            models[logical] = {"logical_call_id": logical, "task": users[0] if len(users) == 1 else None, "attempts": []}
        elif kind == "model_end":
            model = models[data["logical_call_id"]]
            require("end" not in model, "duplicate_model_end")
            model.update(end=data, end_sequence=seq)
        elif kind == "http_request":
            ident = data["attempt_id"]
            logical = data["logical_call_id"]
            model = models.get(logical)
            if model is None or "end" in model or ident in attempts:
                if ident in attempts:
                    attempts[ident]["error"] = "duplicate_or_late_http_request"
                detached[(logical, ident, seq)] = {"logical_call_id": logical, "attempt_id": ident,
                    "request_sequence": seq, "status": "invalid", "reason": "invalid_http_request_association"}
                continue
            attempt = {"attempt_id": ident, "logical_call_id": data["logical_call_id"], "request_sequence": seq, "chunks": [], "error": None}
            model["attempts"].append(attempt)
            attempts[ident] = attempt
            try:
                require(data["body_scope"] == "serialized_http_request_content" and not data.get("error_type"), "invalid_request_scope")
                request = parse(decode(data["body"]))
                require(request.get("stream") is False and isinstance(request["messages"], list), "unsupported_wire_protocol")
                attempt["messages"] = request["messages"]
            except (ValueError, KeyError, TypeError) as error:
                attempt["error"] = str(error)
        elif kind in {"http_response_start", "http_response_chunk", "http_response_end"}:
            logical, ident = data["logical_call_id"], data["attempt_id"]
            attempt = attempts.get(ident)
            if attempt is None or logical != attempt["logical_call_id"]:
                # A response with no attributable request is an audit finding. Keep
                # the observed line separately from an authentic request boundary.
                if not any(k[:2] == (logical, ident) for k in detached):
                    detached[(logical, ident, seq)] = {"logical_call_id": logical, "attempt_id": ident,
                        "request_sequence": seq, "status": "invalid", "reason": "orphan_http_response"}
                continue
            if "end" in attempt:
                attempt["error"] = "response_record_after_end"
                continue
            try:
                if kind == "http_response_start":
                    require("start" not in attempt, "duplicate_response_start")
                    attempt["start"] = data
                elif kind == "http_response_chunk":
                    require("start" in attempt and data["chunk_index"] == len(attempt["chunks"]) + 1, "invalid_chunk_order")
                    attempt["chunks"].append(decode(data["body"]))
                else:
                    attempt.update(end=data, end_sequence=seq)
            except (KeyError, ValueError, TypeError) as error:
                attempt["error"] = str(error)
        elif kind == "tool_start":
            ident = data["tool_span_id"]
            require(ident not in spans, "duplicate_span_id")
            spans[ident] = {"start": data, "start_sequence": seq}
            by_call[data["raw_tool_call_id"]].append(spans[ident])
        elif kind == "tool_end":
            span = spans[data["tool_span_id"]]
            require("end" not in span, "duplicate_span_end")
            span.update(end=data, end_sequence=seq)
        else:
            raise Excluded(f"unknown_archive_event: {kind}")
    return models, by_call, list(detached.values())


def read_turns(source):
    """Return decision rows with isolated prefixes and the complete HTTP attempt ledger."""
    raw = Path(source["path"]).read_bytes()
    require(sha(raw) == source["archive_sha256"], "archive_hash_mismatch")
    models, spans, detached = _index(raw, source)
    public = {key: source[key] for key in ("batch", "episode", "arm", "condition", "scenario", "task_id", "archive_path", "archive_sha256")}
    turns, attempts, accepted = [], [{**public, **item} for item in detached], []
    used_ids = set()
    for logical, model in models.items():
        require("end" in model and model["attempts"], "unclosed_or_unassociated_model")
        records = []
        for attempt in model["attempts"]:
            record = {**public, "logical_call_id": logical, "attempt_id": attempt["attempt_id"], "request_sequence": attempt["request_sequence"]}
            wire = None
            try:
                require(not attempt["error"], attempt["error"])
                wire = _response(attempt)
                if wire is None:
                    record.update(status="no_response", reason=model["end"].get("error_type") or attempt["end"].get("error_type") or "incomplete_response")
                else:
                    require(attempt["end_sequence"] < model["end_sequence"], "response_after_model_end")
                    record.update(status="retried", reason="superseded_http_attempt")
            except (ValueError, TypeError, KeyError) as error:
                record.update(status="invalid", reason=str(error))
                wire = None
            records.append((attempt, record, wire))
            attempts.append(record)
        if model["end"].get("status") != "returned":
            for _, record, wire in records:
                if wire is not None:
                    record.update(status="invalid", reason="http_reply_without_returned_decision")
            continue
        attempt, record, wire = records[-1]
        if wire is None:
            record.update(status="invalid", reason=record["reason"] or "returned_model_without_wire")
            continue
        output = (model["end"].get("output") or {}).get("result") or []
        require(len(output) == 1 and output[0].get("type") == "ai" and wire["text"] == (output[0].get("content") or "")
                and wire["calls"] == _calls(output[0], structured=True), "wire_handler_disagreement")
        ids = [c["id"] for c in wire["calls"]]
        require(not used_ids.intersection(ids) and len(set(ids)) == len(ids), "duplicate_response_tool_call_id")
        used_ids.update(ids)
        record.update(status="response", reason=None)
        n = len(turns) + 1
        prefix = _prefix(attempt["messages"], model["task"], accepted, spans, n, attempt["request_sequence"])
        previous_ids = [c["id"] for c in accepted[-1]["calls"]] if accepted else []
        turns.append({**public, "turn": n, "logical_call_id": logical, "attempt_id": attempt["attempt_id"],
                      "request_sequence": attempt["request_sequence"], "previous_call_ids": previous_ids,
                      "reasoning_tokens": wire["reasoning_tokens"], "has_reasoning_text": wire["has_reasoning_text"], "prefix": prefix})
        accepted.append({"logical_call_id": logical, "end_sequence": model["end_sequence"],
                         "calls": wire["calls"], "text": wire["text"]})
    return turns, sorted(attempts, key=lambda row: row["request_sequence"])
