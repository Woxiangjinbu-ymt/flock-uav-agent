"""Independent, read-only audit of the W2 generated instance partitions.

Standard library only: never imports a generator, Agent, simulator or provider.
Checks data integrity and split isolation, not Agent ability or task solvability.
Shared command/template skeletons are disclosed, not called template holdouts.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile


MODULE_DIR = Path(__file__).resolve().parent
# Exported source snapshots keep these modules in their original scripts/ directory.
ROOT = MODULE_DIR.parents[1] if MODULE_DIR.name == "tools" else MODULE_DIR.parent
SCHEMA = "task-split-independent-audit-v1"
SPLITS = ("train", "dev", "test")
SCOPE = "in_template_instance_generalization"
NEAR_METERS = 1.0
_HASH = re.compile(r"[0-9a-f]{64}")
_META = frozenset({"id", "name", "description", "creator", "created_at", "last_updated",
                   "history", "statistics", "tasks", "task_description"})


class AuditError(ValueError):
    """A data-integrity or declared partition contract failed."""


def require(condition, message):
    if not condition:
        raise AuditError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique(pairs):
    value = {}
    for key, child in pairs:
        require(key not in value, "Duplicate JSON object field")
        value[key] = child
    return value


def parse(data):
    def invalid(value):
        raise AuditError("Non-finite JSON number")
    return json.loads(data, object_pairs_hook=_unique, parse_constant=invalid)


def safe(root, relative):
    require(isinstance(relative, str) and relative and "\\" not in relative, "Invalid relative artifact path")
    path = Path(relative)
    require(not path.is_absolute() and all(p not in {".", ".."} for p in path.parts), "Artifact path escapes dataset")
    target = root / path
    require(target.resolve().is_relative_to(root.resolve()), "Artifact resolves outside dataset")
    return target


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _point(value):
    require(isinstance(value, dict) and _number(value.get("x")) and _number(value.get("y")), "Malformed geometry point")
    require(_number(value.get("z", 0)), "Malformed geometry height")
    return [float(value["x"]), float(value["y"]), float(value.get("z", 0))]


def geometry(scene):
    """ID/name-free geometry, translation-normalized in XY; ground Z is absolute.

    Includes home positions, polygon vertices, obstacle/target sizes. Runtime
    metadata and environmental conditions cannot disguise reuse of a layout.
    This is a conservative grouping key, not proof of physical equivalence.
    """
    rows, all_points = [], []
    for group in ("drones", "targets", "obstacles"):
        require(isinstance(scene.get(group), list), "Scene entity collection missing")
        for entity in scene[group]:
            require(isinstance(entity, dict), "Malformed scene entity")
            positions = {"position": [_point(entity.get("position"))]}
            if entity.get("home_position") is not None:
                positions["home_position"] = [_point(entity["home_position"])]
            if entity.get("vertices") is not None:
                require(isinstance(entity["vertices"], list), "Malformed polygon")
                positions["vertices"] = [_point(v) for v in entity["vertices"]]
            dimensions = {}
            for name in ("radius", "width", "length", "height"):
                if entity.get(name) is not None:
                    require(_number(entity[name]) and entity[name] >= 0, "Invalid geometry dimension")
                    dimensions[name] = float(entity[name])
            row = {"kind": group, "type": entity.get("type", "drone" if group == "drones" else None),
                   "points": positions, "dimensions": dimensions}
            rows.append(row)
            all_points.extend(point for points in positions.values() for point in points)
    require(all_points, "Empty scene geometry")
    anchor = [min(p[axis] for p in all_points) for axis in (0, 1)]
    for row in rows:
        for name, points in row["points"].items():
            row["points"][name] = sorted([[round(p[0] - anchor[0], 8), round(p[1] - anchor[1], 8), round(p[2], 8)] for p in points])
    return sorted(rows, key=lambda row: (row["kind"], str(row["type"]), canonical(row)))


def geometry_near(first, second, tolerance=NEAR_METERS):
    """Same structured layout with every relative coordinate/size within 1 m.

    Canonical entity ordering provides a deterministic, conservative candidate
    test. It is not a complete graph matching or rotation/scale invariant test.
    """
    require(_number(tolerance) and 0 <= tolerance <= NEAR_METERS, "Invalid near-duplicate tolerance")
    if len(first) != len(second):
        return False
    for a, b in zip(first, second):
        if a["kind"] != b["kind"] or a["type"] != b["type"] or set(a["points"]) != set(b["points"]) or set(a["dimensions"]) != set(b["dimensions"]):
            return False
        if any(abs(a["dimensions"][key] - b["dimensions"][key]) > tolerance for key in a["dimensions"]):
            return False
        for key in a["points"]:
            if len(a["points"][key]) != len(b["points"][key]):
                return False
            if any(abs(x - y) > tolerance for pa, pb in zip(a["points"][key], b["points"][key]) for x, y in zip(pa, pb)):
                return False
    return True


def canonical_state(scene):
    """Remove IDs/names/timestamps and canonicalize entity order, retain state."""
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in _META}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    value = clean(scene)
    for group in ("drones", "targets", "obstacles"):
        value[group] = sorted(value[group], key=canonical)
    return value


def command_skeleton(command, scene, *, normalize_numbers=True):
    require(isinstance(command, str), "Missing task command")
    text = command.casefold()
    entities = [(entity.get("name"), group[:-1]) for group in ("drones", "targets", "obstacles") for entity in scene[group]]
    # Longer names first avoids replacing Drone 1 inside Drone 10.
    for name, kind in sorted(entities, key=lambda item: len(item[0] or ""), reverse=True):
        if isinstance(name, str) and name:
            text = re.sub(r"(?<!\w)" + re.escape(name.casefold()) + r"(?!\w)", "<" + kind + ">", text)
    if normalize_numbers:
        text = re.sub(r"(?<!\w)[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", "<number>", text)
    return " ".join(text.split())


def f1_semantics(task, scene):
    """Flight/heading/hover identity excludes irrelevant map XY and other UAVs.

    Actor initial altitude, dynamics and capabilities remain relevant. This
    narrowly scoped fingerprint is not a semantic parser for F2--F6.
    """
    tree = task["original_task"]["execution_check_apis"]
    actor_ids = set()
    def inspect(node):
        if "checks" in node:
            for child in node["checks"]:
                inspect(child)
        else:
            actor_ids.update(v for k, v in node.get("parameters", {}).items() if k == "drone_id")
    inspect(tree)
    require(len(actor_ids) == 1, "F1 must bind one explicit actor")
    actor_id = next(iter(actor_ids))
    actors = [d for d in scene["drones"] if d["id"] == actor_id]
    require(len(actors) == 1, "F1 actor absent or ambiguous")
    actor = actors[0]
    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if value == actor_id:
            return "<actor>"
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    state = {k: normalize(v) for k, v in actor.items()
             if k not in _META | {"position", "home_position", "model"}}
    state["altitude"] = normalize(actor["position"].get("z", 0))
    value = {"command": command_skeleton(task["command"], scene, normalize_numbers=False),
             "checker": normalize(tree), "actor_initial_state_and_capabilities": state}
    return sha(canonical(value).encode())


def _checks(node, identities):
    require(isinstance(node, dict), "Malformed private checker tree")
    if "checks" in node:
        require(node.get("logic") in {"and", "or"} and isinstance(node["checks"], list) and node["checks"], "Malformed checker group")
        return sum(_checks(child, identities) for child in node["checks"])
    require(isinstance(node.get("endpoint"), str) and node["endpoint"].startswith("/check/"), "Invalid checker endpoint")
    require(isinstance(node.get("parameters"), dict), "Checker parameters missing")
    require("expect" not in node or type(node["expect"]) is bool, "Invalid checker expectation")
    for key, value in node["parameters"].items():
        if key.endswith("_id"):
            require(value in identities, "Checker references absent entity")
        if "tolerance" in key:
            require(_number(value) and value >= 0, "Invalid checker tolerance")
    return 1


def _legacy(path):
    with zipfile.ZipFile(path) as archive:
        scenes = [parse(archive.read(name)) for name in archive.namelist() if name.endswith(".json") and not name.startswith("__MACOSX/")]
    require(len(scenes) == 75 and sum(len(s["tasks"]) for s in scenes) == 1500, "Historical benchmark inventory changed")
    return scenes


def _id(value, label):
    require(isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", value)), "Invalid " + label)
    return value


def _audit(dataset, legacy_archive, exposed_datasets):
    root = Path(dataset).resolve()
    require(root.is_dir() and not any(p.is_symlink() for p in root.rglob("*")), "Dataset contains symlinks or is absent")
    raw = (root / "manifest.json").read_bytes()
    require((root / "manifest.sha256").read_text().strip() == sha(raw), "Manifest hash mismatch")
    manifest = parse(raw)
    require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1, "Unknown dataset schema")
    require(manifest.get("phase") in {"pilot", "formal"} and manifest.get("evaluation_scope") == SCOPE, "Unapproved phase or generalization claim")
    require(manifest["phase"] != "formal" or exposed_datasets, "Formal audit requires explicitly registered exposed pilot datasets")
    _id(manifest.get("dataset_id"), "dataset ID")
    require(type(manifest.get("master_seed")) is int and manifest["master_seed"] >= 0, "Missing generation seed")
    require(isinstance(manifest.get("generator_version"), str) and manifest["generator_version"], "Missing generator version")
    require(manifest.get("test_policy") == {"training_allowed": False, "agent_tuning_allowed": False, "audit_only": True}, "Test use policy is unsafe")
    exposure = manifest.get("exposure", {})
    require(exposure.get("old_1500_role") == "exposed_development_only" and exposure.get("formal_test_used_for_agent_tuning") is False, "Historical/test exposure contract failed")
    require(exposure.get("classification") == ("development_only" if manifest["phase"] == "pilot" else "new_generated_instances"), "Exposure class does not match phase")
    require(isinstance(manifest.get("sources"), dict) and manifest["sources"], "Generator sources are not identified")
    for name, digest in manifest["sources"].items():
        safe(root, name)
        require(isinstance(digest, str) and _HASH.fullmatch(digest), "Malformed generator source hash")
    files = manifest.get("files")
    require(isinstance(files, dict) and files, "Empty artifact inventory")
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()} - {"manifest.json", "manifest.sha256"}
    require(actual == set(files), "Unsealed or missing dataset artifact")
    for name, digest in files.items():
        require(isinstance(digest, str) and _HASH.fullmatch(digest), "Malformed artifact hash")
        require(sha(safe(root, name).read_bytes()) == digest, "Artifact hash mismatch: " + name)
    copied_sources = {name for name in files if name.startswith("source/")}
    if copied_sources:
        require(copied_sources == {"source/" + name for name in manifest["sources"]}, "Frozen source inventory differs from generator provenance")
        require(all(files["source/" + name] == digest for name, digest in manifest["sources"].items()), "Frozen source hash differs from generator provenance")
    scenes, tasks = manifest.get("scenes"), manifest.get("tasks")
    require(isinstance(scenes, list) and len(scenes) == 30 and isinstance(tasks, list) and len(tasks) == 174, "Expected fixed 30-scene/174-task inventory")
    scene_map, state_groups, geometry_groups, geometries = {}, defaultdict(list), defaultdict(list), []
    seeds, lineages, paths = set(), set(), set()
    hist = _legacy(legacy_archive)
    old_scenes = {s["id"] for s in hist}
    old_tasks = {t["id"] for s in hist for t in s["tasks"]}
    for row in scenes:
        ident = _id(row.get("scene_id"), "scene ID")
        require(ident not in scene_map and ident not in old_scenes, "Duplicate or exposed scene ID")
        require(row.get("split") in SPLITS and type(row.get("fleet_size")) is int and 1 <= row["fleet_size"] <= 5, "Invalid scene split/fleet")
        require(type(row.get("seed")) is int and row["seed"] >= 0 and row["seed"] not in seeds, "Scene seed reused")
        lineage = _id(row.get("lineage_id"), "lineage ID")
        require(lineage not in lineages, "Scene lineage reused")
        name = row.get("scene_file")
        require(name in files and name not in paths, "Scene payload reused or unsealed")
        scene = parse(safe(root, name).read_bytes())
        require(scene.get("id") == ident and len(scene.get("drones", [])) == row["fleet_size"], "Scene identity/fleet mismatch")
        require(isinstance(scene.get("tasks"), list), "Scene tasks missing")
        entity_ids = [e.get("id") for group in ("drones", "targets", "obstacles") for e in scene.get(group, [])]
        require(all(isinstance(i, str) and i for i in entity_ids) and len(entity_ids) == len(set(entity_ids)), "Ambiguous entity IDs")
        geo = geometry(scene)
        state_hash, geo_hash = sha(canonical(canonical_state(scene)).encode()), sha(canonical(geo).encode())
        entry = {"scene_id": ident, "split": row["split"]}
        state_groups[state_hash].append(entry)
        geometry_groups[geo_hash].append(entry)
        geometries.append((entry, geo))
        scene_map[ident] = (row, scene)
        seeds.add(row["seed"]); lineages.add(lineage); paths.add(name)
    require(Counter(r["split"] for r in scenes) == {"train": 20, "dev": 5, "test": 5}, "Scene split denominator changed")
    require(Counter((r["split"], r["fleet_size"]) for r in scenes) == {(s, f): 4 if s == "train" else 1 for s in SPLITS for f in range(1, 6)}, "Scene/fleet stratification changed")
    duplicate_groups = []
    for kind, groups in (("canonical_state", state_groups), ("translation_normalized_geometry", geometry_groups)):
        for digest, group in groups.items():
            if len(group) > 1:
                duplicate_groups.append({"kind": kind, "sha256": digest, "members": group})
                require(len({r["split"] for r in group}) == 1, "Cross-split " + kind + " duplicate")
    near_pairs = []
    for index, (a, ga) in enumerate(geometries):
        for b, gb in geometries[index + 1:]:
            if geometry_near(ga, gb):
                near_pairs.append({"scenes": [a["scene_id"], b["scene_id"]], "splits": [a["split"], b["split"]]})
                require(a["split"] == b["split"], "Cross-split geometry differs by at most 1 metre")
    historical_geometry = {sha(canonical(geometry(s)).encode()) for s in hist}
    require(not set(geometry_groups) & historical_geometry, "Generated geometry reuses exposed historical scene")
    task_map, task_counts, clusters, templates = {}, Counter(), defaultdict(list), defaultdict(Counter)
    exact_commands, f1_groups = defaultdict(list), defaultdict(list)
    total_leaves = 0
    for row in tasks:
        ident = _id(row.get("task_id"), "task ID")
        require(ident not in task_map and ident not in old_tasks, "Duplicate or exposed task ID")
        require(row.get("scene_id") in scene_map, "Task scene is absent")
        scene_row, scene = scene_map[row["scene_id"]]
        require(all(row.get(k) == scene_row.get(k) for k in ("scene_file", "split", "fleet_size")), "Task split/source differs from scene")
        family = row.get("task_family")
        require(family in {"F1", "F2", "F3", "F4", "F5", "F6"} and not (family == "F5" and row["fleet_size"] == 1), "Unsupported task family/fleet")
        require(isinstance(row.get("template_id"), str) and row["template_id"], "Missing template identity")
        original = row.get("original_task")
        matches = [t for t in scene["tasks"] if t.get("id") == ident]
        require(len(matches) == 1 and original == matches[0], "Task row differs from frozen scene task")
        require(isinstance(row.get("command"), str) and row["command"] == original.get("content"), "Task command differs from source")
        require(row.get("command_sha256") == sha(row["command"].encode()), "Task command hash mismatch")
        identities = {scene["id"], *(e["id"] for group in ("drones", "targets", "obstacles") for e in scene[group])}
        total_leaves += _checks(original.get("execution_check_apis"), identities)
        key = sha(command_skeleton(row["command"], scene).encode())
        clusters[key].append({"task_id": ident, "split": row["split"], "template_id": row["template_id"]})
        exact_key = sha(command_skeleton(row["command"], scene, normalize_numbers=False).encode())
        exact_commands[exact_key].append({"task_id": ident, "split": row["split"], "template_id": row["template_id"]})
        if family == "F1":
            f1_groups[f1_semantics(row, scene)].append({"task_id": ident, "split": row["split"]})
        templates[row["template_id"]][row["split"]] += 1
        task_counts[row["split"]] += 1
        task_map[ident] = row
    require(task_counts == {"train": 116, "dev": 29, "test": 29}, "Task split denominator changed")
    for group in f1_groups.values():
        require(len({t["split"] for t in group}) == 1, "Cross-split F1 task semantic duplicate despite different scene geometry")
    for ident, (row, scene) in scene_map.items():
        rows = [t for t in tasks if t["scene_id"] == ident]
        expected = {"F1", "F2", "F3", "F4", "F6"} | ({"F5"} if row["fleet_size"] > 1 else set())
        require(Counter(t["task_family"] for t in rows) == Counter(expected), "Scene task-family inventory changed")
        require({t["id"] for t in scene["tasks"]} == {t["task_id"] for t in rows} and len(scene["tasks"]) == len(rows), "Unlisted or duplicate scene task")
    historical_skeletons = Counter(sha(command_skeleton(t["content"], s).encode()) for s in hist for t in s["tasks"])
    shared = [{"sha256": key, "tasks": len(group), "by_split": dict(Counter(t["split"] for t in group)),
               "template_ids": sorted({t["template_id"] for t in group}), "historical_tasks_with_same_skeleton": historical_skeletons[key]}
              for key, group in sorted(clusters.items()) if len({t["split"] for t in group}) > 1 or historical_skeletons[key]]
    for key in ("training_task_ids", "teacher_task_ids"):
        if key in manifest:
            require(isinstance(manifest[key], list) and all(i in task_map and task_map[i]["split"] == "train" for i in manifest[key]), "Non-training task included in " + key)
    counts = manifest.get("counts", {})
    require(isinstance(counts, dict) and counts.get("scenes") == 30 and counts.get("tasks") == 174, "Manifest denominator disagrees with inventory")
    if "by_split" in counts:
        require(counts["by_split"] == {s: {"scenes": sum(r["split"] == s for r in scenes), "tasks": task_counts[s]} for s in SPLITS}, "Manifest split counts disagree with inventory")
    exposure_reports = []
    for exposed in dict.fromkeys(Path(p).resolve() for p in exposed_datasets):
        require(exposed != root, "Dataset cannot serve as its own exposure reference")
        other = parse((exposed / "manifest.json").read_bytes())
        require(other.get("phase") == "pilot", "Exposure reference must retain its pilot development identity")
        _audit(exposed, legacy_archive, [])
        require(manifest["master_seed"] != other["master_seed"], "Generation master seed reused from exposed pilot")
        require(not seeds & {r["seed"] for r in other["scenes"]}, "Scene seed reused from exposed pilot")
        require(not lineages & {r["lineage_id"] for r in other["scenes"]}, "Lineage reused from exposed pilot")
        require(not set(scene_map) & {r["scene_id"] for r in other["scenes"]}, "Scene ID reused from exposed pilot")
        require(not set(task_map) & {r["task_id"] for r in other["tasks"]}, "Task ID reused from exposed pilot")
        other_geometries = [geometry(parse(safe(exposed, r["scene_file"]).read_bytes())) for r in other["scenes"]]
        for _, current_geometry in geometries:
            require(not any(geometry_near(current_geometry, previous) for previous in other_geometries), "Exact, translated or near geometry reused from exposed pilot")
        old_by_scene = {r["scene_id"]: parse(safe(exposed, r["scene_file"]).read_bytes()) for r in other["scenes"]}
        other_f1 = {f1_semantics(t, old_by_scene[t["scene_id"]]) for t in other["tasks"] if t["task_family"] == "F1"}
        f1_overlap = set(f1_groups) & other_f1
        require(not any(r["split"] in {"dev", "test"} for key in f1_overlap for r in f1_groups[key]), "Held-out F1 semantics reused from exposed pilot")
        exposure_reports.append({"dataset_id": other["dataset_id"], "manifest_sha256": sha((exposed / "manifest.json").read_bytes()),
            "scenes": len(other["scenes"]), "tasks": len(other["tasks"]), "role": "exposed_development_only", "disjoint": True,
            "train_f1_semantic_overlaps": sum(len(f1_groups[key]) for key in f1_overlap)})
    return {"schema": SCHEMA, "passed": True, "dataset_id": manifest["dataset_id"], "phase": manifest["phase"],
        "manifest_sha256": sha(raw), "auditor_sha256": sha(Path(__file__).read_bytes()), "evaluation_scope": SCOPE,
        "counts": {"scenes": 30, "tasks": 174, "tasks_by_split": dict(task_counts), "checker_leaves": total_leaves},
        "checks": {"artifact_hashes_and_inventory": True, "scene_seed_lineage_task_isolation": True,
                   "canonical_state_and_translation_geometry_isolation": True, "one_metre_geometry_isolation": True,
                   "source_task_checker_structure": True, "formal_test_training_forbidden": True},
        "source_snapshot": {"present": bool(copied_sources), "files": len(copied_sources), "provenance_matches": bool(copied_sources)},
        "exposed_dataset_comparisons": exposure_reports,
        "same_split_duplicate_groups": duplicate_groups, "same_split_near_geometry_pairs": near_pairs,
        "near_duplicate_rule": {"maximum_coordinate_or_size_difference_metres": NEAR_METERS, "translation_axes": ["x", "y"],
            "z_translation_allowed": False, "entity_matching": "canonical_order_candidate_screen_not_complete_graph_isomorphism"},
        "shared_command_skeleton_clusters": shared, "template_counts_by_split": {k: dict(v) for k, v in sorted(templates.items())},
        "exact_command_clusters_preserving_numbers": [{"sha256": key, "tasks": len(group),
            "by_split": dict(Counter(t["split"] for t in group)), "template_ids": sorted({t["template_id"] for t in group})}
            for key, group in sorted(exact_commands.items()) if len({t["split"] for t in group}) > 1],
        "f1_task_semantic_deduplication": {"unique_fingerprints": len(f1_groups), "cross_split_duplicates": 0,
            "irrelevant_map_xy_and_other_actors_excluded": True, "checker_and_initial_actor_state_preserved": True},
        "historical_exposure": {"scene_count": 75, "task_count": 1500, "role": "exposed_development_only", "archive_sha256": sha(Path(legacy_archive).read_bytes())},
        "limitations": ["in_template_instance_generalization_not_template_or_family_holdout", "static_audit_does_not_prove_solvability_or_checker_semantics",
            "source_hashes_identify_generator_version_not_current_workspace_equality", "declared_exposure_requires_separate_access_and_use_records",
            "renaming_translation_and_one_metre_screen_is_not_complete_semantic_deduplication",
            "fine_grained_task_semantic_deduplication_implemented_only_for_F1_not_F2_F6", "no_policy_calls_no_test_based_agent_tuning"],
        "provider_calls": 0}


def audit_dataset(dataset, *, legacy_archive=ROOT / "benchmark/benchmark.zip", exposed_datasets=()):
    return _audit(dataset, Path(legacy_archive), list(exposed_datasets))


def select_records_for_training(dataset, task_ids, *, legacy_archive=ROOT / "benchmark/benchmark.zip", exposed_datasets=()):
    """Only audited train source rows, not model-ready examples.

    Rows still contain private evaluation metadata; a training converter must
    construct its own explicit permitted input/label view.
    """
    audit_dataset(dataset, legacy_archive=legacy_archive, exposed_datasets=exposed_datasets)
    manifest = parse((Path(dataset) / "manifest.json").read_bytes())
    require(isinstance(task_ids, list) and len(task_ids) == len(set(task_ids)), "Expected unique training task IDs")
    by_id = {r["task_id"]: r for r in manifest["tasks"]}
    require(all(i in by_id and by_id[i]["split"] == "train" for i in task_ids), "Only frozen train tasks may enter training")
    return [by_id[i] for i in task_ids]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--exposed-dataset", action="append", type=Path, default=[])
    args = parser.parse_args()
    if args.output:
        require(not args.output.resolve().is_relative_to(args.dataset.resolve()), "Audit output must be outside frozen dataset")
    try:
        report = audit_dataset(args.dataset, exposed_datasets=args.exposed_dataset)
    except (AuditError, OSError, KeyError, TypeError, ValueError) as exc:
        report = {"schema": SCHEMA, "passed": False, "error": type(exc).__name__ + ": " + str(exc), "provider_calls": 0}
    body = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(body)
    print(body, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
