"""Prepare, verify offline, or execute the frozen 200-task October Agent4Drone arm."""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import os
import signal
import subprocess
from tempfile import TemporaryDirectory
import zipfile

from run_flockcue_deployment600 import (
    COMPONENTS, OCTOBER, OCTOBER_SOURCE_SHA, PLAN_FILE as PARENT_PLAN,
    PLAN_SHA as PARENT_SHA, ROOT, check, copy, first_pass_lock, read, restore_environment, sha,
)

PLAN = "agent4drone-matched200-v1"
PLAN_FILE = Path("experiments/plans") / f"{PLAN}.json"
PLAN_SHA = "d1c252e1f377c8c79a612063594451f7d553960dcea67474ba7b16a9711b7ec3"
STAGE = ROOT / "outputs" / PLAN
METRICS = "scripts/reproduction_metrics.py"
REGISTRATION = "deployment-inputs.json"
TEACHER = ROOT / "docs/results/flockcue-deployment600-20261111/summary.json"


def check_plan(plan):
    check(sha(ROOT / PARENT_PLAN) == PARENT_SHA, "Frozen 600-task parent changed")
    parent = read(ROOT / PARENT_PLAN)
    ids = [s["id"] for s in plan["sessions"]]
    expected = [s for s in parent["sessions"] if s["id"] in set(ids)]
    count = sum(len(t["leaves"]) for scene in expected for t in scene["tasks"])
    check(len(ids) == len(set(ids)) == 10 and all(len(s["tasks"]) == 20 for s in expected),
          "Expected 10 whole scenes of 20 tasks")
    check(plan == {**parent, "name": PLAN, "expected_tasks": 200, "expected_checks": count,
                   "sessions": expected}, "Plan differs from the frozen parent or its original order")
    return set(ids)


def telemetry_source(original):
    # Same parsing location as October: consume neither the response stream nor any additional calls.
    anchor = '                    usage = extract_usage(body.get("usage"))'
    check(original.count(anchor) == 1, "Unexpected October observation hook")
    return original.replace(anchor, anchor + '''
                    values["reported_model"] = body.get("model")
                    values["system_fingerprint"] = body.get("system_fingerprint")
                    details = (body.get("usage") or {}).get("completion_tokens_details") or {}
                    values["reasoning_tokens"] = _number(details.get("reasoning_tokens"))''')


def executor(stage, ids):
    sys.path.insert(0, str(stage / "scripts"))
    import reproduction as runner
    check(Path(runner.__file__).resolve().parent == stage / "scripts", "Wrong reproduction module")
    original_select = runner.select_scenes

    def select(name, scenes):
        if name == PLAN:
            return [(name_, s) for name_, s in scenes if s["id"] in ids]
        return original_select(name, scenes)

    runner.select_scenes = select
    return runner


def verify_dependencies(stage):
    for component in COMPONENTS:
        env = ROOT / component / ".venv"
        local = stage / component / ".venv"
        if not local.exists():
            local.symlink_to(env, target_is_directory=True)
        check(local.resolve() == env.resolve(), f"Unexpected {component} environment")
        actual = subprocess.check_output(["uv", "--no-cache", "pip", "freeze", "--python", str(local / "bin/python")])
        check(actual == (OCTOBER / f"{component}-freeze.txt").read_bytes(), f"Dependency drift in {component}")


