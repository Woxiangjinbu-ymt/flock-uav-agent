"""Independent, offline verification of the fixed-response DeepSeek pilot."""
import base64
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .verify import (ROOT, canonical_sha, check_actions, check_initial, check_prepare,
                     check_projection, check_responses, file_sha, original_scene, owned,
                     process_evidence, read, require)


def _ledger(run, config, limits):
    """Replay the durable journal without acquiring locks, reopening trials or repairing files."""
    from harness_live_cost import LiveCostPolicy, LiveCostLedger

    path = run / "cost-ledger.jsonl"
    raw = path.read_bytes()
    require(raw.endswith(b"\n"), "Truncated cost journal")
    rows = [json.loads(line) for line in raw.splitlines()]
    prev = None
    for i, row in enumerate(rows, 1):
        require(row["sequence"] == i and row["previous_sha256"] == prev and
                row["sha256"] == canonical_sha({k: row[k] for k in ("sequence", "previous_sha256", "event")}),
                "Corrupt cost journal chain")
        prev = row["sha256"]
    require(read(path.with_suffix(".jsonl.head.json")) == {"sequence": len(rows), "sha256": prev},
            "Cost journal differs from its durable head")
    currency = config["currency"]
    rates = limits["accounts"][currency]
    policy = LiveCostPolicy(currency, rates["rate_peak_input_cache_miss_per_million"],
                            rates["rate_peak_output_per_million"], limits["max_input_tokens_for_fee_reservation"],
                            limits["max_output_tokens_per_request"], limits["max_request_bytes"])
    state = LiveCostLedger(path, policy, config["batch_cap"])._replay(rows)
    marker = path.with_suffix(".jsonl.blocked.json")
    require(state["blocked"] == marker.exists(), "Budget block marker mismatch")
    if state["blocked"]:
        require(read(marker)["reason"] == state["stop_reason"], "Inconsistent durable stop reason")
    require(all(Decimal(t["budget"]) == Decimal(config["per_task_cap"]) for t in state["trials"].values()),
            "Per-task budget changed")
    return state


