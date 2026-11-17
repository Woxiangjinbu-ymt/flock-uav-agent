"""Verify the October first-pass results against the original benchmark and core 600."""
import argparse
from collections import Counter
import csv
import gzip
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
from zipfile import ZipFile


PACKAGE = Path(__file__).resolve().parent
DATASETS = PACKAGE.parent.parent
MANIFEST_SHA256 = "979c809976460cb6c0bdf41cfe05e43df63e76bfda04d4da4d7eb2db04fc0b0e"


def checked_bytes(path, expected):
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return raw


def reader(raw):
    return csv.DictReader(StringIO(raw.decode("utf-8-sig"), newline=""))


def verify_original_export(source, expected):
    """Reproduce the local CSV in memory, preserving all original fields and row order."""
    packed = checked_bytes(PACKAGE / source["bundled_file"], source["bundled_sha256"])
    published = gzip.decompress(packed)
    if sha256(published).hexdigest() != source["sha256"]:
        raise ValueError("Bundled source differs from the published multi-cohort CSV")
    records = reader(published)
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=records.fieldnames)
    writer.writeheader()
    count = 0
    for row in records:
        count += 1
        if all(row[key] == value for key, value in source["selection"].items()):
            writer.writerow(row)
    if count != source["rows"] or buffer.getvalue().encode("utf-8-sig") != expected:
        raise ValueError("First-pass CSV differs from the original report selection")


def verify(original_csv=None):
    manifest = json.loads(checked_bytes(PACKAGE / "manifest.json", MANIFEST_SHA256))
    results = manifest["results"]
    raw = checked_bytes(PACKAGE / results["file"], results["sha256"])
    rows = list(reader(raw))
    source = manifest["source"]
    verify_original_export(source, raw)
    if original_csv is not None:
        checked_bytes(original_csv, source["sha256"])

    benchmark = manifest["benchmark"]
    scene_ids = set()
    ordered_tasks = []
    with ZipFile(BytesIO(checked_bytes(DATASETS / benchmark["file"], benchmark["sha256"]))) as archive:
        for filename in sorted(name for name in archive.namelist() if name.endswith(".json")):
            scene = json.loads(archive.read(filename))
            scene_ids.add(scene["id"])
            ordered_tasks.extend((scene["id"], task["id"], i, task["name"]) for i, task in enumerate(scene["tasks"], 1))
    if ordered_tasks != [(row["session_id"], row["task_id"], int(row["task_index"]), row["name"]) for row in rows]:
        raise ValueError("Task identity, name or order differs from the benchmark")
    by_task = {(r["session_id"], r["task_id"]): r for r in rows}
    if len(by_task) != len(rows) or any(r[key] != value for key, value in source["selection"].items() for r in rows):
        raise ValueError("Duplicate task or unexpected model/cohort")
    totals = {
        "scenes": len(scene_ids),
        "planned_tasks": len(rows),
        "planned_checks": sum(int(r["expected_checks"]) for r in rows),
        "scored_tasks": sum(r["original_status"] == "scored" for r in rows),
        "checker_passed": sum(r["original_passed"] == "True" for r in rows),
        "task_passed": sum(r["task_pass"] == "True" for r in rows),
        "strict_deployment_passed": sum(r["deployment_pass"] == "True" for r in rows),
        "accounting_reasons": dict(Counter(r["accounting_reason"] for r in rows)),
    }
    if totals != manifest["totals"]:
        raise ValueError("First-pass score or accounting totals differ")

    core = manifest["core_results"]
    shared = list(reader(checked_bytes(DATASETS / core["file"], core["sha256"])))
    pairs = {
        "index": "task_index", "historical_pass": "task_pass", "historical_checker_passed": "original_passed",
        "historical_accounting_reason": "accounting_reason", "historical_timed_out": "agent_timed_out",
        "historical_evidence": "evidence_dir",
    }
    for row in shared:
        key = row["session_id"], row["task_id"]
        original = by_task.get(key)
        if original is None or any(row[k] != original[v] for k, v in pairs.items()):
            raise ValueError(f"Historical core result differs: {key}")
    if (len({(r["session_id"], r["task_id"]) for r in shared}) != len(shared)
            or len(shared) != core["shared_tasks"]
            or sum(r["historical_pass"] == "True" for r in shared) != core["historical_passed"]):
        raise ValueError("Historical core selection or score differs")
    return {**totals, "shared_tasks": len(shared), "core_historical_passed": core["historical_passed"],
            "published_rows": source["rows"], "external_csv_verified": original_csv is not None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-csv", type=Path, help="Also authenticate an external copy of the October CSV")
    print(json.dumps(verify(parser.parse_args().original_csv), indent=2))
