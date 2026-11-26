"""Select complete public conversation turns using the deployed Qwen prompt."""
import json
import sys

from posttraining.inference import MAX_CONTEXT, MAX_GENERATION
from posttraining.sft.adapter import load_tokenizer, render
from posttraining.sft.history import InputRejected, canonical, sha


PROMPT_LIMIT = MAX_CONTEXT - MAX_GENERATION


def select_window(tokenizer, messages, tools, limit=PROMPT_LIMIT):
    """Keep instructions and the longest recent suffix of complete assistant turns."""
    def measure(start):
        indices = [i for i, msg in enumerate(messages)
                   if i >= start or msg["role"] in {"system", "user"}]
        visible = [messages[i] for i in indices]
        prompt, _ = render(tokenizer, visible, tools, thinking=True)
        ids = prompt["input_ids"]
        return {"start": start, "tokens": len(ids), "ids_sha256": sha(canonical(ids))}

    full = measure(0)
    if full["tokens"] <= limit:
        return {**full, "full_tokens": full["tokens"]}
    starts = [i for i, msg in enumerate(messages) if msg["role"] == "assistant"]
    if not starts:
        raise ValueError("Required instructions exceed the student prompt limit")
    best = measure(starts[-1])
    if best["tokens"] > limit:
        raise ValueError("Latest tool-call group and instructions exceed the student prompt limit")
    left, right = 0, len(starts) - 1
    while left < right:
        middle = (left + right) // 2
        candidate = measure(starts[middle])
        if candidate["tokens"] <= limit:
            best, right = candidate, middle
        else:
            left = middle + 1
    return {**best, "full_tokens": full["tokens"]}


def main():
    tokenizer = load_tokenizer()
    for line in sys.stdin:
        request = json.loads(line)
        try:
            result = select_window(tokenizer, request["messages"], request["tools"])
        except (InputRejected, ValueError) as error:
            result = {"error": str(error)}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
