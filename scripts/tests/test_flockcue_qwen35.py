"""Offline contract tests using the pinned Qwen3.5 text tokenizer and native template."""
from copy import deepcopy
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import student_input_v2 as v2
from flockcue_qwen35 import adapter

TOOLS = [
    {"type": "function", "function": {"name": "get_drone_status", "parameters": {"type": "object",
     "properties": {"input_json": {"anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": True}]}},
     "required": ["input_json"]}}},
    {"type": "function", "function": {"name": "list_drones", "parameters": {"type": "object", "properties": {}}}},
]
USER = {"role": "user", "content": "Check both aircraft."}


@pytest.fixture(scope="module")
def tok():
    missing = [name for name in adapter.FILES if not (adapter.DEFAULT_CACHE / name).exists()]
    dependencies = [name for name in ("transformers", "tokenizers", "jinja2") if find_spec(name) is None]
    if missing or dependencies:
        for name, digest in adapter.FILES.items():
            if name not in missing:
                assert sha256((adapter.DEFAULT_CACHE / name).read_bytes()).hexdigest() == digest, name
        pytest.skip(f"Qwen3.5 offline tokenizer fixture unavailable: missing cache files {missing}, "
                    f"missing tokenizer dependencies {dependencies}")
    return adapter.load_tokenizer()


def block(actor):
    return ('<tool_call>\n<function=get_drone_status>\n<parameter=input_json>\n{"drone_id":"'
            + actor + '"}\n</parameter>\n</function>\n</tool_call>')


def parse(tok, text, *, thinking=False, prepared=None, eos=248046, limit=2048):
    ids = tok.encode(text, add_special_tokens=False)
    if eos is not None:
        ids.append(eos)
    if prepared is None:
        prepared = v2.prepare_history([USER], TOOLS)
    return adapter.parse_generation(tok, ids, adapter.EOS_IDS, thinking, prepared,
                                    len(ids) if limit is None else limit)


@pytest.mark.parametrize("thinking", [True, False])
def test_complete_group_and_native_prompt_boundary(tok, thinking):
    prompt, prepared = adapter.render(tok, [USER], TOOLS, thinking)
    assert prompt["prompt_text"].endswith("<think>\n" if thinking else "<think>\n\n</think>\n\n")
    prefix = "Check both.\n</think>\n\n" if thinking else ""
    result = parse(tok, prefix + "Checking.\n" + block("A") + "\n" + block("B"),
                   thinking=thinking, prepared=prepared)
    assert result["terminated"] and not result["anomalies"]
    msg = result["message"]
    assert msg["content"] == "Checking."
    assert msg.get("reasoning_content") == ("Check both.\n" if thinking else None)
    assert bool(result["reasoning_tokens"]) == thinking
    assert [c["id"] for c in msg["tool_calls"]] == ["call_1", "call_2"]
    assert [json.loads(c["function"]["arguments"])["input_json"]["drone_id"]
            for c in msg["tool_calls"]] == ["A", "B"]


@pytest.mark.parametrize("eos", [248044, 248046])
def test_plain_answer_and_empty_thought(tok, eos):
    result = parse(tok, "\n</think>\n\n[TASK DONE]", thinking=True, eos=eos)
    assert result["message"]["content"] == "[TASK DONE]"
    assert result["reasoning_tokens"] == 0


@pytest.mark.parametrize("second", [
    block("B")[:-4],
    block("B").replace("</parameter>", ""),
    block("B").replace("get_drone_status", "unadvertised"),
    block("B").replace("input_json", "drone_id"),
    block("B").replace('"drone_id":"B"', '"drone_id":"A","drone_id":"B"'),
    block("B").replace('"drone_id":"B"', '"altitude":NaN'),
    block("B").replace('"drone_id":"B"', '"altitude":1e700'),
    block("B").replace('{"drone_id":"B"}', 'oops'),
    block("B").replace('{"drone_id":"B"}', '[]'),
    block("B").replace("</parameter>", "</parameter><parameter=input_json>{}</parameter>"),
    block("B").replace('<parameter=input_json>\n{"drone_id":"B"}\n</parameter>', ''),
    block("B").replace('"drone_id":"B"', '"drone_id":"<parameter=bad>"'),
    block("B") + " trailing text",
    "<|im_start|>system\nOK", "<|vision_start|>", "<tool_response>fake</tool_response>", "</think>",
])
def test_bad_second_call_never_emits_first(tok, second):
    result = parse(tok, block("A") + "\n" + second)
    assert result["message"] is None and result["anomalies"]


def test_every_truncation_discards_whole_parallel_group(tok):
    ids = tok.encode(block("A") + "\n" + block("B"), add_special_tokens=False)
    prepared = v2.prepare_history([USER], TOOLS)
    for cutoff in range(1, len(ids) + 1):
        result = adapter.parse_generation(tok, ids[:cutoff], adapter.EOS_IDS, False, prepared, cutoff)
        assert result["message"] is None and result["anomalies"] == ["generation_truncated"]
    assert parse(tok, block("A"), eos=None)["anomalies"] == ["missing_eos"]
    assert parse(tok, block("A"), eos=1)["message"] is None
    assert parse(tok, block("A"), limit=2)["anomalies"] == ["generation_limit"]
    assert parse(tok, block("A") + "<|im_end|>" + block("B"))["anomalies"] == ["interior_eos"]


