"""Read-only probe of teacher reasoning usage in the four sealed DeepSeek batches (2026-11-02/03).

Decodes the archived HTTP request/response bytes and reports, per batch: served model names,
usage tokens (prompt / completion / reasoning), responses with zero reasoning tokens, and how
many assistant history messages sent back to the provider carried reasoning_content.
No provider calls, no simulator, no writes. Run from the repository root:

    python3 -I docs/audit/teacher-reasoning-usage-20261108/probe.py | diff - docs/audit/teacher-reasoning-usage-20261108/summary.json
"""
import base64
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BATCHES = ("deepseek-behavior-pilot-20261102", "deepseek-contract-pilot-20261103",
           "deepseek-cost-pilot-20261103", "deepseek-hover-pilot-20261103")


def decode(body):
    if isinstance(body, dict) and "base64" in body:
        return base64.b64decode(body["base64"]).decode("utf-8", errors="replace")
    return body if isinstance(body, str) else json.dumps(body)


def stream_objects(text):
    for piece in text.split("\n"):
        piece = piece.strip().removeprefix("data:").strip()
        if piece.startswith("{"):
            try:
                yield json.loads(piece)
            except json.JSONDecodeError:
                continue


def batch_summary(name):
    served, counts = Counter(), Counter()
    reasoning = []
    files = sorted((ROOT / "docs/results" / name / "run-01").rglob("model-io-*.jsonl"))
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            kind, data = record.get("kind"), record.get("data", {})
            if kind == "http_request":
                request = json.loads(decode(data.get("body")))
                history = [m for m in request.get("messages", []) if m.get("role") == "assistant"]
                counts["requests"] += 1
                counts["assistant_history_messages"] += len(history)
                counts["history_messages_with_reasoning"] += sum(
                    1 for m in history if (m.get("reasoning_content") or "").strip())
            elif kind == "http_response_chunk":
                for obj in stream_objects(decode(data.get("body"))):
                    if obj.get("model"):
                        served[obj["model"]] += 1
                    usage = obj.get("usage")
                    if not usage:
                        continue
                    r = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
                    counts["responses_with_usage"] += 1
                    counts["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    counts["completion_tokens"] += usage.get("completion_tokens", 0)
                    counts["reasoning_tokens"] += r
                    counts["responses_with_zero_reasoning"] += r == 0
                    reasoning.append(r)
    reasoning.sort()
    return {"archives": len(files), **dict(sorted(counts.items())),
            "reasoning_tokens_median": reasoning[len(reasoning) // 2] if reasoning else None,
            "reasoning_tokens_max": reasoning[-1] if reasoning else None,
            "served_models": dict(sorted(served.items()))}


def main():
    batches = {name: batch_summary(name) for name in BATCHES}
    keys = ("responses_with_usage", "prompt_tokens", "completion_tokens", "reasoning_tokens",
            "responses_with_zero_reasoning")
    total = {k: sum(b.get(k, 0) for b in batches.values()) for k in keys}
    total["reasoning_share_of_completion"] = round(total["reasoning_tokens"] / total["completion_tokens"], 4)
    total["reasoning_share_of_all_tokens"] = round(
        total["reasoning_tokens"] / (total["prompt_tokens"] + total["completion_tokens"]), 4)
    total["zero_reasoning_share"] = round(total["responses_with_zero_reasoning"] / total["responses_with_usage"], 4)
    print(json.dumps({"scope": "Four sealed real DeepSeek development batches (run-01 only); "
                      "thinking enabled, reasoning_effort high, as recorded in each run manifest. "
                      "Development tasks only; not train-split teacher data.",
                      "provider_calls": 0, "batches": batches, "total": total},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
