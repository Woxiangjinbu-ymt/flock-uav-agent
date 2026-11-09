"""Verify the Mac hover thinking switch with the corrected take_off example in both arms."""
import argparse
import base64
import json
from pathlib import Path

from validate_harness_episode import sha
from .adapter import load_tokenizer
from .completion_trace import completion_trace
from .verify import project, read, require, rows
from .verify_example_control import flight_summary
from .verify_mac_model import verify as verify_episode

OFF_SLOT = "mac-qwen35-off"
ON_SLOT = "mac-qwen35-on"
OFF_MANIFEST = "47df5abc53082fb5d895bb806b09ddb7c62471b742826e7adbd5b5afb007772b"
FIRST_WIRE = "308fcf9bd76bd6147e90ad1461c697d953bcf837d11cb88f2a049878e9f5b144"


def sdk_requests(run, slot):
    file, = (run / "trials" / slot / "policy/modelio").glob("*.jsonl")
    return [base64.b64decode(e["data"]["body"]["base64"], validate=True)
            for e in rows(file) if e["kind"] == "http_request"]


def first_prompt_pair(run):
    """Predict both native generation prompts from the exact archived baseline request."""
    require(sha((run / "manifest.json").read_bytes()) == OFF_MANIFEST, "wrong corrected-example baseline")
    raw = sdk_requests(run, OFF_SLOT)[0]
    require(sha(raw) == FIRST_WIRE, "wrong first baseline SDK request")
    wire = json.loads(raw)
    folder = run / "trials" / OFF_SLOT
    row = rows(folder / "private/student-requests.jsonl")[0]
    require(row["turn"] == 1 and row["seed"] == 3346213468 and row["thinking"] is False
            and row["request_body_sha256"] == FIRST_WIRE, "wrong baseline recording")
    tok = load_tokenizer(run / "source/training/tokenizers/qwen35-dev")
    options = {"tools": wire["tools"], "tokenize": False, "add_generation_prompt": True}
    messages = project(wire["messages"])
    off, on = (tok.apply_chat_template(messages, enable_thinking=mode, **options) for mode in (False, True))
    require(on.endswith("<|im_start|>assistant\n<think>\n") and off == on + "\n</think>\n\n",
            "prompt text changed outside the native thinking suffix")
    off_ids = tok.encode(off, add_special_tokens=False)
    on_ids = tok.encode(on, add_special_tokens=False)
    require(off_ids == row["input_ids"] and off_ids != on_ids, "baseline tokenizer replay differs")

    def proof(text, ids):
        return {"seed": row["seed"], "input_tokens": len(ids), "prompt_text_sha256": sha(text.encode()),
                "input_ids_sha256": sha(json.dumps(ids, separators=(",", ":")).encode())}

    return {"sdk_request_sha256": FIRST_WIRE, "tool_count": len(wire["tools"]),
            "off": proof(off, off_ids), "on": {**proof(on, on_ids), "sdk_request_sha256": FIRST_WIRE}}


def match_controls(off, on):
    baseline, treatment = (read(r / "manifest.json") for r in (off, on))
    for field in ("inputs", "scope", "seed_rule", "formal_slots_started", "teacher_calls", "private_canary",
                  "intervention", "matched_command_id"):
        require(baseline[field] == treatment[field], "changed paired control: " + field)
    require(treatment["control_baseline"]["manifest_sha256"] == OFF_MANIFEST and
            treatment["model"] == {**baseline["model"], "thinking": True} and
            treatment["trials"] == [{**baseline["trials"][0], "trial_id": ON_SLOT}],
            "model, sampling, scheduler or budget differs")
    # The mode selector and audit code change. The simulator, model engine, parser,
    # compiled tool descriptions, task initialization and scorer stay byte-identical.
    selectors = {"scripts/flockcue_qwen35/" + name for name in (
        "mac_smoke.py", "takeoff_example.py", "verify_mac_model.py")}
    for file, digest in baseline["sources"].items():
        if file not in selectors:
            require(treatment["sources"].get(file) == digest, "changed paired source: " + file)


def verify_pair(off, on):
    off, on = Path(off), Path(on)
    first = first_prompt_pair(off)
    match_controls(off, on)
    baseline = verify_episode(off)
    treatment = verify_episode(on, thinking=True)
    require(baseline["status"] == "scored", "unscored paired baseline")
    evidence = {"first_prompt": first, "off": baseline, "on": treatment}
    if treatment["status"] == "unscored":
        return {**evidence, "paired_task_comparison": False}
    manifest = read(on / "manifest.json")
    require(manifest["control_baseline"]["expected_treatment_first_prompt"] == first["on"],
            "predeclared native input differs from independent replay")
    for name, run, slot in (("off", off, OFF_SLOT), ("on", on, ON_SLOT)):
        proof = read(run / "first-prompt-check.json")
        require(proof["matched"] is True and proof["observed"] == proof["expected"] == first[name],
                "pre-generation guard differs: " + name)
        file, = (run / "trials" / slot / "policy/modelio").glob("*.jsonl")
        require({event["command_id"] for event in rows(file)} == {manifest["matched_command_id"]["value"]},
                "wrong command identity")
    off_raw = sdk_requests(off, OFF_SLOT)
    on_raw = sdk_requests(on, ON_SLOT)
    require(on_raw and off_raw[0] == on_raw[0], "first SDK requests are not byte-identical")
    fixed = {k: v for k, v in json.loads(off_raw[0]).items() if k != "messages"}
    for raw in off_raw + on_raw:
        require({k: v for k, v in json.loads(raw).items() if k != "messages"} == fixed,
                "SDK tools, order, or sampling controls changed")
    initial = [read(run / "trials" / slot / "worker-result.json")["initial"]
               for run, slot in ((off, OFF_SLOT), (on, ON_SLOT))]
    require(initial[0] == initial[1], "simulation initial state differs")
    runtime = [read(run / "model-runtime.json") for run in (off, on)]
    require({k: v for k, v in runtime[0].items() if k != "pid"} ==
            {k: v for k, v in runtime[1].items() if k != "pid"}, "model runtime differs")
    return {**evidence, "paired_task_comparison": True, "identical_first_wire_bytes": True,
            "identical_initial_state": initial[0], "matched_command_id": manifest["matched_command_id"]["value"],
            "off_flight": flight_summary(baseline), "on_flight": flight_summary(treatment),
            "off_completion": completion_trace(off, OFF_SLOT), "on_completion": completion_trace(on, ON_SLOT)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="check the two first prompts offline")
    parser.add_argument("off", type=Path)
    parser.add_argument("on", nargs="?", type=Path)
    args = parser.parse_args()
    if args.preflight != (args.on is None):
        parser.error("use --preflight OFF or provide OFF ON")
    result = first_prompt_pair(args.off) if args.preflight else verify_pair(args.off, args.on)
    print(json.dumps(result, ensure_ascii=False, indent=2))
