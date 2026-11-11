#!/usr/bin/env python3
"""Single-arm M1-R2 fast100; prepare/preflight are strictly offline.

The historical M0 80/100 is a reference, never a runnable arm. New paid calls
require the separate run/start entry. Shared execution changes only by the
validated resource-writer seal fix; data, scoring and budgets stay unchanged.
Attempts are first-only, without recovery or automatic repetition.
"""
from __future__ import annotations

import argparse
from collections import Counter
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

import m1_progress_pilot as original

r, rp = original.r, original.rp
ROOT = r.ROOT
SPEC = ROOT / "experiments/m1-revision2-v1"
HISTORICAL = ROOT / "experiments/m1-progress-v1"
REPORT = ROOT / "docs/results/m1-progress-fast100-20261006"
STUDY = "m1-revision2-fast100-v1"
ARM = "m1r2"
MODE_VARIABLE = "UAV_TASK_PROGRESS_MODE"
MODE = "evidence_r2"
ENV = {MODE_VARIABLE: MODE, "UAV_MODEL_REQUEST_AUDIT": "1"}
WORKERS = 2
DRIVER = "scripts/m1_revision2_pilot.py"
REQUIRED = ("agent4drone/task_evidence.py", "agent4drone/task_evidence_context.py",
            "agent4drone/model_request_audit.py", "agent4drone/tests/test_task_evidence.py",
            "agent4drone/task_evidence_r2.py", "agent4drone/tests/test_task_evidence_r2.py")
# These exact revisions passed the seal-fix and R2 offline acceptance reports.
# No general runner/checker drift is permitted for the new study.
APPROVED_COMMON = {
    "scripts/reproduction_metrics.py": "ed66d423b8467595c6bd915a8471886cceb710e4f795f5e801f9db700f093cff",
    "scripts/reproduction_parallel.py": "778fc0f395dc7c7aff43a5c7a1df5ed421f3a245bea760facb972bcedf726552",
    "scripts/tests/test_reproduction_metrics.py": "30ec80aeb922f4432614c9f64e2bb1b533af451f622bab9ff747d8d2b30e652e",
    "scripts/tests/test_m1_revision_runtime.py": "437b80d57df073380ceb1e2ae7c506905e98865658677cdd60dc1702051cfe0e",
}
git_commit = original.git_commit
agent_sources = original.agent_sources
common_sources = original.common_sources
settings_for_run = original.settings_for_run
materialize_runtime = original.materialize_runtime
seal = original.seal


def base_plan(manifest):
    plan = original.base_plan(manifest)
    plan["name"] = STUDY
    return plan


def historical_reference():
    """Verify the sealed source of 80/100; copy only reference metadata."""
    for name, base in (("input-sha256.json", ROOT), ("artifact-sha256.json", REPORT)):
        for path, checksum in r.read_json(REPORT / name).items():
            if r.digest((base / path).read_bytes()) != checksum:
                raise ValueError("Historical reference evidence changed")
    summary = r.read_json(REPORT / "summary.json")
    baseline = summary["arms"]["m0"]
    if (baseline["method_commit"], baseline["passed_tasks"], baseline["tasks"], baseline["checks"], baseline["unscored"]) != (
            original.M0_COMMIT, 80, 100, 605, 0):
        raise ValueError("Historical M0 must be verified 80/100")
    pairs = r.read_json(REPORT / "paired-tasks.json")
    tasks = [{"session_id": p["session_id"], "task_id": p["task_id"], "passed": p["m0_pass"],
              "passed_checks": p["m0_passed_checks"], "trace": p["m0_trace"]} for p in pairs]
    if len(tasks) != 100 or sum(p["passed"] for p in tasks) != 80:
        raise ValueError("Historical task ledger differs")
    return {"kind": "historical_reference_not_contemporaneous_control", "summary": baseline,
            "tasks": tasks, "source_summary_sha256": r.digest((REPORT / "summary.json").read_bytes()),
            "source_pairs_sha256": r.digest((REPORT / "paired-tasks.json").read_bytes()),
            "limitations": ["Single new arm; no fresh M0 or static-prompt control",
                            "Development set, five scenes with world/blackboard carry-over",
                            "Remote model alias and random sampling may differ across runs",
                            "Historical paired W/L is descriptive, not randomized causal evidence"]}


