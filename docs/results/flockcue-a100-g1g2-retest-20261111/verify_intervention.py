"""Rebuild every model-visible prompt and check it against the live input IDs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from flockcue_qwen35 import adapter
from thinking_events import sense_example_prepare as example
from thinking_events import student
import student_input_v2 as v2


def read(path):
    return json.loads(path.read_bytes())


def lines(path):
    return list(map(json.loads, path.read_bytes().splitlines()))


def normalize_command_id(body):
    # The original harness assigns a fresh command UUID at episode creation.
    # Preserve every other character, field and ordering in the public SDK body.
    copy = dict(body)
    messages = []
    count = 0
    for message in body["messages"]:
        item = dict(message)
        if item["role"] == "system":
            item["content"], changes = re.subn(
                r'("command_id": ")[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(")',
                r'\g<1><COMMAND_UUID>\2', item["content"])
            count += changes
        messages.append(item)
    if count != 1:
        raise ValueError("Expected exactly one initial command UUID")
    copy["messages"] = messages
    return example.ordered_bytes(copy)


def failure_tool(tokenizer, result, tools):
    """Name the first rejected complete call only if the frozen parser reproduces its error."""
    if result.get("stop_reason") != "tool_arguments":
        return None
    ids = result.get("generated_ids") or []
    if not ids or ids[-1] not in adapter.EOS_IDS:
        return "unresolved"
    answer = tokenizer.decode(ids[:-1], skip_special_tokens=False, clean_up_tokenization_spaces=False)
    if result["thinking"]:
        answer = answer.split("</think>", 1)[-1]
    for block in re.findall(r"<tool_call>.*?</tool_call>", answer, re.S):
        match = re.match(r"<tool_call>\s*<function=([\w-]+)>", block)
        if not match:
            return "unresolved"
        try:
            adapter._decision(block, tools)
        except v2.InputRejected as exc:
            return match.group(1) if exc.code == "tool_arguments" else "unresolved"
    return "unresolved"


def prompt_audit(run, original, model, condition):
    tokenizer = adapter.load_tokenizer(model)
    old_plan = {r["slot_id"]: r for r in lines(original / "plan-slots.jsonl") if r["model"] == "Qwen3.5-2B"}
    new_plan = {r["slot_id"]: r for r in lines(run / "plan-slots.jsonl") if r["model"] == "Qwen3.5-2B"}
    if len(old_plan) != 58 or new_plan != old_plan:
        raise ValueError("2B plan differs from the 58 original slots")
    per_slot = []
    for key, frozen in old_plan.items():
        trial = run / "trials" / key
        wire = example.verified_wire_bodies(sorted((trial / "policy/modelio").glob("*.jsonl")))
        records = lines(trial / "private/student-requests.jsonl")
        old_trial = original / "trials" / key
        original_rows = lines(old_trial / "private/student-requests.jsonl")
        original_first = original_rows[0]
        old_wire = example.verified_wire_bodies(sorted((old_trial / "policy/modelio").glob("*.jsonl")))
        old_first_body = json.loads(example.lookup_body(old_wire, original_first["request_body_sha256"]))
        matched = missing_ids = offered = 0
        first_equal = normalized_equal = None
        last_tool_error = None
        if len(wire) != len(records):
            raise ValueError(f"{key}: SDK requests and model records differ in count")
        for index, ((sha, body), record) in enumerate(zip(wire, records, strict=True)):
            turn = index + 1
            if ((sha, key, turn) != (record["request_body_sha256"], record["slot"], record["turn"])
                    or record.get("seed", frozen["turn_seeds"][index]) != frozen["turn_seeds"][index]
                    or record.get("thinking", frozen["thinking"]) != frozen["thinking"]):
                raise ValueError(f"{key} turn {turn}: dispatch identity, mode or seed changed")
            old = json.loads(body)
            present = any(t["function"]["name"] == example.SENSE for t in old["tools"])
            if present and example.digest(example.sense_function(old)["description"].encode()) != example.DESCRIPTIONS["A"]:
                raise ValueError(f"{key} turn {turn}: unexpected SDK description")
            offered += present
            effective = example.variant(old) if present and condition == "B" else old
            if condition == "B":
                log = read(run / "intervention" / key / f"turn-{turn:02d}.json")
                values = {"messages_sha256": old["messages"], "upstream_tools_sha256": old["tools"],
                          "effective_tools_sha256": effective["tools"]}
                if (any(log[field] != example.digest(example.ordered_bytes(value)) for field, value in values.items())
                        or (log["slot"], log["turn"], log["seed"], log["thinking"], log["sense_offered"]) !=
                        (key, turn, frozen["turn_seeds"][index], frozen["thinking"], present)):
                    raise ValueError(f"{key} turn {turn}: live intervention and independent replay differ")
            prompt, history = adapter.render(tokenizer, effective["messages"], effective["tools"], frozen["thinking"])
            if index == 0:
                historical_prompt, _ = adapter.render(tokenizer, old["messages"], old["tools"], frozen["thinking"])
                first_equal = historical_prompt["input_ids"] == original_first["input_ids"]
                normalized_equal = normalize_command_id(old) == normalize_command_id(old_first_body)
            ids = record.get("input_ids")
            if ids is None:
                if record.get("status") in {"ok", "format_error"}:
                    raise ValueError(f"{key} turn {turn}: generation without input IDs")
                missing_ids += 1
            else:
                budget, _ = student.output_budget(len(ids), record["spent_before"])
                if (prompt["input_ids"] != ids or budget != record["max_new_tokens"]
                        or record["sampling"] != student.SAMPLING or record["template_sha256"] != prompt["template_sha256"]):
                    raise ValueError(f"{key} turn {turn}: effective input IDs, budget, sampling or template differ")
                matched += 1
            if record.get("generated_ids") is not None and record.get("status") in {"ok","format_error"}:
                parsed = adapter.parse_generation(tokenizer, record["generated_ids"], adapter.EOS_IDS, frozen["thinking"],
                                                  history, record["max_new_tokens"])
                if any(parsed[k] != record[k] for k in ("message", "anomalies", "terminated")):
                    raise ValueError(f"{key} turn {turn}: output differs from frozen group parser")
            if index == len(records) - 1:
                last_tool_error = failure_tool(tokenizer, record, effective["tools"])
        per_slot.append({"slot": key, "thinking": frozen["thinking"], "model_requests": len(records),
                         "inputs_verified": matched, "input_ids_unavailable": missing_ids, "sense_offered": offered,
                         "old_description_first_prompt_matches_original": first_equal,
                         "initial_sdk_matches_after_command_uuid_normalization": normalized_equal,
                         "terminal_tool_argument_error": last_tool_error})
    return {"condition": condition, "effective_description_sha256": example.DESCRIPTIONS[condition],
            "frozen_slots": len(per_slot), "requests": sum(r["model_requests"] for r in per_slot),
            "inputs_verified": sum(r["inputs_verified"] for r in per_slot),
            "requests_with_sense_offered": sum(r["sense_offered"] for r in per_slot),
            "input_ids_unavailable": sum(r["input_ids_unavailable"] for r in per_slot),
            "initial_prompts_matching_original": sum(r["old_description_first_prompt_matches_original"] is True for r in per_slot),
            "initial_sdk_matching_except_command_uuid": sum(
                r["initial_sdk_matches_after_command_uuid_normalization"] is True for r in per_slot),
            "terminal_tool_argument_errors": dict(Counter(r["terminal_tool_argument_error"] for r in per_slot
                                                         if r["terminal_tool_argument_error"] is not None)),
            "slots": per_slot}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--original", type=Path, default=ROOT / "outputs/flockcue-stage0-20261110/server/run")
    p.add_argument("--model", type=Path, default=adapter.DEFAULT_CACHE)
    p.add_argument("--condition", choices=["A", "B"], default="B")
    args = p.parse_args()
    print(json.dumps(prompt_audit(args.run, args.original, args.model, args.condition), ensure_ascii=False, indent=2))
