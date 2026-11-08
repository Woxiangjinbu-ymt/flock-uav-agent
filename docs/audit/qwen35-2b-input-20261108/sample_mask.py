"""Check the supervised token span of one authenticated development decision."""
from copy import deepcopy
import hashlib
import json

from dev_example import load_example
from probe import link_receipts, load_tokenizer


def digest(ids):
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()


def audit():
    tok = load_tokenizer()
    messages, tools, target, evidence = load_example()
    # OpenAI wire arguments are serialized, while the official Jinja iterates over a mapping.
    for message in messages:
        for call in message.get("tool_calls", []):
            args = call["function"]["arguments"]
            if isinstance(args, str):
                call["function"]["arguments"] = json.loads(args)
    def encode(text):
        return tok.encode(text, add_special_tokens=False)

    results = {}
    for name, open_thinking, keep_reasoning in (
        ("keep", True, True), ("skip_prefilled", False, False), ("skip_same_prompt", True, False),
        ("keep_linked_history", True, True),
    ):
        history = link_receipts(messages, tools) if name == "keep_linked_history" else messages
        answer = deepcopy(target)
        if not keep_reasoning:
            del answer["reasoning_content"]
        options = {"tools": tools, "tokenize": False, "enable_thinking": open_thinking}
        prompt = tok.apply_chat_template(history, add_generation_prompt=True, **options)
        whole = tok.apply_chat_template([*history, answer], add_generation_prompt=False, **options)
        if not whole.startswith(prompt) or not whole.endswith(tok.eos_token + "\n"):
            raise ValueError(f"{name}: no exact generation prefix or assistant end marker")
        whole = whole.removesuffix("\n")  # A following message's separator is outside this target.
        body = whole[len(prompt):]
        if (target["reasoning_content"].strip() in body) != keep_reasoning:
            raise ValueError(f"{name}: wrong teacher reasoning in supervision")
        for call in target["tool_calls"]:
            if f"<function={call['function']['name']}>" not in body:
                raise ValueError(f"{name}: lost a tool call")
        if name == "skip_same_prompt" and not body.lstrip().startswith("</think>"):
            raise ValueError("The same-prompt skip target must teach the model to close thinking")
        # Preserve inference-time input IDs when BPE would join text across the label boundary.
        prompt_ids, target_ids = encode(prompt), encode(body)
        ids = prompt_ids + target_ids
        labels = [-100] * len(prompt_ids) + target_ids
        if (tok.decode(ids, clean_up_tokenization_spaces=False) != whole or
                target_ids[-1] != tok.eos_token_id or labels[len(prompt_ids):] != target_ids):
            raise ValueError(f"{name}: input/label boundary or end token changed")
        results[name] = {"masked_prompt_tokens": labels.count(-100), "supervised_tokens": len(target_ids),
                         "prompt_ids_sha256": digest(prompt_ids), "target_ids_sha256": digest(target_ids),
                         "labels_sha256": digest(labels), "last_supervised_id": target_ids[-1],
                         "one_pass_ids_equal_split_ids": encode(whole) == ids}
    if results["keep"]["prompt_ids_sha256"] != results["skip_same_prompt"]["prompt_ids_sha256"]:
        raise ValueError("Common-prefix labels reveal the choice in the input")
    linked, native = results["keep_linked_history"], results["keep"]
    if (linked["target_ids_sha256"] != native["target_ids_sha256"] or
            linked["prompt_ids_sha256"] == native["prompt_ids_sha256"]):
        raise ValueError("Receipt projection must change only the history on this fixture")
    return {"scope": "one previously seen teacher decision, CPU format check only",
            "target_sha256": evidence["target"]["target_sha256"], "new_teacher_calls": 0,
            "examples_added_to_training": 0, "variants": results}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, ensure_ascii=False))
