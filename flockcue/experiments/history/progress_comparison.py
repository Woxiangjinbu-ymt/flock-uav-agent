#!/usr/bin/env python3
"""Frozen fast100 M0/M1 study. prepare is offline; run explicitly calls the model.

M0 is the historical remedies implementation, executed afresh. M1 uses an
explicit Git commit. Scoring, deadlines and scene execution remain in the
existing reproduction runner. No recovery, hidden extra smoke, or best-of runs.
"""
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
import zipfile

# An archived run copies this file back to its original scripts directory.
_source_dir = Path(__file__).resolve().parent
if _source_dir.name == "history":
    sys.path[:0] = [str(_source_dir.parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]


import reproduction as r
import reproduction_parallel as rp

ROOT = r.ROOT
SPEC = ROOT / "experiments/m1-progress-v1"
DATA = ROOT / "experiments/subsets/representative-v1"
STUDY = "m1-progress-fast100-v1"
M0_COMMIT = "cb80fde8269a09795782a6ef046d04c381b3f3c9"
FAST_SHA256 = "26f9be1a785d6f87a4f1fd5fbb4160a8941909f6bd8592a9f082b6bbb0a08381"
SCENES = ("ad34350a", "ef36e27a", "3973d0d6", "823c15d5", "35ce64d8")
MODE_VARIABLE = "UAV_TASK_PROGRESS_MODE"
MODES = {"m0": "off", "m1": "summary"}
WORKERS = 2


def git_commit(ref):
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT,
        text=True, stderr=subprocess.DEVNULL).strip()


def agent_sources(commit):
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", commit, "agent4drone"],
        cwd=ROOT, text=True).splitlines()
    sources = {name: subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
               for name in names if name.endswith(".py")}
    if "agent4drone/uav_agent.py" not in sources:
        raise ValueError("Method commit has no Agent4Drone Python implementation")
    return sources


def source_files():
    # A detached study may supply the original flat reproduction module.
    if hasattr(r, "source_files"):
        return r.source_files()
    return {name: ROOT / name for name in r.current_source_hashes()}


def common_sources():
    # Requirements are recorded identically; method Python never comes from
    # the live worktree. The actual three dependency environments are frozen too.
    return {name: path.read_bytes() for name, path in source_files().items()
            if not (name.startswith("agent4drone/") and name.endswith(".py"))}


def settings_for_run(workspace):
    provider = copy.deepcopy(r.read_json(Path(workspace) / "agent4drone/llm_settings.json")
                             ["provider_configs"]["deepseek_official"])
    if (provider.get("type"), provider.get("base_url", "").rstrip("/"), provider.get("default_model")) != (
            "openai-compatible", "https://api.deepseek.com", "deepseek-flash"):
        raise ValueError("Expected official deepseek-flash configuration")
    key = provider.pop("api_key", "") or os.environ.get("DEEPSEEK_API_KEY")
    provider["api_key"] = ""
    settings = {"selected_provider": "deepseek_official", "provider_configs": {"deepseek_official": provider},
                "uav_api_key": "", "share_blackboard_by_session": True, "toolchain_json_recording": True}
    return settings, key


def base_plan(manifest):
    if (manifest["split"], manifest["expected_tasks"], manifest["expected_checks"],
            tuple(s["id"] for s in manifest["sessions"])) != ("fast100", 100, 605, SCENES):
        raise ValueError("Expected frozen fast100 with original scene order and 100/605 denominator")
    if manifest["execution_protocol"]["timeout_seconds"] != 500:
        raise ValueError("Task action budget must remain 500 seconds")
    return {"schema": 1, "name": STUDY, "benchmark_sha256": manifest["benchmark_sha256"],
            "protocol": copy.deepcopy(manifest["execution_protocol"]),
            "expected_tasks": 100, "expected_checks": 605, "sessions": copy.deepcopy(manifest["sessions"])}


