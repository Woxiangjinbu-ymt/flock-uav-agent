"""Pinned tokenizers, templates and wire formats used in the paired 2B/4B study."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import student_input_v2 as v2

MAIN = "Qwen3.5-2B"
REFERENCE = "Qwen3-4B"
MANIFESTS = {MAIN: "Qwen3.5-2B.json", REFERENCE: "4B.json"}
ROOT = Path(__file__).resolve().parents[2]


def manifest_path(model):
    return Path("training/model-manifests") / MANIFESTS[model]


def pinned_manifest(model):
    return json.loads((ROOT / manifest_path(model)).read_bytes())


def tokenizer_files(model):
    if model == MAIN:
        from flockcue_qwen35 import adapter

        return adapter.FILES
    files = pinned_manifest(model)["files"]
    return {name: files[name] for name in (
        "tokenizer.json", "tokenizer_config.json", "merges.txt", "vocab.json", "generation_config.json",
    )}


def load_codec(model, directory=None):
    from . import student

    if model == MAIN:
        from flockcue_qwen35 import adapter

        tokenizer = adapter.load_tokenizer(directory)
        return tokenizer, adapter, sorted(adapter.EOS_IDS), adapter.FILES["chat_template.jinja"]

    # The offline reference tokenizer bundle has these same exact files as
    # Qwen3-4B. Expose only the files verified against the actual 4B revision.
    from transformers import AutoTokenizer

    directory = Path(directory or v2.base.DEFAULT_TOKENIZER)
    with TemporaryDirectory() as tmp:
        clean = Path(tmp)
        for name, expected in tokenizer_files(model).items():
            raw = (directory / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(f"Qwen3-4B tokenizer checksum mismatch: {name}")
            (clean / name).write_bytes(raw)
        tokenizer = AutoTokenizer.from_pretrained(clean, local_files_only=True, trust_remote_code=False, use_fast=True)
        if not tokenizer.is_fast:
            raise ValueError("Expected the pinned fast Qwen3-4B tokenizer")
        eos = json.loads((clean / "generation_config.json").read_bytes())["eos_token_id"]
    return tokenizer, student, eos, v2.template_hash()


def fixture_replies(model, thinking):
    """Two task-independent public replies for the offline paired rehearsal."""
    if model == MAIN:
        fleet = "<tool_call>\n<function=list_drones>\n</function>\n</tool_call>"
        prefix = "Read the fleet.</think>\n\n" if thinking else ""
    elif model == REFERENCE:
        fleet = '<tool_call>\n{"name":"list_drones","arguments":{}}\n</tool_call>'
        prefix = "<think>Read the fleet.</think>\n\n" if thinking else ""
    else:
        raise ValueError(f"Unknown paired model: {model}")
    return [prefix + fleet + "<|im_end|>", prefix + "Done. [TASK DONE]<|im_end|>"]
