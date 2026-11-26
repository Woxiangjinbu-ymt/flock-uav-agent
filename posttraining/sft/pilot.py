"""Export matched-source pilot data using an explicit grounded decision review."""
import argparse
from contextlib import ExitStack
import csv
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from posttraining.trajectories.rebuild import file_digest, packed
from runtime.executor import PILOT_REGISTRATION, canonical_scenes, digest, read_json, save_json
from .adapter import load_tokenizer
from .supervision import supervise


def build(inputs, review, output):
    registration = read_json(PILOT_REGISTRATION)
    scenes = registration["pilot_scenes"]
    task_ids = {s["id"]: {t["id"] for t in s["tasks"]} for _, s in canonical_scenes() if s["id"] in scenes}
    if {p.name for p in inputs.iterdir() if p.is_dir()} != set(scenes):
        raise ValueError("Inputs must contain exactly the three registered training scenes")
    with review.open(newline="") as stream:
        picks = {}
        for row in csv.DictReader(stream):
            key = (row["scene_id"], row["task_id"], int(row["turn"]))
            if key in picks or key[0] not in scenes or not row["reason"].strip():
                raise ValueError(f"Invalid review selection: {key}")
            picks[key] = row
    tokenizer = load_tokenizer()
    counts = {"raw": 0, "grounded": 0}
    with ExitStack() as stack:
        writers = {}
        for arm in counts:
            folder = output / arm
            folder.mkdir()
            raw = stack.enter_context((folder / "train.jsonl.gz").open("xb"))
            train = stack.enter_context(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0))
            writers[arm] = train, stack.enter_context((folder / "index.jsonl").open("xb"))
        for sid in scenes:
            folder = inputs / sid
            with gzip.open(folder / "public.jsonl.gz", "rb") as public, (folder / "decisions.jsonl").open("rb") as decisions:
                for entry in map(json.loads, decisions):
                    if "public_sha256" not in entry:
                        continue
                    line = next(public)
                    if (digest(line) != entry["public_sha256"] or entry["scene_id"] != sid
                            or entry["task_id"] not in task_ids[sid]):
                        raise ValueError("Public turn and scene index disagree")
                    if not entry["raw_candidate"]:
                        continue
                    key = (sid, entry["task_id"], entry["turn"])
                    pick = picks.pop(key, None)
                    wire = json.loads(line)
                    ids, labels, audit = supervise(tokenizer, wire["messages"], wire["tools"], wire["target"])
                    if audit["prompt_ids_sha256"] != entry["window"]["ids_sha256"]:
                        raise ValueError(f"Runtime prefix differs at {key}")
                    sample = packed({"input_ids": ids, "labels": labels}) + b"\n"
                    origin = {"scene_id": sid, "task_id": key[1], "turn": key[2],
                              "source_sha256": entry["public_sha256"], "train_sha256": digest(sample),
                              "prompt_tokens": audit["masked_prompt_tokens"], "target_tokens": audit["supervised_tokens"]}
                    writers["raw"][0].write(sample)
                    writers["raw"][1].write(packed(origin) + b"\n")
                    counts["raw"] += 1
                    if pick:
                        if not entry["receipts_accepted"] or pick["public_sha256"] != entry["public_sha256"]:
                            raise ValueError(f"Rejected or mismatched reviewed decision at {key}")
                        writers["grounded"][0].write(sample)
                        writers["grounded"][1].write(packed({**origin, "kind": pick["kind"]}) + b"\n")
                        counts["grounded"] += 1
                if next(public, None) is not None:
                    raise ValueError(f"Unindexed public turns in {sid}")
    if picks:
        raise ValueError(f"Review contains unavailable training decisions: {list(picks)}")
    save_json(output / "manifest.json", {
        "pilot_registration_sha256": file_digest(PILOT_REGISTRATION), "review_sha256": file_digest(review),
        "rows": counts, "files": {p.relative_to(output).as_posix(): file_digest(p)
                                  for p in sorted(output.glob("*/*"))},
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as temp:
        stage = Path(temp) / "release"
        stage.mkdir()
        build(args.inputs, args.review, stage)
        stage.rename(args.output)
    print(json.dumps(read_json(args.output / "manifest.json")["rows"]))


if __name__ == "__main__":
    main()
