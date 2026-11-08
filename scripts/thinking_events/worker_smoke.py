"""Cross-process CPU delivery fixture on the existing public hover instruction."""

import json
import os
import subprocess
from pathlib import Path
import shutil
import sys

from validate_harness_episode import check_hashes, sha
from .campaign_storage import run_local_slots
from .worker_child import _frozen_modules

ROOT = Path(__file__).resolve().parents[2]
# Two actors from the frozen reference's first policy-visible list_drones result.
ACTOR = "3d229034"
OTHER = "eedfa187"


def _call(name, arguments):
    payload = {"name": name, "arguments": arguments}
    return "<tool_call>\n" + json.dumps(payload, separators=(",", ":")) + "\n</tool_call>"


def scripts():
    read = _call("get_drone_status", {"input_json": {"drone_id": ACTOR}})
    pair = read + "\n" + _call("get_drone_status", {"input_json": {"drone_id": OTHER}})
    takeoff = _call("take_off", {"input_json": {"drone_id": ACTOR, "altitude": 12}})
    hover = _call("hover", {"input_json": {"drone_id": ACTOR}})
    first = _call("list_drones", {})
    # Real tokenization, EOS handling and group parsing are exercised on every reply.
    return {
        "cpu-success": [
            s + "<|im_end|>"
            for s in (
                "<think>Read fleet identities.</think>\n" + first,
                pair,
                takeoff,
                hover,
                read,
                "Done. [TASK DONE]",
            )
        ],
        "cpu-malformed": [first + "<|im_end|>", takeoff + "\n<tool_call>{<|im_end|>"],
        "cpu-cancel": [first + "<|im_end|>", takeoff + "<|im_end|>"],
    }


def prepare_smoke(output):
    from agent_harness.__main__ import checked_reference, prepare_locked

    output = Path(output).absolute()
    lock = json.loads((ROOT / "agent_harness/baseline.lock.json").read_bytes())
    reference = checked_reference(lock)
    output.mkdir(parents=True, exist_ok=False)
    # Derive a new student manifest from a fully checked reference. Preserve the
    # reference manifest in its own output rather than rewriting a sealed run.
    baseline = output / "baseline"
    prepare_locked(lock, reference, baseline)
    run = output / "cpu-run"
    run.mkdir()
    source = run / "source"
    shutil.copytree(baseline / "source", source)
    shutil.copytree(baseline / "inputs", run / "inputs")
    required = [
        ROOT / lock["profile"],
        ROOT / "scripts/student_input.py",
        ROOT / "scripts/student_input_v2.py",
        ROOT / "training/requirements-input.lock",
        ROOT / "training/templates/qwen3-tool-ids-v2.jinja",
        *sorted((ROOT / "scripts/thinking_events").glob("*.py")),
        *sorted((ROOT / "training/tokenizers/qwen3-1.7b-input-v1").glob("*")),
    ]
    for file in required:
        target = source / file.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
    manifest = json.loads((baseline / "manifest.json").read_bytes())
    runtime = {
        "task_seconds": 500,
        "drain_seconds": 0.5,
        "request_limits": {
            "max_total_tokens": 100000,
            "max_http_attempts": 12,
            "max_output_tokens": 2048,
            "per_request_timeout_seconds": 120,
        },
    }
    manifest = {
        "scope": "student_cpu_regression",
        "workspace": str(ROOT),
        "study": str(run / "inputs"),
        "sources": {
            **manifest["sources"],
            **{str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in required},
        },
        "inputs": manifest["inputs"],
        "private_canary": manifest["private_canary"],
        "trials": [
            {
                "trial_id": slot,
                "task_id": "c74d7020",
                "scheduler": "applicability",
                "runtime": runtime,
                "watchdog_seconds": 515,
            }
            for slot in scripts()
        ],
    }
    check_hashes(source, manifest["sources"])
    (run / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (run / "manifest.sha256").write_text(sha((run / "manifest.json").read_bytes()) + "\n")
    return run


def smoke(output, student_python, *, execute_slot=None):
    if execute_slot is None:
        from .worker import run_slot as execute_slot
    output = Path(output).absolute()
    student_python = Path(student_python).absolute()  # keep venv symlink/interpreter identity
    if not student_python.is_file():
        raise FileNotFoundError(student_python)
    run = prepare_smoke(output)
    replies = scripts()
    inputs = run / "fixture-replies.json"
    inputs.write_text(json.dumps(replies, sort_keys=True, indent=2) + "\n")
    plan = [{"slot_id": name, "trial_id": name, "task_id": "c74d7020"} for name in replies]

    def execute(row):
        slot = row["slot_id"]
        return execute_slot(
            run,
            slot,
            model="1.7B",
            thinking=(slot == "cpu-success"),
            student_python=student_python,
            fixture_replies=replies[slot],
            reverse_receipts=(slot == "cpu-success"),
            cancel_at=2 if slot == "cpu-cancel" else None,
        )

    seal = sha((run / "manifest.json").read_bytes() + inputs.read_bytes())
    results = run_local_slots(run, plan, backend_sha256=seal, execute=execute)
    modules = {
        name: str(ROOT / path)
        for name, path in _frozen_modules(ROOT).items()
        if path.startswith(("scripts/", "agent_harness/"))
    }
    summary = {
        "scope": "cpu_fixture_only",
        "gpu_hours": 0,
        "teacher_calls": 0,
        "formal_slots_started": 0,
        "rows": results,
        "coordinator_python": sys.executable,
        "coordinator_modules": modules,
    }
    (output / "smoke.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    if [r["passed"] for r in results] != [True, False, False]:
        raise RuntimeError(f"CPU fixtures failed; inspect {output / 'smoke.json'}")
    env = {**os.environ, "PYTHONPATH": str(run / "source/scripts"), "PYTHONDONTWRITEBYTECODE": "1"}
    proof = subprocess.run(
        [str(student_python), "-m", "thinking_events.worker_verify", str(run)],
        cwd=run / "source",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    summary["verified"] = json.loads(proof.stdout)
    (output / "smoke.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary
