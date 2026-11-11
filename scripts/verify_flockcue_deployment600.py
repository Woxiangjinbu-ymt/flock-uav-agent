"""Exercise the staged October HTTP simulator and FlockCue with a loopback teacher."""
import argparse
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import SimpleQueue
import sys
from threading import Event, Thread


SENSE_DESCRIPTION = "27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227"
TOOLS = ("sense_nearby_entities", "run_harness")
SCENE_ID = "ef36e27a"
TASK_ID = "e59d948d"


def message_for_turn(payload, observed):
    functions = {t["function"]["name"]: t["function"] for t in payload["tools"]}
    sense = functions[TOOLS[0]]
    if sha256(sense["description"].encode()).hexdigest() != SENSE_DESCRIPTION:
        raise ValueError("Unexpected sensing description")
    if sense["parameters"].get("required") or TOOLS[1] not in functions:
        raise ValueError("FlockCue tool contract missing")
    if payload["model"] != "deepseek-flash" or payload.get("temperature") != 0.1:
        raise ValueError("Wrong teacher or sampling configuration")
    if {"thinking", "reasoning_effort", "max_tokens", "max_completion_tokens"} & payload.keys():
        raise ValueError("Unexpected model-request override")
    receipts = [m for m in payload["messages"] if m["role"] == "tool"]
    index = len(receipts)
    if index:
        for m in payload["messages"]:
            if m["role"] == "assistant" and m.get("tool_calls") and not m.get("reasoning_content"):
                raise ValueError("Tool history lost the teacher reasoning field")
        value, _ = json.JSONDecoder().raw_decode(receipts[-1]["content"].lstrip())
        if index == 1:
            if len(value["sensed_drone_ids"]) != 10:
                raise ValueError("Empty sensing call did not observe the ten-drone fleet")
            observed["sensed_drones"] = len(value["sensed_drone_ids"])
        elif index == 2:
            if value["skill"] != "simple_chain" or value["status"] != "completed" or value["cursor"] != 3:
                raise ValueError("Takeoff, rotation and hover did not all execute")
            observed["skill_steps"] = value["cursor"]
            observed["last_action"] = value["state"]["last_action"]["tool"]
            return {"role": "assistant", "content": "Completed the requested flight."}
        else:
            raise ValueError("Unexpected extra model turn")
    args = {} if index == 0 else {"input_json": {"action": "start", "skill": "simple_chain"}}
    return {"role": "assistant", "content": "", "reasoning_content": "Fixture selects the registered action.",
            "tool_calls": [{"id": f"fixture-{index}", "type": "function", "function": {
                "name": TOOLS[index], "arguments": json.dumps(args)}}]}


@contextmanager
def loopback_teacher():
    observed = {"requests": 0, "errors": []}

    class Teacher(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                if self.path != "/chat/completions":
                    raise ValueError("Unexpected local endpoint")
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed["requests"] += 1
                msg = message_for_turn(payload, observed)
                value = {"id": f"local-{observed['requests']}", "object": "chat.completion", "created": 1,
                         "model": "deepseek-flash", "system_fingerprint": "local-fixture",
                         "choices": [{"index": 0, "message": msg,
                                      "finish_reason": "tool_calls" if "tool_calls" in msg else "stop"}],
                         "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26}}
                status = 200
            except Exception as exc:
                observed["errors"].append(f"{type(exc).__name__}: {exc}")
                value, status = {"error": {"message": observed["errors"][-1]}}, 400
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with ThreadingHTTPServer(("127.0.0.1", 0), Teacher) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", observed
        finally:
            server.shutdown()
            thread.join()


def run(stage, output):
    sys.path.insert(0, str(stage / "scripts"))
    import reproduction as r
    import reproduction_parallel as parallel

    if r.ROOT.resolve() != stage:
        raise ValueError("The October runner must be imported from the prepared stage")
    output.mkdir(parents=True, exist_ok=False)
    plan = r.read_json(Path(__file__).resolve().parents[1] / "experiments/plans/flockcue-600-first-pass.json")
    scene = next(s for s in plan["sessions"] if s["id"] == SCENE_ID)
    if scene["tasks"][0]["id"] != TASK_ID:
        raise ValueError("Unexpected first task")
    scene = {**scene, "tasks": scene["tasks"][:1]}
    plan = parallel.scene_plan(plan, scene)
    r.save_json(output / "manifest.json", {"identity_sha256": "offline-fixture"})
    directory = output / SCENE_ID
    directory.mkdir()
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "DEEPSEEK_API_KEY", "BUPT_API_KEY", "DASHSCOPE_API_KEY"):
        os.environ.pop(key, None)
    os.environ.update(HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
                      ALL_PROXY="http://127.0.0.1:1", NO_PROXY="localhost,127.0.0.1")
    with loopback_teacher() as (url, observed):
        r.save_json(output / "agent-settings.json", {"selected_provider": "deepseek_official",
            "share_blackboard_by_session": True, "toolchain_json_recording": True,
            "provider_configs": {"deepseek_official": {"type": "openai-compatible", "base_url": url,
                "default_model": "deepseek-flash", "api_key": "", "requires_api_key": True}}})
        result = parallel.run_scene(r, output, plan, scene, directory, "local-fixture-key", Event(), SimpleQueue())
    if observed["errors"] or result["status"] != "completed":
        raise ValueError(f"Fixture failed: {observed}, {result['error']}")
    row, = parallel.validate_scene_evidence(r, plan, scene, directory)
    assert row["task_pass"] and row["continuation_safe"] and not row["agent_timed_out"]
    assert observed["requests"] == 3 and observed["last_action"] == "hover"
    assert row["expected_checks"] == 2 and all(leaf["result"] for leaf in row["leaves"])
    checks = {"session_id": SCENE_ID, "task_id": TASK_ID, "external_teacher_requests": 0,
              "original_checker_passed": row["task_pass"], "passed_leaves": row["expected_checks"],
              "worker_finished": row["continuation_safe"], "loopback": observed}
    r.save_json(output / "verification.json", checks)
    print(json.dumps(checks))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.stage.resolve(), args.output.resolve())
