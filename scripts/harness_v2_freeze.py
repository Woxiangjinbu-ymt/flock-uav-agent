"""Freeze v1 base + v2 adapter only after offline regression. No online calls."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

from harness_v2_world import ROOT, sha, write_json


def freeze(study, workspace):
    study, workspace = study.resolve(), workspace.resolve()
    if (study / "manifest.json").exists():
        raise ValueError("Frozen study already exists")
    preflight = json.loads((study / "fixture-preflight.json").read_text())
    assert preflight["passed"] and preflight["offline_executions"] == 48
    base_zip = workspace / "experiments/minimal-harness-v1/frozen/shared/source.zip"
    runtime = study / "frozen/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(base_zip) as archive:
        archive.extractall(runtime)
        names = archive.namelist()
    additions = [str(p.relative_to(workspace)) for p in (workspace / "scripts").glob("harness_v2_*.py")]
    additions.append("scripts/tests/test_harness_v2.py")
    for name in additions:
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(workspace / name, target)
    for component in ("agent4drone", "server", "controller"):
        env = runtime / component / ".venv"
        if not env.exists():
            env.symlink_to(workspace / component / ".venv", target_is_directory=True)
        result = subprocess.run([str(env / "bin/python"), "-c",
            "import importlib.metadata as m; print('\\n'.join(sorted(d.metadata['Name']+'=='+d.version for d in m.distributions())))"],
            capture_output=True, text=True, check=True)
        (study / "frozen" / f"{component}-dependencies.txt").write_text(result.stdout)
    tests = ["scripts/tests/test_harness_v2.py", "agent4drone/tests/test_minimal_harness.py",
             "agent4drone/tests/test_task_evidence_r2.py", "agent4drone/tests/test_task_progress_integration.py",
             "scripts/tests/test_minimal_harness_runtime.py", "scripts/tests/test_reproduction_metrics.py"]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYGAME_HIDE_SUPPORT_PROMPT": "1"}
    result = subprocess.run([str(workspace / "agent4drone/.venv/bin/python"), "-m", "pytest", "-q",
                             "-p", "no:cacheprovider", *tests], cwd=runtime, env=env, capture_output=True, text=True)
    (study / "frozen/regression.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError("Frozen runtime regression failed")
    files = [study / "schedule.json", study / "fixture-preflight.json", study / "PROTOCOL_CN.md"]
    files.extend((study / "fixtures").iterdir())
    files.extend(study / "frozen" / f"{c}-dependencies.txt" for c in ("agent4drone", "server", "controller"))
    files.append(study / "frozen/regression.log")
    files.extend(runtime / name for name in set(names + additions))
    manifest = {"schema": 2, "frozen_unix": time.time(), "status": "frozen_ready",
        "base_v1_zip_sha256": sha(base_zip.read_bytes()), "cases": 24, "executions": 96,
        "arms": ["applicability", "failure"], "repeats": 2, "workers": 2,
        "model": "deepseek-flash", "temperature": 0.1,
        "full100_authorized": False, "provider_calls_during_preflight": 0,
        "files": {str(p.relative_to(study)): sha(p.read_bytes()) for p in sorted(files)}}
    write_json(study / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--study", type=Path, required=True)
    p.add_argument("--workspace", type=Path, default=ROOT)
    a = p.parse_args()
    print(json.dumps({"files": len(freeze(a.study, a.workspace)["files"]), "online_calls": 0}))