def dependency_freezes(workspace):
    result = {}
    for component in ("server", "controller", "agent4drone"):
        python = str(Path(workspace) / component / ".venv/bin/python")
        check = subprocess.run(["uv", "--no-cache", "pip", "check", "--python", python],
                               capture_output=True, text=True, timeout=60)
        if check.returncode:
            raise ValueError("Dependency check failed")
        result[component] = subprocess.check_output(
            ["uv", "--no-cache", "pip", "freeze", "--python", python], text=True, timeout=60)
    return result


def prepare(method_commit, study_dir=SPEC):
    study_dir = Path(study_dir).resolve()
    if (study_dir / "frozen").exists():
        raise ValueError("Study already frozen; never overwrite")
    commit = git_commit(method_commit)
    prior = original.load_study(HISTORICAL)
    if commit in {original.M0_COMMIT, prior["methods"]["m1"]["agent_commit"]}:
        raise ValueError("Use a new committed M1-R2 method")
    method = agent_sources(commit)
    if any(name not in method for name in REQUIRED) or b'"evidence_r2"' not in method["agent4drone/uav_agent.py"]:
        raise ValueError("Committed method lacks M1-R2 implementation/tests")
    common = common_sources()
    # Permit only the exact reviewed seal fix and test updates above.
    for name, checksum in prior["common_source_sha256"].items():
        if name not in common or r.digest(common[name]) != APPROVED_COMMON.get(name, checksum):
            raise ValueError("Historical common runner/server/checker source changed")
    for name, checksum in APPROVED_COMMON.items():
        if name in common and r.digest(common[name]) != checksum:
            raise ValueError("Reviewed seal-fix/test source changed")
    extras = set(common) - set(prior["common_source_sha256"])
    if any(not name.startswith(("scripts/m1_revision_", "scripts/tests/test_m1_revision_",
                                "scripts/m1_revision2_", "scripts/tests/test_m1_revision2_")) for name in extras):
        raise ValueError("Unexpected new common source outside revision study")
    if DRIVER not in common:
        raise ValueError("Revision launcher absent from source archive")
    freezes = dependency_freezes(ROOT)
    for component, content in freezes.items():
        if content != (HISTORICAL / "frozen/m0" / f"{component}-freeze.txt").read_text():
            raise ValueError("Installed dependencies differ from historical M0")
    settings, key = settings_for_run(ROOT)
    if not key or r.public_settings(settings) != prior["configuration"]:
        raise ValueError("Provider configuration must match historical M0")
    reference = historical_reference()
    manifest = r.read_json(HISTORICAL / "frozen/fast100.json")
    plan = base_plan(manifest)
    if [(s["id"], t["id"]) for s in plan["sessions"] for t in s["tasks"]] != [
            (t["session_id"], t["task_id"]) for t in reference["tasks"]]:
        raise ValueError("Historical reference cohort/order differs")
    study_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=study_dir))
    try:
        shutil.copyfile(HISTORICAL / "frozen/fast100.json", temporary / "fast100.json")
        for directory in ("data", "assets"):
            shutil.copytree(HISTORICAL / "frozen" / directory, temporary / directory)
        original.write_bundle(temporary / ARM, {**common, **method}, freezes)
        r.save_json(temporary / "historical-m0.json", reference)
        files = {str(p.relative_to(temporary)): r.digest(p.read_bytes())
                 for p in sorted(temporary.rglob("*")) if p.is_file()}
        study = {"schema": 1, "id": STUDY, "created_at": rp.timestamp(), "workspace": str(ROOT),
                 "runs_root": str(r.RUNS), "workers": WORKERS, "arm_order": [ARM],
                 "methods": {ARM: {"agent_commit": commit, "runtime_env": ENV,
                              "agent_python_sha256": {n: r.digest(b) for n, b in sorted(method.items())}}},
                 "plan": plan, "configuration": r.public_settings(settings), "frozen_files": files,
                 "common_source_sha256": {n: r.digest(b) for n, b in sorted(common.items())},
                 "approved_common_changes": {n: {"historical_sha256": prior["common_source_sha256"].get(n),
                                                  "current_sha256": h,
                                                  "purpose": "resource seal fix or offline runtime test only"}
                                             for n, h in APPROVED_COMMON.items() if n in common},
                 "historical_reference": {"file": "historical-m0.json", "passed_tasks": 80, "tasks": 100,
                                           "kind": reference["kind"]},
                 "offline_ready": True, "model_calls_during_prepare": 0,
                 "failure_policy": "first attempt only; stop on infrastructure/provider failure; no restart/retry/recovery",
                 "limitations": reference["limitations"]}
        r.save_json(temporary / "study.json", study)
        temporary.rename(study_dir / "frozen")
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps({"study": str(study_dir), "method_commit": commit, "arms": [ARM],
                      "tasks": 100, "checks": 605, "model_calls": 0}, ensure_ascii=False))
    return study


