"""Build the formal SFT mixture from frozen, reviewed A-fold training releases."""

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
from .export import DATASETS, PUBLIC, RELEASE, ROLES_SHA, checked_bytes, json_line
from .reviewed_decisions import FIRST, FIRST_SHA, REVIEW


CLOSING = DATASETS / "sft/qwen35-2b-closing-repair"
PACKS = {
    "first": (FIRST, FIRST_SHA),
    "closing": (CLOSING, "9ebd23e2a89205fbc8be3653efee690f734fc901131a734258d16d6248978c21"),
    "repair": (CLOSING / "repair-only", "da659cad47218ac36b69c715218aae6a1a47284546b07f31b86fcd86e9bef144"),
}
FIRST_CLOSINGS = RELEASE / "audit/first-pass-closing-review.json"
TOKEN_FIELDS = ("masked_prompt_tokens", "supervised_tokens", "supervised_reasoning_tokens")


def load_rows(root, manifest):
    data = checked_bytes(root / "train.jsonl.gz", manifest["files"]["train.jsonl.gz"]["sha256"])
    index = checked_bytes(root / "index.jsonl", manifest["files"]["index.jsonl"]["sha256"])
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        for number, (line, ref) in enumerate(zip(stream, map(json.loads, index.splitlines()), strict=True), 1):
            if ref["row"] != number or sha256(line).hexdigest() != ref["train_row_sha256"]:
                raise ValueError(f"Training row differs from its index in {root} at {number}")
            yield line, ref


def first_closing_verdicts(first_manifest):
    """Bind each review to the actual text-only wire turn and frozen training row."""
    review = json.loads(FIRST_CLOSINGS.read_bytes())["rows"]
    by_number = {r["row"]: r for r in review}
    public = checked_bytes(RELEASE / PUBLIC, first_manifest["source_files"][PUBLIC])
    text_rows = set()
    with gzip.GzipFile(fileobj=io.BytesIO(public)) as stream:
        for number, line in enumerate(stream, 1):
            if not json.loads(line)["target"].get("tool_calls"):
                if number not in by_number or sha256(line).hexdigest() != by_number[number]["source_row_sha256"]:
                    raise ValueError(f"Missing closing review for original wire turn {number}")
                text_rows.add(number)
    if set(by_number) != text_rows or len(by_number) != len(review):
        raise ValueError("Every review must identify one distinct text-only target")
    return by_number


def summarize(entries):
    refs = [entry["ref"] for entry in entries]
    result = {field: sum(r[field] for r in refs) for field in TOKEN_FIELDS}
    result.update(decisions=len(refs), tasks=len({(r["session_id"], r["task_id"]) for r in refs}),
                  scenes=len({r["session_id"] for r in refs}), kinds=dict(Counter(r["kind"] for r in refs)))
    return result


def write_mix(stage):
    manifests = {name: json.loads(checked_bytes(root / "manifest.json", seal))
                 for name, (root, seal) in PACKS.items()}
    first = manifests["first"]
    roles = checked_bytes(DATASETS / "original-benchmark/scene-roles.csv", ROLES_SHA)
    train_scenes = {r["session_id"] for r in csv.DictReader(roles.decode().splitlines()) if r["role"] == "baseline_train"}
    old_verdicts = first_closing_verdicts(first)
    # The later re-review applies only to these exact original final turns. A resampled
    # answer to the same prompt has its own response, review and row checksum.
    review_seal = manifests["closing"]["sources"]["review_sha256"]
    reviews = json.loads(checked_bytes(REVIEW, review_seal))["tasks"]
    rejected_originals = {(r["scene_id"], r["task_id"], f"model-{r['terminal_turn']}")
                          for r in reviews if r["verdict"] == "recollect"}
    entries = {}
    excluded = []
    rejected_overlaps = []
    for name, (directory, _) in PACKS.items():
        manifest = manifests[name]
        if any(manifest[key] != first[key] for key in ("schema", "split", "tokenizer", "teacher_manifest_sha256")) or manifest["scene_roles_sha256"] != ROLES_SHA:
            raise ValueError(f"Incompatible training source {directory}")
        for line, ref in load_rows(directory, manifest):
            scene, task, turn = ref["session_id"], ref["task_id"], ref["logical_call_id"]
            if scene not in train_scenes:
                raise ValueError(f"Ineligible training scene {scene}")
            row_hash = ref["train_row_sha256"]
            orig = old_verdicts.get(ref["row"]) if name == "first" else None
            if orig and (orig["train_row_sha256"] != row_hash or orig["verdict"] not in {"retain", "exclude"}):
                raise ValueError("Closing review differs from the first-pass training release")
            reasons = []
            if ref["masked_prompt_tokens"] + MAX_GENERATION > MAX_CONTEXT:
                reasons.append("prompt_over_full_generation_reserve")
            if orig and orig["verdict"] == "exclude":
                reasons.append("first_pass_closing_review")
            if name == "first" and (scene, task, turn) in rejected_originals:
                rejected_overlaps.append(row_hash)
                reasons.append("original_terminal_rejected_on_review")
            if reasons:
                excluded.append({"pack": name, "source_row": ref["row"], "reasons": reasons, **{
                    k: ref[k] for k in ("session_id", "task_id", "logical_call_id", "train_row_sha256",
                                         "masked_prompt_tokens")}})
                continue
            entry = entries.setdefault(row_hash, {"line": line, "ref": dict(ref), "sources": {}})
            entry["sources"][name] = ref["row"]
            if name != "first":
                entry["ref"].update(ref)
            entry["ref"]["kind"] = ref.get("kind") or ("closing" if orig else "tool_decision")

    unique = list(entries.values())
    replays = [entry for entry in unique if "closing" in entry["sources"]]
    exposures = unique + replays + replays
    times = Counter()
    train_path, index_path = stage / "train.jsonl.gz", stage / "index.jsonl"
    with train_path.open("xb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0,
                                                        compresslevel=6) as train, index_path.open("xb") as index:
        for number, entry in enumerate(exposures, 1):
            ref = entry["ref"]
            row_hash = ref["train_row_sha256"]
            times[row_hash] += 1
            train.write(entry["line"])
            index.write(json_line({**ref, "row": number, "source_pack_rows": entry["sources"],
                                   "exposure_in_epoch": times[row_hash]}))

    files = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p.read_bytes()).hexdigest()}
             for p in (train_path, index_path)}
    output = {key: first[key] for key in ("schema", "split", "tokenizer", "scene_roles_sha256", "teacher_release",
                                          "teacher_manifest_sha256", "policy")}
    output.update(pack="formal_sft_mix", sources={
        name: {"directory": str(directory.relative_to(DATASETS)), "manifest_sha256": digest}
        for name, (directory, digest) in PACKS.items()
    }, reviews={str(p.relative_to(DATASETS)): sha256(p.read_bytes()).hexdigest() for p in (FIRST_CLOSINGS, REVIEW)},
                  mixture={"deduplication": "SHA-256 of the actual training line including newline",
                           "closing_pack_exposures_per_epoch": 3, "other_exposures_per_epoch": 1,
                           "full_generation_reserve": MAX_GENERATION, "max_context": MAX_CONTEXT,
                           "reviewed_original_terminal_overlap_rejections": rejected_overlaps},
                  excluded=excluded, scene_ids=sorted({r["ref"]["session_id"] for r in unique}),
                  unique_counts=summarize(unique), counts=summarize(exposures), files=files)
    (stage / "manifest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return output


def build(output):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp) / "release"
        stage.mkdir()
        manifest = write_mix(stage)
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)
    return manifest


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(build(args.output)["counts"]))
