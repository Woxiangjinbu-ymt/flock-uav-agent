"""Counterfactual audit may replace only exact native-description duplicates."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("input_audit", ROOT / "scripts/audit_harness_input_layout.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def body():
    tool = {"type": "function", "function": {"name": "navigate", "description": "Visit a named target.\n Preserve partial effects.",
        "parameters": {"type": "object", "properties": {"input_json": {"type": "string"}}, "required": ["input_json"]}}}
    text = m.system_prompt.build_system_prompt([SimpleNamespace(**{k: tool['function'][k] for k in ('name', 'description')})])
    return {"model": "deepseek-flash", "thinking": {"type": "enabled"}, "tools": [tool], "messages": [
        {"role": "system", "content": text + '\n[Execution recovery contract]\n{"status":"unknown","source":"e1"}'},
        {"role": "user", "content": "访问目标并拍照。"},
        {"role": "assistant", "reasoning_content": "raw reasoning unchanged", "content": None,
         "tool_calls": [{"id": "raw-1", "type": "function", "function": {"name": "navigate", "arguments": '{"input_json":{"x":1}}'}}]},
        {"role": "tool", "tool_call_id": "raw-1", "content": '{"status":"partial_success","successful_points":[[1,2,3]]}'}]}


def test_only_catalog_changed_no_raw_reasoning_or_invalid_arguments_normalized():
    original = body()
    snapshot = copy.deepcopy(original)
    changed, audit = m.replace_duplicate_catalog(original)
    assert original == snapshot
    assert original["messages"][1:] == changed["messages"][1:]
    assert original["tools"] == changed["tools"]
    assert changed["messages"][0]["content"].endswith('[Execution recovery contract]\n{"status":"unknown","source":"e1"}')
    assert audit["all_other_request_values_unchanged"] is True
    assert audit["canonical_request_bytes_before"] > audit["canonical_request_bytes_after"]
    assert audit["canonical_request_bytes_before"] == len(m.canonical(original))


@pytest.mark.parametrize("mutation", ["description", "order", "duplicate", "missing", "two_system", "extra_catalog", "rules"])
def test_refuse_unproven_or_ambiguous_catalog_removal(mutation):
    value = body()
    if mutation == "description":
        value["tools"][0]["function"]["description"] = "different"
    elif mutation == "order":
        value["tools"].insert(0, {"type": "function", "function": {"name": "other", "description": "new"}})
    elif mutation == "duplicate":
        value["tools"].append(copy.deepcopy(value["tools"][0]))
    elif mutation == "missing":
        del value["tools"][0]["function"]["description"]
    elif mutation == "two_system":
        value["messages"].append(copy.deepcopy(value["messages"][0]))
    elif mutation == "extra_catalog":
        value["messages"][0]["content"] += "\nAVAILABLE TOOLS:\n"
    else:
        value["messages"][0]["content"] = value["messages"][0]["content"].replace("Always end the final answer", "Never end the final answer")
    with pytest.raises(ValueError):
        m.replace_duplicate_catalog(value)


def test_same_serialization_counts_unicode_bytes_not_characters_or_tokens():
    value = body()
    _, audit = m.replace_duplicate_catalog(value)
    assert len(m.canonical(value)) > len(m.canonical(value).decode())
    total = m.aggregate([audit, audit])
    assert total["requests"] == 2
    assert total["canonical_request_bytes_before"] == 2 * audit["canonical_request_bytes_before"]
    assert not any("tokens" in name or "cost" in name for name in total)
