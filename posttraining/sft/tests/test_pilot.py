"""A pilot release must reproduce and load through the existing training entry point."""
import csv
import gzip
import json
from types import SimpleNamespace

import numpy as np

from posttraining.sft.adapter import REVISION
from posttraining.sft.pilot import build
from posttraining.sft.train import read_release
from posttraining.trajectories.rebuild import packed
from runtime.executor import EXPANSION_REGISTRATION, digest


def test_pilot_loader_and_rebuild(tmp_path):
    teacher = EXPANSION_REGISTRATION.parent
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    review = tmp_path / "review.csv"
    with (teacher / "decisions.jsonl").open() as decisions, \
         gzip.open(teacher / "reviewed-public-turns.jsonl.gz", "rb") as public, review.open("w") as stream:
        choices = (r for r in map(json.loads, decisions) if r["verdict"] == "retain")
        writer = csv.DictWriter(stream, ["scene_id", "task_id", "turn", "kind", "reason", "public_sha256"])
        writer.writeheader()
        for choice, line in zip(choices, public, strict=True):
            folder = inputs / choice["scene_id"]
            if folder.exists():
                continue
            folder.mkdir()
            assert digest(line) == choice["public_sha256"]
            writer.writerow({k: choice[k] for k in writer.fieldnames})
            (folder / "public.jsonl.gz").write_bytes(gzip.compress(line, mtime=0))
            entry = {**choice, "raw_candidate": True, "receipts_accepted": True,
                     "window": {"ids_sha256": choice["projection"]["prompt_ids_sha256"]}}
            (folder / "decisions.jsonl").write_bytes(packed(entry) + b"\n")
    manifests = []
    for name in ("first", "second"):
        output = tmp_path / name
        output.mkdir()
        build(inputs, review, output)
        manifests.append((output / "manifest.json").read_bytes())
        for arm in ("raw", "grounded"):
            samples, _, _ = read_release(SimpleNamespace(tensor=np.asarray, int32=np.int32),
                                         output / arm / "train.jsonl.gz", REVISION)
            assert len(samples) == 3
    assert manifests[0] == manifests[1]
