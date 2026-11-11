"""Review sealed teacher-pilot scenes offline and export paired, target-only Qwen samples."""
import argparse
from collections import Counter
import json
from pathlib import Path

from convert_agent_samples import _calls, canonical, decode, parse, sha
from flockcue_qwen35.adapter import load_tokenizer
from flockcue_qwen35.dev_sample_tokens import supervise
from flockcue_qwen35.dev_samples import PRIVATE_MARKERS, jsonl
from flockcue_session_cpu.teacher_sense import execution_history
from flockcue_session_cpu.teacher_verify import _history, _injections
from flockcue_session_cpu.verify import check_actions, file_sha, original_scene, owned, process_evidence, read, require
from student_input import InputRejected
from thinking_events.campaign_storage import atomic_write, json_bytes
from thinking_events.events import extract_events

PILOT_SHA = "6d7af90aa614d78c79b45fdeb35f88992b436a07bd46d21a956c32d3ca2fbc1e"
SCENES = ("04cd6269", "ef36e27a")
SCORER_MARKERS = (*PRIVATE_MARKERS, b"is_passed", b"eligibility.json", b"boundary.json", b"initial-score.json")


def json_or_text(value):
    try:
        return parse(value)
    except (TypeError, ValueError):
        return value


def archive_index(records):
    sends = [r for r in records if r["kind"] == "http_request"]
    starts, ends, replies, model_ends = {}, {}, {}, {}
    for record in records:
        d = record["data"]
        if record["kind"] == "tool_start":
            call_id = d["raw_tool_call_id"]
            require(call_id not in starts, "duplicate tool call ID")
            starts[call_id] = record
        elif record["kind"] == "tool_end":
            span = d["tool_span_id"]
            require(span not in ends, "duplicate tool end")
            ends[span] = record
        elif record["kind"] == "model_end":
            model_ends[d["logical_call_id"]] = record
        elif record["kind"] == "http_response_chunk":
            replies.setdefault(d["attempt_id"], bytearray()).extend(decode(d["body"]))
    return sends, starts, ends, replies, model_ends


def receipt(call, logical, starts, ends, lower_bound):
    start = starts.get(call["id"])
    if not start:
        return None
    d = start["data"]
    end = ends.get(d["tool_span_id"])
    if not end:
        return None
    out = end["data"].get("output") or {}
    if not (d["source_relation"] == "unique" and d["source_model_id"] == logical
            and d["source_candidates"] == [logical] and d["input"]["id"] == call["id"]
            and d["input"]["name"] == call["name"] and d["input"]["args"] == call["args"]
            and lower_bound < start["sequence"] < end["sequence"]
            and end["data"]["status"] == "returned" and not end["data"]["error_type"]
            and end["data"]["tool_call_id_mismatch"] is False
            and out.get("tool_call_id") == call["id"] and out.get("type") == "tool"
            and out.get("name") == call["name"] and out.get("status") in {"success", "error"}
            and isinstance(out.get("content"), str)):
        return None
    return {"call_id": call["id"], "tool": call["name"], "arguments": call["args"],
            "sequence": end["sequence"], "result": json_or_text(out["content"]), "raw": out["content"],
            "receipt_status": out["status"]}


def event_prefix(command, messages, number, known, sequence):
    batches, gaps = [], []
    for index, msg in enumerate(messages):
        if msg["role"] == "assistant":
            batches.append([])
        if msg["role"] == "tool":
            cid = msg["tool_call_id"]
            item = known.get(cid)
            if not batches or not item or item["sequence"] >= sequence:
                gaps.append("unattributed_visible_receipt:" + cid)
            else:
                batches[-1].append({**item, "wire_message_index": index})
    return extract_events({"task": command, "turn": number, "batches": batches, "gaps": gaps})


def screen(task):
    if task["status"] != "completed":
        return task["status"]
    if task["initial_passed"]:
        return "initial_already_passed"
    if not task["terminal_passed"]:
        return "terminal_failed"
    if not task["committed_actions"]:
        return "no_committed_action"
    return "semantic_review"


def load_choices(path):
    if path is None:
        return {}
    rows = [parse(line) for line in path.read_bytes().splitlines()]
    indexed = {r["id"]: r for r in rows}
    require(len(indexed) == len(rows) and all(isinstance(r.get("keep"), bool) and
            isinstance(r.get("reason"), str) and r["reason"].strip() for r in rows), "invalid reviewer choices")
    return indexed


