#!/usr/bin/env python3
"""Public-only, in-process server validation; no provider, TCP service or hidden oracle in policy.

The administrative channel restores a historical starting snapshot and checks the
result only after the skill ends. Resume decisions are scripted, not model calls.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent4drone"), str(ROOT / "server")]

from fastapi.testclient import TestClient
from api.server import ROLE_SECRETS, UserRole, app
from blackboard import PerceptionBlackboard
from harness_core import HarnessRuntime
from harness_skills import make_skill_factory, search_supported, simple_plan
from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2, public_progress

PAIRS = ROOT / "docs/results/m1-revision2-fast100-20261007/paired-tasks.json"
OUTPUT = ROOT / "docs/results/minimal-harness-offline-20261015"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def body(response):
    response.raise_for_status()
    return response.json()


class PublicAdapter:
    def __init__(self, client, session_id):
        self.client, self.session_id = client, session_id
        self.calls = []
        self.headers = {"X-API-Key": ROLE_SECRETS[UserRole.AGENT]}

    def read(self, endpoint):
        return body(self.client.get(endpoint, headers=self.headers))

    def invoke(self, call):
        self.calls.append({"tool": call.tool, "args": call.args, "action": call.action})
        if call.tool == "list_drones":
            return self.read("/drones")
        if call.tool == "get_mission_progress":
            return public_progress(self.read("/sessions/current"), self.session_id)
        actor = call.args["drone_id"]
        if call.tool == "get_drone_status":
            return self.read(f"/drones/{actor}")
        if call.tool == "get_nearby_entities":
            return self.read(f"/drones/{actor}/nearby")
        if call.tool not in {"take_off", "rotate", "change_altitude", "hover", "move_to"}:
            raise ValueError("Non-public or unsupported skill action")
        return body(self.client.post(f"/drones/{actor}/command/{call.tool}", headers=self.headers,
                                    params={k: v for k, v in call.args.items() if k != "drone_id"}))


def check_one(client, entry, scheduler, out):
    before_path = ROOT / entry["before"]
    before = json.loads(before_path.read_text())
    sid = before["id"]
    admin = {"X-API-Key": ROLE_SECRETS[UserRole.ADMIN]}
    body(client.post(f"/sessions/{sid}", json=before, headers=admin))
    body(client.post(f"/sessions/{sid}/set-current", headers=admin))
    adapter = PublicAdapter(client, sid)
    board = PerceptionBlackboard(sid)
    bootstrap = []
    # All arms receive the same PUBLIC observation seed. This does not reproduce
    # historical 400-second activation or the original model's perception choices.
    if entry["skill"] == "public_search":
        bootstrap.append({"endpoint": "/drones"})
        for drone in adapter.read("/drones"):
            endpoint = f"/drones/{drone['id']}/nearby"
            observation = adapter.read(endpoint)
            board.ingest_nearby(drone["id"], observation)
            bootstrap.append({"endpoint": endpoint, "receipt_sha256": digest(observation)})
    index = SessionEvidenceIndexR2(sid)
    ledger = TaskEvidenceLedgerR2(entry["command"], entry["task_id"], index=index,
                                  aggregate_feedback=True, inherited_blackboard=board.full())
    runtime = HarnessRuntime(task_id=entry["task_id"], command=entry["command"], scene_id=sid,
                             scene_epoch=index.epoch, ledger=ledger, invoke=adapter.invoke,
                             skill_factory=make_skill_factory(board), scope=lambda: (sid, index.epoch),
                             started=time.monotonic(), scheduler=scheduler, fixed_k=4)
    result = runtime.control("start", skill=entry["skill"])
    slices = 1
    while result["status"] == "paused":
        result = runtime.control("resume", plan_id=result["plan_id"])
        slices += 1
        if slices > 2000:
            raise RuntimeError("Unbounded resume loop")
    runtime.close("offline_validation_end")
    final = [{k: d.get(k) for k in ("id", "position", "status", "heading", "battery_level")}
             for d in adapter.read("/drones")]
    progress = public_progress(adapter.read("/sessions/current"), sid)
    # The original checker is POSTHOC ONLY; never visible to the runtime.
    checked = body(client.get(f"/sessions/current/tasks/{entry['task_id']}/check", headers=admin))
    payload = {"task_id": entry["task_id"], "scheduler": scheduler, "response": result,
               "bootstrap_public_calls": bootstrap, "public_calls": adapter.calls,
               "harness": runtime.snapshot(), "ledger": ledger.snapshot(),
               "final_public_states": final, "posthoc_original_check": checked}
    name = f"{entry['task_id']}-{scheduler}.json"
    (out / "traces" / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"task_id": entry["task_id"], "skill": entry["skill"], "scheduler": scheduler,
            "status": result["status"], "reason": result["reason"], "cursor": result["cursor"],
            "control_slices_not_model_calls": slices, "api_calls": len(adapter.calls),
            "bootstrap_public_calls": len(bootstrap),
            "actions": sum(c["action"] for c in adapter.calls),
            "public_calls_sha256": digest(adapter.calls), "final_public_states_sha256": digest(final),
            "public_progress_percent": progress.get("progress_percentage"),
            "posthoc_original_check_result": checked.get("result"), "trace": f"traces/{name}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "traces").mkdir(exist_ok=True)
    entries = []
    for pair in json.loads(PAIRS.read_text()):
        trace_path = ROOT / pair["m1r2_trace"]
        command = json.loads(trace_path.read_text())["command"]
        skill = ("simple_chain" if simple_plan(command) else
                 "public_search" if search_supported(command) else "unsupported")
        before = trace_path.parents[2] / f"{pair['task_id']}.before.json"
        entries.append({"task_id": pair["task_id"], "session_id": pair["session_id"], "command": command,
                        "skill": skill, "before": str(before.relative_to(ROOT)),
                        "before_sha256": hashlib.sha256(before.read_bytes()).hexdigest()})
    counts = dict(Counter(e["skill"] for e in entries))
    if len(entries) != 100 or counts != {"simple_chain": 7, "public_search": 6, "unsupported": 87}:
        raise ValueError(f"Historical eligibility changed: {counts}")
    rows, comparisons = [], []
    with TestClient(app) as client:
        for entry in entries:
            if entry["skill"] == "unsupported":
                continue
            group = [check_one(client, entry, arm, out) for arm in sorted(HarnessRuntime.POLICIES)]
            fields = ("public_calls_sha256", "final_public_states_sha256", "status", "reason",
                      "public_progress_percent", "posthoc_original_check_result")
            same = all(all(row[k] == group[0][k] for k in fields) for row in group)
            exercised = all(row["actions"] > 0 for row in group)
            valid_exit = all(row["status"] == "completed" or (
                entry["skill"] == "public_search" and row["status"] == "needs_replan" and
                row["reason"] in {"empty_public_obstacle_blackboard", "local_repair_budget_exhausted",
                                   "no_public_candidate", "round_limit_below_threshold"}) for row in group)
            comparisons.append({"task_id": entry["task_id"], "same_public_calls_and_results": same,
                                "exercised_actions": exercised, "valid_completion_or_guard_exit": valid_exit})
            rows.extend(group)
            print(json.dumps({"task": entry["task_id"], "skill": entry["skill"], "same": same,
                              "status": group[0]["status"], "reason": group[0]["reason"],
                              "calls": group[0]["api_calls"]}), flush=True)
    report = {"schema": 1, "model_calls": 0, "uses_local_testclient": True,
              "role_policy": "AGENT for policy; ADMIN only fixture restore and posthoc check",
              "eligibility_counts": counts, "full_denominator": 100,
              "entries": entries, "rows": rows, "comparisons": comparisons,
              "passed": all(c["same_public_calls_and_results"] and c["valid_completion_or_guard_exit"] for c in comparisons)
                        and any(row["actions"] > 0 for row in rows if row["skill"] == "public_search"),
              "limitation": "Scripted commit/resume and common public observation seed; mechanical integration validation, not live model success or causal call savings."}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "runs": len(rows), "eligibility": counts, "model_calls": 0}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
