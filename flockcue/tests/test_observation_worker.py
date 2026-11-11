"""Evidence and trigger checks for the opt-in observation worker."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))

from flockcue.evidence.observation_worker import (
    TaskEvidenceWorkerLedger, TaskEvidenceWorkerMiddleware, _valid_plan,
    run_observation_worker,
)
from flockcue.evidence.contact_evidence import SessionEvidenceIndexR2


def test_policy_replay_matches_frozen_shadow_exposure():
    pairs = json.loads((ROOT / "docs/results/m1-revision2-fast100-20261007/paired-tasks.json").read_text())
    indexes = {}
    found = []
    for pair in pairs:
        trace = json.loads((ROOT / pair["m1r2_trace"]).read_text())
        ix = indexes.setdefault(pair["session_id"], SessionEvidenceIndexR2(pair["session_id"]))
        ledger = TaskEvidenceWorkerLedger(trace["command"], trace["command_id"], index=ix)
        middleware = TaskEvidenceWorkerMiddleware(ledger, lambda *_: None, time.monotonic())
        events = trace["task_progress"]["events"]
        timeline = [(e["start_sequence"], "start", e) for e in events]
        timeline += [(e["end_sequence"], "end", e) for e in events if e["end_sequence"] is not None]
        for _sequence, kind, event in sorted(timeline, key=lambda value: value[0]):
            if kind == "start":
                assert ledger.begin_tool(event["tool_name"], event["args"], event["tool_call_id"]) == event["evidence_id"]
            elif isinstance(event["payload"], dict) and set(event["payload"]) == {"tool_exception"}:
                ledger.end_tool(event["evidence_id"], error=event["payload"]["tool_exception"])
            else:
                ledger.end_tool(event["evidence_id"], event["payload"])
            if kind == "end" and not ledger._active:
                trigger = middleware._candidate()
                if trigger:
                    found.append((pair["task_id"], trigger["trigger_sequence"]))
                    middleware._fired = True
        assert not ledger.snapshot()["observer_errors"]
    assert found == [("529c0622", 34), ("22e1e1c5", 30)]


def test_worker_plan_rejects_required_actor_and_outside_public_extent():
    drones = [
        {"id": "required", "position": {"x": 100, "y": 100, "z": 20}, "battery_level": 90},
        {"id": "extra", "position": {"x": 500, "y": 500, "z": 0}, "battery_level": 90},
    ]
    assert _valid_plan({"drone_id": "required", "waypoints": [{"x": 250, "y": 250}]},
                       drones, "required") is None
    assert _valid_plan({"drone_id": "extra", "waypoints": [{"x": 10000, "y": 250}]},
                       drones, "required") is None
    actor, points = _valid_plan({"drone_id": "extra", "waypoints": [{"x": 300, "y": 300}]},
                                drones, "required")
    assert actor == "extra" and points == [{"x": 300.0, "y": 300.0, "z": 12.0}]


def test_static_worker_sensing_updates_ledger_without_model_claim():
    drones = [
        {"id": "required", "name": "Drone 11", "position": {"x": 100, "y": 100, "z": 20}, "battery_level": 90},
        {"id": "extra", "name": "Drone 1", "position": {"x": 500, "y": 500, "z": 0}, "battery_level": 90},
    ]
    target = {"id": "target9", "name": "Fixed Target 9",
              "position": {"x": 510, "y": 520, "z": 0}}
    sense_reply = {"per_drone": {"extra": {"targets": [target], "drones": [], "obstacles": []}}}
    fake_tool = SimpleNamespace(name="sense_nearby_entities",
                                invoke=lambda _args: json.dumps(sense_reply))
    fake_agent = SimpleNamespace(client=SimpleNamespace(list_drones=lambda: drones),
                                 tools=[fake_tool])
    ledger = TaskEvidenceWorkerLedger(
        "Drone Drone 11 must visit and reach target Fixed Target 9.", "task",
        index=SessionEvidenceIndexR2("scene"))
    run = {"tool_receipts": []}
    result = run_observation_worker(fake_agent, ledger,
                                    {"required_actor_id": "required",
                                     "unresolved_targets": ["Fixed Target 9"]}, run)
    assert result == {"status": "localized_by_static_sensing", "worker_move_count": 0}
    requirement = next(item for item in ledger.projection() if item["target_name"] == "Fixed Target 9")
    assert requirement["knowledge_state"] == "localized"
    assert requirement["execution_state"] == "unattempted"
    assert requirement["position"]["evidence_id"] == run["tool_receipts"][-1]["evidence_id"]