def load_study(study_dir=SPEC):
    frozen = Path(study_dir).resolve() / "frozen"
    study = r.read_json(frozen / "study.json")
    actual = {str(p.relative_to(frozen)): r.digest(p.read_bytes()) for p in frozen.rglob("*")
              if p.is_file() and p.name != "study.json"}
    if actual != study["frozen_files"]:
        raise ValueError("Frozen study files changed")
    if (study["id"] != STUDY or study["arm_order"] != [ARM] or set(study["methods"]) != {ARM}
            or study["workers"] != WORKERS or not study["offline_ready"]
            or study["methods"][ARM]["runtime_env"] != ENV
            or study["plan"] != base_plan(r.read_json(frozen / "fast100.json"))):
        raise ValueError("Frozen single-arm method identity differs")
    if r.digest((frozen / "fast100.json").read_bytes()) != original.FAST_SHA256:
        raise ValueError("fast100 denominator/order changed")
    hashes = r.read_json(frozen / ARM / "source_hashes.json")
    if hashes != {**study["common_source_sha256"], **study["methods"][ARM]["agent_python_sha256"]}:
        raise ValueError("Method source identity differs")
    reference = r.read_json(frozen / "historical-m0.json")
    if reference["summary"]["passed_tasks"] != 80 or len(reference["tasks"]) != 100:
        raise ValueError("Historical reference changed")
    return study


def check_environment(study):
    settings, key = settings_for_run(study["workspace"])
    if not key or r.public_settings(settings) != study["configuration"]:
        raise ValueError("Provider configuration differs from preparation")
    if {n: r.digest(b) for n, b in common_sources().items()} != study["common_source_sha256"]:
        raise ValueError("Common launcher/server/checker changed after preparation")
    return settings, key


