"""Verify one provider-backed scene against its independent scores and durable reservations."""
import base64
from collections import Counter
from decimal import Decimal
import hashlib
import json
import sys

from .teacher_verify import _history, _injections, _interval, _scores
from .verify import (ROOT, check_actions, check_initial, check_prepare, check_projection, file_sha,
                     original_scene, owned, process_evidence, read, require)


def task_requests(scene, folder, task, boundary, seals, limits, ledger, sense_interface="B"):
    if str(ROOT / "agent4drone") not in sys.path:
        sys.path.insert(0, str(ROOT / "agent4drone"))
    from model_io_archive import verify_archive

    archive = owned(scene, boundary["archive"])
    require(boundary["archive"] in seals and verify_archive(archive)["valid"], "Corrupt SDK archive")
    records = [json.loads(line) for line in archive.read_bytes().splitlines()]
    requests, responses, receipts = [], {}, {}
    for r in records:
        d = r["data"]
        if r["kind"] == "http_request":
            requests.append((d["attempt_id"], base64.b64decode(d["body"]["base64"], validate=True)))
        elif r["kind"] == "http_response_start":
            responses[d["attempt_id"]] = bytearray()
        elif r["kind"] == "http_response_chunk":
            responses[d["attempt_id"]].extend(base64.b64decode(d["body"]["base64"], validate=True))
        elif r["kind"] == "tool_end":
            out = d.get("output")
            if isinstance(out, dict) and out.get("tool_call_id"):
                receipts[out["tool_call_id"]] = out.get("content")
    injections = _injections(scene, task, records[0]["command_id"], seals)
    prior = []
    trial = ledger["trials"][task["id"]]
    reserved_ids = iter(trial["attempt_ids"])
    wire = folder / "provider-wire"
    spans = folder / "provider-timing"
    expected_wire, expected_spans, turns = set(), set(), []
    require(len(trial["attempt_ids"]) <= limits["max_http_attempts_per_task"], "Task request cap exceeded")
    for i, (attempt, raw) in enumerate(requests, 1):
        req = wire / f"turn-{i:02d}.request.json"
        reply = wire / f"turn-{i:02d}.response.json"
        timing = spans / f"attempt-{i:02d}.json"
        expected_wire.add(req.name)
        expected_spans.add(timing.name)
        require(req.read_bytes() == raw and str(req.relative_to(scene)) in seals and
                str(timing.relative_to(scene)) in seals, "SDK request and sealed transport request differ")
        body = json.loads(raw)
        require(body["model"] == limits["teacher_model"] and body["thinking"]["type"] == limits["teacher_thinking"]
                and body["reasoning_effort"] == limits["reasoning_effort"] and not body.get("stream")
                and body["max_tokens"] == limits["max_output_tokens_per_request"], "Teacher request contract changed")
        if sense_interface == "C":
            sensing = [t["function"] for t in body.get("tools", []) if t["function"]["name"] == "sense_nearby_entities"]
            require(len(sensing) <= 1, "Duplicate public sensing tool")
            if sensing:
                sense = sensing[0]
                require(hashlib.sha256(sense["description"].encode()).hexdigest() ==
                        "27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227" and
                        not sense["parameters"].get("required"), "Incorrect public default for sensing")
        _history(body["messages"], task["content"], prior, receipts, injections)
        t = read(timing)
        require(t["task_id"] == task["id"] and t["attempt"] == i and t["request_body_sha256"] == file_sha(req)
                and t["sdk_received"] == (attempt in responses), "Provider timing is bound to another request")
        start, end = _interval(t["transport"])
        reservation = None
        if t["dispatched"]:
            rid = next(reserved_ids, None)
            require(rid is not None, "Provider dispatch without a reservation")
            reservation = ledger["attempts"][rid]
            require(reservation["request_body_sha256"] == t["request_body_sha256"] and
                    reservation["request_body_length"] == len(raw), "Reservation for another payload")
        row = {"number": i, "request_sha256": t["request_body_sha256"], "reservation_id": None if
               reservation is None else reservation["reservation_id"], "dispatched": t["dispatched"],
               "sdk_received": t["sdk_received"], "status_code": t["status_code"], "model": t["model"],
               "system_fingerprint": t["system_fingerprint"], "usage": t["usage"],
               "response_sha256": t["response_body_sha256"], "transport_ns": end-start}
        if reply.exists():
            expected_wire.add(reply.name)
            require(reservation is not None and str(reply.relative_to(scene)) in seals and
                    file_sha(reply) == t["response_body_sha256"], "Response not bound to its reservation")
        else:
            require(t["response_body_sha256"] is None, "Unarchived provider response")
        if reservation is not None and reservation["status"] == "settled":
            content = reply.read_bytes()
            payload = json.loads(content)
            require(responses.get(attempt) == content and t["sdk_received"] and 200 <= t["status_code"] < 300
                    and t["model"] == payload["model"] and t["system_fingerprint"] == payload.get("system_fingerprint")
                    and t["usage"] == payload["usage"] and reservation["response_body_sha256"] == file_sha(reply)
                    and all(t["usage"][k] == n for k, n in reservation["usage"].items()),
                    "Provider response, SDK archive and settled usage differ")
            msg = dict(payload["choices"][0]["message"])
            msg.setdefault("reasoning_content", None)
            if sense_interface == "C":
                from .teacher_sense import execution_history
                msg = execution_history(msg)
            prior.append(msg)
            row["reasoning_present"] = bool(msg["reasoning_content"])
        else:
            require(not t["sdk_received"] and (
                reservation is None or reservation["status"] == "locked"), "Request in an unknown accounting state")
        turns.append(row)
    require(next(reserved_ids, None) is None and {p.name for p in wire.iterdir()} == expected_wire and
            {p.name for p in spans.iterdir()} == expected_spans, "Unaccounted provider attempt or wire artifact")
    return turns


