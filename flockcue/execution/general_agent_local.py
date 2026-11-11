"""Public scripted replies for local integration checks, never a teacher policy."""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import json
import math
import time
import threading

from harness_episode_local import block_network, sha
from harness_natural_runner import run_worker, run_scorer


def _public_receipt(text):
    """Decode a real wire tool receipt before the maintained R2 advisory suffix."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate public receipt field")
            result[key] = value
        return result
    if not isinstance(text, str):
        raise ValueError("Public receipt must be JSON text")
    value, end = json.JSONDecoder(object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite public receipt"))).raw_decode(text)
    suffix = text[end:]
    if suffix and not suffix.startswith("\n\n[M1-R task evidence; derived from actual tool receipts]\n"):
        raise ValueError("Unknown public receipt decoration")
    return value


def partial_remaining_point(drone_name, messages):
    """Script-fixture helper, never installed as an Agent recovery algorithm.

    Bind the original path and its actual partial receipt by raw call ID. Only
    a single unfinished suffix point is supported. Require a later public state
    read at the last confirmed point, with no intervening model action. No
    checker, scene, private state, requested-only progress or guessed remainder.
    """
    actor = resolve_public("$drone:" + drone_name, messages)
    calls, records = {}, []
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                ident = call.get("id")
                if not ident or ident in calls:
                    raise ValueError("Ambiguous public tool call ID")
                function = call.get("function", {})
                arguments = _public_receipt(function.get("arguments"))
                params = arguments.get("input_json") if isinstance(arguments, dict) else None
                params = _public_receipt(params) if isinstance(params, str) else params
                calls[ident] = (function.get("name"), params)
        if message.get("role") == "tool":
            ident = message.get("tool_call_id")
            if ident not in calls or any(row[1] == ident for row in records):
                raise ValueError("Missing or duplicate public tool association")
            name, params = calls[ident]
            if message.get("name") not in (None, name):
                raise ValueError("Public receipt name mismatch")
            records.append((index, ident, name, params, _public_receipt(message.get("content"))))
    candidates = [row for row in records if row[2] == "move_along_path" and isinstance(row[3], dict)
        and row[3].get("drone_id") == actor and isinstance(row[4], dict) and row[4].get("status") == "partial_success"]
    if len(candidates) != 1:
        raise ValueError("Expected one public partial path receipt for this actor")
    index, _, _, params, receipt = candidates[0]
    if (receipt.get("drone_id") != actor or receipt.get("command") != "move_along_path"
            or params.get("allow_partial_move") is not True):
        raise ValueError("Partial receipt actor/command mismatch")
    def triple(point):
        value = [point.get(key) for key in ("x", "y", "z")] if isinstance(point, dict) else point
        if (not isinstance(value, (list, tuple)) or len(value) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
            raise ValueError("Partial path coordinates must be finite explicit XYZ")
        return tuple(value)
    requested = [triple(point) for point in params.get("waypoints", [])]
    applied = [triple(point) for point in receipt.get("successful_points", [])]
    remaining = [triple(point) for point in receipt.get("unsuccessful_points", [])]
    if (not applied or len(remaining) != 1 or applied + remaining != requested
            or type(receipt.get("successful_points_count")) is not int
            or receipt["successful_points_count"] != len(applied)
            or type(receipt.get("unsuccessful_points_count")) is not int
            or receipt["unsuccessful_points_count"] != len(remaining)):
        raise ValueError("Partial point lists do not faithfully partition the requested path")
    following = [row for row in records if row[0] > index]
    if not following or any(row[2] != "get_drone_status" or not isinstance(row[3], dict)
                            or row[3].get("drone_id") != actor for row in following):
        raise ValueError("Partial recovery requires a later public status read and no intervening action")
    status = following[-1][4]
    if (not isinstance(status, dict) or status.get("id") != actor
            or triple(status.get("position")) != applied[-1]):
        raise ValueError("Public state does not confirm the last applied path point")
    return dict(zip(("x", "y", "z"), remaining[0]))


def validate_trace(trace):
    if not isinstance(trace, list) or not 1 <= len(trace) <= 32:
        raise ValueError("Trace must contain 1..32 public scripted replies")
    for step in trace:
        if not isinstance(step, dict):
            raise ValueError("Invalid scripted reply")
        if "parallel_tools" in step:
            calls = step.get("parallel_tools")
            if (set(step) != {"parallel_tools", "local_api_barrier"}
                    or step["local_api_barrier"] != "two_public_calls_v1"
                    or not isinstance(calls, list) or len(calls) != 2
                    or any(not isinstance(call, dict) or "tool" not in call or "parallel_tools" in call for call in calls)):
                raise ValueError("Parallel fixture requires exactly two public tools and an explicit local barrier")
            validate_trace(calls)
            continue
        if set(step) == {"final"} and isinstance(step["final"], str):
            continue
        if (not {"tool", "arguments"} <= set(step) or set(step) - {"tool", "arguments", "input_json_encoding"}
                or ("input_json_encoding" in step and step["input_json_encoding"] != "object")
                or not isinstance(step["tool"], str)
                or not isinstance(step["arguments"], dict)):
            raise ValueError("Trace accepts tool/arguments or final only")
    return trace


def resolve_public(value, messages):
    """Resolve actor names from actual policy-visible receipts, not fixture IDs."""
    if isinstance(value, str) and value.startswith("$partial_remaining:"):
        parts = value.split(":")
        if len(parts) != 3 or parts[2] not in {"x", "y", "z"}:
            raise ValueError("Partial fixture selector requires a drone name and XYZ axis")
        return partial_remaining_point(parts[1], messages)[parts[2]]
    if value == "$plan:latest":
        controls = {call.get("id") for m in messages if m.get("role") == "assistant"
            for call in m.get("tool_calls", []) if call.get("function", {}).get("name") == "run_harness"}
        for message in reversed(messages):
            if message.get("role") != "tool" or message.get("tool_call_id") not in controls:
                continue
            try:
                receipt = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            if isinstance(receipt, dict) and isinstance(receipt.get("plan_id"), str):
                return receipt["plan_id"]
        raise ValueError("No plan_id in public tool receipts")
    if isinstance(value, str) and value.startswith("$drone:"):
        name = value[len("$drone:"):]
        actors = set()
        for message in messages:
            if message.get("role") != "tool":
                continue
            try:
                receipt = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            if isinstance(receipt, list):
                actors.update(item["id"] for item in receipt if isinstance(item, dict)
                              and item.get("name") == name and isinstance(item.get("id"), str))
        if len(actors) != 1:
            raise ValueError("Script actor is absent or ambiguous in public receipts")
        return actors.pop()
    if isinstance(value, dict):
        return {key: resolve_public(child, messages) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_public(child, messages) for child in value]
    return value


def arm_local_api_barrier(tool_call_ids, record_path):
    """One local-script rendezvous before the admitted client's world lock.

    Used only by explicit parallel fixtures. Each tool's first public API request
    waits until both handlers have entered, so ledger overlap is real. This does
    not change a tool receipt, ledger event, simulator state, or production code.
    """
    import uav_agent
    from harness_full100_world import ORIGIN
    if len(tool_call_ids) != 2 or len(set(tool_call_ids)) != 2:
        raise ValueError("A local barrier needs exactly two distinct tool call IDs")
    client_type = uav_agent.UAVAPIClient
    original = client_type._request
    pending = set(tool_call_ids)
    guard = threading.Lock()
    record_path = Path(record_path)

    def record(value):
        with record_path.open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    def released():
        with guard:
            record({"event": "released", "tool_call_ids": list(tool_call_ids),
                    "scope": "local_script_test_barrier_before_world_lock"})

    barrier = threading.Barrier(2, action=released, timeout=3)

    def synchronized(self, method, endpoint, **kwargs):
        origin = ORIGIN.get() or {}
        ident = origin.get("tool_call_id")
        with guard:
            wait = origin.get("kind") == "model_tool" and ident in pending
            if wait:
                pending.remove(ident)
                record({"event": "entered", "tool_call_id": ident,
                        "method": method, "endpoint": endpoint})
        if wait:
            try:
                barrier.wait()
            except threading.BrokenBarrierError as exc:
                with guard:
                    record({"event": "broken", "tool_call_id": ident})
                raise RuntimeError("Explicit local public-call barrier did not rendezvous") from exc
        return original(self, method, endpoint, **kwargs)

    client_type._request = synchronized


def factory_for(run, trial):
    trace = validate_trace(json.loads((run / "inputs" / trial["trace_file"]).read_text()))
    log_path = run / "trials" / trial["trial_id"] / "private/local-transport.jsonl"

    def factory(httpx, controls):
        index = 0

        def respond(request):
            nonlocal index
            current, index = index, index + 1
            with log_path.open("a") as stream:
                stream.write(json.dumps({"index": current, "body_sha256": sha(request.content)}) + "\n")
            if current >= len(trace):
                raise RuntimeError("Script exhausted; no invented success response")
            payload, step = json.loads(request.content), trace[current]
            if "final" in step:
                message = {"role": "assistant", "content": step["final"]}
            elif "parallel_tools" in step:
                advertised = {item["function"]["name"] for item in payload.get("tools", [])}
                calls = []
                for ordinal, child in enumerate(step["parallel_tools"]):
                    if child["tool"] not in advertised:
                        raise ValueError("Script requested a tool not advertised to the model")
                    arguments = resolve_public(child["arguments"], payload["messages"])
                    if isinstance(arguments.get("input_json"), dict) and child.get("input_json_encoding") != "object":
                        arguments["input_json"] = json.dumps(arguments["input_json"])
                    calls.append({"id": "script-" + str(current) + "-" + str(ordinal), "type": "function",
                                  "function": {"name": child["tool"], "arguments": json.dumps(arguments)}})
                arm_local_api_barrier([call["id"] for call in calls], log_path.with_name("local-api-barriers.jsonl"))
                message = {"role": "assistant", "content": "", "tool_calls": calls}
            else:
                advertised = {item["function"]["name"] for item in payload.get("tools", [])}
                if step["tool"] not in advertised:
                    raise ValueError("Script requested a tool not advertised to the model")
                arguments = resolve_public(step["arguments"], payload["messages"])
                if isinstance(arguments.get("input_json"), dict) and step.get("input_json_encoding") != "object":
                    arguments["input_json"] = json.dumps(arguments["input_json"])
                message = {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "script-" + str(current), "type": "function", "function": {
                        "name": step["tool"], "arguments": json.dumps(arguments)}}]}
            fault = trial.get("fault") or {}
            active = fault.get("at_response") == current
            kind = fault.get("kind") if active else None
            if kind == "cancel":
                controls["request_cancel"]()
            elif kind == "request_timeout":
                time.sleep(trial["runtime"]["request_limits"]["per_request_timeout_seconds"] + 0.1)
            elif kind == "task_deadline":
                time.sleep(max(0, controls["deadline"] - time.monotonic()) + 0.05)
            status = 500 if kind == "http_error" else 200
            response = {"id": "local-" + str(current), "object": "chat.completion", "created": 1,
                "model": "local-scripted-sdk", "choices": [{"index": 0, "message": message,
                    "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}
            if kind == "usage_missing":
                response.pop("usage")
            if status == 500:
                response = {"error": {"message": "local controlled HTTP failure", "type": "server_error"}}
            return httpx.Response(status, json=response, request=request)

        return httpx.MockTransport(respond)
    return factory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--mode", choices=("worker", "score"), required=True)
    args = parser.parse_args()
    attempts = block_network()
    from general_agent import load_manifest
    run = args.run.resolve()
    manifest = load_manifest(run)
    trial = next(t for t in manifest["trials"] if t["trial_id"] == args.trial)
    if args.mode == "worker":
        run_worker(run, args.trial, factory_for(run, trial), network_attempts=attempts)
    else:
        run_scorer(run, args.trial, network_attempts=attempts)