def _calls(msg):
    return [(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"]))
            for c in msg.get("tool_calls", [])]


def _history(messages, command, previous, receipts, injections=None):
    injections = injections or {}
    users = [m for m in messages if m["role"] == "user"]
    require(len(users) == 1 and users[0]["content"] == command + injections.get(None, ""),
            "Original public command or public evidence annotation changed")
    assistants = [m for m in messages if m["role"] == "assistant"]
    require(len(assistants) == len(previous), "Missing assistant history or cross-task conversation")
    expected_ids = []
    for echoed, prior in zip(assistants, previous, strict=True):
        require(echoed.get("reasoning_content") == prior["reasoning_content"] and
                (echoed.get("content") or "") == (prior.get("content") or "") and
                _calls(echoed) == _calls(prior), "Historical assistant reasoning, content or calls changed")
        expected_ids.extend(call_id for call_id, _, _ in _calls(prior))
    tools = [m for m in messages if m["role"] == "tool"]
    require(sorted(m["tool_call_id"] for m in tools) == sorted(expected_ids), "Missing or duplicate tool feedback")
    for msg in tools:
        cid = msg["tool_call_id"]
        require(receipts[cid] + injections.get(cid, "") == msg["content"],
                "Tool feedback differs from archived receipt and its public evidence annotation")


def _injections(run, task, command_id, seals):
    matches = list((run / "policy/toolchains").glob(f"*_{command_id}_*.json"))
    require(len(matches) == 1 and str(matches[0].relative_to(run)) in seals, "Missing sealed command trace")
    trace = read(matches[0])
    require(trace["command_id"] == command_id and trace["command"] == task["content"],
            "Command trace not bound to task and model archive")
    marker = "\n\n[M1-R task evidence; derived from actual tool receipts]\n"
    result = {}
    for row in trace["task_progress"]["task_evidence"]["injections"]:
        ident, suffix = row["tool_call_id"], row["text"]
        require(ident not in result and suffix.startswith(marker), "Duplicate or unknown public annotation")
        annotation = json.loads(suffix[len(marker):])
        require(annotation["schema"] in {"m1-r1", "m1-r2", "m1-r3"} and annotation["mission_completion"] == "not_evaluated"
                and annotation["original_task_controls"] is True, "Unknown public evidence declaration")
        result[ident] = suffix
    return result


def _wire(run, folder, boundary, task, ledger, limits, seals):
    import sys
    if str(ROOT / "agent4drone") not in sys.path:
        sys.path.insert(0, str(ROOT / "agent4drone"))
    from model_io_archive import verify_archive
    from thinking_events.sense_example_prepare import DESCRIPTIONS, SENSE

    path = owned(run, boundary["archive"])
    require(boundary["archive"] in seals, "Unsealed SDK archive")
    quality = verify_archive(path)
    finished = boundary["reason"] == "agent_finished"
    require(quality["valid"] and quality["complete"] == finished, "Invalid SDK archive lifecycle")
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    injections = _injections(run, task, records[0]["command_id"], seals)
    sent, received, steps, spans, receipts = [], {}, {}, {}, {}
    statuses = []
    for record in records:
        d, kind = record["data"], record["kind"]
        if kind == "http_request":
            sent.append((d["attempt_id"], d["logical_call_id"], base64.b64decode(d["body"]["base64"], validate=True)))
        elif kind == "http_response_start":
            statuses.append(d["status_code"])
            received[d["attempt_id"]] = bytearray()
        elif kind == "http_response_chunk":
            received[d["attempt_id"]].extend(base64.b64decode(d["body"]["base64"], validate=True))
        elif kind == "tool_start":
            call_id = d["raw_tool_call_id"]
            require(call_id not in steps and d["source_relation"] == "unique", "Ambiguous call ID")
            steps[call_id] = (d["source_model_id"], d["input"]["name"], d["input"]["args"])
            spans[d["tool_span_id"]] = call_id
        elif kind == "tool_end":
            call_id = spans[d["tool_span_id"]]
            require(d["status"] == "returned" and d["output"]["tool_call_id"] == call_id,
                    "SDK tool has no matching receipt")
            receipts[call_id] = d["output"]["content"]
    require(statuses == boundary["http_statuses"] == [200] * len(received), "SDK HTTP dispositions changed")
    private = (b"/check/", b"execution_check_apis", b"related_apis", b"mark-done", b"is_passed",
               b"world.pkl", b"eligibility.json", b"initial-score.json", b"boundary.json")
    previous, seen_calls, turns = [], set(), []
    attempts = ledger["trials"][task["id"]]["attempt_ids"]
    require(len(attempts) <= len(sent) <= len(attempts) + 1, "Unexpected SDK/dispatch discrepancy")
    expected_wire = set()
    for number, (attempt_id, logical_id, raw) in enumerate(sent, 1):
        body = json.loads(raw)
        require(not any(token in raw for token in private), "Private checker or admin data in teacher input")
        require(body["model"] == limits["teacher_model"] and body["thinking"] == {"type": limits["teacher_thinking"]}
                and body["reasoning_effort"] == limits["reasoning_effort"] and body.get("stream", False) is False
                and body["max_tokens"] == limits["max_output_tokens_per_request"], "Teacher SDK parameters changed")
        functions = {t["function"]["name"]: t["function"] for t in body.get("tools", [])}
        if SENSE in functions:
            require(hashlib.sha256(functions[SENSE]["description"].encode()).hexdigest() == DESCRIPTIONS["B"],
                    "Unexpected public sensing tool description")
        _history(body["messages"], task["content"], previous, receipts, injections)
        dispatched = number <= len(attempts)
        turn = {"turn": number, "attempt_id": attempt_id, "archive": str(path.relative_to(run)),
                "request_sha256": hashlib.sha256(raw).hexdigest(), "dispatched": dispatched,
                "history_reasoning_messages": len(previous), "tool_calls": [], "usage": None,
                "reasoning_present": False, "response_sha256": None, "sdk_response": attempt_id in received}
        turns.append(turn)
        if not dispatched:
            require(not finished and number == len(sent) and attempt_id not in received,
                    "A refused request reached the fixture")
            continue
        req = folder / "fixture-wire" / f"turn-{number:02d}.request.json"
        resp = req.with_name(req.name.replace(".request.", ".response."))
        expected_wire.update([req.name, resp.name])
        for p in (req, resp):
            require(str(p.relative_to(run)) in seals, "Unsealed fixture wire")
        charged = ledger["attempts"][attempts[number - 1]]
        require(charged["request_body_sha256"] == turn["request_sha256"] and
                charged["request_body_length"] == len(raw) and req.read_bytes() == raw,
                "Reservation does not match the exact request received by the fixture")
        reply_raw = resp.read_bytes()
        reply = json.loads(reply_raw)
        turn.update(response_sha256=hashlib.sha256(reply_raw).hexdigest(), request=str(req.relative_to(run)),
                    response=str(resp.relative_to(run)), reservation_id=charged["reservation_id"])
        require(reply["model"] == limits["teacher_model"] and reply["system_fingerprint"] == "offline-fixed-teacher-v1",
                "Fixture response identity changed")
        msg = reply["choices"][0]["message"]
        require(msg["role"] == "assistant" and isinstance(msg.get("reasoning_content"), str)
                and msg["reasoning_content"].strip(), "Missing separate fixture reasoning")
        turn["reasoning_present"] = True
        if charged["status"] == "locked":
            require(not finished and "usage" not in reply and attempt_id not in received, "Unknown usage miscounted")
            continue
        require(charged["status"] == "settled" and charged["response_body_sha256"] == turn["response_sha256"]
                and received.get(attempt_id) == reply_raw and
                all(reply["usage"][key] == n for key, n in charged["usage"].items()),
                "Receipt or usage differs between SDK, fixture and cost ledger")
        turn["usage"] = reply["usage"]
        previous.append(msg)
        for call_id, name, args in _calls(msg):
            require(name in functions and call_id not in seen_calls and
                    steps.get(call_id) == (logical_id, name, args) and call_id in receipts,
                    "Wire call does not match tool execution and receipt")
            turn["tool_calls"].append({"id": call_id, "name": name,
                                       "receipt_sha256": canonical_sha(receipts[call_id])})
            seen_calls.add(call_id)
    require(set(steps) == seen_calls and {p.name for p in (folder / "fixture-wire").iterdir()} == expected_wire,
            "Extra tool or fixture dispatch outside the archived requests")
    if finished:
        require(len(sent) == len(attempts) == len(received) == 5 and not _calls(previous[-1]),
                "Fixed-response task did not reach its terminal reply")
    return turns


def _scores(folder, task, row, boundary, worker, scorer):
    tree = task["execution_check_apis"]
    digest = canonical_sha(tree)
    result = {}
    for name, snapshot, field in (("initial-score", "initial-world", "initial_world_sha256"),
                                  ("score", "world", "world_sha256")):
        relative = row["directory"] + "/"
        require(relative + name + ".json" in scorer["files"], "Missing independent original checker result")
        score = read(folder / (name + ".json"))
        require(score["scorer_pid"] == scorer["pid"] and score["worker_pid"] == worker["pid"] and
                score["task_id"] == task["id"] and score["checker_sha256"] == boundary["checker_sha256"] == digest
                and score["world_sha256"] == boundary[field] == worker["files"][relative + snapshot + ".pkl"],
                "Task score does not match sealed snapshot or original checker")
        result[name] = check_responses(tree, score["checker"])[0]
    finished = row["reason"] == "agent_finished"
    require(boundary["marked_done"] is (finished and result["score"]), "Incorrect task completion mutation")
    if finished:
        require(row["directory"] + "/eligibility.json" in worker["files"], "Missing continuation gate")
        gate = read(folder / "eligibility.json")
        require(gate["pid"] not in (worker["pid"], scorer["pid"]) and gate["passed"] is result["score"]
                and gate["task_id"] == task["id"] and gate["world_sha256"] == boundary["world_sha256"]
                and gate["network_attempts"] == [] and not gate["agent_imported"], "Invalid continuation gate")
    return result["initial-score"], result["score"]


def _tool_requests(actions, turns):
    events = check_actions(actions)
    tools = {c["id"]: c for turn in turns for c in turn["tool_calls"]}
    for call in tools.values():
        call["simulator_request_ids"] = []
    for r in actions["requests"]:
        origin = r["origin"]
        if origin["kind"] == "agent_initialization":
            require(not r["action"], "Action outside an identified model tool")
            continue
        call = tools.get(origin.get("tool_call_id"))
        require(origin["kind"] == "model_tool" and call is not None and call["name"] == origin["tool"],
                "Simulator request does not join to a declared and executed SDK tool")
        require(0 <= r["relative_seconds"] <= r["ended_seconds"], "Invalid simulator request timestamps")
        call["simulator_request_ids"].append(r["request_id"])
    require(all(c["simulator_request_ids"] for c in tools.values()), "Executed fixture tool has no simulator receipt")
    return events


def _interval(observed):
    start, end = (observed[k] for k in ("start_monotonic_ns", "end_monotonic_ns"))
    require(type(start) is int and type(end) is int and 0 < start <= end and
            observed["elapsed_ns"] == end - start, "Invalid monotonic request interval")
    utc = [datetime.fromisoformat(observed[k]) for k in ("start_utc", "end_utc")]
    require(all(t.tzinfo is not None and t.utcoffset() == timezone.utc.utcoffset(t) for t in utc)
            and utc[0] <= utc[1], "Invalid request UTC interval")
    return start, end


def _timing(run, folder, task_id, reason, turns, worker, required):
    directory = folder / "fixture-timing"
    if not directory.exists() and not required:
        return
    expected = [directory / f"attempt-{t['turn']:02d}.json" for t in turns]
    require(directory.is_dir() and set(directory.iterdir()) == set(expected), "Incomplete per-attempt timing")
    previous_end = worker["started_ns"]
    for path, t in zip(expected, turns, strict=True):
        require(str(path.relative_to(run)) in worker["files"], "Unsealed request timing")
        record = read(path)
        require(record["task_id"] == task_id and record["attempt"] == t["turn"] and
                record["request_body_sha256"] == t["request_sha256"] and
                record["response_body_sha256"] == t["response_sha256"] and
                record["fixture_dispatched"] is t["dispatched"] and
                record["sdk_response_received"] is t["sdk_response"] and
                record["stop_reason"] == (None if t["sdk_response"] else reason),
                "Timing record not bound to this SDK request and disposition")
        outer_start, outer_end = _interval(record["transport"])
        require(previous_end <= outer_start <= outer_end <= worker["finished_ns"],
                "Concurrent SDK attempts or timing outside the worker")
        previous_end = outer_end
        if t["dispatched"]:
            require(record["fixture_turn"] == t["turn"], "Fixture dispatch identity mismatch")
            inner_start, inner_end = _interval(record["fixture"])
            require(outer_start <= inner_start <= inner_end <= outer_end, "Invalid fixture dispatch interval")
        else:
            require(record["fixture_turn"] is None and record["fixture"] is None, "Refused request has fixture timing")
        t.update(timing=str(path.relative_to(run)), transport_elapsed_ns=outer_end - outer_start)


def _session(run, spec, rows, ledger, limits, config):
    scene = original_scene(run, spec["session_id"])
    require(file_sha(run / "source-scene.json") == spec["scene_sha256"], "Frozen teacher scene mismatch")
    worker, scorer = (process_evidence(run, role) for role in ("worker", "scorer"))
    require(worker["pid"] != scorer["pid"] and worker["finished_ns"] <= scorer["started_ns"] and
            {"source-scene.json", "session.json", "run-input.json"} <= worker["files"].keys() and
            "session_scores.json" in scorer["files"], "Independent process evidence incomplete")
    inputs, session, scores = (read(run / name) for name in ("run-input.json", "session.json", "session_scores.json"))
    require(inputs["session_id"] == session["session_id"] == scores["session_id"] == spec["session_id"] and
            inputs["expected_task_ids"] == [r["task_id"] for r in rows] and
            session["worker_pid"] == scores["worker_pid"] == worker["pid"] and scores["scorer_pid"] == scorer["pid"],
            "Session or process identity mismatch")
    require(inputs["scope"] == "offline_deepseek_teacher_pilot40" and
            all(inputs["teacher"].get(k) == v for k, v in config.items()) and
            session["teacher_mode"] == "local_fixed_replies" and session["teacher_api_seed"] is None and
            session["runtime_seed"] == spec["runtime_seed"] == inputs["teacher"]["seed"],
            "Teacher run mode or original scene seed changed")
    previous = None
    summary, expect_scores = [], []
    transitions = 0
    for task, item, row, state in zip(scene["tasks"], spec["tasks"], session["tasks"], rows, strict=True):
        require(task["id"] == item["task_id"] == row["task_id"] and
                hashlib.sha256(task["content"].encode()).hexdigest() == item["command_sha256"] and
                all(row[k] == state[k] for k in ("index", "status", "reason", "task_id")), "Changed task ledger")
        identity = {k: state[k] for k in ("session_id", "index", "task_id", "status", "reason")}
        relative = f"tasks/{item['index']:02d}-{task['id']}"
        folder = owned(run, relative)
        if state["status"] == "unstarted":
            require(not folder.exists(), "Unexpected folder for unstarted task")
            summary.append({**identity, "initial_passed": None, "terminal_passed": None, "turns": []})
            continue
        require(row["status"] == "completed" and row["directory"] == relative and
                all(f"{relative}/{name}.json" in worker["files"] for name in ("boundary", "before", "actions")),
                "Incomplete worker evidence for started task")
        before, b = (read(folder / name) for name in ("before.json", "boundary.json"))
        prep, start = before["prepare_before"], before["policy_start"]
        for p in (prep, start, b["terminal"], b["continuation"]):
            check_projection(p)
        check_prepare(prep, start)
        if previous is None:
            check_initial(scene, session, prep)
        else:
            require(prep == previous, "Continuous scene lost world or board between tasks")
            transitions += 1
        previous = b["continuation"]
        require(start["clock_seconds"] - prep["clock_seconds"] >= 4 and
                start["clock_seconds"] <= b["terminal"]["clock_seconds"] <= previous["clock_seconds"],
                "Preparation or execution clock is inconsistent")
        require(b["worker_pid"] == worker["pid"] and b["task_id"] == task["id"] and b["reason"] == row["reason"]
                and b["entry_board_sha256"] == start["blackboard_sha256"]
                and b["world_state_sha256"] == b["terminal"]["world_state_sha256"] and
                set(previous["marked_done"]) == (set(b["terminal"]["marked_done"]) | ({task["id"]} if b["marked_done"] else set())),
                "World, blackboard or mark-done not bound to this task")
        initial, terminal = _scores(folder, task, row, b, worker, scorer)
        turns = _wire(run, folder, b, task, ledger, limits, worker["files"])
        _timing(run, folder, task["id"], b["reason"], turns, worker, inputs["teacher"].get("timing_required", False))
        events = _tool_requests(read(folder / "actions.json"), turns)
        settlement = b["teacher"]
        require(settlement["actual_provider_calls"] == 0 and Decimal(settlement["actual_fee"]) == 0 and
                settlement["fixture_dispatches"] == sum(t["dispatched"] for t in turns),
                "Fixture dispatch log disagrees with the session boundary")
        summary.append({**identity, "initial_passed": initial, "terminal_passed": terminal, "turns": turns,
                        "agent_reported_success": b["agent_success"], "physical_events": events})
        expect_scores.append({"task_id": task["id"], "passed": terminal})
    require(scores["tasks"] == expect_scores and
            {p.name for p in (run / "tasks").iterdir()} == {r["directory"].split("/")[-1] for r in session["tasks"]
                                                            if r["status"] != "unstarted"},
            "Unexpected worker directories or score summary")
    return summary, transitions


def verify_batch(out_dir):
    """Reconstruct all 40 dispositions, scores, transmissions, and mock charges from disk."""
    run = Path(out_dir).resolve(strict=True)
    config, pilot, status = (read(run / name) for name in ("batch-input.json", "pilot40.json", "batch-status.json"))
    require(file_sha(run / "pilot40.json") == config["pilot_sha256"] ==
            file_sha(ROOT / "docs/specs/04-deepseek-pilot40/pilot40.json"), "Changed pilot manifest")
    case = config["case"]
    limits = pilot["pilot_limits"]
    ids = [s["session_id"] for s in pilot["sessions"]]
    require(config["sessions"] == ids == [s["session_id"] for s in status["sessions"]] and
            case in ("normal", "budget-stop", "missing-usage"), "Incorrect batch scope")
    account = limits["accounts"]["CNY"]
    require(status["complete"] is True and status["case"] == case and status["network_attempts"] == [] and
            config["transport"] == "local_fixed_replies" and config["currency"] == "CNY" and
            config["eligible_for_training"] is False and config["per_task_cap"] == account["per_task_cap"] and
            config["max_http_attempts_total"] == limits["max_http_attempts_total"] and
            config["batch_cap"] == account["request_reservation" if case == "budget-stop" else "batch_cap"],
            "Unexpected completed batch configuration")
    ledger = _ledger(run, config, limits)
    require(status["stop_reason"] == ledger["stop_reason"], "Coordinator stop and cost ledger disagree")
    tasks, transitions = [], 0
    positions = [(scene["session_id"], t["index"], t["task_id"]) for scene in pilot["sessions"] for t in scene["tasks"]]
    require(len(positions) == 40 and [(r["session_id"], r["index"], r["task_id"]) for r in status["tasks"]] == positions,
            "Lost or reordered frozen task slots")
    for spec, scene_state in zip(pilot["sessions"], status["sessions"], strict=True):
        sid = spec["session_id"]
        rows = [r for r in status["tasks"] if r["session_id"] == sid]
        directory = owned(run, "sessions/" + sid)
        if all(r["status"] == "unstarted" for r in rows):
            require(not directory.exists() and scene_state["status"] == "unstarted" and
                    scene_state["reason"] == ledger["stop_reason"], "Unstarted scene has inconsistent evidence")
            tasks.extend({**{k: r[k] for k in ("session_id", "index", "task_id", "status", "reason")},
                          "initial_passed": None, "terminal_passed": None, "turns": []} for r in rows)
        else:
            stopped_here = any(r["reason"] != "agent_finished" for r in rows)
            require(scene_state["status"] == ("stopped" if stopped_here else "finished") and
                    scene_state["reason"] == (ledger["stop_reason"] if stopped_here else None),
                    "Session disposition disagrees with the task rows")
            checked, count = _session(directory, spec, rows, ledger, limits, config)
            tasks.extend(checked)
            transitions += count
    started = [r for r in tasks if r["status"] != "unstarted"]
    finished = [r for r in started if r["reason"] == "agent_finished"]
    turns = [t for r in started for t in r["turns"]]
    dispatched = [t for t in turns if t["dispatched"]]
    present = [t for t in dispatched if t["usage"] is not None]
    for row in tasks:
        row["disposition"] = ("unstarted" if row["status"] == "unstarted" else
                              "finished" if row["reason"] == "agent_finished" else "interrupted_scored")
    require(set(ledger["trials"]) == {r["task_id"] for r in started} and
            set(ledger["attempts"]) == {t["reservation_id"] for t in dispatched} and
            len(dispatched) <= config["max_http_attempts_total"], "Ledger and fixture scope differ")
    if case == "normal":
        require(not ledger["blocked"] and ledger["active_trial"] is None and len(finished) == 40
                and transitions == 38 and len(turns) == len(dispatched) == len(present) and
                all(t["status"] == "finished" for t in ledger["trials"].values()), "Incomplete normal batch")
    else:
        stopped = [r for r in tasks if r["reason"] != "agent_finished"]
        require(ledger["blocked"] and stopped and started[-1] == stopped[0] and
                all(r["reason"] == ledger["stop_reason"] for r in stopped) and
                all(r["status"] == "unstarted" for r in stopped[1:]), "Dispatch continued after batch stop")
        reserve = Decimal(account["request_reservation"])
        if case == "budget-stop":
            trial = ledger["trials"][stopped[0]["task_id"]]
            require(ledger["stop_reason"] == "batch_budget_insufficient" and
                    Decimal(ledger["settled_conservative_charge"]) + reserve > Decimal(config["batch_cap"])
                    and Decimal(trial["settled_conservative_charge"]) + reserve <= Decimal(trial["budget"])
                    and len(turns) == len(dispatched) + 1 and not turns[-1]["dispatched"]
                    and len(present) == len(dispatched) and Decimal(ledger["locked_reservation"]) == 0,
                    "No proof of refusal before dispatch at the monetary cap")
        else:
            require(ledger["stop_reason"] == "unknown_or_invalid_usage" and turns[-1]["dispatched"]
                    and len(present) == len(dispatched) - 1 and Decimal(ledger["locked_reservation"]) == reserve,
                    "Unknown usage failed to retain its reservation")
    return {
        "case": case, "planned": len(tasks), "started": len(started), "finished": len(finished),
        "scored": len(started), "interrupted_scored": len(started) - len(finished),
        "unstarted": len(tasks) - len(started), "verified_transitions": transitions,
        "initial_passed": sum(r["initial_passed"] for r in started),
        "terminal_passed": sum(r["terminal_passed"] for r in started),
        "newly_passed": sum(not r["initial_passed"] and r["terminal_passed"] for r in started),
        "sdk_requests": len(turns), "timed_sdk_requests": sum("timing" in t for t in turns),
        "fixture_dispatches": len(dispatched), "settled_requests": len(present),
        "reasoning_responses": sum(t["reasoning_present"] for t in turns),
        "requests_with_reasoning_history": sum(t["history_reasoning_messages"] > 0 for t in turns),
        "tool_calls_with_receipts": sum(len(t["tool_calls"]) for t in turns),
        "external_provider_calls": 0, "actual_fee": "0", "formal_training_examples": 0,
        "currency": config["currency"], "batch_cap": config["batch_cap"], "per_task_cap": config["per_task_cap"],
        "simulated_conservative_charge": ledger["settled_conservative_charge"],
        "locked_reservation": ledger["locked_reservation"], "ledger_stop_reason": ledger["stop_reason"],
        "tasks": tasks,
    }
