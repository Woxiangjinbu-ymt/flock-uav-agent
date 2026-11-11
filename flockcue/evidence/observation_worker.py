"""Opt-in, bounded observation worker on top of the R2 evidence ledger.

The main agent keeps its original tool schema. A worker may run once after a
public-receipt search stall. Its plan is never target evidence: only actual
sensor and movement tool replies enter the shared R2 ledger.
"""
from __future__ import annotations

import json
import math
import time
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from task_evidence_r2 import TaskEvidenceLedgerR2, TaskEvidenceR2Middleware


SENSING_MOVES = {
    "navigate_to_and_sense", "move_along_path_and_sense",
    "move_to_and_sense", "move_towards_and_sense",
}
ACTION_MARKERS = ("move", "navigate", "take_off", "land")
KNOWLEDGE_RANK = {"unknown": 0, "reported_only": 1, "localized": 2}
STALL_MOVES = 8
MIN_MAIN_TIME_SECONDS = 180
MAX_WORKER_MOVES = 3


class TaskEvidenceWorkerLedger(TaskEvidenceLedgerR2):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.worker_runs = []

    def snapshot(self):
        result = super().snapshot()
        result["task_evidence"]["observation_worker_runs"] = list(self.worker_runs)
        return result


class TaskEvidenceWorkerMiddleware(TaskEvidenceR2Middleware):
    """Run one worker at a complete model-request boundary after a strict stall."""

    def __init__(self, ledger, runner, started_monotonic):
        super().__init__(ledger, "evidence_r2")
        self.variant = "evidence_r2_worker"
        self.runner = runner
        self.started_monotonic = started_monotonic
        self._last_end_sequence = 0
        self._last_visits = self._visits()
        self._counts = {}
        self._extra_action_seen = False
        self._fired = False

    def _visits(self):
        return {item["id"]: item for item in self.ledger.projection()
                if item["relation"] == "visit" and item.get("actor_id")}

    def _candidate(self):
        if self._fired or self.ledger._active:
            return None
        visits = self._visits()
        required = {item["actor_id"] for item in visits.values()}
        current_events = [event for event in self.ledger._events
                          if event["end_sequence"] is not None
                          and event["end_sequence"] > self._last_end_sequence]
        advanced = set()
        for key, item in visits.items():
            previous = self._last_visits.get(key)
            if previous is None:
                continue
            if (KNOWLEDGE_RANK.get(item["knowledge_state"], -1)
                    > KNOWLEDGE_RANK.get(previous["knowledge_state"], -1)
                    or previous["execution_state"] != "evidenced"
                    and item["execution_state"] == "evidenced"):
                advanced.add(item["actor_id"])
        for actor in advanced:
            self._counts[actor] = 0
        for event in current_events:
            actor = event["args"].get("drone_id")
            if (actor and actor not in required
                    and any(part in event["tool_name"] for part in ACTION_MARKERS)):
                self._extra_action_seen = True
            if (actor in required and actor not in advanced
                    and event["tool_name"] in SENSING_MOVES
                    and event["status"] == "completed"):
                self._counts[actor] = self._counts.get(actor, 0) + 1
        self._last_end_sequence = max(
            [self._last_end_sequence, *(event["end_sequence"] for event in current_events)])
        self._last_visits = visits
        if self._extra_action_seen or time.monotonic() - self.started_monotonic > (
                500 - MIN_MAIN_TIME_SECONDS):
            return None
        for actor in sorted(required):
            own = [item for item in visits.values() if item["actor_id"] == actor]
            unresolved = [item["target_name"] for item in own
                          if item["knowledge_state"] != "localized"]
            ready = [item for item in own if item["knowledge_state"] == "localized"
                     and item["execution_state"] != "evidenced"]
            if unresolved and not ready and self._counts.get(actor, 0) >= STALL_MOVES:
                return {"required_actor_id": actor, "unresolved_targets": unresolved,
                        "completed_sensing_moves_since_progress": self._counts[actor],
                        "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 3),
                        "trigger_sequence": self._last_end_sequence}
        return None

    def _model_request(self, request):
        try:
            candidate = self._candidate()
            if candidate:
                self._fired = True
                run = {"trigger": candidate, "status": "started", "tool_receipts": []}
                self.ledger.worker_runs.append(run)
                try:
                    result = self.runner(candidate, run)
                    run.update(result if isinstance(result, dict) else {"status": "no_result"})
                except Exception as exc:
                    run.update(status="error", error_type=type(exc).__name__)
                    self._error(exc)
        except Exception as exc:
            self._error(exc)
        return super()._model_request(request)


def _position(drone):
    value = drone.get("position")
    if isinstance(value, dict):
        return {axis: float(value.get(axis, 0)) for axis in ("x", "y", "z")}
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return dict(zip(("x", "y", "z"), map(float, value[:3])))
    return None


def _plan_from_message(message):
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content = "\n".join(str(block.get("text", "")) for block in content
                            if isinstance(block, dict))
    content = str(content).strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        plan = json.loads(content)
    except (TypeError, ValueError):
        return None
    return plan if isinstance(plan, dict) else None


