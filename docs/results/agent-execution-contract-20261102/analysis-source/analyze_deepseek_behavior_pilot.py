"""Read-only, standard-library evidence analysis of a sealed behavior pilot.

No simulator, Agent, SDK, pickle, runner or cost-ledger imports. In particular,
opening the production ledger would create locks/markers and is forbidden here.
Only actual assistant wire outputs supply model decisions. Reasoning content is
never copied into analysis outputs. Output must be a new directory outside run.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path


TERMINAL = {"completed", "needs_replan", "cancelled", "invalidated", "failed", "interrupted"}
TASKS = ("c00d8b78", "b4714028", "7eba7390")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))


def safe(root, name):
    name = Path(name)
    if name.is_absolute() or ".." in name.parts:
        raise ValueError("Evidence path must be relative")
    result = root / name
    if any(p.is_symlink() for p in (result, *result.parents)):
        raise ValueError("Symlink in evidence path")
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence outside run")
    return result


def read(root, name, *, optional=False):
    file = safe(root, name)
    if optional and not file.exists():
        return None
    return parse(file.read_bytes())


def rows(file):
    raw = file.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Unterminated JSONL: " + file.name)
    return [parse(line) for line in raw.splitlines()]


def hashes(root, expected):
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Missing or invalid evidence seal")
    for name, digest in expected.items():
        if not isinstance(digest, str) or sha(safe(root, name).read_bytes()) != digest:
            raise ValueError("Hash mismatch: " + name)


def decode(record):
    raw = base64.b64decode(record["base64"], validate=True)
    if type(record["length"]) is not int or len(raw) != record["length"] or sha(raw) != record["sha256"]:
        raise ValueError("Archived bytes hash/length mismatch")
    return raw


def parsed_content(value):
    if isinstance(value, str):
        try:
            return parse(value)
        except (ValueError, TypeError):
            return value
    return value


def arguments(value):
    value = parsed_content(value)
    if isinstance(value, dict) and set(value) == {"input_json"}:
        value = parsed_content(value["input_json"])
    return value


def clean(value):
    """Never export hidden reasoning fields, even in nested structured records."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k not in {"reasoning_content", "reasoning", "thinking"}}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def excerpt(value, limit=2400):
    text = value if isinstance(value, str) else json.dumps(clean(value), ensure_ascii=False)
    return {"text": text[:limit], "truncated": len(text) > limit, "characters": len(text),
            "sha256": sha(text.encode())}