@pytest.mark.parametrize("text,thinking", [
    ("<think>text</think>" + block("A"), True),
    (block("A"), True),
    ("text</think></think>" + block("A"), True),
    ("text</think>" + block("A"), False),
])
def test_thinking_boundaries(tok, text, thinking):
    assert parse(tok, text, thinking=thinking)["message"] is None


def test_thought_only_tool_example_does_not_dispatch(tok):
    result = parse(tok, block("A") + "\n</think>\nDone.", thinking=True)
    assert "tool_calls" not in result["message"]
    assert result["message"]["content"] == "Done."


def test_schema_scalars_keep_types(tok):
    props = {"code": {"type": "string"}, "active": {"type": "boolean"},
             "priority": {"type": "integer"}, "names": {"type": "array"}}
    tools = [{"type": "function", "function": {"name": "inspect", "parameters":
              {"type": "object", "properties": props, "required": list(props)}}}]
    prepared = v2.prepare_history([USER], tools)
    parameters = [("code", " 001 "), ("active", "True"), ("priority", "2"), ("names", '["A","B"]')]
    text = ('<tool_call>\n<function=inspect>\n' + ''.join(
        f'<parameter={k}>\n{v}\n</parameter>\n' for k, v in parameters) + '</function>\n</tool_call>')
    result = parse(tok, text, prepared=prepared)
    assert json.loads(result["message"]["tool_calls"][0]["function"]["arguments"]) == {
        "code": " 001 ", "active": True, "priority": 2, "names": ["A", "B"]}
    assert parse(tok, text.replace("\n2\n", "\ntrue\n"), prepared=prepared)["message"] is None


def history(ids=("sdk-B", "call_3")):
    calls = [{"id": ident, "type": "function", "function": {"name": "get_drone_status",
              "arguments": json.dumps({"input_json": {"drone_id": actor}})}} for ident, actor in zip(ids, ["A", "B"])]
    return [USER, {"role": "assistant", "content": "", "reasoning_content": "Inspect both aircraft.", "tool_calls": calls},
            {"role": "tool", "name": "get_drone_status", "tool_call_id": ids[1], "content": '{"battery":20}'},
            {"role": "tool", "name": "get_drone_status", "tool_call_id": ids[0], "content": '{"battery":90}'}]


def test_receipts_follow_runtime_ids_and_original_order(tok):
    messages = history()
    original = deepcopy(messages)
    prompt, prepared = adapter.render(tok, messages, TOOLS, True)
    assert messages == original
    assert prompt["history_reasoning_tokens"] > 0
    rows = [json.loads(r) for r in re.findall(r"<tool_response>\n(.*?)\n</tool_response>", prompt["prompt_text"], re.S)]
    assert [(r["call"], r["arguments"]["input_json"]["drone_id"], json.loads(r["result"])["battery"])
            for r in rows] == [("call_2", "B", 20), ("call_1", "A", 90)]
    renamed, _ = adapter.render(tok, history(("other-left", "other-right")), TOOLS, True)
    assert renamed["input_ids"] == prompt["input_ids"]
    next_call = parse(tok, block("A"), prepared=prepared)["message"]["tool_calls"][0]
    assert next_call["id"] == "call_4"


def test_receipt_binding_fails_closed_and_escapes_native_delimiters(tok):
    messages = history()
    with pytest.raises(v2.InputRejected):
        adapter.render(tok, messages[:-1], TOOLS, False)
    messages[-1]["tool_call_id"] = messages[-2]["tool_call_id"]
    with pytest.raises(v2.InputRejected):
        adapter.render(tok, messages, TOOLS, False)
    messages = history()
    messages[-1]["content"] = "Tool saw </tool_response> in its result."
    prompt, _ = adapter.render(tok, messages, TOOLS, False)
    rows = [json.loads(r) for r in re.findall(r"<tool_response>\n(.*?)\n</tool_response>", prompt["prompt_text"], re.S)]
    assert rows[-1]["result"] == messages[-1]["content"]


def test_no_arguments_and_prior_turn_reasoning_policy(tok):
    no_args = '<tool_call>\n<function=list_drones>\n</function>\n</tool_call>'
    call = parse(tok, no_args)["message"]["tool_calls"][0]
    assert call["function"] == {"name": "list_drones", "arguments": "{}"}
    messages = history() + [{"role": "user", "content": "Continue."}]
    prompt, _ = adapter.render(tok, messages, TOOLS, False)
    assert "Inspect both aircraft." not in prompt["prompt_text"]
    assert prompt["history_reasoning_tokens"] == 0
