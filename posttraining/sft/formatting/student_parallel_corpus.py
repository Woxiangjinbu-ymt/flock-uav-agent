"""Pinned development corpus for the v2 tool-ID template experiment.

All captured wire requests are diagnostic prompts, including failed/partial
episodes. They have no target or loss labels. The 61 v1 samples remain a separate
sealed development collection. Synthetic boundary cases are explicitly marked
and cannot be described as observed model behavior or Teacher training data.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from types import ModuleType


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
SOURCE_PLAN = ROOT / "experiments/agent-samples-v1/source-plan.json"
SOURCE_PLAN_SHA256 = "a2e317a1261dcf0a86d5b3460219cee58104233b9d9a4874a3c669bd9eb9a689"
CONVERTER_SHA256 = "26f2cfc214258d68029999c587b54aacb9f1eb27aba07fdc7bacadde8d6387a4"
CONVERSION = ROOT / "docs/results/agent-samples-student-input-20261103/conversion-run-01"
CONVERSION_MANIFEST_SHA256 = "9377805432cdcd1041305d2e2e7d689f91686388867acb42da8c1a12bbabf2be"
VERSION = "student-parallel-diagnostic-corpus-v1"


def _published_converter():
    # This historical diagnostic pins and uses the exact converter published with its 61 samples.
    path = CONVERSION / "source/scripts/convert_agent_samples.py"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != CONVERTER_SHA256:
        raise ValueError("diagnostic_converter_dependency_drift")
    module = ModuleType("published_dev_converter")
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module


wire = _published_converter()


def _check_dependency():
    wire.require(wire.sha(Path(wire.__file__).read_bytes()) == CONVERTER_SHA256, "diagnostic_converter_dependency_drift")


def _no_symlinks(root):
    root = Path(root)
    wire.require(not any(path.is_symlink() for path in (root, *root.parents, *root.rglob("*"))), "source_symlink")


def _verify_sources(source_plan, workspace, expected_source_plan_sha256):
    _check_dependency()
    source_plan, workspace = Path(source_plan), Path(workspace)
    wire.require(not any(path.is_symlink() for path in (source_plan, *source_plan.parents)), "source_symlink")
    raw = source_plan.read_bytes()
    wire.require(not source_plan.with_suffix(".sha256").is_symlink(), "source_symlink")
    wire.require(wire.sha(raw) == expected_source_plan_sha256
                 and source_plan.with_suffix(".sha256").read_text().strip() == expected_source_plan_sha256, "diagnostic_source_plan_anchor")
    plan = wire.parse(raw)
    wire.require(plan.get("schema_version") == 1 and plan.get("historical_source_role") == "development"
                 and plan.get("provider_calls_authorized") == 0 and plan.get("teacher_training_examples_expected") == 0,
                 "diagnostic_source_plan_scope")
    wire.require(tuple(row["run"] for row in plan["runs"]) == wire.RUNS and plan.get("planned_trials") == 22,
                 "diagnostic_fixed_source_inventory")
    sources = []
    for planned in plan["runs"]:
        wire.require(planned.get("purpose") == "development_format_and_input_validation_only", "diagnostic_source_plan_scope")
        run = wire.safe(workspace, planned["run"])
        _no_symlinks(run)
        manifest, source_kind, _ = wire._verify_run(run, planned)
        sources.append((planned, run, manifest, source_kind))
    return raw, sources


def extract_archive_requests(raw, source):
    """Extract only request bytes; response/score/hidden snapshot never becomes input.

    Partial episode endings are permitted here because this function creates no
    supervised examples. Every request still needs an unambiguous logical call,
    immutable ownership, ordered record sequence and verified HTTP body bytes.
    """
    wire.require(raw.endswith(b"\n"), "diagnostic_unterminated_archive")
    rows = [wire.parse(line) for line in raw.splitlines()]
    wire.require(rows and rows[0].get("kind") == "archive_start", "diagnostic_archive_start")
    owner = {key: rows[0][key] for key in ("archive_id", "episode_id", "command_id")}
    models, ended_models, attempts, requests = set(), set(), set(), []
    archive_closed = False
    for sequence, row in enumerate(rows, 1):
        wire.require(not archive_closed and row.get("schema_version") == 1 and row.get("sequence") == sequence,
                     "diagnostic_archive_sequence")
        wire.require(all(row.get(key) == value for key, value in owner.items()), "diagnostic_archive_ownership")
        kind, data = row["kind"], row["data"]
        if kind == "archive_start":
            wire.require(sequence == 1, "diagnostic_duplicate_archive_start")
        elif kind == "model_start":
            logical = data["logical_call_id"]
            wire.require(isinstance(logical, str) and logical and logical not in models, "diagnostic_duplicate_logical_call")
            models.add(logical)
        elif kind == "model_end":
            logical = data["logical_call_id"]
            wire.require(logical in models and logical not in ended_models, "diagnostic_model_end")
            ended_models.add(logical)
        elif kind == "http_request":
            logical, attempt = data["logical_call_id"], data["attempt_id"]
            wire.require(logical in models and logical not in ended_models and isinstance(attempt, str)
                         and attempt and attempt not in attempts, "diagnostic_request_association")
            attempts.add(attempt)
            wire.require(data.get("body_scope") == "serialized_http_request_content" and data.get("error_type") is None,
                         "diagnostic_request_not_wire")
            body = wire.decode(data["body"])
            request = wire.parse(body)
            wire.require(isinstance(request, dict) and isinstance(request.get("messages"), list)
                         and isinstance(request.get("tools", []), list), "diagnostic_request_shape")
            removed = []
            messages = [wire.clean_message(message, f"request.messages[{index}]", removed)
                        for index, message in enumerate(request["messages"])]
            origin = {**source, **owner, "logical_call_id": logical, "attempt_id": attempt,
                      "request_sequence": sequence, "archive_sha256": wire.sha(raw), "request_body_sha256": wire.sha(body),
                      "split": "development", "original_model": request.get("model")}
            request_id = wire.sha(wire.canonical({key: origin[key] for key in
                ("run_manifest_sha256", "trial_id", "archive_sha256", "logical_call_id", "attempt_id", "request_sequence", "request_body_sha256")}))
            requests.append({"schema": "student-diagnostic-request-v1", "request_id": request_id, "source": origin,
                "purpose": "prompt_only_template_diagnostic", "teacher_eligible": False, "messages": messages,
                "tools": deepcopy(request.get("tools", [])), "audit": {"removed_reasoning": removed,
                "response_used": False, "scorer_used_as_input": False, "serialized_request_does_not_prove_provider_dispatch": True}})
        elif kind == "archive_end":
            archive_closed = True
    return requests, {"logical_calls": len(models), "http_request_records": len(requests),
                      "logical_calls_without_http_request": sorted(models - {r["source"]["logical_call_id"] for r in requests}),
                      "archive_closed": archive_closed}


def load_requests(source_plan=SOURCE_PLAN, workspace=ROOT, *, expected_source_plan_sha256=SOURCE_PLAN_SHA256):
    """Return all 98 pinned wire prompts, including failed/partial episodes."""
    raw_plan, sources = _verify_sources(source_plan, workspace, expected_source_plan_sha256)
    requests, logical_calls, slots = [], 0, 0
    for planned, run, manifest, source_kind in sources:
        for trial in manifest["trials"]:
            slots += 1
            folder = wire.safe(run, "trials/" + trial["trial_id"])
            archives = sorted(folder.glob("policy/modelio/*.jsonl")) if folder.exists() else []
            wire.require(len(archives) <= 1, "diagnostic_ambiguous_archive")
            if not archives:
                continue
            archive = archives[0]
            rows, metadata = extract_archive_requests(archive.read_bytes(), {
                "run": planned["run"], "trial_id": trial["trial_id"], "task_id": trial["task_id"],
                "run_manifest_sha256": planned["manifest_sha256"], "campaign_seal_sha256": planned["campaign_seal_sha256"],
                "source_plan_sha256": expected_source_plan_sha256, "archive_file": str(archive.relative_to(run)),
                "source_kind": source_kind})
            requests.extend(rows)
            logical_calls += metadata["logical_calls"]
    wire.require(slots == 22 and logical_calls == len(requests) == 98
                 and len({row["request_id"] for row in requests}) == 98, "diagnostic_fixed_request_count")
    # The caller receives only fully checked in-memory rows. Recheck every source
    # payload, not just an unchanged seal file, to detect drift during extraction.
    final_plan, _ = _verify_sources(source_plan, workspace, expected_source_plan_sha256)
    wire.require(raw_plan == final_plan, "diagnostic_source_drift")
    return requests


def _verify_conversion(package, expected_manifest_sha256):
    _check_dependency()
    package = Path(package)
    _no_symlinks(package)
    manifest_path = wire.safe(package, "manifest.json")
    raw = manifest_path.read_bytes()
    wire.require(wire.sha(raw) == expected_manifest_sha256
                 and wire.safe(package, "manifest.sha256").read_text().strip() == expected_manifest_sha256,
                 "diagnostic_conversion_anchor")
    manifest = wire.parse(raw)
    wire.require(manifest.get("schema") == "agent-sample-export-v1" and manifest.get("purpose") == "development"
                 and manifest.get("teacher_eligible") is False and manifest.get("true_teacher_samples") == 0
                 and manifest.get("source_plan_sha256") == SOURCE_PLAN_SHA256, "diagnostic_conversion_scope")
    wire.verify_hashes(package, manifest["files"])
    actual = {str(path.relative_to(package)) for path in package.rglob("*") if path.is_file()}
    wire.require(actual == set(manifest["files"]) | {"manifest.json", "manifest.sha256"}, "diagnostic_conversion_inventory")
    for name, expected in manifest["sources"].items():
        wire.require(manifest["files"].get("source/" + name) == expected, "diagnostic_conversion_source_hash")
    return manifest


def load_samples(package=CONVERSION, *, expected_manifest_sha256=CONVERSION_MANIFEST_SHA256):
    """Read the original 61 development samples; never infer labels from prompts."""
    package = Path(package)
    manifest = _verify_conversion(package, expected_manifest_sha256)
    raw = wire.safe(package, "samples.jsonl").read_bytes()
    rows = [wire.parse(line) for line in raw.splitlines()]
    wire.require(len(rows) == manifest["counts"]["exported_samples"] == 61
                 and len({row["sample_id"] for row in rows}) == 61, "diagnostic_conversion_count")
    wire.require(all(row.get("schema") == "agent-decision-sample-v1" and row.get("teacher_eligible") is False
                     and row.get("source", {}).get("split") == "development" for row in rows), "diagnostic_sample_scope")
    _verify_conversion(package, expected_manifest_sha256)
    wire.require(wire.safe(package, "samples.jsonl").read_bytes() == raw, "diagnostic_sample_drift")
    return rows


def summarize_parallel_requests(requests):
    """Public wire history only; repeated contexts do not multiply unique groups."""
    groups, same_name_requests, reordered_requests = {}, set(), set()
    for request in requests:
        messages = request["messages"]
        for index, message in enumerate(messages):
            calls = message.get("tool_calls", []) if message.get("role") == "assistant" else []
            if len(calls) < 2:
                continue
            ids = [call["id"] for call in calls]
            names = [call["function"]["name"] for call in calls]
            receipts = []
            for follow in messages[index + 1:]:
                if follow["role"] != "tool":
                    break
                receipts.append(follow["tool_call_id"])
            same_name = len(names) != len(set(names))
            reordered = len(receipts) == len(ids) and set(receipts) == set(ids) and receipts != ids
            if same_name:
                same_name_requests.add(request["request_id"])
            if reordered:
                reordered_requests.add(request["request_id"])
            key = wire.sha(wire.canonical([request["source"]["archive_sha256"], ids]))
            description = {"group_id": key, "run": request["source"]["run"], "trial_id": request["source"]["trial_id"],
                "call_ids": ids, "names": names, "same_name_parallel": same_name,
                "calls": [{"tool_call_id": call["id"], "name": call["function"]["name"],
                           "arguments": wire.parse(call["function"]["arguments"])} for call in calls],
                "receipt_ids": receipts, "receipt_order_matches_calls": receipts == ids, "reordered_receipts": reordered}
            if key in groups:
                expected = {k: v for k, v in groups[key].items() if k != "observed_request_ids"}
                wire.require(expected == description, "diagnostic_history_group_changed")
            else:
                groups[key] = {**description, "observed_request_ids": []}
            groups[key]["observed_request_ids"].append(request["request_id"])
    return {"schema": "student-parallel-wire-summary-v1", "requests": len(requests),
            "same_name_request_count": len(same_name_requests), "reordered_request_count": len(reordered_requests),
            "unique_parallel_groups": len(groups), "unique_same_name_groups": sum(g["same_name_parallel"] for g in groups.values()),
            "unique_reordered_groups": sum(g["reordered_receipts"] for g in groups.values()),
            "groups": list(groups.values()), "scope": "wire_prompt_history_not_execution_completion_order",
            "teacher_examples": 0, "provider_calls": 0}


def synthetic_cases():
    """Explicitly invented positive/negative association fixtures, not demonstrations."""
    tools = [{"type": "function", "function": {"name": "get_drone_status", "description": "Synthetic test tool",
              "parameters": {"type": "object", "properties": {"input_json": {"type": "string"}}, "required": ["input_json"]}}}]
    prefix = [{"role": "system", "content": "Synthetic association test; no real drone is operated."},
              {"role": "user", "content": "Inspect the two synthetic drones."}]
    calls = [{"id": f"synthetic-call-{i}", "type": "function", "function": {"name": "get_drone_status",
              "arguments": json.dumps({"input_json": json.dumps({"drone_id": f"synthetic-drone-{i}"})})}} for i in (1, 2)]
    assistant = {"role": "assistant", "content": None, "tool_calls": calls}
    receipts = [{"role": "tool", "tool_call_id": f"synthetic-call-{i}", "name": "get_drone_status",
                 "content": json.dumps({"status": "success", "drone_id": f"synthetic-drone-{i}", "battery": 90-i})} for i in (1, 2)]
    def case(name, messages, target=None, valid=True, reason=None):
        return {"case_id": name, "origin": "synthetic_unit_fixture", "is_real_model_evidence": False,
                "teacher_eligible": False, "purpose": "template_association_boundary_test", "expected_valid": valid,
                "invalidity": reason, "messages": deepcopy(messages), "tools": deepcopy(tools),
                "target": deepcopy(target or {"role": "assistant", "content": "Synthetic receipt association verified."})}
    ordered = prefix + [assistant] + receipts
    cases = [case("same_name_ordered", ordered), case("same_name_reversed_receipts", prefix + [assistant] + list(reversed(receipts))),
             case("same_name_current_target", prefix, assistant)]
    same_args = deepcopy(ordered)
    same_args[2]["tool_calls"][1]["function"]["arguments"] = same_args[2]["tool_calls"][0]["function"]["arguments"]
    same_args[4]["content"] = same_args[3]["content"]
    cases.append(case("same_name_same_args_distinct_ids", same_args))
    mutations = (
        ("duplicate_call_id", lambda m: m[2]["tool_calls"][1].update(id="synthetic-call-1")),
        ("unknown_receipt_id", lambda m: m[3].update(tool_call_id="never-called")),
        ("wrong_receipt_name", lambda m: m[3].update(name="not_get_drone_status")),
        ("duplicate_receipt", lambda m: m.append(deepcopy(m[3]))),
        ("missing_receipt", lambda m: m.pop()),
        ("nonobject_arguments", lambda m: m[2]["tool_calls"][0]["function"].update(arguments="[]")),
        ("malformed_arguments", lambda m: m[2]["tool_calls"][0]["function"].update(arguments="{broken")),
        ("duplicate_argument_key", lambda m: m[2]["tool_calls"][0]["function"].update(arguments='{"input_json":"a","input_json":"b"}')),
    )
    for name, change in mutations:
        messages = deepcopy(ordered)
        change(messages)
        cases.append(case(name, messages, valid=False, reason=name))
    return cases


def source_metadata():
    return {"version": VERSION, "source_plan_sha256": SOURCE_PLAN_SHA256,
            "conversion_manifest_sha256": CONVERSION_MANIFEST_SHA256,
            "dependency_hashes": {"scripts/convert_agent_samples.py": CONVERTER_SHA256},
            "planned_episodes": 22, "diagnostic_requests": 98, "original_development_samples": 61,
            "diagnostic_scope": "prompt_only_no_targets_no_loss_labels", "teacher_examples": 0, "provider_calls": 0}
