"""Auditable, tokenizer-only student input preparation; never loads a model.

The v1 adapter keeps the full input or rejects it. Its loss covers only the
current assistant continuation, including tool calls and the closing EOS.
Native Qwen tool IDs are absent from the rendered text: IDs remain in a sidecar
and receipt order is checked rather than silently changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
DEFAULT_TOKENIZER = ROOT / "training/tokenizers/qwen3-1.7b-input-v1"
REPO_ID = "Qwen/Qwen3-1.7B"
REVISION = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
TOKENIZER_MANIFEST_SHA256 = "ec876e64316fdb66a771cb6e19eafd76268ee4af3a308b294616ac82288a10e8"
TOKENIZER_FILES = {"LICENSE", "README.md", "config.json", "generation_config.json",
                   "merges.txt", "tokenizer.json", "tokenizer_config.json", "vocab.json"}
LENGTHS = (4096, 8192, 16384, 32768)
RUNTIME = {"transformers": "4.57.3", "tokenizers": "0.22.1", "huggingface_hub": "0.36.0", "jinja2": "3.1.6"}
ADAPTER = "qwen3-current-assistant-no-thinking-v1"
CONTROL_MARKERS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>")


class InputRejected(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def require(condition, code, message, **details):
    if not condition:
        raise InputRejected(code, message, **details)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def runtime_versions():
    return {name: importlib.metadata.version(name) for name in RUNTIME}


def verify_dependency_lock():
    for line in (ROOT / "training/requirements-input.lock").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        require(importlib.metadata.version(name) == expected, "runtime_versions", "Dependency differs from the independent input lock", dependency=name)


def verify_tokenizer_bundle(bundle=DEFAULT_TOKENIZER):
    """Verify a fixed whitelist before importing/loading the local tokenizer."""
    bundle = Path(bundle).resolve()
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    require((bundle / "manifest.sha256").read_text().strip() == sha(manifest_bytes), "tokenizer_seal", "Tokenizer manifest hash mismatch")
    require(sha(manifest_bytes) == TOKENIZER_MANIFEST_SHA256, "tokenizer_pinned_hash", "Tokenizer manifest differs from the audited official small-file download")
    manifest = parse(manifest_bytes)
    require(manifest.get("schema") == "student-tokenizer-bundle-v1" and manifest.get("repo_id") == REPO_ID
            and manifest.get("revision") == REVISION, "tokenizer_identity", "Unexpected tokenizer candidate or revision")
    require(manifest.get("downloaded_weights") is False and manifest.get("trust_remote_code") is False,
            "tokenizer_scope", "This bundle must contain no weights or remote code")
    require(set(manifest.get("files", {})) == TOKENIZER_FILES, "tokenizer_inventory", "Unexpected tokenizer file inventory")
    require({p.name for p in bundle.iterdir()} == TOKENIZER_FILES | {"manifest.json", "manifest.sha256"},
            "tokenizer_inventory", "Unexpected unsealed tokenizer file")
    for name, expected in manifest["files"].items():
        path = bundle / name
        require(path.is_file() and not path.is_symlink(), "tokenizer_inventory", "Nonregular tokenizer file", file=name)
        raw = path.read_bytes()
        require(len(raw) == expected["bytes"] and sha(raw) == expected["sha256"], "tokenizer_hash", "Tokenizer file hash mismatch", file=name)
        require(expected["url"] == f"https://huggingface.co/{REPO_ID}/resolve/{REVISION}/{name}",
                "tokenizer_source", "Tokenizer source must use the exact official commit", file=name)
    config = parse((bundle / "config.json").read_bytes())
    require(config.get("model_type") == "qwen3" and config.get("max_position_embeddings") == 40960,
            "tokenizer_config", "Unexpected candidate context configuration")
    tok_config = parse((bundle / "tokenizer_config.json").read_bytes())
    require(isinstance(tok_config.get("chat_template"), str) and not tok_config.get("auto_map"),
            "tokenizer_config", "Template missing or remote code requested")
    return manifest


def load_tokenizer(bundle=DEFAULT_TOKENIZER):
    verify_tokenizer_bundle(bundle)
    require(runtime_versions() == RUNTIME, "runtime_versions", "Use the pinned independent tokenizer environment", expected=RUNTIME)
    verify_dependency_lock()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(Path(bundle).resolve()), local_files_only=True,
                                               trust_remote_code=False, use_fast=True)
    require(tokenizer.is_fast and tokenizer.pad_token_id is not None and tokenizer.eos_token_id is not None,
            "tokenizer_config", "Fast tokenizer and explicit padding/EOS are required")
    # The official template has no generation tags. Never treat its all-zero
    # assistant_tokens_mask as a valid assistant-only supervision mask.
    require(not re.search(r"\{%[- ]*generation", tokenizer.chat_template), "template_changed", "Re-audit masking after template changes")
    return tokenizer


def normalize_input(messages, tools, target):
    """Reject unsupported/lossy cases; return canonical messages and ID sidecar."""
    require(isinstance(messages, list) and messages, "messages", "Nonempty history is required")
    require(isinstance(tools, list), "tools", "Tools must be an array")
    require(not any(marker in canonical(tools).decode() for marker in CONTROL_MARKERS),
            "control_token_in_tool_data", "Tool schemas cannot contain raw chat control tokens")
    names = set()
    for tool in tools:
        require(isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("function"), dict),
                "tools", "Only function tool JSON schemas are supported")
        function = tool["function"]
        name = function.get("name")
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name) and name not in names, "tools", "Tool names must be unique ASCII function names")
        require(isinstance(function.get("parameters"), dict), "tools", "Tool parameters must be a JSON schema object")
        names.add(name)
    require(isinstance(target, dict) and target.get("role") == "assistant", "target", "Current target must be an assistant message")
    require(isinstance(messages[-1], dict) and messages[-1].get("role") in {"user", "tool"}, "target_boundary", "History must end at a user or complete tool response")
    normalized, links, pending, seen = [], [], [], set()
    saw_user = False
    for index, original in enumerate(messages + [target]):
        require(isinstance(original, dict), "messages", "Every message must be an object")
        message = deepcopy(original)
        role = message.get("role")
        require(role in {"system", "user", "assistant", "tool"}, "role_unsupported", "Only text system/user/assistant/tool roles are supported", message_index=index)
        require(not message.get("reasoning_content") and not message.get("reasoning"), "reasoning_not_allowed", "Reasoning must be removed by the audited converter", message_index=index)
        # Even empty unknown fields can be silently ignored by Jinja. Reject
        # them, except the explicitly removed empty reasoning fields.
        message.pop("reasoning_content", None)
        message.pop("reasoning", None)
        allowed = {"role", "content"} | ({"tool_calls"} if role == "assistant" else set()) | ({"tool_call_id", "name"} if role == "tool" else set())
        require(set(message) <= allowed, "message_fields", "Unsupported message field would be lost in template", message_index=index, fields=sorted(set(message) - allowed))
        content = message.get("content")
        if role == "assistant" and content is None:
            content = ""
        require(isinstance(content, str), "nontext_unsupported", "Only text content is supported", message_index=index)
        message["content"] = content
        require(not any(marker in content for marker in CONTROL_MARKERS),
                "control_token_in_content", "Raw chat control tokens are not valid message content", message_index=index)
        if role == "assistant":
            require("<think>" not in content and "</think>" not in content, "reasoning_not_allowed", "Embedded assistant thinking markup must be handled before encoding", message_index=index)
        if role == "system":
            require(index == 0, "system_position", "Only an initial system message is supported")
        if role == "user":
            saw_user = True
        if role != "tool":
            require(not pending, "tool_receipt_missing", "Every previous tool call must have its receipt before the next message", message_index=index)
        if role == "tool":
            call_id = message.get("tool_call_id")
            require(pending and call_id in [item["tool_call_id"] for item in pending], "tool_id_mapping", "Tool receipt has no matching pending call", message_index=index)
            require(call_id == pending[0]["tool_call_id"], "id_mapping/order_unsupported", "Native Qwen template drops IDs; reordered parallel receipts are unsupported", message_index=index)
            link = pending.pop(0)
            require("name" not in message or message["name"] == link["name"], "tool_id_mapping", "Receipt tool name differs from its call", message_index=index)
            link["receipt_message_index"] = index
            link["receipt_content_sha256"] = sha(content.encode())
        if role == "assistant":
            calls = message.get("tool_calls") or []
            require(isinstance(calls, list), "tool_calls", "Assistant tool_calls must be an array", message_index=index)
            require(content.strip() or calls, "empty_assistant", "Assistant has neither text nor tool calls", message_index=index)
            call_names = []
            for call in calls:
                require(isinstance(call, dict) and set(call) <= {"id", "type", "function"} and call.get("type") == "function" and isinstance(call.get("function"), dict),
                        "tool_calls", "Only OpenAI function calls are supported", message_index=index)
                call_id = call.get("id")
                require(isinstance(call_id, str) and call_id and call_id not in seen, "tool_id_mapping", "Tool call IDs must be nonempty and unique", message_index=index)
                seen.add(call_id)
                function = call["function"]
                require(set(function) == {"name", "arguments"} and function["name"] in names, "tool_calls", "Tool must match the advertised schema", message_index=index)
                args = function["arguments"]
                if isinstance(args, str):
                    try:
                        args = parse(args)
                    except (ValueError, TypeError) as exc:
                        raise InputRejected("tool_arguments", "Tool arguments are not strict JSON", message_index=index) from exc
                require(isinstance(args, dict), "tool_arguments", "Function arguments must be a JSON object", message_index=index)
                arguments_json = canonical(args).decode()  # also rejects NaN in programmatic callers
                require(not any(marker in arguments_json for marker in CONTROL_MARKERS),
                        "control_token_in_tool_data", "Tool arguments cannot contain raw chat control tokens", message_index=index)
                function["arguments"] = args
                call_names.append(function["name"])
                link = {"call_message_index": index, "tool_call_id": call_id, "name": function["name"],
                        "arguments_sha256": sha(canonical(args)), "receipt_message_index": None,
                        "is_current_target": index == len(messages)}
                links.append(link)
                pending.append(link)
            require(len(call_names) == len(set(call_names)), "id_mapping/same_name_parallel_unsupported",
                    "Native Qwen template drops IDs; same-name parallel calls are ambiguous", message_index=index)
            if calls:
                message["tool_calls"] = calls
            else:
                message.pop("tool_calls", None)
        normalized.append(message)
    require(saw_user, "task_missing", "At least one user task message is required")
    return normalized[:-1], deepcopy(tools), normalized[-1], links


def encode_decision(tokenizer, messages, tools, target, max_length, pad_to_length=None):
    """Tokenize a full decision and prove the label boundary in actual tokens."""
    require(type(max_length) is int and 1 <= max_length <= 32768, "length_budget", "Length must be within this adapter's conservative 1..32768 input cap")
    original_hash = sha(canonical({"messages": messages, "tools": tools, "target": target}))
    history, tools, target, links = normalize_input(messages, tools, target)
    kwargs = {"tools": tools, "tokenize": False, "enable_thinking": False}
    prefix = tokenizer.apply_chat_template(history, add_generation_prompt=True, **kwargs)
    full = tokenizer.apply_chat_template(history + [target], add_generation_prompt=False, **kwargs)
    require(full.startswith(prefix), "text_boundary_mismatch", "Template changed the prompt when adding the target")
    # Never use tokenizer truncation/padding here; exact correspondence is
    # checked before length admission and explicit right padding.
    prefix_ids = tokenizer(prefix, add_special_tokens=False, truncation=False)["input_ids"]
    input_ids = tokenizer(full, add_special_tokens=False, truncation=False)["input_ids"]
    require(input_ids[:len(prefix_ids)] == prefix_ids, "token_boundary_mismatch", "BPE boundary crosses prompt and target; masking would be ambiguous")
    start, length = len(prefix_ids), len(input_ids)
    require(start < length and tokenizer.eos_token_id in input_ids[start:], "empty_supervision", "Target needs nonempty labels and its EOS")
    require(length <= max_length, "overlength", "Full decision exceeds the admitted length; nothing was truncated",
            sequence_length=length, target_start=start, supervised_tokens=length-start, max_length=max_length)
    labels = [-100] * start + input_ids[start:]
    attention = [1] * length
    if pad_to_length is not None:
        require(type(pad_to_length) is int and length <= pad_to_length <= max_length,
                "padding", "Right padding length must include the full input and stay within max_length")
        count = pad_to_length - length
        input_ids += [tokenizer.pad_token_id] * count
        attention += [0] * count
        labels += [-100] * count
    return {"schema": "student-encoded-decision-v1", "adapter": ADAPTER,
            "input_ids": input_ids, "attention_mask": attention, "labels": labels,
            "target_start": start, "sequence_length": length, "supervised_tokens": length-start,
            "max_length": max_length, "pad_token_id": tokenizer.pad_token_id, "eos_token_id": tokenizer.eos_token_id,
            "training_eligible": False, "truncated": False, "reasoning_supervised": False,
            "input_sha256": original_hash, "rendered_text_sha256": sha(full.encode()),
            "template_sha256": sha(tokenizer.chat_template.encode()),
            "tool_id_sidecar": links, "tool_id_serialized": False,
            "tool_association": "validated ordered calls/receipts; same-name parallel rejected",
            "label_scope": "current assistant continuation including tool-call text, EOS and template trailing newline; prompt/header/empty-think scaffold masked"}


def encode_sample(tokenizer, sample, max_length, pad_to_length=None):
    require(isinstance(sample, dict) and sample.get("schema") == "agent-decision-sample-v1", "sample_schema", "Unexpected converter sample schema")
    sample_id, messages, supervision = sample.get("sample_id"), sample.get("messages"), sample.get("supervision")
    require(isinstance(sample_id, str) and sample_id, "sample_schema", "Sample ID is required")
    require(isinstance(messages, list) and len(messages) >= 2 and isinstance(supervision, dict), "sample_schema", "History, target and supervision are required")
    loss_mask = supervision.get("message_loss_mask")
    require(type(supervision.get("target_message_index")) is int
            and supervision.get("target_message_index") == len(messages)-1
            and isinstance(loss_mask, list) and all(type(value) is int for value in loss_mask)
            and supervision.get("message_loss_mask") == [0] * (len(messages)-1) + [1]
            and supervision.get("reasoning_supervised") is False
            and supervision.get("supervised_fields") == ["content", "tool_calls"],
            "supervision_contract", "Only the final assistant content/tool_calls may be supervised")
    require(sample.get("teacher_eligible") is False and ("training_eligible" not in sample or sample["training_eligible"] is False),
            "source_contract", "Development samples require explicit false teacher eligibility and cannot enable training")
    source = sample.get("source")
    require(isinstance(source, dict) and source.get("split") == "development" and isinstance(source.get("source_kind"), str),
            "source_contract", "This adapter accepts audited development smoke samples only")
    result = encode_decision(tokenizer, messages[:-1], sample.get("tools"), messages[-1], max_length, pad_to_length)
    result.update({"sample_id": sample_id, "source": deepcopy(source), "source_sample_sha256": sha(canonical(sample))})
    return result


def _stats(values):
    if not values:
        return {"count": 0, "min": None, "max": None, "p50": None, "p95": None}
    values = sorted(values)
    return {"count": len(values), "min": values[0], "max": values[-1],
            "p50": values[(len(values)-1)//2], "p95": values[max(0, (95*len(values)+99)//100-1)]}


def verify_converter_package(samples_file, expected_manifest_sha256):
    """Validate a caller-pinned conversion seal and all copied source hashes.

    This is package integrity, not a second audit of the original wire episodes.
    The original-run audits and converter's independent review remain separate.
    """
    samples_file = Path(samples_file).resolve()
    root = samples_file.parent
    require(samples_file.name == "samples.jsonl", "source_package", "Use the converter's samples.jsonl inside its sealed package")
    require(isinstance(expected_manifest_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256),
            "source_anchor", "An explicit trusted converter manifest SHA-256 is required")
    raw = (root / "manifest.json").read_bytes()
    require(sha(raw) == expected_manifest_sha256 and (root / "manifest.sha256").read_text().strip() == expected_manifest_sha256,
            "source_seal", "Converter seal differs from its supplied anchor")
    manifest = parse(raw)
    require(manifest.get("schema") == "agent-sample-export-v1" and manifest.get("converter_version") == "agent-decision-converter-v1"
            and manifest.get("purpose") == "development" and manifest.get("teacher_eligible") is False
            and type(manifest.get("true_teacher_samples")) is int and manifest["true_teacher_samples"] == 0,
            "source_contract", "Unsupported converter version, purpose or eligibility")
    files, sources = manifest.get("files"), manifest.get("sources")
    require(isinstance(files, dict) and {"samples.jsonl", "episodes.jsonl", "decision_inventory.jsonl", "exclusions.jsonl"} <= set(files)
            and isinstance(sources, dict) and bool(sources), "source_inventory", "Converter inventory is incomplete")
    actual = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), "source_inventory", "Symlinks are forbidden in a converter package")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    require(actual == set(files) | {"manifest.json", "manifest.sha256"}, "source_inventory", "Unsealed or missing converter files")
    for name, expected in files.items():
        path = Path(name)
        require(not path.is_absolute() and path.as_posix() == name and ".." not in path.parts and name not in {"", "."},
                "source_inventory", "Unsafe converter file path")
        require(isinstance(expected, str) and sha((root / path).read_bytes()) == expected, "source_hash", "Converter file hash mismatch", file=name)
    for name, expected in sources.items():
        require(files.get("source/" + name) == expected, "source_hash", "Converter source version hash mismatch", file=name)
    plan_file = "source/experiments/agent-samples-v1/source-plan.json"
    require(files.get(plan_file) == manifest.get("source_plan_sha256"), "source_hash", "Converter source plan hash mismatch")
    plan = parse((root / plan_file).read_bytes())
    require(plan.get("historical_source_role") == "development" and type(plan.get("provider_calls_authorized")) is int
            and plan["provider_calls_authorized"] == 0, "source_contract", "Unexpected source plan authority")
    source_runs = manifest.get("source_runs")
    require(isinstance(source_runs, list) and bool(source_runs) and len({row["run"] for row in source_runs}) == len(source_runs),
            "source_contract", "Source run identities are required and must be unique")
    planned_runs = {row["run"]: row for row in plan.get("runs", [])}
    require(set(planned_runs) == {row["run"] for row in source_runs}, "source_contract", "Source run set differs from copied plan")
    for run in source_runs:
        require(run.get("split") == "development" and run.get("purpose") == "development_format_and_input_validation_only",
                "source_contract", "Source run must remain development")
        require(all(run.get(key) == planned_runs[run["run"]].get(key) for key in ("manifest_sha256", "campaign_seal_sha256", "planned_trials")),
                "source_contract", "Run anchor differs from copied source plan")
    return manifest


def build(samples_file, output, tokenizer_bundle=DEFAULT_TOKENIZER, max_length=None, *, expected_source_manifest_sha256=None):
    """Encode local converted samples without altering the source or its seal."""
    raw_samples, raw_output = Path(samples_file), Path(output)
    require(not any(path.is_symlink() for path in (raw_samples, *raw_samples.parents, raw_output, *raw_output.parents)),
            "path_symlink", "Input/output paths cannot use symlinks")
    samples_file, output, bundle = raw_samples.resolve(), raw_output.resolve(), Path(tokenizer_bundle).resolve()
    require(not output.exists(), "output_exists", "Output directory must not exist")
    require(not output.is_relative_to(samples_file.parent) and not output.is_relative_to(bundle),
            "output_location", "Output must be outside the input sample/tokenizer directories")
    require(max_length is None or max_length in LENGTHS, "length_budget", "Choose one of the four audited candidate lengths")
    source_manifest = verify_converter_package(samples_file, expected_source_manifest_sha256)
    frozen_source_names = ("scripts/student_input.py", "training/requirements-input.in", "training/requirements-input.lock", "training/README_INPUT_CN.md")
    frozen_sources = {name: (ROOT / name).read_bytes() for name in frozen_source_names}
    source_bytes = samples_file.read_bytes()
    source_samples = [parse(line) for line in source_bytes.splitlines() if line.strip()]
    require(bool(source_samples) and all(isinstance(item, dict) for item in source_samples), "sample_schema", "Input must contain JSON object samples")
    ids = [item.get("sample_id") for item in source_samples]
    require(all(isinstance(item, str) and item for item in ids) and len(ids) == len(set(ids)), "sample_ids", "Missing or duplicate sample IDs")
    require(type(source_manifest.get("counts", {}).get("exported_samples")) is int and len(source_samples) == source_manifest["counts"]["exported_samples"],
            "source_count", "Sample count differs from converter manifest")
    runs = {run["run"]: run for run in source_manifest["source_runs"]}
    for sample in source_samples:
        source = sample.get("source", {})
        require(source.get("run") in runs and all(source.get(key) == runs[source["run"]].get(other)
                for key, other in (("run_manifest_sha256", "manifest_sha256"), ("source_kind", "source_kind"), ("split", "split"))),
                "source_contract", "Sample provenance differs from sealed source run")
    tokenizer = load_tokenizer(bundle)
    records, rejected = [], []
    for index, sample in enumerate(source_samples):
        try:
            records.append(encode_sample(tokenizer, sample, 32768))
        except InputRejected as exc:
            rejected.append({"sample_id": sample.get("sample_id"), "source": sample.get("source", {}), "line": index+1,
                             "category": "overlength" if exc.code == "overlength" else "template_compatibility",
                             "code": exc.code, "message": str(exc), **exc.details})
    # Choose the smallest candidate containing every compatible <=32k sample.
    # Any >32k record remains rejected and counted; do not crop it to fit.
    chosen = max_length or (next(limit for limit in LENGTHS if all(row["sequence_length"] <= limit for row in records)) if records else 32768)
    for row in records:
        row["max_length"] = chosen
    accepted = [row for row in records if row["sequence_length"] <= chosen]
    for row in records:
        if row["sequence_length"] > chosen:
            rejected.append({"sample_id": row["sample_id"], "source": row["source"], "category": "overlength", "code": "overlength",
                             "sequence_length": row["sequence_length"], "max_length": chosen})
    measured = [{"sample_id": row["sample_id"], "source_kind": row["source"]["source_kind"],
                 "sequence_length": row["sequence_length"], "target_start": row["target_start"], "supervised_tokens": row["supervised_tokens"]} for row in records]
    measured += [{key: row[key] for key in ("sample_id", "sequence_length", "target_start", "supervised_tokens")}
                 | {"source_kind": row["source"].get("source_kind")} for row in rejected if row["code"] == "overlength" and row.get("sequence_length", 0) > 32768]
    report = {"schema": "student-input-length-report-v1", "samples_total": len(source_samples),
              "template_compatibility_rejected": sum(row["category"] == "template_compatibility" for row in rejected),
              "official_card_context_tokens": 32768, "pinned_config_max_position_embeddings": 40960,
              "adapter_max_length": 32768,
              "length_measurable": len(measured), "selected_smoke_length": chosen, "training_context_frozen": False,
              "length_selection_reason": "explicit_cap_no_compatible_samples" if not records else ("explicit_requested_candidate" if max_length is not None else "smallest_candidate_fitting_all_compatible_samples_at_or_below_32768"),
              "encoded_samples": len(accepted), "rejections": len(rejected),
              "rejection_codes": dict(Counter(row["code"] for row in rejected)),
              "sequence_tokens": _stats([row["sequence_length"] for row in measured]),
              "target_tokens": _stats([row["supervised_tokens"] for row in measured]),
              "candidate_lengths": {str(limit): {"fits": sum(row["sequence_length"] <= limit for row in measured),
                                                   "overlength": sum(row["sequence_length"] > limit for row in measured),
                                                   "template_compatibility_rejected": sum(row["category"] == "template_compatibility" for row in rejected),
                                                   "total": len(source_samples)} for limit in LENGTHS},
              "by_source_kind": {kind: {"tokens": _stats([row["sequence_length"] for row in measured if row["source_kind"] == kind]),
                                        "fits": {str(limit): sum(row["sequence_length"] <= limit for row in measured if row["source_kind"] == kind) for limit in LENGTHS}}
                                 for kind in sorted({row["source_kind"] for row in measured})},
              "measurements": measured,
              "limitations": ["Development input compatibility only; no model weights, inference, training, GPU or quality claim",
                              "Whole-input rejection; no truncation, packing, schema compression or tool removal",
                              "IDs retained in sidecar, not Qwen native token text; ordered distinct-name parallel only",
                              "Only current assistant supervised; teacher reasoning excluded from inputs and labels"]}
    # Detect source/config drift before producing an immutable new directory.
    require(samples_file.read_bytes() == source_bytes, "source_drift", "Sample input changed during encoding")
    verify_converter_package(samples_file, expected_source_manifest_sha256)
    verify_tokenizer_bundle(bundle)
    require(all((ROOT / name).read_bytes() == raw for name, raw in frozen_sources.items()), "source_drift", "Input implementation or protocol changed during encoding")
    output.mkdir(parents=True)
    files = {
        "encoded.jsonl": b"".join(canonical(row) + b"\n" for row in accepted),
        "rejections.jsonl": b"".join(canonical(row) + b"\n" for row in rejected),
        "length-report.json": encoded(report),
    }
    files.update({"source/" + name: raw for name, raw in frozen_sources.items()})
    files["source/converter-manifest.json"] = (samples_file.parent / "manifest.json").read_bytes()
    files["source/tokenizer-manifest.json"] = (bundle / "manifest.json").read_bytes()
    for name, raw in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = {"schema": "student-input-bundle-v1", "adapter": ADAPTER, "purpose": "development_input_smoke",
                "training_eligible": False, "model_candidate_only": True, "model_weights_loaded": False,
                "source_samples": str(samples_file), "source_samples_sha256": sha(source_bytes),
                "source_converter_manifest_sha256": expected_source_manifest_sha256,
                "tokenizer_bundle": str(bundle), "tokenizer_manifest_sha256": sha((bundle / "manifest.json").read_bytes()),
                "tokenizer_repo_id": REPO_ID, "tokenizer_revision": REVISION,
                "runtime_versions": runtime_versions(), "python": sys.version.split()[0],
                "dependency_lock_sha256": sha((ROOT / "training/requirements-input.lock").read_bytes()),
                "script_sha256": sha(Path(__file__).read_bytes()),
                "template_sha256": sha(tokenizer.chat_template.encode()), "selected_smoke_length": chosen,
                "input_samples": len(source_samples), "encoded_samples": len(accepted), "rejected_samples": len(rejected),
                "pad_token_id": tokenizer.pad_token_id, "eos_token_id": tokenizer.eos_token_id,
                "files": {name: sha(raw) for name, raw in files.items()}}
    raw = encoded(manifest)
    (output / "manifest.json").write_bytes(raw)
    (output / "manifest.sha256").write_text(sha(raw) + "\n")
    return manifest, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, default=DEFAULT_TOKENIZER)
    parser.add_argument("--max-length", type=int, choices=LENGTHS)
    parser.add_argument("--source-manifest-sha256", required=True, help="Trusted converter manifest hash; prevents accepting a re-sealed changed source")
    args = parser.parse_args()
    manifest, report = build(args.samples, args.output, args.tokenizer, args.max_length, expected_source_manifest_sha256=args.source_manifest_sha256)
    print(json.dumps({"output": str(args.output), "encoded": manifest["encoded_samples"], "rejected": manifest["rejected_samples"],
                      "selected_smoke_length": report["selected_smoke_length"], "candidate_lengths": report["candidate_lengths"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
