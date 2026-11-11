"""Rebuild or verify the outcome-blind panel of ten complete original sessions."""

import argparse
import csv
import hashlib
import io
import itertools
import json
import zipfile
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
POOL = ROOT / "experiments/flockcue-benchmark-600-v1"
PARENT_PLAN = ROOT / "experiments/plans/flockcue-600-first-pass.json"
NAME = "agent4drone-matched200-v1"
PLAN_PATH = ROOT / "experiments/plans" / (NAME + ".json")
PARENT_SHA = "c3d60aadde603a65c7481495db0db5274d7512f4f823a6e5cb356814a6241e06"
PARENT_PLAN_SHA = "6589d8e1b4be6664cb3d651f09fe2cc54e41ffca7a361a6dae675ee455ec8823"
DIFFICULTIES = ("Easy", "Intermediate", "Moderate", "Hard", "Extreme")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def tie_digest(stage, choice):
    encoded = json.dumps(choice, ensure_ascii=True, separators=(",", ":"))
    return digest(f"{NAME}\n{PARENT_SHA}\n{stage}\n{encoded}".encode())


def read_inputs():
    files = [POOL / "candidate-600.json", POOL / "proposed-folds.json",
             POOL / "sessions-600.zip", PARENT_PLAN]
    hashes = {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in files}
    if hashes[files[0].relative_to(ROOT).as_posix()] != PARENT_SHA or hashes[PARENT_PLAN.relative_to(ROOT).as_posix()] != PARENT_PLAN_SHA:
        raise ValueError("Frozen parent identity changed")
    candidate = json.loads(files[0].read_bytes())
    folds = json.loads(files[1].read_bytes())
    plan = json.loads(PARENT_PLAN.read_bytes())
    if folds["selection_sha256"] != PARENT_SHA:
        raise ValueError("Fold table belongs to a different benchmark")
    cell_folds = {(c["family"], c["difficulty"]): (c["A"], c["B"]) for c in folds["strata"]}
    parent_scenes = {s["id"]: s for s in plan["sessions"]}
    metadata = {}
    with zipfile.ZipFile(files[2]) as archive:
        for cell in candidate["strata"]:
            key = cell["family"], cell["difficulty"]
            given = cell["sessions_selected"]
            if tuple(s["session_id"] for s in given) != cell_folds[key]:
                raise ValueError(f"Original selection/fold disagreement at {key}")
            for fold, s in zip(("A", "B"), given, strict=True):
                sid = s["session_id"]
                raw = archive.read(s["filename"])
                src = json.loads(raw)
                base = parent_scenes[sid]
                task_order = s["task_ids_in_original_order"]
                if (src["id"] != sid or digest(raw) != s["source_sha256"] or
                        base["sha256"] != s["source_sha256"] or base["file"] != s["filename"] or
                        base["family"] != key[0] or len(task_order) != 20 or
                        [t["id"] for t in src["tasks"]] != task_order or
                        [t["id"] for t in base["tasks"]] != task_order):
                    raise ValueError(f"Original scene or task identity mismatch for {sid}")
                metadata[sid] = {"family": key[0], "difficulty": key[1], "fold": fold,
                                 "fleet_size": len(src["drones"]), "target_count": len(src["targets"])}
            a, b = (metadata[s["session_id"]] for s in given)
            if (a["fleet_size"], a["target_count"]) != (b["fleet_size"], b["target_count"]):
                raise ValueError(f"Structural dimensions vary within {key}")
    if len(cell_folds) != 15 or len(metadata) != 30 or set(metadata) != set(parent_scenes):
        raise ValueError("Incomplete frozen parent roster")
    return plan, metadata, cell_folds, hashes


def histograms(scenes):
    return {field: dict(sorted(Counter(s[field] for s in scenes).items()))
            for field in ("family", "difficulty", "fold", "fleet_size", "target_count")}


