"""Offline receipt provenance for the registered teacher pilot. Standard library only."""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "outputs/deepseek-teacher-pilot40-live-20261111"
SCENES = ("04cd6269", "ef36e27a")
SENSE = "sense_nearby_entities"
C_DESCRIPTION = "27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227"


def read(path):
    return json.loads(path.read_bytes())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def indexed(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get(key)].append(row)
    return grouped


def capture(rows, kind):
    return [row["data"] for row in rows if row["kind"] == kind]


def blob(body):
    data = base64.b64decode(body["base64"], validate=True)
    if len(data) != body["length"] or sha(data) != body["sha256"]:
        raise ValueError("ModelIO body failed its embedded digest")
    return data


def call_signature(raw):
    fun = raw["function"]
    try:
        args = json.loads(fun["arguments"])
    except (TypeError, ValueError):
        return [raw.get("id"), fun["name"], {"__invalid_json__": fun["arguments"]}]
    if fun["name"] == SENSE and args == {}:
        args = {"input_json": {}}
    return [raw.get("id"), fun["name"], args]


def c_schema(body):
    senses = [f["function"] for f in body["tools"] if f["function"]["name"] == SENSE]
    if not senses:
        return "unexposed"
    if len(senses) != 1:
        return "mismatch"
    decl = senses[0]
    params = decl["parameters"]
    optional = params == {"type": "object", "properties": {"input_json": {
        "anyOf": [{"type": "string"}, {"additionalProperties": True, "type": "object"}]}}}
    return "C" if sha(decl["description"].encode()) == C_DESCRIPTION and optional else "mismatch"


def tool_echo(messages):
    groups = []
    stray = []
    for msg in messages:
        if msg["role"] == "assistant":
            groups.append((msg, []))
        elif msg["role"] == "tool":
            if groups:
                groups[-1][1].append(msg)
            else:
                stray.append(msg.get("tool_call_id"))
    return groups, stray


def scope_sense(argtext, output, sim_requests):
    try:
        args = json.loads(argtext)
    except (TypeError, ValueError):
        args = None
    if args == {}:
        shape = "no_args"
    elif args == {"input_json": {}}:
        shape = "explicit_empty"
    else:
        shape = "parameterized"
    try:
        data = json.loads(output.get("content") or "")
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    fleet = [r for r in sim_requests if r["method"] == "GET" and r["endpoint"] == "/drones"]
    observed = [m.group(1) for r in sim_requests if r["method"] == "GET" and
                (m := re.fullmatch(r"/drones/([^/]+)/nearby", r["endpoint"]))]
    ids = data.get("sensed_drone_ids")
    board = data.get("blackboard")
    fleet_ids = [d.get("id") for d in fleet[0]["response"]] if len(fleet) == 1 and isinstance(fleet[0]["response"], list) else None
    result = {"arguments_form": shape, "listed_fleet_ids": fleet_ids, "nearby_get_ids": observed,
              "returned_drone_ids": ids, "per_drone_ids": list(data.get("per_drone") or {}),
              "blackboard_sha256": sha(normalized(board).encode()) if isinstance(board, dict) else None}
    params = args.get("input_json", {}) if isinstance(args, dict) else None
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = None
    expected_ids = params.get("drone_ids") if isinstance(params, dict) else None
    if expected_ids is None:
        expected_ids = fleet_ids
    result["observations_match"] = bool(isinstance(ids, list) and len(ids) > 0 and isinstance(expected_ids, list) and
        Counter(ids) == Counter(observed) == Counter(result["per_drone_ids"]) == Counter(expected_ids))
    result["board_matches_requested_visibility"] = bool(isinstance(params, dict) and
        isinstance(board, dict) == bool(params.get("include_blackboard", True)))
    return result


