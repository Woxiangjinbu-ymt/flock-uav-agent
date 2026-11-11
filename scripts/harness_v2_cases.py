"""Predeclared 24 mechanism fixtures; failures act on the real simulator world."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import time

from harness_v2_world import ROOT, World, digest, sha, write_json

SEARCH_COMMAND = "Take off necessary drones and assigned them to all targets (within task radius) until the session task progress exceeds 95%."


def build_case(entry):
    from models.drone import Drone
    from models.target import Target, TargetType
    from models.obstacle import Obstacle, ObstacleType
    from models.environment import Environment
    family = entry["family"]
    number = int(entry["case_id"].rsplit("-", 1)[1])
    search = family.startswith("recoverable") or (family.startswith("invalid") and number > 2)
    variant = ((number - 1) // 4 if "blocked_candidate" in family else (number - 1) % 2)
    origin = 250 + variant * 70
    command = SEARCH_COMMAND if search else (f"Drone Drone 1 takes off to {20 + 5 * number} meters altitude, then rotates to face heading {90 + 15 * number} degrees and hovers.")
    drones = [Drone(name=f"Drone {i+1}", model="controlled-v2", max_speed=20,
                    max_altitude=150, battery_capacity=100000, perceived_radius=80,
                    task_radius=45, drone_id=f"drone-{i+1}",
                    position={"x": origin + i * 100, "y": origin, "z": 0}).to_dict()
              for i in range(3 if search else 1)]
    # Visible harmless low obstacle seeds the unchanged search grammar's public map.
    obstacles = [Obstacle("Low visible marker", ObstacleType.CIRCLE,
                 position={"x": origin + 20, "y": origin + 10, "z": 0}, radius=2,
                 height=1, obstacle_id="marker").to_dict()]
    targets = ([Target("Search target", TargetType.FIXED,
                       {"x": origin + 45, "y": origin + 115, "z": 0},
                       radius=10, target_id="goal").to_dict()] if search else [])
    # A target is not placed at spawn; no initial task credit is carried over.
    scene = {"id": entry["case_id"], "name": "Controlled mechanism fixture",
             "task_type": "target_assignment" if search else "others",
             "drones": drones, "targets": targets, "obstacles": obstacles,
             "environment": Environment(name="Controlled environment", environment_id="environment").to_dict(),
             "tasks": [], "history": {}}
    event = {"kind": "none"}
    if "blocked_candidate" in family:
        event = {"kind": "block_moves", "count": [1, 2, 4, 8][(number - 1) % 4]}
    elif "takeoff_actor" in family:
        event = {"kind": "block_takeoff", "count": 1, "actor": f"drone-{1 + (number - 1) // 2}"}
    elif family.startswith("invalid"):
        event = ({"kind": "actor_change", "heading_delta": 15 * number} if number <= 2
                 else {"kind": "block_moves", "count": 17})
    elif family.startswith("unknown"):
        event = {"kind": "receipt_loss", "applied": number <= 2}
    return {**entry, "command": command, "skill": "public_search" if search else "simple_chain",
            "event": event, "scene": scene, "fixture_status": "materialized",
            "checker": {"kind": "progress_above_95"} if search else
                       {"kind": "simple_sequence", "actor": "drone-1",
                        "altitude": 20 + 5 * number, "heading": 90 + 15 * number}}


class Faults:
    """Fault ordinal counts skill calls only, independent of model and wall time."""
    def __init__(self, world, case):
        self.world, self.case = world, case
        self.event = case["event"]
        self.count = 0
        self.reads = 0
        self.calls = []
        self.events = []

    def wrap(self, invoke):
        def call(public_call):
            from models.obstacle import Obstacle, ObstacleType
            from command_results import command_outcome
            event, world = self.event, self.world
            server = world.server
            actor = public_call.args.get("drone_id")
            drone = server.drone_controller.drones.get(actor)
            kind = event["kind"]
            self.calls.append({"tool": public_call.tool, "args": public_call.args, "action": public_call.action})
            if public_call.tool == "get_drone_status":
                self.reads += 1
            if kind == "actor_change" and self.reads == 3 and not self.count:
                before = copy.deepcopy(drone.position)
                drone.heading = (drone.heading + event["heading_delta"]) % 360
                self.count += 1
                self.events.append({"kind": kind, "at_skill_call": len(self.calls),
                                    "actor": actor, "position_unchanged": before, "new_heading": drone.heading})
            if kind == "receipt_loss" and public_call.tool == "take_off" and not self.count:
                self.count += 1
                receipt = invoke(public_call) if event["applied"] else None
                self.events.append({"kind": kind, "at_skill_call": len(self.calls),
                                    "applied": event["applied"], "actual_receipt": receipt,
                                    "state_after": drone.to_dict()})
                raise TimeoutError("Controlled lost action receipt")
            block_move = kind == "block_moves" and public_call.tool == "move_to" and self.count < event["count"]
            block_takeoff = (kind == "block_takeoff" and public_call.tool == "take_off"
                             and actor == event["actor"] and not self.count)
            if block_move or block_takeoff:
                before = copy.deepcopy(drone.position)
                destination = (public_call.args if block_move else drone.position)
                position = {"x": (before["x"] + destination["x"]) / 2,
                            "y": (before["y"] + destination["y"]) / 2, "z": 0}
                obstacle = Obstacle("Transient obstruction", ObstacleType.CIRCLE,
                                    position=position, radius=1, height=100, obstacle_id="transient")
                server.obstacle_controller.obstacles[obstacle.id] = obstacle
                try:
                    receipt = invoke(public_call)
                    if command_outcome(receipt) not in {"blocked", "failed", "rejected"}:
                        raise AssertionError(f"Physical fault did not block: {receipt}")
                    if drone.position != before:
                        raise AssertionError("Blocked action unexpectedly changed position")
                    self.count += 1
                    self.events.append({"kind": kind, "at_skill_call": len(self.calls), "ordinal": self.count,
                        "obstacle": obstacle.to_dict(), "actual_receipt": receipt, "state_after": drone.to_dict()})
                    return receipt
                finally:
                    # The transient obstruction clears after this command. No forged receipt.
                    server.obstacle_controller.obstacles.pop(obstacle.id, None)
            return invoke(public_call)
        def recorded(public_call):
            try:
                receipt = call(public_call)
            except Exception as exc:
                self.calls[-1]["public_result"] = {"transport_error": type(exc).__name__}
                raise
            self.calls[-1]["public_result"] = copy.deepcopy(receipt)
            return receipt
        def durable(public_call):
            try:
                return recorded(public_call)
            finally:
                if getattr(self.world, "fault_path", None):
                    write_json(self.world.fault_path, self.events)
        return durable


def checker(world, case):
    from task_evidence_r2 import public_progress
    expected = case["checker"]
    server = world.server
    session = server.session_controller.sync_current_session_state()
    if expected["kind"] == "progress_above_95":
        progress = public_progress(session.to_dict(), session.id)
        return {"passed": progress.get("progress_percentage", 0) > 95,
                "public_progress": progress, "target_reaches": copy.deepcopy(session.target_reaches)}
    drone = server.drone_controller.drones[expected["actor"]]
    events = [r.get("event") for r in drone.history_status]
    passed = (drone.position["z"] == expected["altitude"] and drone.heading == expected["heading"]
              and drone.status.value == "hovering" and
              all(e in events for e in ["take_off", "rotate", "hovering"]) and
              events.index("take_off") < events.index("rotate") < events.index("hovering"))
    return {"passed": passed, "actual": drone.to_dict(), "history_status": copy.deepcopy(drone.history_status)}


def offline_run(world, case, arm, resume=True):
    from blackboard import PerceptionBlackboard
    from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2, public_progress
    from harness_core import HarnessRuntime
    from harness_skills import make_skill_factory
    from harness_v2_online import client_class
    client = client_class(world)(base_url="http://testserver")
    board = PerceptionBlackboard(case["case_id"])
    seed = world.public_seed()
    for actor, nearby in seed["nearby"].items():
        board.ingest_nearby(actor, nearby)
    index = SessionEvidenceIndexR2(case["case_id"])
    ledger = TaskEvidenceLedgerR2(case["command"], "offline", index=index, aggregate_feedback=True,
                                 inherited_blackboard=board.full())
    def invoke(call):
        if call.tool == "get_mission_progress":
            return public_progress(client.get_current_session(), case["case_id"])
        return getattr(client, call.tool)(**call.args)
    faults = Faults(world, case)
    runtime = HarnessRuntime(task_id="offline", command=case["command"], scene_id=case["case_id"],
        scene_epoch=index.epoch, ledger=ledger, invoke=faults.wrap(invoke), skill_factory=make_skill_factory(board),
        scope=lambda: (case["case_id"], index.epoch), started=time.monotonic(), scheduler=arm)
    result = runtime.control("start", skill=case["skill"])
    first = copy.deepcopy(result)
    first_calls = copy.deepcopy(faults.calls)
    while resume and result["status"] == "paused":
        result = runtime.control("resume", plan_id=result["plan_id"])
    return {"first": first, "final": result, "first_calls": first_calls, "calls": faults.calls,
            "events": faults.events, "harness": runtime.snapshot(), "checker": checker(world, case)}