@contextmanager
def method_environment():
    previous = {key: os.environ.get(key) for key in ENV}
    os.environ.update(ENV)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def preflight(study_dir=SPEC):
    """Test the actual extracted method with all socket connections forbidden."""
    study_dir = Path(study_dir).resolve()
    study = load_study(study_dir)
    check_environment(study)
    freezes = dependency_freezes(study["workspace"])
    for name, value in freezes.items():
        if value != (study_dir / "frozen" / ARM / f"{name}-freeze.txt").read_text():
            raise ValueError("Dependencies changed after freeze")
    with tempfile.TemporaryDirectory(prefix=".preflight-", dir=study_dir) as directory:
        runtime = materialize_runtime(Path(directory), study_dir / "frozen", ARM, study["workspace"])
        script = ("import socket,sys,pytest; "
                  "socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('offline: network forbidden')); "
                  "socket.create_connection=socket.socket.connect; "
                  "sys.exit(pytest.main(['-q','-p','no:cacheprovider',"
                  "'agent4drone/tests/test_task_evidence.py','agent4drone/tests/test_task_progress_integration.py',"
                  "'agent4drone/tests/test_task_evidence_r2.py','scripts/tests/test_reproduction_metrics.py',"
                  "'scripts/tests/test_m1_revision_runtime.py']))")
        env = {**os.environ, **ENV, "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
               "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false"}
        done = subprocess.run([str(runtime / "agent4drone/.venv/bin/python"), "-B", "-c", script],
                              cwd=runtime, env=env, capture_output=True, text=True, timeout=60)
        (study_dir / "preflight-tests.log").write_text(done.stdout + done.stderr)
        if done.returncode:
            raise ValueError("Frozen offline runtime tests failed; see preflight-tests.log")
    record = {"ready_to_start": True, "status": "prepared_not_started", "paid_model_calls": 0,
              "network_connections_forbidden": True, "tests_returncode": done.returncode,
              "tests_log_sha256": r.digest((study_dir / "preflight-tests.log").read_bytes()),
              "study_sha256": r.digest((study_dir / "frozen/study.json").read_bytes()),
              "method_commit": study["methods"][ARM]["agent_commit"], "runtime_env": ENV,
              "arms": [ARM], "historical_m0": "80/100", "created_at": rp.timestamp()}
    r.save_json(study_dir / "preflight.json", record)
    print(json.dumps(record, ensure_ascii=False))
    return record


def run(study_dir, method_commit):
    """ONLINE. Register exactly one M1-R2 attempt and enter its frozen runtime."""
    study_dir = Path(study_dir).resolve()
    study = load_study(study_dir)
    if git_commit(method_commit) != study["methods"][ARM]["agent_commit"]:
        raise ValueError("Requested method differs from freeze")
    pre = r.read_json(study_dir / "preflight.json")
    if not pre["ready_to_start"] or pre["study_sha256"] != r.digest((study_dir / "frozen/study.json").read_bytes()):
        raise ValueError("Offline preflight is missing/stale")
    settings, _ = check_environment(study)
    with rp.runner_lock(r):
        registry = study_dir / "runs.json"
        records = r.read_json(registry) if registry.exists() else {}
        if records:
            raise ValueError("Study already has a first attempt; no automatic rerun")
        attempt = Path(study["runs_root"]) / (time.strftime("%Y%m%d-%H%M%S") + "-m1r2-fast100-" + uuid.uuid4().hex[:8])
        attempt.mkdir(mode=0o700)
        runtime = materialize_runtime(attempt, study_dir / "frozen", ARM, study["workspace"])
        shutil.copyfile(study_dir / "frozen/historical-m0.json", attempt / "historical-m0.json")
        metadata = {"study_id": STUDY, "arm": ARM, **study["methods"][ARM], "workers": WORKERS,
                    "study_sha256": pre["study_sha256"], "source_zip_sha256": r.digest((attempt / "source.zip").read_bytes()),
                    "historical_m0_sha256": r.digest((attempt / "historical-m0.json").read_bytes()),
                    "runtime_assets_sha256": {n.removeprefix("assets/"): v for n, v in study["frozen_files"].items()
                                               if n.startswith("assets/")}}
        r.save_json(attempt / "method-freeze.json", metadata)
        plan = copy.deepcopy(study["plan"])
        plan["study"] = {"id": STUDY, "arm": ARM, "method_freeze_sha256": r.digest((attempt / "method-freeze.json").read_bytes()),
                         "study_directory": str(study_dir), "workspace": study["workspace"],
                         "runs_root": study["runs_root"], "runtime_env": ENV}
        r.save_json(attempt / "plan.json", plan)
        r.save_json(attempt / "configuration.json", r.public_settings(settings))
        r.save_json(attempt / "agent-settings.json", settings)
        shutil.copyfile(study_dir / "frozen/fast100.json", attempt / "fast100.json")
        r.save_json(attempt / "gateway_preflight.json", {"offline_ready": True, "gateway_verified": False,
                    "model_calls": 0, "configuration": study["configuration"], "freeze": metadata})
        identity = rp.identity(r, attempt, WORKERS)
        r.save_json(attempt / "identity.json", identity)
        r.save_json(attempt / "manifest.json", {"status": "created", "started_at": rp.timestamp(),
                    "study": STUDY, "arm": ARM, "agent_commit": metadata["agent_commit"],
                    "metadata": {"freeze": metadata}, "protocol": plan["protocol"], "plan": STUDY,
                    "benchmark_sha256": r.ZIP_SHA, "identity_sha256": rp.json_digest(r, identity),
                    "source": "frozen M1-R2 only; M0 is a historical reference",
                    "executor": {"id": rp.EXECUTOR_ID, "workers": WORKERS, "automatic_retries": 0,
                                 "recovery": "disabled", "provider_error_policy": rp.PROVIDER_ERROR_POLICY},
                    "scenes": {s["id"]: {"status": "pending", "generation": 0, "directory": None, "history": []}
                               for s in plan["sessions"]}})
        r.save_json(registry, {ARM: {"attempt": str(attempt), "status": "prepared", "created_at": rp.timestamp()}})
    print(json.dumps({"attempt": str(attempt), "arm": ARM, "action": "starting_frozen_runtime"}), flush=True)
    env = {**os.environ, **ENV, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
           "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false"}
    os.execve(str(runtime / "controller/.venv/bin/python"),
              [str(runtime / "controller/.venv/bin/python"), "-B", str(runtime / DRIVER), "_execute", str(attempt)], env)


