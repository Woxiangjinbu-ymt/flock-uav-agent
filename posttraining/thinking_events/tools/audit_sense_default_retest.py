"""Reconstruct exactly the public prompts and the optional sensing default for the A100 run."""

import argparse
from collections import Counter
import json
import importlib.util
import re
from pathlib import Path
import sys


_workspace = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
if __name__ == "__main__" and not __package__:
    sys.path[:0] = [str(_workspace / "scripts"), str(_workspace / "compat/scripts"), str(_workspace)]

from flockcue_qwen35 import adapter
from thinking_events import sense_default, sense_example_prepare as example, student
from thinking_events.campaign_storage import digest, json_bytes, read_jsonl


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_bytes())


def initial_body(raw):
    body = json.loads(raw)
    replacements = 0
    for message in body["messages"]:
        if message["role"] == "system":
            message["content"], n = re.subn(
                r'("command_id": ")[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}(")', r'\g<1><UUID>\2', message["content"])
            replacements += n
    require(replacements == 1, "Expected one first-prompt command ID")
    return example.ordered_bytes(body)


def check_schema(raw, effective):
    if not any(t["function"]["name"] == example.SENSE for t in raw):
        require(raw == effective, "Other tools changed in the absence of sensing")
        return
    previous_tools = example.variant({"tools": raw})["tools"]
    for old, new in zip(previous_tools, effective, strict=True):
        if old["function"]["name"] != example.SENSE:
            require(old == new, "An unrelated tool changed")
            continue
        from copy import deepcopy
        restored = deepcopy(new)
        restored["function"]["description"] = old["function"]["description"]
        require("required" not in restored["function"]["parameters"], "Sense still requires an argument")
        restored["function"]["parameters"]["required"] = ["input_json"]
        require(restored == old, "Unexpected change to registered B sense schema")


def sdk_requests(trial):
    files = sorted((trial / "policy/modelio").glob("*.jsonl"))
    wire = example.verified_wire_bodies(files)
    spans, ended = {}, {}
    for path in files:
        for record in read_jsonl(path):
            data = record["data"]
            if record["kind"] == "tool_start":
                key = (data["source_model_id"], data["raw_tool_call_id"])
                require(key not in spans, f"{trial.name}: repeated tool/call identity")
                spans[key] = (path.name, data["tool_span_id"], data["input"])
            if record["kind"] == "tool_end":
                ended[(path.name, data["tool_span_id"])] = data
    return wire, spans, ended