def write_bundle(directory, sources, dependency_freezes):
    directory.mkdir(parents=True)
    with zipfile.ZipFile(directory / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(sources.items()):
            archive.writestr(name, content)
    r.save_json(directory / "source_hashes.json", {name: r.digest(content) for name, content in sorted(sources.items())})
    for component, content in dependency_freezes.items():
        (directory / f"{component}-freeze.txt").write_text(content)


def prepare(method_commit, study_dir):
    """Offline only. Refuse to replace an existing frozen campaign."""
    study_dir = Path(study_dir).resolve()
    target = study_dir / "frozen"
    if target.exists():
        raise ValueError("Study already frozen; use it or choose a new --study-dir")
    commits = {"m0": git_commit(M0_COMMIT), "m1": git_commit(method_commit)}
    if commits["m1"] == commits["m0"]:
        raise ValueError("M1 must be an explicit different method commit")
    manifest_bytes = (DATA / "fast100.json").read_bytes()
    if r.digest(manifest_bytes) != FAST_SHA256:
        raise ValueError("Frozen fast100 manifest changed")
    manifest = json.loads(manifest_bytes)
    plan = base_plan(manifest)
    # Check the frozen subset against the original benchmark, not a hand-built
    # scene list or a changed local task/check definition.
    canonical = {scene["id"]: (name, scene) for name, scene in r.canonical_scenes()}
    expected = r.make_plan(STUDY, [canonical[sid] for sid in SCENES])
    for scene in expected["sessions"]:
        scene["source_file"] = scene["file"]
        scene["file"] = "scenes/" + scene["file"]
    if expected != plan:
        raise ValueError("Subset differs from original benchmark tasks/checks/protocol")
    settings, key = settings_for_run(ROOT)
    if not key:
        raise ValueError("Official provider key is missing")
    freezes, checks = {}, {}
    for component in ("server", "controller", "agent4drone"):
        python = str(ROOT / component / ".venv/bin/python")
        checked = subprocess.run(["uv", "--no-cache", "pip", "check", "--python", python],
                                 capture_output=True, text=True, timeout=60)
        checks[component] = checked.returncode == 0
        if checked.returncode:
            raise ValueError("Dependency check failed")
        freezes[component] = subprocess.check_output(
            ["uv", "--no-cache", "pip", "freeze", "--python", python], text=True, timeout=60)
    common = common_sources()
    methods = {arm: agent_sources(commit) for arm, commit in commits.items()}
    # Offline structural check: the declared M1 mode must have an implementation.
    if MODE_VARIABLE.encode() not in methods["m1"]["agent4drone/uav_agent.py"]:
        raise ValueError("M1 commit does not implement the declared task-progress mode")
    study_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=study_dir))
    try:
        (temporary / "fast100.json").write_bytes(manifest_bytes)
        for selected in plan["sessions"]:
            content = (DATA / selected["file"]).read_bytes()
            if r.digest(content) != selected["sha256"]:
                raise ValueError("Selected original scene bytes changed")
            path = temporary / "data" / selected["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for source in sorted((ROOT / "server/config/example").glob("*.json")):
            path = temporary / "assets" / source.relative_to(ROOT)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        arms = {}
        for arm, sources in methods.items():
            bundle = temporary / arm
            write_bundle(bundle, {**common, **sources}, freezes)
            arms[arm] = {"agent_commit": commits[arm], "runtime_env": {MODE_VARIABLE: MODES[arm]},
                         "agent_python_sha256": {name: r.digest(data) for name, data in sorted(sources.items())}}
        file_hashes = {str(p.relative_to(temporary)): r.digest(p.read_bytes())
                       for p in sorted(temporary.rglob("*")) if p.is_file()}
        study = {"schema": 1, "id": STUDY, "created_at": rp.timestamp(), "workspace": str(ROOT),
                 "runs_root": str(r.RUNS), "workers": WORKERS, "arm_order": ["m0", "m1"],
                 "plan": plan, "configuration": r.public_settings(settings), "methods": arms,
                 "common_source_sha256": {name: r.digest(data) for name, data in sorted(common.items())},
                 "frozen_files": file_hashes, "offline_ready": all(checks.values()),
                 "model_calls_during_prepare": 0,
                 "failure_policy": "First attempts only; stop dispatch on service, worker, or infrastructure failure; no replay.",
                 "limitations": ["Same API model alias does not fix remote weights or sampling.",
                                 "Five scenes, with within-scene world and blackboard carry-over, are not 100 independent samples."]}
        r.save_json(temporary / "study.json", study)
        temporary.rename(target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps({"study_directory": str(study_dir), "offline_ready": True,
                      "tasks_per_arm": 100, "checks_per_arm": 605, "methods": commits}, ensure_ascii=False), flush=True)
    return study


def load_study(study_dir):
    frozen = Path(study_dir).resolve() / "frozen"
    study = r.read_json(frozen / "study.json")
    actual = {str(p.relative_to(frozen)): r.digest(p.read_bytes()) for p in frozen.rglob("*")
              if p.is_file() and p != frozen / "study.json"}
    if actual != study["frozen_files"] or r.digest((frozen / "fast100.json").read_bytes()) != FAST_SHA256:
        raise ValueError("Frozen study files changed")
    if (study["id"] != STUDY or study["workers"] != WORKERS or not study["offline_ready"]
            or study["plan"] != base_plan(r.read_json(frozen / "fast100.json"))
            or study["methods"]["m0"]["agent_commit"] != M0_COMMIT):
        raise ValueError("Frozen study identity differs")
    for arm in MODES:
        if study["methods"][arm]["runtime_env"] != {MODE_VARIABLE: MODES[arm]}:
            raise ValueError("Frozen method mode differs")
        hashes = r.read_json(frozen / arm / "source_hashes.json")
        agent = {name: value for name, value in hashes.items() if name.startswith("agent4drone/") and name.endswith(".py")}
        common = {name: value for name, value in hashes.items() if name not in agent}
        if agent != study["methods"][arm]["agent_python_sha256"] or common != study["common_source_sha256"]:
            raise ValueError("Method source identity differs")
    return study


def materialize_runtime(attempt, frozen, arm, workspace):
    """Copy a prepared archive; never snapshot a live Agent implementation."""
    attempt = Path(attempt)
    for source in (frozen / arm).iterdir():
        shutil.copyfile(source, attempt / source.name)
    runtime = attempt / "runtime"
    runtime.mkdir()
    with zipfile.ZipFile(attempt / "source.zip") as archive:
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in archive.namelist()):
            raise ValueError("Unsafe source archive path")
        archive.extractall(runtime)
    shutil.copytree(frozen / "data", runtime / "data")
    if (frozen / "assets").exists():
        shutil.copytree(frozen / "assets", runtime, dirs_exist_ok=True)
    for component in ("server", "controller", "agent4drone"):
        (runtime / component).mkdir(exist_ok=True)
        (runtime / component / ".venv").symlink_to(Path(workspace) / component / ".venv", target_is_directory=True)
    hashes = r.read_json(attempt / "source_hashes.json")
    if any(r.digest((runtime / name).read_bytes()) != value for name, value in hashes.items()):
        raise ValueError("Runtime source differs from prepared archive")
    return runtime


