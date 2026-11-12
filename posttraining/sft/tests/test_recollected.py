"""Protect the frozen prefix, version gate and cumulative budget in the terminal resample merge."""
import gzip

import pytest

from posttraining.sft import recollected
from posttraining.sft.export import json_line
from posttraining.sft.history import sha


def test_resample_fingerprint_checked_before_labeling():
    with pytest.raises(ValueError, match="fingerprint"):
        recollected.encode_closing({}, {"model": "teacher", "system_fingerprint": "other"},
                                   {"model": "teacher", "fingerprint": "frozen"}, {}, 0, None)


def test_over_budget_answer_does_not_enter_training(monkeypatch):
    original = {"prompt_ids_sha256": "same", "target_ids_sha256": "old",
                "prompt_tokens": 9000, "target_tokens": 2}
    wire = {"model": "teacher", "fingerprint": "frozen"}
    response = {"model": "teacher", "system_fingerprint": "frozen", "id": "id",
                "choices": [{"message": {"role": "assistant", "content": "completed"}, "finish_reason": "stop"}]}
    monkeypatch.setattr(recollected, "supervise", lambda *args: (
        [], [], {"prompt_ids_sha256": "same", "target_ids_sha256": "new",
                 "masked_prompt_tokens": 9000, "supervised_tokens": 3}))
    with pytest.raises(ValueError, match="budget"):
        recollected.encode_closing({"messages": [], "tools": []}, response, wire, original, 100000, None)


def test_append_preserves_original_index_and_training_lines(tmp_path):
    prior = json_line({"input_ids": [1, 2], "labels": [-100, 2]})
    extra = json_line({"input_ids": [1, 3], "labels": [-100, 3]})
    ix = {"row": 1, "train_row_sha256": sha(prior), "session_id": "s", "task_id": "t"}
    (tmp_path / "index.jsonl").write_bytes(json_line(ix))
    (tmp_path / "train.jsonl.gz").write_bytes(gzip.compress(prior))
    manifest = {"counts": {"masked_prompt_tokens": 1, "supervised_tokens": 1, "supervised_reasoning_tokens": 0},
                "kinds": {"closing": 1}, "files": {}}
    row = {"train_row_sha256": sha(extra), "session_id": "s", "task_id": "other", "masked_prompt_tokens": 1,
           "supervised_tokens": 1, "supervised_reasoning_tokens": 0}
    recollected.append_rows(tmp_path, manifest, [(extra, row)])
    assert gzip.decompress((tmp_path / "train.jsonl.gz").read_bytes()) == prior + extra
    assert (tmp_path / "index.jsonl").read_bytes() == json_line(ix) + json_line({"row": 2, **row})
    assert manifest["counts"] == {"masked_prompt_tokens": 2, "supervised_tokens": 2, "supervised_reasoning_tokens": 0,
                                  "decisions": 2, "scenes": 1, "tasks": 2}
    for name, info in manifest["files"].items():
        assert info["sha256"] == sha((tmp_path / name).read_bytes())


def test_capture_rejects_late_terminal_response():
    reply = {"id": "reply", "model": "teacher", "system_fingerprint": "fp"}
    meta = {"scene_id": "s", "task_id": "t", "turn": 6, "http_status": 200, "original_request_sha256": "request",
            "response_sha256": "response", "response_id": "reply", "model": "teacher", "fingerprint": "fp",
            "remaining_seconds": 10.0, "elapsed_seconds": 10.1}
    with pytest.raises(ValueError, match="time window"):
        recollected.verify_capture(meta, ("s", "t", "model-6"), "request", "response", reply)


def test_continued_request_requires_complete_original_history_and_genuine_bound_receipt():
    call = {"type": "function", "id": "sdk123", "function": {"name": "get_drone_status",
            "arguments": '{"input_json":{"drone_id":"d"}}'}}
    msg = {"role": "assistant", "content": None, "tool_calls": [call], "reasoning_content": "Verify state."}
    reply = {"choices": [{"message": {**msg, "tool_calls": [{"index": 0, **call}]}}]}
    result = {"role": "tool", "tool_call_id": "sdk123", "content": '{"id":"d","status":"hovering"}'}
    receipt = {"messages": [msg, result]}
    original = {"messages": [{"role": "user", "content": "mission"}], "temperature": .1, "tools": []}
    request = {**original, "messages": [*original["messages"], *receipt["messages"]]}
    recollected.verify_query_history(original, reply, receipt, request)
    with pytest.raises(ValueError, match="prefix"):
        recollected.verify_query_history(original, reply, receipt, {**request, "temperature": 0})
    with pytest.raises(ValueError, match="prefix"):
        recollected.verify_query_history(original, reply, receipt, {**request, "messages": [*request["messages"],
                                              {"role": "user", "content": "private outcome"}]})
    with pytest.raises(ValueError, match="actor"):
        recollected.verify_query_history(original, reply, {"messages": [msg, {**result, "content": '{"id":"other"}'}]},
                                        request)
