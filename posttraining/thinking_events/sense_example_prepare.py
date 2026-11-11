"""Reconstruct the frozen 50 request prefixes and their single-field A/B variants."""
from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

from flockcue_qwen35 import adapter
from . import student
from .campaign_storage import atomic_write, digest, json_bytes, locked, read_jsonl

INDEX_SHA = "2384472a4916f86981e1c8e137e259f1c9b9e5107cf83a5df566b8945954f8c7"
CAMPAIGN_SHA = "ba8b8e2d10bdc54af7c5ff97c6919f5b2727a50de7e68db80efb9c7f67f5d2d6"
SENSE = "sense_nearby_entities"
DESCRIPTIONS = {"A": "0b8598b6208e83141241204931ea897edc361a8cbbc58f835e14a6595a43394d",
                "B": "d5b1e501ed100173ea316f45f819e757deefcec343af9302382ec60770ec09a1"}
EXAMPLE = (
    "The outer input_json argument is required, including when all inner fields use their defaults.\n"
    '        Default (all listed drones): {"input_json": {}}\n'
    '        Selected drones (use IDs from the fleet reply): {"input_json": '
    '{"drone_ids": ["drone-001", "drone-002"], "detail": "summary"}}'
)
GROUPS = {"missing_input_json": 42, "flat_fields": 4, "observations_returned": 4}
SCHEMA = "flockcue.sense-example.v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ordered_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def ids_sha256(ids):
    return digest(ordered_bytes(ids))


def verified_bytes(path, expected):
    raw = path.read_bytes()
    require(digest(raw) == expected, f"SHA-256 mismatch: {path}")
    return raw


def only(rows, label):
    values = list(rows)
    require(len(values) == 1, f"Expected one {label}, found {len(values)}")
    return values[0]


def sense_function(body):
    return only((t["function"] for t in body["tools"] if t["function"]["name"] == SENSE), SENSE)


def variant(body):
    changed = deepcopy(body)
    func = sense_function(changed)
    old = func["description"]
    require(digest(old.encode()) == DESCRIPTIONS["A"], "Unexpected old description")
    func["description"] = old[:old.rindex("Example:")] + EXAMPLE
    verify_variant(body, changed)
    return changed


def verify_variant(body, changed):
    # Revert exactly the allowed leaf, then compare the order and values of all fields.
    restored = deepcopy(changed)
    func = sense_function(restored)
    require(digest(func["description"].encode()) == DESCRIPTIONS["B"], "Unexpected new description")
    func["description"] = sense_function(body)["description"]
    require(ordered_bytes(body) == ordered_bytes(restored), "Another field or its order changed")


def verified_wire_bodies(files):
    bodies = []
    for path in files:
        for event in read_jsonl(path):
            if event["kind"] == "http_request":
                info = event["data"]["body"]
                raw = base64.b64decode(info["base64"], validate=True)
                require(len(raw) == info["length"] and digest(raw) == info["sha256"],
                        f"SDK body digest/length mismatch: {path}")
                bodies.append((info["sha256"], raw))
    return bodies


def lookup_body(bodies, sha):
    return only((raw for actual, raw in bodies if actual == sha), f"SDK request {sha}")


