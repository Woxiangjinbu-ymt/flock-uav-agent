"""Fixed-source v1/v2 student tool-ID comparison; no provider/model execution."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

import convert_agent_samples as converter
import student_input as v1
import student_input_v2 as v2
import student_parallel_corpus as corpus
import student_sample_loader as loader

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "experiments/student-tool-ids-v2/plan.json"
PLAN_SHA256 = "5e620b8983dbee2ae1e70fc28f2baa7cf25ca5833769fe7b29dd1de7b76201c0"
SOURCES = ("scripts/validate_student_tool_ids.py", "scripts/student_input_v2.py", "scripts/student_parallel_corpus.py",
           "scripts/student_input.py", "scripts/convert_agent_samples.py", "scripts/student_sample_loader.py",
           "training/templates/qwen3-tool-ids-v2.jinja", "training/requirements-input.lock",
           "docs/STUDENT_TOOL_ID_ADAPTER_CN.md", "experiments/student-tool-ids-v2/plan.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(source):
    return tuple(source[key] for key in ("run", "trial_id", "logical_call_id", "attempt_id"))


def function_value(call):
    function = call["function"]
    args = function["arguments"]
    return {"name": function["name"], "arguments": v1.parse(args) if isinstance(args, str) else args}


def prove_history(original, prepared):
    """Independent semantic check: only outer call IDs/JSON argument types change."""
    actual = prepared["messages"]
    require(len(original) == len(actual), "History message count changed")
    mappings, calls, receipts, same_name_groups = {}, 0, 0, 0
    for before, after in zip(original, actual):
        require(before["role"] == after["role"] and (before.get("content") or "") == (after.get("content") or ""),
                "History order or public content changed")
        if before["role"] == "assistant":
            first, second = before.get("tool_calls", []), after.get("tool_calls", [])
            require(len(first) == len(second), "History call count changed")
            names = [call["function"]["name"] for call in first]
            same_name_groups += int(len(names) != len(set(names)))
            for old, new in zip(first, second):
                calls += 1
                expected = f"call_{calls}"
                require(new["id"] == expected and old["id"] not in mappings, "Canonical call numbering changed")
                require(function_value(old) == function_value(new), "Tool parameters or name changed")
                mappings[old["id"]] = expected
        elif before["role"] == "tool":
            receipts += 1
            require(after["tool_call_id"] == mappings[before["tool_call_id"]], "Receipt ID association changed")
    require(calls == receipts, "Input history has unmatched calls")
    return {"history_calls": calls, "history_receipts": receipts, "same_name_parallel_groups": same_name_groups}


def verify_old_package(folder, expected):
    require(v1.sha((folder / "manifest.json").read_bytes()) == expected, "Old student package anchor changed")
    return loader.load_package(folder)


def validate_output(output, plan):
    output = Path(output)
    protected = [ROOT / plan["conversion_package"], ROOT / plan["v1_student_package"], PLAN.parent,
                 v1.DEFAULT_TOKENIZER, ROOT / "docs/results/agent-samples-student-input-20261103"]
    protected.extend(ROOT / row["run"] for row in v1.parse((ROOT / plan["source_plan"]).read_bytes())["runs"])
    require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)), "Output must be new and nonsymlink")
    require(not any(output.resolve().is_relative_to(p.resolve()) for p in protected), "Output cannot modify sealed sources")


def run(output):
    raw_plan = PLAN.read_bytes()
    require(v1.sha(raw_plan) == PLAN_SHA256 == PLAN.with_suffix(".sha256").read_text().strip(), "Plan changed")
    plan = v1.parse(raw_plan)
    output = Path(output)
    validate_output(output, plan)
    frozen = {name: (ROOT / name).read_bytes() for name in SOURCES}
    require(v1.sha(frozen[plan["protocol"]]) == plan["protocol_sha256"], "Protocol changed")
    requests = corpus.load_requests(ROOT / plan["source_plan"])
    samples = corpus.load_samples(ROOT / plan["conversion_package"])
    require(len(requests) == plan["expected_wire_requests"] and len(samples) == plan["expected_samples"], "Source denominator changed")
    _, old_encoded = verify_old_package(ROOT / plan["v1_student_package"], plan["v1_student_manifest_sha256"])
    old_encoded = {row["sample_id"]: row for row in old_encoded}
    previous_raw = (ROOT / plan["v1_context"]).read_bytes()
    require(v1.sha(previous_raw) == plan["v1_context_sha256"], "Previous context diagnosis changed")
    previous = {identity(row): row for row in v1.parse(previous_raw)["requests"]}
    tokenizer = v1.load_tokenizer()
    prompt_rows, encoded_rows, rejected, comparison = [], [], [], []
    parallel_summary = corpus.summarize_parallel_requests(requests)
    synthetic_results = []
    for case in corpus.synthetic_cases():
        result = {"case_id": case["case_id"], "origin": case["origin"], "expected_valid": case["expected_valid"],
                  "is_real_model_evidence": False, "teacher_eligible": False}
        try:
            prepared = v2.prepare_history(case["messages"], case["tools"])
            prove_history(case["messages"], prepared)
            row = v2.encode_decision(tokenizer, case["messages"], case["tools"], case["target"], plan["max_length"])
            first = v2.prepare_prompt(tokenizer, prepared)
            again = v2.prepare_prompt(tokenizer, v2.prepare_history(prepared["messages"], case["tools"]))
            require(first["input_ids"] == again["input_ids"], "Repeated preparation changed canonical prompt")
            require(first["input_ids"] == row["input_ids"][:row["target_start"]], "Synthetic training/inference prefix mismatch")
            result.update(accepted=True, sequence_length=row["sequence_length"], prefix_equal=True, idempotent=True)
        except v1.InputRejected as exc:
            result.update(accepted=False, reason=exc.code)
        result["passed"] = result["accepted"] == result["expected_valid"]
        synthetic_results.append(result)
    for request in requests:
        source = request["source"]
        old = previous[identity(source)]
        item = {"request_id": request["request_id"], "source": source, "v1_measured": old["measured"],
                "v1_reason": old.get("reason"), "v1_prompt_tokens": old.get("prompt_tokens"), "v2_measured": False}
        try:
            before_hash = v1.sha(v1.canonical(request))
            prepared = v2.prepare_history(request["messages"], request["tools"])
            proof = prove_history(request["messages"], prepared)
            prompt = v2.prepare_prompt(tokenizer, prepared)
            require(before_hash == v1.sha(v1.canonical(request)), "Request mutated")
            count = len(prompt["input_ids"])
            item.update(v2_measured=True, v2_prompt_tokens=count, prompt_sha256=v1.sha(prompt["text"].encode()),
                        prompt_token_sha256=v1.sha(v1.canonical(prompt["input_ids"])), association=proof,
                        token_delta=count - old["prompt_tokens"] if old["measured"] else None)
        except v1.InputRejected as exc:
            item.update(v2_reason=exc.code)
        prompt_rows.append(item)
    for sample in samples:
        try:
            row = v2.encode_sample(tokenizer, sample, plan["max_length"])
            old = old_encoded[sample["sample_id"]]
            old_target = old["input_ids"][old["target_start"]:old["sequence_length"]]
            new_target = row["input_ids"][row["target_start"]:row["sequence_length"]]
            require(new_target == old_target, "Current target tokens changed from v1")
            prompt = v2.prepare_prompt(tokenizer, v2.prepare_history(sample["messages"][:-1], sample["tools"]))
            require(prompt["input_ids"] == row["input_ids"][:row["target_start"]], "Training/inference prefix differs")
            text = tokenizer.decode(new_target, skip_special_tokens=False)
            parsed = v2.parse_generated_decision(text, sample["tools"])
            expected = sample["messages"][-1]
            require(parsed.get("content", "").strip() == (expected.get("content") or "").strip(), "Decoded assistant text changed")
            require([function_value(c) for c in parsed.get("tool_calls", [])] ==
                    [function_value(c) for c in expected.get("tool_calls", [])], "Decoded tool calls changed")
            attached = v2.attach_generated_decision(v2.prepare_history(sample["messages"][:-1], sample["tools"]), parsed)
            require(len(attached["messages"]) == len(sample["messages"]), "Runtime append lost a message")
            encoded_rows.append(row)
            comparison.append({"sample_id": row["sample_id"], "v1_sequence_tokens": old["sequence_length"],
                               "v2_sequence_tokens": row["sequence_length"], "token_delta": row["sequence_length"] - old["sequence_length"],
                               "identical_target_tokens": True, "training_inference_prefix_equal": True, "decoder_roundtrip_equal": True})
        except v1.InputRejected as exc:
            rejected.append({"sample_id": sample["sample_id"], "reason": exc.code})
    chosen = next((limit for limit in plan["candidate_lengths"] if encoded_rows and all(r["sequence_length"] <= limit for r in encoded_rows)), plan["max_length"])
    for row in encoded_rows:
        row["max_length"] = chosen
        loader.validate_tokens(row)
    measured = [r for r in prompt_rows if r["v2_measured"]]
    paired = [r for r in measured if r["v1_measured"]]
    report = {"schema": "student-tool-id-comparison-v2", "scope": "development_input_only",
              "planned_episodes": plan["expected_planned_episodes"], "wire_requests": len(requests),
              "prompt_source_kinds": dict(Counter(r["source"]["source_kind"] for r in requests)),
              "sample_source_kinds": dict(Counter(r["source"]["source_kind"] for r in samples)),
              "v1_measured_prompts": sum(r["v1_measured"] for r in prompt_rows), "v2_measured_prompts": len(measured),
              "previously_rejected_resolved": sum(not r["v1_measured"] for r in measured),
              "v2_prompt_rejections": dict(Counter(r["v2_reason"] for r in prompt_rows if not r["v2_measured"])),
              "v2_prompt_tokens": v1._stats([r["v2_prompt_tokens"] for r in measured]),
              "paired_prompt_token_delta": v1._stats([r["token_delta"] for r in paired]),
              "full_samples": len(samples), "encoded_samples": len(encoded_rows), "rejected_samples": len(rejected),
              "v2_sequence_tokens": v1._stats([r["sequence_length"] for r in encoded_rows]),
              "paired_sequence_token_delta": v1._stats([r["token_delta"] for r in comparison]),
              "paired_sequence_totals": {"v1": sum(r["v1_sequence_tokens"] for r in comparison),
                                         "v2": sum(r["v2_sequence_tokens"] for r in comparison)},
              "paired_prompt_totals": {"v1": sum(r["v1_prompt_tokens"] for r in paired),
                                       "v2": sum(r["v2_prompt_tokens"] for r in paired)},
              "selected_smoke_length": chosen, "diagnostic_output_reserve_tokens": plan["diagnostic_output_reserve_tokens"],
              "synthetic_cases": len(synthetic_results), "synthetic_passed": sum(r["passed"] for r in synthetic_results),
              "unique_same_name_parallel_groups": parallel_summary["unique_same_name_groups"],
              "observed_reordered_wire_requests": parallel_summary["reordered_request_count"],
              "candidate_lengths": {str(limit): {
                  "complete_samples_fit": sum(r["sequence_length"] <= limit for r in encoded_rows),
                  "prompt_only_fit": sum(r["v2_prompt_tokens"] <= limit for r in measured),
                  "prompt_plus_reserve_fit": sum(r["v2_prompt_tokens"] + plan["diagnostic_output_reserve_tokens"] <= limit for r in measured)}
                  for limit in plan["candidate_lengths"]},
              "provider_calls": 0, "teacher_training_samples": 0, "weights_loaded": False, "training_started": False,
              "limitations": ["Historical failed search requests remain prompt-only, not training targets",
                              "Custom-template representation/decoder validation only; model understanding and serving integration untested"]}
    # Keep all failures in the report, but do not label a partial repair passed.
    report["passed"] = len(measured) == len(requests) and len(encoded_rows) == len(samples) and all(r["passed"] for r in synthetic_results)
    require(corpus.load_requests(ROOT / plan["source_plan"]) == requests, "Wire source drift")
    require(corpus.load_samples(ROOT / plan["conversion_package"]) == samples, "Converted source drift")
    verify_old_package(ROOT / plan["v1_student_package"], plan["v1_student_manifest_sha256"])
    v1.verify_tokenizer_bundle()
    require(PLAN.read_bytes() == raw_plan and all((ROOT / name).read_bytes() == raw for name, raw in frozen.items()), "Implementation drift during validation")
    blobs = {"comparison.json": v1.encoded(report), "parallel-history.json": v1.encoded(parallel_summary),
             "synthetic-checks.json": v1.encoded({"passed": all(r["passed"] for r in synthetic_results), "cases": synthetic_results}),
             "prompt-comparison.jsonl": b"".join(v1.canonical(r)+b"\n" for r in prompt_rows),
             "sample-comparison.jsonl": b"".join(v1.canonical(r)+b"\n" for r in comparison),
             "encoded.jsonl": b"".join(v1.canonical(r)+b"\n" for r in encoded_rows),
             "rejections.jsonl": b"".join(v1.canonical(r)+b"\n" for r in rejected)}
    blobs.update({"source/"+name: raw for name, raw in frozen.items()})
    manifest = {"schema": "student-input-bundle-v1", "adapter": v2.ADAPTER, "purpose": "development_input_smoke",
                "training_eligible": False, "model_weights_loaded": False, "selected_smoke_length": chosen,
                "encoded_samples": len(encoded_rows), "pad_token_id": tokenizer.pad_token_id,
                "source_plan_sha256": plan["source_plan_sha256"], "trial_plan_sha256": PLAN_SHA256,
                "source_converter_manifest_sha256": plan["conversion_manifest_sha256"],
                "v1_student_manifest_sha256": plan["v1_student_manifest_sha256"],
                "tokenizer_manifest_sha256": v1.TOKENIZER_MANIFEST_SHA256,
                "files": {name: v1.sha(raw) for name, raw in blobs.items()}}
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in blobs.items():
        destination = output/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    raw = v1.encoded(manifest)
    (output/"manifest.json").write_bytes(raw)
    (output/"manifest.sha256").write_text(v1.sha(raw)+"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["passed"] else 1)
