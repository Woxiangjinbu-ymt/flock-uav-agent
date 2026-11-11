"""Deterministic public calibration histories. No benchmark tasks or private state."""
from __future__ import annotations

import hashlib

from thinking_events import paired_backend, student
from thinking_events.campaign import PAIRED_MODELS
from thinking_events.campaign_storage import digest, json_bytes


TOOLS = [{"type": "function", "function": {
    "name": "list_drones", "description": "Read the public fleet status.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}}]
LENGTHS = (4096, 8192, 13000)
MAX_LENGTH_ERROR = 55
PROBLEMS = (
    ("Aster", "Birch", "a low battery", "Return the aircraft with low charge to its base."),
    ("Coral", "Delta", "a blocked route", "Hold the affected aircraft and use the clear route."),
    ("Elm", "Flint", "a lost camera feed", "Use the available camera to inspect the gate."),
)


def seed(name):
    return int.from_bytes(hashlib.sha256(("public-t37-v1/" + name).encode()).digest()[:4], "big")


def history(case, repeats):
    first, second, issue, goal = PROBLEMS[case]
    lines = [f"Public practice log. {goal}"]
    for i in range(repeats):
        lines.append(f"Record {i}: {first} inspected tile {i % 71}; {second} checked relay {i % 31}. "
                     f"Both reported nominal status at this time.")
    receipt_id = f"public-inventory-{case}"
    return [{"role": "system", "content": "You coordinate a simulated flight crew. Use only the public log "
             "and tool feedback. Give the crew its next instruction and explain it briefly."},
            {"role": "user", "content": "\n".join(lines)},
            {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "id": receipt_id,
             "function": {"name": "list_drones", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": receipt_id, "name": "list_drones",
             "content": f"Latest: {first} reports {issue}. {second} is ready at base. Report is current."}]


def render_fixture(tokenizer, codec, thinking, case, target):
    def render(repeats):
        messages = history(case, repeats)
        prompt, prepared = codec.render(tokenizer, messages, TOOLS, thinking)
        if len(prepared["id_sidecar"]) != 1 or prepared["id_sidecar"][0]["original_id"] != f"public-inventory-{case}":
            raise ValueError("Lost public tool receipt identity")
        return messages, prompt

    messages, prompt = render(0)
    if target:
        initial = len(prompt["input_ids"])
        _, pilot = render(40)
        repeats = round((target - initial) * 40 / (len(pilot["input_ids"]) - initial))
        messages, prompt = render(repeats)
        if abs(target - len(prompt["input_ids"])) > MAX_LENGTH_ERROR:
            adjusted = round(repeats * (target - initial) / (len(prompt["input_ids"]) - initial))
            messages, prompt = render(adjusted)
        if abs(target - len(prompt["input_ids"])) > MAX_LENGTH_ERROR:
            raise ValueError(f"Could not freeze the {target}-token public fixture")
    ids = prompt["input_ids"]
    # 4B normally generates its opener. Pressure-only prefill guarantees all 2048
    # measured outputs are decoded inside a thought on both models.
    prefill = [] if prompt["prompt_text"].endswith("<think>\n") else tokenizer.encode("<think>\n", add_special_tokens=False)
    return {"messages": messages, "input_ids": ids, "input_sha256": digest(json_bytes(ids)),
            "stress_think_prefill_ids": prefill if thinking else [], "template_sha256": prompt["template_sha256"]}


def freeze(model_root):
    histories, schedule = {}, []
    for model in PAIRED_MODELS:
        tokenizer, codec, _, _ = paired_backend.load_codec(model, model_root / model)
        for thinking in (True, False):
            arm = "on" if thinking else "off"
            for length in (0, *LENGTHS):
                for case in range(3 if length else 1):
                    key = f"{model}-{arm}-{length}-{case}"
                    histories[key] = render_fixture(tokenizer, codec, thinking, case, length)
                    schedule.append({"id": key, "fixture": key, "model": model, "arm": arm,
                                     "kind": "natural" if length else "warmup", "target": length, "seed": seed(key)})
        for i in range(20):
            key = f"{model}-stress-{i:02d}"
            schedule.append({"id": key, "fixture": f"{model}-on-13000-{i % 3}", "model": model, "arm": "on",
                             "kind": "stress", "target": 13000, "seed": seed(key)})
        for factors in ((1, 2, 4), (7, 4, 1)):
            # Include each request's entire 2048-ID reserve. Actual prompt lengths
            # must satisfy the episode token allowance for both preregistered mixes.
            worst = sum(n * (max(len(histories[f"{model}-on-{length}-{i}"]["input_ids"]) for i in range(3))
                             + student.MAX_GENERATION) for length, n in zip(LENGTHS, factors))
            if worst > student.EPISODE_TOKEN_LIMIT:
                raise ValueError(f"Public workload would exceed the per-episode token budget: {worst}")
    return {"schema": "paired-public-t37-fixtures-v1", "tools": TOOLS, "histories": histories, "schedule": schedule,
            "sampling": student.SAMPLING, "max_new_tokens": student.MAX_GENERATION, "request_timeout": 120,
            "revisions": PAIRED_MODELS}