def _load_source(run_dir, q, frozen_slot):
    slot = q["slot_id"]
    folder = run_dir / "trials" / slot
    turn = q["turn"]
    require(frozen_slot["model"] == "Qwen3.5-2B" and
            all(q[k] == frozen_slot[k] for k in ("slot_id", "task_id", "scene_id", "thinking")) and
            q["seed"] == frozen_slot["turn_seeds"][turn - 1], f"Frozen trial mismatch: {slot}")

    result = json.loads((run_dir / "terminal-results" / f"{slot}.json").read_bytes())
    seal = json.loads(verified_bytes(run_dir / "worker-exit-seals" / f"{slot}.json",
                                     result["worker_seal_sha256"]))
    for name, sha in seal.items():
        verified_bytes(folder / name, sha)
    modelio = sorted((folder / "policy/modelio").glob("*.jsonl"))
    records_path = folder / "private/student-requests.jsonl"
    require(all(str(p.relative_to(folder)) in seal for p in [*modelio, records_path]),
            f"Unsealed worker input: {slot}")
    bodies = verified_wire_bodies(modelio)
    records = read_jsonl(records_path)
    record = only((r for r in records if r["turn"] == turn and r["slot"] == slot), "archived generation")
    require(record["request_body_sha256"] == q["sdk_body_sha256"] and
            all(record[k] == q[k] for k in ("seed", "spent_before", "thinking", "max_new_tokens")),
            f"Index differs from archived generation: {slot}")
    spent = sum(r["usage"]["total_tokens"] for r in records if r["turn"] < turn)
    require(record["spent_before"] == spent and record["remaining_seconds"] >= 120,
            f"Archived budget differs: {slot}")
    raw = lookup_body(bodies, q["sdk_body_sha256"])
    if q["original_outcome"] == "observations_returned":
        next_row = only((r for r in records if r["turn"] == turn + 1), "next generation")
        next_wire = json.loads(lookup_body(bodies, next_row["request_body_sha256"]))
        receipt = only((m for m in next_wire["messages"] if m["role"] == "tool"
                        and m["tool_call_id"] == q["receipt_id"]), "public sense receipt")
        content = json.loads(receipt["content"])
        require(len(content["sensed_drone_ids"]) == q["sensed_drones"] > 0 and
                set(content["sensed_drone_ids"]) == set(content["per_drone"]),
                f"Incomplete public sense receipt: {slot}")
    return raw, record, result["worker_seal_sha256"]


def _historical_outcome(tok, prepared, q, record):
    generated = record["generated_ids"]
    parsed = adapter.parse_generation(tok, generated, adapter.EOS_IDS, q["thinking"], prepared,
                                      record["max_new_tokens"])
    ok = parsed["message"] is not None
    require(all(parsed[k] == record[k] for k in ("message", "anomalies", "terminated")) and
            parsed["reasoning_tokens"] == record["usage"]["completion_tokens_details"]["reasoning_tokens"] and
            record["status"] == ("ok" if ok else "format_error") and
            record["stop_reason"] == (None if ok else parsed["anomalies"][0]),
            f"Archived output parses differently: {q['slot_id']}")
    if ok:
        call = only((c for c in parsed["message"].get("tool_calls", []) if c["function"]["name"] == SENSE),
                    "archived sense call")
        require(call["id"] == q.get("receipt_id"), "Sense call/receipt ID mismatch")
        return "observations_returned"
    require(parsed["terminated"] and parsed["anomalies"] == ["tool_arguments"], "Unexpected archived failure")
    text = tok.decode(generated[:-1], skip_special_tokens=False, clean_up_tokenization_spaces=False)
    decision = text.partition("</think>")[2] if q["thinking"] else text
    # The historical failures contain complete native call blocks. Locate only this function's parameters.
    block = only(re.findall(r"<tool_call>\s*<function=sense_nearby_entities>(.*?)"
                            r"</function>\s*</tool_call>", decision, re.S), "archived sense block")
    fields = adapter.PARAMETER.findall(block)
    if not block.strip():
        return "missing_input_json"
    require(fields and set(fields) <= {"drone_ids", "detail", "include_blackboard"},
            "Unexpected historical sense argument structure")
    return "flat_fields"


def _pair(tok, q, raw, archived, seal_sha, pair_index):
    old = json.loads(raw)
    new = variant(old)
    rows = []
    for condition in ("AB" if pair_index % 2 else "BA"):
        wire = old if condition == "A" else new
        payload = raw if condition == "A" else ordered_bytes(new)
        prompt, prepared = adapter.render(tok, wire["messages"], wire["tools"], q["thinking"])
        ids = prompt["input_ids"]
        cap, reason = student.output_budget(len(ids), q["spent_before"])
        require(reason is None and cap == q["max_new_tokens"] == 2048, "Unexpected output cap")
        if condition == "A":
            require(ids == archived["input_ids"] and
                    prompt["history_reasoning_tokens"] == archived["history_reasoning_tokens"],
                    f"Historical prompt mismatch: {q['slot_id']}")
            require(_historical_outcome(tok, prepared, q, archived) == q["original_outcome"],
                    f"Historical category mismatch: {q['slot_id']}")
        else:
            require(len(ids) == len(archived["input_ids"]) + 48, "Unexpected B input length")
        key = f"{q['slot_id']}-t{q['turn']}-{condition}"
        row = {**q, "slot_key": key, "pair_index": pair_index, "condition": condition,
               "input_ids_sha256": ids_sha256(ids), "input_tokens": len(ids),
               "request_path": f"requests/{key}.json", "request_sha256": digest(payload),
               "worker_seal_sha256": seal_sha, "historical_generated_ids": archived["generated_ids"],
               "historical_success": q["original_outcome"] == "observations_returned"}
        rows.append((row, payload))
    return rows


