"""Check the frozen 600 tasks, optional original archive and historical results."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import zipfile


PACKAGE = Path(__file__).resolve().parent
SELECTION_SHA = "c3d60aadde603a65c7481495db0db5274d7512f4f823a6e5cb356814a6241e06"
SOURCE_SHA = "b5040097d2bfdd44600f3bf486fdb43ee3eb1247fec9c67900b5fcda6feb94a3"
HISTORY_SHA = "69c5d3b3451f1029c52bcd8e5ccefacfb955aaba1d80d056b119aa95be28f2c3"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_file(path, expected):
    raw = path.read_bytes()
    require(digest(raw) == expected, f"Wrong SHA-256 for {path}")
    return raw


def check_source(path, strata):
    checked_file(path, SOURCE_SHA)
    populations = defaultdict(list)
    with zipfile.ZipFile(path) as source:
        for name in source.namelist():
            if name.endswith(".json"):
                data = json.loads(source.read(name))
                difficulty = data["name"].rsplit(" ", 2)[1]
                populations[data["task_type"], difficulty].append(data["id"])
                require(len(data["tasks"]) == 20, f"Wrong task count in {name}")
        require(len(populations) == 15, "Wrong number of source strata")
        for group in strata:
            ids = populations[group["family"], group["difficulty"]]
            ranked = sorted(ids, key=lambda sid: digest(f"flockcue-original-benchmark-v1/{sid}".encode()))
            require(len(ranked) == 5 and ranked == group["full_five_session_order_for_nested_expansion"],
                    "Source stratum or selection order differs")
            require(ranked[:2] == [s["session_id"] for s in group["sessions_selected"]],
                    "Selected sessions differ from independent ranking")
            for entry in group["sessions_selected"]:
                require(digest(source.read(entry["filename"])) == entry["source_sha256"],
                        f"Source scene differs: {entry['filename']}")


def check_package():
    manifest = json.loads(checked_file(PACKAGE / "manifest.json", (PACKAGE / "manifest.sha256").read_text().strip()))
    require(manifest["status"] == "frozen" and manifest["source"]["sha256"] == SOURCE_SHA, "Wrong frozen identity")
    require(manifest["selection"]["sha256"] == SELECTION_SHA, "Manifest is detached from confirmed selection")
    candidate = json.loads(checked_file(PACKAGE / "candidate-600.json", SELECTION_SHA))
    require(candidate["benchmark_sha256"] == SOURCE_SHA, "Selection is detached from original archive")
    strata = candidate["strata"]
    sessions = [entry for g in strata for entry in g["sessions_selected"]]
    require(len(strata) == 15 and all(len(g["sessions_selected"]) == 2 for g in strata), "Expected 15 pairs")
    require(len({s["session_id"] for s in sessions}) == 30, "Duplicate session")

    pairs = set()
    path = PACKAGE / "sessions-600.zip"
    checked_file(path, manifest["sessions_archive"]["sha256"])
    with zipfile.ZipFile(path) as bundle:
        require(bundle.namelist() == [s["filename"] for s in sessions], "Unexpected archive members or order")
        for entry in sessions:
            raw = bundle.read(entry["filename"])
            require(digest(raw) == entry["source_sha256"], f"Changed scene: {entry['filename']}")
            scene = json.loads(raw)
            ids = [t["id"] for t in scene["tasks"]]
            require(scene["id"] == entry["session_id"] and len(ids) == 20 and ids == entry["task_ids_in_original_order"],
                    f"Changed scene identity or task order: {entry['filename']}")
            pairs.update((scene["id"], task) for task in ids)
    require(len(pairs) == 600, "Duplicate task identities")

    folds = json.loads((PACKAGE / "proposed-folds.json").read_bytes())
    expected = [{"family": g["family"], "difficulty": g["difficulty"],
                 "A": g["sessions_selected"][0]["session_id"], "B": g["sessions_selected"][1]["session_id"]}
                for g in strata]
    require(folds["status"] == "proposed" and folds["selection_sha256"] == SELECTION_SHA and
            folds["strata"] == expected, "Proposed whole-session folds differ from the confirmed selection")
    return strata, pairs


def check_historical(path, pairs, history):
    # The November paired results contain the October reference for just these 600 tasks.
    # The full October CSV can also be supplied from the external run archive.
    paired_sha = "838e7d82b934a63893d4c1334b53274d2257cfceca39e8adc02a529c2d3264da"
    if digest(path.read_bytes()) == paired_sha:
        with path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        require(len(rows) == len(pairs) and {(r["session_id"], r["task_id"]) for r in rows} == pairs,
                "Paired rows differ from the frozen benchmark")
        require(sum(r["historical_pass"] == "True" for r in rows) == history["task_pass"] and
                Counter(r["historical_accounting_reason"] for r in rows) == history["accounting_reasons"],
                "Historical scores or failure accounting differ from the paired release")
        return "published_600_pairs"
    checked_file(path, history["historic_task_file_sha256"])
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = [row for row in csv.DictReader(file) if row["model"] == history["model"]
                and row["cohort"] == history["cohort"] and (row["session_id"], row["task_id"]) in pairs]
    require(len(rows) == 600 and {(r["session_id"], r["task_id"]) for r in rows} == pairs,
            "Historical records do not match all 600 tasks")
    passing = sum(row["task_pass"] == "True" for row in rows)
    deployed = sum(row["deployment_pass"] == "True" for row in rows)
    require(passing == history["task_pass"] == 506 and deployed == history["deployment_pass"] == 506 and
            Counter(r["accounting_reason"] for r in rows) == history["accounting_reasons"],
            "Historical score or failure accounting differs")
    return "original_october_csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--benchmark", type=Path, default=PACKAGE.parent / "original-benchmark/benchmark.zip")
    group.add_argument("--standalone", action="store_true", help="Verify the portable package without the full benchmark")
    parser.add_argument("--historical-csv", nargs="?", type=Path,
                        const=PACKAGE.parent / "results/benchmark-600/tasks.csv",
                        help="Recount October scores using the 600 paired rows, or supply the original October CSV")
    args = parser.parse_args()
    strata, pairs = check_package()
    if not args.standalone:
        check_source(args.benchmark, strata)
    history = json.loads(checked_file(PACKAGE / "historical-reference-600.json", HISTORY_SHA))
    require(history["selection_sha256"] == SELECTION_SHA, "Historical reference belongs to another selection")
    source = check_historical(args.historical_csv, pairs, history) if args.historical_csv else None
    print(json.dumps({"dataset_id": "flockcue-benchmark-600-v1", "strata": len(strata), "sessions": 30,
                      "tasks": len(pairs), "proposed_fold_sizes": [300, 300],
                      "full_source_verified": not args.standalone,
                      "historical_score_recomputed": bool(source), "historical_score_source": source}))


if __name__ == "__main__":
    main()