def choose(metadata, cell_folds):
    families = sorted({s["family"] for s in metadata.values()})
    full = histograms(metadata.values())
    layouts = []
    # At each difficulty choose two families by naming the one to omit.
    for omitted in itertools.product(families, repeat=len(DIFFICULTIES)):
        if sorted(Counter(omitted).values()) != [1, 2, 2]:
            continue
        cells = [(fam, diff) for diff, skip in zip(DIFFICULTIES, omitted)
                 for fam in families if fam != skip]
        example = [metadata[cell_folds[cell][0]] for cell in cells]
        counts = histograms(example)
        error = sum(abs(3 * counts[key].get(size, 0) - total)
                    for key in ("fleet_size", "target_count") for size, total in full[key].items())
        score = (-len(counts["fleet_size"]), -len(counts["target_count"]), error)
        layouts.append((score, tie_digest("cells", cells), cells))
    score, layout_hash, cells = min(layouts)
    # Cells are in difficulty order, with family names ascending within each pair.
    options = []
    for first_folds in itertools.product(("A", "B"), repeat=len(DIFFICULTIES)):
        chosen = []
        for i, first in enumerate(first_folds):
            flags = (0, 1) if first == "A" else (1, 0)
            chosen.extend(cell_folds[cell][flag] for cell, flag in zip(cells[2*i:2*i+2], flags))
        counts = Counter((metadata[s]["family"], metadata[s]["fold"]) for s in chosen)
        if all(abs(counts[f, "A"] - counts[f, "B"]) <= 1 for f in families):
            options.append((tie_digest("scenes", chosen), chosen))
    scene_hash, chosen = min(options)
    selection_info = {"eligible_layouts": len(layouts), "layouts_tied_on_structural_criteria":
                      sum(k == score for k, _, _ in layouts), "eligible_fold_assignments": len(options),
                      "selected_fleet_sizes_count": -score[0], "selected_target_counts_count": -score[1],
                      "integer_histogram_error": score[2], "winning_cell_hash": layout_hash,
                      "winning_scene_hash": scene_hash}
    return set(chosen), selection_info


def build():
    parent, metadata, cell_folds, hashes = read_inputs()
    chosen, selection_info = choose(metadata, cell_folds)
    scenes = [s for s in parent["sessions"] if s["id"] in chosen]
    slots = io.StringIO(newline="")
    writer = csv.writer(slots, lineterminator="\n")
    writer.writerow(["session_id", "index", "task_id", "family", "scene_difficulty",
                     "fold", "fleet_size", "target_count", "task_difficulty", "name", "expected_checks"])
    roster = []
    count = checks = 0
    for s in scenes:
        m = metadata[s["id"]]
        roster.append({"session_id": s["id"], **m, "scene_file": s["file"], "scene_sha256": s["sha256"]})
        for i, t in enumerate(s["tasks"], 1):
            leaves = len(t["leaves"])
            count += 1
            checks += leaves
            writer.writerow([s["id"], i, t["id"], m["family"], m["difficulty"], m["fold"],
                             m["fleet_size"], m["target_count"], t["difficulty"], t["name"], leaves])
    if len(scenes) != 10 or count != 200:
        raise ValueError("Incomplete matched panel")
    plan = {**parent, "name": NAME, "expected_tasks": count, "expected_checks": checks, "sessions": scenes}
    plan_data, csv_data = json_bytes(plan), slots.getvalue().encode()
    full, sub = histograms(metadata.values()), histograms(roster)
    missing = [{"family": f, "difficulty": d} for (f, d), ids in cell_folds.items() if chosen.isdisjoint(ids)]
    data = {"name": NAME, "status": "frozen", "seed": NAME, "parent_selection_sha256": PARENT_SHA,
            "source_sha256": hashes, "plan_sha256": digest(plan_data), "tasks_csv_sha256": digest(csv_data),
            "rule": [
                "Select 10 whole original sessions. At each of the five ordered difficulty levels select two distinct families. Family counts are 3, 3, 4 in any order.",
                "Among these layouts maximize the number of distinct fleet sizes, then distinct target counts. Minimize the sum of abs(3 * selected_scene_count - full_scene_count) over bins of both marginal histograms.",
                "Break remaining layout ties by minimum SHA-256 over seed, frozen parent selection SHA-256, 'cells', compact JSON pairs [family,difficulty] ordered by difficulty then ascending family. Join fields with a single LF and encode UTF-8.",
                "Assign one A and one B per difficulty with a fold imbalance of at most one in each family. Break ties with the same hash procedure using 'scenes' and the chosen scene IDs in cell order.",
                "Write original parent-plan session and task order and complete original check trees. No scores, trajectories, costs or timings enter selection."
            ], "design": "Purposive metadata-balanced panel. Primary paired results describe these 200 tasks. The five omitted family/difficulty combinations have no observations.",
            "selection_diagnostics": selection_info, "full_session_histograms": full, "selected_session_histograms": sub,
            "omitted_cells": missing, "selected_scene_ids": [s["id"] for s in scenes],
            "sessions": roster, "expected_tasks": count, "expected_checks": checks}
    return {PLAN_PATH: plan_data, HERE / "selection.json": json_bytes(data), HERE / "tasks.csv": csv_data}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write regenerated artifacts. The default verifies them.")
    args = parser.parse_args()
    for path, data in build().items():
        if args.write:
            path.write_bytes(data)
        elif path.read_bytes() != data:
            raise ValueError(f"Frozen artifact changed: {path}")
        print(digest(data), path.relative_to(ROOT))


if __name__ == "__main__":
    main()
