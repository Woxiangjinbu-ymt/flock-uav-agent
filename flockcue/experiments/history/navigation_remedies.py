#!/usr/bin/env python3
"""One fresh 60-task B1 remedies run, compared only with saved B0 first attempts.

Reuses the v3 scorer, deadline, worker lifecycle and scene coordinator unchanged.
There is no resume command, model smoke run or B0 execution in this entry point.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid
import zipfile

# An archived run copies this file back to its original scripts directory.
_source_dir = Path(__file__).resolve().parent
if _source_dir.name == "history":
    sys.path.insert(0, str(_source_dir.parents[2] / "scripts"))


import reproduction as r
import reproduction_parallel as rp

ROOT = r.ROOT
STUDY = "b1-remedies-historical-b0-60-v1"
METHOD = "b1-targeted-remedies-v1"
METHOD_COMMIT = "cb80fde8269a09795782a6ef046d04c381b3f3c9"
NAME = "b1-remedies-60-v1"
SCENES = ("ad34350a", "ef36e27a", "3973d0d6")
DATA = ROOT / "experiments/subsets/representative-v1"
SPEC = ROOT / "experiments/b1-remedies-pilot-v1"
HISTORY = ROOT / "experiments/runs/20261003-143255-full-5c501118"
OLD_PILOT = ROOT / "experiments/runs/20261006-115922-harness-pilot-3f71f966/plan.json"


def settings_for_run():
    # Explicit provider selection; do not change the user's selected provider or
    # inherit a generic key that may belong to Qwen or a different gateway.
    provider = copy.deepcopy(r.read_json(ROOT / "agent4drone/llm_settings.json")["provider_configs"]["deepseek_official"])
    if (provider.get("type"), provider.get("base_url", "").rstrip("/"), provider.get("default_model")) != (
        "openai-compatible", "https://api.deepseek.com", "deepseek-flash"
    ):
        raise ValueError("Expected historical baseline's official deepseek-flash configuration")
    key = provider.pop("api_key", "") or os.environ.get("DEEPSEEK_API_KEY")
    provider["api_key"] = ""
    settings = {"selected_provider": "deepseek_official", "provider_configs": {"deepseek_official": provider},
                "uav_api_key": "", "share_blackboard_by_session": True, "toolchain_json_recording": True}
    return settings, key


def assert_agent_source():
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", METHOD_COMMIT, "agent4drone"],
                                    cwd=ROOT, text=True).splitlines()
    expected = {p: r.digest(subprocess.check_output(["git", "show", f"{METHOD_COMMIT}:{p}"], cwd=ROOT))
                for p in paths if p.endswith(".py")}
    actual = {p: checksum for p, checksum in r.current_source_hashes().items() if p.startswith("agent4drone/") and p.endswith(".py")}
    if actual != expected:
        raise ValueError("Actual agent Python source differs from the frozen remedies commit")
    return expected


def assert_common_source():
    def common(hashes):
        return {name: value for name, value in hashes.items()
                if name.endswith(".py") and name.startswith(("server/", "controller/"))}
    expected = common(r.read_json(HISTORY / "source_hashes.json"))
    actual = common(r.current_source_hashes())
    # Development tests can differ without changing the simulated world/checker.
    expected = {p: h for p, h in expected.items() if "/tests/" not in p}
    actual = {p: h for p, h in actual.items() if "/tests/" not in p}
    if actual != expected:
        raise ValueError("Common server/controller runtime differs from historical baseline")
    return expected


def build_plan():
    canonical = {scene["id"]: (name, scene) for name, scene in r.canonical_scenes()}
    plan = r.make_plan(NAME, [canonical[sid] for sid in SCENES])
    for scene in plan["sessions"]:
        scene["source_file"] = scene["file"]
        scene["file"] = "scenes/" + scene["file"]
        if r.digest((DATA / scene["file"]).read_bytes()) != scene["sha256"]:
            raise ValueError("Selected original scene data changed")
    previous = r.read_json(OLD_PILOT)["plans"]["pilot"]
    if plan["sessions"] != previous["sessions"] or (plan["expected_tasks"], plan["expected_checks"]) != (60, 320):
        raise ValueError("This study must use exactly the previously fixed 60 tasks")
    plan["study"] = {
        "id": STUDY, "method": METHOD, "agent_commit": METHOD_COMMIT, "workers": 2,
        "baseline": "B0 historical first_attempt_no_replay; no new B0 execution",
        "baseline_inputs": {name: r.digest((SPEC / name).read_bytes()) for name in ("baseline.json", "baseline-audit.json")},
        "old_selection_sha256": r.digest(OLD_PILOT.read_bytes()),
        "comparison_limitations": ["Historical B0 used 8 workers on 2026-10-03; this run uses 2 workers.",
            "Same API model ID does not fix provider weights or sampling across dates.",
            "Scene state and memory carry forward within each original task sequence.",
            "Three scenes are not sixty independent samples; targeted distance failure examples are outside this set."],
        "failure_policy": "Stop dispatch on provider, worker or infrastructure failure; retain all first attempts; no replay.",
        "preflight": "Offline only; the first model-backed task belongs to the frozen denominator.",
    }
    return plan


def load_plan(name=NAME):
    if name != NAME:
        raise ValueError("Unexpected study plan")
    saved = r.read_json(SPEC / "plan.json")
    if saved != build_plan():
        raise ValueError("Frozen study plan or historical baseline changed")
    return saved


def assert_baseline(plan):
    baseline = r.read_json(SPEC / "baseline.json")
    audit = r.read_json(SPEC / "baseline-audit.json")
    source = baseline["baseline_source"]
    keys = [(row["scene_id"], row["task_id"]) for row in baseline["rows"]]
    expected = [(scene["id"], task["id"]) for scene in plan["sessions"] for task in scene["tasks"]]
    if (keys != expected or not audit["readiness"]["ready"] or baseline["status"] != "frozen"
            or audit["baseline_sha256"] != r.digest((SPEC / "baseline.json").read_bytes())
            or source["agent_commit"] != "1794e45e421fb5de03094f0b63f9ca95f86ab42f"
            or source["cohort"] != "first_attempt_no_replay" or source["generation"] != 1
            or source["model"] != "deepseek-flash" or source["provider"] != "deepseek_official"
            or sum(row["task_pass"] is True for row in baseline["rows"]) != 47):
        raise ValueError("Historical comparison is not the audited B0 first-attempt 60")


def prepare():
    agent_hashes = assert_agent_source()
    plan = build_plan()
    assert_baseline(plan)
    target = SPEC / "plan.json"
    if target.exists() and r.read_json(target) != plan:
        raise ValueError("Refusing to overwrite a different study plan")
    r.save_json(target, plan)
    settings, key = settings_for_run()
    checks = {}
    for component in ("server", "controller", "agent4drone"):
        check = subprocess.run(["uv", "--no-cache", "pip", "check", "--python", str(ROOT / component / ".venv/bin/python")],
                               capture_output=True, text=True, timeout=60)
        checks[component] = {"ok": check.returncode == 0, "output": (check.stdout + check.stderr).strip()}
    common = assert_common_source()
    report = {"study": STUDY, "checked_at": rp.timestamp(), "configuration": r.public_settings(settings),
              "plan_sha256": r.digest(target.read_bytes()), "agent_python_hashes": agent_hashes,
              "dependencies": checks, "key_present": bool(key), "historical_common_python_files": len(common),
              "historical_common_source_mismatches": [],
              "offline_ready": bool(key) and all(v["ok"] for v in checks.values())}
    r.save_json(SPEC / "preflight.local.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "agent_python_hashes"}, ensure_ascii=False, indent=2))
    return report["offline_ready"]


def freeze_runtime(attempt):
    """Launch workers and the checker from the archived Python, not live source."""
    r.code_snapshot(attempt)
    runtime = attempt / "runtime"
    runtime.mkdir()
    with zipfile.ZipFile(attempt / "source.zip") as archive:
        archive.extractall(runtime)
    assets = {}
    for source in sorted((ROOT / "server/config/example").glob("*.json")):
        relative = source.relative_to(ROOT)
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        assets[str(relative)] = r.digest(target.read_bytes())
    for component in ("server", "controller", "agent4drone"):
        (runtime / component / ".venv").symlink_to(ROOT / component / ".venv", target_is_directory=True)
    for selected in load_plan()["sessions"]:
        target = runtime / "data" / selected["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DATA / selected["file"], target)
    r.save_json(attempt / "runtime-assets.json", assets)
    return runtime


def seal(attempt):
    files = {str(p.relative_to(attempt)): r.digest(p.read_bytes()) for p in sorted(attempt.rglob("*"))
             if p.is_file() and p.name != "suite-seal.json"}
    r.save_json(attempt / "suite-seal.json", {"files": files})


def verify(attempt):
    sys.dont_write_bytecode = True
    attempt = Path(attempt).resolve()
    saved = r.read_json(attempt / "suite-seal.json")["files"]
    actual = {str(p.relative_to(attempt)): r.digest(p.read_bytes()) for p in attempt.rglob("*")
              if p.is_file() and p.name != "suite-seal.json"}
    if actual != saved:
        raise ValueError("Sealed attempt files changed")
    rp.verify_archive(r, attempt)
    manifest, plan = r.read_json(attempt / "manifest.json"), r.read_json(attempt / "plan.json")
    for name, checksum in plan["study"]["baseline_inputs"].items():
        if r.digest((attempt / name).read_bytes()) != checksum:
            raise ValueError("Archived historical comparison differs from the frozen plan")
    old_root, old_data = r.ROOT, r.DATA
    try:
        r.ROOT, r.DATA = attempt / "runtime", attempt / "runtime/data"
        checker = sys.modules.get("check_ui.agent_checker")
        if checker and not Path(checker.__file__).resolve().is_relative_to(r.ROOT):
            raise ValueError("A different checker is already imported; verify in a fresh process")
        for selected in plan["sessions"]:
            record = manifest["scenes"][selected["id"]]
            if record["generation"] != 1:
                raise ValueError("Study permits first attempts only")
            rp.verify_scene(r, attempt, plan, selected, record)
    finally:
        r.ROOT, r.DATA = old_root, old_data
    result = r.summarize(plan, r.read_json(attempt / "results.json"), manifest["status"])
    if manifest["status"] != "completed" or not result["valid_complete_run"]:
        raise ValueError("Run is incomplete or invalid; cannot report a final success rate")
    return result


def run():
    plan, (settings, key) = load_plan(), settings_for_run()
    assert_baseline(plan)
    preflight = r.read_json(SPEC / "preflight.local.json")
    if not (preflight["offline_ready"] and key and preflight["configuration"] == r.public_settings(settings)
            and preflight["plan_sha256"] == r.digest((SPEC / "plan.json").read_bytes())):
        raise ValueError("Prepare this exact configuration before launch")
    assert_agent_source()
    assert_common_source()
    # Never copy credentials into runtime archives or JSON evidence.
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    with rp.runner_lock(r):
        attempt = r.RUNS / (time.strftime("%Y%m%d-%H%M%S") + "-b1-remedies60-" + uuid.uuid4().hex[:8])
        attempt.mkdir(mode=0o700)
        r.save_json(attempt / "plan.json", plan)
        r.save_json(attempt / "configuration.json", r.public_settings(settings))
        r.save_json(attempt / "agent-settings.json", settings)
        r.save_json(attempt / "gateway_preflight.json", preflight)
        for name in ("baseline.json", "baseline-audit.json"):
            shutil.copyfile(SPEC / name, attempt / name)
        runtime = freeze_runtime(attempt)
        fingerprint = rp.identity(r, attempt, 2)
        r.save_json(attempt / "identity.json", fingerprint)
        manifest = {"status": "created", "started_at": rp.timestamp(), "git_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    "study": STUDY, "agent_commit": METHOD_COMMIT, "method_version": METHOD,
                    "protocol": plan["protocol"], "plan": NAME, "benchmark_sha256": r.ZIP_SHA,
                    "source": "source.zip and runtime/; workers and checker use frozen copies",
                    "identity_sha256": rp.json_digest(r, fingerprint),
                    "executor": {"id": rp.EXECUTOR_ID, "workers": 2, "automatic_retries": 0,
                                 "recovery": "disabled for this study", "provider_error_policy": rp.PROVIDER_ERROR_POLICY},
                    "scenes": {s["id"]: {"status": "pending", "generation": 0, "directory": None, "history": []}
                               for s in plan["sessions"]}}
        r.save_json(attempt / "manifest.json", manifest)
        old_root, old_data, old_loader, original_run_scene = r.ROOT, r.DATA, r.load_plan, rp.run_scene
        previous_signal = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))

        def stop_on_failure(*args, **kwargs):
            try:
                result = original_run_scene(*args, **kwargs)
                if result["status"] != "completed":
                    args[6].set()  # shared stop_event, not an extra retry/recovery path
                else:
                    rp.verify_scene(args[0], args[1], args[2], args[3], {
                        "directory": str(args[4].relative_to(args[1])), "seal_sha256": result["seal_sha256"]})
                return result
            except BaseException:
                args[6].set()
                raise

        try:
            r.ROOT, r.DATA, r.load_plan = runtime, runtime / "data", load_plan
            rp.run_scene = stop_on_failure
            with rp.attempt_lock(attempt):
                ok = rp.coordinate(r, attempt, plan, settings, key, manifest, {})
        finally:
            r.ROOT, r.DATA, r.load_plan, rp.run_scene = old_root, old_data, old_loader, original_run_scene
            signal.signal(signal.SIGTERM, previous_signal)
            seal(attempt)
        if ok:
            verify(attempt)
        print(json.dumps({"attempt": str(attempt), "complete_and_verified": bool(ok)}, ensure_ascii=False), flush=True)
        return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("run")
    check = sub.add_parser("verify")
    check.add_argument("attempt", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            print(json.dumps(verify(args.attempt), ensure_ascii=False, indent=2))
            return 0
        return 0 if (prepare() if args.command == "prepare" else run()) else 2
    except Exception as error:
        # Only exception class: provider details or config locals can contain keys.
        print(f"Study stopped: {type(error).__name__}; inspect saved local evidence", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
