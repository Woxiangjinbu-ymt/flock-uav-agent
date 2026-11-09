"""Build target-only labels for one public DeepSeek decision with the pinned Qwen3.5 tokenizer."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from student_input import InputRejected, canonical, parse, require, sha
from thinking_events.student import MAX_CONTEXT, MAX_GENERATION

from .adapter import EOS_IDS, load_tokenizer, parse_generation, render
from .verify import project


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
    calls = [_function(c) for c in raw_calls or []]
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


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("samples", type=Path, help="retained development samples JSONL")
    args = cli.parse_args()
    tokenizer = load_tokenizer()
    with args.samples.open() as source:
        for line in source:
            row = parse(line)
            _, _, audit = supervise(tokenizer, row["messages"], row["tools"], row["target"])
            print(json.dumps({"sample_id": row["sample_id"], "tokenization": audit}, ensure_ascii=False))


if __name__ == "__main__":
    main()