def tool_output_view(value):
    """Keep actual error text and selected public result facts, not giant maps."""
    if isinstance(value, str):
        return {"kind": "text", **excerpt(value, limit=900)}
    if isinstance(value, dict):
        keys = ("status", "error", "outcome", "reason", "message", "id", "drone_id", "command",
                "position", "heading", "speed", "battery_level", "arrival_verified", "distance_remaining")
        result = {k: clean(value[k]) for k in keys if k in value}
        for key in ("navigation_result", "move_result", "last_move_result"):
            if isinstance(value.get(key), dict):
                result[key] = tool_output_view(value[key])
        return {"kind": "object", "fields": result, "sha256": sha(canonical(clean(value)))}
    return {"kind": type(value).__name__, "sha256": sha(canonical(clean(value)))}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def archive_analysis(file):
    records = rows(file)
    attempts, spans, models, identity, ending = {}, {}, {}, None, None
    for index, row in enumerate(records, 1):
        require(row["sequence"] == index and ending is None, "Invalid archive sequence/close boundary")
        owner = tuple(row[k] for k in ("command_id", "episode_id", "archive_id"))
        identity = identity or owner
        require(identity == owner, "Mixed archive ownership")
        kind, data = row["kind"], row["data"]
        if kind == "model_start":
            ident = data["logical_call_id"]
            require(ident not in models, "Duplicate logical model call")
            models[ident] = {"start_sequence": index, "end_sequence": None}
        elif kind == "model_end":
            ident = data["logical_call_id"]
            require(ident in models and models[ident]["end_sequence"] is None, "Unmatched model end")
            models[ident].update(end_sequence=index, status=data.get("status"), error_type=data.get("error_type"))
        elif kind == "http_request":
            ident, logical = data["attempt_id"], data["logical_call_id"]
            require(ident not in attempts and logical in models, "Unmatched/duplicate HTTP request")
            body = decode(data["body"])
            attempts[ident] = {"attempt_id": ident, "logical_call_id": logical,
                "request_sequence": index, "request_body": body, "request_body_sha256": sha(body),
                "response_start": None, "chunks": [], "end": None}
        elif kind in {"http_response_start", "http_response_chunk", "http_response_end"}:
            item = attempts[data["attempt_id"]]
            require(item["logical_call_id"] == data["logical_call_id"] and item["end"] is None,
                "Invalid response association")
            if kind == "http_response_start":
                require(item["response_start"] is None, "Duplicate response start")
                item["response_start"] = data
            elif kind == "http_response_chunk":
                require(item["response_start"] is not None and data["chunk_index"] == len(item["chunks"]) + 1,
                    "Invalid response chunk order")
                item["chunks"].append(decode(data["body"]))
            else:
                raw = b"".join(item["chunks"])
                require((data["length"], data["sha256"], data["chunk_count"]) == (len(raw), sha(raw), len(item["chunks"])),
                    "Response aggregate hash mismatch")
                item["end"] = data
        elif kind == "tool_start":
            ident = data["tool_span_id"]
            require(ident not in spans, "Duplicate tool span")
            spans[ident] = {"start_sequence": index, "start": data, "end_sequence": None, "end": None}
        elif kind == "tool_end":
            item = spans[data["tool_span_id"]]
            require(item["end"] is None, "Duplicate tool end")
            item.update(end_sequence=index, end=data)
        elif kind == "archive_end":
            ending = data
    require(bool(records) and records[0]["kind"] == "archive_start", "Missing archive start")
    decisions, finals, responses, wire_issues = [], [], [], []
    usages = []
    for item in attempts.values():
        request = parse(item["request_body"])
        request_tools = [x.get("function", {}).get("name") for x in request.get("tools", [])]
        raw = b"".join(item["chunks"])
        detail = {k: item[k] for k in ("attempt_id", "logical_call_id", "request_sequence", "request_body_sha256")}
        detail.update(request_bytes=len(item["request_body"]), request_model=request.get("model"),
            request_tool_names=request_tools, response_body_sha256=sha(raw) if raw else None,
            status_code=(item["response_start"] or {}).get("status_code"), response_closed=item["end"] is not None)
        payload = None
        if raw:
            try:
                payload = parse(raw)
            except ValueError:
                wire_issues.append({"attempt_id": item["attempt_id"], "reason": "response_not_complete_json"})
        if isinstance(payload, dict):
            detail.update(response_id=payload.get("id"), response_model=payload.get("model"),
                system_fingerprint=payload.get("system_fingerprint"), usage=clean(payload.get("usage")))
            usage = payload.get("usage")
            if isinstance(usage, dict):
                keys = ("prompt_tokens", "completion_tokens", "total_tokens")
                if all(type(usage.get(k)) is int and usage[k] >= 0 for k in keys) and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]:
                    usages.append(usage)
                else:
                    wire_issues.append({"attempt_id": item["attempt_id"], "reason": "invalid_usage"})
            for choice in payload.get("choices", []):
                message = choice.get("message", {})
                if message.get("role") != "assistant":
                    continue
                for call in message.get("tool_calls", []) or []:
                    function = call.get("function", {})
                    decision = {"ordinal": len(decisions) + 1, "logical_call_id": item["logical_call_id"],
                        "attempt_id": item["attempt_id"], "response_id": payload.get("id"),
                        "choice_index": choice.get("index"), "tool_call_id": call.get("id"),
                        "name": function.get("name"), "arguments": clean(arguments(function.get("arguments", ""))),
                        "wire_arguments": clean(parsed_content(function.get("arguments", ""))),
                        "request_sequence": item["request_sequence"], "executions": []}
                    for span_id, span in spans.items():
                        start = span["start"]
                        if start.get("raw_tool_call_id") != call.get("id") or start.get("source_model_id") != item["logical_call_id"]:
                            continue
                        end = span["end"] or {}
                        output = end.get("output") or {}
                        message_status = output.get("status") if isinstance(output, dict) else None
                        if isinstance(output, dict):
                            output = parsed_content(output.get("content"))
                        execution = {"tool_span_id": span_id, "start_sequence": span["start_sequence"],
                            "end_sequence": span["end_sequence"], "source_relation": start.get("source_relation"),
                            "status": end.get("status"), "error_type": end.get("error_type"),
                            "tool_call_id_mismatch": end.get("tool_call_id_mismatch"),
                            "tool_message_status": message_status, "public_output": tool_output_view(output)}
                        if function.get("name") == "run_harness" and isinstance(output, dict):
                            execution["control_response"] = clean(output)
                        decision["executions"].append(execution)
                    decisions.append(decision)
                content = message.get("content")
                if content and not message.get("tool_calls"):
                    finals.append({"logical_call_id": item["logical_call_id"], "attempt_id": item["attempt_id"],
                        "finish_reason": choice.get("finish_reason"), "content": excerpt(content)})
        responses.append(detail)
    usage_summary = {k: sum(u[k] for u in usages) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    # Missing cache fields remain unknown; never assume zero or add to prompt twice.
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        known = [u[key] for u in usages if type(u.get(key)) is int and u[key] >= 0]
        usage_summary[key] = sum(known) if known else None
        usage_summary[key + "_reported_responses"] = len(known)
    usage_summary.update(known_usage_responses=len(usages), archived_http_request_attempts=len(attempts),
        logical_model_calls=len(models), closed_model_calls=sum(v["end_sequence"] is not None for v in models.values()))
    return {"file": file.name, "sha256": sha(file.read_bytes()), "ownership": identity,
        "record_count": len(records), "archive_end": ending, "complete": bool(ending and ending.get("complete")),
        "issues": wire_issues, "responses": responses, "usage": usage_summary,
        "decisions": decisions, "final_messages": finals, "tool_spans": len(spans)}


def harness_analysis(harness, wire, public):
    events = (harness or {}).get("events", [])
    frames = (harness or {}).get("frames", [])
    decisions = (wire or {}).get("decisions", [])
    controls, opportunities, terminals, resume_attempts, raw_after = [], [], [], [], []
    for decision in decisions:
        if decision["name"] != "run_harness":
            continue
        args = decision["arguments"] if isinstance(decision["arguments"], dict) else {}
        control = {"decision_ordinal": decision["ordinal"], "tool_call_id": decision["tool_call_id"],
            "logical_call_id": decision["logical_call_id"], "action": args.get("action"),
            "skill": args.get("skill"), "plan_id": args.get("plan_id"), "responses": []}
        for execution in decision["executions"]:
            response = execution.get("control_response")
            if not isinstance(response, dict):
                continue
            view = {k: response.get(k) for k in ("plan_id", "skill", "status", "reason", "plan_status", "plan_reason", "cursor", "resumable", "remaining_s")}
            view.update(archive_end_sequence=execution["end_sequence"], source_relation=execution["source_relation"])
            control["responses"].append(view)
            relation_valid = execution["source_relation"] == "unique" and execution.get("tool_call_id_mismatch") is False
            if not relation_valid:
                continue
            status, plan = response.get("status"), response.get("plan_id")
            if status in TERMINAL:
                terminals.append({**view, "tool_call_id": decision["tool_call_id"], "decision_ordinal": decision["ordinal"]})
            if status in {"paused", "ready"} and response.get("resumable") is True and isinstance(response.get("remaining_s"), (int, float)) and response["remaining_s"] > 0:
                matches = [e for e in events if e.get("kind") == "skill_exit" and e.get("plan_id") == plan and e.get("status") == status]
                opportunities.append({**view, "tool_call_id": decision["tool_call_id"],
                    "decision_ordinal": decision["ordinal"], "corroborating_skill_exit_sequences": [e["sequence"] for e in matches],
                    "scope": "observed_resumable_response_with_remaining_time; future_validity_checked_at_resume"})
        if args.get("action") == "resume":
            prior = [x for x in opportunities if x["plan_id"] == args.get("plan_id") and x["decision_ordinal"] < decision["ordinal"]]
            later_terminals = [x for x in terminals if x["plan_id"] == args.get("plan_id") and prior and prior[-1]["decision_ordinal"] < x["decision_ordinal"] < decision["ordinal"]]
            model_events = [e for e in events if e.get("kind") == "model_control_tool" and e.get("tool_call_id") == decision["tool_call_id"]]
            enters = []
            if len(model_events) == 1:
                start = model_events[0]["sequence"]
                next_control = min([e["sequence"] for e in events if e.get("kind") == "model_control_tool" and e["sequence"] > start] or [float("inf")])
                enters = [e for e in events if e.get("kind") == "skill_enter" and e.get("plan_id") == args.get("plan_id") and start < e["sequence"] < next_control]
            resume_attempts.append({"tool_call_id": decision["tool_call_id"], "plan_id": args.get("plan_id"),
                "has_prior_observed_opportunity": bool(prior) and not later_terminals,
                "reentered_skill_sequences": [e["sequence"] for e in enters],
                "effective_recovery_verdict": "not_automatically_inferred; inspect_cursor_effects_and_replay"})
        controls.append(control)
    for decision in decisions:
        if decision["name"] == "run_harness":
            continue
        earlier = [t for t in terminals if t["archive_end_sequence"] is not None and t["archive_end_sequence"] < decision["request_sequence"]]
        if not earlier:
            continue
        calls = [p for p in public if p.get("origin", {}).get("kind") == "model_tool" and p["origin"].get("tool_call_id") == decision["tool_call_id"]]
        raw_after.append({"decision_ordinal": decision["ordinal"], "tool_call_id": decision["tool_call_id"],
            "name": decision["name"], "after_terminal": earlier[-1],
            "request_ids": [p["request_id"] for p in calls],
            "action_request_ids": [p["request_id"] for p in calls if p.get("action")],
            "committed_action_request_ids": [p["request_id"] for p in calls if p.get("action") and p.get("committed") is True],
            "obligation_progress": "requires_content_review; temporal_followup_is_not_completion"})
    brief_frames = [{k: clean(f.get(k)) for k in ("plan_id", "skill", "status", "reason", "cursor", "actor_bindings", "api_calls", "repairs", "units")} for f in frames]
    rejects = sum(any(r.get("status") == "rejected" for r in c["responses"]) for c in controls)
    return {"enabled": harness.get("enabled", True) if harness is not None else None,
        "artifact_present": harness is not None, "frames": brief_frames,
        "event_counts": dict(Counter(e.get("kind") for e in events)), "controls": controls,
        "control_action_counts": dict(Counter(c["action"] or "unknown" for c in controls)),
        "rejected_control_calls": rejects, "observed_resumable_opportunities": opportunities,
        "opportunity_count": len(opportunities), "resume_attempts": resume_attempts,
        "recovery_rate": None, "recovery_rate_status": "not_estimable_zero_opportunities" if not opportunities else "requires_effect_and_replay_review",
        "terminal_responses": terminals, "raw_followups_after_terminal": raw_after,
        "raw_action_followups_after_terminal": sum(bool(x["action_request_ids"]) for x in raw_after),
        "controlled_or_natural_origin": "see_run_local_smoke_flag; frame_origin_alone_is_not_evidence"}


def world_analysis(public, physical, fixture):
    effects = defaultdict(list)
    for row in physical:
        effects[row.get("request_id")].append(row)
    actions = []
    names = {}
    for row in public:
        if row.get("endpoint") == "/drones" and isinstance(row.get("response"), list):
            names.update({d["id"]: d.get("name") for d in row["response"] if isinstance(d, dict) and "id" in d})
        if not row.get("action"):
            continue
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        parts = row.get("endpoint", "").split("/")
        actor = parts[2] if len(parts) >= 5 and parts[1] == "drones" else None
        actions.append({"request_id": row["request_id"], "tool": parts[-1], "actor_id": actor,
            "actor_name": names.get(actor), "origin": clean(row.get("origin")), "params": clean(row.get("params")),
            "body": clean(row.get("body")), "status": response.get("status"), "error": row.get("error"),
            "receipt_message": response.get("message"), "committed": row.get("committed"),
            "relative_seconds": row.get("relative_seconds"), "ended_seconds": row.get("ended_seconds"),
            "physical_events": [{"event_id": e.get("event_id"), "actor": e.get("actor"), "committed": e.get("committed"),
                "event": e.get("history", {}).get("event"), "position": e.get("history", {}).get("position"),
                "length_xyz": e.get("length_xyz")} for e in effects[row["request_id"]]]})
    hover = [a for a in actions if a["tool"] == "hover"]
    required_name = "Drone 1" if fixture["task_id"] == "c00d8b78" else "Drone 3" if fixture["task_id"] == "b4714028" else None
    confirmed = [a for a in hover if a["committed"] is True and a["status"] == "success" and
        any(e["committed"] is True and e["event"] == "hovering" and e["actor"] == a["actor_id"] for e in a["physical_events"])]
    return {"api_requests": len(public), "actions": actions, "action_attempts": len(actions),
        "committed_action_attempts": sum(a["committed"] is True for a in actions),
        "action_status_counts": dict(Counter(str(a["status"]) for a in actions)),
        "action_origin_counts": dict(Counter((a["origin"] or {}).get("kind", "unknown") for a in actions)),
        "physical_history_events": len(physical),
        "hover_audit": {"raw_requests": len(hover), "successful_committed_with_physical_event": len(confirmed),
            "required_actor_name": required_name,
            "required_actor_hover_confirmed": any(a["actor_name"] == required_name for a in confirmed) if required_name else None,
            "request_ids": [a["request_id"] for a in confirmed],
            "interpretation": "confirms observed hover effect only; duration/final persistence/order are not inferred"}}


def verify_run(run):
    manifest = read(run, "manifest.json")
    require(manifest.get("scope") == "deepseek_behavior_pilot_v1", "Not a DeepSeek behavior pilot")
    expected_order = [(task, arm) for index, task in enumerate(TASKS) for arm in
        (("integrated", "baseline") if index == 1 else ("baseline", "integrated"))]
    require([(t["task_id"], t["arm"]) for t in manifest["trials"]] == expected_order
        and all(t["trial_id"] == t["task_id"] + "-" + t["arm"] for t in manifest["trials"])
        and type(manifest.get("local_smoke")) is bool, "Outside fixed three-task/six-slot pilot")
    require(sha(safe(run, "manifest.json").read_bytes()) == safe(run, "manifest.sha256").read_text().strip(), "Manifest hash mismatch")
    seal = read(run, "campaign-seal.json")["files"]
    hashes(run, seal)
    actual = {str(p.relative_to(run)) for p in run.rglob("*") if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
    require(set(seal) == actual, "Campaign file inventory changed")
    hashes(run / "source", manifest["sources"])
    hashes(run / "inputs", manifest["inputs"])
    require(sha(safe(run, "PROTOCOL_CN.md").read_bytes()) == manifest["protocol_sha256"], "Protocol hash mismatch")
    results = read(run, "results.json")
    planned = manifest["trials"]
    require([r["trial_id"] for r in results] == [t["trial_id"] for t in planned[:len(results)]], "Results are not fixed trial prefix")
    verification = {"campaign_files": len(seal), "manifest_sha256": sha(safe(run, "manifest.json").read_bytes()),
        "campaign_seal_sha256": sha(safe(run, "campaign-seal.json").read_bytes()), "trials": {}}
    for trial, result in zip(planned, results):
        ident, folder = trial["trial_id"], safe(run, "trials/" + trial["trial_id"])
        worker = read(folder, "worker-result.json", optional=True)
        score = read(folder, "evaluation/result.json", optional=True)
        fixture = read(run / "inputs", "fixtures/" + trial["task_id"] + ".json")
        require(fixture["task_id"] == trial["task_id"] and fixture["scene_id"] == "3973d0d6" and
            sha(fixture["command"].encode()) == fixture["command_sha256"], "Fixture identity/command mismatch")
        v = {"worker_present": worker is not None, "score_present": score is not None}
        require(result.get("worker") == worker and result.get("score") == score, "Results differ from worker/scorer artifacts: " + ident)
        for process in result.get("processes", []):
            require(process["log_sha256"] == sha(safe(run, "process-logs/" + ident + "-" + process["mode"] + ".log").read_bytes()), "Process log hash mismatch")
        if worker is not None:
            require(worker["trial_id"] == ident and worker["task_id"] == trial["task_id"], "Worker identity mismatch")
            hashes(folder, worker["files"])
            hashes(folder, read(run, "worker-exit-seals/" + ident + ".json"))
            require(worker["initial"]["original_state_sha256"] == fixture["state_sha256"] and
                worker["initial"]["public_startup_sha256"] == fixture["public_startup_sha256"], "Worker initial state differs from fixture")
            require(worker.get("entry", {}).get("frames") == 0 and worker.get("entry", {}).get("ledger_events") == 0,
                "Worker entry already contains plan/evidence")
            v["worker_hashes_verified"] = True
        if score is not None:
            require(worker is not None and worker.get("drained") is True, "Scored worker missing or not drained")
            hashes(folder, read(run, "scorer-exit-seals/" + ident + ".json"))
            require(score["trial_id"] == ident and score["worker_result_sha256"] == sha(safe(folder, "worker-result.json").read_bytes()), "Score worker association mismatch")
            require(score["snapshot_sha256"] == worker["snapshot"]["sha256"] == sha(safe(folder, worker["snapshot"]["path"]).read_bytes()), "Scored snapshot mismatch")
            require(score["worker_pid"] == worker["pid"] and score["pid"] != worker["pid"] and score.get("agent_imported") is False,
                "Scorer isolation mismatch")
            require(score.get("checker_tree_matches_original") is True and score.get("private_canary_present") is True, "Scorer source/identity check failed")
            ps = result.get("processes", [])
            require(len(ps) == 2 and [p["mode"] for p in ps] == ["worker", "score"] and
                [p["pid"] for p in ps] == [worker["pid"], score["pid"]] and
                all(p.get("exited") is True and p.get("returncode") == 0 and p.get("kill_reason") is None for p in ps),
                "Worker/scorer process exit evidence invalid")
            v["scorer_hashes_and_snapshot_verified"] = True
        verification["trials"][ident] = v
    return manifest, results, verification


def cost_analysis(run, trial_reports):
    summary = read(run, "cost-summary.json")
    ledger = rows(safe(run, "cost-ledger.jsonl"))
    require(bool(ledger), "Empty cost ledger")
    for index, row in enumerate(ledger, 1):
        require(row["sequence"] == index and row["previous_sha256"] == (ledger[index - 2]["sha256"] if index > 1 else None)
            and row["sha256"] == sha(canonical({k: v for k, v in row.items() if k != "sha256"})), "Cost ledger hash chain mismatch")
    require(read(run, "cost-ledger.jsonl.head.json") == {"sequence": len(ledger), "sha256": ledger[-1]["sha256"]}, "Cost durable head mismatch")
    reservations, charges = {}, defaultdict(lambda: Decimal("0"))
    for row in ledger:
        event = row["event"]
        if event["kind"] == "reserved":
            reservations[event["reservation_id"]] = event
        elif event["kind"] == "settled":
            reserved = reservations[event["reservation_id"]]
            usage = event["usage"]
            rates = summary["policy"]
            charge = (Decimal(rates["input_per_million"]) * usage["prompt_tokens"] + Decimal(rates["output_per_million"]) * usage["completion_tokens"]) / Decimal(1000000)
            require(charge == Decimal(event["charge"]), "Cost settlement arithmetic mismatch")
            charges[reserved["trial_id"]] += charge
            wire = trial_reports[reserved["trial_id"]].get("model_io")
            matches = [r for r in (wire or {}).get("responses", []) if r["request_body_sha256"] == reserved["request_body_sha256"] and r.get("response_body_sha256") == event["response_body_sha256"]]
            require(len(matches) == 1, "Settled cost cannot be uniquely linked to wire bytes")
            require(all(matches[0].get("usage", {}).get(k) == usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")), "Cost usage differs from wire response")
    require(sum(charges.values(), Decimal("0")) == Decimal(summary["settled_conservative_charge"]), "Batch cost summary mismatch")
    for ident, trial in summary["trials"].items():
        require(charges[ident] == Decimal(trial["settled_conservative_charge"]), "Trial cost summary mismatch")
        trial_reports[ident]["cost"] = clean(trial)
    return {"currency": summary["currency"], "settled_conservative_charge": summary["settled_conservative_charge"],
        "reserved": summary["reserved"], "locked_reservation": summary.get("locked_reservation"),
        "blocked": summary["blocked"], "stop_reason": summary.get("stop_reason"), "journal_records": len(ledger),
        "hash_chain_and_settled_wire_associations_verified": True,
        "meaning": "cache_miss_conservative_usage_accounting_not_supplier_invoice"}


def analyze(run):
    manifest, results, verification = verify_run(run)
    reports = {}
    for trial in manifest["trials"]:
        ident = trial["trial_id"]
        result = next((r for r in results if r["trial_id"] == ident), None)
        fixture = read(run / "inputs", "fixtures/" + trial["task_id"] + ".json")
        report = {"trial_id": ident, "task_id": trial["task_id"], "arm": trial["arm"], "scene_id": fixture["scene_id"],
            "command": fixture["command"], "started": result is not None, "checker_passed": None, "status": "not_run"}
        reports[ident] = report
        if result is None:
            continue
        folder = safe(run, "trials/" + ident)
        worker, score = result.get("worker"), result.get("score")
        report.update(status="scored" if score else "started_incomplete", result_layers=clean(result.get("result")),
            process_wall_seconds=sum(p.get("elapsed_seconds", 0) for p in result.get("processes", [])),
            worker_elapsed_seconds=worker.get("elapsed") if worker else None,
            checker_passed=score.get("checker", {}).get("passed") if score else None,
            checker_counts={k: score["checker"].get(k) for k in ("checks_passed", "checks_expected", "errors")} if score else None,
            request_limits=clean(worker.get("limits")) if worker else read(folder, "private/request-limits.json", optional=True))
        archives = list(safe(folder, "policy/modelio").glob("*.jsonl"))
        require(len(archives) <= 1, "More than one model archive for a trial")
        wire = archive_analysis(archives[0]) if archives else None
        if wire is not None:
            limits = report.get("request_limits") or {}
            wire["usage"].update(admitted_http_attempts=limits.get("admitted_http_attempts"),
                delegated_http_attempts=limits.get("delegated_http_attempts"),
                archived_attempts_without_admission=(len(wire["responses"]) - limits["admitted_http_attempts"])
                    if type(limits.get("admitted_http_attempts")) is int else None)
            if type(limits.get("known_total_tokens")) is int:
                require(limits["known_total_tokens"] == wire["usage"]["total_tokens"], "Limit usage differs from archived responses")
            for decision in wire["decisions"]:
                decision["tool_error_returned"] = any(e.get("tool_message_status") == "error" for e in decision["executions"])
            wire["tool_errors"] = [{"tool_call_id": d["tool_call_id"], "name": d["name"], "logical_call_id": d["logical_call_id"],
                "outputs": [e["public_output"] for e in d["executions"] if e.get("tool_message_status") == "error"]}
                for d in wire["decisions"] if d["tool_error_returned"]]
        report["model_io"] = wire
        public_file, physical_file = safe(folder, "policy/worldlogs/public-api.jsonl"), safe(folder, "policy/worldlogs/physical-events.jsonl")
        public, physical = rows(public_file) if public_file.exists() else [], rows(physical_file) if physical_file.exists() else []
        report["world"] = world_analysis(public, physical, fixture)
        report["harness"] = harness_analysis(read(folder, "policy/harness.json", optional=True), wire, public)
    costs = cost_analysis(run, reports)
    grouped = {}
    for arm in ("baseline", "integrated"):
        group = [r for r in reports.values() if r["arm"] == arm]
        scored = [r for r in group if type(r["checker_passed"]) is bool]
        grouped[arm] = {"planned": len(group), "started": sum(r["started"] for r in group), "scored": len(scored),
            "checker_passed": sum(r["checker_passed"] for r in scored),
            "not_scored": len(group) - len(scored),
            "usage_known_total_tokens": sum((r.get("model_io") or {}).get("usage", {}).get("total_tokens", 0) for r in group),
            "delegated_http_attempts": sum(((r.get("model_io") or {}).get("usage", {}).get("delegated_http_attempts") or 0) for r in group),
            "worker_elapsed_seconds": sum(r.get("worker_elapsed_seconds") or 0 for r in group),
            "scope": "known_usage_only; unscored_is_not_automatically_failed"}
    pairs = []
    for task in dict.fromkeys(t["task_id"] for t in manifest["trials"]):
        b, i = reports[task + "-baseline"], reports[task + "-integrated"]
        complete = type(b["checker_passed"]) is bool and type(i["checker_passed"]) is bool
        pair = {"task_id": task, "complete_pair": complete,
            "baseline_passed": b["checker_passed"], "integrated_passed": i["checker_passed"],
            "checker_difference_integrated_minus_baseline": int(i["checker_passed"]) - int(b["checker_passed"]) if complete else None}
        if complete:
            pair["differences_integrated_minus_baseline"] = {
                "known_total_tokens": i["model_io"]["usage"]["total_tokens"] - b["model_io"]["usage"]["total_tokens"],
                "delegated_http_attempts": i["model_io"]["usage"]["delegated_http_attempts"] - b["model_io"]["usage"]["delegated_http_attempts"],
                "action_attempts": i["world"]["action_attempts"] - b["world"]["action_attempts"],
                "worker_elapsed_seconds": i["worker_elapsed_seconds"] - b["worker_elapsed_seconds"],
                "settled_conservative_charge": str(Decimal(i["cost"]["settled_conservative_charge"]) - Decimal(b["cost"]["settled_conservative_charge"]))}
        pairs.append(pair)
    processes = [p for r in results for p in r.get("processes", [])]
    process_span = (max(p["ended_ns"] for p in processes) - min(p["started_ns"] for p in processes)) / 1e9 if processes else 0
    return {"schema_version": 1, "valid": True, "run": str(run), "local_smoke": manifest["local_smoke"],
        "analyzer": {"path": str(Path(__file__).resolve()), "sha256": sha(Path(__file__).read_bytes())},
        "evidence_type": "synthetic_local_protocol_check" if manifest["local_smoke"] else "natural_provider_known_development_pilot",
        "verification": verification, "summary": read(run, "summary.json"), "cost": costs,
        "wall_time": {"first_process_start_to_last_exit_seconds": process_span,
            "sum_worker_elapsed_seconds": sum(r.get("worker_elapsed_seconds") or 0 for r in reports.values()),
            "sum_worker_and_scorer_process_seconds": sum(p.get("elapsed_seconds", 0) for p in processes)},
        "task_count": len({t["task_id"] for t in manifest["trials"]}), "scene_count": len({r["scene_id"] for r in reports.values()}),
        "repetitions_per_task_arm": 1, "by_arm": grouped, "pairs": pairs, "trials": list(reports.values()),
        "limits": ["Three known tasks in one scene; not F1-F6 coverage or independent generalization.",
            "One repetition per task/arm; no significance or stable-improvement claim.",
            "Whole-system package contrast: raw tools shared, state/progress/harness schema differ.",
            "Checker does not fully verify natural-language obligations; hover audited separately.",
            "Zero observed recovery opportunities means not estimable, not zero recovery ability.",
            "Temporal raw continuation is not automatically obligation progress or recovery success.",
            "Archive/header hashes are local consistency checks, not an external cryptographic trust root."]}


def mdcell(value):
    if value is None:
        return "未记录/不可估计"
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown(report):
    text = ["# DeepSeek 行为 pilot 证据分析", "", "运行目录：`" + report["run"] + "`", ""]
    if not report["valid"]:
        return "\n".join(text + ["**完整性核验失败；不输出可信行为或成绩结论。**", "", mdcell(report["error"]), ""])
    text += ["**仅为本地合成协议检查，不能当作模型自然行为。**" if report["local_smoke"] else
        "真实模型 API＋本地隔离仿真的已知开发行为 pilot。", "",
        "3 个任务、1 个场景、每题每臂 1 次；这是整合包对照，不支持单机制归因、显著性或泛化主张。", "",
        "campaign、源码/输入、各已完成 worker/scorer、评分快照与费用账本关联均已复核。", "",
        "| task / arm | checker | 已派发 HTTP | 已知 token | 动作 | start / resume / cancel | 真实可恢复返回 | hover效果 |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | --- |"]
    for r in report["trials"]:
        wire, h, w = r.get("model_io") or {}, r.get("harness") or {}, r.get("world") or {}
        counts = h.get("control_action_counts", {})
        text.append("| " + " | ".join(map(mdcell, [r["task_id"] + " / " + r["arm"], r["checker_passed"],
            wire.get("usage", {}).get("delegated_http_attempts"), wire.get("usage", {}).get("total_tokens"), w.get("action_attempts"),
            " / ".join(str(counts.get(k, 0)) for k in ("start", "resume", "cancel")) if r["started"] else "未运行", h.get("opportunity_count"),
            w.get("hover_audit", {}).get("required_actor_hover_confirmed")])) + " |")
    cost = report["cost"]
    text += ["", "保守结算费用：" + cost["currency"] + " " + cost["settled_conservative_charge"] +
        "；锁定预留：" + str(cost["locked_reservation"]) + "。这是 cache-miss 口径，不是供应商账单。", ""]
    for r in report["trials"]:
        text += ["## " + r["trial_id"], "", "原指令：" + r["command"], ""]
        if not r["started"]:
            text += ["未运行；不计作任务失败。", ""]
            continue
        wire, h = r.get("model_io") or {}, r.get("harness") or {}
        limits = r.get("request_limits")
        stop_label = ("无（正常返回）" if isinstance(limits, dict) and "stop_reason" in limits and limits["stop_reason"] is None
            and (r.get("result_layers") or {}).get("run", {}).get("agent_returned_normally") is True
            else "无已记录的停止原因" if isinstance(limits, dict) and "stop_reason" in limits and limits["stop_reason"] is None
            else mdcell(limits.get("stop_reason") if isinstance(limits, dict) else None))
        text += ["运行/评分：" + r["status"] + "；worker耗时 " + mdcell(r.get("worker_elapsed_seconds")) +
            "s；停止原因 " + stop_label + "。", "",
            "| 决策 | logical call / 原 tool call ID | 工具 | 参数 |", "| ---: | --- | --- | --- |"]
        for d in wire.get("decisions", []):
            text.append("| " + " | ".join(map(mdcell, [d["ordinal"], d["logical_call_id"] + " / " + str(d["tool_call_id"]), d["name"], json.dumps(d["arguments"], ensure_ascii=False)])) + " |")
        text += ["", "子程序终态：" + mdcell(json.dumps(h.get("frames", []), ensure_ascii=False)) + "。",
            "可恢复机会 " + str(h.get("opportunity_count", 0)) + "；恢复率：" + mdcell(h.get("recovery_rate_status")) +
            "。终态后关联的原工具动作决策数 " + str(h.get("raw_action_followups_after_terminal", 0)) +
            "（时间关联本身不证明剩余义务完成）。", ""]
        for error in wire.get("tool_errors", []):
            text += ["工具错误回执 `" + str(error["tool_call_id"]) + "`（" + str(error["name"]) + "）：" +
                mdcell(json.dumps(error["outputs"], ensure_ascii=False)), ""]
        usage = wire.get("usage", {})
        text += ["已派发 HTTP " + mdcell(usage.get("delegated_http_attempts")) +
            "；归档请求尝试 " + mdcell(usage.get("archived_http_request_attempts")) +
            "（包含准入前拒绝，不能全部算作供应商调用）。", ""]
        for final in wire.get("final_messages", []):
            text += ["模型可见最终文本（不含 reasoning）：" + mdcell(final["content"]["text"]), ""]
        models = sorted({str(x.get("response_model")) for x in wire.get("responses", []) if x.get("response_model")})
        fingerprints = sorted({str(x.get("system_fingerprint")) for x in wire.get("responses", []) if x.get("system_fingerprint")})
        text += ["实际响应 model：" + mdcell(models) + "；fingerprint：" + mdcell(fingerprints or None) + "。", ""]
    text += ["恢复的有效效果、无动作重放、剩余义务完成必须结合 JSON 中 call ID、frame、公开 request ID 和物理事件审阅。没有事件按 0 记录，分母为 0 的比率保持不可估计。", ""]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New directory outside run")
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(run):
        parser.error("--output must be a nonexistent directory outside --run")
    try:
        report = analyze(run)
    except Exception as exc:
        report = {"schema_version": 1, "valid": False, "run": str(run),
            "error": type(exc).__name__ + ": " + str(exc), "trials": [],
            "interpretation": "Evidence verification failed; no trusted behavior conclusions produced"}
    output.mkdir(parents=True, exist_ok=False)
    (output / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (output / "REPORT_CN.md").write_text(markdown(report))
    print(json.dumps({"valid": report["valid"], "output": str(output), "error": report.get("error")}, ensure_ascii=False))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
