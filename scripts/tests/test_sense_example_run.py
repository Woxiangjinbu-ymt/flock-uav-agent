"""CPU checks for the per-prefix request boundary, recovery, timeout and card budget."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinking_events import sense_example_run as runner


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def read(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def slots(tmp_path):
    rows = []
    for condition in ["A", "B"]:
        key = f"original-t2-{condition}"
        body = {"messages": [{"role": "assistant", "content": "", "reasoning_content": "history"}],
                "tools": [{"type": "function", "function": {"name": "sense_nearby_entities",
                                                           "description": condition, "parameters": {}}}]}
        raw = json.dumps(body).encode()
        path = tmp_path / (key + ".json")
        path.write_bytes(raw)
        rows.append({"slot_key": key, "slot_id": "original", "condition": condition,
                     "turn": 2, "spent_before": 1234, "thinking": True, "seed": 42,
                     "input_ids_sha256": hashlib.sha256(b"[1,2]").hexdigest(), "max_new_tokens": 2048,
                     "request_sha256": runner.digest(raw), "request_path": path.name,
                     "original_outcome": "missing_input_json", "private_checker": "keep out"})
    return rows


class Factory:
    def __init__(self, replies, clock):
        self.replies = iter(replies)
        self.clock = clock
        self.calls = []
        self.services = []

    def __call__(self):
        factory = self

        class Service:
            closed = False
            metadata = {"model": "fixture", "eos_ids": [1]}

            def close(self):
                self.closed = True

            cancel = close

            def request(self, **request):
                assert not self.closed
                factory.calls.append(request)
                factory.clock.advance(2)
                status = next(factory.replies)
                identity = {"slot": request["slot"], "turn": request["turn"]}
                if status != "ok":
                    return {**identity, "status": "stopped", "stop_reason": status, "usage": None}
                return {**identity, "status": "ok", "stop_reason": None, "thinking": request["thinking"],
                        "input_ids": [1, 2], "generated_ids": [1], "max_new_tokens": 2048,
                        "sampling": runner.SAMPLING, "seed": request["seed"], "usage": {"total_tokens": 3}}

        self.clock.advance(1)
        service = Service()
        self.services.append(service)
        return service


def execute(tmp_path, rows, factory, **changes):
    identity = {"model": "frozen", "runner_sha256": "v1", "plan_sha256": "plan1", **changes}
    return runner._execute(tmp_path, rows, identity, factory.clock.read() + 86400, factory,
                           wall=factory.clock.read, monotonic=factory.clock.read)


def test_public_request_and_reproducible_identity(tmp_path):
    rows = slots(tmp_path)
    factory = Factory(["ok", "ok"], Clock())
    ledger = execute(tmp_path, rows, factory)
    assert ledger["gpu_seconds"] == 5
    assert ledger["incremental_cny"] == 0 and ledger["stop_reason"] == "finished"
    assert ledger["status_counts"] == {"completed": 2}
    assert len(factory.services) == 1 and factory.services[0].closed
    first, second = factory.calls
    assert first["seed"] == second["seed"] == 42 and first["spent_tokens"] == second["spent_tokens"] == 1234
    assert first["thinking"] and second["thinking"] and first["messages"] == second["messages"]
    assert first["tools"][0]["function"]["description"] == "A"
    assert second["tools"][0]["function"]["description"] == "B"
    assert set(first) == {"slot", "turn", "thinking", "seed", "spent_tokens", "tools", "messages",
                          "request_timeout", "episode_deadline"}
    assert first["request_timeout"] == second["request_timeout"] == 120
    assert first["episode_deadline"] == 1000001 + 121 and second["episode_deadline"] == 1000003 + 121
    assert execute(tmp_path, rows, factory)["gpu_seconds"] == 5
    for key in ("model", "runner_sha256", "plan_sha256"):
        with pytest.raises(ValueError, match="Changed"):
            execute(tmp_path, rows, factory, **{key: "changed"})
    assert len(factory.calls) == 2


@pytest.mark.parametrize("after_receipt, expected_status", [(False, "missing"), (True, "completed")])
def test_interrupted_slot_is_never_retried(tmp_path, monkeypatch, after_receipt, expected_status):
    rows = slots(tmp_path)
    factory = Factory(["ok", "ok"], Clock())
    original_write = runner._write

    def crash(path, data):
        if path.name == rows[0]["slot_key"] + ".json":
            if after_receipt and data.get("state") == "terminal":
                raise KeyboardInterrupt
            if not after_receipt and path.parent.name == "receipts":
                raise KeyboardInterrupt
        original_write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_write", crash)
        with pytest.raises(KeyboardInterrupt):
            execute(tmp_path, rows, factory)
    assert len(factory.calls) == 1 and factory.services[0].closed
    ledger = execute(tmp_path, rows, factory)
    receipt = runner._read(tmp_path / "receipts" / (rows[0]["slot_key"] + ".json"))
    assert receipt["status"] == expected_status
    assert (receipt["response"] is None) == (expected_status == "missing")
    assert ledger["gpu_seconds"] == 6 and len(ledger["segments"]) == 2
    assert len(factory.calls) == 2


def test_simultaneous_coordinators_dispatch_once(tmp_path):
    rows = slots(tmp_path)
    factory = Factory(["ok", "ok"], Clock())
    barrier = threading.Barrier(2)

    def start():
        barrier.wait()
        return execute(tmp_path, rows, factory)
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(start) for _ in range(2)]
        assert all(j.result()["status_counts"] == {"completed": 2} for j in jobs)
    assert len(factory.calls) == 2 and len(factory.services) == 1


def test_timeout_and_transport_loss_are_separate(tmp_path):
    rows = slots(tmp_path)
    factory = Factory(["request_timeout", "student_transport_error"], Clock())
    ledger = execute(tmp_path, rows, factory)
    assert ledger["status_counts"] == {"timeout": 1, "missing": 1}
    assert len(factory.services) == 2 and all(service.closed for service in factory.services)
    for row in rows:
        saved = runner._read(tmp_path / "receipts" / (row["slot_key"] + ".json"))
        assert saved["response"]["usage"] is None


@pytest.mark.parametrize("load, available, expected", [(False, 180, None), (False, 179, "gpu_time_cap"),
                                                       (True, 360, None), (True, 359, "gpu_time_cap")])
def test_budget_boundary(load, available, expected):
    used = runner.GPU_CAP - available
    assert runner._admission_stop(used, 10, 10 + runner.GPU_CAP, load=load) == expected
    assert runner._admission_stop(0, 10, 10 + 179, load=load) == "prepaid_window"


def test_budget_stop_keeps_unstarted_slots(tmp_path):
    rows = slots(tmp_path)
    factory = Factory([], Clock())
    ledger = runner._execute(tmp_path, rows, {}, factory.clock.read() + 359, factory,
                             wall=factory.clock.read, monotonic=factory.clock.read)
    assert ledger["stop_reason"] == "prepaid_window" and ledger["status_counts"] == {"unstarted": 2}
    assert not factory.calls and not factory.services and ledger["gpu_seconds"] == 0
