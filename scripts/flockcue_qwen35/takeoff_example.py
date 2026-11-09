"""Single, predeclared change to the Qwen3.5 Mac development hover experiment."""
import json

from validate_harness_episode import sha, write


SOURCE = "agent4drone/uav_langchain_tools.py"
CONTROL_SOURCE_SHA = "3809e3a2258a0b55b6e2680a89e42ff8ba668388fe2c2c9d05d1060e5cc9fa74"
CONTROL_MANIFEST_SHA = "e5edb6c70e8952bedd9a921074fc03b4000d2c198710e8d059d4cae0bdf9781f"
COMMAND_SOURCE = "agent4drone/uav_agent.py"
COMMAND_SOURCE_SHA = "0ce3e0d0935511b61d8461939907b81ab21ee70c7ab163746a3067c751aaa9b4"
CONTROL_COMMAND_ID = "c11d5d84-e59d-4409-b65c-b9c839e85cd6"
ORIGINAL = 'Example: {{"drone_id": "drone-001", "altitude": 15.0}}'
REPLACEMENT = 'Example: {"input_json": {"drone_id": "drone-001", "altitude": 15.0}}'
# Independent preflight of the control's archived first request, changing only ORIGINAL.
FIRST_PROMPT = {
    "seed": 3346213468,
    "input_tokens": 8535,
    "prompt_text_sha256": "ee65e9abaa249a1f96f50ba8c5c83a235bfcdb3c0f3cf2564f715e183a90b3c8",
    "input_ids_sha256": "a1d902e9ea65e59e5856eddd79b53d979e93f0f64b61975188b7fd7c33b28a0a",
}


def replace_in_copy(run, sources, name, digest, before, after):
    source = run / "source" / name
    original = source.read_bytes()
    if sources[name] != digest or sha(original) != digest:
        raise ValueError(f"Source differs from locked v5: {name}")
    if original.count(before.encode()) != 1:
        raise ValueError(f"Expected a unique source location in {name}")
    variant = original.replace(before.encode(), after.encode())
    source.write_bytes(variant)
    sources[name] = sha(variant)


def prepare_variant(run, sources):
    """Apply the docstring edit exclusively to this run's new, unfrozen source tree."""
    replace_in_copy(run, sources, SOURCE, CONTROL_SOURCE_SHA, ORIGINAL, REPLACEMENT)
    return {"kind": "take_off_description_example", "source_path": SOURCE,
            "original_example": ORIGINAL, "replacement_example": REPLACEMENT,
            "original_source_sha256": CONTROL_SOURCE_SHA, "replacement_source_sha256": sources[SOURCE]}


def match_command_id(run, sources):
    """Reuse the reference command's public ID at its single point of creation."""
    replace_in_copy(run, sources, COMMAND_SOURCE, COMMAND_SOURCE_SHA,
                    "        command_id = str(uuid.uuid4())\n", f'        command_id = "{CONTROL_COMMAND_ID}"\n')
    return {"value": CONTROL_COMMAND_ID, "source_path": COMMAND_SOURCE,
            "original_source_sha256": COMMAND_SOURCE_SHA, "replacement_source_sha256": sources[COMMAND_SOURCE]}


def check_first_prompt(run, prompt, seed):
    ids = prompt["input_ids"]
    observed = {"seed": seed, "input_tokens": len(ids), "prompt_text_sha256": sha(prompt["prompt_text"].encode()),
                "input_ids_sha256": sha(json.dumps(ids, separators=(",", ":")).encode())}
    matched = observed == FIRST_PROMPT
    write(run / "first-prompt-check.json", {"observed": observed, "expected": FIRST_PROMPT, "matched": matched})
    if not matched:
        raise ValueError("First request differs from the predeclared take_off example variant")
