#!/usr/bin/env python3
"""Prepare and verify a same-skill study OFFLINE. Deliberately has no launch command.

Freezes the tested working tree, including new Python modules, rather than a Git
commit which would omit the implementation. Secrets and historical traces are
excluded from the source archive. Online launch is a separate research step.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import copy
import json
import shutil
import subprocess
import tempfile
import zipfile

import m1_progress_pilot as base
from m1_revision2_pilot import dependency_freezes

r, rp, ROOT = base.r, base.rp, base.ROOT
SPEC = ROOT / "experiments/minimal-harness-v1"
ORIGINAL = ROOT / "experiments/m1-revision2-v1/frozen"
STUDY = "minimal-harness-same-skill-v1"
ARMS = {
    "r2_reference": {"UAV_TASK_PROGRESS_MODE": "evidence_r2"},
    "same_skill_every_step": {"UAV_TASK_PROGRESS_MODE": "evidence_harness", "UAV_HARNESS_SCHEDULER": "every_step"},
    "same_skill_fixed4": {"UAV_TASK_PROGRESS_MODE": "evidence_harness", "UAV_HARNESS_SCHEDULER": "fixed"},
    "same_skill_failure": {"UAV_TASK_PROGRESS_MODE": "evidence_harness", "UAV_HARNESS_SCHEDULER": "failure"},
    "same_skill_applicability": {"UAV_TASK_PROGRESS_MODE": "evidence_harness", "UAV_HARNESS_SCHEDULER": "applicability"},
}
for _environment in ARMS.values():
    _environment.update(UAV_MODEL_REQUEST_AUDIT="1", UAV_HARNESS_FIXED_K="4")


def prepare():
    if (SPEC / "frozen").exists():
        raise ValueError("Already frozen; never overwrite a prepared study")
    manifest = r.read_json(ORIGINAL / "fast100.json")
    plan = base.base_plan(manifest)
    plan["name"] = STUDY
    settings, _ = base.settings_for_run(ROOT)  # no key check or provider request
    public_config = r.public_settings(settings)
    freezes = dependency_freezes(ROOT)
    sources = {name: path.read_bytes() for name, path in base.source_files().items()}
    analysis = "experiments/analysis/validate_minimal_harness.py"
    sources[analysis] = (ROOT / analysis).read_bytes()
    required = {"agent4drone/harness_core.py", "agent4drone/harness_skills.py",
                "agent4drone/harness_integration.py", "agent4drone/tests/test_minimal_harness.py"}
    if not required <= sources.keys():
        raise ValueError("Missing implementation in source archive")
    SPEC.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".prepare-", dir=SPEC))
    try:
        shutil.copyfile(ORIGINAL / "fast100.json", temp / "fast100.json")
        for scene in plan["sessions"]:
            original = ORIGINAL / "data" / scene["file"]
            if r.digest(original.read_bytes()) != scene["sha256"]:
                raise ValueError("Original scene hash changed")
            destination = temp / "data" / scene["file"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
        shutil.copytree(ORIGINAL / "assets", temp / "assets")
        base.write_bundle(temp / "shared", sources, freezes)
        for arm, environment in ARMS.items():
            arm_plan = copy.deepcopy(plan)
            arm_plan["study"] = {"id": STUDY, "arm": arm, "runtime_env": environment,
                                 "source_bundle": "shared/source.zip", "workers": 2,
                                 "repeat": 1, "automatic_retries": 0, "recovery": "disabled"}
            r.save_json(temp / "plans" / f"{arm}.json", arm_plan)
        audit = r.read_json(ROOT / "docs/results/minimal-harness-offline-20261015/report.json")
        if not audit["passed"]:
            raise ValueError("Local server interface validation has not passed")
        r.save_json(temp / "eligibility.json", {"counts": audit["eligibility_counts"],
                    "entries": [{k: row[k] for k in ("task_id", "session_id", "skill", "command")}
                                for row in audit["entries"]]})
        study = {"schema": 1, "id": STUDY, "created_at": rp.timestamp(), "status": "prepared_not_launched",
                 "plan": plan, "arms": ARMS, "workers": 2, "model_calls_during_preparation": 0,
                 "configuration": public_config, "source_origin": "tested_working_tree_snapshot_including_untracked_python",
                 "head_commit": base.git_commit("HEAD"), "source_bundle": "shared/source.zip",
                 "first_exploratory_arm_order": ["same_skill_fixed4", "r2_reference", "same_skill_applicability",
                                                "same_skill_every_step", "same_skill_failure"],
                 "denominator": {"tasks_per_arm": 100, "checks_per_arm": 605, "scenes": 5,
                                 "total_task_executions_first_sweep": 500, "repeat": 1},
                 "common_protocol": "Original scene/task order and within-scene carryover; 500 seconds; first attempts; unchanged checker; all 100 tasks retained.",
                 "policy_scope": "All four skill arms share primitives, guards, local repair, observations, prompt and activation rule; only checkpoint scheduling differs. Failure means handoff after a recoverable failure; mandatory invalidators apply to every arm.",
                 "activation_rule": "Model explicitly selects and commits run_harness; no inherited 400-second trigger; no forced invocation in the online study.",
                 "primary": "Original task-level success at 500 seconds on all 100 tasks; paired wins/losses against each declared comparator.",
                 "secondary": ["original leaf checks / 605", "deadline and abandonment", "logical model requests",
                               "actual HTTP attempts and unresolved attempts", "handler wall time (not internal thinking)",
                               "provider usage including cache fields", "public reads/actions", "skill eligibility/invocation/completion/exit",
                               "local failures handled without model; unnecessary or missed handoff examples"],
                 "analysis_rule": "Report full denominator first; eligible/invoked subgroups are descriptive; do not infer significance or noninferiority from one development sweep. Include failures and uninvoked eligible tasks. Do not select best repeats.",
                 "next_confirmation": "After this exploratory sweep, freeze separate validation tasks and repetition/order protocol before any confirmatory run.",
                 "offline_report_sha256": r.digest((ROOT / "docs/results/minimal-harness-offline-20261015/report.json").read_bytes())}
        study["frozen_files"] = {str(p.relative_to(temp)): r.digest(p.read_bytes())
                                  for p in temp.rglob("*") if p.is_file()}
        r.save_json(temp / "study.json", study)
        temp.rename(SPEC / "frozen")
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    print(json.dumps({"status": "prepared_not_launched", "arms": len(ARMS), "tasks_per_arm": 100,
                      "source_files": len(sources), "model_calls": 0}))


def verify():
    frozen = SPEC / "frozen"
    study = r.read_json(frozen / "study.json")
    actual = {str(p.relative_to(frozen)): r.digest(p.read_bytes()) for p in frozen.rglob("*")
              if p.is_file() and p != frozen / "study.json"}
    if study["id"] != STUDY or study["arms"] != ARMS or actual != study["frozen_files"]:
        raise ValueError("Study metadata or frozen file hash mismatch")
    hashes = r.read_json(frozen / "shared/source_hashes.json")
    with zipfile.ZipFile(frozen / "shared/source.zip") as archive:
        if {name: r.digest(archive.read(name)) for name in archive.namelist()} != hashes:
            raise ValueError("Source archive content mismatch")
    for arm in ARMS:
        plan = r.read_json(frozen / "plans" / f"{arm}.json")
        if plan["study"]["runtime_env"] != ARMS[arm] or plan["sessions"] != study["plan"]["sessions"]:
            raise ValueError("Arm differs in tasks or environment")
    return study


def preflight():
    study = verify()
    r.save_json(SPEC / "preflight.json", {"ready": False, "model_calls": 0})
    freezes = dependency_freezes(ROOT)
    with tempfile.TemporaryDirectory(prefix=".preflight-", dir=SPEC) as tmp:
        runtime = base.materialize_runtime(Path(tmp), SPEC / "frozen", "shared", ROOT)
        for component, text in freezes.items():
            if text != (Path(tmp) / f"{component}-freeze.txt").read_text():
                raise ValueError("Dependencies differ from freeze")
        completed = subprocess.run([str(runtime / "agent4drone/.venv/bin/python"), "-B", "-m", "pytest", "-q",
            "-p", "no:cacheprovider", "agent4drone/tests/test_minimal_harness.py",
            "agent4drone/tests/test_task_evidence_r2.py", "agent4drone/tests/test_task_progress_integration.py",
            "scripts/tests/test_minimal_harness_runtime.py"], cwd=runtime, capture_output=True, text=True, timeout=120)
        (SPEC / "preflight-tests.log").write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise ValueError("Frozen runtime tests failed; see preflight-tests.log")
    result = {"ready": True, "status": "offline_prepared_not_launched", "model_calls": 0,
              "study_sha256": r.digest((SPEC / "frozen/study.json").read_bytes()),
              "shared_source_bundle_sha256": study["frozen_files"]["shared/source.zip"],
              "created_at": rp.timestamp(), "scope": "Same-skill offline tests, R2 integration, service path, dependencies, source/data hashes; no provider availability check."}
    r.save_json(SPEC / "preflight.json", result)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify", "preflight"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "preflight":
        preflight()
    else:
        study = verify()
        print(json.dumps({"verified": True, "status": study["status"], "model_calls": 0}))


if __name__ == "__main__":
    main()
