#!/usr/bin/env python3
"""Launch the already frozen five-arm study; never modify agent or scoring code.

This external adapter is separately sealed. It uses frozen reproduction modules,
preserves one first attempt per arm, and stops the campaign on any failed arm.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
import zipfile

STUDY_REL = Path("experiments/minimal-harness-v1")
EXTRA_SOURCE = "experiments/analysis/validate_minimal_harness.py"
SELF_REL = "scripts/minimal_harness_online.py"
ANALYSIS_REL = "experiments/analysis/summarize_minimal_harness_online.py"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def frozen(workspace):
    spec = workspace / STUDY_REL
    root = spec / "frozen"
    study = read(root / "study.json")
    pre = read(spec / "preflight.json")
    if not pre["ready"] or pre["study_sha256"] != digest(root / "study.json"):
        raise ValueError("Missing or stale frozen preflight")
    actual = {str(p.relative_to(root)): digest(p) for p in root.rglob("*")
              if p.is_file() and p != root / "study.json"}
    if actual != study["frozen_files"]:
        raise ValueError("Frozen study changed")
    if (study["plan"]["expected_tasks"], study["plan"]["expected_checks"], study["workers"],
            study["plan"]["protocol"]["timeout_seconds"]) != (100, 605, 2, 500):
        raise ValueError("Unexpected frozen protocol")
    with zipfile.ZipFile(root / "shared/source.zip") as archive:
        actual_sources = {n: hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist()}
    if actual_sources != read(root / "shared/source_hashes.json"):
        raise ValueError("Source content differs from its manifest")
    return spec, study


def load_modules(runtime):
    sys.path.insert(0, str(runtime / "scripts"))
    import m1_progress_pilot as base
    if Path(base.r.__file__).resolve().parents[1] != runtime.resolve():
        raise ValueError("Runner was imported from a different runtime")
    return base, base.r, base.rp


def clean_environment(study, arm):
    env = os.environ.copy()
    for key in {k for values in study["arms"].values() for k in values}:
        env.pop(key, None)
    env.update(study["arms"][arm])
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", MPLBACKEND="Agg",
               LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
    return env


def next_arm(study, registry):
    for arm in study["first_exploratory_arm_order"]:
        if arm not in registry:
            return arm
        if registry[arm]["status"] != "completed_and_verified":
            raise ValueError("Existing first attempt is unfinished or failed; no retry")
    return None


def planned_attempt(workspace, spec, arm, launch_hash):
    plan = copy.deepcopy(read(spec / "frozen/plans" / f"{arm}.json"))
    plan["study"].update(workspace=str(workspace), study_directory=str(spec),
                         runs_root=str(workspace / "experiments/runs"),
                         launch_adapter_sha256=launch_hash)
    return plan


def initialize_attempt(base, r, rp, workspace, spec, arm, attempt, launch_hash, study):
    runtime = base.materialize_runtime(attempt, spec / "frozen", "shared", workspace)
    plan = planned_attempt(workspace, spec, arm, launch_hash)
    settings, key = base.settings_for_run(workspace)
    if not key or r.public_settings(settings) != study["configuration"]:
        raise ValueError("Provider configuration changed or credential missing")
    save(attempt / "plan.json", plan)
    save(attempt / "configuration.json", r.public_settings(settings))
    save(attempt / "agent-settings.json", settings)  # API key remains empty
    save(attempt / "gateway_preflight.json", {"offline_ready": True, "configuration": study["configuration"],
                                             "online_probe": False})
    identity = rp.identity(r, attempt, study["workers"])
    save(attempt / "identity.json", identity)
    save(attempt / "manifest.json", {"status": "created", "started_at": now(), "study": study["id"],
         "arm": arm, "plan": study["id"], "benchmark_sha256": plan["benchmark_sha256"],
         "protocol": plan["protocol"], "identity_sha256": rp.json_digest(r, identity),
         "executor": {"id": rp.EXECUTOR_ID, "workers": study["workers"], "automatic_retries": 0,
                      "recovery": "disabled", "provider_error_policy": rp.PROVIDER_ERROR_POLICY},
         "scenes": {s["id"]: {"status": "pending", "generation": 0, "directory": None, "history": []}
                    for s in plan["sessions"]}})
    return runtime, plan, settings


def configure_runner(r, workspace, runtime, plan):
    r.ROOT, r.DATA, r.RUNS = runtime, runtime / "data", workspace / "experiments/runs"
    r.load_plan = lambda name: copy.deepcopy(plan) if name == plan["name"] else (_ for _ in ()).throw(ValueError("Unknown plan"))
    original = r.current_source_hashes
    # The frozen manifest includes one offline audit script outside the runner's
    # four source roots. Add that exact path to inventory; do not omit any source
    # comparison or alter the frozen archive to make provenance pass.
    def inventory():
        return {**original(), EXTRA_SOURCE: digest(runtime / EXTRA_SOURCE)}
    r.current_source_hashes = inventory
    return original


def prepare(workspace):
    spec, study = frozen(workspace)
    execution = spec / "execution"
    if execution.exists():
        raise ValueError("Execution adapter already sealed; verify or start it")
    hashes = read(spec / "frozen/shared/source_hashes.json")
    for name in ("scripts/m1_progress_pilot.py", "scripts/reproduction.py", "scripts/reproduction_parallel.py"):
        if digest(workspace / name) != hashes[name]:
            raise ValueError("Bootstrap helper differs from freeze")
    base, r, rp = load_modules(workspace)
    execution.mkdir(mode=0o700)
    try:
        for name in (SELF_REL, ANALYSIS_REL):
            target = execution / Path(name).name
            shutil.copyfile(workspace / name, target)
        bootstrap = execution / "bootstrap"
        bootstrap.mkdir()
        base.materialize_runtime(bootstrap, spec / "frozen", "shared", workspace)
        metadata = {"schema": 1, "created_at": now(), "study_sha256": digest(spec / "frozen/study.json"),
                    "files": {Path(n).name: digest(execution / Path(n).name) for n in (SELF_REL, ANALYSIS_REL)},
                    "scope": "External registration, sequential arm dispatch, provenance inventory, aggregation. Frozen agent, skills, prompts, checker and runner source unchanged.",
                    "inventory_addition": EXTRA_SOURCE}
        save(execution / "adapter.json", metadata)
    except BaseException:
        shutil.rmtree(execution)
        raise
    print(json.dumps({"adapter_prepared": True, "model_calls": 0}))


def verify_adapter(spec):
    execution = spec / "execution"
    metadata = read(execution / "adapter.json")
    if metadata["study_sha256"] != digest(spec / "frozen/study.json"):
        raise ValueError("Adapter belongs to another freeze")
    if any(digest(execution / name) != value for name, value in metadata["files"].items()):
        raise ValueError("Execution adapter changed")
    return digest(execution / "adapter.json")


def preflight(workspace):
    spec, study = frozen(workspace)
    launch_hash = verify_adapter(spec)
    base, r, rp = load_modules(spec / "execution/bootstrap/runtime")
    checks = []
    for arm in study["first_exploratory_arm_order"]:
        with tempfile.TemporaryDirectory(prefix=".launch-check-", dir=spec) as directory:
            attempt = Path(directory)
            runtime, plan, settings = initialize_attempt(base, r, rp, workspace, spec, arm, attempt, launch_hash, study)
            original = configure_runner(r, workspace, runtime, plan)
            try:
                rp.verify_provenance(r, attempt, plan, settings)
                if read(attempt / "agent-settings.json")["provider_configs"]["deepseek_official"]["api_key"]:
                    raise ValueError("Credential written to attempt")
                for selected in plan["sessions"]:
                    if digest(runtime / "data" / selected["file"]) != selected["sha256"]:
                        raise ValueError("Scene data changed")
                checks.append({"arm": arm, "provenance_passed": True, "tasks": 100, "checks": 605})
            finally:
                r.current_source_hashes = original
    result = {"ready": True, "created_at": now(), "adapter_sha256": launch_hash,
              "study_sha256": digest(spec / "frozen/study.json"), "arms": checks, "model_calls": 0}
    save(spec / "execution/preflight.json", result)
    print(json.dumps(result))


def start(workspace):
    spec, study = frozen(workspace)
    launch_hash = verify_adapter(spec)
    pre = read(spec / "execution/preflight.json")
    if not pre["ready"] or pre["adapter_sha256"] != launch_hash:
        raise ValueError("Launch adapter preflight missing or stale")
    base, r, rp = load_modules(spec / "execution/bootstrap/runtime")
    r.RUNS = workspace / "experiments/runs"
    settings, key = base.settings_for_run(workspace)
    if not key or r.public_settings(settings) != study["configuration"]:
        raise ValueError("Provider configuration changed")
    with rp.runner_lock(r):
        if (spec / "campaign.json").exists() or (spec / "runs.json").exists():
            raise ValueError("This campaign already has a first launch; no duplicate")
        save(spec / "campaign.json", {"status": "queued", "created_at": now(), "adapter_sha256": launch_hash,
              "arm_order": study["first_exploratory_arm_order"], "authorized_task_executions": 500})
        save(spec / "runs.json", {})
        owner_path = spec / "execution/minimal_harness_online.py"
        with (spec / "campaign.log").open("ab", buffering=0) as output:
            owner = subprocess.Popen([str(workspace / "controller/.venv/bin/python"), "-B", str(owner_path),
                                      "--workspace", str(workspace), "campaign"], cwd=workspace,
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True)
        save(spec / "launch.json", {"started_at": now(), "owner_pid": owner.pid,
                                  "adapter_sha256": launch_hash, "model": "deepseek-flash", "workers": 2})
    print(json.dumps({"status": "launched", "owner_pid": owner.pid, "arm_order": study["first_exploratory_arm_order"]}))


def analyze(workspace, spec):
    completed = subprocess.run([str(workspace / "controller/.venv/bin/python"), "-B",
                               str(spec / "execution/summarize_minimal_harness_online.py"),
                               "--workspace", str(workspace)], cwd=workspace, timeout=180)
    if completed.returncode:
        raise RuntimeError("Result aggregation failed")


def campaign(workspace):
    spec, study = frozen(workspace)
    launch_hash = verify_adapter(spec)
    base, r, rp = load_modules(spec / "execution/bootstrap/runtime")
    r.RUNS = workspace / "experiments/runs"
    with (spec / ".campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read(spec / "campaign.json")
        if state["status"] != "queued":
            raise ValueError("Campaign has already started")
        state.update(status="running", started_at=now(), owner_pid=os.getpid())
        save(spec / "campaign.json", state)
        keeper = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child = None
        def interrupt(*_):
            if child is not None and child.poll() is None:
                child.send_signal(signal.SIGTERM)
            raise KeyboardInterrupt("campaign interrupted")
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, interrupt)
        try:
            while True:
                registry = read(spec / "runs.json")
                arm = next_arm(study, registry)
                if arm is None:
                    state.update(status="completed_and_verified", finished_at=now(), active_arm=None)
                    save(spec / "campaign.json", state)
                    analyze(workspace, spec)
                    return 0
                frozen(workspace)
                verify_adapter(spec)
                with rp.runner_lock(r):
                    attempt = r.RUNS / (time.strftime("%Y%m%d-%H%M%S") + f"-harness-{arm}-" + uuid.uuid4().hex[:8])
                    attempt.mkdir(mode=0o700)
                    runtime, _, _ = initialize_attempt(base, r, rp, workspace, spec, arm, attempt, launch_hash, study)
                    registry[arm] = {"attempt": str(attempt), "status": "prepared", "created_at": now()}
                    save(spec / "runs.json", registry)
                state.update(active_arm=arm, active_attempt=str(attempt))
                save(spec / "campaign.json", state)
                log_path = spec / "logs" / f"{arm}.log"
                log_path.parent.mkdir(exist_ok=True)
                with log_path.open("ab", buffering=0) as log:
                    child = subprocess.Popen([str(runtime / "controller/.venv/bin/python"), "-B",
                        str(spec / "execution/minimal_harness_online.py"), "--workspace", str(workspace),
                        "execute", str(attempt)], cwd=runtime, env=clean_environment(study, arm),
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                    state.update(active_pid=child.pid)
                    save(spec / "campaign.json", state)
                    code = child.wait()
                analyze(workspace, spec)
                if code != 0 or read(spec / "runs.json")[arm]["status"] != "completed_and_verified":
                    raise RuntimeError("Arm failed; remaining arms are not launched")
        except BaseException as exc:
            if child is not None and child.poll() is None:
                child.send_signal(signal.SIGTERM)
                child.wait()
            state.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                         error_type=type(exc).__name__, finished_at=now())
            save(spec / "campaign.json", state)
            print(json.dumps({"campaign_status": state["status"], "error_type": type(exc).__name__}), flush=True)
            return 2
        finally:
            keeper.terminate()
            keeper.wait()


def execute(workspace, attempt):
    spec, study = frozen(workspace)
    launch_hash = verify_adapter(spec)
    runtime = attempt / "runtime"
    base, r, rp = load_modules(runtime)
    plan = read(attempt / "plan.json")
    arm = plan["study"]["arm"]
    if plan != planned_attempt(workspace, spec, arm, launch_hash):
        raise ValueError("Attempt changed frozen plan")
    for key, value in study["arms"][arm].items():
        if os.environ.get(key) != value:
            raise ValueError("Arm environment mismatch")
    configure_runner(r, workspace, runtime, plan)
    settings, key = base.settings_for_run(workspace)
    if not key or r.public_settings(settings) != study["configuration"]:
        raise ValueError("Provider configuration changed")
    original_scene = rp.run_scene
    def stop_on_failure(*args, **kwargs):
        result = original_scene(*args, **kwargs)
        if result["status"] != "completed":
            args[6].set()
        return result
    with rp.runner_lock(r), rp.attempt_lock(attempt):
        registry = read(spec / "runs.json")
        if registry[arm]["status"] != "prepared" or registry[arm]["attempt"] != str(attempt):
            raise ValueError("Not a fresh registered first attempt")
        rp.verify_provenance(r, attempt, plan, settings)
        registry[arm].update(status="running", pid=os.getpid(), started_at=now())
        save(spec / "runs.json", registry)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
        ok, failure = False, None
        try:
            rp.run_scene = stop_on_failure
            save(attempt / "execution-environment.json", {"runtime_env": study["arms"][arm],
                 "runtime_root": str(runtime), "adapter_sha256": launch_hash, "recorded_at": now()})
            ok = rp.coordinate(r, attempt, plan, settings, key, read(attempt / "manifest.json"), {})
            if ok:
                rp.verify_provenance(r, attempt, plan, settings)
        except BaseException as exc:
            failure = type(exc).__name__
            raise
        finally:
            rp.run_scene = original_scene
            registry[arm].update(status="awaiting_verification" if ok else "interrupted",
                                 finished_at=now(), error_type=failure)
            save(spec / "runs.json", registry)
            base.seal(attempt)
        if ok:
            # Reuse the existing complete-scene and suite verification logic.
            import coordinated_search_pilot as verifier
            verifier.verify(attempt)
            registry[arm]["status"] = "completed_and_verified"
            save(spec / "runs.json", registry)
        return 0 if ok else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("command", choices=("prepare", "preflight", "start", "campaign", "execute"))
    parser.add_argument("attempt", type=Path, nargs="?")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if args.command == "execute":
        return execute(workspace, args.attempt.resolve())
    return globals()[args.command](workspace) or 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Online study adapter stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
