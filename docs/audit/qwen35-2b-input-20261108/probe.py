"""Probe the pinned native text template; no model weights or model service."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

import jinja2
import tokenizers
import transformers
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import student_input_v2 as v2

MODEL = "Qwen/Qwen3.5-2B"
REVISION = "15852e8c16360a2fea060d615a32b45270f8a8fc"
DEFAULT_CACHE = Path.home() / ".cache/flockcue/Qwen3.5-2B" / REVISION
FILES = {
    "chat_template.jinja": "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80",
    "config.json": "ed1c1723241f23f7f4e23430759cbd7dcfb4103cbdfe052bfe7626b57c2615b4",
    "tokenizer.json": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
    "tokenizer_config.json": "49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c",
}
TOOLS = [{"type": "function", "function": {"name": "get_drone_status",
          "parameters": {"type": "object", "properties": {"drone_id": {"type": "string"}},
                         "required": ["drone_id"]}}}]


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_files(cache, fetch):
    records = {}
    for name, expected in FILES.items():
        path = cache / name
        if not path.exists() and fetch:
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cache) as tmp:
                partial = Path(tmp) / name
                url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}"
                subprocess.run(["curl", "-fLsS", "--max-time", "120", "--retry", "2",
                                url, "-o", str(partial)], check=True)
                require(sha(partial.read_bytes()) == expected, f"Downloaded digest differs for {name}")
                partial.replace(path)
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}; first run probe.py --fetch")
        raw = path.read_bytes()
        require(sha(raw) == expected, f"Cached digest differs for {name}")
        records[name] = {"bytes": len(raw), "sha256": expected}
    return records


def load_tokenizer(cache=DEFAULT_CACHE):
    """Load only from the local directory after checking all four pinned digests."""
    cache = Path(cache)
    checked_files(cache, False)
    tok = AutoTokenizer.from_pretrained(cache, local_files_only=True, trust_remote_code=False)
    require(tok.is_fast and tok.chat_template == (cache / "chat_template.jinja").read_text(),
            "Loaded tokenizer or native template differs")
    return tok


def turns():
    calls = [{"id": call_id, "type": "function",
              "function": {"name": "get_drone_status", "arguments": {"drone_id": aircraft}}}
             for call_id, aircraft in [("sdk-left", "D1"), ("sdk-right", "D2")]]
    return [
        {"role": "user", "content": "Check readiness."},
        {"role": "assistant", "content": "Ready.", "reasoning_content": "EARLIER_REASON_SENTINEL"},
        {"role": "user", "content": "Compare the battery levels."},
        {"role": "assistant", "content": "", "tool_calls": calls,
         "reasoning_content": "CURRENT_REASON_SENTINEL"},
        {"role": "tool", "tool_call_id": "sdk-right", "name": "get_drone_status", "content": '{"battery":20}'},
        {"role": "tool", "tool_call_id": "sdk-left", "name": "get_drone_status", "content": '{"battery":90}'},
    ]


def legacy_parser_diagnostics(tok):
    """Exercise the already-frozen Qwen3 adapter on native Qwen3.5 suffixes."""
    from thinking_events.student import parse_generation
    prepared = v2.prepare_history([{"role": "user", "content": "Inspect."}], TOOLS)
    call = ("<tool_call>\n<function=get_drone_status>\n<parameter=drone_id>\nD1\n</parameter>"
            "\n</function>\n</tool_call>")
    outcomes = {}
    for mode, prefix in (("off", ""), ("on", "Inspect before flying.\n</think>\n\n")):
        ids = tok.encode(prefix + call, add_special_tokens=False) + [tok.eos_token_id]
        result = parse_generation(tok, ids, {tok.eos_token_id}, mode == "on", prepared, 2048)
        outcomes[mode] = result["anomalies"]
    require(outcomes == {"off": ["generated_json"], "on": ["malformed_thinking"]},
            "Frozen parser diagnostics changed")
    return outcomes


def link_receipts(messages, tools):
    """Candidate text-only projection; change only the contents of past public receipts."""
    history = deepcopy(messages)
    for msg in history:
        if msg["role"] == "assistant":
            msg.pop("reasoning_content", None)
    prepared = v2.prepare_history(history, tools)
    functions = {c["id"]: c["function"] for msg in prepared["messages"] for c in msg.get("tool_calls", [])}
    projected = deepcopy(messages)
    for link in prepared["id_sidecar"]:
        receipt = projected[link["receipt_message_index"]]
        canonical_id = link["canonical_id"]
        body = {"call": canonical_id, **functions[canonical_id], "result": receipt["content"]}
        receipt["content"] = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return projected


def receipt_projection_diagnostics(render, original, swapped):
    linked = link_receipts(original, TOOLS)
    text, ids = render(linked, enable_thinking=True)
    _, swapped_ids = render(link_receipts(swapped, TOOLS), enable_thinking=True)
    require(ids != swapped_ids, "Projected histories still collide")
    owners = [json.loads(msg["content"]) for msg in linked if msg["role"] == "tool"]
    require([(o["call"], o["arguments"]["drone_id"], o["result"]) for o in owners] ==
            [("call_2", "D2", '{"battery":20}'), ("call_1", "D1", '{"battery":90}')],
            "Receipt owners or arrival order changed")
    renamed = deepcopy(original)
    rename = {"sdk-left": "other-908", "sdk-right": "other-104"}
    for message in renamed:
        for call in message.get("tool_calls", []):
            call["id"] = rename[call["id"]]
        if message["role"] == "tool":
            message["tool_call_id"] = rename[message["tool_call_id"]]
    require(render(link_receipts(renamed, TOOLS), enable_thinking=True) == (text, ids) and
            all(tag not in text for tag in rename), "Vendor ID changed prompt or leaked to model")
    duplicate = deepcopy(original)
    duplicate[-1]["tool_call_id"] = duplicate[-2]["tool_call_id"]
    failures = {}
    for name, invalid in (("duplicate", duplicate), ("missing", original[:-1])):
        try:
            link_receipts(invalid, TOOLS)
        except v2.InputRejected as error:
            failures[name] = error.code
    require(failures == {"duplicate": "tool_id_mapping", "missing": "tool_receipt_missing"},
            "Malformed input linkage was accepted or misclassified")
    return {"scope": "history-only candidate projection on synthetic public observations",
            "receipt_order": [o["call"] for o in owners], "tokens_with_thinking_on": len(ids),
            "swapping_owners_changes_token_ids": True, "renaming_vendor_ids_changes_token_ids": False,
            "invalid_histories_rejected": failures}


def probe(cache, fetch):
    records = checked_files(cache, fetch)
    tok = load_tokenizer(cache)
    native_backend = tokenizers.Tokenizer.from_file(str(cache / "tokenizer.json"))
    template = (cache / "chat_template.jinja").read_text()
    messages = turns()

    def render(history, **kwargs):
        options = dict(tools=TOOLS, add_generation_prompt=True, **kwargs)
        text = tok.apply_chat_template(history, tokenize=False, **options)
        ids = tok.apply_chat_template(history, tokenize=True, **options)
        require(ids == tok.encode(text, add_special_tokens=False), "Tokenization paths disagree")
        require(ids == native_backend.encode(text, add_special_tokens=False).ids,
                "Transformers tokens differ from the pinned tokenizer.json")
        return text, ids

    variants = {"default": render(messages), "off": render(messages, enable_thinking=False),
                "on": render(messages, enable_thinking=True)}
    require(variants["default"] == variants["off"], "Default differs from explicit off")
    off, off_ids = variants["off"]
    on, on_ids = variants["on"]
    start = "<|im_start|>assistant\n"
    require(on.endswith(start + "<think>\n") and off.endswith(start + "<think>\n\n</think>\n\n")
            and on_ids != off_ids, "Unexpected generation prefixes")
    require(all("CURRENT_REASON_SENTINEL" in s and "EARLIER_REASON_SENTINEL" not in s
                for s, _ in variants.values()), "Historical reasoning scope changed")
    for setting in (True, False):
        require(render(messages, enable_thinking=True, preserve_thinking=setting) == variants["on"],
                "preserve_thinking changed this fixture")

    # Construct a second *valid* history with the same ordered payloads but different owners.
    swapped = deepcopy(messages)
    swapped[-2]["tool_call_id"], swapped[-1]["tool_call_id"] = "sdk-left", "sdk-right"
    require(render(swapped, enable_thinking=True) == variants["on"], "Expected native ID collision is absent")
    require(all(call not in on for call in ("sdk-left", "sdk-right")), "Native template now renders IDs")
    call_text = on.split("CURRENT_REASON_SENTINEL", 1)[1]
    require(call_text.count("<function=get_drone_status>") == 2 and
            "<parameter=drone_id>\nD1\n</parameter>" in call_text and
            call_text.index('{"battery":20}') < call_text.index('{"battery":90}'), "Tool text layout changed")
    config = json.loads((cache / "config.json").read_bytes())
    markers = {s: tok.convert_tokens_to_ids(s) for s in
               ("<think>", "</think>", "<|im_start|>", "<|im_end|>", "<|endoftext|>")}
    require(all(tok.encode(s, add_special_tokens=False) == [i] for s, i in markers.items()),
            "Expected atomic markers differ")
    return {
        "scope": "Mac text-only tokenizer and native template on a synthetic fixture",
        "model": MODEL, "revision": REVISION,
        "source": f"https://huggingface.co/{MODEL}/tree/{REVISION}", "files": records,
        "runtime": {"python": platform.python_version(), "transformers": transformers.__version__,
                    "tokenizers": tokenizers.__version__, "jinja2": jinja2.__version__,
                    "tokenizer_class": type(tok).__name__},
        "default_equals_off": True, "on_opens_reasoning_in_prompt": True, "off_closes_reasoning_in_prompt": True,
        "keep_reasoning_after_latest_user": True, "keep_reasoning_before_latest_user": False,
        "preserve_thinking_true_false_changes_fixture": False, "preserve_thinking_referenced_in_template":
            "preserve_thinking" in template,
        "native_tool_format": "function and parameter tags", "native_tool_call_ids_visible": False,
        "reply_payload_order_retained": True, "reassigning_two_same_name_replies_gives_identical_ids": True,
        "special_token_ids": markers, "tokenizer_eos_id": tok.eos_token_id,
        "config_text_eos_id": config["text_config"]["eos_token_id"],
        "frozen_qwen3_parser_anomalies_on_native_scripted_output": legacy_parser_diagnostics(tok),
        "candidate_receipt_projection": receipt_projection_diagnostics(render, messages, swapped),
        "fixture_inputs": {mode: {"tokens": len(ids), "token_ids_sha256": sha(json.dumps(
            ids, separators=(",", ":")).encode())} for mode, (_, ids) in variants.items()},
        "model_weight_files_downloaded": 0, "model_generations": 0, "paid_teacher_calls": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--fetch", action="store_true", help="Fetch only missing files from the pinned revision")
    args = parser.parse_args()
    print(json.dumps(probe(args.cache.resolve(), args.fetch), indent=2, ensure_ascii=False))
