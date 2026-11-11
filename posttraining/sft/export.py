"""Export the first reviewed FlockCue training split as unpadded causal LM examples."""
import argparse
from collections import Counter
import csv
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ..inference import MAX_CONTEXT, MAX_GENERATION
from .adapter import DEFAULT_CACHE, FILES, REVISION, load_tokenizer
from .supervision import supervise


DATASETS = Path(__file__).resolve().parents[2] / "datasets"
RELEASE = DATASETS / "teacher/2026-11-11-fold-a"
PUBLIC = "public/reviewed-compatible-public-turns.jsonl.gz"
PROJECTION = "audit/reviewed-qwen-projection.jsonl"
MANIFEST_SHA = "210dd19888a70c9eeaf9106a75b4bf198ba0215dee8b45fb5a39a4147189beac"
ROLES_SHA = "2f59f5e5ee3bd8cc79732c823a2c54d29490c1b08a2dedf9aac12200a629c393"


def checked_bytes(path, expected):
    data = path.read_bytes()
    if sha256(data).hexdigest() != expected:
        raise ValueError(f"Unexpected SHA-256 for {path}")
    return data


def verified_examples(projection, source, tokenizer):
    """Replay each native target and match its tokens to the published per-turn audit."""
    fields = {"masked_prompt_tokens": "prompt_tokens", "supervised_tokens": "target_tokens",
              "supervised_reasoning_tokens": "target_reasoning_tokens",
              "prompt_ids_sha256": "prompt_ids_sha256", "target_ids_sha256": "target_ids_sha256"}
    reviewed = (row for row in map(json.loads, projection) if row["status"] == "compatible")
    count = 0
    for count, (record, line) in enumerate(zip(reviewed, source, strict=True), 1):
        if count != record["reviewed_file_line"] or sha256(line).hexdigest() != record["source_row_sha256"]:
            raise ValueError(f"Reviewed row {count} differs from its registered identity")
        row = json.loads(line)
        ids, labels, audit = supervise(tokenizer, row["messages"], row["tools"], row["target"])
        for field, registered in fields.items():
            if audit[field] != record[registered]:
                raise ValueError(f"Reviewed row {count} differs in {field}")
        yield record, ids, labels, audit
    if count != 386:
        raise ValueError(f"Expected 386 reviewed decisions, got {count}")


def json_line(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def write_split(stage, release, tokenizer):
    release_manifest = json.loads(checked_bytes(release / "audit/manifest.json", MANIFEST_SHA))
    digests = {Path(entry["path"]).name: entry["sha256"] for entry in release_manifest["files"]}
    source_hashes = {name: digests[Path(name).name] for name in (PROJECTION, PUBLIC, "audit/episodes.jsonl")}
    sources = {name: checked_bytes(release / name, digest) for name, digest in source_hashes.items()}

    inventory = checked_bytes(DATASETS / "original-benchmark/scene-roles.csv", ROLES_SHA)
    train_scenes = {row["session_id"] for row in csv.DictReader(inventory.decode().splitlines())
                    if row["role"] == "baseline_train"}
    train_tasks = {(row["session_id"], row["task_id"])
                   for row in map(json.loads, sources["audit/episodes.jsonl"].splitlines())
                   if row["session_id"] in train_scenes and row["role"] == "train"
                   and row["semantic_status"] == "verified"}

    train_path, index_path = stage / "train.jsonl.gz", stage / "index.jsonl"
    keys = set()
    counts = Counter()
    with gzip.GzipFile(fileobj=io.BytesIO(sources[PUBLIC]), mode="rb") as source, \
         train_path.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb",
                                                     mtime=0, compresslevel=6) as train, \
         index_path.open("xb") as index:
        for record, ids, labels, audit in verified_examples(io.BytesIO(sources[PROJECTION]), source, tokenizer):
            key = (record["session_id"], record["task_id"], record["logical_call_id"])
            if key[:2] not in train_tasks or key in keys:
                raise ValueError(f"Duplicate or ineligible training decision {key}")
            keys.add(key)
            inputs = json_line({"input_ids": ids, "labels": labels})
            train.write(inputs)
            index.write(json_line({
                "row": record["reviewed_file_line"], "session_id": key[0], "task_id": key[1],
                "logical_call_id": key[2], "source_training_line": record["source_training_line"],
                "source_row_sha256": record["source_row_sha256"], "train_row_sha256": sha256(inputs).hexdigest(),
                "masked_prompt_tokens": audit["masked_prompt_tokens"], "supervised_tokens": audit["supervised_tokens"],
                "supervised_reasoning_tokens": audit["supervised_reasoning_tokens"],
            }))
            for field in ("masked_prompt_tokens", "supervised_tokens", "supervised_reasoning_tokens"):
                counts[field] += audit[field]
            counts["rows_without_full_generation_reserve"] += audit["masked_prompt_tokens"] > MAX_CONTEXT - MAX_GENERATION

    counts.update(decisions=len(keys), tasks=len({key[:2] for key in keys}), scenes=len({key[0] for key in keys}))
    files = {path.name: {"sha256": sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
             for path in (train_path, index_path)}
    manifest = {
        "schema": "flockcue-sft-causal-lm-v1", "split": "baseline_train",
        "teacher_release": release_manifest["release"], "teacher_manifest_sha256": MANIFEST_SHA,
        "source_files": source_hashes, "scene_roles_sha256": ROLES_SHA,
        "tokenizer": {"repository": "Qwen/Qwen3.5-2B", "revision": REVISION, "files": FILES},
        "policy": {"unit": "one reviewed decision", "reasoning": "retain_available_teacher_reasoning",
                   "ignore_index": -100, "target": "assistant suffix including EOS",
                   "padding": False, "truncation": False, "max_sequence_tokens": MAX_CONTEXT,
                   "max_target_tokens": MAX_GENERATION},
        "counts": dict(counts), "files": files,
    }
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def export(output, release=RELEASE, tokenizer_files=DEFAULT_CACHE):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Choose a new output directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        stage = Path(temp) / "release"
        stage.mkdir()
        manifest = write_split(stage, release, load_tokenizer(tokenizer_files))
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Output appeared during export: {output}")
        stage.rename(output)
    return manifest


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True, help="new directory for training rows, audit index and manifest")
    cli.add_argument("--release", type=Path, default=RELEASE, help="local copy of the pinned teacher release")
    cli.add_argument("--tokenizer", type=Path, default=DEFAULT_CACHE, help="pinned local tokenizer files")
    args = cli.parse_args()
    print(json.dumps(export(args.output, args.release, args.tokenizer)["counts"]))


if __name__ == "__main__":
    main()
