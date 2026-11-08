"""Re-render the actual archived SDK requests in an independent tokenizer process."""

import base64
import hashlib
import json
from pathlib import Path
import sys

import student_input_v2 as v2
from .student import render
from .worker_child import _frozen_modules


def verify_inputs(run):
    root = run.resolve() / "source"
    modules = _frozen_modules(root)
    for name in ("student_input", "student_input_v2", "thinking_events.student"):
        if name not in modules:
            raise ValueError(f"Tokenizer verifier imported outside the frozen source: {name}")
    tokenizer = v2.load_tokenizer(v2.base.DEFAULT_TOKENIZER)
    summary = {}
    for trial in sorted((run / "trials").iterdir()):
        worker = json.loads((trial / "worker-result.json").read_bytes())
        if not (
            worker["drained"] and all(worker["isolation"].values()) and worker["network_attempts"] == []
        ):
            raise ValueError("worker boundary failed")
        archive = Path(worker["agent"]["archive_info"]["path"])
        requests = [
            row["data"]
            for row in map(json.loads, archive.read_bytes().splitlines())
            if row["kind"] == "http_request"
        ]
        rows = list(map(json.loads, (trial / "private/student-requests.jsonl").read_bytes().splitlines()))
        reversed_pairs = []
        replayed_history = False
        for wire, row in zip(requests, rows, strict=True):
            body = base64.b64decode(wire["body"]["base64"], validate=True)
            if hashlib.sha256(body).hexdigest() != row["request_body_sha256"]:
                raise ValueError("SDK request and student audit disagree")
            request = json.loads(body)
            messages = request["messages"]
            prompt, _ = render(tokenizer, messages, request["tools"], trial.name == "cpu-success")
            if prompt["input_ids"] != row["input_ids"]:
                raise ValueError("student input IDs differ from archived request reconstruction")
            if row["turn"] == 2 and row["history_reasoning_tokens"] > 0:
                replayed_history = any(
                    m.get("reasoning_content") == "Read fleet identities." for m in messages
                )
            if row["turn"] == 3:
                calls = messages[-3].get("tool_calls", [])
                if len(calls) == 2 and len({c["function"]["name"] for c in calls}) == 1:
                    expected = [c["id"] for c in reversed(calls)]
                    actual = [m.get("tool_call_id") for m in messages[-2:]]
                    if expected != actual or len(set(actual)) != 2:
                        raise ValueError("parallel receipts not reversed by unique ID")
                    # Removing changed order or swapping contents while keeping IDs
                    # would still look plausible to a position-based consumer.
                    reply_actors = [
                        json.JSONDecoder().raw_decode(m["content"])[0]["id"] for m in messages[-2:]
                    ]
                    arguments = [
                        json.loads(c["function"]["arguments"])["input_json"] for c in reversed(calls)
                    ]
                    if reply_actors != [a["drone_id"] for a in arguments]:
                        raise ValueError("tool receipt linked to wrong aircraft")
                    reversed_pairs = actual
        limits = worker["limits"]
        summary[trial.name] = {
            "verifier_modules": modules,
            "reproduced_token_sequences": len(rows),
            "reversed_receipt_ids": reversed_pairs,
            "second_turn_replayed_reasoning": replayed_history,
            "actions": len(worker["actions"]),
            "late_action_rejected": worker["isolation"]["late_action_rejected"],
            "stop_reason": limits["stop_reason"],
            "archive_intact": worker["agent"]["archive_verify"]["valid"],
        }
    success = summary["cpu-success"]
    if (
        not success["reversed_receipt_ids"]
        or not success["second_turn_replayed_reasoning"]
        or success["actions"] != 2
        or success["stop_reason"] is not None
    ):
        raise ValueError("positive student fixture evidence missing")
    if summary["cpu-malformed"]["stop_reason"] != "generated_json" or summary["cpu-malformed"]["actions"]:
        raise ValueError("malformed call group was not rejected atomically")
    if summary["cpu-cancel"]["stop_reason"] != "cancelled" or summary["cpu-cancel"]["actions"]:
        raise ValueError("cancelled slot dispatched an action")
    return summary


if __name__ == "__main__":
    print(json.dumps(verify_inputs(Path(sys.argv[1]))))
