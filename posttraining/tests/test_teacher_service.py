"""Exercise the official-teacher transport against loopback sockets and reopening a real journal."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from flockcue_session_cpu.teacher_network import endpoint, restrict_network
from flockcue_session_cpu.teacher_service import TeacherService, exclusive_worker, recover_ledger
from flockcue_session_cpu.teacher_transport import create_ledger, ledger_snapshot
from langchain_core.messages import HumanMessage

LIMITS = json.loads((ROOT / "docs/specs/04-deepseek-pilot40/pilot40.json").read_bytes())["pilot_limits"]


@contextmanager
def provider(fault):
    received = []
    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            if fault == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            reply = {"id": "local", "object": "chat.completion", "created": 1, "model": "variable-service-version",
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": "done",
                                   "reasoning_content": ""}, "finish_reason": "stop"}]}
            if fault != "missing_usage":
                reply["usage"] = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
            raw = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with ThreadingHTTPServer(("127.0.0.1", 0), Peer) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", received
        finally:
            server.shutdown()
            thread.join()


def guarded(monkeypatch, url):
    # Restore the process-wide socket patches after each test.
    for obj, key in ((socket, "getaddrinfo"), (socket.socket, "connect"), (socket.socket, "connect_ex")):
        monkeypatch.setattr(obj, key, getattr(obj, key))
    return restrict_network(url)


@pytest.mark.parametrize("fault", ["complete", "disconnect", "missing_usage"])
def test_live_sdk_uses_loopback_and_never_replays_unknown_usage(tmp_path, monkeypatch, fault):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-canary-key-must-stay-unread")
    ledger = tmp_path / "cost-ledger.jsonl"
    account = LIMITS["accounts"]["CNY"]
    create_ledger(ledger, LIMITS)
    with provider(fault) as (url, received):
        blocked_network = guarded(monkeypatch, url)
        config = {"transport": "loopback_http", "loopback_url": url, "currency": "CNY",
                  "per_task_cap": account["per_task_cap"], "batch_cap": account["batch_cap"]}
        teacher = TeacherService(ledger, LIMITS, config)
        try:
            assert teacher.begin("task1", "test", tmp_path, time.monotonic() + 60)
            model = teacher.model_factory()
            if fault == "complete":
                assert model.invoke([HumanMessage(content="test")]).content == "done"
            else:
                with pytest.raises(Exception):
                    model.invoke([HumanMessage(content="test")])
            result = teacher.finish()
        finally:
            teacher.close()
        state = recover_ledger(ledger, LIMITS, currency="CNY", batch_cap=config["batch_cap"])
        assert result["attempts"] == len(received) == len(state["attempts"]) == 1
        assert result["external_provider_calls"] == 0 and not blocked_network
        record = json.loads((tmp_path/"provider-timing/attempt-01.json").read_bytes())
        assert record["dispatched"] and (record["sdk_received"] == (fault == "complete"))
        assert record["system_fingerprint"] is None
        assert state["blocked"] is (fault != "complete")
        assert state["locked_reservation"] == ("0" if fault == "complete" else account["request_reservation"])
        assert received[0][1] == (tmp_path/"provider-wire/turn-01.request.json").read_bytes()
        if fault != "disconnect":
            assert record["model"] == "variable-service-version"
        restarted = TeacherService(ledger, LIMITS, config)
        try:
            if fault != "complete":
                assert not restarted.begin("task1", "test", tmp_path, time.monotonic()+60)
            assert len(received) == 1
        finally:
            restarted.close()
        with pytest.raises(PermissionError):
            socket.getaddrinfo("unexpected.invalid", 443)
        assert blocked_network == ["forbidden_provider_socket"]


def test_recovery_excludes_an_active_worker(tmp_path):
    path = tmp_path / "cost-ledger.jsonl"
    create_ledger(path, LIMITS)
    with exclusive_worker(tmp_path):
        with pytest.raises(RuntimeError, match="still active"):
            recover_ledger(path, LIMITS, currency="CNY", batch_cap="30")
    assert not ledger_snapshot(path, LIMITS)["blocked"]


@pytest.mark.parametrize("url", ["https://127.0.0.1:1234", "http://localhost:1234", "http://127.0.0.1:80/redirect",
                                "http://127.0.0.1:1234@other.example:1234", "http://127.0.0.1"])
def test_loopback_endpoint_scope(url):
    with pytest.raises(ValueError):
        endpoint({"transport":"loopback_http", "loopback_url":url})