def assert_method_identity(attempt):
    attempt = Path(attempt)
    plan = r.read_json(attempt / "plan.json")
    meta = r.read_json(attempt / "method-freeze.json")
    if (meta["arm"] != ARM or meta["study_id"] != STUDY or meta["runtime_env"] != ENV
            or meta != r.read_json(attempt / "manifest.json")["metadata"]["freeze"]
            or r.digest((attempt / "method-freeze.json").read_bytes()) != plan["study"]["method_freeze_sha256"]
            or plan["study"]["runtime_env"] != ENV
            or r.digest((attempt / "source.zip").read_bytes()) != meta["source_zip_sha256"]
            or r.digest((attempt / "historical-m0.json").read_bytes()) != meta["historical_m0_sha256"]
            or r.digest((attempt / "fast100.json").read_bytes()) != original.FAST_SHA256):
        raise ValueError("Method freeze/configuration changed")
    if {k: v for k, v in plan.items() if k != "study"} != base_plan(r.read_json(attempt / "fast100.json")):
        raise ValueError("Original tasks/scoring/budget changed")
    hashes = r.read_json(attempt / "source_hashes.json")
    if {n: v for n, v in hashes.items() if n.startswith("agent4drone/") and n.endswith(".py")} != meta["agent_python_sha256"]:
        raise ValueError("Frozen agent identity differs")
    for name, value in meta["runtime_assets_sha256"].items():
        if r.digest((attempt / "runtime" / name).read_bytes()) != value:
            raise ValueError("Frozen runtime assets changed")
    for scene in plan["sessions"]:
        if r.digest((attempt / "runtime/data" / scene["file"]).read_bytes()) != scene["sha256"]:
            raise ValueError("Frozen runtime scene changed")
    return plan, meta


