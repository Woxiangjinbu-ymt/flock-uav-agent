"""Replay the frozen SFT example through the runtime parser without dispatching tools."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
INPUT_AUDIT = ROOT / "docs/audit/qwen35-2b-input-20261108"
sys.path.insert(0, str(INPUT_AUDIT))
sys.path.insert(0, str(ROOT / "scripts"))

from dev_example import load_example
from flockcue_qwen35.adapter import load_tokenizer, parse_generation, render


def ids_digest(ids):
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()


def replay():
    tokens = load_tokenizer()
    history, tools, target, evidence = load_example()
    frozen = json.loads((INPUT_AUDIT / "sample_mask.json").read_text())
    if evidence["target"]["target_sha256"] != frozen["target_sha256"]:
        raise ValueError("Teacher target differs from the archived example")
    results = {}
    for variant, thinking, keep in (
        ("keep_linked_history", True, True),
        ("skip_same_prompt", True, False),
        ("skip_prefilled", False, False),
    ):
        prompt, prepared = render(tokens, history, tools, thinking)
        # Construct the supervised suffix independently with the official template.
        # Earlier messages cannot change how the assistant formats its two tool calls.
        example_prompt = [{"role": "user", "content": "Replay."}]
        answer = deepcopy(target)
        if not keep:
            del answer["reasoning_content"]
        options = {"tokenize": False, "enable_thinking": thinking, "tools": tools}
        prefix = tokens.apply_chat_template(example_prompt, add_generation_prompt=True, **options)
        thread = tokens.apply_chat_template([*example_prompt, answer], add_generation_prompt=False, **options)
        if not thread.startswith(prefix) or not thread.endswith(tokens.eos_token + "\n"):
            raise ValueError("Official template changed the generation boundary")
        suffix = tokens.encode(thread[len(prefix):-1], add_special_tokens=False)
        if ids_digest(suffix) != frozen["variants"][variant]["target_ids_sha256"]:
            raise ValueError(f"{variant}: generated IDs differ from the frozen training target")
        if thinking and ids_digest(prompt["input_ids"]) != frozen["variants"]["keep_linked_history"]["prompt_ids_sha256"]:
            raise ValueError(f"{variant}: runtime input differs from the frozen linked-receipt input")
        for end in range(1, len(suffix)):
            partial = parse_generation(tokens, suffix[:end], {tokens.eos_token_id}, thinking, prepared, end)
            if partial["message"] is not None or partial["anomalies"] != ["generation_truncated"]:
                raise ValueError(f"{variant}: accepted a target cut after token {end}")
        decoded = parse_generation(tokens, suffix, {tokens.eos_token_id}, thinking, prepared, 2048)
        if decoded["anomalies"] or not decoded["terminated"] or not decoded["message"]:
            raise ValueError(f"{variant}: parser rejected the training target: {decoded['anomalies']}")
        message = decoded["message"]
        calls = [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}
                 for c in message.get("tool_calls", [])]
        if calls != [c["function"] for c in target["tool_calls"]]:
            raise ValueError(f"{variant}: tools, order or typed arguments changed")
        if ((message.get("reasoning_content") or "").strip() != (answer.get("reasoning_content") or "").strip()
                or (message.get("content") or "").strip() != (answer.get("content") or "").strip()):
            raise ValueError(f"{variant}: reasoning or public assistant text changed")
        results[variant] = {"target_tokens": len(suffix), "truncated_prefixes_rejected": len(suffix) - 1,
                            "tool_names": [c["name"] for c in calls]}
    return results


if __name__ == "__main__":
    print(json.dumps(replay(), indent=2))
