"""Construct and verify target-only training labels for a public teacher turn."""
from copy import deepcopy
import json

from .adapter import EOS_IDS, parse_generation, render
from .history import InputRejected, canonical, parse, require, sha

from ..inference import MAX_CONTEXT, MAX_GENERATION


def project(messages):
    """Independent reference projection. Recover receipt ownership from SDK IDs."""
    history = deepcopy(messages)
    owners = {}
    pending = set()
    for msg in history:
        for call in msg.get("tool_calls", []):
            sdk_id, function = call["id"], call["function"]
            require(sdk_id not in owners, "sft_receipt", "reused SDK call ID")
            args = function["arguments"]
            if isinstance(args, str):
                function["arguments"] = json.loads(args)
            owners[sdk_id] = {"call": f"call_{len(owners) + 1}", **function}
            pending.add(sdk_id)
        if msg["role"] == "tool":
            sdk_id = msg["tool_call_id"]
            require(sdk_id in pending, "sft_receipt", "unknown or duplicate receipt")
            pending.remove(sdk_id)
            payload = json.dumps({**owners[sdk_id], "result": msg["content"]},
                                 sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            msg["content"] = payload.replace("<", r"\u003c").replace(">", r"\u003e")
    require(not pending, "sft_receipt", "missing receipt")
    return history


def _function(call):
    require(isinstance(call, dict) and set(call) <= {"type", "id", "function"}
            and call.get("type") == "function" and isinstance(call.get("function"), dict),
            "sft_call", "Expected a function call")
    function = deepcopy(call["function"])
    require(set(function) == {"name", "arguments"} and isinstance(function["name"], str),
            "sft_call", "Unexpected function fields")
    if isinstance(function["arguments"], str):
        try:
            function["arguments"] = parse(function["arguments"])
        except ValueError as exc:
            raise InputRejected("sft_arguments", "Invalid JSON function arguments") from exc
    require(isinstance(function["arguments"], dict), "sft_arguments", "Expected a JSON argument object")
    return function


def supervise(tokenizer, messages, tools, target):
    """Return unpadded input IDs, causal LM labels and a compact audit suitable for a ledger."""
    allowed = {"role", "content", "reasoning_content", "tool_calls"}
    require(isinstance(target, dict) and target.get("role") == "assistant" and set(target) <= allowed,
            "sft_target", "Expected a single public assistant target")
    require(all(target.get(k) is None or isinstance(target[k], str) for k in ("content", "reasoning_content")),
            "sft_target", "Expected text or null assistant content and reasoning")
    raw_calls = target.get("tool_calls")
    require(raw_calls is None or isinstance(raw_calls, list), "sft_call", "Expected an array of function calls")
    # The wire-format provider includes each call's position. Verify the order
    # before removing this transport field from the native student target.
    calls = []
    for index, call in enumerate(raw_calls or []):
        if isinstance(call, dict) and "index" in call:
            require(type(call["index"]) is int and call["index"] == index,
                    "sft_call_order", "Teacher tool indices differ from their wire order")
            call = {key: value for key, value in call.items() if key != "index"}
        calls.append(_function(call))
    answer = dict(target)
    answer["tool_calls"] = [{"type": "function", "function": f} for f in calls]
    # The same inference prefix teaches both keeping the teacher's thought and closing immediately.
    prompt, prepared = render(tokenizer, messages, tools, thinking=True)
    options = {"tools": tools, "tokenize": False, "enable_thinking": True}
    whole = tokenizer.apply_chat_template([*project(messages), answer], add_generation_prompt=False, **options)
    prefix = prompt["prompt_text"]
    require(whole.startswith(prefix) and whole.endswith(tokenizer.eos_token + "\n"),
            "sft_boundary", "Reference transcript differs from the runtime prefix or terminal marker")
    whole = whole.removesuffix("\n")  # The newline after the turn belongs to the next message.
    suffix = tokenizer.encode(whole[len(prefix):], add_special_tokens=False)
    ids = prompt["input_ids"] + suffix
    require(len(ids) <= MAX_CONTEXT and len(suffix) <= MAX_GENERATION,
            "sft_overlength", "Untruncated example exceeds the supported input or generation span")
    require(suffix and suffix[-1] == tokenizer.eos_token_id and
            tokenizer.decode(ids, clean_up_tokenization_spaces=False, skip_special_tokens=False) == whole,
            "sft_boundary", "Separately encoded input and target do not reconstruct the native transcript")

    parsed = parse_generation(tokenizer, suffix, EOS_IDS, True, prepared, MAX_GENERATION)
    require(not parsed["anomalies"] and parsed["terminated"] and parsed["message"] is not None,
            "sft_decode", f"Native target rejected by runtime parser: {parsed['anomalies']}")
    replay = parsed["message"]
    require(all((target.get(k) or "").strip() == (replay.get(k) or "").strip()
                for k in ("content", "reasoning_content")), "sft_text", "Replayed teacher text differs")
    decoded = [_function(c) for c in replay.get("tool_calls", [])]
    require(len(decoded) == len(calls), "sft_calls", "Different number of replayed calls")
    normalized = []
    for index, (expected, actual) in enumerate(zip(calls, decoded, strict=True)):
        # A schema accepting either object or legacy JSON text has one native spelling.
        # Only use this equivalence for input_json and only when the runtime produced an object.
        original = expected["arguments"].get("input_json")
        if isinstance(original, str) and isinstance(actual["arguments"].get("input_json"), dict):
            try:
                expected["arguments"]["input_json"] = parse(original)
            except ValueError as exc:
                raise InputRejected("sft_arguments", "Invalid legacy input_json") from exc
            normalized.append(index)
        require(canonical(actual) == canonical(expected), "sft_calls", "Replayed function or typed arguments differ")

    labels = [-100] * len(prompt["input_ids"]) + suffix
    return ids, labels, {
        "policy": "retain_available_teacher_reasoning", "masked_prompt_tokens": len(prompt["input_ids"]),
        "supervised_tokens": len(suffix), "supervised_reasoning_tokens": parsed["reasoning_tokens"],
        "historical_reasoning_tokens": prompt["history_reasoning_tokens"],
        "linked_public_receipts": len(prepared["id_sidecar"]), "last_supervised_id": suffix[-1],
        "prompt_ids_sha256": sha(canonical(prompt["input_ids"])), "target_ids_sha256": sha(canonical(suffix)),
        "labels_sha256": sha(canonical(labels)), "roundtrip_tool_names": [c["name"] for c in decoded],
        "legacy_input_json_replayed_as_object_call_indices": normalized,
    }