def pair(sample, tokenizer):
    plain = {**sample, "target": {k: v for k, v in sample["target"].items() if k != "reasoning_content"}}
    versions = (sample, plain)
    audits = []
    for current in versions:
        _, _, audit = supervise(tokenizer, current["messages"], current["tools"], current["target"])
        audits.append(audit)
    require(audits[0]["prompt_ids_sha256"] == audits[1]["prompt_ids_sha256"]
            and audits[0]["roundtrip_tool_names"] == audits[1]["roundtrip_tool_names"]
            and audits[1]["supervised_reasoning_tokens"] == 0, "unequal paired supervision")
    audits[1]["policy"] = "remove_current_teacher_reasoning"
    return versions, dict(zip(("keep_reasoning", "remove_reasoning"), audits, strict=True))


def inspect_episode(scene, task, original, fold_id, seals):
    folder = scene / "tasks" / f"{task['index']:02}-{task['task_id']}"
    bound = read(folder / "boundary.json")
    archive = owned(scene, bound["archive"])
    digest = seals[bound["archive"]]
    records = [parse(l) for l in archive.read_bytes().splitlines()]
    injections = _injections(scene, original, records[0]["command_id"], seals)
    sends, starts, ends, replies, model_ends = archive_index(records)
    raw_receipts = {r["data"]["output"]["tool_call_id"]: r["data"]["output"]["content"] for r in ends.values()
                    if isinstance(r["data"].get("output"), dict) and r["data"]["output"].get("tool_call_id")}
    controls = Counter()
    for end in ends.values():
        output = end["data"].get("output") or {}
        if output.get("name") == "run_harness":
            result = json_or_text(output["content"])
            if isinstance(result, dict) and result.get("plan_id"):
                controls[result["plan_id"]] += 1
    actions = read(folder / "actions.json")
    previous, known = [], {}
    for logged, sent in zip(task["turns"], sends, strict=True):
        number = logged["number"]
        sample_id = f"{fold_id}/turn-{number:02}"
        d = sent["data"]
        prefix = f"turn-{number:02}"
        req_path = folder / "provider-wire" / (prefix + ".request.json")
        body = req_path.read_bytes()
        require(sha(body) == logged["request_sha256"] == seals[str(req_path.relative_to(scene))]
                and body == decode(d["body"]), "request was not authenticated")
        wire = parse(body)
        msgs = wire["messages"]
        _history(msgs, original["content"], previous, raw_receipts, injections)
        events = event_prefix(original["content"], msgs, number, known, sent["sequence"])
        row = {"id": sample_id, "number": number, "archive": bound["archive"], "archive_sha256": digest,
               "sequence": sent["sequence"], "logical_call_id": d["logical_call_id"], "attempt_id": d["attempt_id"],
               "request_sha256": logged["request_sha256"], "response_sha256": logged["response_sha256"],
               "events": events, "model": logged["model"], "dispatched": logged["dispatched"],
               "sdk_received": logged["sdk_received"], "calls": []}
        if not logged["sdk_received"] or not logged.get("response_sha256") or not 200 <= logged["status_code"] < 300:
            yield row, None, "incomplete_service_response"
            continue
        reply_path = folder / "provider-wire" / (prefix + ".response.json")
        raw = reply_path.read_bytes()
        require(sha(raw) == logged["response_sha256"] == seals[str(reply_path.relative_to(scene))]
                and raw == replies[d["attempt_id"]], "response was not authenticated")
        response = parse(raw)
        choice, = response["choices"]
        message = choice["message"]
        executed = execution_history(message)
        previous.append({**executed, "reasoning_content": message.get("reasoning_content")})
        calls = _calls(message)
        executable_calls = _calls(executed)
        reason = None
        require(response["model"] == logged["model"] and message["role"] == "assistant", "wrong teacher identity")
        if choice["finish_reason"] != ("tool_calls" if calls else "stop") or message.get("refusal"):
            reason = "incomplete_teacher_reply"
        model_end = model_ends.get(d["logical_call_id"])
        if not model_end or model_end["data"].get("status") != "returned":
            reason = "teacher_decision_unreturned"
        if any(m["tool_call_id"] not in known or known[m["tool_call_id"]]["sequence"] >= sent["sequence"]
               for m in msgs if m["role"] == "tool"):
            reason = "unattributed_visible_receipt"
        row["finish_reason"] = choice["finish_reason"]
        row["public_answer"] = message.get("content") or ""
        for call, executable in zip(calls, executable_calls, strict=True):
            cid = call["id"]
            r = receipt(executable, d["logical_call_id"], starts, ends, model_end["sequence"]) if model_end else None
            if not r:
                reason = "missing_or_unattributed_receipt"
            else:
                require(cid not in known, "reused tool call identifier")
                known[cid] = r
            origin = {"kind": "model_tool", "tool": call["name"], "tool_call_id": cid}
            requests = [q for q in actions["requests"] if q["origin"] == origin]
            physical = [q for q in actions["events"] if q["phase"] == "task" and q["origin"] == origin]
            skill = {}
            if call["name"] == "run_harness" and r and isinstance(r["result"], dict):
                plan = r["result"].get("plan_id")
                if plan and controls[plan] == 1:
                    def belongs(item):
                        return item["origin"].get("kind") == "skill" and item["origin"].get("plan_id") == plan
                    child_requests = [q for q in actions["requests"] if belongs(q)]
                    if len(child_requests) == r["result"].get("api_calls"):
                        requests = requests + child_requests
                        physical = physical + [q for q in actions["events"] if q["phase"] == "task" and belongs(q)]
                        skill = {"subroutine_plan_id": plan}
            require(not any(q["admin"] for q in requests), "private API in the policy trace")
            row["calls"].append({**call, **skill, "execution_args": executable["args"], "receipt_status": r["receipt_status"] if r else None,
                                 "receipt_sequence": r["sequence"] if r else None,
                                 "receipt_sha256": sha(canonical(r["raw"])) if r else None,
                                 "requests": [q["request_id"] for q in requests],
                                 "committed_actors": [q["actor"] for q in physical if q["committed"]]})
        target = {"role": "assistant", "content": row["public_answer"]}
        if isinstance(message.get("reasoning_content"), str):
            target["reasoning_content"] = message["reasoning_content"]
        if calls:
            target["tool_calls"] = [{"type": "function", "function": {"name": c["name"], "arguments": c["args"]}}
                                    for c in calls]
        sample = {"sample_id": sample_id, "messages": msgs, "tools": wire["tools"], "target": target}
        public = canonical(sample)
        if any(k in public for k in SCORER_MARKERS):
            reason = "private_marker"
        allowed = {t["function"]["name"] for t in wire["tools"]}
        if any(c["name"] not in allowed for c in calls):
            reason = "unavailable_tool"
        yield row, sample, reason