@contextmanager
def method_environment(mode):
    if mode not in MODES.values():
        raise ValueError("Unexpected method mode")
    previous = os.environ.get(MODE_VARIABLE)
    os.environ[MODE_VARIABLE] = mode
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(MODE_VARIABLE, None)
        else:
            os.environ[MODE_VARIABLE] = previous


def run(arm, study_dir, method_commit):
    """Create one attempt then replace this process with the archived launcher."""
    study_dir = Path(study_dir).resolve()
    study = load_study(study_dir)
    if git_commit(method_commit) != study["methods"]["m1"]["agent_commit"]:
        raise ValueError("Requested M1 commit differs from the prepared study")
    frozen = study_dir / "frozen"
    settings, key = settings_for_run(study["workspace"])
    if not key or r.public_settings(settings) != study["configuration"]:
        raise ValueError("Provider configuration differs from offline preparation")
    current_common = {name: r.digest(data) for name, data in common_sources().items()}
    if current_common != study["common_source_sha256"]:
        raise ValueError("Common launcher/server/checker source changed after preparation")
    # Atomic registration under the existing global lock prevents duplicate arms.
    # _execute takes the same lock again before any service/model launch.
    with rp.runner_lock(r):
        records_path = study_dir / "runs.json"
        records = r.read_json(records_path) if records_path.exists() else {}
        if arm in records:
            raise ValueError("This arm already has a first attempt; no automatic replay")
        if arm == "m1":
            previous = records.get("m0")
            if not previous or previous.get("status") != "completed_and_verified":
                raise ValueError("Complete and verify M0 before launching M1")
        attempt = r.RUNS / (time.strftime("%Y%m%d-%H%M%S") + f"-m1-fast100-{arm}-" + uuid.uuid4().hex[:8])
        attempt.mkdir(mode=0o700)
        runtime = materialize_runtime(attempt, frozen, arm, study["workspace"])
        metadata = {"study_id": STUDY, "arm": arm, **study["methods"][arm],
                    "study_sha256": r.digest((frozen / "study.json").read_bytes()),
                    "source_zip_sha256": r.digest((attempt / "source.zip").read_bytes()),
                    "fast100_sha256": FAST_SHA256, "workers": WORKERS,
                    "runtime_assets_sha256": {name.removeprefix("assets/"): value
                                               for name, value in study["frozen_files"].items()
                                               if name.startswith("assets/")}}
        r.save_json(attempt / "method-freeze.json", metadata)
        plan = copy.deepcopy(study["plan"])
        plan["study"] = {"id": STUDY, "arm": arm, "method_freeze_sha256": r.digest((attempt / "method-freeze.json").read_bytes()),
                         "study_directory": str(study_dir), "workspace": study["workspace"],
                         "runs_root": str(r.RUNS), "runtime_env": metadata["runtime_env"]}
        r.save_json(attempt / "plan.json", plan)
        r.save_json(attempt / "configuration.json", r.public_settings(settings))
        r.save_json(attempt / "agent-settings.json", settings)
        shutil.copyfile(frozen / "fast100.json", attempt / "fast100.json")
        r.save_json(attempt / "gateway_preflight.json", {"offline_ready": True, "gateway_verified": False,
                    "model_calls": 0, "configuration": study["configuration"], "freeze": metadata})
        fingerprint = rp.identity(r, attempt, WORKERS)
        r.save_json(attempt / "identity.json", fingerprint)
        r.save_json(attempt / "manifest.json", {"status": "created", "started_at": rp.timestamp(),
                    "study": STUDY, "arm": arm, "agent_commit": metadata["agent_commit"],
                    "metadata": {"freeze": metadata}, "protocol": plan["protocol"], "plan": STUDY,
                    "benchmark_sha256": r.ZIP_SHA, "identity_sha256": rp.json_digest(r, fingerprint),
                    "source": "prepared source.zip and runtime; never active workspace agent",
                    "executor": {"id": rp.EXECUTOR_ID, "workers": WORKERS, "automatic_retries": 0,
                                 "recovery": "disabled", "provider_error_policy": rp.PROVIDER_ERROR_POLICY},
                    "scenes": {s["id"]: {"status": "pending", "generation": 0, "directory": None, "history": []}
                               for s in plan["sessions"]}})
        records[arm] = {"attempt": str(attempt), "status": "prepared", "created_at": rp.timestamp()}
        r.save_json(records_path, records)
    print(json.dumps({"attempt": str(attempt), "arm": arm, "action": "starting_frozen_runtime"}), flush=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
           "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
           MODE_VARIABLE: MODES[arm]}
    os.execve(str(runtime / "controller/.venv/bin/python"),
              [str(runtime / "controller/.venv/bin/python"), "-B", str(runtime / "scripts/m1_progress_pilot.py"),
               "_execute", str(attempt)], env)


