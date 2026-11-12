"""Build the reviewed closing and correction examples from the original training wire records."""
import argparse
from collections import Counter
import csv
import gzip
from hashlib import file_digest, sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ..inference import EPISODE_TOKEN_LIMIT, MAX_CONTEXT, MAX_GENERATION, output_budget
from .adapter import load_tokenizer
from .export import DATASETS, MANIFEST_SHA, RELEASE, ROLES_SHA, checked_bytes, json_line
from .supervision import supervise


FIRST = DATASETS / "sft/qwen35-2b-first-pass"
FIRST_SHA = "a9c8ba6576ae1231afe2bcf030c62167cb6291988aa4c1022310ad40065e48bf"
CANDIDATES = DATASETS / "results/student-validation-60/stop-analysis/teacher-candidates.csv"
COHORT_SHA = "fb3997969c774522719658c88935f8cb219d5f43534a8853373403ad2b30ee9e"
REVIEW = RELEASE / "audit/closing-repair-review.json"
PUBLIC = RELEASE / "public/training-public-turns.jsonl.gz"


def read_projected_turns():
    manifest = json.loads(checked_bytes(RELEASE / "audit/manifest.json", MANIFEST_SHA))
    hashes = {Path(r["path"]).name: r["sha256"] for r in manifest["files"]}
    path = RELEASE / "audit/training-candidate-qwen-audit.jsonl"
    records = map(json.loads, checked_bytes(path, hashes[path.name]).splitlines())
    projection = {(r["session_id"], r["task_id"], r["logical_call_id"]): r for r in records}
    return projection, hashes


def selection(reviews, projection):
    roles = checked_bytes(DATASETS / "original-benchmark/scene-roles.csv", ROLES_SHA)
    train = {r["session_id"] for r in csv.DictReader(roles.decode().splitlines()) if r["role"] == "baseline_train"}
    cohort = list(csv.DictReader(checked_bytes(CANDIDATES, COHORT_SHA).decode().splitlines()))
    pairs = [(r["train_scene_id"], r["task_id"]) for r in cohort]
    if [(r["scene_id"], r["task_id"]) for r in reviews] != pairs:
        raise ValueError("Review must cover the registered training candidates in order")
    wanted = {}
    for candidate, review in zip(cohort, reviews, strict=True):
        scene, task = review["scene_id"], review["task_id"]
        last = int(candidate["teacher_turns"])
        if scene not in train or review["verdict"] not in {"accept", "recollect"}:
            raise ValueError(f"Ineligible review for {task}")
        spent = 0
        for turn in range(1, last + 1):
            r = projection[scene, task, f"model-{turn}"]
            if r["status"] != "compatible":
                raise ValueError(f"Incompatible teacher decision in {task}")
            limit, reason = output_budget(r["prompt_tokens"], spent)
            if reason or r["target_tokens"] > limit:
                raise ValueError(f"Teacher path exceeds student limits at {task}/{turn}")
            spent += r["prompt_tokens"] + r["target_tokens"]
        if spent != int(candidate["teacher_path_projected_tokens"]):
            raise ValueError(f"Teacher path accounting changed for {task}")
        accepted = review["verdict"] == "accept"
        selected = review["selected"]
        if [item["turn"] for item in selected if item["kind"] == "closing"] != ([last] if accepted else []):
            raise ValueError(f"Terminal decision and review disagree for {task}")
        for item in selected:
            turn, kind = item["turn"], item["kind"]
            if not 1 <= turn <= last or kind not in {"action", "correction", "verification", "closing"}:
                raise ValueError(f"Invalid selected decision in {task}")
            if not accepted and kind not in {"correction", "verification"}:
                raise ValueError(f"Only independently reviewed repairs may be taken from {task}")
            r = projection[scene, task, f"model-{turn}"]
            line = r["source_training_line"]
            if line in wanted:
                raise ValueError(f"Duplicate source row {line}")
            wanted[line] = (r, kind, "closing" if accepted else "repair-only")
    return wanted


def check_repair(example):
    """Verify an observed rejection, changed arguments and a completed same-skill retry."""
    prior = example["messages"]
    last_reply = prior[-1]
    failed_call = prior[-2]["tool_calls"][0]
    new_call, = example["target"]["tool_calls"]
    receipt, = example["post_action_receipts"]
    if (last_reply["role"] != "tool" or failed_call["id"] != last_reply["tool_call_id"]
            or json.loads(last_reply["content"]).get("status") != "rejected"
            or failed_call["function"]["name"] != "run_harness" or new_call["function"]["name"] != "run_harness"
            or new_call["id"] != receipt["tool_call_id"] or json.loads(receipt["content"]).get("status") != "completed"):
        raise ValueError("Correction lacks a visible rejection or a successful bound retry")
    commands = []
    for call in (failed_call, new_call):
        args = call["function"]["arguments"]
        value = json.loads(args) if isinstance(args, str) else args
        command = value["input_json"]
        commands.append(json.loads(command) if isinstance(command, str) else command)
    old, new = commands
    retained = {field: old.get(field) for field in ("action", "skill")}
    if new != {"action": "start", "skill": "simple_chain"} or retained != new or old == new:
        raise ValueError("Expected a corrected simple_chain start after the rejected tool call")


