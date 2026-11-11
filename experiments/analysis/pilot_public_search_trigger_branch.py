#!/usr/bin/env python3
"""Explore a branch from each recorded R2 late public-progress receipt.

Replays the original public API prefix against the original task-start server
snapshot, checks each command receipt, and only then runs the opt-in search
pilot within the remaining recorded task window. This is a retrospective
branch, not a contemporaneous agent A/B run.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

from fastapi.testclient import TestClient
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from api.server import ROLE_SECRETS, UserRole, app  # noqa: E402
from pilot_timed_public_search import RUN, body, restore_snapshot, run_one  # noqa: E402

SHADOW = ROOT / "docs/audit/r2-public-search-trigger-20261009/shadow.json"
OUT = ROOT / "docs/audit/r2-public-search-trigger-20261009/branches.json"
FAILURE_IDS = ("5e743d21", "24ac1838", "7cdefb56")


def stable_message(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"battery: [^)]*", "battery: *", value)


def replay_prefix(client, task_id: str, row: dict, admin: dict, agent: dict) -> dict:
    restore_snapshot(client, task_id, admin)
    trace = json.loads((ROOT / row["trace"]).read_text())
    elapsed = row["first_trigger"]["elapsed_s"]
    window = json.loads((RUN / f"{task_id}.window.json").read_text())
    opened = datetime.fromtimestamp(window["opened_at"])
    progress_calls = [call for call in trace["api_calls"]
                      if call["endpoint"] == "/sessions/current"]
    trigger_call = next((call for call in progress_calls
                         if abs((datetime.fromisoformat(call["completed_at"]) -
                                  opened).total_seconds() - elapsed) < 0.002), None)
    if trigger_call is None:
        raise ValueError("Trigger receipt did not map to an original API call")
    prefix = [call for call in trace["api_calls"]
              if call["index"] <= trigger_call["index"]]
    trigger_time = datetime.fromisoformat(trigger_call["completed_at"])
    if any(datetime.fromisoformat(call["completed_at"]) > trigger_time for call in prefix) or any(
            datetime.fromisoformat(call["completed_at"]) <= trigger_time
            for call in trace["api_calls"] if call["index"] > trigger_call["index"]):
        raise ValueError("Concurrent API calls cross the proposed branch boundary")
    mismatches = []
    command_count = 0
    nearby_count = 0
    position_count = 0
    battery_message_differences = 0
    for call in prefix:
        response = client.request(call["method"], call["endpoint"], headers=agent,
                                  params=call["params"], json=call["json"])
        try:
            observed = response.json()
        except Exception:
            observed = None
        expected = call["response"]
        if response.status_code != call["status_code"]:
            mismatches.append({"index": call["index"], "kind": "status_code",
                               "expected": call["status_code"],
                               "observed": response.status_code})
            break
        if call["method"] == "POST" and "/command/" in call["endpoint"]:
            command_count += 1
            if (isinstance(observed, dict) and isinstance(expected, dict) and
                    observed.get("message") != expected.get("message") and
                    stable_message(observed.get("message")) ==
                    stable_message(expected.get("message"))):
                battery_message_differences += 1
            if not isinstance(observed, dict) or not isinstance(expected, dict) or (
                    observed.get("status"), stable_message(observed.get("message"))) != (
                        expected.get("status"), stable_message(expected.get("message"))):
                mismatches.append({"index": call["index"], "kind": "command_receipt",
                                   "expected": {k: expected.get(k) for k in ("status", "message")},
                                   "observed": {k: observed.get(k) for k in ("status", "message")}
                                   if isinstance(observed, dict) else observed})
                break
        if call["method"] == "GET" and call["endpoint"].startswith("/drones/") and (
                call["endpoint"].count("/") == 2 and isinstance(expected, dict)
                and "position" in expected):
            position_count += 1
            if not isinstance(observed, dict) or observed.get("position") != expected["position"]:
                mismatches.append({"index": call["index"], "kind": "public_drone_position"})
                break
        if (call["method"] == "GET" and call["endpoint"].endswith("/nearby")
                and isinstance(expected, dict)):
            nearby_count += 1
            for entity_kind in ("targets", "obstacles"):
                old_ids = {str(item["id"]) for item in expected.get(entity_kind, [])}
                new_ids = {str(item["id"]) for item in observed.get(entity_kind, [])}
                if old_ids != new_ids:
                    mismatches.append({"index": call["index"],
                                       "kind": "public_nearby_" + entity_kind,
                                       "old_count": len(old_ids), "new_count": len(new_ids)})
                    break
            if mismatches:
                break
    if not mismatches:
        progress = body(client.get("/sessions/current/task-progress", headers=agent),
                        "branch-progress")
        if progress["progress_percentage"] != row["first_trigger"]["progress_percent"]:
            mismatches.append({"kind": "public_progress",
                               "expected": row["first_trigger"]["progress_percent"],
                               "observed": progress["progress_percentage"]})
    return {"task_id": task_id, "original_prefix_api_calls": len(prefix),
            "original_prefix_commands": command_count,
            "original_prefix_nearby_checks": nearby_count,
            "original_prefix_position_checks": position_count,
            "battery_message_differences": battery_message_differences,
            "trigger_elapsed_s": elapsed,
            "remaining_original_budget_s": row["first_trigger"]["remaining_s"],
            "prefix_mismatches": mismatches,
            "matched": not mismatches}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", choices=FAILURE_IDS)
    parser.add_argument("--base-url", help="Optional local HTTP server URL")
    args = parser.parse_args()
    destination = OUT if not args.base_url else OUT.with_name("branches-http.json")
    rows = {row["task_id"]: row for row in json.loads(SHADOW.read_text())["rows"]}
    admin = {"X-API-Key": ROLE_SECRETS[UserRole.ADMIN]}
    agent = {"X-API-Key": ROLE_SECRETS[UserRole.AGENT]}
    results = []
    with (httpx.Client(base_url=args.base_url, timeout=60.0) if args.base_url
          else TestClient(app)) as client:
        for task_id in ([args.task_id] if args.task_id else FAILURE_IDS):
            prefix = replay_prefix(client, task_id, rows[task_id], admin, agent)
            if prefix["matched"]:
                branch = run_one(client, task_id, admin, agent, restore=False,
                                 limit_s=prefix["remaining_original_budget_s"])
                prefix["branch"] = branch
            results.append(prefix)
    output = {"schema": 1, "transport": "loopback_http" if args.base_url else "fastapi_testclient",
              "method": "Original public API prefix replay, then opt-in search",
              "model_calls_after_trigger": 0, "rows": results}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(destination), "rows": [{
        "task_id": row["task_id"], "prefix_matched": row["matched"],
        "commands": row["original_prefix_commands"],
        "mismatches": row["prefix_mismatches"],
        "branch_attempts": row.get("branch", {}).get("attempt_count"),
        "branch_progress": row.get("branch", {}).get("final_public_progress_percent"),
        "branch_timed_s": row.get("branch", {}).get("timed_s")}
        for row in results]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
