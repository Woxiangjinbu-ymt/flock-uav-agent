"""Render public Qwen3.5 histories and decode complete generated function-call groups."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import student_input_v2 as v2

REVISION = "15852e8c16360a2fea060d615a32b45270f8a8fc"
DEFAULT_CACHE = Path.home() / ".cache/flockcue/Qwen3.5-2B" / REVISION
FILES = {
    "chat_template.jinja": "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80",
    "config.json": "ed1c1723241f23f7f4e23430759cbd7dcfb4103cbdfe052bfe7626b57c2615b4",
    "tokenizer.json": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
    "tokenizer_config.json": "49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c",
}
# Native chat turn boundary and the pinned text model's EOS, respectively.
EOS_IDS = frozenset({248046, 248044})
CONTROL = re.compile(r"<\||<tts_")
NATIVE_TAG = re.compile(r"</?(?:tool_call|function|parameter|tool_response|think)\b", re.I)
FUNCTION = re.compile(r"<function=([A-Za-z0-9_-]+)>\s*")
PARAMETER = re.compile(r"<parameter=([A-Za-z0-9_-]+)>")
HISTORY_THOUGHT = re.compile(r"<\|im_start\|>assistant\n<think>\n(.*?)\n</think>", re.S)
JSON_TYPES = {str: "string", dict: "object", list: "array", bool: "boolean", int: "integer",
              float: "number", type(None): "null"}


def load_tokenizer(cache=None):
    """Use only the four pinned local files. No downloads or model weights."""
    from transformers import AutoTokenizer
    from tempfile import TemporaryDirectory

    cache = DEFAULT_CACHE if cache is None else Path(cache)
    # AutoTokenizer can also read optional tokenizer files from its directory.
    # Only expose the verified data to its loader.
    with TemporaryDirectory() as tmp:
        for name, expected in FILES.items():
            raw = (cache / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(f"Qwen3.5-2B digest mismatch for {name}")
            (Path(tmp) / name).write_bytes(raw)
        tokenizer = AutoTokenizer.from_pretrained(tmp, local_files_only=True, trust_remote_code=False)
        if not tokenizer.is_fast or tokenizer.chat_template != (Path(tmp) / "chat_template.jinja").read_text():
            raise ValueError("Qwen3.5-2B native tokenizer/template mismatch")
    return tokenizer


def render(tokenizer, messages, tools, thinking):
    """Resolve public receipts by ID before the native template drops those IDs."""
    history = deepcopy(messages)
    reasoning = {}
    for index, msg in enumerate(history):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            body = msg.pop("reasoning_content", None)
            if body is not None:
                v2.require(isinstance(body, str) and not CONTROL.search(body)
                           and "<think>" not in body and "</think>" not in body,
                           "history_reasoning", "Invalid historical reasoning")
                reasoning[index] = body
    prepared = v2.prepare_history(history, tools)
    formatted = deepcopy(prepared["messages"])
    functions = {c["id"]: c["function"] for m in formatted for c in m.get("tool_calls", [])}
    for func in functions.values():
        arguments = json.dumps(func["arguments"], ensure_ascii=False)
        v2.require(not NATIVE_TAG.search(arguments) and not CONTROL.search(arguments),
                   "history_arguments", "Reserved delimiters in a historical function argument")
    for index, msg in enumerate(formatted):
        v2.require(not CONTROL.search(msg["content"]), "history_control", "Reserved Qwen control marker")
        if index in reasoning:
            msg["reasoning_content"] = reasoning[index]
    for link in prepared["id_sidecar"]:
        receipt = formatted[link["receipt_message_index"]]
        call_id = link["canonical_id"]
        body = {"call": call_id, **functions[call_id], "result": receipt["content"]}
        # Literal tool-response markup in a result remains public data inside JSON.
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        receipt["content"] = payload.replace("<", r"\u003c").replace(">", r"\u003e")
    text = tokenizer.apply_chat_template(formatted, tools=prepared["tools"], tokenize=False,
                                         add_generation_prompt=True, enable_thinking=thinking)
    encoded = tokenizer(text, add_special_tokens=False, truncation=False, return_offsets_mapping=True)
    spans = [m.span(1) for m in HISTORY_THOUGHT.finditer(text) if m.group(1).strip()]
    count = sum(any(start < right and stop > left for left, right in spans)
                for start, stop in encoded["offset_mapping"])
    return {"input_ids": list(encoded["input_ids"]), "prompt_text": text,
            "history_reasoning_tokens": count, "template_sha256": FILES["chat_template.jinja"]}, prepared


def _argument(name, raw, schema):
    """Recover the advertised parameter type from the template's text representation."""
    # Remove only the newlines inserted by the native template.
    raw = raw.removeprefix("\n").removesuffix("\n")
    variants = schema.get("anyOf", [schema])
    types = {variant.get("type") for variant in variants}
    if name == "input_json" and types <= {"string", "object"}:
        # Harness envelopes always carry a JSON object, including legacy strings.
        parsed = _strict_json(raw)
        v2.require(isinstance(parsed, dict), "tool_arguments", "input_json must encode an object")
        decoded = json.dumps(parsed, ensure_ascii=False)
        v2.require(not NATIVE_TAG.search(decoded) and not CONTROL.search(decoded), "tool_arguments",
                   "Control markup in decoded input_json")
        return parsed if "object" in types else raw
    if types == {"string"}:
        value = raw
    else:
        normalized = {"True": "true", "False": "false", "None": "null"}.get(raw, raw)
        try:
            value = _strict_json(normalized)
        except v2.InputRejected:
            if "string" not in types:
                raise
            value = raw
        actual = JSON_TYPES[type(value)]
        if actual not in types and not (actual == "integer" and "number" in types):
            v2.require("string" in types, "tool_arguments", "Parameter type differs from tool schema")
            value = raw
    if "enum" in schema:
        v2.require(any(type(value) is type(item) and value == item for item in schema["enum"]),
                   "tool_arguments", "Parameter outside advertised choices")
    return value


