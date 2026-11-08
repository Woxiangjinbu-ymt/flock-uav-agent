"""Reconstruct one previously seen DeepSeek decision in memory for CPU format checks.

Run under Python 3.11 from any directory. Stdout contains provenance and counts only.
The independent episode score is an audit-side selection criterion.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from convert_agent_samples import _calls, _episode_gate, canonical, decode, parse, read, require, sha
from thinking_events.archive import read_turns, verify_sources
from thinking_events.events import extract_events


def load_example():
    """Return (wire history, advertised tools, current target, out-of-band evidence)."""
    sources, inventory = verify_sources(ROOT)
    source, = (s for s in sources if s["batch"] == "deepseek-hover-pilot-20261103"
               and s["episode"] == "explicit_hover-candidate")
    turns, _ = read_turns(source)
    turn, = (t for t in turns if t["turn"] == 2)
    prefix = turn["prefix"]
    require(not prefix["history_gaps"] and not prefix["gaps"], "incomplete_public_history")
    receipts = [r for batch in prefix["batches"] for r in batch]
    rows = [parse(line) for line in source["path"].read_bytes().splitlines()]
    request_seq = turn["request_sequence"]
    req = rows[request_seq - 1]
    require(req["kind"] == "http_request" and req["data"]["attempt_id"] == turn["attempt_id"]
            and req["data"]["logical_call_id"] == turn["logical_call_id"], "request_identity")
    body = decode(req["data"]["body"])
    request = parse(body)
    messages, tools = request["messages"], request["tools"]
    require(receipts and all(r["sequence"] < request_seq and
            messages[r["wire_message_index"]]["tool_call_id"] == r["call_id"] for r in receipts),
            "receipt_outside_public_prefix")
    require(sum(m["role"] == "tool" for m in messages) == len(receipts), "unverified_tool_message")
    public = canonical({"messages": messages, "tools": tools})
    forbidden = (b"/check/", b"execution_check_apis", b"related_apis", b"mark-done",
                 b"world.pkl", b"checker_sha256", b"score-world.pkl")
    require(not any(token in public for token in forbidden), "private_marker_in_wire_input")
    # read_turns already validated chunk order, SHA-256 and model-end agreement.
    reply_rows = [r for r in rows if r["kind"] == "http_response_chunk"
                  and r["data"]["attempt_id"] == turn["attempt_id"]]
    require(reply_rows and all(r["sequence"] > request_seq and
            r["data"]["logical_call_id"] == turn["logical_call_id"] for r in reply_rows), "reply_identity")
    raw_reply = b"".join(decode(r["data"]["body"]) for r in reply_rows)
    response = parse(raw_reply)
    message = response["choices"][0]["message"]
    calls = _calls(message)
    available = {tool["function"]["name"] for tool in tools}
    require(message["role"] == "assistant" and message["reasoning_content"].strip()
            and calls and all(call["name"] in available for call in calls), "unsupported_current_target")
    require(all(call["id"].encode() not in public for call in calls), "current_call_id_in_input")
    target = {
        "role": "assistant", "content": message.get("content") or "",
        "reasoning_content": message["reasoning_content"],
        "tool_calls": [{"type": "function", "function": {"name": c["name"], "arguments": c["args"]}}
                       for c in calls],
    }

    run = ROOT / "docs/results" / source["batch"] / "run-01"
    campaign = read(run, "campaign-seal.json")["files"]
    trial_id = source["episode"]
    score_rel = f"trials/{trial_id}/evaluation/result.json"
    for name in ("results.json", score_rel, f"scorer-exit-seals/{trial_id}.json"):
        require(sha((run / name).read_bytes()) == campaign[name], "changed_episode_evidence")
    trial, = (t for t in read(run, "manifest.json")["trials"] if t["trial_id"] == trial_id)
    result, = (r for r in read(run, "results.json") if r["trial_id"] == trial_id)
    score = read(run, score_rel)
    worker = read(run, f"trials/{trial_id}/worker-result.json")
    _episode_gate(run, trial, result, worker, score)
    event = extract_events(prefix)
    evidence = {
        "scope": "Previously seen real development decision; CPU formatting fixture only",
        "eligible_for_training": False, "teacher_calls_this_audit": 0,
        "authenticated_development_episodes": len(sources),
        "campaign_set_sha256": inventory["campaign_seals_sha256"], "model": response["model"],
        "archive_path": source["archive_path"], "archive_sha256": source["archive_sha256"],
        "episode": trial_id, "task_id": source["task_id"], "turn": turn["turn"],
        "logical_call_id": turn["logical_call_id"], "attempt_id": turn["attempt_id"],
        "request_sequence": request_seq, "request_body_sha256": sha(body),
        "response_body_sha256": sha(raw_reply), "response_chunk_sequences": [r["sequence"] for r in reply_rows],
        "input": {"roles": [m["role"] for m in messages], "public_receipt_sequences": [r["sequence"] for r in receipts],
                  "public_receipt_tools": [r["tool"] for r in receipts],
                  "tools_count": len(tools), "public_projection_sha256": sha(public)},
        "target": {"tool_names": [c["name"] for c in calls], "reasoning_tokens_reported_by_teacher": turn["reasoning_tokens"],
                   "reasoning_utf8_bytes": len(target["reasoning_content"].encode()), "target_sha256": sha(canonical(target))},
        "supervision": {"level": "message", "mask": [0] * len(messages) + [1],
                        "current_assistant_fields": ["reasoning_content", "content", "tool_calls"],
                        "supervise_provider_call_ids": False},
        "events_before_reply": sorted({e["type"] for e in event["events"]}),
        "episode_score_audit_only": {"path": (run / score_rel).relative_to(ROOT).as_posix(),
                                     "sha256": campaign[score_rel], "original_checker_passed": score["checker"]["passed"],
                                     "checks_passed": score["checker"]["checks_passed"],
                                     "checks_expected": score["checker"]["checks_expected"]},
    }
    return messages, tools, target, evidence


if __name__ == "__main__":
    print(json.dumps(load_example()[3], indent=2, ensure_ascii=False))