def analyze(base, output, sid, choices_path=None):
    base, output = base.resolve(), output.resolve()
    require(not output.is_relative_to(base), "review outputs must be outside the run")
    config = read(base / "batch-input.json")
    require(config["transport"] == "deepseek" and config["synthetic"] is False and config["sense_interface"] == "C"
            and file_sha(base / "pilot40.json") == config["pilot_sha256"] == PILOT_SHA
            and file_sha(base / "batch-input.json") == (base / "batch-input.sha256").read_text().strip(),
            "expected authentic frozen DeepSeek pilot C")
    scene = base / "sessions" / sid
    cp = read(base / "checkpoints" / (sid + ".json"))
    require(cp["session_id"] == sid and cp["transport"] == "deepseek", "wrong checkpoint")
    worker, scorer = (process_evidence(scene, name) for name in ("worker", "scorer"))
    require(worker["pid"] != scorer["pid"] and worker["finished_ns"] <= scorer["started_ns"] and
            all(file_sha(scene / (kind + "-exit.json")) == cp[kind + "_seal_sha256"] for kind in ("worker", "scorer")),
            "independent scorer or seal mismatch")
    scene_original = original_scene(scene, sid)
    session = read(scene / "session.json")
    require(session["session_id"] == sid and session["teacher_mode"] == "deepseek" and
            "session.json" in worker["files"], "unexpected service or session")
    choices = load_choices(choices_path)
    seen_choices, episodes, turns = set(), [], []
    versions = ([], [])
    tokenizer = None
    for task, original, entry in zip(cp["tasks"], scene_original["tasks"], session["tasks"], strict=True):
        require(task["task_id"] == original["id"] and all(task[k] == entry[k] for k in
                ("task_id", "index", "status", "reason")), "task order or disposition mismatch")
        eid = f"{sid}/{task['task_id']}"
        ep = {k: v for k, v in task.items() if k != "turns"}
        why = screen(task)
        ep.update(id=eid, command=original["content"], screening=why, attributed=False, kept_turns=0)
        episodes.append(ep)
        if task["status"] != "completed":
            continue
        folder = scene / "tasks" / f"{task['index']:02}-{task['task_id']}"
        require(check_actions(read(folder / "actions.json")) == task["committed_actions"] and
                str((folder / "actions.json").relative_to(scene)) in worker["files"], "unverified actions")
        for fn, k in (("score.json", "terminal_passed"), ("initial-score.json", "initial_passed")):
            require(read(folder / fn)["checker"]["passed"] is task[k] and str((folder / fn).relative_to(scene))
                    in scorer["files"], "score/checkpoint mismatch")
        if why == "semantic_review" and choices_path:
            choice = choices[eid]
            require(choice["worker_sha256"] == cp["worker_seal_sha256"], "reviewed another episode")
            seen_choices.add(eid)
            ep.update(attributed=choice["keep"], reduction_reason=choice["reason"])
        for row, sample, problem in inspect_episode(scene, task, original, eid, worker["files"]):
            turns.append(row)
            row.update(kept=False, reason=problem or why)
            if why != "semantic_review" or choices_path is None:
                continue
            if not ep["attributed"]:
                row["reason"] = problem or ep["reduction_reason"]
                continue
            if problem:
                continue
            choice = choices[row["id"]]
            seen_choices.add(row["id"])
            require(choice["request_sha256"] == row["request_sha256"], "reviewed another decision")
            row["reason"] = choice["reason"]
            if not choice["keep"]:
                continue
            if tokenizer is None:
                tokenizer = load_tokenizer()
            try:
                examples, audit = pair(sample, tokenizer)
            except InputRejected as error:
                row.update(reason=error.code, rejected_detail=str(error))
                continue
            row.update(kept=True, tokenization=audit)
            ep["kept_turns"] += 1
            for dest, example in zip(versions, examples, strict=True):
                dest.append(example)
    require(seen_choices == set(choices), "unused review choice")
    require(len(episodes) == 20, "missing planned slots")
    final = choices_path is not None
    out = output / sid / ("final" if final else "screening")
    out.mkdir(parents=True, exist_ok=True)
    atomic_write(out / "episodes.jsonl", jsonl(episodes))
    atomic_write(out / "decisions.jsonl", jsonl(turns))
    if final:
        for name, values in zip(("keep-reasoning", "remove-reasoning"), versions, strict=True):
            atomic_write(out / (name + ".jsonl"), jsonl(values))
    def events(rows):
        return {"states": dict(Counter(r["events"]["event_state"] for r in rows)),
                "event_instances": dict(Counter(e["type"] for r in rows for e in r["events"]["events"]))}
    summary = {"scene": sid, "fold": "A", "phase": "final" if final else "screening", "slots": len(episodes),
               "scored": sum("terminal_passed" in ep for ep in episodes),
               "independent_passed": sum(ep.get("terminal_passed", False) for ep in episodes),
               "screen": dict(Counter(ep["screening"] for ep in episodes)), "requests": len(turns),
               "all_request_events": events(turns),
               "received_reply_events": events([r for r in turns if r["sdk_received"]]), "worker_sha256": cp["worker_seal_sha256"],
               "scorer_sha256": cp["scorer_seal_sha256"]}
    if final:
        retained = [r for r in turns if r["kept"]]
        summary.update(attributed_tasks=sum(ep["attributed"] for ep in episodes),
                       tasks_with_samples=sum(bool(ep["kept_turns"]) for ep in episodes),
                       retained_decisions=len(retained), retained_events=events(retained),
                       review_sha256=file_sha(choices_path))
    summary["artifacts"] = {p.name: file_sha(p) for p in sorted(out.glob("*.jsonl"))}
    atomic_write(out / "summary.json", json_bytes(summary))
    return summary


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--run", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--scene", choices=SCENES, required=True)
    cli.add_argument("--review", type=Path, help="per-scene signed-off semantic selection JSONL")
    args = cli.parse_args()
    print(json.dumps(analyze(args.run, args.output, args.scene, args.review), ensure_ascii=False))


if __name__ == "__main__":
    main()
