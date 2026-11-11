"""Run one frozen pilot scene with a shared blackboard and a local teacher fixture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random


def run_teacher_session(scene: dict, output: Path, config: dict):
    from .runtime import run_session
    from .teacher_transport import TeacherFixture

    pilot = json.loads(Path(config["manifest"]).read_bytes())
    selected, = (item for item in pilot["sessions"] if item["session_id"] == config["session_id"])
    if scene["id"] != selected["session_id"] or config["seed"] != selected["runtime_seed"]:
        raise ValueError("Teacher scene or runtime seed differs from the frozen pilot")
    commands = [{"index": i, "task_id": task["id"],
                 "command_sha256": hashlib.sha256(task["content"].encode("utf-8")).hexdigest()}
                for i, task in enumerate(scene["tasks"], start=1)]
    if commands != selected["tasks"] or len(commands) != 20:
        raise ValueError("Teacher task order or command bytes differ from the original scene")

    random.seed(config["seed"])
    ledger = Path(config["batch_root"]) / "cost-ledger.jsonl"
    teacher = TeacherFixture(ledger, pilot["pilot_limits"], currency=config.get("currency", "CNY"),
                             batch_cap=config.get("batch_cap"), per_task_cap=config.get("per_task_cap"),
                             missing_usage=config.get("missing_usage", False))
    try:
        run_session(scene, output, teacher=teacher, seed=config["seed"])
    finally:
        teacher.close()
