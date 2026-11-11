"""Original controller check tree, executed only in policy-free processes."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_world():
    from harness_v2_world import World
    # The original controller UI imports its widget definitions at module import.
    # We call only its unchanged pure check-tree driver and local check endpoints.
    sys.path.extend(str(p) for p in (ROOT / "controller/.venv/lib").glob("python*/site-packages"))
    for name in ("models", "controllers"):
        sys.modules[name + ".__init__"] = importlib.import_module(name)
    return World()


def evaluate(world, source, task, path, expected, expected_state=None):
    from reproduction import API, upstream_evaluate

    class LocalCheckAPI:
        def call(self, method, endpoint, *, params):
            if method != "GET" or not endpoint.startswith("/check/"):
                raise ValueError(f"Unexpected private check endpoint {endpoint}")
            return world.request(method, endpoint, params=params, admin=True)

        api_check_execution = API.api_check_execution

    world.restore(path.read_bytes(), expected)
    if expected_state is not None and world.state_digest() != expected_state:
        raise ValueError("Sealed world differs from recorded terminal projection")
    tree = task["execution_check_apis"]
    restored = world.server.session_controller.sessions[source["id"]].tasks[task["id"]]
    if restored["execution_check_apis"] != tree:
        raise ValueError("Frozen simulator check tree differs from original scene")
    passed, details = upstream_evaluate(LocalCheckAPI(), tree, source["id"])
    errors = [d for d in details if d.get("error") or d.get("type") == "error"]
    leaves = [d for d in details if d.get("type") == "leaf"]
    if errors or not leaves or any(type(d.get("actual")) is not bool for d in leaves):
        raise RuntimeError(f"Incomplete original check tree for {task['id']}: {errors}")
    return {"passed": passed, "checks_passed": sum(d["result"] for d in leaves),
            "checks_expected": len(leaves), "details": details, "errors": errors}


def score_session(output: Path):
    from harness_v2_world import write_json
    scene = json.loads((output / "source-scene.json").read_bytes())
    session = json.loads((output / "session.json").read_bytes())
    seal = json.loads((output / "worker-exit.json").read_bytes())
    if seal["returncode"] != 0 or seal["pid"] != session["worker_pid"] or os.getpid() == seal["pid"]:
        raise ValueError("Missing independently closed worker")
    world = load_world()
    records = []
    for task, row in zip(scene["tasks"], session["tasks"], strict=True):
        if row["task_id"] != task["id"]:
            raise ValueError("Original task order changed")
        if row["status"] != "completed":
            continue
        folder = output / row["directory"]
        boundary = json.loads((folder / "boundary.json").read_bytes())
        expected = seal["files"][row["directory"] + "/world.pkl"]
        if expected != boundary["world_sha256"] or canonical_sha(task["execution_check_apis"]) != boundary["checker_sha256"]:
            raise ValueError("Original checker or world snapshot differs from seal")
        checker = evaluate(world, scene, task, folder / "world.pkl", expected, boundary["world_state_sha256"])
        if boundary["marked_done"] != (checker["passed"] and row["reason"] == "agent_finished"):
            raise ValueError("Continuation mark-done disagrees with independent post-exit score")
        record = {"task_id": task["id"], "worker_pid": seal["pid"], "scorer_pid": os.getpid(),
                  "world_sha256": expected, "checker_sha256": boundary["checker_sha256"], "checker": checker}
        write_json(folder / "score.json", record)
        records.append({"task_id": task["id"], "passed": checker["passed"]})
    write_json(output / "session_scores.json", {"session_id": scene["id"], "scorer_pid": os.getpid(),
               "worker_pid": seal["pid"], "tasks": records})


def eligibility():
    """Each synchronous gate runs outside the worker, with no policy modules or replies."""
    from harness_episode_local import block_network
    from harness_v2_world import write_json
    attempts = block_network()
    scene_file, directory, task_id, expected = sys.argv[1:]
    source = json.loads(Path(scene_file).read_bytes())
    folder = Path(directory)
    task, = (t for t in source["tasks"] if t["id"] == task_id)
    checker = evaluate(load_world(), source, task, folder / "world.pkl", expected)
    if attempts or "uav_agent" in sys.modules:
        raise RuntimeError("Private mark-done scorer crossed an isolation boundary")
    write_json(folder / "eligibility.json", {"pid": os.getpid(), "task_id": task_id, "passed": checker["passed"],
               "world_sha256": expected, "agent_imported": False, "network_attempts": attempts})


if __name__ == "__main__":
    eligibility()