def seal(attempt):
    r.save_json(attempt / "suite-seal.json", {"files": {
        str(p.relative_to(attempt)): r.digest(p.read_bytes()) for p in sorted(attempt.rglob("*"))
        if p.is_file() and p.name != "suite-seal.json"}})


def assert_method_identity(attempt):
    plan = r.read_json(attempt / "plan.json")
    metadata = r.read_json(attempt / "method-freeze.json")
    if (r.digest((attempt / "method-freeze.json").read_bytes()) != plan["study"]["method_freeze_sha256"]
            or metadata != r.read_json(attempt / "manifest.json")["metadata"]["freeze"]
            or metadata["runtime_env"] != {MODE_VARIABLE: MODES[metadata["arm"]]}
            or metadata["runtime_env"] != plan["study"]["runtime_env"]
            or r.digest((attempt / "source.zip").read_bytes()) != metadata["source_zip_sha256"]
            or r.digest((attempt / "fast100.json").read_bytes()) != FAST_SHA256):
        raise ValueError("Method freeze/configuration changed")
    core = {k: v for k, v in plan.items() if k != "study"}
    if core != base_plan(r.read_json(attempt / "fast100.json")):
        raise ValueError("Attempt data or protocol changed")
    hashes = r.read_json(attempt / "source_hashes.json")
    actual_agent = {k: v for k, v in hashes.items() if k.startswith("agent4drone/") and k.endswith(".py")}
    if actual_agent != metadata["agent_python_sha256"]:
        raise ValueError("Method source hash mismatch")
    for name, checksum in metadata["runtime_assets_sha256"].items():
        if r.digest((attempt / "runtime" / name).read_bytes()) != checksum:
            raise ValueError("Frozen runtime asset changed")
    for selected in plan["sessions"]:
        if r.digest((attempt / "runtime/data" / selected["file"]).read_bytes()) != selected["sha256"]:
            raise ValueError("Frozen runtime scene changed")
    if metadata["arm"] == "m0" and metadata["agent_commit"] != M0_COMMIT:
        raise ValueError("M0 must remain the pinned remedies method")
    return plan, metadata