def _strict_json(raw):
    try:
        value = v2.base.parse(raw)
        # Covers float overflow, deep recursion and unpaired Unicode surrogates, too.
        v2.canonical(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise v2.InputRejected("tool_arguments", "Expected a strict finite JSON parameter") from exc
    return value


def _decision(text, tools):
    """Parse the complete answer, then let the caller attach all calls in one step."""
    prefix, opening, tail = text.partition("<tool_call>")
    v2.require(not NATIVE_TAG.search(prefix), "generated_format", "Stray tool/thinking delimiter")
    decision = {"role": "assistant", "content": prefix.strip()}
    if not opening:
        return decision
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    remaining = opening + tail
    calls = []
    while remaining:
        v2.require(remaining.startswith("<tool_call>"), "generated_format", "Unexpected suffix after call")
        remaining = remaining[len("<tool_call>"):].lstrip()
        function = FUNCTION.match(remaining)
        v2.require(function, "generated_format", "Expected a function block")
        name = function.group(1)
        v2.require(name in schemas, "tool_calls", "Unadvertised function")
        schema = schemas[name]
        props = schema.get("properties", {})
        args = {}
        remaining = remaining[function.end():]
        while parameter := PARAMETER.match(remaining):
            key = parameter.group(1)
            v2.require(key in props and key not in args, "tool_arguments", "Unknown or duplicate parameter")
            raw, close, tail = remaining[parameter.end():].partition("</parameter>")
            v2.require(close and not NATIVE_TAG.search(raw), "generated_format", "Malformed parameter block")
            args[key] = _argument(key, raw, props[key])
            remaining = tail.lstrip()
        v2.require(remaining.startswith("</function>"), "generated_format", "Unclosed function")
        remaining = remaining[len("</function>"):].lstrip()
        v2.require(remaining.startswith("</tool_call>"), "generated_format", "Unclosed tool call")
        remaining = remaining[len("</tool_call>"):].lstrip()
        v2.require(set(schema.get("required", [])) <= args.keys(), "tool_arguments", "Missing required parameter")
        calls.append({"type": "function", "function": {"name": name, "arguments": args}})
    decision["tool_calls"] = calls
    return decision


def parse_generation(tokenizer, generated, eos_ids, thinking, prepared, limit):
    """Parse the unpadded, generated-only suffix as one indivisible message.

    Callers must remove batch padding using generation lengths before this call.
    ID 248044 can be either a real model EOS or padding; IDs alone cannot distinguish them.
    """
    ids = list(generated)
    terminated = bool(ids and ids[-1] in EOS_IDS and ids[-1] in eos_ids)
    result = {"message": None, "anomalies": [], "reasoning_tokens": None, "terminated": terminated}
    try:
        v2.require(len(ids) <= limit, "generation_limit", "Generated sequence exceeds its limit")
        v2.require(terminated, "generation_truncated" if len(ids) == limit else "missing_eos",
                   "Generation needs a real terminal token")
        body = ids[:-1]
        v2.require(not any(i in EOS_IDS for i in body), "interior_eos", "Internal end token")
        vocabulary_size = len(tokenizer)
        v2.require(all(type(i) is int and 0 <= i < vocabulary_size for i in ids),
                   "generated_token", "Invalid tokenizer ID")

        def decode(tokens):
            return tokenizer.decode(tokens, skip_special_tokens=False, clean_up_tokenization_spaces=False)

        answer = decode(body)
        v2.require(not CONTROL.search(answer), "generated_format", "Reserved control token")
        thought = None
        result["reasoning_tokens"] = 0
        if thinking:
            close = tokenizer.convert_tokens_to_ids("</think>")
            v2.require(body.count(close) == 1 and answer.count("</think>") == 1 and "<think>" not in answer,
                       "malformed_thinking", "Generation must close the prompt's open think block exactly once")
            index = body.index(close)
            thought = decode(body[:index])
            result["reasoning_tokens"] = index if thought.strip() else 0
            answer = decode(body[index + 1:]).lstrip("\n")
        else:
            v2.require("<think>" not in answer and "</think>" not in answer,
                       "unexpected_thinking", "Thinking-off output contains a thought delimiter")
        decision = _decision(answer, prepared["tools"])
        message = v2.attach_generated_decision(prepared, decision)["messages"][-1]
        used = {link["original_id"] for link in prepared["id_sidecar"]}
        index = prepared["next_call_index"]
        for call in message.get("tool_calls", []):
            while f"call_{index}" in used:
                index += 1
            call["id"] = f"call_{index}"
            index += 1
            function = call["function"]
            function["arguments"] = json.dumps(function["arguments"], ensure_ascii=False,
                                               allow_nan=False, separators=(",", ":"))
        if thought is not None:
            message["reasoning_content"] = thought
        result["message"] = message
    except v2.InputRejected as error:
        result["anomalies"].append(error.code)
    except RecursionError:
        result["anomalies"].append("tool_arguments")
    return result
