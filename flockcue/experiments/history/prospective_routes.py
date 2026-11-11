#!/usr/bin/env python3
"""First-attempt R2/R3 pair on prospectively frozen, executable route scenes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

# An archived run copies this file back to its original scripts directory.
_source_dir = Path(__file__).resolve().parent
if _source_dir.name == "history":
    sys.path[:0] = [str(_source_dir.parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]


import m1_progress_pilot as base
import m1_revision2_pilot as r2

r, rp = base.r, base.rp
ROOT = r.ROOT
STUDY = "r3-valid-route-v1"
SPEC = ROOT / "experiments/r3-valid-route-v1"
R2_COMMIT = "69dd34c067ec97b746db5180684594c546a35172"
R3_PATCH = ROOT / "docs/results/m1-revision3-offline-20261008/m1-r3.patch"
R3_HASHES = ROOT / "docs/results/m1-revision3-offline-20261008/source-sha256.json"
ARMS = {"control": "evidence_r2", "r3": "evidence_r3"}
WORKERS = 1


def scene_records():
    manifest = r.read_json(SPEC / "manifest.json")
    if manifest["accepted_required"] != 2 or len(manifest["accepted"]) != 2:
        raise ValueError("Expected two prospectively accepted scenes")
    records = []
    for entry in manifest["accepted"]:
        filename = f"{entry['scene_id']}.json"
        path = SPEC / "data" / filename
        if r.digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Frozen candidate scene changed")
        scene = r.read_json(path)
        if scene["id"] != entry["scene_id"] or len(scene["tasks"]) != 1:
            raise ValueError("Expected one task per new scene")
        task = scene["tasks"][0]
        if task["id"] != entry["task_id"]:
            raise ValueError("Frozen task ID changed")
        records.append({"id": scene["id"], "name": scene["name"], "file": filename,
                        "sha256": entry["sha256"], "family": "prospective_valid_route",
                        "tasks": [{"id": task["id"], "name": task["name"],
                                   "difficulty": task["difficulty"],
                                   "content_sha256": r.digest(task["content"].encode()),
                                   "leaves": r.leaves(task["execution_check_apis"])}]})
    return records


def arm_order():
    ids = [scene["id"] for scene in scene_records()]
    return [[ids[0], "control"], [ids[0], "r3"], [ids[1], "r3"], [ids[1], "control"]]


def r3_agent_sources(control):
    """Rebuild reviewed R3 patch on the pinned R2 commit; verify its hashes."""
    expected = r.read_json(R3_HASHES)
    with tempfile.TemporaryDirectory(prefix="r3-method-", dir=SPEC) as directory:
        method_root = Path(directory)
        for name, content in control.items():
            path = method_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        subprocess.run(["git", "apply", "--check", str(R3_PATCH)], cwd=method_root,
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "apply", str(R3_PATCH)], cwd=method_root,
                       check=True, capture_output=True, text=True)
        method = {str(path.relative_to(method_root)): path.read_bytes()
                  for path in method_root.glob("agent4drone/**/*.py")}
    if set(method) != set(control) | {
            "agent4drone/task_evidence_r3.py", "agent4drone/tests/test_task_evidence_r3.py"}:
        raise ValueError("Unexpected R3 agent source surface")
    for name, content in method.items():
        if name in control and name != "agent4drone/uav_agent.py" and content != control[name]:
            raise ValueError(f"R3 changed unsupported agent file: {name}")
        if name in expected and r.digest(content) != expected[name]:
            raise ValueError(f"R3 reviewed source hash differs: {name}")
    return method


def pilot_plan():
    manifest = r.read_json(ROOT / "experiments/m1-revision2-v1/frozen/fast100.json")
    plan = r2.base_plan(manifest)
    plan["name"] = STUDY
    plan["benchmark_sha256"] = r.digest((SPEC / "manifest.json").read_bytes())
    plan["sessions"] = scene_records()
    plan["expected_tasks"] = sum(len(scene["tasks"]) for scene in plan["sessions"])
    plan["expected_checks"] = sum(len(task["leaves"]) for scene in plan["sessions"] for task in scene["tasks"])
    if (plan["expected_tasks"] != 2 or plan["expected_checks"] != 52
            or plan["protocol"]["timeout_seconds"] != 500):
        raise ValueError("Pilot protocol changed")
    return plan


def prepare():
    if (SPEC / "frozen").exists():
        raise ValueError("Study already frozen")
    if base.git_commit(R2_COMMIT) != R2_COMMIT:
        raise ValueError("Pinned R2 commit unavailable")
    SPEC.mkdir(parents=True, exist_ok=True)
    control = base.agent_sources(R2_COMMIT)
    r3_method = r3_agent_sources(control)
    common = base.common_sources()
    settings, key = base.settings_for_run(ROOT)
    if not key:
        raise ValueError("Official provider key is missing")
    freezes = r2.dependency_freezes(ROOT)
    plan = pilot_plan()
    source_data = SPEC / "data"
    SPEC.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=SPEC))
    try:
        shutil.copyfile(SPEC / "manifest.json", temporary / "candidate-manifest.json")
        shutil.copyfile(SPEC / "server-dry-run.json", temporary / "server-dry-run.json")
        if not r.read_json(temporary / "server-dry-run.json")["all_passed"]:
            raise ValueError("Original server dry-run has not passed")
        for scene in plan["sessions"]:
            origin = source_data / scene["file"]
            if r.digest(origin.read_bytes()) != scene["sha256"]:
                raise ValueError("Frozen original scene changed")
            target = temporary / "data" / scene["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        shutil.copytree(ROOT / "experiments/m1-revision2-v1/frozen/assets", temporary / "assets")
        for arm, sources in (("control", control), ("r3", r3_method)):
            base.write_bundle(temporary / arm, {**common, **sources}, freezes)
        metadata = {"schema": 1, "id": STUDY, "plan": plan,
            "arms": ARMS, "arm_order": arm_order(), "workers": WORKERS,
            "workspace": str(ROOT), "runs_root": str(r.RUNS),
            "configuration": r.public_settings(settings),
            "r2_commit": R2_COMMIT,
            "source_hashes": {arm: r.read_json(temporary / arm / "source_hashes.json") for arm in ARMS},
            "protocol_note": "Two prospectively frozen new scenes, one task each; original checker; 500s cutoff; no model retries.",
            "selection_note": "First two offline-valid fixed-seed candidates; selection never reads model outcomes; separate from original benchmark.",
            "r3_patch_sha256": r.digest(R3_PATCH.read_bytes()),
            "r3_reviewed_hashes_sha256": r.digest(R3_HASHES.read_bytes()),
            "created_at": rp.timestamp()}
        metadata["frozen_files"] = {str(path.relative_to(temporary)): r.digest(path.read_bytes())
                                    for path in temporary.rglob("*") if path.is_file()}
        r.save_json(temporary / "study.json", metadata)
        temporary.rename(SPEC / "frozen")
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps({"study": str(SPEC), "tasks_per_arm": 2,
                      "checks_per_arm": plan["expected_checks"], "model_calls": 0}))


def load_study():
    frozen = SPEC / "frozen"
    study = r.read_json(frozen / "study.json")
    actual = {str(path.relative_to(frozen)): r.digest(path.read_bytes())
              for path in frozen.rglob("*") if path.is_file() and path.name != "study.json"}
    if (study["id"] != STUDY or study["arms"] != ARMS or study["arm_order"] != arm_order()
            or study["plan"] != pilot_plan()
            or actual != study["frozen_files"]):
        raise ValueError("Pilot freeze changed")
    return study


@contextmanager
def mode_environment(mode):
    keys = {"UAV_TASK_PROGRESS_MODE": mode, "UAV_MODEL_REQUEST_AUDIT": "1"}
    previous = {name: os.environ.get(name) for name in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def preflight():
    study = load_study()
    freezes = r2.dependency_freezes(ROOT)
    for arm in ARMS:
        with tempfile.TemporaryDirectory(prefix=".preflight-", dir=SPEC) as tmp:
            runtime = base.materialize_runtime(Path(tmp), SPEC / "frozen", arm, ROOT)
            for component, value in freezes.items():
                if value != (Path(tmp) / f"{component}-freeze.txt").read_text():
                    raise ValueError("Dependency freeze changed")
            command = [str(runtime / "agent4drone/.venv/bin/python"), "-B", "-m", "pytest", "-q",
                "-p", "no:cacheprovider", "agent4drone/tests/test_task_evidence_r2.py",
                *(["agent4drone/tests/test_task_evidence_r3.py"] if arm == "r3" else []),
                "agent4drone/tests/test_task_progress_integration.py"]
            completed = r.subprocess.run(command, cwd=runtime, capture_output=True, text=True, timeout=90,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            (SPEC / f"preflight-{arm}.log").write_text(completed.stdout + completed.stderr)
            if completed.returncode:
                raise ValueError(f"Frozen {arm} offline tests failed")
            if arm == "r3":
                r.subprocess.run([str(runtime / "agent4drone/.venv/bin/python"), "-B", "-c",
                    "import sys; sys.path.insert(0, 'agent4drone'); import task_evidence_r3, uav_agent"],
                    cwd=runtime, check=True, capture_output=True, text=True, timeout=30)
    record = {"ready": True, "model_calls": 0,
              "study_sha256": r.digest((SPEC / "frozen/study.json").read_bytes()),
              "created_at": rp.timestamp()}
    r.save_json(SPEC / "preflight.json", record)
    print(json.dumps(record))


def register_arm(scene_id, arm):
    study = load_study()
    preflight_record = r.read_json(SPEC / "preflight.json")
    if not preflight_record["ready"] or preflight_record["study_sha256"] != r.digest((SPEC / "frozen/study.json").read_bytes()):
        raise ValueError("Pilot preflight missing or stale")
    settings, key = base.settings_for_run(ROOT)
    if not key or r.public_settings(settings) != study["configuration"]:
        raise ValueError("Provider settings changed")
    with rp.runner_lock(r):
        registry = SPEC / "runs.json"
        records = r.read_json(registry) if registry.exists() else {}
        order = arm_order()
        key_name = f"{scene_id}:{arm}"
        completed = [(sid, method) for sid, method in order
                     if records.get(f"{sid}:{method}", {}).get("status") == "completed_and_verified"]
        if key_name in records or len(completed) >= len(order) or order[len(completed)] != [scene_id, arm]:
            raise ValueError("First-attempt scene/arm order changed or prior attempt is incomplete")
        attempt = r.RUNS / (time.strftime("%Y%m%d-%H%M%S") + f"-valid-route-{scene_id}-{arm}-" + uuid.uuid4().hex[:8])
        attempt.mkdir(mode=0o700)
        runtime = base.materialize_runtime(attempt, SPEC / "frozen", arm, ROOT)
        plan = copy.deepcopy(study["plan"])
        plan["sessions"] = [scene for scene in plan["sessions"] if scene["id"] == scene_id]
        if len(plan["sessions"]) != 1:
            raise ValueError("Unknown scene")
        plan["expected_tasks"] = 1
        plan["expected_checks"] = len(plan["sessions"][0]["tasks"][0]["leaves"])
        plan["study"] = {"id": STUDY, "arm": arm, "scene_id": scene_id,
                         "registry_key": key_name, "runs_root": str(r.RUNS),
                         "study_directory": str(SPEC), "workspace": str(ROOT),
                         "runtime_env": {"UAV_TASK_PROGRESS_MODE": ARMS[arm], "UAV_MODEL_REQUEST_AUDIT": "1"}}
        r.save_json(attempt / "plan.json", plan)
        r.save_json(attempt / "configuration.json", r.public_settings(settings))
        r.save_json(attempt / "agent-settings.json", settings)
        r.save_json(attempt / "gateway_preflight.json", {"offline_ready": True, "configuration": study["configuration"]})
        identity = rp.identity(r, attempt, WORKERS)
        r.save_json(attempt / "identity.json", identity)
        r.save_json(attempt / "manifest.json", {"status": "created", "started_at": rp.timestamp(),
            "study": STUDY, "arm": arm, "plan": STUDY, "benchmark_sha256": plan["benchmark_sha256"],
            "protocol": plan["protocol"], "identity_sha256": rp.json_digest(r, identity),
            "executor": {"id": rp.EXECUTOR_ID, "workers": WORKERS, "automatic_retries": 0,
                         "recovery": "disabled", "provider_error_policy": rp.PROVIDER_ERROR_POLICY},
            "scenes": {scene["id"]: {"status": "pending", "generation": 0, "directory": None, "history": []}
                       for scene in plan["sessions"]}})
        records[key_name] = {"attempt": str(attempt), "status": "prepared", "created_at": rp.timestamp()}
        r.save_json(registry, records)
    return attempt, runtime


def launch_arm(scene_id, arm):
    attempt, runtime = register_arm(scene_id, arm)
    print(json.dumps({"scene_id": scene_id, "arm": arm, "attempt": str(attempt), "action": "starting"}), flush=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
           "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
           "UAV_TASK_PROGRESS_MODE": ARMS[arm], "UAV_MODEL_REQUEST_AUDIT": "1"}
    os.execve(str(runtime / "controller/.venv/bin/python"),
              [str(runtime / "controller/.venv/bin/python"), "-B",
               str(runtime / "scripts/r3_valid_route_pilot.py"), "_execute", str(attempt)], env)


def execute(attempt):
    attempt = Path(attempt).resolve()
    if ROOT.resolve() != attempt / "runtime":
        raise ValueError("Must execute from frozen runtime")
    plan = r.read_json(attempt / "plan.json")
    arm = plan["study"]["arm"]
    registry_key = plan["study"]["registry_key"]
    r.RUNS, r.DATA = Path(plan["study"]["runs_root"]), attempt / "runtime/data"
    r.load_plan = lambda name: copy.deepcopy(plan) if name == STUDY else (_ for _ in ()).throw(ValueError("Unknown plan"))
    settings, key = base.settings_for_run(plan["study"]["workspace"])
    records_path = Path(plan["study"]["study_directory"]) / "runs.json"
    if not key or r.public_settings(settings) != r.read_json(attempt / "configuration.json"):
        raise ValueError("Provider settings changed")
    original_run_scene = rp.run_scene
    def stop_on_failure(*args, **kwargs):
        result = original_run_scene(*args, **kwargs)
        if result["status"] != "completed":
            args[6].set()
        return result
    with rp.runner_lock(r), rp.attempt_lock(attempt):
        records = r.read_json(records_path)
        if records[registry_key]["status"] != "prepared":
            raise ValueError("Arm is not a fresh first attempt")
        rp.verify_provenance(r, attempt, plan, settings)
        records[registry_key].update(status="running", started_at=rp.timestamp(), pid=os.getpid())
        r.save_json(records_path, records)
        old_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
        ok, failure = False, None
        try:
            rp.run_scene = stop_on_failure
            with mode_environment(ARMS[arm]):
                r.save_json(attempt / "execution-environment.json", {"runtime_env": plan["study"]["runtime_env"],
                    "runtime_root": str(ROOT), "recorded_at": rp.timestamp()})
                ok = rp.coordinate(r, attempt, plan, settings, key, r.read_json(attempt / "manifest.json"), {})
                if ok:
                    rp.verify_provenance(r, attempt, plan, settings)
        except BaseException as exc:
            failure = type(exc).__name__
            raise
        finally:
            rp.run_scene = original_run_scene
            signal.signal(signal.SIGTERM, old_handler)
            records[registry_key].update(status="awaiting_verification" if ok else "interrupted",
                                finished_at=rp.timestamp(), exception_type=failure)
            r.save_json(records_path, records)
            base.seal(attempt)
        if ok:
            verify(attempt)
            records[registry_key]["status"] = "completed_and_verified"
            r.save_json(records_path, records)
        return ok


def verify(attempt):
    attempt = Path(attempt).resolve()
    plan = r.read_json(attempt / "plan.json")
    expected = r.read_json(attempt / "suite-seal.json")["files"]
    actual = {str(path.relative_to(attempt)): r.digest(path.read_bytes())
              for path in attempt.rglob("*") if path.is_file() and path.name != "suite-seal.json"}
    if actual != expected:
        raise ValueError("Sealed attempt changed")
    rp.verify_archive(r, attempt)
    manifest = r.read_json(attempt / "manifest.json")
    original_root, original_data = r.ROOT, r.DATA
    try:
        r.ROOT, r.DATA = attempt / "runtime", attempt / "runtime/data"
        for scene in plan["sessions"]:
            record = manifest["scenes"][scene["id"]]
            if record["generation"] != 1:
                raise ValueError("Not a first attempt")
            rp.verify_scene(r, attempt, plan, scene, record)
    finally:
        r.ROOT, r.DATA = original_root, original_data
    summary = r.summarize(plan, r.read_json(attempt / "results.json"), manifest["status"])
    if not summary["valid_complete_run"]:
        raise ValueError("Incomplete pilot arm")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("preflight")
    run = sub.add_parser("run")
    run.add_argument("--arm", choices=tuple(ARMS), required=True)
    run.add_argument("--scene-id", required=True)
    sub.add_parser("_execute").add_argument("attempt", type=Path)
    sub.add_parser("verify").add_argument("attempt", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "preflight":
        preflight()
    elif args.command == "run":
        launch_arm(args.scene_id, args.arm)
    elif args.command == "_execute":
        return 0 if execute(args.attempt) else 2
    elif args.command == "verify":
        print(json.dumps(verify(args.attempt), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
