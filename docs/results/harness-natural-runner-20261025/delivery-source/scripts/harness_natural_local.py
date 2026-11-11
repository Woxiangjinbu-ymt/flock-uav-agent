"""Local-only transport fixtures for the natural-entry adapter acceptance.

The runner never selects a skill itself. These synthetic model replies live in
this separate test entry point, which denies sockets and provider configuration.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import threading
import time

from harness_episode_local import block_network, sha
from harness_natural_runner import run_worker, run_scorer


def factory_for(run, trial):
    log_path = run / "trials" / trial["trial_id"] / "private/local-transport.jsonl"
    scenario = trial["scenario"]
    def factory(httpx, public_controls):
        count = 0
        def record(value):
            with log_path.open("a") as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        def tool(name, ident, arguments):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": ident, "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}]}
        def actor_from(payload):
            for row in payload["messages"]:
                if row.get("role") == "tool":
                    try:
                        value = json.loads(row["content"])
                    except (ValueError, TypeError):
                        continue
                    if isinstance(value, list):
                        actors = [x["id"] for x in value if isinstance(x, dict) and x.get("name") == "Drone 3"]
                        if len(actors) == 1:
                            return actors[0]
            raise ValueError("Script needs the actual public drone list")
        def respond(request):
            nonlocal count
            index, count = count, count + 1
            payload = json.loads(request.content)
            record({"kind": "delegated_request", "index": index, "at": time.monotonic(),
                    "body_sha256": sha(request.content), "body_length": len(request.content),
                    "timeouts": request.extensions.get("timeout")})
            if index == 0:
                message = tool("list_drones", "local-list", {})
            elif scenario in {"skill_success", "post_kill_B"}:
                if index == 1:
                    message = tool("run_harness", "local-skill", {"input_json": json.dumps({"action": "start", "skill": "simple_chain"})})
                elif index == 2:
                    message = {"role": "assistant", "content": "Public sequence completed [TASK DONE]"}
                else:
                    raise AssertionError("Unexpected extra model dispatch")
            else:
                actor = actor_from(payload)
                if index == 1:
                    message = tool("take_off", "local-takeoff", {"input_json": json.dumps({"drone_id": actor, "altitude": 14.0})})
                elif index == 2:
                    message = tool("rotate", "local-rotate", {"input_json": json.dumps({"drone_id": actor, "heading": 353.0})})
                elif index == 3 and scenario == "raw_success":
                    message = tool("hover", "local-hover", {"input_json": json.dumps({"drone_id": actor})})
                elif index == 4 and scenario == "raw_success":
                    message = {"role": "assistant", "content": "Public raw sequence completed [TASK DONE]"}
                else:
                    raise AssertionError("Unexpected dispatch after test boundary")
            if index == 2:
                if scenario in {"hung_drain", "watchdog_hang"}:
                    threading.Event().wait()
                elif scenario == "request_timeout":
                    time.sleep(2 * trial["runtime"]["request_limits"]["per_request_timeout_seconds"] + 0.02)
                elif scenario == "task_deadline":
                    time.sleep(max(0, public_controls["deadline"] - time.monotonic()) + 0.05)
                elif scenario == "cancel":
                    public_controls["request_cancel"]()
                    time.sleep(0.05)
            status = 500 if scenario == "http_error" and index == 2 else 200
            response = {"id": "local-response-" + str(index), "object": "chat.completion", "created": 1,
                "model": "local-scripted-sdk", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}
            if scenario == "usage_missing" and index == 2:
                response.pop("usage")
            if status == 500:
                response = {"error": {"message": "controlled local HTTP failure", "type": "server_error"}}
            body = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
            record({"kind": "produced_response", "index": index, "at": time.monotonic(),
                    "status_code": status, "body_sha256": sha(body), "body_length": len(body),
                    "usage": response.get("usage")})
            return httpx.Response(status, content=body, headers={"content-type": "application/json"}, request=request)
        return httpx.MockTransport(respond)
    return factory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--mode", choices=("worker", "score"), required=True)
    args = parser.parse_args()
    network_attempts = block_network()
    run = args.run.resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest["scope"] != "local_mock_wallclock_only" or manifest["online_authorized_executions"] != 0:
        raise ValueError("This entry point only permits local acceptance")
    trial = next(t for t in manifest["trials"] if t["trial_id"] == args.trial)
    if args.mode == "worker":
        run_worker(run, args.trial, factory_for(run, trial), network_attempts=network_attempts)
    else:
        run_scorer(run, args.trial, network_attempts=network_attempts)
