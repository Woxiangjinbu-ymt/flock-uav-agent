"""Freeze structural development coverage from the already exposed benchmark.

Standard library only. Does not import a world, Agent, SDK, pickle, provider or
historical scoring implementation. Model outcomes/costs never enter selection.
The copied original scenes/tasks/checkers are private evaluation material.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATASET = "task-coverage-v1-development18"
BENCHMARK = "benchmark/benchmark.zip"
TEMPLATES = "controller/default_templates.py"
EXPOSURE = "docs/results/baseline-deployment-20261005/tasks.json"
EXPOSURE_SUMS = "docs/results/baseline-deployment-20261005/SHA256SUMS.json"
PROTOCOL = "docs/TASK_COVERAGE_AND_SPLITS_CN.md"

# One predetermined structural slot per template. A three-UAV scene is required
# for the short navigation slot to include an available fleet size other than 5.
SLOTS = (
    ("F1", "basic_takeoff", 1, None), ("F1", "simple_rotation", 1, None),
    ("F1", "complete_flight_cycle", 1, None),
    ("F2", "quick_checkpoint", 1, 3), ("F2", "fly_several_waypoints", 1, None),
    ("F2", "obstacle_avoidance_path", 1, None),
    ("F3", "target_inspection", 1, None), ("F3", "multi_target_sequential", 1, None),
    ("F3", "multiple_target_inspection_advanced", 3, None),
    ("F4", "area_coverage_search", 1, None), ("F4", "dual_drone_area_coverage_search", 2, None),
    ("F4", "target_area_full_coverage", 1, None),
    ("F5", "multi_target_survey", 2, None),
    ("F5", "fleet_coordination_takeoff_and_formation_4drones", 4, None),
    ("F5", "fleet_coordination_target_sweep_5drones", 5, None),
    ("F6", "target_assignment", None, None),
    ("F6", "target_assignment_progress_50_three_targets", None, None),
    ("F6", "area_search_task_completion", None, None),
)
TOOLS = {
    "F1": ["list_drones", "get_drone_status", "take_off", "rotate", "hover", "navigate_to", "return_home", "land"],
    "F2": ["list_drones", "get_drone_status", "take_off", "navigate_to", "move_along_path", "hover", "land"],
    "F3": ["list_drones", "sense_nearby_entities", "get_target_info", "navigate_to", "move_within_distance", "take_photo"],
    "F4": ["list_drones", "sense_nearby_entities", "get_target_info", "generate_coverage_path", "move_along_path_and_sense"],
    "F5": ["list_drones", "sense_nearby_entities", "navigate_to", "move_towards", "hover", "take_photo"],
    "F6": ["list_drones", "sense_nearby_entities", "navigate_to_and_sense", "move_along_path_and_sense", "get_mission_progress"],
}


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
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe(root, name):
    relative = Path(name)
    require(isinstance(name, str) and not relative.is_absolute() and ".." not in relative.parts,
            "Unsafe relative artifact path")
    result = root / relative
    require(not any(p.is_symlink() for p in (result, *result.parents)), "Symlink in artifact path")
    require(result.resolve().is_relative_to(root.resolve()), "Artifact escapes root")
    return result


def leaves(node, path="root"):
    require(isinstance(node, dict), "Checker node must be an object")
    if "checks" in node:
        require(isinstance(node["checks"], list) and node["checks"] and node.get("logic") in {"and", "or"},
                "Invalid checker logic")
        return [leaf for i, child in enumerate(node["checks"]) for leaf in leaves(child, path + "." + str(i))]
    require(isinstance(node.get("endpoint"), str) and node["endpoint"].startswith("/check/"), "Invalid checker leaf")
    return [{"path": path, "definition": node}]


def load_templates(path):
    tree = ast.parse(path.read_bytes())
    assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "DEFAULT_TEMPLATES" for t in n.targets)]
    require(len(assignments) == 1, "Ambiguous template catalog")
    templates = ast.literal_eval(assignments[0].value)
    names = [t["name"] for t in templates.values()]
    require(len(names) == len(set(names)), "Ambiguous template names")
    return templates


def load_pool(path, templates):
    names = {t["name"]: key for key, t in templates.items()}
    scenes, tasks, task_ids = [], [], set()
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        require(len(members) == len(set(members)), "Duplicate benchmark archive member")
        for member in sorted(members):
            if not member.endswith(".json"):
                continue
            require(Path(member).name == member, "Benchmark scene must be a flat JSON member")
            raw = archive.read(member)
            scene = parse(raw)
            require(isinstance(scene, dict) and all(isinstance(scene.get(k), list)
                    for k in ("tasks", "drones", "targets", "obstacles")), "Invalid original scene")
            scene_record = {"scene_id": scene["id"], "member": member, "raw": raw, "scene": scene}
            scenes.append(scene_record)
            for ordinal, task in enumerate(scene["tasks"], 1):
                require(task["id"] not in task_ids, "Duplicate original task ID")
                task_ids.add(task["id"])
                origin = re.fullmatch(r"Task Template (.+) created by .+", task.get("originated_from", ""))
                require(origin is not None and origin[1] in names, "Unknown original task template")
                require(isinstance(task.get("content"), str), "Missing original command")
                leaves(task["execution_check_apis"])
                tasks.append({"scene_record": scene_record, "task": task, "ordinal": ordinal,
                              "template_id": names[origin[1]]})
    require(len({s["scene_id"] for s in scenes}) == len(scenes), "Duplicate scene ID")
    return scenes, tasks


def actors(record):
    command = record["task"]["content"]
    return sorted({"Drone " + n for n in re.findall(r"\bDrone (\d+)\b", command)})


def select(tasks, slots=SLOTS):
    selected, audit = [], []
    ordered = sorted(tasks, key=lambda t: (t["scene_record"]["member"], t["ordinal"]))
    for ordinal, (family, template, required_actors, fleet) in enumerate(slots, 1):
        candidates, rejected = [], []
        for item in ordered:
            if item["template_id"] != template:
                continue
            scene = item["scene_record"]["scene"]
            reasons = []
            if not 1 <= len(scene["drones"]) <= 5:
                reasons.append("available_fleet_outside_1_to_5")
            if fleet is not None and len(scene["drones"]) != fleet:
                reasons.append("slot_requires_available_fleet_" + str(fleet))
            named = actors(item)
            if required_actors is not None and len(named) != required_actors:
                reasons.append("distinct_named_actor_count_mismatch")
            if not set(named) <= {d["name"] for d in scene["drones"]}:
                reasons.append("named_actor_absent_from_original_scene")
            if any(t.get("velocity") or t.get("moving_path") or t.get("movement_mode") for t in scene["targets"]):
                reasons.append("moving_target_outside_v1_static_scope")
            if reasons:
                rejected.append({"scene_id": scene["id"], "task_id": item["task"]["id"], "reasons": reasons})
            else:
                candidates.append(item)
        require(candidates, "No eligible structural candidate: " + template)
        chosen = candidates[0]
        selected.append((family, chosen))
        audit.append({"slot": ordinal, "task_family": family, "template_id": template,
            "required_distinct_named_actors": required_actors, "required_available_fleet": fleet,
            "eligible_order": [{"scene_id": i["scene_record"]["scene_id"], "task_id": i["task"]["id"]}
                               for i in candidates], "selected_task_id": chosen["task"]["id"],
            "rejected_before_execution": rejected})
    require(len({t["task"]["id"] for _, t in selected}) == len(selected), "Duplicate selected task")
    return selected, audit


def exposure_index(raw, task_keys):
    """Deliberate field allowlist: pass/error/token/cost/latency are never read."""
    index = {}
    for row in parse(raw):
        if row.get("cohort") != "first_attempt_no_replay":
            continue
        key = (row["session_id"], row["task_id"])
        require(key in task_keys, "Exposure record not in original benchmark")
        record = {field: row[field] for field in ("model", "cohort", "session_id", "task_id", "generation", "evidence_dir")}
        require(record["generation"] == 1, "Exposure generation is not the first-attempt cohort")
        index.setdefault(key, []).append(record)
    require(set(index) == task_keys, "Incomplete known-development exposure inventory")
    for value in index.values():
        require(len(value) == 2 and {r["model"] for r in value} == {"deepseek", "qwen"}, "Ambiguous two-model exposure record")
    return index


def checker_notes(task):
    flat = leaves(task["execution_check_apis"])
    endpoints = {x["definition"]["endpoint"] for x in flat}
    command = task["content"].lower()
    notes = []
    if "hover" in command and "/check/drone_hovering" not in endpoints:
        notes.append("hover_not_directly_checked_by_original_endpoint")
    if "photo" in command and not any("photo" in endpoint for endpoint in endpoints):
        notes.append("photo_obligation_not_directly_checked")
    if "in sequence" in command:
        notes.append("visit_order_requires_independent_behavior_review")
    if "/check/target_is_reached" in endpoints:
        notes.append("generic_reached_check_does_not_bind_each_visit_to_commanded_actor")
    if any(word in command for word in ("together", "synchronized", "simultaneous")):
        notes.append("temporal_synchronization_not_certified_by_current_endpoint_list")
    leaf_hashes = [sha(canonical(x["definition"])) for x in flat]
    if len(set(leaf_hashes)) < len(leaf_hashes):
        notes.append("duplicate_original_checker_leaves_retained")
    if "at least 3 photos" in command and "/check/drone_has_taken_photo" in endpoints:
        notes.append("photo_count_and_area_subject_binding_require_checker_parameter_review")
    return {"notes": notes, "source": "static_original_instruction_and_checker_structure_only",
        "not_a_solubility_proof": True, "initial_checker": "pending_materialization",
        "geometry_and_instruction_consistency": "pending_independent_review",
        "original_checker_modified": False, "behavioral_obligation_audit_required": True}


def subprograms(template_id):
    # Coverage labels identify supported template families, not a runtime test.
    if template_id in {"basic_takeoff", "simple_rotation", "simple_altitude_change"}:
        return ["simple_chain"]
    if template_id == "target_assignment":
        return ["public_search"]
    return []


def record(family, item, templates, exposures, template_hash):
    task, scene_record = item["task"], item["scene_record"]
    scene = scene_record["scene"]
    template = templates[item["template_id"]]
    named = actors(item)
    return {"task_id": task["id"], "scene_id": scene["id"], "scene_file": "scenes/" + scene_record["member"],
        "task_family": family, "split": "development", "exposure_status": "known_development",
        "fleet_size": len(scene["drones"]), "named_actor_names": named, "named_actor_count": len(named),
        "actor_count_semantics": "distinct_explicit_names" if named else "model_selects_necessary_or_available_actors; actual_count_not_known",
        "command": task["content"], "command_sha256": sha(task["content"].encode()),
        "original_task": task, "original_task_sha256": sha(canonical(task)), "original_ordinal": item["ordinal"],
        "n_checks": len(leaves(task["execution_check_apis"])),
        "checker_sha256": sha(canonical(task["execution_check_apis"])),
        "checker_leaves": leaves(task["execution_check_apis"]),
        "checker_visibility": "private_independent_evaluation_only_never_policy_input",
        "template": {"id": item["template_id"], "name": template["name"], "category": template["category"],
            "originated_from": task["originated_from"], "reference_file": TEMPLATES,
            "reference_file_sha256": template_hash, "reference_definition_sha256": sha(canonical(template)),
            "generation_version": "unknown_current_reference_is_not_proof_of_original_generator_version"},
        "paths": {"raw_tools_available": True, "representative_raw_tools": TOOLS[family],
            "optional_subprogram_candidates": subprograms(item["template_id"]),
            "subprogram_match_status": "template_family_candidate_requires_exact_runtime_grammar_check",
            "path_selected_by_model": "not_run"},
        "initial_state": {"status": "original_scene_available_requires_materialization", "scene_sha256": sha(scene_record["raw"]),
            "snapshot_file": None, "timeline_file": None, "scene_modified": False, "preparatory_actions": [],
            "original_generator_seed": None, "materialization_seed": None},
        "exposure_sources": [{"source_file": EXPOSURE, "scope": "historical_first_attempt_two_model_deployment",
                              **value} for value in exposures[(scene["id"], task["id"])]],
        "exposure_inventory_scope": "conservative_whole_1500_pool; additional_manual_and_module_exposure_not_exhaustively_enumerated",
        "checker_review": checker_notes(task), "execution_status": "not_run", "success": None}


def scene_records(chosen):
    unique = {item["scene_record"]["scene_id"]: item["scene_record"] for _, item in chosen}
    return [{"scene_id": s["scene_id"], "scene_file": "scenes/" + s["member"], "split": "development",
        "original_archive_member": s["member"], "sha256": sha(s["raw"]), "fleet_size": len(s["scene"]["drones"]),
        "task_type": s["scene"]["task_type"], "original_generator_seed": None,
        "target_count": len(s["scene"]["targets"]), "obstacle_count": len(s["scene"]["obstacles"]),
        "initialization": "original_scene_import_without_preparatory_actions"}
        for s in sorted(unique.values(), key=lambda s: s["member"])]


def coverage_record(records, scenes):
    return {"families": dict(Counter(t["task_family"] for t in records)),
        "selected_available_fleet_sizes": sorted({t["fleet_size"] for t in records}),
        "selected_explicit_actor_counts": sorted({t["named_actor_count"] for t in records if t["named_actor_count"]}),
        "original_pool_available_fleet_sizes": sorted({len(s["scene"]["drones"]) for s in scenes}),
        "optional_subprogram_candidate_counts": dict(Counter(p for t in records for p in t["paths"]["optional_subprogram_candidates"])),
        "raw_only_tasks": sum(not t["paths"]["optional_subprogram_candidates"] for t in records),
        "initial_state_materialized_tasks": 0, "model_executed_tasks": 0}


def build(output, *, workspace=ROOT):
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    require(not output.exists(), "Output already exists; frozen coverage is never overwritten")
    templates = load_templates(workspace / TEMPLATES)
    scenes, tasks = load_pool(workspace / BENCHMARK, templates)
    require(len(scenes) == 75 and len(tasks) == 1500, "Expected original 75-scene/1500-task benchmark")
    chosen, selection = select(tasks)
    exposure_raw = (workspace / EXPOSURE).read_bytes()
    sums = parse((workspace / EXPOSURE_SUMS).read_bytes())
    require(sha(exposure_raw) == sums["tasks.json"], "Historical exposure catalog hash mismatch")
    exposures = exposure_index(exposure_raw, {(t["scene_record"]["scene_id"], t["task"]["id"]) for t in tasks})
    template_hash = sha((workspace / TEMPLATES).read_bytes())
    records = [record(family, item, templates, exposures, template_hash) for family, item in chosen]
    unique_scenes = {item["scene_record"]["scene_id"]: item["scene_record"] for _, item in chosen}
    selected_scenes = scene_records(chosen)
    spec = {"schema_version": 1, "dataset_id": DATASET, "split": "development", "planned_tasks": 18,
        "source_population_policy": "all_original_1500_are_known_development_never_unseen_test",
        "selection": "first_eligible_by_archive_member_lexicographic_then_original_task_ordinal",
        "criteria": ["original fleet 1..5 retained unchanged", "explicit template slot", "distinct named actors match slot",
            "named actors exist in original scene", "static target scope", "F2 quick checkpoint requires original fleet 3"],
        "not_used_for_selection": ["historical task success", "passed checks", "model error", "tokens", "cost", "runtime"],
        "slots": [{"task_family": f, "template_id": t, "named_actors": a, "fleet_size": n} for f, t, a, n in SLOTS],
        "online_authorized_executions": 0, "post_materialization_replacement": False}
    limitations = ["development coverage only; no autonomous execution or generalization result",
        "original 1500 tasks all conservatively exposed; old validation60 is not unseen test",
        "available fleet sizes 1,2,4 absent from original pool and not fabricated by deleting actors",
        "original generator version/seed unknown; current template file is reference only",
        "original checker limitations retained; initial checker/geometry/solubility review pending materialization",
        "execution order, current input budget, model and costs are not authorized by this catalog",
        "relay and triple-area-coverage templates have no <=5-fleet candidates in this original pool"]
    coverage = coverage_record(records, scenes)
    readme = render_readme(records, coverage, limitations)
    files = {"selection-spec.json": encoded(spec), "selection-audit.json": encoded({"slots": selection}),
             "README_CN.md": readme.encode(), "PROTOCOL_CN.md": (workspace / PROTOCOL).read_bytes()}
    files.update({"scenes/" + s["member"]: s["raw"] for s in unique_scenes.values()})
    sources = {name: sha((workspace / name).read_bytes()) for name in (BENCHMARK, TEMPLATES, EXPOSURE, EXPOSURE_SUMS)}
    manifest = {"schema_version": 1, "dataset_id": DATASET, "status": "frozen_development_coverage_pending_materialization",
        "visibility": "private_data_and_evaluation_catalog_not_policy_input", "split": "development",
        "online_authorized_executions": 0, "selection_spec_sha256": sha(files["selection-spec.json"]),
        "protocol_source": {"path": PROTOCOL, "sha256": sha(files["PROTOCOL_CN.md"])},
        "builder_sha256": sha(Path(__file__).read_bytes()), "source_files": sources,
        "source_population": {"scenes": 75, "tasks": 1500, "exposure": "known_development", "unseen_tasks": 0},
        "tasks": records, "scenes": selected_scenes, "coverage": coverage, "limitations": limitations,
        "files": {name: sha(value) for name, value in sorted(files.items())}}
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        path = safe(output, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (output / "manifest.json").write_bytes(encoded(manifest))
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return manifest


def render_readme(tasks, coverage, limitations):
    lines = ["# 已知开发覆盖集 v1", "", "18 题，F1–F6 各 3 题；按预先声明的结构条件及原文件顺序选取，不按历史成绩或成本排名。",
        "全部来自已暴露的旧 1500 题，不属于独立 train/dev/test。这里没有新模型执行结果。", "",
        "| 族 | Task ID | Scene ID | 模板 | 可用机队 | 明确参与主体 | 可选子程序候选 |", "| --- | --- | --- | --- | ---: | ---: | --- |"]
    for task in tasks:
        lines.append("| " + " | ".join(map(str, (task["task_family"], task["task_id"], task["scene_id"], task["template"]["name"],
            task["fleet_size"], task["named_actor_count"] or "由模型选择",
            ", ".join(task["paths"]["optional_subprogram_candidates"]) or "原工具"))) + " |")
    lines += ["", "原始场景 JSON 逐字节复制，原任务与 checker 原样保留。manifest 中的 original_task、checker 与场景全量状态仅供私有准备/隔离评分，不能放入模型提示。",
        "", "本包尚未物化 snapshot/timeline，初态评分、恢复一致性及几何可解性待单独的本地 materialization 验收；不得运行后按成绩换题。",
        "", "已明确的检查弱点记录在每题 checker_review：悬停、照片、指定主体、次序或同步义务未必被原 checker 完整检查。保留原分母，需要另记行为证据。",
        "", "参与主体覆盖 1–5；所选场景可用机队是 3/5。旧池没有 1/2/4 机队场景，不删机制造这些规模。新生成划分另行覆盖。",
        "", "开发暴露依据是两模型首次部署清单的身份与证据路径字段；选择器不读取 pass、token、费用或耗时字段。额外人工/模块暴露未逐项穷尽，因此整池保守标为 known_development。",
        "", "## 复核", "", "```bash", "python3 scripts/build_development_coverage.py verify --dataset experiments/task-coverage-v1", "```", "",
        "重建必须指定不存在的新目录。此清单不授权 provider、Teacher 采数或模型训练。", ""]
    return "\n".join(lines)


def verify(dataset, *, workspace=ROOT):
    dataset, workspace = Path(dataset).resolve(), Path(workspace).resolve()
    raw = (dataset / "manifest.json").read_bytes()
    require(sha(raw) == (dataset / "manifest.sha256").read_text().strip(), "Manifest hash mismatch")
    manifest = parse(raw)
    require(manifest["dataset_id"] == DATASET and manifest["split"] == "development"
            and type(manifest["online_authorized_executions"]) is int and manifest["online_authorized_executions"] == 0
            and manifest["source_population"] == {"scenes": 75, "tasks": 1500, "exposure": "known_development", "unseen_tasks": 0},
            "Development scope changed")
    require(not any(p.is_symlink() for p in dataset.rglob("*")), "Symlink in frozen catalog")
    inventory = {str(p.relative_to(dataset)) for p in dataset.rglob("*") if p.is_file()}
    require(inventory == set(manifest["files"]) | {"manifest.json", "manifest.sha256"}, "Catalog inventory changed")
    for name, digest in manifest["files"].items():
        require(sha(safe(dataset, name).read_bytes()) == digest, "Catalog file hash mismatch: " + name)
    require(manifest["selection_spec_sha256"] == sha((dataset / "selection-spec.json").read_bytes())
            and manifest["protocol_source"] == {"path": PROTOCOL, "sha256": sha((dataset / "PROTOCOL_CN.md").read_bytes())},
            "Specification/protocol binding changed")
    require(set(manifest["source_files"]) == {BENCHMARK, TEMPLATES, EXPOSURE, EXPOSURE_SUMS}, "Source inventory changed")
    for name, digest in manifest["source_files"].items():
        require(sha(safe(workspace, name).read_bytes()) == digest, "Source provenance changed: " + name)
    templates = load_templates(workspace / TEMPLATES)
    scenes, pool = load_pool(workspace / BENCHMARK, templates)
    chosen, audit = select(pool)
    require(len(scenes) == 75 and len(pool) == 1500, "Source population changed")
    require(parse((dataset / "selection-audit.json").read_bytes()) == {"slots": audit}, "Structural selection audit changed")
    require([r["task_id"] for r in manifest["tasks"]] == [i["task"]["id"] for _, i in chosen], "Predeclared selected task order changed")
    exposure = exposure_index((workspace / EXPOSURE).read_bytes(), {(t["scene_record"]["scene_id"], t["task"]["id"]) for t in pool})
    expected = [record(family, item, templates, exposure, manifest["source_files"][TEMPLATES]) for family, item in chosen]
    require(manifest["tasks"] == expected, "Task/command/checker/lineage/initial-state record changed")
    require(manifest["scenes"] == scene_records(chosen), "Scene/split/size/provenance metadata changed")
    require(manifest["coverage"] == coverage_record(expected, scenes), "Coverage summary changed")
    for scene in manifest["scenes"]:
        original = next(s for s in scenes if s["scene_id"] == scene["scene_id"])
        require(safe(dataset, scene["scene_file"]).read_bytes() == original["raw"], "Original scene altered")
    require(manifest["coverage"]["families"] == {"F" + str(i): 3 for i in range(1, 7)}, "F1-F6 denominator changed")
    require(manifest["coverage"]["selected_explicit_actor_counts"] == [1, 2, 3, 4, 5], "Actor coverage changed")
    return {"valid": True, "dataset_id": DATASET, "planned_tasks": len(expected), "scenes": len(manifest["scenes"]),
            "source_population_tasks": len(pool), "provider_calls": 0, "manifest_sha256": sha(raw),
            "split": "development", "unseen_tasks": 0, "materialized_tasks": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("build").add_argument("--output", type=Path, required=True)
    sub.add_parser("verify").add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "build":
        result = build(args.output)
        result = {"dataset_id": result["dataset_id"], "tasks": len(result["tasks"]), "scenes": len(result["scenes"]), "provider_calls": 0}
    else:
        result = verify(args.dataset)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
