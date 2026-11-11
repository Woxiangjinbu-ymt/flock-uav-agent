"""Opt-in deterministic search at an R2 public-progress model boundary.

The baseline tool schema is unchanged. This module reads only public agent
memory and normal agent API responses; it never reads a scene or checker.
"""
from __future__ import annotations

from collections import Counter
import time
import uuid

from langchain_core.messages import ToolMessage

from coordinated_search_r2 import MAX_ROUNDS, PublicSearchPolicy
from coordinated_search_trigger_r2 import decide_public_search_trigger
from task_evidence_r2 import TaskEvidenceLedgerR2, TaskEvidenceR2Middleware, public_progress


PROGRESS_EVERY = 10


class TaskEvidenceSearchLedger(TaskEvidenceLedgerR2):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.search_runs: list[dict] = []

    def snapshot(self):
        result = super().snapshot()
        result["task_evidence"]["coordinated_search_runs"] = list(self.search_runs)
        return result


class TaskEvidenceSearchMiddleware(TaskEvidenceR2Middleware):
    """Run once after a complete, fresh public-progress tool receipt."""

    def __init__(self, ledger, runner, started_monotonic: float):
        super().__init__(ledger, "evidence_r2")
        self.variant = "evidence_r2_search"
        self.runner = runner
        self.started_monotonic = started_monotonic
        self._fired = False
        self._last_checked_evidence_id = None

    def _candidate(self, request):
        if self._fired or self.ledger._active or not self.ledger.progress_observations:
            return None
        calls = {call["id"] for message in request.messages
                 for call in getattr(message, "tool_calls", [])}
        replies = {message.tool_call_id for message in request.messages
                   if isinstance(message, ToolMessage)}
        if not calls <= replies:
            return None
        observation = self.ledger.progress_observations[-1]
        evidence_id = observation["evidence_id"]
        if evidence_id == self._last_checked_evidence_id:
            return None
        self._last_checked_evidence_id = evidence_id
        if not any(item["freshness"] == "observed" and
                   item["observation"] and
                   item["observation"]["evidence_id"] == evidence_id
                   for item in self.ledger.aggregate_projection()):
            return None
        elapsed = time.monotonic() - self.started_monotonic
        decision = decide_public_search_trigger(
            enabled=True, task_content=self.ledger.command,
            progress_percent=observation["progress_percentage"], elapsed_s=elapsed)
        if not decision.activate:
            return None
        return {"progress_evidence_id": evidence_id,
                "public_progress_percent": observation["progress_percentage"],
                "elapsed_s": round(elapsed, 3), "remaining_s": round(500 - elapsed, 3),
                "trigger_sequence": observation["end_sequence"]}

    def _model_request(self, request):
        try:
            candidate = self._candidate(request)
            if candidate:
                self._fired = True
                run = {"trigger": candidate, "status": "started"}
                self.ledger.search_runs.append(run)
                try:
                    result = self.runner(candidate, run)
                    run.update(result if isinstance(result, dict) else {"status": "no_result"})
                except Exception as exc:
                    run.update(status="error", error_type=type(exc).__name__)
                    self._error(exc)
        except Exception as exc:
            self._error(exc)
        return super()._model_request(request)


