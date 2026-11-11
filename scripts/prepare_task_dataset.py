#!/usr/bin/env python3
"""Materialize sealed W2 scenes for the existing local episode entry point.

Only locally generated, hash-verified snapshots are written. This entry neither
calls a model nor turns task definitions/quality witnesses into training samples.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def parse_json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError("Non-finite JSON number: " + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)


def safe_path(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or any(x in {"..", "."} for x in relative.parts):
        raise ValueError("Unsafe dataset path")
    candidate = root / relative
    if any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
        raise ValueError("Symlink in dataset")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Dataset path escaped")
    return candidate


def load_dataset(dataset):
    dataset = Path(dataset).resolve()
    content = safe_path(dataset, "manifest.json").read_bytes()
    if sha(content) != safe_path(dataset, "manifest.sha256").read_text().strip():
        raise ValueError("Dataset manifest changed")
    manifest = parse_json(content)
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("Dataset file inventory missing")
    for name, expected in manifest["files"].items():
        if sha(safe_path(dataset, name).read_bytes()) != expected:
            raise ValueError(f"Dataset hash mismatch: {name}")
    if any(p.is_symlink() for p in dataset.rglob("*")):
        raise ValueError("Symlink in dataset inventory")
    inventory = {str(p.relative_to(dataset)) for p in dataset.rglob("*") if p.is_file()}
    if inventory != set(manifest["files"]) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("Dataset inventory changed")
    scenes = manifest.get("scenes", [])
    tasks = manifest.get("tasks", [])
    if not scenes or not tasks:
        raise ValueError("Empty dataset")
    if len({s["scene_id"] for s in scenes}) != len(scenes) or len({t["task_id"] for t in tasks}) != len(tasks):
        raise ValueError("Duplicate scene/task ID")
    by_scene = {}
    for scene in scenes:
        if scene.get("split") not in {"development", "train", "dev", "test"}:
            raise ValueError("Unknown scene split")
        if scene.get("seed") is not None and (type(scene["seed"]) is not int or scene["seed"] < 0):
            raise ValueError("Invalid scene seed")
        path = scene["scene_file"]
        if path not in manifest["files"]:
            raise ValueError("Unsealed scene")
        raw = parse_json(safe_path(dataset, path).read_bytes())
        if raw["id"] != scene["scene_id"]:
            raise ValueError("Scene identity mismatch")
        by_scene[scene["scene_id"]] = (scene, raw)
    for task in tasks:
        scene, raw = by_scene[task["scene_id"]]
        original = [t for t in raw["tasks"] if t["id"] == task["task_id"]]
        if (len(original) != 1 or original[0] != task["original_task"]
                or task["command"] != original[0]["content"]
                or sha(task["command"].encode()) != task["command_sha256"]
                or task["scene_file"] != scene["scene_file"]
                or task["split"] != scene["split"]
                or task["fleet_size"] != len(raw["drones"])):
            raise ValueError("Task/source/split binding mismatch")
    return manifest, by_scene


def select_tasks(manifest, split, purpose):
    if split not in {"development", "train", "dev"}:
        raise ValueError("Test is reserved for quality audit and frozen evaluation, not development/Teacher export")
    if purpose not in {"development", "teacher_training"}:
        raise ValueError("Unknown dataset use")
    if purpose == "teacher_training" and split != "train":
        raise ValueError("Teacher training may only use the train split")
    selected = [t for t in manifest["tasks"] if t["split"] == split]
    if not selected:
        raise ValueError("No tasks in requested split")
    return selected


def flatten(node, path="root"):
    if isinstance(node.get("checks"), list):
        return [leaf for i, child in enumerate(node["checks"]) for leaf in flatten(child, f"{path}.{i}")]
    if not str(node.get("endpoint", "")).startswith("/check/"):
        raise ValueError("Invalid checker leaf")
    return [{"path": path, "definition": node}]


def materialize(dataset, output, *, split="development", purpose="development", timeline_seconds=501):
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Cannot overwrite an existing study")
    if output.is_relative_to(dataset):
        raise ValueError("Output must not modify the frozen dataset")
    if type(timeline_seconds) is not int or not 1 <= timeline_seconds <= 501:
        raise ValueError("Invalid timeline bound")
    initial_manifest_bytes = (dataset / "manifest.json").read_bytes()
    manifest, scenes = load_dataset(dataset)
    if initial_manifest_bytes != (dataset / "manifest.json").read_bytes():
        raise ValueError("Dataset changed during admission")
    selected = select_tasks(manifest, split, purpose)
    # Imports are delayed until all manifest/selection gates have passed.
    from harness_full100_world import FullWorld
    from harness_v2_world import EPOCH, digest

    output.mkdir(parents=True)
    source_paths = [p for p in (ROOT / "server").rglob("*.py")
                    if not any(x in {".venv", "tests", "__pycache__"} for x in p.parts)]
    source_paths += [ROOT / "scripts" / n for n in
                     ("prepare_task_dataset.py", "harness_full100_world.py", "harness_v2_world.py")]
    for path in source_paths:
        target = output / "source" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    (output / "origin-manifest.json").write_bytes(initial_manifest_bytes)
    reports = []
    for scene_id in sorted({t["scene_id"] for t in selected}):
        scene, raw = scenes[scene_id]
        seed = scene.get("seed")
        if seed is None:
            # This controls simulator weather/IDs only, not unknown historical generation.
            seed = int(sha(scene_id.encode())[:16], 16) ^ 20261103
        world = FullWorld(seed=seed)
        world.import_session(raw)
        blob = world.snapshot()
        state_hash = world.state_digest()
        timeline = world.bake_timeline(timeline_seconds)
        if world.state_digest() != state_hash:
            raise ValueError("Timeline changed initial state")
        public_hash = digest(world.public_seed())
        world.restore(blob, sha(blob))
        timeline_name = f"scene-{scene_id}.timeline.pkl"
        folder = output / "fixtures"
        folder.mkdir(exist_ok=True)
        (folder / timeline_name).write_bytes(timeline)
        source_scene = output / "scenes" / f"{scene_id}.json"
        source_scene.parent.mkdir(exist_ok=True)
        source_scene.write_bytes(safe_path(dataset, scene["scene_file"]).read_bytes())
        for task in [t for t in selected if t["scene_id"] == scene_id]:
            world.restore(blob, sha(blob))
            initial = world.evaluate(task["task_id"], blob)
            checks = flatten(task["original_task"]["execution_check_apis"])
            expected = len(checks)
            if initial["errors"] or initial["checks_expected"] != expected:
                raise ValueError(f"Invalid checker: {task['task_id']}")
            first = next(iter(world.server.drone_controller.drones.values()))
            first.battery_volume = 0
            first.history_status.append({"quality_restore_canary": True})
            world.rng.random()
            world.restore(blob, sha(blob))
            if world.state_digest() != state_hash or digest(world.public_seed()) != public_hash:
                raise ValueError("Restoration changed state/public observations")
            if world.evaluate(task["task_id"], blob) != initial:
                raise ValueError("Restoration changed checker")
            ident = task["task_id"]
            (folder / f"{ident}.pkl").write_bytes(blob)
            fixture = {k: v for k, v in task.items() if k != "quality"}
            fixture.update(checks=checks, n_checks=expected, state_status="materialized",
                           snapshot_sha256=sha(blob), state_sha256=state_hash,
                           public_startup_sha256=public_hash, timeline_file=timeline_name,
                           timeline_sha256=sha(timeline), initial_checker=initial,
                           task_clock_epoch=EPOCH, simulator_seed=seed,
                           generation_seed=scene.get("seed"),
                           preparation_script="none; independent restore of complete source scene",
                           source=str(source_scene.relative_to(output)), source_sha256=sha(source_scene.read_bytes()))
            write(folder / f"{ident}.json", fixture)
            reports.append({"task_id": ident, "scene_id": scene_id, "task_family": task["task_family"],
                            "checks_expected": expected, "initial_passed": initial["passed"],
                            "restore_verified": True, "checker_errors": 0})
        print(json.dumps({"scene_id": scene_id, "materialized_tasks": len(reports)}), flush=True)
    write(output / "preflight.json", {"schema_version": 1, "provider_calls": 0,
          "tasks": len(reports), "scenes": len({r["scene_id"] for r in reports}),
          "initially_satisfied": [r["task_id"] for r in reports if r["initial_passed"]], "rows": reports,
          "scope": "initial state/checker/restoration only; no claim of solvability or agent success"})
    files = {str(p.relative_to(output)): sha(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file()}
    for path in source_paths:
        if sha(path.read_bytes()) != files[str(Path("source") / path.relative_to(ROOT))]:
            raise ValueError("Materialization source changed during execution")
    load_dataset(dataset)
    if initial_manifest_bytes != (dataset / "manifest.json").read_bytes():
        raise ValueError("Dataset changed during materialization")
    result = {"schema_version": 1, "scope": "task_dataset_materialization_v1", "split": split,
              "purpose": purpose, "online_authorized_executions": 0,
              "origin_dataset": str(dataset), "origin_manifest_sha256": sha(initial_manifest_bytes),
              "tasks": reports, "timeline_seconds": timeline_seconds, "files": files}
    write(output / "manifest.json", result)
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "train", "dev"), default="development")
    parser.add_argument("--purpose", choices=("development", "teacher_training"), default="development")
    parser.add_argument("--timeline-seconds", type=int, default=501)
    args = parser.parse_args()
    materialize(args.dataset, args.output, split=args.split, purpose=args.purpose, timeline_seconds=args.timeline_seconds)


if __name__ == "__main__":
    main()
