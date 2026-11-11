"""Independently score the pre-action world of each teacher task."""
import os
from pathlib import Path

from .verify import canonical_sha, owned, read, require


def score_start_states(output: Path):
    """Run the original private checker on each sealed pre-action world after worker exit."""
    from harness_v2_world import write_json
    from .score import evaluate, load_world

    scene, session, seal = (read(output / name) for name in
                            ("source-scene.json", "session.json", "worker-exit.json"))
    require(seal["returncode"] == 0 and seal["pid"] == session["worker_pid"] != os.getpid(),
            "Initial-state scoring requires an exited worker and independent scorer")
    world = load_world()
    for task, row in zip(scene["tasks"], session["tasks"], strict=True):
        require(task["id"] == row["task_id"], "Initial scoring order changed")
        if row["status"] == "unstarted":
            continue
        folder = owned(output.resolve(), row["directory"])
        before, boundary = (read(folder / name) for name in ("before.json", "boundary.json"))
        expected = seal["files"][row["directory"] + "/initial-world.pkl"]
        require(expected == boundary["initial_world_sha256"] and
                canonical_sha(task["execution_check_apis"]) == boundary["checker_sha256"],
                "Initial snapshot or checker differs from sealed worker evidence")
        result = evaluate(world, scene, task, folder / "initial-world.pkl", expected,
                          before["policy_start"]["world_state_sha256"])
        write_json(folder / "initial-score.json", {
            "task_id": task["id"], "scorer_pid": os.getpid(), "worker_pid": seal["pid"],
            "world_sha256": expected, "checker_sha256": boundary["checker_sha256"], "checker": result,
        })