def _valid_plan(plan, drones, required_actor_id):
    if not isinstance(plan, dict):
        return None
    by_id = {str(drone.get("id")): drone for drone in drones if drone.get("id")}
    actor = str(plan.get("drone_id", ""))
    if actor == required_actor_id or actor not in by_id:
        return None
    drone = by_id[actor]
    if not isinstance(drone.get("battery_level"), (int, float)) or drone["battery_level"] < 15:
        return None
    points = plan.get("waypoints")
    if not isinstance(points, list) or not 1 <= len(points) <= MAX_WORKER_MOVES:
        return None
    positions = [_position(item) for item in drones]
    positions = [item for item in positions if item]
    if not positions:
        return None
    bounds = {axis: (max(0.0, min(p[axis] for p in positions) - 200),
                     max(p[axis] for p in positions) + 200) for axis in ("x", "y")}
    clean = []
    for item in points:
        if not isinstance(item, dict):
            return None
        try:
            x, y = float(item["x"]), float(item["y"])
        except (TypeError, ValueError, KeyError):
            return None
        if (not math.isfinite(x) or not math.isfinite(y)
                or not bounds["x"][0] <= x <= bounds["x"][1]
                or not bounds["y"][0] <= y <= bounds["y"][1]):
            return None
        clean.append({"x": x, "y": y, "z": max(12.0, _position(drone)["z"])})
    return actor, clean


def run_observation_worker(agent, ledger, candidate, run):
    """One independent planning call; sensor and flight receipts are authoritative."""
    call_number = 0

    def observe(name, args, invoke):
        nonlocal call_number
        call_number += 1
        eid = ledger.begin_tool(name, args, f"observation-worker-{uuid.uuid4().hex[:12]}")
        try:
            result = invoke()
        except BaseException as exc:
            ledger.end_tool(eid, error=exc)
            raise
        ledger.end_tool(eid, result)
        event = next(item for item in ledger._events if item["evidence_id"] == eid)
        run["tool_receipts"].append({"evidence_id": eid, "tool_name": name,
                                     "actor_id": args.get("drone_id"), "status": event["status"]})
        return result

    drones = observe("list_drones", {}, agent.client.list_drones)
    by_name = {tool.name: tool for tool in agent.tools}
    sense_args = {"detail": "summary", "include_blackboard": False}
    observe("sense_nearby_entities", sense_args,
            lambda: by_name["sense_nearby_entities"].invoke(
                {"input_json": json.dumps(sense_args)}))
    unresolved = set(candidate["unresolved_targets"])
    if not any(item["target_name"] in unresolved and item["knowledge_state"] != "localized"
               for item in ledger.projection() if item["relation"] == "visit"):
        return {"status": "localized_by_static_sensing", "worker_move_count": 0}
    compact = [{"id": str(item.get("id")), "name": item.get("name"),
                "position": _position(item), "battery_level": item.get("battery_level"),
                "perceived_radius": item.get("perceived_radius")}
               for item in drones if item.get("id")]
    main_recent = [{"tool": event["tool_name"], "args": event["args"]}
                   for event in ledger._events[-18:] if event["args"].get("drone_id")]
    prompt = {
        "goal": "Find, but do not complete, the unresolved named target(s).",
        "unresolved_target_names": candidate["unresolved_targets"],
        "required_actor_id_not_for_worker": candidate["required_actor_id"],
        "available_drones": compact,
        "recent_actions": main_recent,
        "blackboard_targets": agent.blackboard.summary()["targets"][:30],
        "output_schema": {"drone_id": "an extra drone id", "waypoints": [{"x": 0, "y": 0}]},
    }
    agent._in_observation_worker = True
    try:
        response = agent.llm.invoke([
            SystemMessage(content=(
                "You are a bounded UAV observation worker. Pick ONE non-required drone and 1-3 "
                "distinct, legal exploratory XY waypoints to reveal the named target with local sensors. "
                "Use only given public observations. Do not invent a target position, target ID, or mission completion. "
                "Return only a JSON object with drone_id and waypoints; no prose.")),
            HumanMessage(content=json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))),
        ])
    finally:
        agent._in_observation_worker = False
    from uav_agent import token_usage_from_message, merge_token_usage
    usage = token_usage_from_message(response)
    if usage:
        merge_token_usage(agent._observation_worker_token_usage, usage)
    plan = _valid_plan(_plan_from_message(response), drones, candidate["required_actor_id"])
    if not plan:
        return {"status": "invalid_or_no_plan", "worker_move_count": 0}
    actor, points = plan
    worker = next(item for item in drones if str(item.get("id")) == actor)
    position = _position(worker)
    run["worker_actor_id"] = actor
    run["planned_waypoints"] = points
    if position["z"] < 1:
        args = {"drone_id": actor, "altitude": points[0]["z"]}
        observe("take_off", args, lambda: by_name["take_off"].invoke(
            {"input_json": json.dumps(args)}))
        if run["tool_receipts"][-1]["status"] != "completed":
            return {"status": "takeoff_not_completed", "worker_move_count": 0}
    moves = 0
    for point in points:
        if time.monotonic() - agent._observation_worker_task_started > 500 - MIN_MAIN_TIME_SECONDS:
            break
        args = {"drone_id": actor, **point, "detail": "summary"}
        observe("navigate_to_and_sense", args,
                lambda args=args: by_name["navigate_to_and_sense"].invoke(
                    {"input_json": json.dumps(args)}))
        moves += 1
        if not any(item["target_name"] in unresolved and item["knowledge_state"] != "localized"
                   for item in ledger.projection() if item["relation"] == "visit"):
            break
    found = [item["target_name"] for item in ledger.projection()
             if item["relation"] == "visit" and item["target_name"] in unresolved
             and item["knowledge_state"] == "localized"]
    return {"status": "localized" if found else "searched_without_localization",
            "worker_move_count": moves, "localized_targets": found}