def prepare():
    check(not STAGE.exists(), f"Stage exists: {STAGE}. Use verify to inspect it.")
    check(sha(ROOT / PLAN_FILE) == PLAN_SHA, "Expected the registered representative 200-task plan")
    plan = read(ROOT / PLAN_FILE)
    ids = check_plan(plan)
    archive = OCTOBER / "source.zip"
    check(sha(archive) == OCTOBER_SOURCE_SHA, "October source archive changed")
    STAGE.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".original200-", dir=STAGE.parent) as temp:
        stage = Path(temp) / "stage"
        stage.mkdir(mode=0o700)
        with zipfile.ZipFile(archive) as z:
            z.extractall(stage)
            original = z.read(METRICS).decode("utf-8")
        (stage / METRICS).write_text(telemetry_source(original), encoding="utf-8")
        verify_dependencies(stage)
        copy(ROOT / "benchmark/benchmark.zip", stage / "benchmark/benchmark.zip")
        copy(ROOT / PLAN_FILE, stage / PLAN_FILE)
        runner = executor(stage, ids)
        runner.prepare()  # extract original scenes, construct its offline preflight plans, empty-key config
        check(runner.load_plan(PLAN) == plan, "October executor did not rebuild the frozen subset")
        hashes = runner.current_source_hashes()
        historical = read(OCTOBER / "source_hashes.json")
        check(set(hashes) == set(historical) and
              all(hashes[k] == v for k, v in historical.items() if k != METRICS),
              "Staged policy, simulator, coordinator or checkers differ from October")
        # The runner includes the stage's git HEAD in every attempt archive.
        previous = read(TEACHER)["gateway_fingerprints"]["november"]
        check(len(previous) == 1, "Ambiguous reference teacher fingerprint")
        registered = {"plan": PLAN, "tasks": 200, "scenes": 10, "workers": 8,
                      "plan_sha256": sha(stage / PLAN_FILE), "parent_sha256": PARENT_SHA,
                      "october_source_sha256": OCTOBER_SOURCE_SHA, "launcher_sha256": sha(Path(__file__)),
                      "settings_sha256": sha(runner.SETTINGS), "reference_fingerprint": previous[0],
                      "archived_files": hashes}
        runner.save_json(stage / REGISTRATION, registered)
        subprocess.run(["git", "init", "--quiet"], cwd=stage, check=True)
        subprocess.run(["git", "add", REGISTRATION], cwd=stage, check=True)
        subprocess.run(["git", "-c", "user.name=Agent4Drone", "-c", "user.email=baseline@localhost",
                        "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit",
                        "--quiet", "-m", "Freeze October baseline for matched 200-task evaluation"], cwd=stage, check=True)
        stage.rename(STAGE)
    print(f"Prepared {STAGE} (offline).", flush=True)


def verify(stage, *, archive_only=False):
    reg = read(stage / REGISTRATION)
    committed = subprocess.check_output(["git", "show", f"HEAD:{REGISTRATION}"], cwd=stage)
    check(committed == (stage / REGISTRATION).read_bytes(), "Stage registration changed")
    if archive_only and sha(Path(__file__)) != reg["launcher_sha256"]:
        # Verify the sealed historical launcher for a read-only report. Live
        # execution below always requires the running launcher itself to match.
        sys.path.insert(0, str(ROOT))
        from flockcue.history import sealed_source
        sealed_source(ROOT, "scripts/run_agent4drone_matched_subset.py", reg["launcher_sha256"])
    else:
        check(sha(Path(__file__)) == reg["launcher_sha256"], "Launcher changed after preparation")
    check(sha(stage / PLAN_FILE) == sha(ROOT / PLAN_FILE) == reg["plan_sha256"] == PLAN_SHA,
          "Frozen subset changed")
    ids = check_plan(read(stage / PLAN_FILE))
    runner = executor(stage, ids)
    check(runner.current_source_hashes() == reg["archived_files"], "Staged executable source changed")
    check(sha(runner.SETTINGS) == reg["settings_sha256"], "Staged settings changed")
    verify_dependencies(stage)
    runner.selected_config()  # validates official URL, model, privileges, original blackboard and recording
    runner.load_plan(PLAN)
    return runner, reg


def run(stage):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        provider = read(ROOT / "agent4drone/llm_settings.json")["provider_configs"]["deepseek_official"]
        check(provider["base_url"].rstrip("/") == "https://api.deepseek.com", "Wrong official provider URL")
        key = provider.get("api_key")
    restore_environment()
    runner, registration = verify(stage)
    with first_pass_lock(stage):
        check(key, "Configure the official DeepSeek credential")
        os.environ["DEEPSEEK_API_KEY"] = key
        if not runner.preflight(gateway=True):
            return 2
        gateway = read(stage / "experiments/preflight.local.json")["gateway"]
        fingerprints = {gateway.get(field, {}).get("system_fingerprint")
                        for field in ("first_response_metadata", "final_response_metadata")}
        print(f"Gateway fingerprints: {sorted(str(f) for f in fingerprints)}; "
              f"reference: {registration['reference_fingerprint']}", flush=True)

        def interrupt(_signum, _frame):
            raise KeyboardInterrupt("SIGTERM")

        signal.signal(signal.SIGTERM, interrupt)
        return 0 if runner.run_attempt(PLAN, workers=8) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify", "run"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "verify":
        _, registration = verify(STAGE, archive_only=True)
        print(f"Verified original policy and {registration['tasks']} tasks (offline).")
    else:
        return run(STAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