def audit_skills(trace, timeline, prior, counts, problem):
    """Bind pre-defined subroutine executions through their own per-task plan and evidence IDs."""
    requests = [r for r in timeline["requests"] if (r.get("origin") or {}).get("kind") == "skill"]
    if not requests:
        return []
    trace_events = trace["harness"]["events"]
    starts = indexed([e for e in trace_events if e["kind"] == "model_control_tool"], "tool_call_id")
    finishes = indexed([e for e in trace_events if e["kind"] == "public_call_end"], "call_id")
    evidence = indexed(trace["task_progress"]["events"], "tool_call_id")
    calls = {c["id"]: c for _, group in prior for c in group}
    controls = defaultdict(list)
    for e in trace_events:
        if e["kind"] != "control_result":
            continue
        cid = e["tool_call_id"]
        c = calls.get(cid)
        if not c or c["receipt_text"] != e["content"] or len(starts[cid]) != 1:
            problem("skill_control_receipt", id=cid)
            continue
        decoded = json.loads(e["content"])
        controls[decoded.get("plan_id")].append((starts[cid][0]["sequence"], e["sequence"], cid))
    plans = []
    for plan in sorted({r["origin"].get("plan_id") for r in requests}):
        public = [e for e in trace_events if e["kind"] == "public_call_start" and e["plan_id"] == plan]
        observed = [r for r in requests if r["origin"].get("plan_id") == plan]
        children = []
        for e in public:
            sid = e["call_id"]
            framed = [cid for start, end, cid in controls[plan] if start < e["sequence"] < end]
            end, ledger = finishes[sid], evidence[sid]
            if len(end) != 1 or len(ledger) != 1 or len(framed) != 1:
                problem("skill_child_ownership", plan_id=plan, child=sid, parents=framed)
            else:
                digest = sha(json.dumps(ledger[0].get("payload"), sort_keys=True, default=str).encode())
                if (e["evidence_id"] != end[0]["evidence_id"] or ledger[0]["evidence_id"] != e["evidence_id"]
                        or end[0].get("receipt_sha256") != digest):
                    problem("skill_child_receipt", plan_id=plan, child=sid)
            children.append({"id": sid, "name": e["call"]["tool"], "action": e["call"]["action"],
                             "model_parent_ids": framed, "evidence_id": e["evidence_id"]})
        # Multi-request children can share a tool name. Preserve plan-level provenance in such cases.
        one_to_one = [(c["name"], c["action"]) for c in children] == [(r["origin"]["tool"], r["action"]) for r in observed]
        items = []
        for order, r in enumerate(observed):
            candidates = [children[order]] if one_to_one else [c for c in children if c["name"] == r["origin"]["tool"]]
            parents = sorted({cid for c in candidates for cid in c["model_parent_ids"]})
            if not parents:
                problem("skill_request_without_parent", plan_id=plan, request_id=r["request_id"])
            counts["skill_requests_with_multiple_possible_parent_ids"] += len(parents) > 1
            counts["skill_actions_with_multiple_possible_parent_ids"] += r["action"] and len(parents) > 1
            fields = {k: r[k] for k in ("request_id", "method", "endpoint", "action", "committed")}
            items.append(dict(fields, tool=r["origin"]["tool"], model_parent_ids=parents,
                              events=[e["event_id"] for e in timeline["events"] if e["request_id"] == r["request_id"]],
                              possible_child_ids=[c["id"] for c in candidates]))
        plans.append({"plan_id": plan, "ordered_one_to_one_subcalls": one_to_one, "children": children, "requests": items})
        counts["skill_subcalls"] += len(children)
        counts["skill_public_requests"] += len(observed)
        counts["skill_action_requests"] += sum(r["action"] for r in observed)
        counts["skill_committed_action_requests"] += sum(r["action"] and r["committed"] for r in observed)
    return plans


