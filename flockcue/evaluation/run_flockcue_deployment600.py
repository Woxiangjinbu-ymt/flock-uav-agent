"""Stage and run the registered FlockCue first pass with the October scene executor."""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
import zipfile


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
OCTOBER = ROOT / "experiments/runs/20261003-143255-full-5c501118"
PLAN = "flockcue-600-first-pass"
PLAN_FILE = Path("experiments/plans") / f"{PLAN}.json"
PLAN_SHA = "6589d8e1b4be6664cb3d651f09fe2cc54e41ffca7a361a6dae675ee455ec8823"
OCTOBER_SOURCE_SHA = "8ef80197a34dc4fee9c6feb1aef6bed43d0ff8e3c705ecb3274ccfc1ff0de600"
PROFILE = Path("experiments/hover-compact-contract-v2/profile.json")
COMPONENTS = ("controller", "server", "agent4drone")
HELPERS = (
    "flockcue_session_cpu/__init__.py", "flockcue_session_cpu/teacher_sense.py",
    "flockcue_qwen35/__init__.py", "flockcue_qwen35/adapter.py",
    "thinking_events/__init__.py", "thinking_events/sense_default.py",
    "thinking_events/sense_example_prepare.py", "thinking_events/student.py",
    "thinking_events/student_process.py", "thinking_events/student_timing.py",
    "thinking_events/campaign_storage.py",
    "prepare_task_dataset.py", "student_input.py", "student_input_v2.py",
)
DEFAULT_STAGE = ROOT / "outputs/flockcue-deployment600-20261111"


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def copy(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def import_executor(stage):
    """The original loader still compares the complete, independently rebuilt plan."""
    sys.path.insert(0, str(stage / "scripts"))
    import reproduction as runner
    check(Path(runner.__file__).resolve().parent == stage / "scripts", "Wrong executor imported")
    plan_path = stage / PLAN_FILE
    check(sha(plan_path) == PLAN_SHA, "Changed registered 600-task plan")
    ids = {s["id"] for s in read(plan_path)["sessions"]}
    original_select = runner.select_scenes

    def select(name, scenes):
        if name == PLAN:
            return [(path, scene) for path, scene in scenes if scene["id"] in ids]
        return original_select(name, scenes)

    runner.select_scenes = select
    return runner


def source(stage):
    # This entry reproduces the registered November deployment. Later
    # refactors belong to a new evaluation, with their own registration.
    sys.path.insert(0, str(ROOT))
    from flockcue.history import sealed_source, source_registry
    relocated = source_registry()["sources"]

    def policy_source(name, target, expected=None):
        if expected is None:
            expected = relocated[name]["before_sha256"] if name in relocated else sha(ROOT / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sealed_source(ROOT, name, expected))

    archive = OCTOBER / "source.zip"
    check(sha(archive) == OCTOBER_SOURCE_SHA, "Unexpected October source")
    with zipfile.ZipFile(archive) as data:
        data.extractall(stage)
    # The sealed current profile identifies the Agent files which replace October's policy.
    manifest_path = ROOT / "docs/results/hover-compact-contract-20261103/local-01/runs/action/manifest.json"
    manifest = read(manifest_path)
    for name, expected in manifest["sources"].items():
        if name.startswith("agent4drone/"):
            policy_source(name, stage / name, expected)
    seal = read(manifest_path.parents[2] / "campaign-seal.json")["files"]
    check(sha(ROOT / PROFILE) == seal["selection-source/" + str(PROFILE)], "Changed FlockCue profile")
    copy(ROOT / PROFILE, stage / PROFILE)
    for helper in HELPERS:
        policy_source("scripts/" + helper, stage / "scripts" / helper)
    policy_source("scripts/flockcue_deployment_agent.py", stage / "scripts/reproduction_agent.py")
    copy(Path(__file__).resolve(), stage / "scripts/run_flockcue_deployment600.py")
    for component in COMPONENTS:
        env = ROOT / component / ".venv"
        actual = subprocess.check_output(["uv", "--no-cache", "pip", "freeze", "--python", str(env / "bin/python")])
        check(actual == (OCTOBER / f"{component}-freeze.txt").read_bytes(), f"Dependency drift in {component}")
        (stage / component / ".venv").symlink_to(env, target_is_directory=True)
    copy(ROOT / "benchmark/benchmark.zip", stage / "benchmark/benchmark.zip")
    copy(ROOT / PLAN_FILE, stage / PLAN_FILE)


def prepare(stage):
    check(not stage.exists(), f"Stage already exists: {stage}")
    stage.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(ROOT / "agent4drone/.venv/bin/python"), "-m", "agent_harness", "check"],
                   cwd=ROOT, check=True)
    with TemporaryDirectory(prefix=".flockcue600-", dir=stage.parent) as temp:
        staged = Path(temp) / "deployment"
        staged.mkdir(mode=0o700)
        source(staged)
        runner = import_executor(staged)
        # Uses published benchmark bytes. Writes a local empty-key DeepSeek config.
        runner.prepare()
        plan = runner.load_plan(PLAN)
        check((len(plan["sessions"]), plan["expected_tasks"], plan["expected_checks"]) == (30, 600, 3705),
              "Wrong deployment input counts")
        reference = read(OCTOBER / "plan.json")
        selected = {s["id"] for s in plan["sessions"]}
        check(plan["protocol"] == reference["protocol"] and
              plan["sessions"] == [s for s in reference["sessions"] if s["id"] in selected],
              "Selected October original plan differs")
        expected_files = runner.current_source_hashes()
        for file in (PROFILE, PLAN_FILE, Path("agent4drone/llm_settings.json"), Path("benchmark/benchmark.zip")):
            expected_files[str(file)] = sha(staged / file)
        registration = {
            "plan": PLAN, "workers": 8, "tasks": 600, "leaf_checks": 3705,
            "model": "deepseek-flash", "monetary_cap": None, "first_attempt_only": True,
            "october_source_sha256": OCTOBER_SOURCE_SHA,
            "agent_entry_source": "scripts/flockcue_deployment_agent.py",
            "workspace_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "files": expected_files,
        }
        runner.save_json(staged / "deployment-inputs.json", registration)
        # Give the archived runner its own git_head, binding it to the staged file hashes.
        subprocess.run(["git", "init", "--quiet"], cwd=staged, check=True)
        subprocess.run(["git", "add", "deployment-inputs.json"], cwd=staged, check=True)
        subprocess.run(["git", "-c", "user.name=FlockCue", "-c", "user.email=flockcue@localhost",
                        "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                        "commit", "--quiet", "-m", "Freeze FlockCue October-protocol deployment inputs"],
                       cwd=staged, check=True)
        staged.rename(stage)
    print(f"Prepared {stage} for {PLAN}. No provider calls.", flush=True)


def restore_environment():
    # Keep machine-specific service settings outside both the snapshot and child env.
    for name in list(os.environ):
        if (name.startswith(("UAV_", "LANGCHAIN_", "LANGSMITH_", "REPRO_", "OPENAI_", "LLM_"))
                or name in {"BUPT_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY",
                            "PYTHONPATH"}):
            del os.environ[name]
    os.environ.update(PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", LANGSMITH_TRACING="false",
                      LANGCHAIN_TRACING_V2="false", NO_PROXY="127.0.0.1,localhost",
                      no_proxy="127.0.0.1,localhost")


def check_stage(stage):
    registered = (stage / "deployment-inputs.json").read_bytes()
    committed = subprocess.check_output(["git", "show", "HEAD:deployment-inputs.json"], cwd=stage)
    check(registered == committed, "Changed deployment registration")
    files = json.loads(registered)["files"]
    for path, expected in files.items():
        check(sha(stage / path) == expected, f"Changed staged input: {path}")
    check(sha(Path(__file__)) == files["scripts/run_flockcue_deployment600.py"],
          "Launcher differs from staged version; use the registered launcher")
    runner = import_executor(stage)
    runner.load_plan(PLAN)
    return runner


@contextmanager
def first_pass_lock(stage):
    with (stage / ".first-pass.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("An operation on this stage is already running") from None
        runs = stage / "experiments/runs"
        check(not runs.exists() or not any(p.is_dir() for p in runs.iterdir()),
              "A first pass already has an attempt in this stage; inspect that attempt")
        yield


def execute(stage, command, key):
    runner = check_stage(stage)
    with first_pass_lock(stage):
        check(bool(key), "Configure DEEPSEEK_API_KEY or the explicit local deepseek_official provider")
        os.environ["DEEPSEEK_API_KEY"] = key
        if command == "preflight":
            ok = runner.preflight(gateway=True)
        else:
            # The original coordinator handles child cancellation and all subprocess groups.
            def interrupt(_signum, _frame):
                raise KeyboardInterrupt("SIGTERM")

            signal.signal(signal.SIGTERM, interrupt)
            print(f"Running {PLAN}, 8 workers, 600 first attempts, no monetary cap.", flush=True)
            ok = runner.run_attempt(PLAN, workers=8)
    return 0 if ok else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight", "run"))
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    args = parser.parse_args()
    stage = args.stage.resolve()
    if args.command == "prepare":
        prepare(stage)
        return 0
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        # Preserve the user's active provider. Read credentials only for the official endpoint.
        provider = read(ROOT / "agent4drone/llm_settings.json")["provider_configs"]["deepseek_official"]
        check(provider["base_url"].rstrip("/") == "https://api.deepseek.com", "Invalid configured DeepSeek endpoint")
        key = provider.get("api_key")
    restore_environment()
    return execute(stage, args.command, key)


if __name__ == "__main__":
    raise SystemExit(main())
