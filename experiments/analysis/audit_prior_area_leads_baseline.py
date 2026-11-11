#!/usr/bin/env python3
"""Scene-disjoint public-blackboard lead prevalence in the B0 full benchmark.

This is a domain opportunity audit, not an R2 trigger replay or treatment test.
Only selected, verified B0 scene generations are read. No SYSTEM scene/target
snapshot or checker state is used to decide whether a prior lead exists.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from task_evidence import references

FULL = ROOT / "docs/results/full-round1"
R2_PLAN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403/plan.json"
OUTPUT = ROOT / "docs/audit/observation-worker-next-step-20261009/baseline-lead-prevalence.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prior_leads(blackboard, target_names):
    targets = blackboard.get("targets", {})
    if not isinstance(targets, dict):
        return []
    leads = []
    for name in target_names:
        matches = [entry for entry in targets.values() if entry.get("name") == name]
        if len(matches) != 1:
            continue
        target = matches[0]
        facts = target.get("facts", {})
        position = facts.get("position")
        kind = target.get("entity_type")
        if (not isinstance(position, (list, tuple)) or len(position) < 2
                or kind not in {"circle", "polygon"}):
            continue
        has_shape = (kind == "circle" and isinstance(facts.get("radius"), (int, float))
                     or kind == "polygon" and isinstance(facts.get("vertices"), list)
                     and len(facts["vertices"]) >= 3)
        if has_shape:
            leads.append({"name": name, "id": target["id"], "type": kind})
    return leads


def main():
    full = read(FULL / "results.json")
    assert full["status"] == "complete_verified"
    attempt = ROOT / full["attempt"]
    plan = read(attempt / "plan.json")
    held_out = {session["id"] for session in read(R2_PLAN)["sessions"]}
    plan_by_id = {session["id"]: session for session in plan["sessions"]}
    with (FULL / "tasks.csv").open(encoding="utf-8-sig", newline="") as file:
        outcomes = {(row["session_id"], row["task_id"]): row for row in csv.DictReader(file)}
    records = []
    quality = Counter()
    for scene in full["scenes"]:
        session_id = scene["session_id"]
        if session_id in held_out:
            continue
        tasks = plan_by_id[session_id]["tasks"]
        scene_dir = attempt / scene["directory"]
        prior_board = {"targets": {}}
        for task in tasks:
            task_id = task["id"]
            path = scene_dir / f"{task_id}.agent-state.json"
            if not path.exists():
                quality["missing_agent_state"] += 1
                continue
            state = read(path)
            job = state.get("job", {})
            command = job.get("command", "")
            if not command:
                quality["missing_command"] += 1
                continue
            refs = [item for item in references(command)
                    if item["relation"] == "visit" and item.get("required_actor")
                    and item["target_name"].startswith(("Circle Target ", "Polygon Target "))]
            names = sorted({item["target_name"] for item in refs})
            leads = prior_leads(prior_board, names)
            outcome = outcomes.get((session_id, task_id))
            if outcome is None:
                quality["missing_outcome"] += 1
                continue
            if names:
                after_board = state.get("blackboards", {}).get(session_id, {})
                records.append({"session_id": session_id, "task_id": task_id,
                                "family": scene["family"], "difficulty": scene["difficulty"],
                                "named_area_targets": names, "prior_public_leads": leads,
                                "prior_public_target_ids": sorted(prior_board.get("targets", {})),
                                "after_public_target_ids": sorted(after_board.get("targets", {})),
                                "passed": outcome["passed"] == "True",
                                "timed_out": outcome["agent_timed_out"] == "True"})
            prior_board = state.get("blackboards", {}).get(session_id, prior_board)
    lead_records = [row for row in records if row["prior_public_leads"]]
    failure_endpoints = Counter()
    failed_lead_target_overlap = []
    failed_lead_tasks_with_target_checks = 0
    failed_target_checks_on_prior_known_id = 0
    failed_target_checks_on_prior_unknown_id = 0
    failed_target_checks_on_after_known_id = 0
    failed_target_checks_on_after_unknown_id = 0
    failed_lead_tasks_with_after_unknown_target_check = 0
    for row in lead_records:
        if row["passed"]:
            continue
        scene_dir = attempt / next(scene["directory"] for scene in full["scenes"]
                                   if scene["session_id"] == row["session_id"])
        checks = read(scene_dir / f"{row['task_id']}.checks.json")
        failed = [item for item in checks if item.get("type") == "leaf"
                  and item.get("result") is False]
        failure_endpoints.update(item.get("endpoint") for item in failed)
        target_checks = [item for item in failed
                         if item.get("endpoint", "").startswith("/check/target_")]
        failed_lead_tasks_with_target_checks += bool(target_checks)
        known_ids = set(row["prior_public_target_ids"])
        after_ids = set(row["after_public_target_ids"])
        failed_target_checks_on_prior_known_id += sum(
            item.get("parameters", {}).get("target_id") in known_ids for item in target_checks)
        failed_target_checks_on_prior_unknown_id += sum(
            item.get("parameters", {}).get("target_id") not in known_ids for item in target_checks)
        failed_target_checks_on_after_known_id += sum(
            item.get("parameters", {}).get("target_id") in after_ids for item in target_checks)
        failed_target_checks_on_after_unknown_id += sum(
            item.get("parameters", {}).get("target_id") not in after_ids for item in target_checks)
        failed_lead_tasks_with_after_unknown_target_check += any(
            item.get("parameters", {}).get("target_id") not in after_ids for item in target_checks)
        lead_ids = {item["id"] for item in row["prior_public_leads"]}
        matches = [item for item in target_checks
                   if item.get("parameters", {}).get("target_id") in lead_ids]
        if matches:
            failed_lead_target_overlap.append({"session_id": row["session_id"],
                                               "task_id": row["task_id"],
                                               "matched_failed_checks": len(matches)})
    result = {
        "method": "scene-disjoint-B0-public-blackboard-prevalence-not-R2-trigger-replay",
        "full_attempt": full["attempt"],
        "excluded_R2_development_scene_ids": sorted(held_out),
        "included_scenes": len(full["scenes"]) - len(held_out),
        "included_tasks": sum(scene["tasks"] for scene in full["scenes"]
                              if scene["session_id"] not in held_out),
        "explicit_named_area_visit_tasks": len(records),
        "tasks_with_prior_public_shape_lead": len(lead_records),
        "lead_tasks_passed_B0": sum(row["passed"] for row in lead_records),
        "lead_tasks_failed_B0": sum(not row["passed"] for row in lead_records),
        "lead_tasks_timed_out_B0": sum(row["timed_out"] for row in lead_records),
        "failed_lead_tasks_with_any_failed_target_check": failed_lead_tasks_with_target_checks,
        "failed_lead_tasks_with_failed_check_on_hinted_target": len(failed_lead_target_overlap),
        "failed_target_checks_on_any_prior_public_target_id": failed_target_checks_on_prior_known_id,
        "failed_target_checks_on_id_absent_from_prior_blackboard": failed_target_checks_on_prior_unknown_id,
        "failed_target_checks_on_id_present_in_after_blackboard": failed_target_checks_on_after_known_id,
        "failed_target_checks_on_id_absent_from_after_blackboard": failed_target_checks_on_after_unknown_id,
        "failed_lead_tasks_with_target_check_absent_from_after_blackboard": failed_lead_tasks_with_after_unknown_target_check,
        "failed_lead_target_overlap_cases": failed_lead_target_overlap,
        "failed_check_endpoints_in_lead_tasks": dict(failure_endpoints),
        "all_explicit_area_tasks_failed_B0": sum(not row["passed"] for row in records),
        "quality": dict(quality),
        "lead_cases": lead_records,
        "limitations": [
            "B0 policy/trajectories differ from R2; this does not estimate worker trigger rate or treatment benefit",
            "A blackboard lead here lacks tool-action provenance; these are upper-bound candidates, not confirmed direct receipts",
            "Existing prior leads can already help the main agent; B0 failures are not automatically rescuable by a scout",
            "Only explicit actor-to-named-circle/polygon visit syntax recognized by the current parser is counted",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "included_scenes", "included_tasks", "explicit_named_area_visit_tasks",
        "tasks_with_prior_public_shape_lead", "lead_tasks_passed_B0",
        "lead_tasks_failed_B0", "lead_tasks_timed_out_B0",
        "failed_lead_tasks_with_failed_check_on_hinted_target", "quality")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