def audit(run, baseline, model_path, partial=False):
    # Reuse the preceding condition's unchanged attribution against the new advertised tools.
    prior_audit = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir()) / "docs/results/flockcue-a100-g1g2-retest-20261111/verify_intervention.py"
    spec = importlib.util.spec_from_file_location("flockcue_prior_tool_audit", prior_audit)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    tokenizer = adapter.load_tokenizer(model_path)
    plan = {r["slot_id"]: r for r in read_jsonl(run / "plan-slots.jsonl") if r["model"] == "Qwen3.5-2B"}
    reference_plan = {r["slot_id"]: r for r in read_jsonl(baseline / "plan-slots.jsonl") if r["model"] == "Qwen3.5-2B"}
    require(plan == reference_plan and len(plan) == 58, "Changed 58-slot plan")
    terminal_errors = {"on": Counter(), "off": Counter()}
    summary = Counter()
    examples = []
    totals = {arm: Counter() for arm in ("on", "off")}
    for sid, row in plan.items():
        trial = run / "trials" / sid
        if partial and not (trial / "evaluation/result.json").exists():
            continue
        records = read_jsonl(trial / "private/student-requests.jsonl")
        wire, spans, ended = sdk_requests(trial)
        require(len(records) == len(wire), f"{sid}: SDK/worker row counts differ")
        reference_wire = example.verified_wire_bodies(sorted((baseline / "trials" / sid / "policy/modelio").glob("*.jsonl")))
        require(initial_body(wire[0][1]) == initial_body(reference_wire[0][1]), f"{sid}: changed initial SDK body")
        local = Counter(initial_sdk_equal_except_uuid=1)
        for turn, ((body_sha, raw), rec) in enumerate(zip(wire, records, strict=True), 1):
            body = json.loads(raw)
            log = read(run / "intervention" / sid / f"turn-{turn:02d}.json")
            tools = sense_default.effective_tools(body["tools"])
            check_schema(body["tools"], tools)
            for field, value in (("messages", body["messages"]), ("upstream_tools", body["tools"]), ("effective_tools", tools)):
                require(log[field + "_sha256"] == digest(json_bytes(value)), f"{sid}/{turn}: {field}")
            require(body_sha == rec["request_body_sha256"] and log["slot"] == sid and log["turn"] == turn and
                    log["thinking"] == row["thinking"] and row["turn_seeds"][turn-1] == log["seed"] == rec["seed"],
                    f"{sid}/{turn}: slot, seed or SDK mismatch")
            prompt, prepared = adapter.render(tokenizer, body["messages"], tools, row["thinking"])
            local["requests"] += 1
            if rec.get("input_ids") is not None:
                budget, _ = student.output_budget(len(prompt["input_ids"]), rec["spent_before"])
                require(rec["input_ids"] == prompt["input_ids"] and rec["template_sha256"] == prompt["template_sha256"]
                        and rec["max_new_tokens"] == budget and rec["sampling"] == student.SAMPLING,
                        f"{sid}/{turn}: prompt, sampling or budget changed")
                local["verified_input_ids"] += 1
            if rec["status"] not in ("ok", "format_error"):
                require(log["execution_message"] is log["generated_message"] is None, f"{sid}/{turn}: stopped call")
                continue
            parsed = adapter.parse_generation(tokenizer, rec["generated_ids"], adapter.EOS_IDS, row["thinking"],
                                              prepared, rec["max_new_tokens"])
            message = parsed["message"]
            require(message == log["generated_message"] and sense_default.execution_message(message) ==
                    log["execution_message"] == rec["message"] and parsed["anomalies"] == rec["anomalies"] and
                    parsed["terminated"] == rec["terminated"], f"{sid}/{turn}: generated or executed message changed")
            if turn == len(records) and rec.get("stop_reason") == "tool_arguments":
                name = previous.failure_tool(tokenizer, rec, tools)
                require(name not in (None, "unresolved"), f"{sid}/{turn}: unresolved terminal tool error")
                terminal_errors["on" if row["thinking"] else "off"][name] += 1
            local["parsed_generations"] += 1
            for call in (message or {}).get("tool_calls", []):
                function = call["function"]
                if function["name"] != example.SENSE or json.loads(function["arguments"]) != {}:
                    continue
                local["emitted_omitted_sense"] += 1
                key = (f"model-{turn}", call["id"])
                if key in spans:
                    file, span, start = spans[key]
                    require(start["name"] == example.SENSE and start["args"] == {"input_json": {}},
                            f"{sid}/{turn}: wrong execution of default")
                    receipt = ended.get((file, span))
                    local["executed_omitted_sense"] += 1
                    if receipt and receipt["status"] == "returned":
                        require(receipt["output"]["tool_call_id"] == call["id"], "Wrong receipt identity")
                        local["returned_omitted_sense"] += 1
                examples.append({"slot": sid, "turn": turn, "call_id": call["id"], "started": key in spans})
        local["slots"] += 1
        summary.update(local)
        totals["on" if row["thinking"] else "off"].update(local)
    return {"condition": "C", "partial": partial, "summary": summary, "by_thinking": totals, "default_calls": examples,
            "terminal_tool_argument_errors_by_arm": terminal_errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", type=Path, default=adapter.DEFAULT_CACHE)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    evidence = audit(args.run, args.baseline, args.model, args.partial)
    if args.output.resolve().is_relative_to(args.run.resolve()):
        parser.error("Write verification outside the sealed run")
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(evidence["summary"]))
