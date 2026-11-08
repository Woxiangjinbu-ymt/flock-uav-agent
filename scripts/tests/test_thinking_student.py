"""CPU-only conformance for the student service and its two token-ID adapters."""

from contextlib import nullcontext
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import student_input_v2 as v2
from thinking_events import student, student_process, student_service, student_timing

TOOLS = [{"type": "function", "function": {"name": "observe", "parameters": {"type": "object"}}}]
MESSAGES = [{"role": "user", "content": "Inspect aircraft A and B."}]
CALL = '<tool_call>\n{"name":"observe","arguments":{"actor":"%s"}}\n</tool_call>'
TOKENIZER_PYTHON = Path(os.environ.get("FLOCKCUE_TOKENIZER_PYTHON", ROOT / "training/.venv-input/bin/python"))


@pytest.fixture(scope="module")
def tokenizer():
    return v2.load_tokenizer()


def request(client, **changes):
    args = {"slot": "cpu", "turn": 1, "messages": MESSAGES, "tools": TOOLS, "thinking": True,
            "seed": 77, "spent_tokens": 0, "episode_deadline": time.monotonic() + 25}
    return client.request(**{**args, **changes})


def test_two_turn_open_closed_and_reverse_receipt_ids(tokenizer):
    suffix = "<think>inspect both</think>\n\n" + CALL % "A" + "\n" + CALL % "B" + "<|im_end|>"
    with student.StudentProcess(TOKENIZER_PYTHON, fixture_replies=[suffix, "Complete.<|endoftext|>"]) as client:
        first = request(client)
        assert not client.metadata["weights_loaded"] and client.metadata["backend"] == "fixture"
        assert first["timing"]["generation_seconds"] >= 0
        assert first["timing"]["ttft_seconds"] is first["peak_vram_bytes"] is None
        assert first["timing"]["unavailable"]["decode_tokens_per_second"] == "fixture_emits_complete_sequence"
        assert first["status"] == "ok" and first["seed"] == 77 and first["sampling"] == student.SAMPLING
        assert tokenizer.decode(first["generated_ids"]) == suffix
        assert first["usage"]["completion_tokens_details"]["reasoning_tokens"] == len(tokenizer.encode("inspect both"))
        assert first["usage"]["total_tokens"] == len(first["input_ids"]) + len(first["generated_ids"])
        left, right = first["message"]["tool_calls"]
        history = MESSAGES + [first["message"], {"role": "tool", "tool_call_id": right["id"], "content": "B seen"},
                              {"role": "tool", "tool_call_id": left["id"], "content": "A seen"}]
        second = request(client, turn=2, messages=history, spent_tokens=first["usage"]["total_tokens"])
        assert second["message"]["content"] == "Complete."
        text = tokenizer.decode(second["input_ids"])
        assert text.index("B seen") < text.index("A seen")
        assert '"id": "call_2"' in text and "inspect both" in text
        assert second["history_reasoning_tokens"] == len(tokenizer.encode("inspect both"))
        missing = deepcopy(history)
        missing[-1]["tool_call_id"] = right["id"]
        assert request(client, turn=2, messages=missing)["stop_reason"] == "input_rejected"
        closed = request(client, thinking=False)
        assert closed["input_ids"][:len(first["input_ids"])] == first["input_ids"]
        assert tokenizer.decode(closed["input_ids"][len(first["input_ids"]):]) == "<think>\n\n</think>\n\n"
        assert closed["status"] == "format_error" and closed["message"] is None


@pytest.mark.parametrize("text,thinking,limited,reason", [
    ("<think> </think>ok<|im_end|>", True, False, None),
    ("ordinary<|endoftext|>", True, False, None),
    ("<think>unclosed", True, True, "unclosed_thinking"),
    ("<think>a<think>b</think>", True, True, "malformed_thinking"),
    ("<think>text</think>ok<|im_end|>", False, False, "unexpected_thinking"),
    ("cutoff", False, True, "generation_truncated"),
    (CALL % "A" + "\n<tool_call>{<|im_end|>", False, False, "generated_json"),
    ('<tool_call>{"id":"bad","name":"observe","arguments":{}}</tool_call><|im_end|>', False, False,
     "generated_tool_fields"),
])
def test_complete_generation_boundary(tokenizer, text, thinking, limited, reason):
    ids = tokenizer.encode(text, add_special_tokens=False)
    prepared = v2.prepare_history(MESSAGES, TOOLS)
    result = student.parse_generation(tokenizer, ids, [151645, 151643], thinking, prepared,
                                      len(ids) if limited else 2048)
    if reason:
        assert result["message"] is None and reason in result["anomalies"]
    else:
        assert result["message"]["content"] and result["reasoning_tokens"] == 0


