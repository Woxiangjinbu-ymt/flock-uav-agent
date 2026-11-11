"""Freeze/recheck the Harness research specification; never execute a model.

This is a review packet and source/data fingerprint, not an online coordinator.
Existing local experiments independently freeze their runnable source and inputs.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import copy
import hashlib
import json

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
PLAN = "experiments/harness-method-v1/validation-plan.json"
METHOD = "docs/HARNESS_METHOD_CN.md"
PROTOCOL = "docs/HARNESS_VALIDATION_PROTOCOL_CN.md"
CANDIDATE = {
    "task_progress_mode": "evidence_harness", "execution_memory": "evidence_r2",
    "harness_revision": "observed_target_v7", "execution_contract": "task_execution_state_v1",
    "scheduler": "applicability", "static_obstacles": True, "cross_episode_memory": False,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def safe(root, name):
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("Expected repository-relative path")
    p = root / p
    if not p.resolve().is_relative_to(root.resolve()) or any(q.is_symlink() for q in (p, *p.parents)):
        raise ValueError("Symlink or outside-root path")
    return p


def validate_plan(plan):
    fixed = {
        "schema_version": 1, "plan_id": "harness-method-validation-v1",
        "status": "local_validation_and_review_only", "online_authorized_executions": 0,
        "teacher_collection": False, "training": False,
        "reference_local_profile": "experiments/agent-execution-v1/profile-main.json",
        "development_study": "experiments/task-coverage-v1-study",
        "development_study_manifest_sha256": "6386876e9e6172655abb899c836b4199909e438846b83be515a5f75aadb48bae",
        "natural_repetitions": 2,
        "model_identity": None, "paid_batch_budget": None, "candidate": CANDIDATE,
        "mechanism_arms": ["failure", "applicability"],
        "guarded_macro": "semantic_reference_not_an_additional_product_arm",
        "formal_mechanism_scenarios": None, "independent_evaluation_scenarios": None,
        "primary_metric": "independently_verified_whole_task_success",
        "zero_opportunity_rate": None,
        "system_comparison_kind": "package_not_single_mechanism_ablation",
    }
    if not isinstance(plan, dict) or set(plan) != set(fixed) | {
            "natural_task_ids", "fault_categories", "secondary_metrics"}:
        raise ValueError("Unsupported validation plan fields")
    for key, value in fixed.items():
        if encode(plan[key]) != encode(value):
            raise ValueError("Changed frozen research boundary: " + key)
    tasks = plan["natural_task_ids"]
    if not isinstance(tasks, list) or len(tasks) != 18 or len(set(tasks)) != 18:
        raise ValueError("Expected all 18 development tasks without replacement")
    if plan["fault_categories"] != ["normal", "local_repair", "repair_exhausted", "partial_effect",
            "unknown_applied", "unknown_unapplied", "dependency_changed", "terminal_resume",
            "transferable_endpoint", "nontransferable_failure"]:
        raise ValueError("Fault coverage changed")
    if plan["secondary_metrics"] != ["model_calls", "input_tokens", "output_tokens", "wall_seconds",
            "public_api_calls", "physical_action_attempts", "action_errors", "duplicate_applied_effects",
            "invalid_continuations", "recovery_opportunities", "remaining_obligations_completed"]:
        raise ValueError("Cost or recovery metrics changed")


def mechanism_arms(plan):
    validate_plan(plan)
    arms = {name: {**copy.deepcopy(plan["candidate"]), "scheduler": name}
            for name in plan["mechanism_arms"]}
    left, right = arms.values()
    if {key for key in left if encode(left[key]) != encode(right[key])} != {"scheduler"}:
        raise ValueError("Mechanism arms differ beyond checkpoint scheduling")
    return arms


def natural_slots(plan):
    validate_plan(plan)
    slots = []
    for repeat in range(plan["natural_repetitions"]):
        for index, task in enumerate(plan["natural_task_ids"]):
            arms = ["raw", "integrated"] if (index + repeat) % 2 == 0 else ["integrated", "raw"]
            for arm in arms:
                slots.append({"slot_id": f"{task}-r{repeat + 1}-{arm}", "task_id": task,
                    "repeat": repeat + 1, "arm": arm, "execution_status": "not_authorized_not_run"})
    return slots


def build_packet(root=ROOT):
    root = Path(root).resolve()
    plan = read(root / PLAN)
    validate_plan(plan)
    sys.path.insert(0, str(root / "agent4drone"))
    from agent_profile import AgentProfile
    profile = AgentProfile.load(safe(root, plan["reference_local_profile"]))
    value = profile.to_dict()
    actual = {"task_progress_mode": value["agent"]["task_progress_mode"],
        "execution_memory": value["agent"]["execution_memory"],
        "harness_revision": value["harness"]["revision"],
        "execution_contract": profile.execution_contract, "scheduler": value["harness"]["scheduler"],
        "static_obstacles": value["harness"]["static_obstacles"],
        "cross_episode_memory": value["agent"]["share_blackboard_by_session"]}
    if actual != plan["candidate"]:
        raise ValueError("Candidate does not match current v2 local profile")
    study = safe(root, plan["development_study"])
    raw = (study / "manifest.json").read_bytes()
    if (sha(raw) != plan["development_study_manifest_sha256"]
            or sha(raw) != (study / "manifest.sha256").read_text().strip()):
        raise ValueError("Development manifest changed")
    origin = read(study / "manifest.json")
    if origin["split"] != "development" or origin["online_authorized_executions"] != 0:
        raise ValueError("Expected known development source without online authority")
    # Check exact original order, not merely membership: order is a paired control.
    source_tasks = [item["task_id"] if isinstance(item, dict) else item for item in origin["tasks"]]
    if plan["natural_task_ids"] != source_tasks:
        raise ValueError("Selection or ordering differs from frozen development list")
    inputs = {str((study / "manifest.json").relative_to(root)): sha(raw)}
    for task in plan["natural_task_ids"]:
        name = "fixtures/" + task + ".json"
        fixture = read(safe(study, name))
        names = [name, "fixtures/" + task + ".pkl", "fixtures/" + fixture["timeline_file"]]
        if fixture["split"] != "development" or fixture["task_id"] != task:
            raise ValueError("Fixture split/identity differs")
        for item in names:
            path = safe(study, item)
            digest = sha(path.read_bytes())
            if digest != origin["files"][item]:
                raise ValueError("Development input changed: " + item)
            inputs[str(path.relative_to(root))] = digest
    paths = [p for folder in ("agent4drone", "server") for p in (root / folder).rglob("*.py")
        if not {".venv", "tests", "__pycache__"}.intersection(p.relative_to(root).parts)]
    # Include all maintained script dependencies and their verification logic;
    # a short hand-written entrypoint list silently missed transitive oracles.
    paths += list((root / "scripts").glob("*.py"))
    paths += list((root / "scripts/tests").glob("*.py"))
    paths += [root / folder / "requirements.txt" for folder in ("agent4drone", "server")]
    paths += [p for p in (root / "experiments/harness-method-v1").iterdir()
              if p.is_file() and p.suffix in {".json", ".md"}]
    if (root / "compat/agent4drone").is_dir():
        from validate_harness_episode import agent_source_items
        paths += [root / "scripts" / p.name for p in (root / "compat/scripts").glob("*.py")]
        sources = {name: sha(p.read_bytes()) for name, p in agent_source_items(root, paths)}
    else:
        sources = {str(p.relative_to(root)): sha(p.read_bytes()) for p in sorted(paths)}
    documents = {name: safe(root, name).read_bytes()
        for name in (PLAN, METHOD, PROTOCOL, plan["reference_local_profile"])}
    packet = {"schema_version": 1, "scope": "research_specification_not_execution_authorization",
        "online_authorized_executions": 0, "provider_calls": 0,
        "profile_sha256": profile.fingerprint, "mechanism_arms": mechanism_arms(plan),
        "natural_slots": natural_slots(plan), "source_sha256": sources, "input_sha256": inputs,
        "documents": {name: sha(raw) for name, raw in documents.items()},
        "readiness": {"local_profile_matches": True, "natural_selection_frozen": True,
            "real_provider_v2_validated": False, "formal_mechanism_scenarios_frozen": False,
            "independent_evaluation_frozen": False, "paper_effect_claims_ready": False}}
    return packet, documents


def prepare(output, root=ROOT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Use a new review packet; never overwrite evidence")
    packet, documents = build_packet(root)
    output.mkdir(parents=True)
    for name, raw in documents.items():
        target = output / "documents" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (output / "manifest.json").write_bytes(encode(packet))
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return packet


def verify(output, root=ROOT):
    output = Path(output).resolve()
    raw = (output / "manifest.json").read_bytes()
    if sha(raw) != (output / "manifest.sha256").read_text().strip():
        raise ValueError("Review manifest changed")
    recorded = read(output / "manifest.json")
    actual, documents = build_packet(root)
    if encode(recorded) != encode(actual):
        raise ValueError("Current specification/source/data differs from review packet")
    for name, content in documents.items():
        if safe(output / "documents", name).read_bytes() != content:
            raise ValueError("Frozen specification changed: " + name)
    return recorded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = (prepare if args.action == "prepare" else verify)(args.output)
    print(json.dumps({"scope": result["scope"], "planned_natural_slots": len(result["natural_slots"]),
        "provider_calls": 0, "readiness": result["readiness"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
