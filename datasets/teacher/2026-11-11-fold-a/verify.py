"""Verify the published A-fold records using the original release manifest."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


RELEASE = Path(__file__).resolve().parent
ROOT = RELEASE.parents[2]
MANIFEST_SHA256 = "210dd19888a70c9eeaf9106a75b4bf198ba0215dee8b45fb5a39a4147189beac"
FULL_PAYLOADS = {"training-public-turns.jsonl.gz", "validation-public-turns.jsonl.gz"}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(full):
    manifest = RELEASE / "audit/manifest.json"
    check(sha256(manifest) == MANIFEST_SHA256, "Release manifest changed")
    files = json.loads(manifest.read_bytes())["files"]
    checked = 0
    for record in files:
        archived = ROOT / record["path"]
        if not full and archived.name in FULL_PAYLOADS:
            continue
        check(archived.is_file(), f"Missing archived file: {archived}")
        check(archived.stat().st_size == record["bytes"] and sha256(archived) == record["sha256"],
              f"Size or SHA-256 differs: {archived}")
        checked += 1

    with (RELEASE / "audit/reviewed-qwen-projection.jsonl").open() as stream:
        selected = [row for row in map(json.loads, stream) if row["status"] == "compatible"]
    with (RELEASE / "audit/episodes.jsonl").open() as stream:
        training = {(row["session_id"], row["task_id"]) for row in map(json.loads, stream)
                    if row["role"] == "train" and row["semantic_status"] == "verified"}
    tasks = set()
    package = RELEASE / "public/reviewed-compatible-public-turns.jsonl.gz"
    with gzip.open(package, "rb") as stream:
        for number, (line, row) in enumerate(zip(stream, selected, strict=True), start=1):
            task = row["session_id"], row["task_id"]
            check(task in training, f"Unreviewed or held-out task at line {number}")
            check(row["reviewed_file_line"] == number and hashlib.sha256(line).hexdigest() == row["source_row_sha256"],
                  f"Public row differs from audited source at line {number}")
            tasks.add(task)
    check(len(tasks) == 110 and len(selected) == 386, "Reviewed training coverage differs")
    print(f"Verified {checked}/{len(files)} sealed files, {len(tasks)} training tasks and {len(selected)} reviewed turns.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Also require the two local full payloads.")
    verify(parser.parse_args().full)
