"""Checks specific to the single-field intervention in the Mac hover comparison."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from flockcue.history import sealed_source
from flockcue_qwen35 import takeoff_example as control
from validate_harness_episode import sha


def test_changes_one_docstring_in_private_copy(tmp_path):
    path = tmp_path / "source" / control.SOURCE
    path.parent.mkdir(parents=True)
    working_source = (ROOT / control.SOURCE).read_bytes()
    original = sealed_source(ROOT, control.SOURCE, control.CONTROL_SOURCE_SHA)
    path.write_bytes(original)
    sources = {control.SOURCE: sha(original)}
    declaration = control.prepare_variant(tmp_path, sources)
    derived = path.read_bytes()
    assert (ROOT / control.SOURCE).read_bytes() == working_source
    assert derived.count(control.REPLACEMENT.encode()) == 1
    assert derived.replace(control.REPLACEMENT.encode(), control.ORIGINAL.encode()) == original
    assert declaration["replacement_source_sha256"] == sources[control.SOURCE] == sha(derived)
    with pytest.raises(ValueError, match="locked v5"):
        control.prepare_variant(tmp_path, sources)


def test_refuses_unexpected_first_input(tmp_path):
    prompt = {"input_ids": [1], "prompt_text": "other prompt"}
    with pytest.raises(ValueError, match="First request differs"):
        control.check_first_prompt(tmp_path, prompt, control.FIRST_PROMPT["seed"])


def test_paired_command_id_changes_only_its_creation_site(tmp_path):
    path = tmp_path / "source" / control.COMMAND_SOURCE
    path.parent.mkdir(parents=True)
    original = sealed_source(ROOT, control.COMMAND_SOURCE, control.COMMAND_SOURCE_SHA)
    path.write_bytes(original)
    sources = {control.COMMAND_SOURCE: sha(original)}
    declaration = control.match_command_id(tmp_path, sources)
    created = f'        command_id = "{control.CONTROL_COMMAND_ID}"\n'.encode()
    derived = path.read_bytes()
    assert derived.count(created) == 1
    assert derived.replace(created, b"        command_id = str(uuid.uuid4())\n") == original
    assert declaration["replacement_source_sha256"] == sources[control.COMMAND_SOURCE] == sha(derived)
