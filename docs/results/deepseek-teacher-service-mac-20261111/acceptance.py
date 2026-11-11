"""Exercise the normal service CLI against a local HTTP peer that counts received POSTs."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "scripts/run_teacher_service.py"
SCENES = ("04cd6269", "ef36e27a")
def read(path):
    return json.loads(path.read_bytes())
def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Peer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        state = self.server.state
        assert self.path == "/chat/completions"
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        body = json.loads(raw)
        with state["mutex"]:
            n = len(state["received"]) + 1
            entry = {"index": n, "request_sha256": sha(raw)}
            state["received"].append(entry)
            with (state["directory"] / "peer-receipts.jsonl").open("ab") as f:
                f.write((json.dumps(entry) + "\n").encode())
                f.flush()
                os.fsync(f.fileno())
        if state["case"] in ("disconnect", "worker-kill"):
            if state["case"] == "worker-kill":
                proc = state["coordinator"]
                ps = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True)
                matches = [int(col[0]) for row in ps.splitlines() if len(col := row.split(None, 2)) == 3
                           and int(col[1]) == proc.pid and "flockcue_session_cpu _child worker " in col[2]
                           and str(state["directory"]) in col[2]]
                assert len(matches) == 1, matches
                os.kill(matches[0], signal.SIGKILL)
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            return
        first = not any(m["role"] == "assistant" for m in body["messages"])
        if first:
            state["tasks"] += 1
        use_tool = first and state["tasks"] % 2 == 0
        msg = {"role": "assistant", "content": "[TASK DONE]"}
        if use_tool:
            msg.update(content=None, reasoning_content="Inspect the public fleet.",
                       tool_calls=[{"type":"function","id":f"local-call-{n}","function":{
                           "name":"list_drones", "arguments":json.dumps({"input_json":"{}"})}}])
        payload = {"id": f"local-{n}", "created": 1, "object": "chat.completion", "model": "local-test-served-version",
                   "choices":[{"index":0, "message":msg,"finish_reason":"tool_calls" if use_tool else "stop"}],
                   "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120,
                            "prompt_cache_hit_tokens":40,"prompt_cache_miss_tokens":60,
                            "completion_tokens_details":{"reasoning_tokens":2 if use_tool else 0}}}
        if use_tool:
            payload["system_fingerprint"] = "local-test-revision"
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def exercise(case, output):
    directory = output / case
    directory.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    state = {"case":case, "directory":directory, "mutex":threading.Lock(), "received":[], "tasks":0}
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run = directory / "run"
    env = {k:os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
    def call(*args, fail=False):
        with (directory/"cli.log").open("ab") as f:
            proc = subprocess.Popen([sys.executable, str(CLI), *map(str,args)], cwd=ROOT, env=env,
                                    stdout=f, stderr=subprocess.STDOUT)
            state["coordinator"] = proc
            rc = proc.wait(timeout=350)
        assert (rc != 0) == fail, (case, args, rc, directory/"cli.log")
    try:
        call("prepare","--run",run,"--transport","loopback_http","--currency","CNY",
             "--loopback-url",f"http://127.0.0.1:{server.server_port}")
        # Even if explicitly asked out of order, the second scene cannot begin.
        call("advance","--run",run,"--scene",SCENES[1],fail=True)
        assert not state["received"]
        call("advance","--run",run,"--scene",SCENES[0], fail=case=="worker-kill")
        count = len(state["received"])
        journal = run/"cost-ledger.jsonl"
        ledger_before = sha(journal.read_bytes())
        call("advance","--run",run,"--scene",SCENES[0])
        stable = sha(journal.read_bytes())
        assert len(state["received"]) == count and (case == "worker-kill" or stable == ledger_before)
        call("advance","--run",run,"--scene",SCENES[0])
        assert len(state["received"]) == count and sha(journal.read_bytes()) == stable
        status = read(run/"batch-status.json")
        assert len(status["tasks"]) == 40 and status["reservations"] == count
        if case == "normal":
            first = run/"checkpoints"/(SCENES[0]+".json")
            first_sha = sha(first.read_bytes())
            assert count == 30 and len({len(t["turns"]) for t in read(first)["tasks"]}) == 2
            call("advance","--run",run,"--scene",SCENES[1])
            before_final = sha(journal.read_bytes())
            call("advance","--run",run,"--scene",SCENES[1])
            assert sha(first.read_bytes()) == first_sha and sha(journal.read_bytes()) == before_final
            status = read(run/"batch-status.json")
            assert not status["blocked"] and status["reservations"] == len(state["received"]) == 60
            assert all(t["status"] == "completed" for t in status["tasks"])
            reports = [read(run/"checkpoints"/(sid+".json")) for sid in SCENES]
            assert all(r["scored"] == 20 and Decimal(r["settled_conservative_charge"]) == Decimal("0.0108")
                       for r in reports)
            assert sum(Decimal(r["settled_conservative_charge"]) for r in reports) == Decimal(status["settled"])
            turns = [t for report in reports for task in report["tasks"] for t in task["turns"]]
            assert {t["model"] for t in turns} == {"local-test-served-version"}
            assert {t["system_fingerprint"] for t in turns} == {None, "local-test-revision"}
        else:
            call("advance","--run",run,"--scene",SCENES[1])
            assert len(state["received"]) == count == 1 and sha(journal.read_bytes()) == stable
            assert status["blocked"] and status["locked_reservation"] == "2.162688"
            assert sum(t["status"] == "unstarted" for t in status["tasks"]) == 39
        events=[json.loads(line)["event"] for line in journal.read_bytes().splitlines()]
        assert [e["request_body_sha256"] for e in events if e["kind"]=="reserved"] == [
            r["request_sha256"] for r in state["received"]]
        output_data={"case":case, "peer_received":len(state["received"]), "first_scene_peer_received":count,
                     "repeat_dispatches":0, "remote_api_calls":0, "actual_fee":"0", "planned":40,
                     "reservations":status["reservations"], "settled_estimate":status["settled"],
                     "locked_reservation":status["locked_reservation"],"stop_reason":status["stop_reason"],
                     "task_statuses":dict(Counter(t["status"] for t in status["tasks"])),
                     "ledger_sha256":sha(journal.read_bytes()),"peer_file_sha256":sha((directory/"peer-receipts.jsonl").read_bytes())}
        (directory/"verified.json").write_text(json.dumps(output_data, indent=2)+"\n")
        return output_data
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("normal","disconnect","worker-kill"), action="append")
    args=parser.parse_args()
    base=args.output.resolve()
    base.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures=[ex.submit(exercise,c, base) for c in args.case or ["normal","disconnect","worker-kill"]]
        for f in futures:
            print(json.dumps(f.result()), flush=True)
