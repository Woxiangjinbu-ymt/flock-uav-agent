#!/usr/bin/env python3
"""First-attempt, paired R2 versus R2+observation-worker prefix pilot.

Two full original scenes are loaded. Their task lists end at the two shadow
trigger candidates, preserving within-scene carryover. The upstream 500-second
protocol, checks, provider configuration, and runner are shared by both arms.
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
import sys
import tempfile
import time
import uuid

import m1_progress_pilot as base
import m1_revision2_pilot as r2

r, rp = base.r, base.rp
ROOT = r.ROOT
STUDY = "m1r2-observation-worker-prefix-v2"
SPEC = ROOT / "experiments/m1r2-observation-worker-prefix-v2"
R2_COMMIT = "69dd34c067ec97b746db5180684594c546a35172"
ARMS = {"control": "evidence_r2", "worker": "evidence_r2_worker"}
PREFIX = {"ad34350a": (7, "529c0622"), "35ce64d8": (5, "22e1e1c5")}
WORKERS = 2


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("R2 source patch anchor changed")
    return source.replace(old, new, 1)


def worker_agent_source(original):
    """Apply only the opt-in worker wiring to the committed R2 agent."""
    source = original.decode()
    source = replace_once(source, "import os\nimport uuid", "import os\nimport time\nimport uuid")
    source = replace_once(
        source,
        '"evidence_r2", "evidence_r2_hints", "evidence_r2_shadow"}:\n            raise ValueError',
        '"evidence_r2", "evidence_r2_hints", "evidence_r2_shadow", "evidence_r2_worker"}:\n            raise ValueError')
    source = replace_once(
        source,
        '            previous = self._model_wire_requests[-1] if self._model_wire_requests else None\n',
        '            role = "observation_worker" if getattr(self, "_in_observation_worker", False) else "main"\n'
        '            record["agent_role"] = role\n'
        '            previous = next((item for item in reversed(self._model_wire_requests)\n'
        '                             if item.get("agent_role", "main") == role), None)\n')
    anchor = '            if mode in {"evidence_r2", "evidence_r2_hints", "evidence_r2_shadow"}:\n'
    branch = '''            if mode == "evidence_r2_worker":
                from task_evidence_r2 import SessionEvidenceIndexR2
                from observation_worker_r2 import (TaskEvidenceWorkerLedger,
                    TaskEvidenceWorkerMiddleware, run_observation_worker)
                index = getattr(self.blackboard, "task_evidence_index", None)
                if index is None:
                    index = SessionEvidenceIndexR2(self.blackboard.session_id)
                    self.blackboard.task_evidence_index = index
                if not isinstance(index, SessionEvidenceIndexR2):
                    raise ValueError("Changing evidence revision requires a fresh agent/blackboard")
                ledger = TaskEvidenceWorkerLedger(command, command_id, index=index,
                    aggregate_feedback=True, inherited_blackboard=inherited,
                    max_summary_chars=3900)
                self._task_progress_middleware = TaskEvidenceWorkerMiddleware(
                    ledger, lambda candidate, run: run_observation_worker(self, ledger, candidate, run),
                    self._observation_worker_task_started)
                self._task_progress = ledger
                return
'''
    source = replace_once(source, anchor, branch + anchor)
    anchor = '    ) -> Dict[str, Any]:\n        if not getattr(self, "toolchain_json_recording", False):\n'
    new = '''    ) -> Dict[str, Any]:
        worker_usage = getattr(self, "_observation_worker_token_usage", None)
        if worker_usage and worker_usage.get("llm_calls"):
            result = {**result, "token_usage": dict(result.get("token_usage") or self._empty_token_usage())}
            merge_token_usage(result["token_usage"], worker_usage)
        if not getattr(self, "toolchain_json_recording", False):
'''
    source = replace_once(source, anchor, new)
    anchor = '        self._model_wire_errors = [e for e in getattr(self, "_model_wire_errors", []) if e.startswith("setup:")]\n'
    source = replace_once(source, anchor, anchor +
        '        self._in_observation_worker = False\n'
        '        self._observation_worker_token_usage = empty_token_usage()\n'
        '        self._observation_worker_task_started = time.monotonic()\n')
    return source.encode()


def pilot_plan():
    manifest = r.read_json(ROOT / "experiments/m1-revision2-v1/frozen/fast100.json")
    plan = r2.base_plan(manifest)
    plan["name"] = STUDY
    plan["sessions"] = [copy.deepcopy(scene) for scene in plan["sessions"] if scene["id"] in PREFIX]
    for scene in plan["sessions"]:
        count, target = PREFIX[scene["id"]]
        scene["tasks"] = scene["tasks"][:count]
        if scene["tasks"][-1]["id"] != target:
            raise ValueError("Frozen candidate prefix changed")
    plan["expected_tasks"] = sum(len(scene["tasks"]) for scene in plan["sessions"])
    plan["expected_checks"] = sum(len(task["leaves"]) for scene in plan["sessions"] for task in scene["tasks"])
    if plan["expected_tasks"] != 12 or plan["protocol"]["timeout_seconds"] != 500:
        raise ValueError("Pilot protocol changed")
    return plan


def prepare():
    if (SPEC / "frozen").exists():
        raise ValueError("Study already frozen")
    if base.git_commit(R2_COMMIT) != R2_COMMIT:
        raise ValueError("Pinned R2 commit unavailable")
    control = base.agent_sources(R2_COMMIT)
    worker = dict(control)
    worker["agent4drone/uav_agent.py"] = worker_agent_source(control["agent4drone/uav_agent.py"])
    worker["agent4drone/observation_worker_r2.py"] = (ROOT / "agent4drone/observation_worker_r2.py").read_bytes()
    common = base.common_sources()
    settings, key = base.settings_for_run(ROOT)
    if not key:
        raise ValueError("Official provider key is missing")
    freezes = r2.dependency_freezes(ROOT)
    plan = pilot_plan()
    source_data = ROOT / "experiments/m1-revision2-v1/frozen/data"
    SPEC.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=SPEC))
    try:
        shutil.copyfile(ROOT / "experiments/m1-revision2-v1/frozen/fast100.json", temporary / "fast100.json")
        for scene in plan["sessions"]:
            origin = source_data / scene["file"]
            if r.digest(origin.read_bytes()) != scene["sha256"]:
                raise ValueError("Frozen original scene changed")
            target = temporary / "data" / scene["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        shutil.copytree(ROOT / "experiments/m1-revision2-v1/frozen/assets", temporary / "assets")
        for arm, sources in (("control", control), ("worker", worker)):
            base.write_bundle(temporary / arm, {**common, **sources}, freezes)
        metadata = {"schema": 1, "id": STUDY, "plan": plan,
            "arms": ARMS, "arm_order": list(ARMS), "workers": WORKERS,
            "workspace": str(ROOT), "runs_root": str(r.RUNS),
            "configuration": r.public_settings(settings),
            "r2_commit": R2_COMMIT,
            "source_hashes": {arm: r.read_json(temporary / arm / "source_hashes.json") for arm in ARMS},
            "protocol_note": "Original full scenes; first 7 and 5 tasks in original order; 500s cutoff; no retries; fresh arm per scene.",
            "selection_note": "Candidate endpoints chosen on the historical R2 development trace; exploratory pilot, not an unbiased generalization test.",
            "created_at": rp.timestamp()}
        metadata["frozen_files"] = {str(path.relative_to(temporary)): r.digest(path.read_bytes())
                                    for path in temporary.rglob("*") if path.is_file()}
        r.save_json(temporary / "study.json", metadata)
        temporary.rename(SPEC / "frozen")
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps({"study": str(SPEC), "tasks_per_arm": 12,
                      "checks_per_arm": plan["expected_checks"], "model_calls": 0}))


def load_study():
    frozen = SPEC / "frozen"
    study = r.read_json(frozen / "study.json")
    actual = {str(path.relative_to(frozen)): r.digest(path.read_bytes())
              for path in frozen.rglob("*") if path.is_file() and path.name != "study.json"}
    if (study["id"] != STUDY or study["arms"] != ARMS or study["plan"] != pilot_plan()
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
                "agent4drone/tests/test_task_progress_integration.py"]
            completed = r.subprocess.run(command, cwd=runtime, capture_output=True, text=True, timeout=90,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            (SPEC / f"preflight-{arm}.log").write_text(completed.stdout + completed.stderr)
            if completed.returncode:
                raise ValueError(f"Frozen {arm} offline tests failed")
            if arm == "worker":
                r.subprocess.run([str(runtime / "agent4drone/.venv/bin/python"), "-B", "-c",
                    "import sys; sys.path.insert(0, 'agent4drone'); import observation_worker_r2, uav_agent"],
                    cwd=runtime, check=True, capture_output=True, text=True, timeout=30)
    record = {"ready": True, "model_calls": 0,
              "study_sha256": r.digest((SPEC / "frozen/study.json").read_bytes()),
              "created_at": rp.timestamp()}
    r.save_json(SPEC / "preflight.json", record)
    print(json.dumps(record))


def register_arm(arm):
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
        if arm in records or (arm == "worker" and records.get("control", {}).get("status") != "completed_and_verified"):
            raise ValueError("First control must be complete; each arm runs once")
        attempt = r.RUNS / (time.strftime("%Y%m%d-%H%M%S") + f"-worker-prefix-{arm}-" + uuid.uuid4().hex[:8])
        attempt.mkdir(mode=0o700)
        runtime = base.materialize_runtime(attempt, SPEC / "frozen", arm, ROOT)
        plan = copy.deepcopy(study["plan"])
        plan["study"] = {"id": STUDY, "arm": arm, "runs_root": str(r.RUNS),
                         "study_directory": str(SPEC), "workspace": str(ROOT),
                         "runtime_env": {"UAV_TASK_PROGRESS_MODE": ARMS[arm], "UAV_MODEL_REQUEST_AUDIT": "1"}}
        r.save_json(attempt / "plan.json", plan)
        r.save_json(attempt / "configuration.json", r.public_settings(settings))
        r.save_json(attempt / "agent-settings.json", settings)
        r.save_json(attempt / "gateway_preflight.json", {"offline_ready": True, "configuration": study["configuration"]})
        identity = rp.identity(r, attempt, WORKERS)
        r.save_json(attempt / "identity.json", identity)
        r.save_json(attempt / "manifest.json", {"status": "created", "started_at": rp.timestamp(),
            "study": STUDY, "arm": arm, "plan": STUDY, "benchmark_sha256": r.ZIP_SHA,
            "protocol": plan["protocol"], "identity_sha256": rp.json_digest(r, identity),
            "executor": {"id": rp.EXECUTOR_ID, "workers": WORKERS, "automatic_retries": 0,
                         "recovery": "disabled", "provider_error_policy": rp.PROVIDER_ERROR_POLICY},
            "scenes": {scene["id"]: {"status": "pending", "generation": 0, "directory": None, "history": []}
                       for scene in plan["sessions"]}})
        records[arm] = {"attempt": str(attempt), "status": "prepared", "created_at": rp.timestamp()}
        r.save_json(registry, records)
    return attempt, runtime


def launch_arm(arm):
    attempt, runtime = register_arm(arm)
    print(json.dumps({"arm": arm, "attempt": str(attempt), "action": "starting"}), flush=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
           "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
           "UAV_TASK_PROGRESS_MODE": ARMS[arm], "UAV_MODEL_REQUEST_AUDIT": "1"}
    os.execve(str(runtime / "controller/.venv/bin/python"),
              [str(runtime / "controller/.venv/bin/python"), "-B",
               str(runtime / "scripts/observation_worker_pilot.py"), "_execute", str(attempt)], env)


def execute(attempt):
    attempt = Path(attempt).resolve()
    if ROOT.resolve() != attempt / "runtime":
        raise ValueError("Must execute from frozen runtime")
    plan = r.read_json(attempt / "plan.json")
    arm = plan["study"]["arm"]
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
        if records[arm]["status"] != "prepared":
            raise ValueError("Arm is not a fresh first attempt")
        rp.verify_provenance(r, attempt, plan, settings)
        records[arm].update(status="running", started_at=rp.timestamp(), pid=os.getpid())
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
            records[arm].update(status="awaiting_verification" if ok else "interrupted",
                                finished_at=rp.timestamp(), exception_type=failure)
            r.save_json(records_path, records)
            base.seal(attempt)
        if ok:
            verify(attempt)
            records[arm]["status"] = "completed_and_verified"
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
    sub.add_parser("_execute").add_argument("attempt", type=Path)
    sub.add_parser("verify").add_argument("attempt", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "preflight":
        preflight()
    elif args.command == "run":
        launch_arm(args.arm)
    elif args.command == "_execute":
        return 0 if execute(args.attempt) else 2
    elif args.command == "verify":
        print(json.dumps(verify(args.attempt), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