def verify_saved(attempt):
    attempt = Path(attempt).resolve()
    expected = r.read_json(attempt / "suite-seal.json")["files"]
    actual = {str(p.relative_to(attempt)): r.digest(p.read_bytes()) for p in attempt.rglob("*")
              if p.is_file() and p.name != "suite-seal.json"}
    if expected != actual:
        raise ValueError("Sealed attempt files changed")
    plan, _ = assert_method_identity(attempt)
    if r.read_json(attempt / "execution-environment.json")["runtime_env"] != plan["study"]["runtime_env"]:
        raise ValueError("Observed execution mode differs from frozen method")
    rp.verify_archive(r, attempt)
    manifest = r.read_json(attempt / "manifest.json")
    previous_root, previous_data = r.ROOT, r.DATA
    try:
        r.ROOT, r.DATA = attempt / "runtime", attempt / "runtime/data"
        checker = sys.modules.get("check_ui.agent_checker")
        if checker and not Path(checker.__file__).resolve().is_relative_to(r.ROOT):
            raise ValueError("Verify in a fresh process with the archived checker")
        for selected in plan["sessions"]:
            record = manifest["scenes"][selected["id"]]
            if record["generation"] != 1:
                raise ValueError("Only first attempts are allowed")
            rp.verify_scene(r, attempt, plan, selected, record)
    finally:
        r.ROOT, r.DATA = previous_root, previous_data
    result = r.summarize(plan, r.read_json(attempt / "results.json"), manifest["status"])
    if manifest["status"] != "completed" or not result["valid_complete_run"]:
        raise ValueError("Incomplete run; no final success rate")
    return result


