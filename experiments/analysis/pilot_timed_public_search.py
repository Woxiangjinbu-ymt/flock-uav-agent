#!/usr/bin/env python3
"""Run a bounded public-feedback search against the real server handlers.

Only restore_snapshot() reads a private task-start file. The policy receives
public API data and the obstacles recorded in the original public first sense.
The original task checker is called once, after the decision loop has stopped.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "agent4drone"))
from api.server import ROLE_SECRETS, UserRole, app  # noqa: E402
from coordinated_search_r2 import MAX_ROUNDS, PublicSearchPolicy  # noqa: E402
from build_public_aggregate_search_plan import (  # noqa: E402
    SCENE, TASK_IDS, public_inputs, trace_for,
)

RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403" / SCENE
OUT = ROOT / "docs/audit/r2-timed-public-search-20261009/testclient-results.json"
LIMIT_S = 500.0
PROGRESS_EVERY = 10


def body(response, label: str):
    try:
        value = response.json()
    except Exception as exc:
        raise RuntimeError(f"{label}: invalid JSON, HTTP {response.status_code}") from exc
    if response.status_code not in (200, 201):
        raise RuntimeError(f"{label}: HTTP {response.status_code}: {value}")
    return value


class TimedClient:
    def __init__(self, client, headers: dict):
        self.client = client
        self.headers = headers
        self.calls = Counter()
        self.seconds = Counter()

    def request(self, method: str, path: str, kind: str, **kwargs):
        started = time.perf_counter()
        response = self.client.request(method, path, headers=self.headers, **kwargs)
        self.seconds[kind] += time.perf_counter() - started
        self.calls[kind] += 1
        return body(response, kind)


def restore_snapshot(client, task_id: str, admin_headers: dict) -> str:
    # The private file stops here. Nothing from it enters the timed pilot.
    before = json.loads((RUN / f"{task_id}.before.json").read_text())
    session_id = before["id"]
    body(client.post(f"/sessions/{session_id}", headers=admin_headers, json=before), "restore")
    body(client.post(f"/sessions/{session_id}/set-current", headers=admin_headers), "set-current")
    return session_id


def public_start(task_id: str) -> tuple[list[dict], str]:
    path, trace = trace_for(task_id)
    old_drones, obstacles, _, _, _ = public_inputs(trace)
    return (old_drones, obstacles), str(path.relative_to(ROOT))


def run_one(client, task_id: str, admin_headers: dict, agent_headers: dict,
            progress_every: int = PROGRESS_EVERY, *, restore: bool = True,
            limit_s: float = LIMIT_S) -> dict:
    (old_drones, inherited_obstacles), trace_path = public_start(task_id)
    session_id = (restore_snapshot(client, task_id, admin_headers)
                  if restore else SCENE)
    api = TimedClient(client, agent_headers)
    start = time.perf_counter()

    drones = api.request("GET", "/drones", "list_drones")
    public_start_xy = {str(d["id"]): (float(d["position"]["x"]),
                                        float(d["position"]["y"])) for d in old_drones}
    actual_start_xy = {str(d["id"]): (float(d["position"]["x"]),
                                        float(d["position"]["y"])) for d in drones}
    if restore and actual_start_xy != public_start_xy:
        raise ValueError(f"{task_id}: original public list differs from restored list")

    planning_s = 0.0
    plan_started = time.perf_counter()
    policy = PublicSearchPolicy(drones, inherited_obstacles)
    planning_s += time.perf_counter() - plan_started
    progress = api.request("GET", "/sessions/current/task-progress", "task_progress")
    initial_progress = progress["progress_percentage"]
    progress_receipts = [{"after_attempt": 0, "progress_percent": initial_progress,
                          "elapsed_s": round(time.perf_counter() - start, 3)}]
    attempts = []
    took_off = {str(drone["id"]) for drone in drones
                if float(drone["position"]["z"]) > 0}
    sequence = 0
    stop_reason = None
    for round_number in range(1, MAX_ROUNDS + 1):
        if stop_reason:
            break
        made_attempt = False
        for actor in policy.ids:
            if time.perf_counter() - start >= limit_s:
                stop_reason = "wall_clock_limit"
                break
            if progress["progress_percentage"] > 95:
                stop_reason = "public_progress_strictly_above_95"
                break
            plan_started = time.perf_counter()
            choice = policy.choose(actor)
            planning_s += time.perf_counter() - plan_started
            if choice is None:
                continue
            index, destination, _ = choice
            if actor not in took_off:
                takeoff = api.request("POST", f"/drones/{actor}/command/take_off",
                                      "take_off", params={"altitude": 20.0})
                if takeoff.get("status") != "success":
                    policy.disable(actor)
                    continue
                took_off.add(actor)
            sequence += 1
            made_attempt = True
            origin = list(policy.xy[actor])
            move = api.request("POST", f"/drones/{actor}/command/move_to", "move_to",
                               params={"x": destination[0], "y": destination[1], "z": 20.0})
            status = move.get("status")
            if status == "success":
                outcome = "completed"
            elif status == "blocked" or (status == "error" and
                    "obstacle" in str(move.get("message", "")).lower()):
                outcome = "blocked"
            else:
                raise RuntimeError(f"Unexpected move status for {actor}: {status}: "
                                   f"{move.get('message')}")
            state = api.request("GET", f"/drones/{actor}", "drone_status")
            actual_xy = (float(state["position"]["x"]), float(state["position"]["y"]))
            nearby = api.request("GET", f"/drones/{actor}/nearby", "nearby")
            plan_started = time.perf_counter()
            new_obstacles = policy.accept(actor, index, outcome, actual_xy,
                                          nearby["obstacles"], round_number=round_number,
                                          sequence=sequence)
            planning_s += time.perf_counter() - plan_started
            attempts.append({"sequence": sequence, "round": round_number,
                             "drone_id": actor, "from_xy": origin,
                             "to_xy": list(destination), "outcome": outcome,
                             "actual_xy": list(actual_xy),
                             "new_obstacle_ids": new_obstacles,
                             "battery_percent": state["battery_level"],
                             "elapsed_s": round(time.perf_counter() - start, 3)})
            if sequence % progress_every == 0:
                progress = api.request("GET", "/sessions/current/task-progress", "task_progress")
                progress_receipts.append({"after_attempt": sequence,
                                          "progress_percent": progress["progress_percentage"],
                                          "elapsed_s": round(time.perf_counter() - start, 3)})
                if progress["progress_percentage"] < progress_receipts[-2]["progress_percent"]:
                    raise ValueError("Public progress regressed")
                if progress["progress_percentage"] > 95:
                    stop_reason = "public_progress_strictly_above_95"
                    break
        if not made_attempt and not stop_reason:
            stop_reason = "no_public_candidate"
    if stop_reason is None:
        stop_reason = "round_limit"

    if not progress_receipts or progress_receipts[-1]["after_attempt"] != sequence:
        progress = api.request("GET", "/sessions/current/task-progress", "task_progress")
        progress_receipts.append({"after_attempt": sequence,
                                  "progress_percent": progress["progress_percentage"],
                                  "elapsed_s": round(time.perf_counter() - start, 3)})
    timed_s = time.perf_counter() - start
    # Post-run checker is diagnostic only; it has no input path to the policy.
    checked = body(client.get(f"/sessions/current/tasks/{task_id}/check",
                              headers=admin_headers), "original-check")
    outcomes = Counter(item["outcome"] for item in attempts)
    return {"task_id": task_id, "session_id": session_id, "public_trace": trace_path,
            "initial_progress_percent": initial_progress,
            "final_public_progress_percent": progress["progress_percentage"],
            "stop_reason": stop_reason, "original_checker_passed": bool(checked.get("result")),
            "timed_s": round(timed_s, 3), "planning_s": round(planning_s, 3),
            "api_seconds": {k: round(v, 3) for k, v in api.seconds.items()},
            "api_calls": dict(api.calls), "attempt_count": sequence,
            "outcomes": dict(outcomes), "public_new_obstacles": len(policy.obstacle_sources),
            "min_battery_percent": min((item["battery_percent"] for item in attempts), default=None),
            "progress_receipts": progress_receipts, "attempts": attempts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", choices=TASK_IDS)
    parser.add_argument("--base-url", help="Optional local HTTP server URL")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or (OUT if not args.base_url else OUT.with_name("http-results.json"))
    admin = {"X-API-Key": ROLE_SECRETS[UserRole.ADMIN]}
    agent = {"X-API-Key": ROLE_SECRETS[UserRole.AGENT]}
    transport = "loopback_http" if args.base_url else "fastapi_testclient"
    with (httpx.Client(base_url=args.base_url, timeout=60.0) if args.base_url
          else TestClient(app)) as client:
        rows = [run_one(client, task_id, admin, agent) for task_id in
                ([args.task_id] if args.task_id else TASK_IDS)]
    output = {"schema": 1, "transport": transport, "model_calls": 0,
              "policy_source_sha256": hashlib.sha256(
                  (ROOT / "agent4drone/coordinated_search_r2.py").read_bytes()).hexdigest(),
              "limit_s_per_task": LIMIT_S, "progress_every_attempts": PROGRESS_EVERY,
              "rows": rows}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(destination), "rows": [
        {k: row[k] for k in ("task_id", "attempt_count", "timed_s",
                             "final_public_progress_percent", "stop_reason",
                             "original_checker_passed")} for row in rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