def audit_traces(attempt, plan):
    """Method validity is separate from task scores; never rescore tasks here."""
    count = 0
    for scene in plan["sessions"]:
        traces = [r.read_json(p) for p in (attempt / scene["id"] / "agent-traces/toolchains").glob("*.json")]
        expected = Counter(t["content_sha256"] for t in scene["tasks"])
        if Counter(r.digest(t["command"].encode()) for t in traces) != expected:
            raise ValueError("Missing/duplicate task traces")
        for trace in traces:
            runtime, progress = trace.get("runtime", {}), trace.get("task_progress", {})
            if (runtime.get("task_progress_mode") != MODE or runtime.get("task_progress_active") is not True
                    or progress.get("revision") != "m1-r2" or progress.get("observer_errors") != []
                    or progress.get("mission_completion") != "not_evaluated"
                    or trace.get("model_request_audit_errors") != [] or not trace.get("model_requests_wire")):
                raise ValueError("M1-R2 activation/evidence/request audit failed")
            if not progress.get("task_evidence", {}).get("model_requests"):
                raise ValueError("Missing model view audit")
            count += 1
    return {"traces": count, "mode": MODE, "observer_errors": 0}


def historical_comparison(attempt, rows):
    reference = r.read_json(Path(attempt) / "historical-m0.json")
    prior = {(x["session_id"], x["task_id"]): x for x in reference["tasks"]}
    if {(x["session_id"], x["task_id"]) for x in rows} != set(prior) or len(rows) != 100:
        raise ValueError("Comparison must keep all 100 original tasks")
    counts = Counter()
    scenes = {}
    for row in rows:
        if row["status"] != "scored" or not isinstance(row.get("passed"), bool):
            raise ValueError("Partial run: unscored tasks must not become algorithm failures")
        old, new = prior[row["session_id"], row["task_id"]]["passed"], row["passed"]
        label = "both_pass" if old and new else "both_fail" if not old and not new else "W" if new else "L"
        counts[label] += 1
        per_scene = scenes.setdefault(row["session_id"], {"m0": 0, "m1r2": 0, "W": 0, "L": 0})
        per_scene["m0"] += int(old)
        per_scene["m1r2"] += int(new)
        if label in {"W", "L"}:
            per_scene[label] += 1
    passed = sum(row["passed"] for row in rows)
    return {"kind": reference["kind"], "m0_fixed_passed": 80, "m1r2_passed": passed,
            "tasks": 100, "net_tasks": passed - 80, "paired": dict(counts), "scenes": scenes,
            "limitations": reference["limitations"]}


def verify_saved(attempt):
    attempt = Path(attempt).resolve()
    actual = {str(p.relative_to(attempt)): r.digest(p.read_bytes()) for p in attempt.rglob("*")
              if p.is_file() and p.name != "suite-seal.json"}
    if actual != r.read_json(attempt / "suite-seal.json")["files"]:
        raise ValueError("Sealed attempt files changed")
    plan, _ = assert_method_identity(attempt)
    if r.read_json(attempt / "execution-environment.json")["runtime_env"] != ENV:
        raise ValueError("Observed execution mode differs")
    rp.verify_archive(r, attempt)
    manifest = r.read_json(attempt / "manifest.json")
    previous_root, previous_data = r.ROOT, r.DATA
    try:
        r.ROOT, r.DATA = attempt / "runtime", attempt / "runtime/data"
        checker = sys.modules.get("check_ui.agent_checker")
        if checker and not Path(checker.__file__).resolve().is_relative_to(r.ROOT):
            raise ValueError("Verify with the archived checker in a fresh process")
        for selected in plan["sessions"]:
            record = manifest["scenes"][selected["id"]]
            if record["generation"] != 1:
                raise ValueError("Only first attempts allowed")
            rp.verify_scene(r, attempt, plan, selected, record)
    finally:
        r.ROOT, r.DATA = previous_root, previous_data
    rows = r.read_json(attempt / "results.json")
    summary = r.summarize(plan, rows, manifest["status"])
    if manifest["status"] != "completed" or not summary["valid_complete_run"]:
        raise ValueError("Incomplete run; partial diagnosis only")
    return {"task_results": summary, "method_audit": audit_traces(attempt, plan),
            "historical_comparison": historical_comparison(attempt, rows)}