def materialize(wanted, tokenizer, public_sha, first_index):
    examples = {"closing": [], "repair-only": []}
    with PUBLIC.open("rb") as source:
        if file_digest(source, "sha256").hexdigest() != public_sha:
            raise ValueError("Teacher training wire release changed")
    with gzip.open(PUBLIC, "rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if line_number not in wanted:
                continue
            ref, kind, split = wanted[line_number]
            if sha256(line).hexdigest() != ref["source_row_sha256"]:
                raise ValueError(f"Source row {line_number} checksum mismatch")
            example = json.loads(line)
            if (kind == "closing") != (not example["target"].get("tool_calls")):
                raise ValueError(f"Supervision kind disagrees with the raw response at {line_number}")
            if kind == "correction":
                check_repair(example)
            ids, labels, audit = supervise(tokenizer, example["messages"], example["tools"], example["target"])
            for k in ("prompt_ids_sha256", "target_ids_sha256"):
                if audit[k] != ref[k]:
                    raise ValueError(f"{k} mismatch at {line_number}")
            data = json_line({"input_ids": ids, "labels": labels})
            index = {k: ref[k] for k in ("session_id", "task_id", "logical_call_id", "source_training_line",
                                          "source_row_sha256", "prompt_ids_sha256", "target_ids_sha256")}
            for k in ("masked_prompt_tokens", "supervised_tokens", "supervised_reasoning_tokens"):
                index[k] = audit[k]
            overlap = first_index.get(line_number)
            digest = sha256(data).hexdigest()
            if overlap and overlap["train_row_sha256"] != digest:
                raise ValueError(f"First-pass row is inconsistent with source row {line_number}")
            index.update(kind=kind, train_row_sha256=digest, first_pass_row=overlap["row"] if overlap else None)
            examples[split].append((data, index))
    if sum(map(len, examples.values())) != len(wanted):
        raise ValueError("Missing selected teacher decisions")
    return examples


def write_pack(path, examples, first, sources, pack_name):
    path.mkdir()
    counts = Counter()
    counts_by_kind = Counter()
    scenes, tasks = set(), set()
    with (path / "train.jsonl.gz").open("xb") as raw, gzip.GzipFile(
        filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6
    ) as train, (path / "index.jsonl").open("xb") as index:
        for number, (data, ref) in enumerate(examples, 1):
            train.write(data)
            index.write(json_line({"row": number, **ref}))
            for k in ("masked_prompt_tokens", "supervised_tokens", "supervised_reasoning_tokens"):
                counts[k] += ref[k]
            counts["first_pass_overlap"] += ref["first_pass_row"] is not None
            counts_by_kind[ref["kind"]] += 1
            tasks.add((ref["session_id"], ref["task_id"]))
            scenes.add(ref["session_id"])
    counts.update(decisions=len(examples), tasks=len(tasks), scenes=len(scenes))
    files = {p.name: {"sha256": sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
             for p in sorted(path.iterdir())}
    manifest = {k: first[k] for k in ("schema", "split", "tokenizer", "policy", "teacher_release",
                                       "teacher_manifest_sha256", "scene_roles_sha256")}
    manifest.update(pack=pack_name, sources=sources, counts=dict(counts), kinds=dict(counts_by_kind),
                    teacher_path_limits={"episode": EPISODE_TOKEN_LIMIT, "context": MAX_CONTEXT,
                                         "generation": MAX_GENERATION}, files=files)
    (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def export(output, review_path=REVIEW):
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    first = json.loads(checked_bytes(FIRST / "manifest.json", FIRST_SHA))
    rows = checked_bytes(FIRST / "index.jsonl", first["files"]["index.jsonl"]["sha256"]).splitlines()
    first_index = {r["source_training_line"]: r for r in map(json.loads, rows)}
    review = review_path.read_bytes()
    projection, hashes = read_projected_turns()
    wanted = selection(json.loads(review)["tasks"], projection)
    examples = materialize(wanted, load_tokenizer(), hashes[PUBLIC.name], first_index)
    sources = {"review_sha256": sha256(review).hexdigest(), "cohort_sha256": COHORT_SHA,
               "first_manifest_sha256": FIRST_SHA, "teacher_training_public_sha256": hashes[PUBLIC.name],
               "projection_sha256": hashes["training-candidate-qwen-audit.jsonl"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        stage = Path(temp) / "release"
        write_pack(stage, examples["closing"], first, sources, "reviewed_closing_sequences")
        write_pack(stage / "repair-only", examples["repair-only"], first, sources, "independently_reviewed_repairs")
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory for the two reviewed training packs")
    parser.add_argument("--review", type=Path, default=REVIEW)
    args = parser.parse_args()
    export(args.output, args.review)