def test_canonical_render_keeps_next_wire_id_unique(tokenizer):
    previous = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_2", "type": "function", "function": {"name": "observe", "arguments": "{}"}}]}
    history = [*MESSAGES, previous, {"role": "tool", "tool_call_id": "call_2", "content": "ok"}]
    _, prepared = student.render(tokenizer, history, TOOLS, True)
    ids = tokenizer.encode((CALL % "A") + "<|im_end|>", add_special_tokens=False)
    reply = student.parse_generation(tokenizer, ids, [151645], True, prepared, 2048)["message"]
    current = reply["tool_calls"][0]["id"]
    assert current != "call_2"
    student.render(tokenizer, [*history, reply, {"role": "tool", "tool_call_id": current, "content": "ok"}], TOOLS, True)


@pytest.mark.parametrize("length,spent,answer", [
    (20, 99980, (0, "token_budget")), (20, 99979, (1, None)),
    (30720, 0, (2048, None)), (30721, 0, (0, "context_length")),
])
def test_budget_boundaries(length, spent, answer):
    assert student.output_budget(length, spent) == answer


def test_deadlines_reap_before_reuse():
    slow = [{"text": "too late<|im_end|>", "delay": 1.2}]
    with student.StudentProcess(TOKENIZER_PYTHON, fixture_replies=slow) as client:
        assert request(client, turn=13)["stop_reason"] == "request_limit"
        assert request(client, episode_deadline=time.monotonic() - 1)["stop_reason"] == "episode_deadline"
        result = request(client, episode_deadline=time.monotonic()+0.1)
        assert result["stop_reason"] == "episode_deadline" and result["usage"] is None
        assert client.process.poll() is not None
        assert request(client, slot="next")["stop_reason"] == "cancelled"
    with student.StudentProcess(TOKENIZER_PYTHON, fixture_replies=slow) as client:
        assert request(client, request_timeout=0.1)["stop_reason"] == "request_timeout"
        assert client.process.poll() is not None


def test_wrong_slot_never_returns_calls(monkeypatch):
    with student.StudentProcess(TOKENIZER_PYTHON, fixture_replies=["ok<|im_end|>"]) as client:
        original = student_process.receive
        def wrong(*args):
            return {**original(*args), "slot": "other"}
        monkeypatch.setattr(student_process, "receive", wrong)
        result = request(client)
        assert result["stop_reason"] == "response_identity_mismatch" and result["message"] is None
        assert client.process.poll() is not None