def execute(attempt):
    attempt = Path(attempt).resolve()
    if ROOT.resolve() != attempt / "runtime":
        raise ValueError("Execution must enter archived runtime")
    plan, metadata = assert_method_identity(attempt)
    r.RUNS, r.DATA = Path(plan["study"]["runs_root"]), attempt / "runtime/data"
    if attempt.parent != r.RUNS.resolve():
        raise ValueError("Attempt outside frozen runs root")
    r.load_plan = lambda name: copy.deepcopy(plan) if name == STUDY else (_ for _ in ()).throw(ValueError("Unknown plan"))
    settings, key = settings_for_run(plan["study"]["workspace"])
    if not key or r.public_settings(settings) != r.read_json(attempt / "configuration.json"):
        raise ValueError("Provider configuration changed")
    registry = Path(plan["study"]["study_directory"]) / "runs.json"
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
        records = r.read_json(registry)
        record = records[ARM]
        if set(records) != {ARM} or record["attempt"] != str(attempt) or record["status"] != "prepared":
            raise ValueError("Not a new registered M1-R2 first attempt")
        rp.verify_provenance(r, attempt, plan, settings)
        record.update(status="running", pid=os.getpid(), started_at=rp.timestamp())
        r.save_json(registry, records)
        previous_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
        ok, failure = False, None
        try:
            rp.run_scene = stop_on_failure
            with method_environment():
                r.save_json(attempt / "execution-environment.json", {"runtime_env": {k: os.environ[k] for k in ENV},
                            "pid": os.getpid(), "runtime_root": str(ROOT), "recorded_at": rp.timestamp()})
                ok = rp.coordinate(r, attempt, plan, settings, key, r.read_json(attempt / "manifest.json"), {})
                if ok:
                    rp.verify_provenance(r, attempt, plan, settings)
        except BaseException as error:
            failure = type(error).__name__
            raise
        finally:
            rp.run_scene = original_run_scene
            signal.signal(signal.SIGTERM, previous_handler)
            record.update(status="awaiting_verification" if ok and failure is None else "interrupted",
                          finished_at=rp.timestamp(), exception_type=failure)
            r.save_json(registry, records)
            seal(attempt)
        if ok:
            try:
                result = verify_saved(attempt)
            except Exception as error:
                record.update(status="verification_failed", exception_type=type(error).__name__)
                r.save_json(registry, records)
                raise
            record.update(status="completed_and_verified", historical_comparison=result["historical_comparison"])
            r.save_json(registry, records)
        return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run"):
        cmd = sub.add_parser(name, help="OFFLINE freeze" if name == "prepare" else "ONLINE paid M1-R2 only")
        cmd.add_argument("--method-commit", required=True)
        cmd.add_argument("--study-dir", type=Path, default=SPEC)
    sub.add_parser("preflight", help="OFFLINE extracted-runtime checks").add_argument("--study-dir", type=Path, default=SPEC)
    for name in ("verify", "_verify", "_execute"):
        sub.add_parser(name).add_argument("attempt", type=Path)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    try:
        if args.command == "prepare":
            prepare(args.method_commit, args.study_dir)
        elif args.command == "preflight":
            preflight(args.study_dir)
        elif args.command == "run":
            run(args.study_dir, args.method_commit)
        elif args.command == "_execute":
            return 0 if execute(args.attempt) else 2
        elif args.command == "verify":
            runtime = args.attempt.resolve() / "runtime"
            os.execve(str(runtime / "controller/.venv/bin/python"),
                      [str(runtime / "controller/.venv/bin/python"), "-B", str(runtime / DRIVER), "_verify", str(args.attempt.resolve())],
                      {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        else:
            print(json.dumps(verify_saved(args.attempt), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"M1-R2 stopped: {type(error).__name__}; inspect local evidence", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
