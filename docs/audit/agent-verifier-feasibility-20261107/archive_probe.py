"""Read-only feasibility audit. Counts raw visible histories and provider usage, with no model."""
import base64
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from tokenizers import Tokenizer


ROOT = Path(__file__).resolve().parents[3]
TOK = Tokenizer.from_file(str(ROOT / "training/tokenizers/qwen3-1.7b-input-v1/tokenizer.json"))


def decoded(body):
    raw = base64.b64decode(body["base64"], validate=True)
    assert len(raw) == body["length"] and hashlib.sha256(raw).hexdigest() == body["sha256"]
    return raw


def observed_messages(request):
    # One candidate view only. No system instructions, schema catalogue, target,
    # future context, private evaluator, or runtime recommendation added here.
    return [{k: m[k] for k in ("role", "content", "name", "tool_call_id", "tool_calls") if k in m}
            for m in request["messages"] if m["role"] in ("user", "assistant", "tool")]


def scan(archive):
    lengths, counts, chunks, pending_lengths = [], Counter(), defaultdict(list), {}
    for line in archive.open():
        row = json.loads(line)
        kind, data = row["kind"], row["data"]
        if kind == "http_request":
            request = json.loads(decoded(data["body"]))
            view = json.dumps(observed_messages(request), ensure_ascii=False, separators=(",", ":"))
            assert data["attempt_id"] not in pending_lengths
            pending_lengths[data["attempt_id"]] = len(TOK.encode(view, add_special_tokens=False).ids)
        elif kind == "http_response_chunk":
            chunks[data["attempt_id"]].append((data["chunk_index"], decoded(data["body"])))
        elif kind == "http_response_end":
            if not data["complete"]:
                assert data["status"] == "partial" and data["chunk_count"] == 0 and data["attempt_id"] not in chunks
                counts["empty_partial_response_events"] += 1
                pending_lengths.pop(data["attempt_id"])
                continue
            raw = b"".join(chunk for _, chunk in sorted(chunks.pop(data["attempt_id"])))
            assert hashlib.sha256(raw).hexdigest() == data["sha256"]
            lengths.append(pending_lengths.pop(data["attempt_id"]))
            usage = json.loads(raw)["usage"]
            counts.update({"requests": 1, "prompt": usage["prompt_tokens"],
                           "completion": usage["completion_tokens"], "total": usage["total_tokens"],
                           "reasoning": usage["completion_tokens_details"]["reasoning_tokens"]})
    assert not chunks and not pending_lengths and counts["requests"] == len(lengths)
    return lengths, counts


def summarize(lengths):
    return {"n": len(lengths), "min": min(lengths), "median": median(lengths), "max": max(lengths),
            "at_most_4096": sum(n <= 4096 for n in lengths)}


report = {"scope": "Archived development only. Length = plain JSON of all user/assistant/tool message fields listed in script; excludes system, schemas, and formatting. No evidence sufficiency or GPU performance claim.", "batches": {}}
for name in ("deepseek-hover-pilot-20261103", "deepseek-cost-pilot-20261103"):
    lengths, counts, trials = [], Counter(), {}
    root = ROOT / "docs/results" / name / "run-01/trials"
    for arc in sorted(root.glob("*/policy/modelio/*.jsonl")):
        trial = arc.parents[2].name
        ns, usage = scan(arc)
        trials[trial] = {"visible_history_raw_tokens": summarize(ns), "wire_usage": dict(usage)}
        lengths.extend(ns)
        counts.update(usage)
    report["batches"][name] = {"trials": trials, "visible_history_raw_tokens": summarize(lengths), "wire_usage": dict(counts)}
json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
print()