def run_public_search(agent, ledger, candidate: dict, run: dict) -> dict:
    """Execute direct public API calls and append their real receipts to R2."""
    started = time.monotonic()
    deadline = agent._public_search_task_started + 500.0
    api_calls = Counter()

    def observe(name, args, invoke):
        evidence_id = ledger.begin_tool(name, args, f"coordinated-search-{uuid.uuid4().hex[:12]}")
        try:
            result = invoke()
        except BaseException as exc:
            ledger.end_tool(evidence_id, error=exc)
            raise
        ledger.end_tool(evidence_id, result)
        api_calls[name] += 1
        return result

    if agent.blackboard.session_id != ledger.index.session_id:
        return {"status": "public_blackboard_session_mismatch", "attempt_count": 0,
                "elapsed_s": round(time.monotonic() - started, 3)}
    obstacles = agent.blackboard.full()["obstacles"]
    if not obstacles:
        return {"status": "empty_public_obstacle_blackboard", "attempt_count": 0,
                "elapsed_s": round(time.monotonic() - started, 3)}
    drones = observe("list_drones", {}, agent.client.list_drones)
    policy = PublicSearchPolicy(drones, obstacles)
    progress = observe("get_mission_progress", {}, lambda: public_progress(
        agent.client.get_current_session(), ledger.index.session_id))
    if "error" in progress or progress["progress_percentage"] > 95:
        return {"status": "progress_unavailable_or_already_met", "attempt_count": 0,
                "elapsed_s": round(time.monotonic() - started, 3),
                "final_public_progress_percent": progress.get("progress_percentage")}

    airborne = {str(drone["id"]) for drone in drones
                if float(drone["position"]["z"]) > 0}
    attempts = 0
    outcomes = Counter()
    new_obstacle_count = 0
    stop_reason = None
    for round_number in range(1, MAX_ROUNDS + 1):
        if stop_reason:
            break
        made_attempt = False
        for actor in policy.ids:
            if time.monotonic() >= deadline - 2.0:
                stop_reason = "remaining_budget_under_2s"
                break
            if progress["progress_percentage"] > 95:
                stop_reason = "public_progress_strictly_above_95"
                break
            choice = policy.choose(actor)
            if choice is None:
                continue
            index, destination, _ = choice
            if actor not in airborne:
                takeoff = observe("take_off", {"drone_id": actor, "altitude": 20.0},
                                  lambda actor=actor: agent.client.take_off(actor, 20.0))
                if takeoff.get("status") != "success":
                    policy.disable(actor)
                    continue
                airborne.add(actor)
            move_args = {"drone_id": actor, "x": destination[0],
                         "y": destination[1], "z": 20.0}
            move = observe("move_to", move_args,
                           lambda actor=actor, point=destination:
                           agent.client.move_to(actor, point[0], point[1], 20.0))
            status = move.get("status")
            if status == "success":
                outcome = "completed"
            elif status == "blocked" or (status == "error" and
                    "obstacle" in str(move.get("message", "")).lower()):
                outcome = "blocked"
            else:
                return {"status": "unexpected_move_receipt", "attempt_count": attempts,
                        "api_calls": dict(api_calls),
                        "elapsed_s": round(time.monotonic() - started, 3)}
            state = observe("get_drone_status", {"drone_id": actor},
                            lambda actor=actor: agent.client.get_drone_status(actor))
            nearby = observe("get_nearby_entities", {"drone_id": actor},
                             lambda actor=actor: agent.client.get_nearby_entities(actor))
            agent.blackboard.ingest_nearby(actor, nearby)
            actual_xy = (float(state["position"]["x"]), float(state["position"]["y"]))
            attempts += 1
            made_attempt = True
            outcomes[outcome] += 1
            new_obstacle_count += len(policy.accept(actor, index, outcome, actual_xy,
                                                    nearby["obstacles"], round_number=round_number,
                                                    sequence=attempts))
            if attempts % PROGRESS_EVERY == 0:
                progress = observe("get_mission_progress", {}, lambda: public_progress(
                    agent.client.get_current_session(), ledger.index.session_id))
                if "error" in progress:
                    stop_reason = "public_progress_unavailable"
                    break
                if progress["progress_percentage"] > 95:
                    stop_reason = "public_progress_strictly_above_95"
                    break
        if not made_attempt and not stop_reason:
            stop_reason = "no_public_candidate"
    if stop_reason is None:
        stop_reason = "round_limit"
    if attempts % PROGRESS_EVERY and time.monotonic() < deadline - 1.0:
        progress = observe("get_mission_progress", {}, lambda: public_progress(
            agent.client.get_current_session(), ledger.index.session_id))
    run.update({"api_calls": dict(api_calls), "attempt_count": attempts,
                "outcomes": dict(outcomes), "new_public_obstacles": new_obstacle_count,
                "final_public_progress_percent": progress.get("progress_percentage"),
                "elapsed_s": round(time.monotonic() - started, 3)})
    return {**run, "status": stop_reason}
