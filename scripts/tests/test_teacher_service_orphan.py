"""A detached scene worker must retain exclusive dispatch authority during coordinator recovery."""
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from flockcue_session_cpu.teacher_service import exclusive_worker
from flockcue_session_cpu.teacher_transport import ledger_snapshot
from run_teacher_pilot40 import load_pilot


def test_existing_worker_after_coordinator_kill_blocks_duplicate_dispatch(tmp_path):
    base = tmp_path / "run"
    cli = [sys.executable, str(ROOT/"scripts/run_teacher_service.py")]
    env = {k:v for k,v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TMPDIR"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    seen, release = threading.Event(), threading.Event()
    receipts = []

    class SuspendedPeer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            receipts.append(len(body))
            seen.set()
            release.wait(35)
            with suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

    with ThreadingHTTPServer(("127.0.0.1", 0), SuspendedPeer) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        api = f"http://127.0.0.1:{server.server_port}"
        advance = cli + ["advance", "--run", str(base), "--scene", "04cd6269"]
        subprocess.run(cli + ["prepare", "--run", str(base), "--transport", "loopback_http",
                              "--currency", "CNY", "--loopback-url", api], check=True, env=env)
        proc = subprocess.Popen(advance, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert seen.wait(25), "Worker did not send its first simulated request"
            journal = base / "cost-ledger.jsonl"
            before = journal.read_bytes()
            proc.kill()
            proc.wait(timeout=5)
            duplicate = subprocess.run(advance, capture_output=True, timeout=10, env=env)
            assert duplicate.returncode and b"worker is still active" in duplicate.stderr
            assert journal.read_bytes() == before and len(receipts) == 1
        finally:
            release.set()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            server.shutdown()
    # The disconnected worker owns the lease until its ledger is closed.
    deadline = time.monotonic() + 15
    while True:
        try:
            with exclusive_worker(base):
                break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)
    subprocess.run(advance, check=True, env=env, capture_output=True, timeout=10)
    plan = load_pilot()
    state = ledger_snapshot(journal, plan["pilot_limits"])
    assert state["blocked"] and state["locked_reservation"] == "2.162688"
    assert len(state["attempts"]) == len(receipts) == 1
    assert state["settled_conservative_charge"] == "0"
    result = {"transport":"loopback_http", "peer_requests":len(receipts), "real_provider_calls":0,
              "duplicate_advance_refused_while_worker_alive":True, "journal_unchanged_while_worker_alive":True,
              "reservations_after_worker_exit":len(state["attempts"]), "locked_reservation_cny":state["locked_reservation"],
              "settled_cny":state["settled_conservative_charge"], "stop_reason":state["stop_reason"]}
    (tmp_path/"orphan-check.json").write_text(json.dumps(result, indent=2)+"\n")
