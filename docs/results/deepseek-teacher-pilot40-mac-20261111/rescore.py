"""Re-run the original 40 initial and 40 terminal check trees from the sealed Mac rehearsal."""
import argparse
from contextlib import chdir
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from harness_episode_local import block_network
from flockcue_session_cpu.verify import canonical_sha, file_sha, original_scene, owned, process_evidence, read, require


def rescore_scene(run, session_id, world):
    from flockcue_session_cpu.score import evaluate

    worker = process_evidence(run, "worker")
    scorer = process_evidence(run, "scorer")
    require(worker["finished_ns"] <= scorer["started_ns"], "Scorer ran before worker exit")
    source = original_scene(run, session_id)
    inputs = read(run / "run-input.json")
    for name, expected in inputs["source_sha256"].items():
        if name.startswith(("server/", "controller/")) or name in {
                "scripts/reproduction.py", "scripts/harness_v2_world.py", "scripts/flockcue_session_cpu/score.py"}:
            require(file_sha(owned(ROOT, name)) == expected, f"Scoring source changed: {name}")
    session = read(run / "session.json")
    results = []
    for task, row in zip(source["tasks"], session["tasks"], strict=True):
        tid = task["id"]
        require(tid == row["task_id"] and (row["status"], row["reason"]) == ("completed", "agent_finished"),
                f"Missing completed original task: {tid}")
        folder = owned(run, row["directory"])
        boundary = read(folder / "boundary.json")
        before = read(folder / "before.json")
        require(boundary["checker_sha256"] == canonical_sha(task["execution_check_apis"]), f"Changed tree: {tid}")
        record = {"task_id": tid}
        for phase, snapshot, score_name, snapshot_hash, state_hash in (
                ("initial", "initial-world.pkl", "initial-score.json", boundary["initial_world_sha256"],
                 before["policy_start"]["world_state_sha256"]),
                ("terminal", "world.pkl", "score.json", boundary["world_sha256"], boundary["world_state_sha256"])):
            score_path = folder / score_name
            require(worker["files"][row["directory"] + "/" + snapshot] == snapshot_hash and
                    scorer["files"][row["directory"] + "/" + score_name] == file_sha(score_path),
                    f"Unsealed {phase} evidence: {tid}")
            saved = read(score_path)
            require((saved["task_id"], saved["world_sha256"], saved["scorer_pid"], saved["worker_pid"]) ==
                    (tid, snapshot_hash, scorer["pid"], worker["pid"]), f"Score identity mismatch: {tid}")
            checker = evaluate(world, source, task, folder / snapshot, snapshot_hash, state_hash)
            digest = canonical_sha(checker)
            require(digest == canonical_sha(saved["checker"]), f"{tid} {phase} check tree or leaf responses differ")
            record[phase] = {"passed": checker["passed"], "leaves": checker["checks_expected"],
                             "checker_result_sha256": digest, "snapshot_sha256": snapshot_hash}
        results.append(record)
    require(len(results) == 20, "A complete original scene is required")
    return {"session_id": session_id, "tasks": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run, output = args.run.resolve(strict=True), args.output.resolve()
    if output.is_relative_to(run):
        parser.error("Write the rescore outside the sealed run")
    network_attempts = block_network()
    from flockcue_session_cpu.score import load_world
    config = read(run / "batch-input.json")
    status = read(run / "batch-status.json")
    require(config["case"] == "normal" and config["sessions"] == ["04cd6269", "ef36e27a"] and
            status["complete"] and status["stop_reason"] is None, "A completed normal pilot is required")
    with TemporaryDirectory(prefix="flockcue-offline-rescore-") as tmp, chdir(tmp):
        world = load_world()
        scenes = [rescore_scene(run / "sessions" / session_id, session_id, world) for session_id in config["sessions"]]
    policy_modules = sorted(set(sys.modules) & {
        "uav_agent", "flockcue_session_cpu.runtime", "flockcue_session_cpu.teacher_transport", "harness_deepseek_adapter"})
    require(not policy_modules and not network_attempts, "Rescorer crossed the policy or network boundary")
    totals = {}
    for phase in ("initial", "terminal"):
        rows = [task[phase] for scene in scenes for task in scene["tasks"]]
        totals[phase] = {"trees_matched": len(rows), "leaves_matched": sum(r["leaves"] for r in rows),
                         "trees_passed": sum(r["passed"] for r in rows)}
    audit = {"totals": totals, "network_attempts": network_attempts, "policy_modules_imported": policy_modules,
             "script_sha256": file_sha(Path(__file__)), "sessions": scenes}
    output.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(audit, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if output.exists():
        require(output.read_text() == data, "Existing audit differs from the new rescore")
    else:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(data)
    print(json.dumps(totals))


if __name__ == "__main__":
    main()
