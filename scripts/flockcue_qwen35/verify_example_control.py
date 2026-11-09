"""Compare the wrapped take_off example against the sealed, single-task Mac baseline."""
import argparse
import base64
from copy import deepcopy
import json
from pathlib import Path

from validate_harness_episode import sha
from .adapter import load_tokenizer
from .verify import project, read, require, rows
from .verify_mac_model import ACTOR, SLOT, verify as verify_episode

SOURCE = "agent4drone/uav_langchain_tools.py"
COMMAND_SOURCE = "agent4drone/uav_agent.py"
CONTROL_COMMAND = "c11d5d84-e59d-4409-b65c-b9c839e85cd6"
OLD = 'Example: {{"drone_id": "drone-001", "altitude": 15.0}}'
NEW = 'Example: {"input_json": {"drone_id": "drone-001", "altitude": 15.0}}'
BASE_MANIFEST = "e5edb6c70e8952bedd9a921074fc03b4000d2c198710e8d059d4cae0bdf9781f"
BASE_FIRST_REQUEST = "5b50ddbe615a2dab7ff2aad628b6e853ad30a8573fee7e76e002d9614ffb56fb"


def sdk_requests(run):
    archive, = (run / "trials" / SLOT / "policy/modelio").glob("*.jsonl")
    return [base64.b64decode(e["data"]["body"]["base64"], validate=True)
            for e in rows(archive) if e["kind"] == "http_request"]


def archive_turns(run):
    return rows(run / "trials" / SLOT / "private/student-requests.jsonl")


def baseline_first(run):
    require(sha((run / "manifest.json").read_bytes()) == BASE_MANIFEST, "wrong baseline manifest")
    first = sdk_requests(run)[0]
    require(sha(first) == BASE_FIRST_REQUEST, "wrong baseline SDK request")
    row = archive_turns(run)[0]
    require(row["turn"] == 1 and row["request_body_sha256"] == BASE_FIRST_REQUEST
            and row["seed"] == 3346213468 and row["thinking"] is False, "baseline first-turn controls differ")
    return json.loads(first), row


def expected_payload(first):
    predicted = deepcopy(first)
    match, = [i for i, t in enumerate(predicted["tools"]) if t["function"]["name"] == "take_off"]
    function = predicted["tools"][match]["function"]
    description = function["description"]
    require(description.endswith(OLD) and description.count(OLD) == 1, "different baseline take_off example")
    function["description"] = description.removesuffix(OLD) + NEW
    return predicted, match


def first_prompt_guard(baseline_run, messages, tools, seed, tokenizer):
    """Run in the policy coordinator before inference; return audit data only."""
    wire, row = baseline_first(Path(baseline_run))
    expected, index = expected_payload(wire)
    require(expected["messages"] == messages and expected["tools"] == tools and seed == row["seed"],
            "first model-visible messages, tool signature/order, or seed changed")
    options = {"add_generation_prompt": True, "tokenize": False, "enable_thinking": False}
    history = project(messages)
    original_text = tokenizer.apply_chat_template(history, tools=wire["tools"], **options)
    revised_text = tokenizer.apply_chat_template(history, tools=tools, **options)
    old_literal, new_literal = (json.dumps(s)[1:-1] for s in (OLD, NEW))
    require(original_text.count(old_literal) == 1
            and original_text.replace(old_literal, new_literal, 1) == revised_text,
            "rendered native prompt changed outside the example")
    original = tokenizer.encode(original_text, add_special_tokens=False)
    revised = tokenizer.encode(revised_text, add_special_tokens=False)
    require(original == row["input_ids"] and original != revised, "unexpected control prompt IDs")

    def digest(ids):
        return sha(json.dumps(ids, separators=(",", ":")).encode())

    return {"sdk_field": f"tools[{index}].function.description", "tool": "take_off",
            "before": OLD, "after": NEW, "common_tool_count": len(tools), "seed": seed,
            "baseline_first_request_sha256": BASE_FIRST_REQUEST,
            "baseline_prompt_text_sha256": sha(original_text.encode()),
            "treatment_prompt_text_sha256": sha(revised_text.encode()),
            "baseline_prompt_tokens": len(original), "treatment_prompt_tokens": len(revised),
            "baseline_input_ids_sha256": digest(original), "treatment_input_ids_sha256": digest(revised)}


def preflight(baseline):
    baseline = Path(baseline)
    first, row = baseline_first(baseline)
    predicted, _ = expected_payload(first)
    tokenizer = load_tokenizer(baseline / "source/training/tokenizers/qwen35-dev")
    return first_prompt_guard(baseline, predicted["messages"], predicted["tools"], row["seed"], tokenizer)


def flight_summary(result):
    actions = result["flight_commands"]
    prefix = f"/drones/{ACTOR}/command/"
    good = [a for a in actions if a["committed"] and a["status"] == "success"]
    takeoffs = [i for i, a in enumerate(good) if a["tool"] == "take_off" and a["endpoint"] == prefix + "take_off"
                and a["params"].get("altitude") == 12]
    hovers = [i for i, a in enumerate(good) if a["tool"] == "hover" and a["endpoint"] == prefix + "hover"]
    return {"target_takeoff_to_12m_committed": bool(takeoffs), "target_hover_command_committed": bool(hovers),
            "target_takeoff_then_hover": bool(takeoffs and hovers and min(takeoffs) < max(hovers)),
            "original_checker_passed": result["checker"]["passed"]}