def test_pinned_local_snapshot_checksum_and_revision(tmp_path):
    names = ["tokenizer_config.json", "config.json", "generation_config.json", "tokenizer.json", "model.safetensors"]
    for name in names:
        (tmp_path/name).write_bytes(name.encode())
    revision = student.MODEL_REVISIONS["4B"]
    files = {n:sha256(n.encode()).hexdigest() for n in names}
    manifest = {"model":"4B", "revision":revision, "files":files}
    assert student_service.verify_model(tmp_path, "4B", revision, manifest)
    with pytest.raises(ValueError, match="revision"):
        student_service.verify_model(tmp_path, "4B", "main", manifest)
    (tmp_path/"model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        student_service.verify_model(tmp_path, "4B", revision, manifest)


def test_vllm_cpu_double_captures_exact_ids_and_sampling(monkeypatch):
    monkeypatch.setitem(sys.modules, "vllm", SimpleNamespace(SamplingParams=lambda **kw: kw))
    engine = object.__new__(student_service.VllmModel)
    calls = []
    def generate(prompts, params, use_tqdm):
        calls.append((prompts, params, use_tqdm))
        return [SimpleNamespace(prompt_token_ids=[1,2], outputs=[SimpleNamespace(token_ids=[3,4])])]
    engine.model = SimpleNamespace(generate=generate)
    generated = engine.generate([1,2], 17, 9, [4])
    assert generated["generated_ids"] == [3,4]
    assert generated["first_token_monotonic"] is generated["timing"]["decode_seconds"] is None
    assert generated["timing"]["unavailable"]["ttft_seconds"] == "vllm_token_timestamps_unavailable"
    assert calls == [([{"prompt_token_ids":[1,2]}], {**student.SAMPLING, "max_tokens":17, "seed":9,
                       "stop_token_ids":[4], "detokenize":False}, False)]


def test_transformers_cpu_double_captures_new_ids_and_sampling():
    engine = object.__new__(student_service.TransformersModel)
    seeds, generations = [], []
    prompt = [[1,2]]
    cuda = SimpleNamespace(reset_peak_memory_stats=lambda: None, max_memory_allocated=lambda: 128,
                           max_memory_reserved=lambda: 256)
    engine.torch = SimpleNamespace(cuda=cuda, manual_seed=seeds.append, tensor=lambda data, **_:data, long=int,
                                   ones_like=lambda _: [[1,1]], inference_mode=nullcontext)
    class Output:
        def __getitem__(self, key):
            assert key == (0, slice(2,None))
            return SimpleNamespace(tolist=lambda:[3,4])
    def generate(**kwargs):
        streamer = kwargs.pop("streamer")
        streamer.put(SimpleNamespace(numel=lambda:2))  # Prompt callback before the generated IDs
        for _ in range(2):
            streamer.put(SimpleNamespace(numel=lambda:1))
        streamer.end()
        generations.append(kwargs)
        return Output()
    engine.model = SimpleNamespace(generate=generate, generation_config=SimpleNamespace(pad_token_id=9))
    measured = engine.generate(prompt[0], 17, 9, [4])
    assert measured["generated_ids"] == [3,4] and measured["peak_vram_bytes"] == 256
    assert measured["peak_allocated_bytes"] == 128 and measured["first_token_monotonic"] > 0
    assert measured["timing"]["decode_tokens_per_second"] > 0
    assert seeds == [9]
    assert generations == [{**student.SAMPLING, "input_ids":prompt, "attention_mask":[[1,1]],
                            "eos_token_id":[4], "pad_token_id":9, "do_sample":True,"max_new_tokens":17}]


def test_time_samples_use_strict_token_and_clock_boundaries():
    assert student_timing.vllm_timestamps(SimpleNamespace(first_token_ts=12, last_token_ts=14), 10, 15) == (12,14)
    assert student_timing.vllm_timestamps(SimpleNamespace(first_token_ts=12, last_token_ts=9), 10, 15) == (None,None)
    result = student_timing.generation_stats([3,4,5], 10, 15, source="test", first=12, last=14)
    assert result["timing"]["decode_seconds"] == 2 and result["timing"]["decode_tokens_per_second"] == 1
    for start, end, first, last in [(10,9,None,None), (10,15,9,14), (10,15,14,11)]:
        with pytest.raises(ValueError):
            student_timing.generation_stats([3,4], start, end, source="test", first=first, last=last)
    one = student_timing.generation_stats([3],10,15,source="test",first=12,last=12)
    assert one["timing"]["unavailable"]["decode_seconds"] == "fewer_than_two_tokens"


def test_extra_private_fields_rejected_before_pipe_write(monkeypatch):
    with student.StudentProcess(TOKENIZER_PYTHON, fixture_replies=["ok<|im_end|>"]) as client:
        def forbid(*args):
            pytest.fail("non-public message crossed the model pipe")
        monkeypatch.setattr(student_process, "send", forbid)
        assert request(client, messages=[{**MESSAGES[0], "checker": "private"}])["error"] == "nonpublic_request_fields"
        assert request(client, tools=[{**TOOLS[0], "trial_state": {}}])["error"] == "nonpublic_request_fields"
