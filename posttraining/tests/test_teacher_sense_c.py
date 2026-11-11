"""Verify a no-argument real SDK tool call at the loopback service and simulator."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import threading

from flockcue_session_cpu.__main__ import run_session_cli
from flockcue_session_cpu.teacher_sense import execution_history
from flockcue_session_cpu.teacher_transport import create_ledger
from run_teacher_pilot40 import load_pilot, PILOT, PILOT_SHA
from thinking_events.sense_example_prepare import SENSE


def test_c_preserves_other_calls_and_raw_messages():
    def call(name, args):
        return {"id":"c", "function":{"name":name, "arguments":args}}
    for msg in ({"content":"done", "tool_calls":None},
                {"tool_calls":[call("navigate_to","{}"),call(SENSE,'{"detail":"full"}')]}):
        assert execution_history(msg) is msg
    raw = {"tool_calls":[call(SENSE,' { \r\n} ')]}
    assert execution_history(raw)["tool_calls"] == [call(SENSE,'{"input_json":{}}')]
    assert raw["tool_calls"] == [call(SENSE,' { \r\n} ')]


def test_real_sdk_keeps_id_and_executes_omitted_sense(tmp_path):
    received = []
    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            call = len(received)
            invoked = {"id":"public-empty-sense-1", "type":"function",
                       "function":{"name":SENSE, "arguments":"{}"}}
            message = ({"role":"assistant", "content":None, "reasoning_content":"Use local sensors.",
                       "tool_calls":[invoked]} if call == 1 else {"role":"assistant", "content":"[TASK DONE]"})
            body = {"id":f"test-c-{call}", "object":"chat.completion", "created":1, "model":"loopback-c",
                    "choices":[{"message":message, "index":0,"finish_reason":"tool_calls" if call==1 else "stop"}]}
            if call < 3:  # Stop at the next task, after the first trajectory has been closed and scored.
                body["usage"] = {"prompt_tokens":10, "completion_tokens":10, "total_tokens":20}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pilot = load_pilot()
    spec = pilot["sessions"][0]
    scene = tmp_path/"sessions"/spec["session_id"]
    (tmp_path/"sessions").mkdir()
    shutil.copyfile(PILOT, tmp_path/"pilot40.json")
    create_ledger(tmp_path/"cost-ledger.jsonl", pilot["pilot_limits"])
    try:
        run_session_cli(scene, spec["session_id"], "none", teacher={
            "transport":"loopback_http", "loopback_url":f"http://127.0.0.1:{server.server_port}",
            "sense_interface":"C", "manifest":"../../pilot40.json", "batch_root":"../..",
            "currency":"CNY", **{k: pilot["pilot_limits"]["accounts"]["CNY"][k]
                                 for k in ("batch_cap", "per_task_cap")}, "pilot_sha256":PILOT_SHA,
            "session_id":spec["session_id"], "seed":spec["runtime_seed"]})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert len(received) == 3
    offered = next(t["function"] for t in received[0]["tools"] if t["function"]["name"]==SENSE)
    assert not offered["parameters"].get("required")
    digest = sha256(offered["description"].encode()).hexdigest()
    assert digest == "27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227"
    folder, *_ = sorted((scene/"tasks").iterdir())
    read = lambda p: json.loads(p.read_bytes())
    msg = read(folder/"provider-wire/turn-01.response.json")["choices"][0]["message"]
    echoed = next(m for m in received[1]["messages"] if m["role"]=="assistant")
    assert msg["tool_calls"][0]["function"]["arguments"] == "{}"
    assert json.loads(echoed["tool_calls"][0]["function"]["arguments"]) == {"input_json":{}}
    assert echoed["reasoning_content"] == msg["reasoning_content"]
    receipt = next(m for m in received[1]["messages"] if m["role"]=="tool")
    assert receipt["tool_call_id"] == msg["tool_calls"][0]["id"] == echoed["tool_calls"][0]["id"]
    sensed = json.loads(receipt["content"].split("\n\n[M1-R")[0])
    assert len(sensed["sensed_drone_ids"]) == 5
    actions = [r for r in read(folder/"actions.json")["requests"] if r["origin"].get("tool_call_id")==receipt["tool_call_id"]]
    assert len(actions)==6 and all(r["method"]=="GET" and not r["admin"] for r in actions)
    before = read(folder/"before.json")["policy_start"]["blackboard_sha256"]
    boundary = read(folder/"boundary.json")
    assert boundary["terminal"]["blackboard_sha256"] != before
    assert read(folder/"score.json")["worker_pid"] != read(folder/"score.json")["scorer_pid"]
    assert read(scene/"worker-boundary.json")["network_attempts"] == []
    (tmp_path/"interface-c-proof.json").write_text(json.dumps({
        "tool_description_sha256":digest, "raw_sense_arguments":"{}", "echoed_arguments":echoed["tool_calls"][0]["function"]["arguments"],
        "tool_call_id":receipt["tool_call_id"], "listed_drones_sensed":len(sensed["sensed_drone_ids"]),
        "public_gets":len(actions), "blackboard_changed":True, "independently_scored":True,
        "real_api_calls":0}, indent=2)+"\n")
