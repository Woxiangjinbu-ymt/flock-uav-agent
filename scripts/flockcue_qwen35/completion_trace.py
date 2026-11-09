"""Link public assistant replies to ordered simulator actions and public tool receipts."""
import base64
import json
from pathlib import Path

from .verify import read, require, rows


def completion_trace(run, slot):
    """Return public text for semantic review, plus the hover evidence available at each reply."""
    folder = Path(run) / "trials" / slot
    archive, = (folder / "policy/modelio").glob("*.jsonl")
    events = rows(archive)
    world = rows(folder / "policy/worldlogs/public-api.jsonl")
    worker = read(folder / "worker-result.json")
    attempts = worker["limits"]["attempts"]
    physical = {a["request_id"]: a for a in worker["actions"]}
    starts = {e["data"]["raw_tool_call_id"]: e for e in events if e["kind"] == "tool_start"}
    ends = {e["data"]["tool_span_id"]: e for e in events if e["kind"] == "tool_end"}
    actions = []
    for api in world:
        if api["phase"] != "task" or not api["action"]:
            continue
        action = physical[api["request_id"]]
        origin = api["origin"]
        call_id = origin.get("tool_call_id")
        if origin["kind"] != "model_tool":
            continue
        start = starts[call_id]
        span_id = start["data"]["tool_span_id"]
        end = ends[span_id]
        require(start["sequence"] < end["sequence"] and start["data"]["input"]["name"] == origin["tool"]
                and api["endpoint"] == action["endpoint"] and action["committed"] == api["committed"],
                "flight action or receipt span mismatch")
        output = end["data"].get("output") or {}
        text = output.get("content") or ""
        try:
            result, _ = json.JSONDecoder().raw_decode(text)
        except ValueError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        receipt_ok = (end["data"]["status"] == "returned" and not end["data"]["tool_call_id_mismatch"]
                      and output.get("tool_call_id") == call_id and result.get("status") == "success")
        actions.append({"tool": origin["tool"], "call_id": call_id, "span_id": span_id,
                        "model_id": start["data"]["source_model_id"], "request_id": api["request_id"],
                        "endpoint": action["endpoint"], "committed": action["committed"], "status": action["status"],
                        "start_sequence": start["sequence"], "receipt_sequence": end["sequence"],
                        "simulator_start_seconds": api["relative_seconds"], "simulator_end_seconds": api["ended_seconds"],
                        "receipt_success": receipt_ok, "receipt_drone": result.get("drone_id"),
                        "receipt_command": result.get("command"), "receipt_command_id": result.get("command_id")})

    target = "3d229034"
    hovers = [a for a in actions if a["tool"] == "hover" and a["endpoint"] == f"/drones/{target}/command/hover"
              and a["committed"] and a["status"] == "success"]
    requests = [e for e in events if e["kind"] == "http_request"]
    turns = []
    for turn, (request, attempt) in enumerate(zip(requests, attempts, strict=True), 1):
        q = request["data"]
        sdk_input = json.loads(base64.b64decode(q["body"]["base64"], validate=True))
        seen = {m["tool_call_id"] for m in sdk_input["messages"] if m["role"] == "tool"}
        prior = [a for a in hovers if a["receipt_sequence"] < request["sequence"]]
        reply = {"turn": turn, "sdk_request_sequence": request["sequence"],
                 "sdk_response_end_sequence": None, "public_content": None, "called_tools": [],
                 "target_hover_committed_before_reply": bool(prior),
                 "successful_target_hover_receipts_in_prompt": [a["call_id"] for a in prior if a["call_id"] in seen
                     and a["receipt_success"] and a["receipt_drone"] == target and a["receipt_command"] == "hover"]}
        if attempt["delivered_to_sdk"]:
            chunks = [e for e in events if e["kind"] == "http_response_chunk"
                      and e["data"]["attempt_id"] == q["attempt_id"]]
            finish, = (e for e in events if e["kind"] == "http_response_end"
                       and e["data"]["attempt_id"] == q["attempt_id"])
            raw = b"".join(base64.b64decode(e["data"]["body"]["base64"], validate=True) for e in chunks)
            msg = json.loads(raw)["choices"][0]["message"]
            reply.update(public_content=msg.get("content") or "", sdk_response_end_sequence=finish["sequence"],
                         called_tools=[{"call_id": c["id"], "name": c["function"]["name"]}
                                       for c in msg.get("tool_calls", [])])
            require(all(finish["sequence"] < starts[c["call_id"]]["sequence"] for c in reply["called_tools"]
                        if c["call_id"] in starts), "tool execution preceded the issuing reply")
        turns.append(reply)
    return {"target_drone": target, "turns": turns, "flight_actions": actions}
