import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import audit_student_context as diagnostic
import student_input as student


class RecordingTokenizer:
    def apply_chat_template(self, messages, *, tools, tokenize, add_generation_prompt, enable_thinking):
        assert tokenize is False and add_generation_prompt is True and enable_thinking is False
        self.history = copy.deepcopy(messages)
        self.tools = copy.deepcopy(tools)
        return json.dumps({"messages": messages, "tools": tools})

    def __call__(self, prefix, *, add_special_tokens, truncation):
        assert add_special_tokens is False and truncation is False
        self.prefix = prefix
        return {"input_ids": list(range(len(prefix)))}


def test_prompt_only_removes_reasoning_without_target_or_source_mutation():
    source = {"messages": [{"role": "system", "content": "instructions"},
                           {"role": "user", "content": "task"},
                           {"role": "assistant", "content": "visible", "reasoning_content": "private draft"},
                           {"role": "user", "content": "continue"}], "tools": []}
    original = copy.deepcopy(source)
    tokenizer = RecordingTokenizer()
    result = diagnostic.measure_request(tokenizer, source)
    assert source == original
    assert "private draft" not in tokenizer.prefix
    assert "input validation sentinel" not in tokenizer.prefix
    assert tokenizer.history[-1] == source["messages"][-1]
    assert result["history_messages"] == 4 and result["removed_reasoning_fields"] == 1
    assert result["prompt_tokens"] == len(tokenizer.prefix)
    assert not {"labels", "target", "assistant_target"} & result.keys()


def test_overlength_input_is_counted_without_cropping():
    tokenizer = RecordingTokenizer()
    text = "x" * 40000
    result = diagnostic.measure_request(tokenizer, {"messages": [{"role": "user", "content": text}]})
    assert result["prompt_tokens"] > 32768
    assert tokenizer.history[0]["content"] == text


@pytest.mark.parametrize("message", [
    {"role": "developer", "content": "hidden role"},
    {"role": "user", "content": "task", "unknown": "would be lost"},
])
def test_unsupported_wire_messages_are_rejected(message):
    with pytest.raises((student.InputRejected, diagnostic.converter.Excluded)):
        diagnostic.measure_request(RecordingTokenizer(), {"messages": [message]})


@pytest.mark.parametrize("destination", ["plan/report.json", "sealed/report.json", "tokenizer/report.json", "existing.json"])
def test_output_never_mutates_sealed_sources(tmp_path, destination):
    plan = tmp_path / "plan/source-plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"runs": [{"run": "sealed"}]}))
    (tmp_path / "existing.json").write_text("{}")
    with pytest.raises(ValueError, match="outside sealed"):
        diagnostic.validate_output(plan, tmp_path / destination, workspace=tmp_path, tokenizer_bundle=tmp_path / "tokenizer")
    diagnostic.validate_output(plan, tmp_path / "new/report.json", workspace=tmp_path, tokenizer_bundle=tmp_path / "tokenizer")
