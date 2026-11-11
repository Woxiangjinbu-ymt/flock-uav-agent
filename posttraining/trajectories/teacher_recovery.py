"""Complete only a never-started independent scorer from a quiescent, sealed teacher worker."""
import os
from pathlib import Path

from .__main__ import ROOT, inventory, launch, read, sha, verify_files
from .teacher_service import exclusive_worker
from .verify import process_evidence, require


def recover_scores(scene, batch_root):
    """Called under the coordinator lock. A prior scorer log is its exclusive launch claim."""
    scene, base = Path(scene).resolve(), Path(batch_root).resolve()
    require(scene.parent == base / "sessions", "Teacher scene is outside this batch")
    with exclusive_worker(base):
        worker_file = scene / "worker-exit.json"
        if not worker_file.is_file() or (scene / "scorer.log").exists() or (scene / "scorer-exit.json").exists():
            return False
        if read(worker_file).get("returncode") != 0:
            return False
        worker = process_evidence(scene, "worker")
        previous = {**worker["files"], "worker-exit.json": sha(worker_file)}
        if inventory(scene) != previous:
            # A prior scorer or another process left files that the worker did not seal.
            return False
        inputs = read(scene / "run-input.json")
        state = read(scene / "session.json")
        require(inputs["scope"] == "deepseek_teacher_continuous_service" and
                inputs["session_id"] == scene.name == state["session_id"] and
                inputs["teacher"]["session_id"] == scene.name and state["worker_pid"] == worker["pid"] and
                inputs["expected_task_ids"] == [r["task_id"] for r in state["tasks"]],
                "Sealed worker identity does not match this teacher scene")
        verify_files(ROOT, inputs["source_sha256"])
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if k in os.environ}
        env.update(PYTHONPATH=str(ROOT / "scripts"), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONHASHSEED="0", HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES="", PYGAME_HIDE_SUPPORT_PROMPT="1")
        # launch() exclusively creates scorer.log before spawning. A live orphan scorer always owns that path.
        launch("scorer", scene, env, previous)
        process_evidence(scene, "scorer")
        return True