def audit_task(scene, folder, boundary, spec, counts, sealcheck):
    evidence = [folder / "boundary.json", folder / "actions.json", scene / boundary["archive"]]
    rows = [json.loads(line) for line in evidence[2].read_bytes().splitlines()]
    command = rows[0]["command_id"]
    traces = list((scene / "policy/toolchains").glob(f"*_{command}_*.json"))
    if len(traces) != 1:
        raise ValueError(f"Expected one public trace for {folder.name}")
    evidence += traces
    timeline = read(evidence[1])
    trace = read(traces[0])
    injections = {i["tool_call_id"]: i["text"] for i in trace["task_progress"]["task_evidence"]["injections"]}
    result = {"task_id": boundary["task_id"], "status": "archived", "reason": boundary["reason"],
              "command_id": command, "sdk_archive": boundary["archive"], "archive_close": rows[-1]["data"],
              "turns": [], "issues": []}
    issues = result["issues"]
    def problem(code, **details):
        issues.append({"code": code, **details})
        counts["issue_" + code] += 1
    if (sha(trace["command"].encode()) != spec["command_sha256"] or trace["command_id"] != command
            or any(r["command_id"] != command for r in rows) or boundary["task_id"] != spec["task_id"]
            or rows[-1]["kind"] != "archive_end"):
        problem("command_or_archive_boundary")

    active = [r for r in timeline["requests"] if (r.get("origin") or {}).get("kind") == "model_tool"]
    req_by_call = indexed([dict(r, cid=r["origin"].get("tool_call_id")) for r in active], "cid")
    req_by_id = indexed(timeline["requests"], "request_id")
    if len({r["request_id"] for r in timeline["requests"]}) != len(timeline["requests"]):
        problem("duplicate_sim_request_id")
    if len({e["event_id"] for e in timeline["events"]}) != len(timeline["events"]):
        problem("duplicate_sim_event_id")
    events_by_call = defaultdict(list)
    for event in timeline["events"]:
        origin = event.get("origin") or {}
        parents = req_by_id[event["request_id"]]
        if origin.get("kind") == "model_tool":
            counts["model_tool_events"] += 1
            events_by_call[origin.get("tool_call_id")].append(event)
        elif origin.get("kind") == "skill":
            counts["skill_events"] += 1
        if (len(parents) != 1 or parents[0]["origin"] != origin or not parents[0]["action"] or
                parents[0]["method"] != "POST" or event["committed"] != parents[0]["committed"]):
            problem("event_origin", event_id=event["event_id"], request_id=event["request_id"])
    counts["model_tool_public_requests"] += len(active)
    counts["model_tool_action_requests"] += sum(bool(r["action"]) for r in active)
    counts["model_tool_committed_action_requests"] += sum(bool(r["action"] and r["committed"]) for r in active)
    for r in timeline["requests"]:
        if r["action"] and (r.get("origin") or {}).get("kind") not in {"model_tool", "skill"}:
            problem("action_without_identified_source", request_id=r["request_id"])
    reqs = capture(rows, "http_request")
    chunks = indexed(capture(rows, "http_response_chunk"), "attempt_id")
    ends = indexed(capture(rows, "model_end"), "logical_call_id")
    starts = indexed(capture(rows, "tool_start"), "raw_tool_call_id")
    receipts = indexed(capture(rows, "tool_end"), "tool_span_id")
    raw_ids, prior, expected_files = [], [], set()
    requests_by_model = Counter(r["logical_call_id"] for r in reqs)
    if (requests_by_model != Counter(r["logical_call_id"] for r in capture(rows, "model_start"))
            or requests_by_model != Counter(r["logical_call_id"] for r in capture(rows, "model_end"))
            or any(v != 1 for v in requests_by_model.values())):
        problem("model_span_inventory")
    receipt_ids = Counter((e.get("output") or {}).get("tool_call_id") for e in capture(rows, "tool_end"))
    for cid, frequency in receipt_ids.items():
        if not cid:
            problem("missing_receipt_id", occurrences=frequency)
        elif frequency > 1:
            problem("duplicate_receipt_id", id=cid, occurrences=frequency)
    signatures = Counter()
    for index, event in enumerate(reqs, 1):
        logical, attempt = event["logical_call_id"], event["attempt_id"]
        wire = folder / "provider-wire"
        request = wire / f"turn-{index:02d}.request.json"
        response = wire / f"turn-{index:02d}.response.json"
        timing = folder / "provider-timing" / f"attempt-{index:02d}.json"
        evidence.extend((request, timing))
        expected_files.add(request)
        stamp, raw_in = read(timing), request.read_bytes()
        body = json.loads(raw_in)
        row = {"turn": index, "sdk_model_id": logical, "sdk_attempt_id": attempt, "request_sha256": sha(raw_in),
               "dispatched": stamp["dispatched"], "sdk_received": stamp["sdk_received"], "schema": c_schema(body)}
        result["turns"].append(row)
        counts["wire_requests"] += 1
        counts["dispatched_requests"] += bool(stamp["dispatched"])
        counts["schema_" + row["schema"]] += 1
        if row["schema"] == "mismatch" or blob(event["body"]) != raw_in or stamp["request_body_sha256"] != sha(raw_in):
            problem("request_contract", turn=index)
        groups, stray = tool_echo(body["messages"])
        if stray or len(groups) != len(prior):
            problem("history_group_count", turn=index)
        # All preceding groups are checked, including the first echo and later echoes.
        for (echo, tools), (msg, calls) in zip(groups, prior):
            if (echo.get("content") or "") != (msg.get("content") or "") or echo.get("reasoning_content") != msg.get("reasoning_content") or [call_signature(c) for c in echo.get("tool_calls", [])] != [c["execution_signature"] for c in calls]:
                problem("assistant_history", turn=index)
            echoed = indexed(tools, "tool_call_id")
            if Counter(m.get("tool_call_id") for m in tools) != Counter(c["id"] for c in calls):
                problem("history_receipt_ids", turn=index)
            for c in calls:
                group = echoed[c["id"]]
                if len(group) == 1 and c["receipt_text"] is not None:
                    correct = (group[0].get("content") == c["receipt_text"] + injections.get(c["id"], ""))
                    c["history_echoes"] += 1
                    c["dispatched_history_echoes"] += bool(stamp["dispatched"])
                    if not correct:
                        problem("history_receipt_content", turn=index, id=c["id"])
        model_end = ends[logical]
        if not response.exists():
            row["stop_reason"] = stamp["stop_reason"]
            counts["undispatched_guards" if not stamp["dispatched"] else "dispatched_without_response"] += 1
            if stamp["dispatched"] or stamp["sdk_received"] or chunks[attempt] or len(model_end) != 1 or model_end[0]["status"] != "error":
                problem("missing_provider_generation", turn=index)
            continue
        expected_files.add(response)
        evidence.append(response)
        raw_out = response.read_bytes()
        payload = json.loads(raw_out)
        if len(payload.get("choices", [])) != 1:
            problem("provider_choices", turn=index)
            continue
        msg = payload["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        args = [call_signature(c) for c in calls]
        text, reasoning = msg.get("content") or "", msg.get("reasoning_content")
        group_cards = []
        prior.append((msg, group_cards))
        row.update(response_sha256=sha(raw_out), provider_id=payload.get("id"), finish_reason=payload["choices"][0].get("finish_reason"),
                   content_chars=len(text), content_sha256=sha(text.encode()), reasoning_present=bool(reasoning),
                   reasoning_chars=len(reasoning or ""), reasoning_sha256=sha(reasoning.encode()) if reasoning is not None else None, calls=group_cards)
        sdk_outputs = model_end[0].get("output", {}).get("result", []) if len(model_end) == 1 and model_end[0]["status"] == "returned" else []
        archived_chunks = b"".join(blob(c["body"]) for c in chunks[attempt])
        if archived_chunks != raw_out or stamp["response_body_sha256"] != sha(raw_out) or not stamp["sdk_received"] or not stamp["dispatched"]:
            problem("response_bytes", turn=index)
        same = len(sdk_outputs) == 1 and msg.get("role") == "assistant"
        if same:
            sdk = sdk_outputs[0]
            same = (sdk.get("type") == "ai" and (sdk.get("content") or "") == text and
                    sdk["additional_kwargs"].get("reasoning_content") == reasoning and not sdk.get("invalid_tool_calls") and
                    [[c.get("id"), c.get("name"), c.get("args")] for c in sdk.get("tool_calls", [])] == args)
        if not same:
            problem("sdk_generation", turn=index)
        row["sdk_generation_matches"] = same
        counts["provider_generations"] += 1
        counts["sdk_generation_matches"] += same
        counts["finish_" + str(row["finish_reason"])] += 1
        counts["generations_with_reasoning_text"] += bool(reasoning)
        counts["generations_with_content"] += bool(text)
        counts["multi_call_messages" if len(calls) > 1 else "single_call_messages" if calls else "no_call_messages"] += 1
        for raw_call, signature in zip(calls, args):
            cid, name, parsed = signature
            raw_ids.append(cid)
            counts["tool_calls"] += 1
            counts["calls_in_multi_call_messages" if len(calls) > 1 else "calls_in_single_call_messages"] += 1
            ser = normalized([name, parsed])
            counts["repeated_tool_and_args_after_first_within_task"] += bool(signatures[ser])
            signatures[ser] += 1
            card = {"id": cid, "name": name, "raw_arguments": raw_call["function"]["arguments"],
                    "execution_signature": signature, "span_ids": [], "history_echoes": 0, "dispatched_history_echoes": 0,
                    "receipt_text": None}
            group_cards.append(card)
            bound = [s for s in starts[cid] if s["source_model_id"] == logical]
            sim = req_by_call[cid]
            card["public_requests"] = [{k: r[k] for k in ("request_id","method","endpoint","action","committed")} for r in sim]
            card["events"] = [v["event_id"] for v in events_by_call[cid]]
            if not sim:
                counts["calls_with_no_direct_public_simulator_request"] += 1
            if any(r["origin"].get("tool") != name for r in sim):
                problem("action_tool_name", turn=index, id=cid)
            if len(bound) != 1:
                problem("missing_start" if not bound else "duplicate_start", turn=index, id=cid)
            for s in bound:
                span = s["tool_span_id"]
                card["span_ids"].append(span)
                issued = s["input"] if isinstance(s["input"], dict) else {}
                if s["source_relation"] != "unique" or issued.get("id") != cid or issued.get("name") != name or issued.get("args") != parsed:
                    problem("start_id_source_or_args", turn=index, id=cid)
                finished = receipts[span]
                if len(finished) != 1:
                    problem("missing_end" if not finished else "duplicate_end", turn=index, id=cid)
                    continue
                end = finished[0]
                out = end["output"] if isinstance(end.get("output"), dict) else {}
                if end["status"] != "returned" or end.get("tool_call_id_mismatch") or out.get("tool_call_id") != cid or out.get("name") != name or not isinstance(out.get("content"), str):
                    problem("receipt_links", turn=index, id=cid)
                    continue
                counts["linked_tool_receipts"] += 1
                counts["tool_status_" + str(out.get("status"))] += 1
                card.update(receipt_text=out["content"], receipt_sha256=sha(out["content"].encode()), receipt_status=out.get("status"))
                if name == SENSE:
                    c = scope_sense(card["raw_arguments"], out, sim)
                    card["sensing"] = c
                    counts["sense_calls"] += 1
                    counts["sense_" + c["arguments_form"]] += 1
                    counts["sense_receipts_with_observations_and_board"] += c["observations_match"] and bool(c["blackboard_sha256"])
                    if not c["observations_match"] or not c["board_matches_requested_visibility"]:
                        counts["sense_without_requested_observations_or_board"] += 1
                    if row["schema"] != "C":
                        problem("sense_without_c_contract", turn=index, id=cid)
        if len(calls) > 1:
            completion_order = [e["tool_span_id"] for e in capture(rows, "tool_end") if any(e["tool_span_id"] in c["span_ids"] for c in group_cards)]
            row["receipt_order_call_ids"] = [c["id"] for span in completion_order for c in group_cards if span in c["span_ids"]]
            counts["multi_call_messages_with_changed_return_order"] += row["receipt_order_call_ids"] != [c["id"] for c in group_cards]
    result["subroutine_plans"] = audit_skills(trace, timeline, prior, counts, problem)
    for row in result["turns"]:
        for c in row.get("calls", []):
            if c["history_echoes"]:
                counts["calls_echoed_at_least_once"] += 1
                if c["dispatched_history_echoes"]:
                    counts["calls_echoed_in_dispatched_history"] += 1
                else:
                    counts["calls_echoed_only_in_undispatched_guard"] += 1
            elif row == result["turns"][-1]:
                counts["calls_without_following_request"] += 1
            else:
                counts["calls_missing_echo_in_following_request"] += 1
            del c["receipt_text"]
    for cid, frequency in Counter(raw_ids).items():
        if not cid:
            problem("missing_raw_id", id=cid)
        if frequency > 1:
            problem("duplicate_provider_id", id=cid, occurrences=frequency)
    for label, ids in (("orphan_start", starts), ("orphan_public_request", req_by_call), ("orphan_event", events_by_call)):
        for cid in (k for k,v in ids.items() if v and k not in raw_ids):
            problem(label, id=cid)
    all_spans = {s["tool_span_id"] for group in starts.values() for s in group}
    for span in receipts.keys() - all_spans:
        problem("orphan_end", span_id=span)
    if set((folder / "provider-wire").glob("*")) != expected_files or len(list((folder / "provider-timing").glob("*.json"))) != len(reqs):
        problem("wire_timing_inventory")
    for f in evidence:
        sealcheck(f)
    result["full_task_archive"] = bool(result["archive_close"].get("complete"))
    counts["tasks_full_sdk_archive" if result["full_task_archive"] else "tasks_incomplete_sdk_archive"] += 1
    counts["tasks_with_linkage_issue"] += bool(issues)
    result["provider_generations"] = len(prior)
    result["raw_calls"] = len(raw_ids)
    result["distinct_call_ids"] = len(set(raw_ids))
    result["receipt_count"] = sum(receipt_ids.values())
    result["distinct_receipt_ids"] = len(receipt_ids)
    result["raw_id_multiset_equals_receipt_ids"] = Counter(raw_ids) == receipt_ids
    result["all_provider_generations_match_sdk"] = all(r.get("sdk_generation_matches", not r["dispatched"]) for r in result["turns"])
    return result


def audit_scene(scene, partial):
    manifest, session = scene / "run-input.json", scene / "session.json"
    frozen = ROOT / "docs/specs/04-deepseek-pilot40/pilot40.json"
    prepared = scene.parent.parent / "pilot40.json"
    if sha(frozen.read_bytes()) != sha(prepared.read_bytes()):
        raise ValueError("Prepared selection differs from the frozen teacher pilot")
    spec = next(s for s in read(frozen)["sessions"] if s["session_id"] == scene.name)
    seals = {}
    if not partial:
        record = read(scene / "worker-exit.json")
        score_seal = read(scene / "scorer-exit.json")
        seals = record["files"] | score_seal["files"]
        if not (scene / "session_scores.json").exists():
            raise ValueError("Missing independently finished scene")
    def check(path):
        if not partial and seals.get(str(path.relative_to(scene))) != sha(path.read_bytes()):
            raise ValueError(f"Missing or mismatched seal on {path}")
    tasks = read(manifest)["expected_task_ids"]
    if len(set(tasks)) != 20 or len(tasks) != 20 or tasks != [r["task_id"] for r in spec["tasks"]]:
        raise ValueError("Invalid plan")
    session_rows = read(session)["tasks"] if session.exists() else []
    if not partial:
        if [r["task_id"] for r in session_rows] != tasks:
            raise ValueError("Task order differs from the planned sequence")
        check(session)
        check(scene/"session_scores.json")
    keys = ("tool_calls", "linked_tool_receipts", "calls_without_following_request",
            "calls_echoed_in_dispatched_history", "calls_echoed_only_in_undispatched_guard",
            "calls_missing_echo_in_following_request", "dispatched_without_response",
            "issue_duplicate_provider_id", "issue_missing_raw_id", "issue_duplicate_receipt_id", "issue_missing_receipt_id",
            "issue_missing_start", "issue_missing_end", "issue_duplicate_start", "issue_duplicate_end", "issue_orphan_end",
            "issue_orphan_public_request", "issue_event_origin", "sense_no_args", "sense_explicit_empty",
            "sense_without_requested_observations_or_board", "schema_unexposed", "schema_mismatch")
    counts, task_rows = Counter(dict.fromkeys(keys, 0)), []
    for idx, tid in enumerate(tasks, 1):
        f = scene / "tasks" / f"{idx:02d}-{tid}"
        b = f / "boundary.json"
        if b.exists():
            row = audit_task(scene, f, read(b), spec["tasks"][idx-1], counts, check)
            counts["archived_tasks"] += 1
        else:
            state = session_rows[idx-1]["status"] if session_rows else "pending"
            if not partial and state != "unstarted":
                raise ValueError(f"Missing completed task boundary: {tid}")
            row = {"task_id": tid, "status": state, "reason": session_rows[idx-1]["reason"] if session_rows else None}
        row["slot"] = idx
        task_rows.append(row)
    return {"schema_version": 1, "scene_id": scene.name, "planned_slots": len(tasks), "final_scene_seals_verified": not partial,
            "worker_exit_sha256": sha((scene/"worker-exit.json").read_bytes()) if not partial else None,
            "scorer_exit_sha256": sha((scene/"scorer-exit.json").read_bytes()) if not partial else None,
            "expected_sense_description_sha256": C_DESCRIPTION, "counts": dict(sorted(counts.items())), "tasks": task_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=BASE / "run")
    parser.add_argument("--scene", required=True, choices=SCENES)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--partial", action="store_true", help="Inspect closed task boundaries before final seals")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.run.resolve()):
        parser.error("Audit output must be outside the run")
    result = audit_scene(args.run / "sessions" / args.scene, args.partial)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
