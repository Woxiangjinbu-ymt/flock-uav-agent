#!/usr/bin/env python3
"""Fixed, public-API solvability checks, never model/Teacher trajectories.

The supervisor starts one local process per scene. Each task and negative control
restores its initial snapshot; checker access is confined to the evaluator.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from prepare_task_dataset import ROOT, load_dataset, safe_path, sha, write


def verify_recipe(command, recipe):
    if recipe.get("version") != 1 or not isinstance(recipe.get("steps"), list) or not recipe["steps"]:
        raise ValueError("Unsupported quality witness")
    def mentioned(name):
        return isinstance(name, str) and bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", command))
    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value)
    altitudes = {float(n) for n in re.findall(r"take off to ([-+]?\d+(?:\.\d+)?) meters", command)}
    headings = {float(n) for n in re.findall(r"heading ([-+]?\d+(?:\.\d+)?) degrees", command)}
    fields = {"take_off": {"op", "drone", "altitude"}, "rotate": {"op", "drone", "heading"},
              "hover": {"op", "drone"}, "take_photo": {"op", "drone"},
              "move_to": {"op", "drone", "position", "source"},
              "visit_target": {"op", "drone", "target", "altitude"},
              "cover_target": {"op", "drone", "target", "altitude", "spacing"}}
    for step in recipe["steps"]:
        op = step.get("op")
        if op not in fields or set(step) - fields[op]:
            raise ValueError("Unknown or private witness field")
        fleet_choice = "available fleet" in command or "any available drones" in command
        if not isinstance(step.get("drone"), str) or (not mentioned(step["drone"]) and not fleet_choice):
            raise ValueError("Witness actor absent from command")
        all_targets = "all non-waypoint targets" in command
        if "target" in step and not mentioned(step["target"]) and not all_targets:
            raise ValueError("Witness target absent from command")
        for key in ("altitude", "heading"):
            allowed_default = key == "altitude" and not altitudes and step.get(key) == 20 and recipe.get("constants", {}).get("default_flight_altitude") == 20
            allowed_values = altitudes if key == "altitude" else headings
            if key in step and (not numeric(step[key]) or (float(step[key]) not in allowed_values and not allowed_default)):
                raise ValueError("Witness numeric instruction absent from command")
        if op == "move_to":
            if step.get("source") != "command" or set(step.get("position", {})) != {"x", "y", "z"}:
                raise ValueError("Private waypoint source")
            if not all(numeric(n) for n in step["position"].values()):
                raise ValueError("Invalid public waypoint number")
            point = tuple(float(step["position"][axis]) for axis in ("x", "y", "z"))
            triples = {tuple(map(float, match)) for match in re.findall(
                r"\(\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\)", command)}
            if point not in triples:
                raise ValueError("Waypoint tuple absent from public instruction")
        if op == "cover_target" and (not numeric(step.get("spacing")) or not 1 <= step["spacing"] <= 10):
            raise ValueError("Unbounded coverage spacing")
    omit = recipe.get("negative_omit_step_index")
    if type(omit) is not int or not 0 <= omit < len(recipe["steps"]):
        raise ValueError("Missing predetermined negative control")


def execute_public_recipe(command, recipe, request, *, negative=False):
    """Receives no scene/checker/snapshot; resolves all target geometry publicly."""
    verify_recipe(command, recipe)
    public_drones = request("GET", "/drones")
    by_name = {}
    for drone in public_drones:
        if drone["name"] in by_name:
            raise ValueError("Ambiguous public actor")
        by_name[drone["name"]] = drone["id"]
    discovered = {}
    # Record only entities actually returned by the allowed public sensor.
    for drone in public_drones:
        nearby = request("GET", f"/drones/{drone['id']}/nearby")
        for target in nearby.get("targets", []):
            prior = discovered.get(target["name"])
            if prior is not None and prior != target["id"]:
                raise ValueError("Ambiguous public target")
            discovered[target["name"]] = target["id"]
    actions = []
    for index, step in enumerate(recipe["steps"]):
        if negative and index == recipe["negative_omit_step_index"]:
            continue
        actor = by_name[step["drone"]]
        op = step["op"]
        endpoint = f"/drones/{actor}/command/"
        params = {}
        payload = None
        if op in {"take_off", "rotate"}:
            params = {k: step[k] for k in ("altitude", "heading") if k in step}
        elif op == "move_to":
            params = step["position"]
        elif op in {"visit_target", "cover_target"}:
            target_id = discovered.get(step["target"])
            if target_id is None:
                raise ValueError("Witness target has not been publicly observed")
            target = request("GET", f"/targets/{target_id}")
            if target["id"] != target_id or target["name"] != step["target"]:
                raise ValueError("Public target identity changed")
            x, y = target["position"]["x"], target["position"]["y"]
            if op == "visit_target":
                op, params = "move_to", {"x": x, "y": y, "z": step["altitude"]}
            else:
                if target["type"] != "circle" or not 0 < target["radius"] <= 100:
                    raise ValueError("Coverage witness supports bounded public circles")
                radius, spacing = float(target["radius"]), float(step["spacing"])
                # Cover a bounding square with a fixed boustrophedon path.
                lines = math.ceil(2 * radius / spacing)
                points = []
                for row in range(lines + 1):
                    yy = y - radius + 2 * radius * row / lines
                    xs = (x - radius, x + radius) if row % 2 == 0 else (x + radius, x - radius)
                    points.extend({"x": xx, "y": yy, "z": step["altitude"]} for xx in xs)
                op, payload = "move_along_path", {"waypoints": points, "allow_partial_move": False}
        kwargs = {"json": payload} if payload is not None else ({"params": params} if params else {})
        result = request("POST", endpoint + op, **kwargs)
        actions.append({"recipe_step": index, "endpoint": endpoint + op, "result": result})
        if result.get("status") != "success":
            raise ValueError(f"Quality witness action failed: {op}")
    return {"public_target_ids": sorted(discovered.values()), "actions": actions}


def verify_sources(output):
    manifest_bytes = (output / "run-manifest.json").read_bytes()
    if sha(manifest_bytes) != (output / "run-manifest.sha256").read_text().strip():
        raise ValueError("Quality run manifest changed")
    manifest = json.loads(manifest_bytes)
    for name, expected in manifest["sources"].items():
        if sha(safe_path(ROOT, name).read_bytes()) != expected:
            raise ValueError(f"Source changed during quality audit: {name}")
    dataset = Path(manifest["dataset"]).resolve()
    if sha((dataset / "manifest.json").read_bytes()) != manifest["dataset_manifest_sha256"]:
        raise ValueError("Dataset version changed during quality audit")
    load_dataset(dataset)
    return manifest


def scene_worker(dataset, output, scene_id):
    run_manifest = verify_sources(output)
    if Path(run_manifest["dataset"]).resolve() != Path(dataset).resolve():
        raise ValueError("Worker dataset path changed")
    manifest, scenes = load_dataset(dataset)
    scene, raw = scenes[scene_id]
    from harness_full100_world import FullWorld
    from harness_natural_runner import task_request_allowed
    from harness_v2_world import digest
    from prepare_task_dataset import flatten

    class PublicWorld(FullWorld):
        def request(self, method, endpoint, *, admin=False, **kwargs):
            if self.phase == "task" and not task_request_allowed(method, endpoint, admin):
                raise PermissionError("Private evaluator unavailable to quality solver")
            return super().request(method, endpoint, admin=admin, **kwargs)

    world = PublicWorld(seed=scene["seed"])
    world.import_session(raw)
    blob = world.snapshot()
    initial_state_hash = world.state_digest()
    initial_public_hash = digest(world.public_seed())
    rows = []
    for task in [t for t in manifest["tasks"] if t["scene_id"] == scene_id]:
        row = {"task_id": task["task_id"], "scene_id": scene_id, "split": task["split"],
               "task_family": task["task_family"], "passed": False, "provider_calls": 0}
        try:
            world.restore(blob, sha(blob))
            initial = world.evaluate(task["task_id"], blob)
            row["initial_checker"] = initial
            if initial["passed"] or initial["errors"] or initial["checks_expected"] != len(flatten(task["original_task"]["execution_check_apis"])):
                raise ValueError("Initially satisfied or invalid checker")
            for name, negative in (("positive", False), ("missing_required_step", True)):
                world.restore(blob, sha(blob))
                world.closed = False
                world.score_blob = None
                world.score_seconds = None
                world.phase = "preparation"
                world.requests, world.physical_events = [], []
                if world.state_digest() != initial_state_hash or digest(world.public_seed()) != initial_public_hash:
                    raise ValueError("Restoration changed initial state/observations")
                world.start(timer=False)
                action_error = None
                try:
                    public_trace = execute_public_recipe(task["command"], task["quality"]["solver_recipe"],
                                                        world.request, negative=negative)
                except Exception as exc:
                    public_trace = None
                    action_error = f"{type(exc).__name__}: {exc}"
                score_blob = world.freeze_score(reason="quality_script_finished")
                score = world.evaluate(task["task_id"], score_blob)
                run = {"actions": public_trace, "error": action_error, "checker": score,
                       "public_requests": [r for r in world.requests if r["phase"] == "task"],
                       "physical_events": copy.deepcopy(world.physical_events)}
                row[name] = run
                if action_error or score["errors"] or score["passed"] == negative:
                    raise ValueError(f"Quality control failed: {name}")
            row["passed"] = True
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    verify_sources(output)
    write(output / "scenes" / f"{scene_id}.json", {"scene_id": scene_id, "rows": rows,
          "passed": all(r["passed"] for r in rows), "initial_state_sha256": initial_state_hash,
          "initial_public_sha256": initial_public_hash})


def audit(dataset, output):
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Quality audits cannot be overwritten")
    if output.is_relative_to(dataset):
        raise ValueError("Output must not modify the frozen dataset")
    initial_manifest_bytes = (dataset / "manifest.json").read_bytes()
    manifest, _ = load_dataset(dataset)
    if initial_manifest_bytes != (dataset / "manifest.json").read_bytes():
        raise ValueError("Dataset changed during admission")
    if "generator_version" not in manifest:
        raise ValueError("This fixed witness protocol applies only to the new generated dataset")
    output.mkdir(parents=True)
    sources = [p for p in (ROOT / "server").rglob("*.py")
               if not any(x in {".venv", "tests", "__pycache__"} for x in p.parts)]
    sources += [ROOT / "scripts" / n for n in ("validate_task_dataset.py", "prepare_task_dataset.py",
                 "harness_v2_world.py", "harness_full100_world.py", "harness_natural_runner.py",
                 "harness_episode_local.py", "validate_harness_episode.py")]
    source_hashes = {}
    for path in sources:
        name = str(path.relative_to(ROOT))
        source_hashes[name] = sha(path.read_bytes())
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    write(output / "run-manifest.json", {"schema_version": 1, "dataset": str(dataset),
          "dataset_manifest_sha256": sha(initial_manifest_bytes),
          "sources": source_hashes, "planned_tasks": len(manifest["tasks"]),
          "exposure": "predeclared automatic quality audit only; no model or Agent tuning",
          "provider_calls": 0, "python": sys.version, "python_executable": sys.executable})
    (output / "run-manifest.sha256").write_text(sha((output / "run-manifest.json").read_bytes()) + "\n")
    processes = []
    for scene in manifest["scenes"]:
        ident = scene["scene_id"]
        with (output / f"{ident}.console.log").open("x") as stream:
            try:
                process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--dataset", str(dataset),
                    "--output", str(output), "--scene-id", ident], stdout=stream, stderr=subprocess.STDOUT,
                    timeout=120, check=False)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = "timeout_killed_and_joined"
        processes.append({"scene_id": ident, "exit_code": code})
        print(json.dumps(processes[-1]), flush=True)
    verify_sources(output)
    rows = []
    for scene in manifest["scenes"]:
        path = output / "scenes" / f"{scene['scene_id']}.json"
        if path.exists():
            rows.extend(json.loads(path.read_text())["rows"])
    result = {"schema_version": 1, "planned_tasks": len(manifest["tasks"]), "audited_tasks": len(rows),
              "passed_tasks": sum(r["passed"] for r in rows), "failed_task_ids": [r["task_id"] for r in rows if not r["passed"]],
              "unreported_task_ids": sorted({t["task_id"] for t in manifest["tasks"]} - {r["task_id"] for r in rows}),
              "passed": len(rows) == len(manifest["tasks"]) and all(r["passed"] for r in rows) and all(p["exit_code"] == 0 for p in processes),
              "processes": processes, "provider_calls": 0, "model_performance": "not_evaluated",
              "teacher_eligible": False, "scope": "scripted public-action solvability and checker negative controls"}
    write(output / "summary.json", result)
    files = {str(p.relative_to(output)): sha(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file()}
    write(output / "seal.json", {"files": files})
    (output / "seal.sha256").write_text(sha((output / "seal.json").read_bytes()) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.scene_id:
        scene_worker(args.dataset.resolve(), args.output.resolve(), args.scene_id)
    else:
        result = audit(args.dataset, args.output)
        print(json.dumps(result))
        if not result["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
