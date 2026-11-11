#!/usr/bin/env python3
"""Prompt-only length diagnostics, including rejected/partial source episodes.

No assistant target is fabricated, rendered, labeled or exported. HTTP request
attempts and logical calls keep separate denominators; an observed serialized
request is not evidence that a provider accepted it.
"""
from __future__ import annotations


import argparse
from collections import Counter
import json
from pathlib import Path
import sys


_workspace = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(_workspace / "scripts"))

import convert_agent_samples as converter
import student_input as student


def measure_request(tokenizer, request):
    removed = []
    messages = [converter.clean_message(value, f"request.messages[{i}]", removed)
                for i, value in enumerate(request["messages"])]
    # This value only drives structural validation; it is never rendered.
    history, tools, _, _ = student.normalize_input(messages, request.get("tools", []),
                                                  {"role": "assistant", "content": "input validation sentinel"})
    prefix = tokenizer.apply_chat_template(history, tools=tools, tokenize=False,
                                           add_generation_prompt=True, enable_thinking=False)
    ids = tokenizer(prefix, add_special_tokens=False, truncation=False)["input_ids"]
    return {"prompt_tokens": len(ids), "removed_reasoning_fields": len(removed),
            "prompt_render_sha256": converter.sha(prefix.encode()),
            "tool_count": len(tools), "history_messages": len(history)}


def audit(source_plan, *, workspace=converter.ROOT, tokenizer_bundle=student.DEFAULT_TOKENIZER):
    source_plan, workspace = Path(source_plan).resolve(), Path(workspace).resolve()
    sources_code = (Path(__file__).resolve(), Path(converter.__file__).resolve(), Path(student.__file__).resolve())
    code_hashes = {str(p.relative_to(converter.ROOT)): converter.sha(p.read_bytes()) for p in sources_code}
    plan_raw = source_plan.read_bytes()
    if converter.sha(plan_raw) != source_plan.with_suffix(".sha256").read_text().strip():
        raise ValueError("Source plan changed")
    plan = converter.parse(plan_raw)
    tokenizer = student.load_tokenizer(tokenizer_bundle)
    tokenizer_hash = converter.sha((Path(tokenizer_bundle) / "manifest.json").read_bytes())
    rows, episodes, logical_total, sources = [], [], 0, []
    for source in plan["runs"]:
        run = converter.safe(workspace, source["run"])
        manifest, kind, _ = converter._verify_run(run, source)
        sources.append(source)
        for trial in manifest["trials"]:
            folder = run / "trials" / trial["trial_id"]
            files = sorted(folder.glob("policy/modelio/*.jsonl")) if folder.exists() else []
            summary = {"run": source["run"], "trial_id": trial["trial_id"], "task_id": trial["task_id"],
                       "source_kind": kind, "logical_calls": 0, "http_request_records": 0,
                       "logical_calls_without_http_request": [], "archive_present": bool(files)}
            if len(files) > 1:
                raise ValueError("Ambiguous source archive")
            if not files:
                episodes.append(summary)
                continue
            raw = files[0].read_bytes()
            if not raw.endswith(b"\n"):
                raise ValueError("Unterminated archived record cannot be length-audited")
            events = [converter.parse(line) for line in raw.splitlines()]
            starts = [e["data"]["logical_call_id"] for e in events if e["kind"] == "model_start"]
            requests = [e for e in events if e["kind"] == "http_request"]
            logical_total += len(starts)
            summary.update(logical_calls=len(starts), http_request_records=len(requests),
                           logical_calls_without_http_request=sorted(set(starts) - {e["data"]["logical_call_id"] for e in requests}))
            for event in requests:
                data = event["data"]
                row = {"run": source["run"], "trial_id": trial["trial_id"], "task_id": trial["task_id"],
                       "source_kind": kind, "split": "development", "archive_sha256": converter.sha(raw),
                       "logical_call_id": data["logical_call_id"], "attempt_id": data["attempt_id"],
                       "request_sequence": event["sequence"], "measured": False}
                try:
                    body = converter.decode(data["body"])
                    if data.get("body_scope") != "serialized_http_request_content":
                        raise ValueError("Not a serialized wire request")
                    request = converter.parse(body)
                    row.update(measure_request(tokenizer, request), measured=True, request_body_sha256=converter.sha(body))
                except student.InputRejected as exc:
                    row.update(reason=exc.code)
                except (converter.Excluded, KeyError, ValueError, TypeError):
                    row.update(reason="unavailable_or_unsupported_request")
                rows.append(row)
            episodes.append(summary)
    if source_plan.read_bytes() != plan_raw:
        raise ValueError("Source plan drift during diagnostic")
    for source in sources:
        converter._verify_run(converter.safe(workspace, source["run"]), source)
    if code_hashes != {str(p.relative_to(converter.ROOT)): converter.sha(p.read_bytes()) for p in sources_code}:
        raise ValueError("Diagnostic implementation changed during execution")
    student.verify_tokenizer_bundle(tokenizer_bundle)
    if tokenizer_hash != converter.sha((Path(tokenizer_bundle) / "manifest.json").read_bytes()):
        raise ValueError("Tokenizer changed during diagnostic")
    measured = [r for r in rows if r["measured"]]
    return {"schema": "student-context-diagnostic-v1", "scope": "prompt_only_no_targets_no_training_labels",
            "source_plan_sha256": converter.sha(plan_raw), "planned_episodes": len(episodes),
            "logical_calls": logical_total, "http_request_records": len(rows), "measured_requests": len(measured),
            "unmeasurable_requests": len(rows) - len(measured),
            "logical_calls_without_http_request": sum(len(e["logical_calls_without_http_request"]) for e in episodes),
            "prompt_tokens": student._stats([r["prompt_tokens"] for r in measured]),
            "candidate_lengths": {str(limit): {"prompt_only_fits": sum(r["prompt_tokens"] <= limit for r in measured),
                "prompt_alone_exceeds": sum(r["prompt_tokens"] > limit for r in measured),
                "output_headroom_not_guaranteed": True} for limit in student.LENGTHS},
            "unmeasurable_reasons": dict(Counter(r["reason"] for r in rows if not r["measured"])),
            "episodes": episodes, "requests": rows, "teacher_examples": 0, "provider_calls": 0,
            "tokenizer_manifest_sha256": tokenizer_hash,
            "source_sha256": code_hashes,
            "limitations": ["Captured HTTP serialization does not imply provider dispatch",
                            "Includes incomplete/failed episodes solely to diagnose input sizes; no label eligibility",
                            "Prompt-only fit does not reserve space for an assistant answer",
                            "Development sources only; no new holdout or Teacher collection"]}


def validate_output(source_plan, output, *, workspace=converter.ROOT, tokenizer_bundle=student.DEFAULT_TOKENIZER):
    source_plan, output = Path(source_plan).resolve(), Path(output).resolve()
    protected = [source_plan.parent, Path(tokenizer_bundle).resolve()]
    protected += [converter.safe(Path(workspace).resolve(), source["run"]) for source in converter.parse(source_plan.read_bytes())["runs"]]
    if output.exists() or any(output.is_relative_to(root) for root in protected):
        raise ValueError("Diagnostic output must be new and outside sealed sources/tokenizer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path, default=converter.ROOT / "experiments/agent-samples-v1/source-plan.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate_output(args.source_plan, args.output)
    result = audit(args.source_plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k not in {"episodes", "requests", "source_sha256"}}))


if __name__ == "__main__":
    main()