def audit_scene(scene, spec, config, limits, ledger):
    """Read only sealed scene outputs. The resulting checkpoint stays valid as the next scene runs."""
    sid = spec["session_id"]
    original = original_scene(scene, sid)
    worker, scorer = (process_evidence(scene, role) for role in ("worker", "scorer"))
    require(worker["pid"] != scorer["pid"] and worker["finished_ns"] <= scorer["started_ns"],
            "Scorer was not isolated from the execution")
    inputs, session, scores = (read(scene/n) for n in ("run-input.json", "session.json", "session_scores.json"))
    require(inputs["scope"] == "deepseek_teacher_continuous_service" and inputs["session_id"] == sid ==
            session["session_id"] == scores["session_id"] and session["teacher_mode"] == config["transport"]
            and session["runtime_seed"] == spec["runtime_seed"] and all(inputs["teacher"].get(k) == v for k,v in config.items())
            and session["worker_pid"] == scores["worker_pid"] == worker["pid"] and scores["scorer_pid"] == scorer["pid"],
            "Scene, worker, scorer or prepared configuration changed")
    result, previous, score_rows = [], None, []
    task_stop = False
    for task, row, spec_task in zip(original["tasks"], session["tasks"], spec["tasks"], strict=True):
        tid = task["id"]
        require(tid == row["task_id"] == spec_task["task_id"] and row["index"] == spec_task["index"]
                and hashlib.sha256(task["content"].encode()).hexdigest() == spec_task["command_sha256"],
                "Incorrect scene task order or command")
        summary = {k:row[k] for k in ("task_id", "index", "status", "reason")}
        folder = owned(scene, row["directory"])
        require(folder == scene/"tasks"/f"{row['index']:02}-{tid}", "Task archived in an unexpected directory")
        result.append(summary)
        if row["status"] == "unstarted":
            require(tid not in ledger["trials"] and not folder.exists(), "Unstarted task has a dispatch or artifacts")
            task_stop = True
            continue
        require(not task_stop and row["status"] == "completed" and tid in ledger["trials"], "Missing task disposition")
        before, boundary = (read(folder/n) for n in ("before.json", "boundary.json"))
        require(boundary["worker_pid"] == worker["pid"] and boundary["task_id"] == tid
                and row["reason"] == boundary["reason"], "Mismatched task evidence")
        for item in (before["prepare_before"], before["policy_start"], boundary["terminal"], boundary["continuation"]):
            check_projection(item)
        check_prepare(before["prepare_before"], before["policy_start"])
        if previous is None:
            check_initial(original, session, before["prepare_before"])
        else:
            require(previous == before["prepare_before"], "Unproven world or blackboard handoff")
        previous = boundary["continuation"]
        initial, final = _scores(folder, task, row, boundary, worker, scorer)
        score_rows.append({"task_id":tid, "passed":final})
        summary.update(initial_passed=initial, terminal_passed=final,
                       committed_actions=check_actions(read(folder/"actions.json")),
                       turns=task_requests(scene, folder, task, boundary, worker["files"], limits, ledger,
                                           config.get("sense_interface", "B")))
        transport = boundary["teacher"]
        dispatched = sum(t["dispatched"] for t in summary["turns"])
        require(transport["attempts"] == dispatched and transport["sdk_attempts"] == len(summary["turns"])
                and transport["external_provider_calls"] == (dispatched if config["transport"] == "deepseek" else 0)
                and transport["actual_fee"] == (None if config["transport"] == "deepseek" else "0")
                and Decimal(transport["settled_conservative_charge"]) ==
                    Decimal(ledger["trials"][tid]["settled_conservative_charge"]),
                "Worker dispositions and booked amounts differ from the ledger")
        task_stop = bool(transport["batch_stopped"])
    require(score_rows == scores["tasks"], "Independent score summary differs from per-task files")
    attempts = [t for r in result for t in r.get("turns", []) if t["dispatched"]]
    settled = sum((Decimal(ledger["attempts"][t["reservation_id"]].get("settled_conservative_charge", "0")) for t in attempts), Decimal(0))
    return {"session_id":sid, "transport":config["transport"], "worker_seal_sha256":file_sha(scene/"worker-exit.json"),
            "scorer_seal_sha256":file_sha(scene/"scorer-exit.json"), "tasks":result,
            "finished_without_batch_stop":not task_stop, "reserved_attempts":len(attempts),
            "settled_conservative_charge":str(settled), "scored":len(score_rows),
            "task_dispositions":dict(Counter(r["reason"] for r in result))}
