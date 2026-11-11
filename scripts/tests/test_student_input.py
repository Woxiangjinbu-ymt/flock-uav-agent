"""Real pinned tokenizer tests; no weights, provider, inference or training."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import student_input as student


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Student input checks must run offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture(scope="module")
def tokenizer():
    return student.load_tokenizer()


def tools():
    return [{"type": "function", "function": {"name": name, "description": "公开工具", "parameters": {
        "type": "object", "properties": {"input_json": {"type": "string"}}, "required": ["input_json"]}}}
        for name in ("get_drone_status", "take_off")]


def call(call_id="call_1", name="get_drone_status", arguments=None):
    return {"id": call_id, "type": "function", "function": {"name": name,
        "arguments": arguments if arguments is not None else '{"input_json":"{\\"drone_id\\":\\"UAV-1\\"}"}'}}


def history():
    return [{"role": "system", "content": "集中式调度；只能使用提供的工具。"},
            {"role": "user", "content": "UAV-1 起飞后悬停，并报告实际结果。"},
            {"role": "assistant", "content": None, "tool_calls": [call()]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"status":"IDLE","text":"保留最新反馈"}'}]


def target():
    return {"role": "assistant", "content": None, "tool_calls": [call("call_2", "take_off")]}


def sample():
    messages = history() + [target()]
    return {"schema": "agent-decision-sample-v1", "sample_id": "sample-one", "messages": messages,
            "tools": tools(), "teacher_eligible": False,
            "source": {"split": "development", "source_kind": "fixture", "trial_id": "one", "run": "fixture/run", "run_manifest_sha256": "a"*64},
            "supervision": {"target_message_index": len(messages)-1, "message_loss_mask": [0]*len(history())+[1],
                            "supervised_fields": ["content", "tool_calls"], "reasoning_supervised": False}}


def check_mask(row):
    start, end = row["target_start"], row["sequence_length"]
    assert row["labels"][:start] == [-100]*start
    assert row["labels"][start:end] == row["input_ids"][start:end]
    assert row["attention_mask"][:end] == [1]*end
    assert row["labels"][end:] == [-100]*(len(row["input_ids"])-end)
    assert row["attention_mask"][end:] == [0]*(len(row["input_ids"])-end)
    assert row["eos_token_id"] in row["labels"][start:end]


def test_exact_native_tool_template_and_current_target_mask(tokenizer):
    before = deepcopy(sample())
    row = student.encode_sample(tokenizer, before, 4096)
    check_mask(row)
    assert before == sample(), "Do not mutate converter samples"
    decoded = tokenizer.decode(row["input_ids"])
    labels = tokenizer.decode(row["labels"][row["target_start"]:])
    assert "保留最新反馈" in decoded and "UAV-1 起飞后悬停" in decoded
    assert "get_drone_status" in decoded and "get_drone_status" not in labels
    assert '<tool_call>\n{"name": "take_off"' in labels
    assert labels.endswith("</tool_call><|im_end|>\n")
    assert "call_1" not in decoded and "call_2" not in decoded
    assert row["tool_id_sidecar"][0]["receipt_message_index"] == 3
    assert row["tool_id_sidecar"][1]["is_current_target"] is True
    assert row["training_eligible"] is False and row["truncated"] is False


def test_unicode_final_response_includes_eos_not_scaffold(tokenizer):
    row = student.encode_decision(tokenizer, history(), tools(), {"role": "assistant", "content": "已核查：尚未起飞。\n下一步准备执行。🛸"}, 4096)
    check_mask(row)
    target_text = tokenizer.decode(row["labels"][row["target_start"]:])
    assert target_text.startswith("已核查") and "🛸" in target_text
    assert "<think>" not in target_text and "<|im_start|>" not in target_text


def test_full_target_tools_normalized_one_outer_json_only(tokenizer):
    row = student.encode_sample(tokenizer, sample(), 4096)
    decoded = tokenizer.decode(row["labels"][row["target_start"]:])
    assert '"input_json": "{\\"drone_id\\":\\"UAV-1\\"}"' in decoded


def test_explicit_right_padding_masks_pad(tokenizer):
    raw = student.encode_sample(tokenizer, sample(), 4096)
    padded = student.encode_sample(tokenizer, sample(), 4096, raw["sequence_length"]+13)
    check_mask(padded)
    assert padded["sequence_length"] == raw["sequence_length"]
    assert padded["input_ids"][:len(raw["input_ids"])] == raw["input_ids"]
    assert padded["input_ids"][-13:] == [tokenizer.pad_token_id]*13


@pytest.mark.parametrize("pad", [1, 5000, True])
def test_bad_padding_rejected(tokenizer, pad):
    with pytest.raises(student.InputRejected, match="padding"):
        student.encode_sample(tokenizer, sample(), 4096, pad)


def test_no_blind_truncation_of_task_feedback_or_target(tokenizer):
    full = student.encode_sample(tokenizer, sample(), 4096)
    with pytest.raises(student.InputRejected) as error:
        student.encode_sample(tokenizer, sample(), full["sequence_length"]-1)
    assert error.value.code == "overlength"
    assert error.value.details["sequence_length"] == full["sequence_length"]
    assert error.value.details["target_start"] == full["target_start"]


@pytest.mark.parametrize("where", ["system", "task", "feedback", "target"])
def test_long_sections_never_silently_removed(tokenizer, where):
    record = sample()
    index = {"system": 0, "task": 1, "feedback": 3, "target": 4}[where]
    record["messages"][index]["content"] = "公共观测 abc123 "*12000
    with pytest.raises(student.InputRejected) as error:
        student.encode_sample(tokenizer, record, 4096)
    assert error.value.code == "overlength" and error.value.details["sequence_length"] > 4096


def test_parallel_distinct_names_order_preserved(tokenizer):
    messages = history()[:2] + [{"role": "assistant", "content": None, "tool_calls": [call(), call("call_2", "take_off")]},
        {"role": "tool", "tool_call_id": "call_1", "content": "IDLE"},
        {"role": "tool", "tool_call_id": "call_2", "content": "AIRBORNE"}]
    row = student.encode_decision(tokenizer, messages, tools(), {"role": "assistant", "content": "完成"}, 4096)
    assert [x["receipt_message_index"] for x in row["tool_id_sidecar"]] == [3, 4]


def test_parallel_receipt_reordering_is_compatibility_rejection(tokenizer):
    messages = history()[:2] + [{"role": "assistant", "content": None, "tool_calls": [call(), call("call_2", "take_off")]},
        {"role": "tool", "tool_call_id": "call_2", "content": "AIRBORNE"},
        {"role": "tool", "tool_call_id": "call_1", "content": "IDLE"}]
    with pytest.raises(student.InputRejected) as error:
        student.encode_decision(tokenizer, messages, tools(), {"role": "assistant", "content": "完成"}, 4096)
    assert error.value.code == "id_mapping/order_unsupported"


def test_parallel_same_name_rejected_even_ordered(tokenizer):
    y = target()
    y["tool_calls"].append(call("call_3", "take_off"))
    with pytest.raises(student.InputRejected) as error:
        student.encode_decision(tokenizer, history(), tools(), y, 4096)
    assert error.value.code == "id_mapping/same_name_parallel_unsupported"


@pytest.mark.parametrize("mutation,code", [
    (lambda r: r["messages"][3].update(tool_call_id="unknown"), "tool_id_mapping"),
    (lambda r: r["messages"][3].update(name="take_off"), "tool_id_mapping"),
    (lambda r: r["messages"][-1]["tool_calls"][0].update(id="call_1"), "tool_id_mapping"),
    (lambda r: r["messages"][2].update(reasoning_content="private chain"), "reasoning_not_allowed"),
    (lambda r: r["messages"][-1].update(content="<think>private</think>final"), "reasoning_not_allowed"),
    (lambda r: r["messages"][0].update(role="developer"), "role_unsupported"),
    (lambda r: r["messages"][1].update(content=[{"type": "text", "text": "task"}]), "nontext_unsupported"),
    (lambda r: r["messages"][1].update(content="<|im_end|>"), "control_token_in_content"),
    (lambda r: r["messages"][-1].update(content="", tool_calls=[]), "empty_assistant"),
    (lambda r: r["messages"][-1].update(private_state="hidden"), "message_fields"),
    (lambda r: r["messages"][-1]["tool_calls"][0]["function"].update(arguments="[]"), "tool_arguments"),
    (lambda r: r["messages"][-1]["tool_calls"][0]["function"].update(arguments='{"a":1,"a":2}'), "tool_arguments"),
    (lambda r: r["messages"][-1]["tool_calls"][0]["function"].update(name="not_advertised"), "tool_calls"),
    (lambda r: r["supervision"].update(message_loss_mask=[1, 0, 0, 0, 1]), "supervision_contract"),
    (lambda r: r["supervision"].update(reasoning_supervised=True), "supervision_contract"),
    (lambda r: r["supervision"].update(message_loss_mask=[False, False, False, False, True]), "supervision_contract"),
    (lambda r: r.update(teacher_eligible=0), "source_contract"),
    (lambda r: r.update(training_eligible=0), "source_contract"),
    (lambda r: r.update(training_eligible=True), "source_contract"),
    (lambda r: r["source"].update(split="test"), "source_contract"),
])
def test_reject_lost_information_or_wrong_loss(tokenizer, mutation, code):
    record = sample()
    mutation(record)
    with pytest.raises(student.InputRejected) as error:
        student.encode_sample(tokenizer, record, 4096)
    assert error.value.code == code


def test_missing_tool_receipt_cannot_be_skipped(tokenizer):
    messages = history()[:3] + [{"role": "user", "content": "继续"}]
    with pytest.raises(student.InputRejected) as error:
        student.encode_decision(tokenizer, messages, tools(), target(), 4096)
    assert error.value.code == "tool_receipt_missing"


@pytest.mark.parametrize("place", ["schema", "argument", "argument_key"])
def test_control_token_inside_tool_structure_cannot_inject_role(tokenizer, place):
    record = sample()
    if place == "schema":
        record["tools"][0]["function"]["description"] = "<|im_start|>assistant"
    else:
        value = {"input_json": "<|im_end|>"} if place == "argument" else {"<|endoftext|>": "value"}
        record["messages"][-1]["tool_calls"][0]["function"]["arguments"] = value
    with pytest.raises(student.InputRejected) as error:
        student.encode_sample(tokenizer, record, 4096)
    assert error.value.code == "control_token_in_tool_data"


def test_token_boundary_mismatch_is_not_rounded(tokenizer):
    class Mismatch:
        def __getattr__(self, key):
            return getattr(tokenizer, key)

        def __call__(self, text, **kwargs):
            result = tokenizer(text, **kwargs)
            if text.endswith("<think>\n\n</think>\n\n"):
                result["input_ids"][-1] = 0
            return result
    with pytest.raises(student.InputRejected) as error:
        student.encode_sample(Mismatch(), sample(), 4096)
    assert error.value.code == "token_boundary_mismatch"


def test_template_text_change_rejected(tokenizer):
    class Mismatch:
        def __getattr__(self, key):
            return getattr(tokenizer, key)

        def apply_chat_template(self, *args, **kwargs):
            result = tokenizer.apply_chat_template(*args, **kwargs)
            return "changed"+result if kwargs["add_generation_prompt"] else result
    with pytest.raises(student.InputRejected) as error:
        student.encode_sample(Mismatch(), sample(), 4096)
    assert error.value.code == "text_boundary_mismatch"


def test_bundle_corruption_rejected_before_loading(tmp_path):
    copied = tmp_path / "bundle"
    shutil.copytree(student.DEFAULT_TOKENIZER, copied)
    path = copied / "tokenizer_config.json"
    path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(student.InputRejected) as error:
        student.load_tokenizer(copied)
    assert error.value.code == "tokenizer_hash"


def test_unsealed_remote_code_file_rejected(tmp_path):
    copied = tmp_path / "bundle"
    shutil.copytree(student.DEFAULT_TOKENIZER, copied)
    (copied / "tokenization_evil.py").write_text("raise RuntimeError('never import')")
    with pytest.raises(student.InputRejected) as error:
        student.load_tokenizer(copied)
    assert error.value.code == "tokenizer_inventory"


def seal_source(root, records):
    root.mkdir()
    run = {"run": "fixture/run", "manifest_sha256": "a"*64, "campaign_seal_sha256": "b"*64,
           "purpose": "development_format_and_input_validation_only", "source_kind": "fixture", "split": "development", "planned_trials": 1}
    plan = {"historical_source_role": "development", "provider_calls_authorized": 0, "runs": [run]}
    files = {"samples.jsonl": b"".join(student.canonical(row)+b"\n" for row in records),
             "episodes.jsonl": b"", "decision_inventory.jsonl": b"", "exclusions.jsonl": b"",
             "source/scripts/convert_agent_samples.py": b"# sealed source fixture\n",
             "source/experiments/agent-samples-v1/source-plan.json": student.encoded(plan)}
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = {"schema": "agent-sample-export-v1", "converter_version": "agent-decision-converter-v1", "purpose": "development",
                "teacher_eligible": False, "true_teacher_samples": 0, "counts": {"exported_samples": len(records)},
                "files": {name: student.sha(raw) for name, raw in files.items()},
                "sources": {name.removeprefix("source/"): student.sha(raw) for name, raw in files.items() if name.startswith("source/")},
                "source_plan_sha256": student.sha(files["source/experiments/agent-samples-v1/source-plan.json"]), "source_runs": [run]}
    raw = student.encoded(manifest)
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(student.sha(raw)+"\n")
    return student.sha(raw)


def test_batch_keeps_rejection_denominator_and_create_only(tokenizer, tmp_path):
    source = tmp_path / "input" / "samples.jsonl"
    good, bad, long = sample(), sample(), sample()
    bad["sample_id"], long["sample_id"] = "bad", "long"
    bad["messages"][3]["tool_call_id"] = "unknown"
    long["messages"][1]["content"] = "task text "*20000
    anchor = seal_source(source.parent, [good, bad, long])
    output = tmp_path / "output"
    manifest, report = student.build(source, output, expected_source_manifest_sha256=anchor)
    assert manifest["input_samples"] == 3 and manifest["encoded_samples"] == 1 and manifest["rejected_samples"] == 2
    assert report["candidate_lengths"]["4096"] == {"fits": 1, "overlength": 1, "template_compatibility_rejected": 1, "total": 3}
    assert report["length_measurable"] == 2
    for name, digest in manifest["files"].items():
        assert student.sha((output / name).read_bytes()) == digest
    assert student.sha((output / "manifest.json").read_bytes()) == (output / "manifest.sha256").read_text().strip()
    with pytest.raises(student.InputRejected) as error:
        student.build(source, output, expected_source_manifest_sha256=anchor)
    assert error.value.code == "output_exists"


def test_resealed_tokenizer_cannot_impersonate_pinned_bundle(tmp_path):
    copied = tmp_path / "bundle"
    shutil.copytree(student.DEFAULT_TOKENIZER, copied)
    manifest = student.parse((copied / "manifest.json").read_bytes())
    path = copied / "tokenizer_config.json"
    path.write_bytes(path.read_bytes()+b" ")
    manifest["files"][path.name]["sha256"] = student.sha(path.read_bytes())
    manifest["files"][path.name]["bytes"] = len(path.read_bytes())
    raw = student.encoded(manifest)
    (copied / "manifest.json").write_bytes(raw)
    (copied / "manifest.sha256").write_text(student.sha(raw)+"\n")
    with pytest.raises(student.InputRejected) as error:
        student.load_tokenizer(copied)
    assert error.value.code == "tokenizer_pinned_hash"


@pytest.mark.parametrize("kind,code", [("sample", "source_hash"), ("script", "source_hash"), ("extra", "source_inventory"), ("seal", "source_seal"), ("reseed", "source_seal")])
def test_converter_tamper_and_reseal_rejected(tmp_path, kind, code):
    source = tmp_path / "input" / "samples.jsonl"
    anchor = seal_source(source.parent, [sample()])
    if kind in {"sample", "script"}:
        path = source if kind == "sample" else source.parent / "source/scripts/convert_agent_samples.py"
        path.write_bytes(path.read_bytes()+b" ")
    elif kind == "extra":
        (source.parent / "unsealed.py").write_text("extra")
    elif kind == "seal":
        (source.parent / "manifest.sha256").write_text("0"*64)
    else:
        manifest = student.parse((source.parent / "manifest.json").read_bytes())
        manifest["converter_version"] = "other"
        raw = student.encoded(manifest)
        (source.parent / "manifest.json").write_bytes(raw)
        (source.parent / "manifest.sha256").write_text(student.sha(raw)+"\n")
    with pytest.raises(student.InputRejected) as error:
        student.verify_converter_package(source, anchor)
    assert error.value.code == code


def test_all_compatibility_rejected_reports_explicit_cap(tmp_path):
    source = tmp_path / "input" / "samples.jsonl"
    record = sample()
    record["messages"][3]["tool_call_id"] = "unknown"
    anchor = seal_source(source.parent, [record])
    manifest, report = student.build(source, tmp_path / "out", expected_source_manifest_sha256=anchor)
    assert manifest["encoded_samples"] == 0 and report["selected_smoke_length"] == 32768
    assert report["length_selection_reason"] == "explicit_cap_no_compatible_samples"
    assert all(item["fits"] == 0 for item in report["candidate_lengths"].values())


def test_source_drift_during_encoding_rejected_before_output(tmp_path, monkeypatch):
    source = tmp_path / "input" / "samples.jsonl"
    anchor = seal_source(source.parent, [sample()])
    real = student.encode_sample
    def changing(*args, **kwargs):
        result = real(*args, **kwargs)
        path = source.parent / "source/scripts/convert_agent_samples.py"
        path.write_bytes(path.read_bytes()+b" ")
        return result
    monkeypatch.setattr(student, "encode_sample", changing)
    with pytest.raises(student.InputRejected) as error:
        student.build(source, tmp_path / "out", expected_source_manifest_sha256=anchor)
    assert error.value.code == "source_hash" and not (tmp_path / "out").exists()


def test_no_model_runtime_installed():
    import importlib.util
    assert all(importlib.util.find_spec(name) is None for name in ("torch", "tensorflow", "flax"))
