#!/usr/bin/env python3
"""Classify saved R2 terminal claims against original external checks.

Categories are a manual reading of the first frozen R2 run. Each assigned
phrase is asserted against the original model output for reproducibility.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ATTEMPT = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403"
OUTPUT = ROOT / "docs/audit/r3-original-recovery-20261009/r2-terminal-claims.json"
R2_COMMIT = "69dd34c067ec97b746db5180684594c546a35172"

# This is a descriptive post-hoc coding of the original text, not an online
# completion detector. The phrase checks prevent drift or task-ID confusion.
LABELS = {
    "95fef67d": ("acknowledges_unfinished", "start point and waypoint 1 could not be occupied"),
    "529c0622": ("acknowledges_unfinished", "Circle Target 6 — NOT FOUND"),
    "018d1d05": ("acknowledges_unfinished", "exact points are therefore unreachable"),
    "de19fecd": ("coverage_claim_actor_semantics_unclear", "The completed coverage was recorded"),
    "f9868b0d": ("acknowledges_unfinished", "Waypoint 3 (470, 352, 27) could not be reached"),
    "b2c116a1": ("acknowledges_unfinished", "physically unreachable"),
    "18e35f9d": ("acknowledges_unfinished", "The only deviation from the literal instructions"),
    "ce97a68d": ("acknowledges_unfinished", "Fixed Target 59 — not reached"),
    "c6e28e3b": ("acknowledges_unfinished", "Targets **59 and 55 could not be located"),
    "9e46da10": ("acknowledges_unfinished", "that half of the task remains unfulfilled"),
    "925eadfa": ("acknowledges_unfinished", "could not be entered"),
    "08332988": ("acknowledges_unfinished", "are physically unreachable"),
    "4ccc0dbc": ("acknowledges_unfinished", "that exact point is unreachable"),
    "f777f4e3": ("acknowledges_unfinished", "unreachable"),
    "cf8ee51f": ("claims_all_coordinates_reached", "all requested start/waypoint/end coordinates were reached"),
}


def read(path: Path):
    return json.loads(path.read_text())


def main():
    source = subprocess.check_output(["git", "show", f"{R2_COMMIT}:agent4drone/uav_agent.py"],
                                     cwd=ROOT, text=True)
    if ('if "[TASK DONE]" not in output:' not in source
            or '"success": True,' not in source):
        raise ValueError("Frozen R2 completion rule changed")
    rows = read(ATTEMPT / "results.json")
    failed = [row for row in rows if not row["passed"]]
    timed_out = [row for row in failed if row["agent_timed_out"]]
    completed = [row for row in failed if not row["agent_timed_out"]]
    if len(rows) != 100 or len(failed) != 21 or len(timed_out) != 6 or {x["task_id"] for x in completed} != set(LABELS):
        raise ValueError("R2 terminal-claim cohort changed")
    details = []
    for row in completed:
        task_id = row["task_id"]
        job = read(ATTEMPT / row["session_id"] / f"{task_id}.job.json")
        if job["status"] != "completed" or row["agent_success"] is not True:
            raise ValueError(f"Expected completed runtime record: {task_id}")
        output = job["result"]["output"]
        label, phrase = LABELS[task_id]
        match_at = output.casefold().find(phrase.casefold())
        if match_at < 0 or "[TASK DONE]" not in output:
            raise ValueError(f"Saved model output no longer supports label: {task_id}")
        details.append({"session_id": row["session_id"], "task_id": task_id,
                        "external_pass": row["passed"], "passed_checks": sum(x["result"] for x in row["leaves"]),
                        "total_checks": len(row["leaves"]), "runtime_success": row["agent_success"],
                        "terminal_marker_present": True, "manual_output_category": label,
                        "supporting_excerpt": output[max(0, match_at - 25):match_at + len(phrase) + 70].replace("\n", " "),
                        "output_sha256": hashlib.sha256(output.encode()).hexdigest()})
    result = {"schema": 1, "source_commit": R2_COMMIT,
              "completion_rule": "R2 runtime checks for nonempty output and [TASK DONE], then returns success=True; it does not evaluate the external task checks",
              "scope": "Manual post-hoc reading of saved model text, not a verified causal intervention",
              "failed_tasks": len(failed), "timed_out_failures": len(timed_out),
              "runtime_completed_but_external_failed": len(completed),
              "output_categories": dict(Counter(item["manual_output_category"] for item in details)),
              "rows": details}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("failed_tasks", "timed_out_failures",
                                                 "runtime_completed_but_external_failed", "output_categories")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
