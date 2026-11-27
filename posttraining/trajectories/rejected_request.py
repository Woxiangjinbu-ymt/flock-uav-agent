"""Restore a rejected pre-dispatch request using the collection environment's message ABI."""
import base64
import json
from pathlib import Path
import sys

from langchain_core.messages import messages_from_dict

from flockcue.evidence.execution_state import build_task_execution_state, concise_recovery_projection
from flockcue.evidence.middleware import digest, message_value
from flockcue.service import DeploymentDeepSeek


def restore(archive, chain):
    evidence = chain["task_progress"]["task_evidence"]
    annotations = {r["tool_call_id"]: r["text"] for r in evidence["injections"]}
    previous = raw = rejection = None
    with archive.open() as stream:
        for event in map(json.loads, stream):
            data = event["data"]
            if event["kind"] == "model_start":
                raw = data["input"]
            elif event["kind"] == "http_request":
                previous = json.loads(base64.b64decode(data["body"]["base64"]))
            elif event["kind"] == "student_context" and data["window"].get("error"):
                rejection = data
                break
    if rejection is None or previous is None:
        raise ValueError("Expected a pre-dispatch rejection after a recorded request")
    messages = messages_from_dict([{"type": m["type"], "data": m}
                                   for m in (raw["system_message"], *raw["messages"])])
    harness = chain["harness"]
    cut = max(i for i, e in enumerate(harness["events"]) if e["kind"] == "model_request_start")
    state = build_task_execution_state(chain["command"], chain["command_id"], chain["task_progress"],
                                       {**harness, "events": harness["events"][:cut]})
    marker = "\n\n[Execution recovery contract: advisory public evidence, not task completion]\n"
    old_system = previous["messages"][0]["content"]
    system = old_system.split(marker)[0] + marker + json.dumps(concise_recovery_projection(state), ensure_ascii=False)
    hashes = evidence["model_requests"][-1]["message_sha256"]
    for i, (message, expected) in enumerate(zip(messages, hashes, strict=True)):
        if digest(message_value(message)) == expected:
            continue
        candidates = (old_system, system) if i == 0 else (
            message.content + annotations.get(getattr(message, "tool_call_id", None), ""),)
        for content in candidates:
            candidate = message.model_copy(update={"content": content})
            if digest(message_value(candidate)) == expected:
                messages[i] = candidate
                break
        else:
            raise ValueError(f"Unverified model-visible message {i}")
    # Serialize through the pinned provider adapter. No inference request is made.
    client = DeploymentDeepSeek(model="deepseek-flash", api_key="offline")
    return {"messages": client._get_request_payload(messages)["messages"], "tools": previous["tools"],
            "window": rejection["window"], "logical_call_id": rejection["logical_call_id"]}


if __name__ == "__main__":
    archive, chain = map(Path, sys.argv[1:])
    print(json.dumps(restore(archive, json.loads(chain.read_bytes())), ensure_ascii=False))
