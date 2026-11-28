"""Extract public pilot turns and their private eligibility index from a sealed scene."""
import argparse
from dataclasses import dataclass
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime import executor, parallel
from posttraining.inference import HISTORICAL_EPISODE_TOKEN_LIMIT, MAX_GENERATION
from posttraining.sft.adapter import load_tokenizer
from posttraining.sft.history import InputRejected
from posttraining.sft.supervision import supervise
from .context_window import PROMPT_LIMIT
from .index_wire import scene_indices
from .rebuild import packed, public_turn


def succeeded(value):
    return not (value.get("error") or value.get("tool_exception") or value.get("harness_transport_error")
                or value.get("partial_success") or value.get("success") is False
                or value.get("status") in {"error", "partial_success"}
                or value.get("outcome") in {"blocked", "error", "partial", "unknown"})


def accepted_receipt(receipt):
    if receipt["status"] != "success":
        return False
    try:
        value = json.loads(receipt["content"])
    except ValueError:
        return False
    if not isinstance(value, dict):
        return isinstance(value, list)
    if not succeeded(value):
        return False
    # Compound movement tools report the actual command outcome inside the sensing envelope.
    return all(succeeded(value[key]) for key in ("navigation_result", "move_result")
               if isinstance(value.get(key), dict))


def project_turn(tokenizer, stream, turn, fallback_limit=None):
    # The wire index retains the evidence coordinates for incomplete calls.
    if "response_attempt_id" not in turn or any(c["receipt"] is None for c in turn["calls"]):
        return {"projection": "incomplete_response_or_receipts"}, None
    wire = public_turn(stream, turn)
    line = packed(wire) + b"\n"
    fields = {"public_sha256": executor.digest(line),
              "receipts_accepted": all(map(accepted_receipt, wire["post_action_receipts"]))}
    window = turn.get("student_context", {})
    limit = window.get("fallback_prompt_limit", PROMPT_LIMIT)
    if not window or window["tokens"] > limit:
        raise ValueError("Pilot reply lacks an admitted Qwen prompt")
    if limit != PROMPT_LIMIT and (limit != fallback_limit or window["tokens"] <= PROMPT_LIMIT
                                  or sum(m["role"] == "assistant" for m in wire["messages"]) > 1):
        raise ValueError("Fallback must contain only the required group that exceeded the ordinary limit")
    try:
        _, _, audit = supervise(tokenizer, wire["messages"], wire["tools"], wire["target"],
                                context_limit=limit + MAX_GENERATION)
    except InputRejected as error:
        fields["projection"] = error.code
        if error.code != "sft_overlength":
            return fields, line
        fields["target_tokens"] = error.details["target_tokens"]
        prefix_hash = error.details["prompt_ids_sha256"]
    else:
        fields.update(projection="ok", target_tokens=audit["supervised_tokens"])
        prefix_hash = audit["prompt_ids_sha256"]
    if prefix_hash != window["ids_sha256"]:
        raise ValueError("Training prefix differs from the teacher's live Qwen projection")
    return fields, line


@dataclass
class SceneSource:
    attempt: Path
    scene: dict
    folder: Path
    files: dict
    rows: list
    split: str
    evidence_sha256: str

    def origin(self):
        return {"attempt": self.attempt.name, "scene_id": self.scene["id"],
                "seal_sha256": self.evidence_sha256,
                "plan_sha256": executor.digest((self.attempt / "plan.json").read_bytes()),
                "settings_sha256": executor.digest((self.attempt / "agent-settings.json").read_bytes())}


def sealed_source(attempt, scene_id):
    executor.use_stage(attempt.parent.parent)
    plan = executor.read_json(attempt / "plan.json")
    split = "training_expansion"
    if plan["name"] in {executor.COLLECTION_PLAN, executor.REPAIR_PLAN}:
        registration = executor.read_json(executor.COLLECTION_REGISTRATION)
        role = executor.collection_train_roles().get(scene_id)
        if not role or scene_id in registration["carried_seals"]:
            raise ValueError("Scene is outside the newly collected training cohort")
        if plan["name"] == executor.REPAIR_PLAN:
            registration = executor.read_json(executor.REPAIR_REGISTRATION)
        if role == "baseline_train":
            split = "training"
    else:
        registration = executor.read_json(executor.PILOT_REGISTRATION)
        if scene_id not in registration["pilot_scenes"]:
            raise ValueError("Expected a registered training pilot scene")
    if executor.digest((attempt / "plan.json").read_bytes()) != registration["plan_sha256"]:
        raise ValueError("Attempt differs from the registered training plan")
    manifest = executor.read_json(attempt / "manifest.json")
    scene = next(s for s in plan["sessions"] if s["id"] == scene_id)
    record = manifest["scenes"][scene_id]
    folder = parallel.safe_directory(attempt, record["directory"])
    parallel.verify_archive(executor, attempt)
    scores = parallel.verify_scene(executor, attempt, plan, scene, record)
    files = executor.read_json(folder / "complete.json")["files"]
    return SceneSource(attempt, scene, folder, files, scores, split, record["seal_sha256"])


def write_episodes(source, public, index, tokenizer, mixed):
    attempt, scene, folder = source.attempt, source.scene, source.folder
    scene_id = scene["id"]
    settings = executor.read_json(attempt / "configuration.json")
    fallback_limit = settings.get("fallback_prompt_limit")
    episode_limit = settings.get("student_episode_tokens", HISTORICAL_EPISODE_TOKEN_LIMIT)
    failed_jobs = parallel.terminal_http_jobs(folder)
    task_ids = {row["task_id"] for row in source.rows}
    tasks = [task for task in executor.read_json(attempt.parent.parent / "scenes" / scene["file"])["tasks"]
             if task["id"] in task_ids]
    wire_rows = scene_indices(Path("."), folder, source.files, scene_id, source.split, task_ids)
    episodes = []
    for (ep, turns, _), score, task in zip(wire_rows, source.rows, tasks, strict=True):
        task_id = ep["task_id"]
        if task_id != score["task_id"] or task_id != task["id"]:
            raise ValueError("Score, wire archive and original task order disagree")
        if mixed:
            ep["attempt"] = attempt.name
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
                fields, line = project_turn(tokenizer, stream, turn, fallback_limit)
                row.update(fields)
                if line is not None:
                    public.write(line)
                if row["projection"] == "ok":
                    spent += window["tokens"] + row["target_tokens"]
                reachable = (reachable and turn["wire_verified"] and row["projection"] == "ok"
                             and (episode_limit is None or spent <= episode_limit))
                row["shadow_reachable"] = reachable
                row["raw_candidate"] = bool(passed and ep["archive_complete"]
                                            and turn["wire_verified"] and row["projection"] == "ok")
                index.write(packed(row) + b"\n")
    return episodes


def export_sources(sources, destination, tokenizer, origin):
    with (destination / "public.jsonl.gz").open("xb") as raw, gzip.GzipFile(
        filename="", mode="wb", fileobj=raw, mtime=0
    ) as public, (destination / "decisions.jsonl").open("xb") as index:
        episodes = [ep for source in sources
                    for ep in write_episodes(source, public, index, tokenizer, len(sources) > 1)]
    executor.save_json(destination / "episodes.json", episodes)
    executor.save_json(destination / "origin.json", origin)
    return {"tasks": len(episodes), "passed": sum(e["formal_pass"] for e in episodes),
            "turns": sum(e["logical_turns"] for e in episodes)}


def extract_scene(attempt, scene_id, destination, tokenizer):
    source = sealed_source(attempt, scene_id)
    return export_sources([source], destination, tokenizer, source.origin())


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
