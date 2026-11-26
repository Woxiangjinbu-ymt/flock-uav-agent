"""Extract public pilot turns and their private eligibility index from a sealed scene."""
import argparse
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime import executor, parallel
from posttraining.inference import EPISODE_TOKEN_LIMIT
from posttraining.sft.adapter import load_tokenizer
from posttraining.sft.history import InputRejected
from posttraining.sft.supervision import supervise
from .context_window import PROMPT_LIMIT
from .index_wire import scene_indices
from .rebuild import packed, public_turn


def accepted_receipt(receipt):
    if receipt["status"] != "success":
        return False
    try:
        value = json.loads(receipt["content"])
    except ValueError:
        return False
    if not isinstance(value, dict):
        return isinstance(value, list)
    return not (value.get("error") or value.get("tool_exception") or value.get("partial_success")
                or value.get("success") is False or value.get("status") in {"error", "partial_success"}
                or value.get("outcome") in {"blocked", "error", "partial", "unknown"})


def project_turn(tokenizer, stream, turn):
    # The wire index retains the evidence coordinates for incomplete calls.
    if "response_attempt_id" not in turn or any(c["receipt"] is None for c in turn["calls"]):
        return {"projection": "incomplete_response_or_receipts"}, None
    wire = public_turn(stream, turn)
    line = packed(wire) + b"\n"
    fields = {"public_sha256": executor.digest(line),
              "receipts_accepted": all(map(accepted_receipt, wire["post_action_receipts"]))}
    window = turn.get("student_context", {})
    if not window or window["tokens"] > PROMPT_LIMIT:
        raise ValueError("Pilot reply lacks an admitted Qwen prompt")
    try:
        _, _, audit = supervise(tokenizer, wire["messages"], wire["tools"], wire["target"])
    except InputRejected as error:
        fields["projection"] = error.code
    else:
        if (audit["prompt_ids_sha256"] != window["ids_sha256"]
                or audit["masked_prompt_tokens"] != window["tokens"]):
            raise ValueError("Training prefix differs from the teacher's live Qwen projection")
        fields.update(projection="ok", target_tokens=audit["supervised_tokens"])
    return fields, line


def extract_scene(attempt, scene_id, destination, tokenizer):
    executor.use_stage(attempt.parent.parent)
    registration = executor.read_json(executor.PILOT_REGISTRATION)
    plan = executor.read_json(attempt / "plan.json")
    if (executor.digest((attempt / "plan.json").read_bytes()) != registration["plan_sha256"]
            or scene_id not in registration["pilot_scenes"]):
        raise ValueError("Expected a registered training pilot scene")
    manifest = executor.read_json(attempt / "manifest.json")
    scene = next(s for s in plan["sessions"] if s["id"] == scene_id)
    record = manifest["scenes"][scene_id]
    folder = parallel.safe_directory(attempt, record["directory"])
    parallel.verify_archive(executor, attempt)
    scores = parallel.verify_scene(executor, attempt, plan, scene, record)
    files = executor.read_json(folder / "complete.json")["files"]
    metrics = (folder / "agent-traces/reproduction-metrics.jsonl").read_text().splitlines()
    failed_jobs = {e["job_id"] for e in map(json.loads, metrics)
                   if e["event"] == "llm_error" and (e.get("status_code") or 0) >= 400}
    tasks = executor.read_json(executor.DATA / scene["file"])["tasks"]
    episodes = []
    with (destination / "public.jsonl.gz").open("xb") as raw, gzip.GzipFile(
        filename="", mode="wb", fileobj=raw, mtime=0
    ) as public, (destination / "decisions.jsonl").open("xb") as index:
        wire_rows = scene_indices(Path("."), folder, files, scene_id, "training_expansion")
        for (ep, turns, _), score, task in zip(wire_rows, scores, tasks, strict=True):
            task_id = ep["task_id"]
            if task_id != score["task_id"] or task_id != task["id"]:
                raise ValueError("Score, wire archive and original task order disagree")
            initial = executor.read_json(folder / f"{task_id}.initial-checks.json")["passed"]
            passed = score["task_pass"] and ep["job_id"] not in failed_jobs
            episodes.append({**ep, "formal_pass": passed, "checker_pass": score["task_pass"],
                             "initial_pass": initial, "instruction": task["content"]})
            spent, reachable = 0, True
            with (attempt / ep["archive_path"]).open("rb") as stream:
                for turn in turns:
                    window = turn.get("student_context", {})
                    row = {"scene_id": scene_id, "task_id": task_id, "turn": turn["turn"],
                           "logical_call_id": turn["logical_call_id"], "attempts": len(turn["attempts"]),
                           "window": window, "fingerprint": turn.get("fingerprint"), "model": turn.get("model"),
                           "usage": turn.get("usage"), "tools": [c["name"] for c in turn["calls"]],
                           "wire_issues": turn["issues"], "formal_pass": passed, "initial_pass": initial,
                           "complete_episode": ep["archive_complete"], "late": turn["turn"] * 3 > len(turns) * 2}
                    fields, line = project_turn(tokenizer, stream, turn)
                    row.update(fields)
                    if line is not None:
                        public.write(line)
                    if row["projection"] == "ok":
                        spent += window["tokens"] + row["target_tokens"]
                    reachable = reachable and row["projection"] == "ok" and spent <= EPISODE_TOKEN_LIMIT
                    row["shadow_reachable"] = reachable
                    row["raw_candidate"] = bool(passed and ep["archive_complete"]
                                                and turn["wire_verified"] and row["projection"] == "ok")
                    index.write(packed(row) + b"\n")
    executor.save_json(destination / "episodes.json", episodes)
    return {"tasks": len(episodes), "passed": sum(e["formal_pass"] for e in episodes),
            "turns": sum(e["logical_turns"] for e in episodes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scene", required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as temp:
        stage = Path(temp) / "scene"
        stage.mkdir()
        summary = extract_scene(args.attempt.resolve(), args.scene, stage, load_tokenizer())
        stage.rename(args.output)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