def _publish(path, raw):
    if path.exists():
        require(path.read_bytes() == raw, f"Prepared data differs from existing file: {path}")
    else:
        atomic_write(path, raw)


def load_plan(out_dir):
    out_dir = Path(out_dir)
    sha = (out_dir / "plan.sha256").read_text().strip()
    plan = json.loads(verified_bytes(out_dir / "plan.json", sha))
    require(plan["schema"] == SCHEMA, "Unknown sense-example plan")
    for row in plan["slots"]:
        verified_bytes(out_dir / row["request_path"], row["request_sha256"])
    return plan


def prepare(index: Path, run_dir: Path, out_dir: Path, model_path: Path) -> dict:
    index, run_dir, out_dir = Path(index), Path(run_dir), Path(out_dir)
    entries = [json.loads(line) for line in verified_bytes(index, INDEX_SHA).splitlines()]
    campaign = json.loads(verified_bytes(run_dir / "campaign.json", CAMPAIGN_SHA))
    require((run_dir / "campaign.sha256").read_text().strip() == CAMPAIGN_SHA, "Campaign seal mismatch")
    require(len(entries) == 50 and len({(q["slot_id"], q["turn"]) for q in entries}) == 50 and
            len({q["task_id"] for q in entries}) == 28 and
            Counter(q["thinking"] for q in entries) == {True: 27, False: 23} and
            Counter(q["original_outcome"] for q in entries) == GROUPS, "Frozen cohort mismatch")
    slot_file = verified_bytes(run_dir / "plan-slots.jsonl", campaign["plan_slots_sha256"])
    original_slots = {r["slot_id"]: r for r in map(json.loads, slot_file.splitlines())}

    # The local CPU modules and the runtime source archive must implement identical rendering/limits.
    source_hashes = {}
    for module in (adapter, adapter.v2, adapter.v2.base, student):
        path = Path(module.__file__)
        rel = "scripts/" + module.__name__.replace(".", "/") + ".py"
        source_hashes[rel] = campaign["sources"][rel]
        verified_bytes(path, source_hashes[rel])
        verified_bytes(run_dir / "source" / rel, source_hashes[rel])
    require(adapter.REVISION == campaign["model_revisions"]["Qwen3.5-2B"], "Model revision differs")
    tok = adapter.load_tokenizer(model_path)
    prepared = []
    for i, q in enumerate(entries, 1):
        raw, archived, seal = _load_source(run_dir, q, original_slots[q["slot_id"]])
        prepared.extend(_pair(tok, q, raw, archived, seal, i))
    slots = [row for row, payload in prepared]
    require(len({r["slot_key"] for r in slots}) == 100, "Duplicate condition slot")
    plan = {"schema": SCHEMA, "dependencies": {"campaign_sha256": CAMPAIGN_SHA, "index_sha256": INDEX_SHA,
            "plan_slots_sha256": campaign["plan_slots_sha256"], "source_sha256": source_hashes,
            "model_revision": adapter.REVISION, "tokenizer_sha256": adapter.FILES,
            "description_sha256": DESCRIPTIONS, "new_example": EXAMPLE}, "slots": slots}
    (out_dir / "requests").mkdir(parents=True, exist_ok=True)
    with locked(out_dir, ".prepare.lock"):
        for row, payload in prepared:
            _publish(out_dir / row["request_path"], payload)
        encoded = json_bytes(plan)
        _publish(out_dir / "plan.json", encoded)
        _publish(out_dir / "plan.sha256", (digest(encoded) + "\n").encode())
    return plan
