"""Run the first published hover task with a local model, live policy and real simulator."""
from contextlib import ExitStack
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
from threading import Thread
import uuid
import zipfile

from runtime import executor
from simulation.server.config.privilege_keys import ADMIN_PRIVILEGE_KEYS

SCENE = "Area_Assignment_and_Patrol_Easy_3_04cd6269.json"
TASK_ID = "a05a871a"
# Captured first HTTP body of 04cd6269/a05a871a on the 600-task deployment.
# The freshly generated command_id is the only nonconstant field in this request.
FIRST_BODY_SHA = "9098cb94178d2bcf60f145397b9e8f1d382bd4ac7b1c251376a0fe140d18a96a"
ZERO_ID = "00000000-0000-0000-0000-000000000000"


class HoverModel(BaseHTTPRequestHandler):
    """Exercise the registered skill through the same SDK as the teacher."""
    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        body = json.loads(raw)
        self.server.requests.append(raw)
        turns = [item for item in body["messages"] if item["role"] == "tool"]
        arguments = {"action": "start", "skill": "simple_chain"}
        if turns:
            receipt = json.loads(turns[-1]["content"])
            if receipt.get("resumable"):
                arguments = {"action": "resume", "plan_id": receipt["plan_id"]}
            else:
                arguments = None
        message = {"role": "assistant", "content": "Hover complete. [TASK DONE]"}
        if arguments is not None:
            message.update(content=None, tool_calls=[{
                "type": "function", "id": f"smoke-{len(turns)}",
                "function": {"name": "run_harness", "arguments": json.dumps({"input_json": arguments})},
            }])
        response = {"id": "smoke", "model": "deepseek-flash", "object": "chat.completion", "created": 1,
                    "choices": [{"index": 0, "message": message,
                                 "finish_reason": "tool_calls" if arguments is not None else "stop"}]}
        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def first_request_sha(raw):
    prompt = json.loads(raw)["messages"][0]["content"]
    ids = re.findall(r'"command_id": "([0-9a-f-]{36})"', prompt)
    if len(ids) != 1 or raw.count(ids[0].encode()) != 1:
        raise ValueError("Expected one generated command ID in the first policy request")
    return hashlib.sha256(raw.replace(ids[0].encode(), ZERO_ID.encode())).hexdigest()


def run(output):
    root = executor.ROOT
    with zipfile.ZipFile(root / "datasets/flockcue-600/sessions-600.zip") as bundle:
        scene = json.loads(bundle.read(SCENE))
    task = scene["tasks"][0]
    if task["id"] != TASK_ID:
        raise ValueError("Frozen first task changed")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = output / "fixture-settings.json"
    admin = ADMIN_PRIVILEGE_KEYS[0]
    token, lease = uuid.uuid4().hex, uuid.uuid4().hex
    server_port, policy_port = executor.allocate_ports()
    simulator = executor.API(f"http://127.0.0.1:{server_port}", admin)
    policy = executor.API(f"http://127.0.0.1:{policy_port}", token)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "NO_PROXY": "127.0.0.1,localhost",
           "no_proxy": "127.0.0.1,localhost"}
    with ExitStack() as stack:
        peer = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), HoverModel))
        peer.requests = []
        thread = Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        stack.callback(thread.join)
        stack.callback(peer.shutdown)
        executor.save_json(config, {"selected_provider": "deepseek_official", "share_blackboard_by_session": True,
            "toolchain_json_recording": True, "provider_configs": {"deepseek_official": {
                "type": "openai-compatible", "base_url": f"http://127.0.0.1:{peer.server_port}",
                "api_key": "fixture", "default_model": "deepseek-flash", "requires_api_key": True}}})
        server = executor.Child([executor.interpreter("server"), "-m", "runtime.server",
            "--host", "127.0.0.1", "--port", str(server_port)], root, output / "server.log",
            {**env, "REPRO_ADMIN_KEY": admin, "REPRO_SERVER_LOG_DIR": str(output / "server-traces")})
        stack.callback(server.stop)
        executor.wait_ready(server, simulator, "/version")
        simulator.call("POST", f"/sessions/{scene['id']}", {**scene, "status": "inactive"})
        simulator.call("POST", f"/sessions/{scene['id']}/set-current")
        agent = executor.Child([sys.executable, "-m", "flockcue.service", "--config", str(config),
            "--port", str(policy_port), "--logs", str(output / "agent-traces")], root, output / "agent.log",
            {**env, "UAV_API_URL": simulator.url, "REPRO_CONTROL_TOKEN": token})
        stack.callback(agent.stop)
        executor.wait_ready(agent, policy, "/health", agent=True)
        simulator.call("POST", "/_reproduction/prepare", {})
        executor.prepare_task(simulator, scene, {"land_wait_seconds": 2, "charge_wait_seconds": 2})
        window = simulator.call("POST", "/_reproduction/open", {"lease": lease, "timeout_seconds": 500})
        accepted = policy.call("POST", "/agent/command/async", {"command": task["content"]},
            extra_headers={"X-Reproduction-Lease": lease, "X-Reproduction-Cutoff": str(window["cutoff_at"])})
        job = executor.wait_job(policy, accepted["job_id"], 60, 0.2, lambda *_: None)
        frozen = simulator.call("POST", "/_reproduction/close", {"lease": lease, "drain_seconds": 5})
        worker = executor.wait_worker(policy, accepted["job_id"], 10)
        passed, checks = executor.upstream_evaluate(simulator, task["execution_check_apis"], scene["id"])
        if not (job["status"] == "completed" and worker["worker_finished"] and frozen["quiescent"] and passed):
            raise RuntimeError(f"Smoke task failed; inspect {output}")
        request_sha = first_request_sha(peer.requests[0])
        receipts = [m for m in json.loads(peer.requests[-1])["messages"] if m["role"] == "tool"]
        if not receipts or json.loads(receipts[-1]["content"]).get("status") != "completed":
            raise ValueError("No completed run_harness tool receipt on the wire")
        if request_sha != FIRST_BODY_SHA:
            raise ValueError(f"First model request differs from the formal deployment: {request_sha}")
        summary = {"scene": scene["id"], "task": task["id"], "model": "local fixture", "remote_provider_calls": 0,
                   "first_wire_sha256_normalized": request_sha, "tool_count": len(json.loads(peer.requests[0])["tools"]),
                   "local_http_calls": len(peer.requests), "skill": "simple_chain", "skill_status": "completed",
                   "worker_finished": True, "passed": passed, "checks": checks}
        executor.save_json(output / "result.json", summary)
        return summary
