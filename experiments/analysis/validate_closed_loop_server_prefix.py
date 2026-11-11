#!/usr/bin/env python3
"""Replay the closed-loop offline actions against the unmodified local server.

No external model or TCP service is used. The FastAPI TestClient runs in this
process; every task imports its own original task-start snapshot.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from api.server import ROLE_SECRETS, UserRole, app  # noqa: E402

RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403/823c15d5"
INPUT = ROOT / "docs/audit/r2-closed-loop-search-20261009/evaluation.json"
OUTPUT_DIR = ROOT / "docs/audit/r2-closed-loop-search-20261009"


def body(response, label: str):
    try:
        result = response.json()
    except Exception as exc:
        raise RuntimeError(f"{label}: invalid JSON, HTTP {response.status_code}") from exc
    if response.status_code not in (200, 201):
        raise RuntimeError(f"{label}: HTTP {response.status_code}: {result}")
    return result


def check_one(client: TestClient, row: dict, max_rounds: int, headers: dict) -> dict:
    task_id = row["task_id"]
    before = json.loads((RUN / f"{task_id}.before.json").read_text())
    session_id = before["id"]
    body(client.post(f"/sessions/{session_id}", headers=headers, json=before), "restore")
    body(client.post(f"/sessions/{session_id}/set-current", headers=headers), "set-current")
    progress = body(client.get("/sessions/current/task-progress", headers=headers), "initial-progress")
    if progress["progress_percentage"] != row["start_progress_percent"]:
        raise ValueError(f"Initial progress mismatch: {progress}")
    took_off = set()
    completed = blocked = senses = 0
    mismatches = []
    checkpoints = []
    for event in row["attempts"]:
        if event["round"] > max_rounds:
            break
        actor = event["drone_id"]
        if actor not in took_off:
            takeoff = body(client.post(f"/drones/{actor}/command/take_off",
                                       params={"altitude": 20.0}, headers=headers), "takeoff")
            if takeoff.get("status") != "success":
                mismatches.append({"sequence": event["sequence"], "kind": "takeoff",
                                   "server": takeoff.get("status"), "offline": event["outcome"]})
                break
            took_off.add(actor)
        x, y = event["to_xy"]
        response = client.post(f"/drones/{actor}/command/move_to",
                               params={"x": x, "y": y, "z": 20.0}, headers=headers)
        result = body(response, "move_to")
        actual = result.get("status")
        collision_error = (actual == "error" and "obstacle" in str(result.get("message", "")).lower())
        expected_success = event["outcome"] == "completed"
        matched_outcome = (actual == "success" if expected_success else
                           actual == "blocked" or collision_error)
        if not matched_outcome:
            mismatches.append({"sequence": event["sequence"], "kind": "movement",
                               "server": actual, "offline": event["outcome"],
                               "server_message": result.get("message")})
            break
        completed += actual == "success"
        blocked += actual == "blocked" or collision_error
        nearby = body(client.get(f"/drones/{actor}/nearby", headers=headers), "nearby")
        senses += 1
        targets = {str(item["id"]) for item in nearby["targets"]}
        obstacles = {str(item["id"]) for item in nearby["obstacles"]}
        if targets != set(event["sensed_target_ids"]) or obstacles != set(event["sensed_obstacle_ids"]):
            mismatches.append({"sequence": event["sequence"], "kind": "nearby",
                               "server_target_count": len(targets),
                               "offline_target_count": len(event["sensed_target_ids"]),
                               "server_obstacle_count": len(obstacles),
                               "offline_obstacle_count": len(event["sensed_obstacle_ids"]),
                               "target_diff": sorted(targets ^ set(event["sensed_target_ids"])),
                               "obstacle_diff": sorted(obstacles ^ set(event["sensed_obstacle_ids"]))})
            break
        if event["sequence"] == row["attempts"][-1]["sequence"] or (
                event["round"] in (4, 8, 16) and
                (event is row["attempts"][-1] or
                 row["attempts"][event["sequence"]]["round"] != event["round"])):
            progress = body(client.get("/sessions/current/task-progress", headers=headers), "progress")
            checked = body(client.get(
                f"/sessions/current/tasks/{task_id}/check", headers=headers), "original-check")
            expected_row = next(item for item in row["checkpoint_rows"]
                                if item["rounds_per_drone"] == event["round"])
            actual_progress = progress["progress_percentage"]
            checkpoints.append({"round": event["round"], "server_progress_percent": actual_progress,
                                "offline_progress_percent": expected_row["geometric_progress_percent"],
                                "original_check_result": checked.get("result")})
            if (actual_progress != expected_row["geometric_progress_percent"] or
                    bool(checked.get("result")) != expected_row["original_checker_threshold_met"]):
                mismatches.append({"sequence": event["sequence"], "kind": "progress",
                                   "server": actual_progress,
                                   "offline": expected_row["geometric_progress_percent"],
                                   "original_check": checked.get("result")})
                break
    return {"task_id": task_id, "max_rounds": max_rounds, "completed_moves": completed,
            "blocked_moves": blocked, "nearby_queries": senses,
            "checkpoints": checkpoints, "mismatches": mismatches,
            "matched": not mismatches}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, choices=(4, 8, 16), default=4)
    parser.add_argument("--task-id", choices=("5e743d21", "24ac1838", "7cdefb56"))
    args = parser.parse_args()
    evaluation = json.loads(INPUT.read_text())
    rows = [row for row in evaluation["rows"]
            if args.task_id is None or row["task_id"] == args.task_id]
    headers = {"X-API-Key": ROLE_SECRETS[UserRole.ADMIN]}
    with TestClient(app) as client:
        results = [check_one(client, row, args.rounds, headers) for row in rows]
    output = {"schema": 1, "source_evaluation": str(INPUT.relative_to(ROOT)),
              "max_rounds": args.rounds, "uses_local_testclient": True,
              "model_calls": 0, "results": results}
    output_path = OUTPUT_DIR / f"server-replay-{args.rounds}rounds.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))
    if any(not item["matched"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
