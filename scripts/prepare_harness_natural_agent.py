"""Prepare/verify a proposed natural-Agent study without authorizing execution.

Standard library only: no worker, subprocess, provider settings, simulator import,
pickle deserialization, or online execution entry point. Binary inputs are hashed
in place, never copied into the public catalog.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDY = "experiments/minimal-harness-full100-v1"
ACCEPTED_RUN = "docs/results/harness-full-episode-20261025/run-20261025-02"
PROTOCOL = "docs/HARNESS_NATURAL_AGENT_VALIDATION_CN.md"
TASK_SOURCE = "scripts/validate_harness_observed_target.py"
SCRIPT = "scripts/prepare_harness_natural_agent.py"
DEFAULT_OUTPUT = "experiments/harness-natural-agent-plan-v1"
TASKS = (
    ("7eba7390", "public_search"),
    ("24ac1838", "public_search"),
    ("b1ad4f3e", "public_search"),
    ("b1eb0270", "simple_chain"),
    ("898cfdca", "simple_chain"),
    ("784d71f8", "public_search"),
    ("1905e0ad", "public_search"),
)
GATES = (
    "provider_and_actual_model_configuration_not_verified",
    "http_attempt_admission_not_implemented",
    "token_and_cost_admission_not_implemented",
    "per_request_wall_deadline_not_implemented",
    "real_clock_deadline_drain_and_outer_watchdog_not_validated",
    "natural_worker_and_independent_scorer_adapter_not_implemented",
    "explicit_online_authorization_not_granted",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def absolute(path):
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("Parent traversal is not allowed")
    path = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Symlink paths are not allowed")
    return path


def local(root, relative):
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts or str(value) in {".", ""}:
        raise ValueError("Expected a local relative artifact path")
    result = absolute(root / value)
    if not result.is_relative_to(root):
        raise ValueError("Artifact escapes its root")
    return result


def output_path(workspace, output):
    value = Path(output)
    result = absolute(value if value.is_absolute() else workspace / value)
    if result == workspace or not result.is_relative_to(workspace):
        raise ValueError("Output must be a child of the workspace")
    return result


def read_json(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def check_hashes(root, expected, count=None):
    if not isinstance(expected, dict) or (count is not None and len(expected) != count):
        raise ValueError("Unexpected frozen file count")
    for name, checksum in expected.items():
        if not isinstance(name, str) or not isinstance(checksum, str) or len(checksum) != 64:
            raise ValueError("Invalid hash reference")
        path = local(root, name)
        if not path.is_file() or file_sha(path) != checksum:
            raise ValueError("Frozen artifact mismatch: " + name)


def documents(workspace):
    """Rebuild all expected bytes from fixed semantics and checked references."""
    protocol = local(workspace, PROTOCOL).read_bytes()
    task_source = local(workspace, TASK_SOURCE)
    declarations = [node.value for node in ast.parse(task_source.read_bytes()).body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "TASKS" for target in node.targets)]
    if len(declarations) != 1 or ast.literal_eval(declarations[0]) != TASKS:
        raise ValueError("Task order differs from the observed-target study")

    study = local(workspace, STUDY)
    original_path = local(study, "manifest.json")
    original = read_json(original_path)
    check_hashes(study, original["files"], 410)
    catalog, annotations, selected, source_inputs = [], [], {}, {}
    for index, (task_id, requested_skill) in enumerate(TASKS):
        name = f"fixtures/{task_id}.json"
        if name not in original["files"]:
            raise ValueError("Unmanifested fixture")
        fixture = read_json(local(study, name))
        if fixture["task_id"] != task_id:
            raise ValueError("Fixture identity mismatch")
        for field in ("scene_id", "command", "command_sha256", "problem_cluster"):
            if not isinstance(fixture[field], str) or not fixture[field]:
                raise ValueError("Invalid public fixture field: " + field)
        if sha(fixture["command"].encode()) != fixture["command_sha256"]:
            raise ValueError("Original command hash mismatch")
        dependencies = {name: original["files"][name],
                        f"fixtures/{task_id}.pkl": fixture["snapshot_sha256"],
                        "fixtures/" + fixture["timeline_file"]: fixture["timeline_sha256"]}
        if any(original["files"].get(path) != checksum for path, checksum in dependencies.items()):
            raise ValueError("Fixture dependency hash disagrees with original manifest")
        check_hashes(study, dependencies)
        selected.update(dependencies)
        source_name, source_hash = fixture["source"], fixture["source_sha256"]
        check_hashes(workspace, {source_name: source_hash})
        if source_name in source_inputs and source_inputs[source_name] != source_hash:
            raise ValueError("Conflicting original-source references")
        source_inputs[source_name] = source_hash
        eligibility = "public_search" if index < 3 else "simple_chain" if index < 5 else "unsupported"
        if fixture["eligibility"] != eligibility:
            raise ValueError("Evaluation eligibility changed")
        catalog.append({"task_id": task_id, "scene_id": fixture["scene_id"],
                        "command": fixture["command"], "command_hash": fixture["command_sha256"]})
        annotations.append({"task_id": task_id, "requested_skill_in_previous_scripted_study": requested_skill,
                            "eligibility": eligibility,
                            "problem_cluster": fixture["problem_cluster"], "known_development": True,
                            "group": "search_development" if index < 3 else
                                     "simple_chain_positive_control" if index < 5 else "unsupported_grammar_control",
                            "scope": "evaluator_only_not_policy_input"})
    if len({item["problem_cluster"] for item in annotations[:3]}) != 3:
        raise ValueError("Search representatives must have three distinct problem clusters")

    accepted = local(workspace, ACCEPTED_RUN)
    seal_path = local(accepted, "campaign-seal.json")
    seal = read_json(seal_path)
    if seal["status"] != "passed":
        raise ValueError("Referenced episode campaign did not pass")
    check_hashes(accepted, seal["files"], 302)
    accepted_manifest_path = local(accepted, "manifest.json")
    if (local(accepted, "manifest.sha256").read_text().strip() != file_sha(accepted_manifest_path)
            or seal["files"].get("manifest.json") != file_sha(accepted_manifest_path)):
        raise ValueError("Accepted manifest is not sealed")
    accepted_manifest = read_json(accepted_manifest_path)
    check_hashes(local(accepted, "source"), accepted_manifest["sources"], 80)
    check_hashes(workspace, accepted_manifest["sources"], 80)
    if any(seal["files"].get("source/" + name) != checksum
           for name, checksum in accepted_manifest["sources"].items()):
        raise ValueError("Accepted sources are not covered by the campaign seal")
    summary_path = local(accepted, "summary.json")
    summary = read_json(summary_path)
    if (seal["files"].get("summary.json") != file_sha(summary_path)
            or summary["status"] != "passed" or summary["check_count"] != 96
            or len(summary["checks"]) != 96 or summary["failed_checks"]
            or any(value is not True for value in summary["checks"].values())):
        raise ValueError("Accepted episode checks are not intact")

    slots = []
    for index, (task_id, _) in enumerate(TASKS):
        order = ("applicability", "failure") if index % 2 == 0 else ("failure", "applicability")
        for scheduler in order:
            slots.append({"slot": len(slots) + 1, "task_id": task_id, "scheduler": scheduler,
                          "proposed_id": f"natural-{task_id}-{scheduler}"})
    values = {
        "tasks.public.json": catalog,
        "evaluation-annotations.json": annotations,
        "schedule.proposed.json": {"status": "proposed_only", "proposed_slot_count": 14,
            "arms": {"A": "applicability", "F": "failure"}, "order_counts": {"AF": 4, "FA": 3},
            "fully_balanced": False, "repetitions_per_task_arm": 1, "slots": slots},
        "input-references.json": {"study": STUDY, "original_manifest_sha256": file_sha(original_path),
            "original_manifest_files": original["files"], "selected_dependencies": selected,
            "original_task_sources": source_inputs, "binary_handling": "hash_only_no_copy_no_deserialization"},
        "source-references.json": {"accepted_run": ACCEPTED_RUN,
            "accepted_campaign_seal_sha256": file_sha(seal_path),
            "accepted_manifest_sha256": file_sha(accepted_manifest_path),
            "accepted_summary_sha256": file_sha(summary_path), "accepted_sealed_file_count": 302,
            "accepted_source_count": 80, "accepted_sources_matching_current": accepted_manifest["sources"],
            "preparation_only_sources_not_covered_by_episode_acceptance": {
                SCRIPT: file_sha(local(workspace, SCRIPT)), TASK_SOURCE: file_sha(task_source),
                PROTOCOL: sha(protocol)}},
        "preflight.json": {"status": "local_preparation_only", "online_ready": False,
            "checks": {"original_410_files_verified": True, "seven_fixture_dependencies_verified": True,
                "public_catalog_whitelist": True, "accepted_302_file_seal_verified": True,
                "accepted_80_sources_match_current": True, "accepted_96_checks_passed": True,
                "task_order_matches_previous_study": True, "proposed_14_slots_scheduler_only": True},
            "uncompleted_online_gates": list(GATES), "provider_calls": 0,
            "scope": "Static references and proposed protocol only; no simulator or SDK validation"},
    }
    payloads = {name: encoded(value) for name, value in values.items()}
    payloads["PROTOCOL_CN.md"] = protocol
    manifest = {"schema_version": 1, "status": "local_preparation_only",
        "authorized_executions": 0, "authorized_trial_ids": [], "provider_calls": 0,
        "online_ready": False, "online_budget": {"executions": 0, "http_attempts": 0, "tokens": 0, "cost": 0},
        "candidate_model": {"name": "deepseek-flash", "temperature": 0.1},
        "common": {"revision": "observed_target_v7", "static_obstacles": True,
                   "task_progress_mode": "evidence_harness", "scheduler": "per_proposed_slot"},
        "proposed_tasks": 7, "proposed_slots": 14, "uncompleted_online_gates": list(GATES),
        "historical_task_budget_reference_only": {"deadline_seconds": 500, "max_iterations": 600,
            "recursion_limit": 1800, "api_calls_per_plan": 2000, "repair_budget": 16, "search_rounds": 16},
        "scope": "Known development tasks; proposal is neither online authorization nor training data",
        "files": {name: sha(content) for name, content in payloads.items()}}
    payloads["manifest.json"] = encoded(manifest)
    return payloads, manifest


def prepare(workspace, output):
    workspace = absolute(workspace)
    if not workspace.is_dir():
        raise ValueError("Workspace directory is missing")
    output = output_path(workspace, output)
    if output.exists():
        raise FileExistsError("Existing preparation must not be overwritten")
    payloads, manifest = documents(workspace)
    # All input checks and serialization finish before an output directory exists.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    for name, content in payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(content)
    return manifest


def verify(workspace, output):
    workspace = absolute(workspace)
    output = output_path(workspace, output)
    if not output.is_dir():
        raise ValueError("Preparation directory is missing")
    payloads, manifest = documents(workspace)
    if {path.name for path in output.iterdir()} != set(payloads):
        raise ValueError("Unexpected or missing preparation artifact")
    for name, expected in payloads.items():
        path = local(output, name)
        if not path.is_file() or path.read_bytes() != expected:
            raise ValueError("Preparation differs from checked sources or fixed semantics: " + name)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    args = parser.parse_args()
    result = (prepare if args.action == "prepare" else verify)(args.workspace, args.output)
    print(json.dumps({key: result[key] for key in ("status", "authorized_executions", "provider_calls", "online_ready")}))
