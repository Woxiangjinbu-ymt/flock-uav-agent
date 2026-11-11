"""List the paper's scene roles and check proposed training inputs against the fixed holdouts."""
import argparse
from collections import Counter
import csv
import json

from verify import PACKAGE, check_package, check_source, checked_file, require


AUDIT = PACKAGE.parent / "teacher/2026-11-11-fold-a/audit"
ROLES_SHA = "85fd45ecf0443ffc01f8725c6cd0fc98443a53a6690f0167898e52aa2c793261"
PROJECTION_SHA = "53e88f511eceaf637b58544ad8fef9fb4dc70bada276e9277767a01731c5a5ee"
INVENTORY_SHA = "2f59f5e5ee3bd8cc79732c823a2c54d29490c1b08a2dedf9aac12200a629c393"


def load_roles():
    strata, core_tasks = check_package()
    check_source(PACKAGE.parent / "original-benchmark/benchmark.zip", strata)
    released = json.loads(checked_file(AUDIT / "scene-roles.json", ROLES_SHA))
    checked_file(PACKAGE / "proposed-folds.json", released["parent_fold_sha256"])
    fold_a = {row["session_id"]: row["role"] for row in released["scenes"]}
    require(set(fold_a) == {group["sessions_selected"][0]["session_id"] for group in strata},
            "Teacher release must assign exactly the 15 fold-A scenes")

    roles = {}
    for group in strata:
        first, second, *remaining = group["full_five_session_order_for_nested_expansion"]
        roles[first] = {"train": "baseline_train", "internal_validation": "internal_validation"}[fold_a[first]]
        roles[second] = "fixed_test"
        for scene in remaining:
            roles[scene] = "expansion_pool"
    require(Counter(roles.values()) == {
        "baseline_train": 12, "internal_validation": 3, "fixed_test": 15, "expansion_pool": 45,
    }, "The 75 scenes must belong to four disjoint roles")
    inventory = checked_file(PACKAGE.parent / "original-benchmark/scene-roles.csv", INVENTORY_SHA)
    published = [(row["session_id"], row["role"]) for row in csv.DictReader(inventory.decode().splitlines())]
    require(published == list(roles.items()), "Published scene roles differ from the benchmark and release")

    raw = checked_file(AUDIT / "reviewed-qwen-projection.jsonl", PROJECTION_SHA)
    reviewed = [row for row in map(json.loads, raw.splitlines()) if row["status"] == "compatible"]
    for row in reviewed:
        identity = row["session_id"], row["task_id"]
        require(identity in core_tasks and roles[identity[0]] == "baseline_train",
                f"Reviewed training row outside baseline training scenes: {identity}")
    return roles, len(reviewed)


def check_training(roles, requested):
    counts = Counter(requested)
    for scene, count in counts.items():
        require(count == 1, f"Duplicate scene: {scene}")
        require(scene in roles, f"Unknown scene: {scene}")
        require(roles[scene] in {"baseline_train", "expansion_pool"},
                f"Excluded from training: {scene} ({roles[scene]})")
    return {
        "baseline_train": [scene for scene in requested if roles[scene] == "baseline_train"],
        "expansion_candidates_only": [scene for scene in requested if roles[scene] == "expansion_pool"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-training", nargs="+", metavar="SCENE_ID",
                        help="Check proposed whole scenes without assigning expansion candidates to training")
    args = parser.parse_args()
    roles, reviewed_turns = load_roles()
    groups = {}
    for scene, role in roles.items():
        groups.setdefault(role, []).append(scene)
    result = {"roles": {role: {"session_ids": ids, "tasks": len(ids) * 20} for role, ids in groups.items()},
              "reviewed_baseline_training_turns": reviewed_turns}
    if args.check_training:
        try:
            result["training_proposal"] = check_training(roles, args.check_training)
        except ValueError as error:
            parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