def execute(attempt):
    """Internal entry: imported only from this attempt's archived runtime."""
    attempt = Path(attempt).resolve()
    if ROOT.resolve() != attempt / "runtime":
        raise ValueError("Execution must enter the archived runtime")
    plan, metadata = assert_method_identity(attempt)
    r.RUNS = Path(plan["study"]["runs_root"])
    if attempt.parent != r.RUNS.resolve():
        raise ValueError("Attempt is outside the frozen runs root")
    r.DATA = attempt / "runtime/data"
    r.load_plan = lambda name: copy.deepcopy(plan) if name == STUDY else (_ for _ in ()).throw(ValueError("Unknown plan"))
    settings, key = settings_for_run(plan["study"]["workspace"])
    if not key or r.public_settings(settings) != r.read_json(attempt / "configuration.json"):
        raise ValueError("Official provider configuration changed")
    records_path = Path(plan["study"]["study_directory"]) / "runs.json"
    original_run_scene = rp.run_scene

    def stop_on_failure(*args, **kwargs):
        try:
            result = original_run_scene(*args, **kwargs)
            if result["status"] != "completed":
                args[6].set()
            return result
        except BaseException:
            args[6].set()
            raise

    with rp.runner_lock(r), rp.attempt_lock(attempt):
        records = r.read_json(records_path)
        record = records[metadata["arm"]]
        if record["attempt"] != str(attempt) or record["status"] != "prepared":
            raise ValueError("Arm is not a new registered first attempt")
        rp.verify_provenance(r, attempt, plan, settings)
        record.update(status="running", pid=os.getpid(), started_at=rp.timestamp())
        r.save_json(records_path, records)
        previous_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
        ok = False
        failure = None
        try:
            rp.run_scene = stop_on_failure
            with method_environment(metadata["runtime_env"][MODE_VARIABLE]):
                r.save_json(attempt / "execution-environment.json", {
                    "runtime_env": {MODE_VARIABLE: os.environ[MODE_VARIABLE]},
                    "pid": os.getpid(), "runtime_root": str(ROOT), "recorded_at": rp.timestamp()})
                ok = rp.coordinate(r, attempt, plan, settings, key, r.read_json(attempt / "manifest.json"), {})
                if ok:
                    rp.verify_provenance(r, attempt, plan, settings)
        except BaseException as error:
            ok = False
            failure = type(error).__name__
            raise
        finally:
            rp.run_scene = original_run_scene
            signal.signal(signal.SIGTERM, previous_handler)
            # State/exit provenance outside suite evidence is still written if
            # ordinary exceptions interrupt collection. SIGKILL needs the outer
            # durable launcher/observer; it cannot be solved in a finally block.
            record.update(status="awaiting_verification" if ok else "interrupted", finished_at=rp.timestamp(),
                          exception_type=failure)
            r.save_json(records_path, records)
            seal(attempt)
        if ok:
            verify_saved(attempt)
            record["status"] = "completed_and_verified"
            r.save_json(records_path, records)
        print(json.dumps({"attempt": str(attempt), "arm": metadata["arm"], "complete_and_verified": ok}), flush=True)
        return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare", help="Offline freeze; never calls the model")
    prepare_parser.add_argument("--method-commit", required=True, help="Explicit M1 Git commit")
    prepare_parser.add_argument("--study-dir", type=Path, default=SPEC)
    run_parser = sub.add_parser("run", help="ONLINE: execute one new paid model arm")
    run_parser.add_argument("--arm", choices=tuple(MODES), required=True)
    run_parser.add_argument("--method-commit", required=True, help="Must match the explicitly prepared M1 commit")
    run_parser.add_argument("--study-dir", type=Path, default=SPEC)
    for name in ("verify", "_execute", "_verify"):
        sub.add_parser(name).add_argument("attempt", type=Path)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    try:
        if args.command == "prepare":
            prepare(args.method_commit, args.study_dir)
            return 0
        if args.command == "run":
            run(args.arm, args.study_dir, args.method_commit)
            return 0
        if args.command == "_execute":
            return 0 if execute(args.attempt) else 2
        if args.command == "verify":
            attempt = args.attempt.resolve()
            runtime = attempt / "runtime"
            # A separate interpreter guarantees checker and runner imports come
            # from the same frozen archive as the recorded experiment.
            os.execve(str(runtime / "controller/.venv/bin/python"),
                      [str(runtime / "controller/.venv/bin/python"), "-B", str(runtime / "scripts/m1_progress_pilot.py"),
                       "_verify", str(attempt)], {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        print(json.dumps(verify_saved(args.attempt), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # Exceptions/provider responses/configuration may contain credentials.
        print(f"Study stopped: {type(error).__name__}; inspect local evidence", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