def verify_pair(baseline, treatment):
    baseline, treatment = Path(baseline).resolve(), Path(treatment).resolve()
    base, changed = (verify_episode(r) for r in (baseline, treatment))
    require(base["manifest_sha256"] == BASE_MANIFEST and base["status"] == "scored", "different baseline")
    orig, new = (read(r / "manifest.json") for r in (baseline, treatment))
    for field in ("scope", "inputs", "private_canary", "model", "trials", "seed_rule"):
        require(orig[field] == new[field], "changed experiment control: " + field)
    intervention = new["intervention"]
    require(intervention["kind"] == "take_off_description_example" and intervention["source_path"] == SOURCE
            and intervention["original_example"] == OLD and intervention["replacement_example"] == NEW,
            "unexpected declared intervention")
    old_file, new_file = ((r / "source" / SOURCE).read_bytes() for r in (baseline, treatment))
    require(old_file.count(OLD.encode()) == 1 and new_file == old_file.replace(OLD.encode(), NEW.encode(), 1)
            and sha(old_file) == intervention["original_source_sha256"]
            and sha(new_file) == intervention["replacement_source_sha256"], "tool implementation changed")
    pin = new["matched_command_id"]
    before, after = ((r / "source" / COMMAND_SOURCE).read_bytes() for r in (baseline, treatment))
    make_id = b"        command_id = str(uuid.uuid4())"
    fixed_id = f'        command_id = "{CONTROL_COMMAND}"'.encode()
    require(before.count(make_id) == 1 and after == before.replace(make_id, fixed_id, 1)
            and pin["value"] == CONTROL_COMMAND and pin["source_path"] == COMMAND_SOURCE
            and sha(before) == pin["original_source_sha256"] and sha(after) == pin["replacement_source_sha256"],
            "different change at the task command ID creation site")
    for path, digest in orig["sources"].items():
        if not path.startswith("scripts/flockcue_qwen35/") or path.rsplit("/", 1)[-1] in {"adapter.py", "mac_model.py"}:
            if path not in (SOURCE, COMMAND_SOURCE):
                require(new["sources"].get(path) == digest, "changed model, harness, or scoring code: " + path)
    summary = {"baseline": base, "treatment": changed, "baseline_task": flight_summary(base)}
    if changed["status"] == "unscored":
        return {**summary, "treatment_task": None, "paired_task_comparison": False}

    first, base_row = baseline_first(baseline)
    expected, _ = expected_payload(first)
    for run in (baseline, treatment):
        archive, = (run / "trials" / SLOT / "policy/modelio").glob("*.jsonl")
        require({event["command_id"] for event in rows(archive)} == {CONTROL_COMMAND},
                "archived requests and receipts do not belong to the matched task command")
    raw = sdk_requests(treatment)
    require(raw and json.loads(raw[0]) == expected, "actual first SDK payload differs beyond the one example")
    for body in raw:
        wire = json.loads(body)
        require({k:v for k, v in wire.items() if k != "messages"} ==
                {k:v for k, v in expected.items() if k != "messages"}, "tools or SDK settings drifted during treatment")
    tokenizer = load_tokenizer(treatment / "source/training/tokenizers/qwen35-dev")
    first_row = archive_turns(treatment)[0]
    first_check = first_prompt_guard(baseline, expected["messages"], expected["tools"], first_row["seed"], tokenizer)
    require(first_row["seed"] == base_row["seed"] and first_row["thinking"] is False
            and first_row["request_body_sha256"] == sha(raw[0])
            and sha(json.dumps(first_row["input_ids"], separators=(",", ":")).encode()) ==
            first_check["treatment_input_ids_sha256"], "actual first treatment input IDs or seed differ")
    proof = read(treatment / "first-prompt-check.json")
    expected_proof = {"seed": first_check["seed"], "input_tokens": first_check["treatment_prompt_tokens"],
                      "prompt_text_sha256": first_check["treatment_prompt_text_sha256"],
                      "input_ids_sha256": first_check["treatment_input_ids_sha256"]}
    require(proof["matched"] is True and proof["observed"] == proof["expected"] == expected_proof,
            "recorded pre-generation check differs from reconstructed input")
    initial = [read(r / "trials" / SLOT / "worker-result.json")["initial"] for r in (baseline, treatment)]
    require(initial[0] == initial[1], "initial world, public state, or checker differ")
    runtime = [read(r / "model-runtime.json") for r in (baseline, treatment)]
    require({k:v for k,v in runtime[0].items() if k != "pid"} ==
            {k:v for k,v in runtime[1].items() if k != "pid"}, "real model or runtime controls differ")
    same_history = [i for i, (a, b) in enumerate(zip(sdk_requests(baseline), raw), 1)
                    if json.loads(a)["messages"] == json.loads(b)["messages"]]
    return {**summary, "treatment_task": flight_summary(changed), "paired_task_comparison": True,
            "matched_command_id": CONTROL_COMMAND, "turns_with_identical_wire_messages": same_history,
            "first_request": {**first_check, "treatment_first_request_sha256": sha(raw[0])},
            "identical_initial_state": initial[0], "treatment_requests_checked": len(raw)}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--preflight", action="store_true", help="construct and verify the paired first prompt offline")
    cli.add_argument("baseline", type=Path)
    cli.add_argument("treatment", nargs="?", type=Path)
    args = cli.parse_args()
    if args.preflight != (args.treatment is None):
        cli.error("use --preflight BASELINE or provide BASELINE TREATMENT")
    result = preflight(args.baseline) if args.preflight else verify_pair(args.baseline, args.treatment)
    print(json.dumps(result, ensure_ascii=False, indent=2))
