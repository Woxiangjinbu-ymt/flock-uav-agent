"""Two narrow skills with identical resumable generators for every experiment arm."""
from __future__ import annotations

import math
import re
import copy

from command_results import command_outcome
from coordinated_search_r2 import MAX_ROUNDS, PublicSearchPolicy
from harness_core import Checkpoint, DecisionFrame, PublicCall, SkillExit
from observed_target_search import ObservedTargetFollowup
from search_progress import PublicSearchProgress

PROGRESS_EVERY = 10

# Only the public move_to endpoint rejection proves that changing the origin
# cannot fix this destination. Generic blockage also includes path collisions.
_ENDPOINT_CONFLICT = re.compile(
    r"Target position conflicts with obstacle: Position conflicts with "
    r"(?:circle|ellipse|polygon) obstacle '[^\n]+'")


def _endpoint_conflict(action, actor):
    receipt = action.get("receipt")
    if (action.get("tool") != "move_to" or action.get("args", {}).get("drone_id") != actor
            or not isinstance(receipt, dict) or receipt.get("command") != "move_to"
            or receipt.get("drone_id") != actor or receipt.get("status") != "error"
            or command_outcome(receipt) != "blocked"):
        return False
    message = receipt.get("message")
    return isinstance(message, str) and _ENDPOINT_CONFLICT.fullmatch(message) is not None


def _observe_static_obstacles(board, observed):
    """Reject observed violations; unseen edits still require writer isolation.

    Compare normalized public geometry, not observation metadata or names. Keep
    deep copies because the blackboard replaces/updates its entries in place.
    A locally empty nearby receipt does not delete blackboard entries.
    """
    current = {}
    for item in board.full()["obstacles"]:
        facts = {**item, **item.get("facts", {})}
        current[str(item["id"])] = copy.deepcopy({
            "type": facts.get("type") or facts.get("entity_type"),
            **{k: facts.get(k) for k in ("position", "coordinates", "radius", "width", "length",
                                       "vertices", "height", "dimensions")}})
    if any(oid not in current or current[oid] != geometry for oid, geometry in observed.items()):
        raise SkillExit("shared_endpoint_static_obstacle_contract_changed")
    observed.update(current)


def simple_plan(command):
    number = r"(\d+(?:\.\d+)?)"
    patterns = [
        (rf"Drone Drone (\d+) takes off to {number} meters altitude, then rotates to face heading {number} degrees and hovers\.", "rotate"),
        (rf"Drone Drone (\d+) should take off to {number} meters, then climb to {number} meters altitude and hover\.", "climb"),
        (rf"Command drone Drone (\d+) to take off to an altitude of {number} meters and hover in place\.", "hover"),
    ]
    for pattern, family in patterns:
        match = re.fullmatch(pattern, command)
        if match:
            values = match.groups()
            steps = [{"tool": "take_off", "args": {"altitude": float(values[1])}}]
            if family == "rotate":
                steps.append({"tool": "rotate", "args": {"heading": float(values[2])}})
            elif family == "climb":
                steps.append({"tool": "change_altitude", "args": {"altitude": float(values[2])}})
            steps.append({"tool": "hover", "args": {}})
            for step in steps:
                for key, value in step["args"].items():
                    if not math.isfinite(value) or (key == "altitude" and value <= 0) or (key == "heading" and not 0 <= value <= 360):
                        return None
            return {"actor_name": f"Drone {values[0]}", "steps": steps}
    return None


def search_supported(command):
    # Full command match: a convenient substring must not erase extra obligations.
    return " ".join(command.lower().split()) == (
        "take off necessary drones and assigned them to all targets (within task radius) "
        "until the session task progress exceeds 95%.")


def _state(payload, actor):
    if not isinstance(payload, dict) or str(payload.get("id")) != actor:
        raise SkillExit("missing_or_wrong_actor_state")
    position = payload.get("position")
    if not isinstance(position, dict):
        raise SkillExit("missing_public_position")
    values = [position.get(k) for k in ("x", "y", "z")]
    values.append(payload.get("battery_level"))
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise SkillExit("invalid_public_state")
    return payload


def _check_progress(value):
    p = value.get("progress_percentage") if isinstance(value, dict) else None
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 100:
        raise SkillExit("public_progress_unavailable")
    return p


def _checked_action(call, frame):
    result = yield call
    frame.state["last_action"] = {"tool": call.tool, "args": call.args, "receipt": result}
    if isinstance(result, dict) and result.get("drone_id") not in (None, call.args.get("drone_id")):
        raise SkillExit("wrong_actor_receipt")
    outcome = command_outcome(result)
    if outcome == "unknown":
        state = yield PublicCall("get_drone_status", {"drone_id": call.args["drone_id"]})
        frame.state["unknown_effect_observation"] = state
        raise SkillExit("unknown_action_effect_observed_do_not_replay")
    return outcome


def simple_chain(frame, compiled):
    drones = yield PublicCall("list_drones")
    if not isinstance(drones, list):
        raise SkillExit("public_fleet_unavailable")
    candidates = [str(d["id"]) for d in drones if isinstance(d, dict) and d.get("name") == compiled["actor_name"] and d.get("id")]
    if len(candidates) != 1:
        raise SkillExit("ambiguous_or_missing_actor_binding")
    actor = candidates[0]
    frame.actor_bindings[compiled["actor_name"]] = actor
    state = _state((yield PublicCall("get_drone_status", {"drone_id": actor})), actor)
    if state.get("status") not in {"idle", "landed"} or state["position"]["z"] != 0:
        raise SkillExit("takeoff_precondition_changed")
    expected_z = state["position"]["z"]
    expected_xy = tuple(state["position"][k] for k in ("x", "y"))
    expected_heading = None
    for step in compiled["steps"]:
        if frame.cursor:
            # All arms make this same public read, including uninterrupted ones.
            # Model latency or out-of-band world changes must not make a paused
            # frame reuse a stale actor state.
            fresh = _state((yield PublicCall("get_drone_status", {"drone_id": actor})), actor)
            if (fresh["position"] != state["position"] or fresh.get("heading") != state.get("heading")
                    or fresh.get("status") != state.get("status")):
                raise SkillExit("actor_state_changed_between_units")
            state = fresh
        if state["battery_level"] <= 0:
            raise SkillExit("public_battery_unavailable_for_action")
        args = {"drone_id": actor, **step["args"]}
        outcome = yield from _checked_action(PublicCall(step["tool"], args, True), frame)
        if outcome != "completed":
            raise SkillExit(f"simple_action_{outcome}")
        state = _state((yield PublicCall("get_drone_status", {"drone_id": actor})), actor)
        expected_z = step["args"].get("altitude", expected_z)
        expected_heading = step["args"].get("heading", expected_heading)
        actual = state["position"]
        if any(abs(actual[k] - want) > 1e-6 for k, want in zip(("x", "y", "z"), (*expected_xy, expected_z))):
            raise SkillExit("unexpected_public_position")
        if expected_heading is not None:
            heading = state.get("heading")
            if (isinstance(heading, bool) or not isinstance(heading, (int, float)) or not math.isfinite(heading)
                    or abs((heading - expected_heading + 180) % 360 - 180) > 1e-6):
                raise SkillExit("unexpected_public_heading")
        if step["tool"] == "hover" and state.get("status") != "hovering":
            raise SkillExit("hover_not_publicly_confirmed")
        frame.cursor += 1
        frame.state.update(last_verified_state=state, remaining_steps=compiled["steps"][frame.cursor:])
        if frame.cursor < len(compiled["steps"]):
            yield Checkpoint()
    frame.state["public_stop_condition"] = "simple_sequence_and_final_state_supported"


def _entry_fleet(drones):
    if not isinstance(drones, list) or not drones:
        raise SkillExit("public_fleet_unavailable")
    ids, names = set(), set()
    for drone in drones:
        if (not isinstance(drone, dict) or not drone.get("id")
                or not isinstance(drone.get("name"), str) or not drone["name"]):
            raise SkillExit("invalid_public_fleet")
        actor = str(drone["id"])
        _state(drone, actor)
        if actor in ids or drone["name"] in names:
            raise SkillExit("ambiguous_fleet_binding")
        ids.add(actor)
        names.add(drone["name"])
    return sorted(ids)


def _valid_entry_perception(value):
    # The real public nearby response has all three arrays. An error, missing
    # field, or malformed entity must never be treated as a successful empty read.
    if (not isinstance(value, dict) or value.get("status") not in (None, "success")
            or (value.get("success") is not None and value.get("success") is not True) or value.get("error")
            or value.get("harness_transport_error")):
        return False
    return all(isinstance(value.get(kind), list)
               and all(isinstance(entity, dict) and entity.get("id") for entity in value[kind])
               for kind in ("obstacles", "targets", "drones"))


def public_search(frame, board, *, revision="legacy", static_obstacle_contract=False):
    followup = ObservedTargetFollowup() if revision in {"observed_target_v7", "search_progress_v8", "search_confirm_v9"} else None
    progress_gate = PublicSearchProgress(frame.scene_id, PROGRESS_EVERY) if revision == "search_progress_v8" else None
    confirm_only = revision == "search_confirm_v9"
    if confirm_only:
        frame.state["search_confirmation"] = {"revision": revision, "cached_progress_minimum": 95,
            "stagnation_handoff_enabled": False, "extra_reads": [],
            "scope": "public_session_aggregate_not_full_task_verdict"}

    def checked_progress(receipt):
        # v9 must establish scope for both the cached value and the extra read.
        # Its threshold does not alter v7 exploration, periodic reads or budgets.
        if confirm_only and (not isinstance(receipt, dict) or receipt.get("error")
                or receipt.get("harness_transport_error") or receipt.get("status") not in (None, "success")
                or (receipt.get("success") is not None and receipt["success"] is not True)
                or (receipt.get("partial_success") is not None and receipt["partial_success"] is not False)
                or receipt.get("session_id") != str(frame.scene_id)
                or receipt.get("source_endpoint") != "/sessions/current"
                or receipt.get("scope") != "public_session_aggregate_not_full_task_verdict"):
            raise SkillExit("public_progress_unavailable")
        return _check_progress(receipt)

    def gate_read(receipt, trigger, attempts, policy=None, states=None, shared_endpoints=None):
        if progress_gate is None:
            return
        try:
            decision = progress_gate.observe(receipt, frame.evidence_refs[-1], attempts=attempts,
                trigger=trigger, fixed_target_ids=followup.targets,
                pending_target_ids=followup.pending_ids(states, policy, shared_endpoints)
                if policy is not None else ())
        except ValueError as exc:
            raise SkillExit(str(exc)) from exc
        frame.state["search_progress"] = progress_gate.snapshot()
        if decision == "handoff":
            raise SkillExit("search_progress_stalled")
    if followup is not None:
        frame.state["observed_target_followup"] = followup.snapshot()
    shared = revision in {"shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"}
    if shared and static_obstacle_contract is not True:
        raise SkillExit("shared_endpoint_requires_static_obstacle_contract")
    observed_geometry = {}
    if shared:
        _observe_static_obstacles(board, observed_geometry)
        frame.state["shared_endpoint_scope"] = {
            "contract": "static_obstacles_no_external_writers",
            "plan_id": frame.plan_id, "scene_id": frame.scene_id, "scene_epoch": frame.scene_epoch,
            "altitude": 20.0, "match": "exact_xyz", "source": "verified_public_move_to_endpoint_error"}
    receipt = yield PublicCall("get_mission_progress")
    progress = checked_progress(receipt)
    gate_read(receipt, "entry", 0)
    frame.state.update(progress_percent=progress, attempts=0, outcomes={}, new_public_obstacles=0)
    if progress > 95:
        frame.state["public_stop_condition"] = "session_progress_strictly_above_95"
        return
    obstacles = board.full()["obstacles"]
    observe_entry = not obstacles and revision in {"entry_contract_v2", "flight_state_v3", "blocked_candidate_v4", "shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"}
    if not obstacles and not observe_entry:
        raise SkillExit("empty_public_obstacle_blackboard")
    drones = yield PublicCall("list_drones")
    if observe_entry:
        actors = _entry_fleet(drones)
        frame.actor_bindings = {str(d["name"]): str(d["id"]) for d in drones}
        frame.state["entry_observations"] = []
        # One public read per returned actor, no retries and no motion. All calls
        # remain under the runtime's scene/epoch, deadline and API-budget guards.
        for actor in actors:
            nearby = yield PublicCall("get_nearby_entities", {"drone_id": actor})
            valid = _valid_entry_perception(nearby)
            observation = {"actor": actor, "scene_id": frame.scene_id,
                           "scene_epoch": frame.scene_epoch,
                           "scope": "local_obstacle_observation",
                           "evidence_ref": frame.evidence_refs[-1],
                           "status": "invalid"}
            if valid:
                observation.update(status="valid_empty" if not nearby["obstacles"] else "valid_nonempty",
                                   obstacle_count=len(nearby["obstacles"]))
            frame.state["entry_observations"].append(observation)
            if not valid:
                raise SkillExit("entry_public_perception_unavailable")
            board.ingest_nearby(actor, nearby)
            if followup is not None:
                followup.observe(nearby, actor, frame.evidence_refs[-1])
                frame.state["observed_target_followup"] = followup.snapshot()
            if shared:
                _observe_static_obstacles(board, observed_geometry)
        frame.state["entry_observations_complete"] = True
        # Empty means no obstacles in these local observations, never a global
        # free-space claim. The unchanged search still handles blocked motion.
        obstacles = board.full()["obstacles"]
    policy = PublicSearchPolicy(drones, obstacles)
    # This memory belongs to this generator/plan, at the fixed search altitude.
    # It survives pauses and origin changes; absent nearby geometry is not a
    # retraction. Runtime epoch/external-action guards invalidate the whole plan.
    # Dynamic obstacle revisions need a separate contract, as does the existing
    # geometry cache; no TTL or inferred world-wide obstacle region is added here.
    blocked_candidates = {}
    shared_endpoints = {}
    states = {str(d["id"]): _state(d, str(d["id"])) for d in drones}
    frame.actor_bindings = {str(d["name"]): str(d["id"]) for d in drones}
    if len(frame.actor_bindings) != len(drones):
        raise SkillExit("ambiguous_fleet_names")
    airborne = {actor for actor, state in states.items() if state["position"]["z"] > 0}
    attempts = 0
    for round_number in range(1, MAX_ROUNDS + 1):
        if progress > 95:
            break
        made_attempt = False
        for actor in policy.ids:
            if progress > 95:
                break
            fresh = _state((yield PublicCall("get_drone_status", {"drone_id": actor})), actor)
            if shared:
                _observe_static_obstacles(board, observed_geometry)
            if (tuple(fresh["position"][k] for k in ("x", "y")) != policy.xy[actor]
                    or fresh["position"]["z"] != states[actor]["position"]["z"]):
                raise SkillExit("search_actor_state_changed_between_units")
            states[actor] = fresh
            if shared:
                # This service's point-collision predicate is actor-independent.
                # Share only the exact requested XYZ, never a path or region.
                excluded = {i for i, point in enumerate(policy.candidates)
                            if (*point, 20.0) in shared_endpoints}
                if followup is not None:
                    # Directed points remain stable indices for accept/evidence,
                    # but may never leak into ordinary exploration or retries.
                    excluded.update(followup.directed_indices)
                    tried_destinations = {policy.candidates[i] for a, start, i in policy.tried_edges
                                          if a == actor and start == policy.xy[actor]}
                    excluded.update(i for i, point in enumerate(policy.candidates)
                                    if point in tried_destinations)
                choice = policy.choose(actor, excluded_indices=excluded)
            else:
                choice = (policy.choose(actor, excluded_indices={i for a, i in blocked_candidates if a == actor})
                          if revision == "blocked_candidate_v4" else policy.choose(actor))
            if choice is None and followup is None:
                continue
            if states[actor]["battery_level"] <= 0:
                policy.disable(actor)
                continue
            if followup is not None:
                directed = followup.choose(actor, fresh, policy, shared_endpoints, choice,
                    round_number=round_number, sequence=attempts + 1)
                if directed is not None:
                    choice = directed
                frame.state["observed_target_followup"] = followup.snapshot()
            if choice is None:
                continue
            index, destination, _ = choice
            needs_takeoff = actor not in airborne
            if revision in {"flight_state_v3", "blocked_candidate_v4", "shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"}:
                # Public command legality is determined by the latest status,
                # not altitude or a cached successful takeoff. In particular,
                # imported scenarios can expose idle actors above ground.
                status = fresh.get("status")
                if not isinstance(status, str) or status not in {"idle", "ready", "hovering", "flying", "moving"}:
                    raise SkillExit("search_actor_flight_state_unavailable")
                needs_takeoff = status in {"idle", "ready"}
            if needs_takeoff:
                outcome = yield from _checked_action(PublicCall("take_off", {"drone_id": actor, "altitude": 20.0}, True), frame)
                if outcome != "completed":
                    policy.disable(actor)
                    frame.repairs += 1
                    if frame.repairs > frame.repair_budget:
                        raise SkillExit("local_repair_budget_exhausted")
                    yield Checkpoint("actor_takeoff_failed_disabled", failed=True)
                    continue
                states[actor] = state = _state((yield PublicCall("get_drone_status", {"drone_id": actor})), actor)
                if (tuple(state["position"][k] for k in ("x", "y")) != policy.xy[actor]
                        or abs(state["position"]["z"] - 20.0) > 1e-6):
                    raise SkillExit("search_takeoff_effect_mismatch")
                if revision in {"flight_state_v3", "blocked_candidate_v4", "shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"} and state.get("status") not in ("hovering", "flying", "moving"):
                    raise SkillExit("search_takeoff_state_mismatch")
                airborne.add(actor)
            if shared:
                _observe_static_obstacles(board, observed_geometry)
            before_move_position = dict(states[actor]["position"])
            positive_names_before = set(followup.positive_names) if progress_gate is not None or confirm_only else set()
            outcome = yield from _checked_action(PublicCall("move_to", {
                "drone_id": actor, "x": destination[0], "y": destination[1], "z": 20.0}, True), frame)
            if followup is not None:
                followup.record_action(index, outcome, frame.evidence_refs[-1])
                followup.observe_move(frame.state["last_action"], actor, frame.evidence_refs[-1])
                frame.state["observed_target_followup"] = followup.snapshot()
            # Capture the action ref now: subsequent state/perception reads have
            # their own refs and must not be cited as the collision receipt.
            endpoint_failure = None
            if revision in {"blocked_candidate_v4", "shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"} and _endpoint_conflict(frame.state["last_action"], actor):
                endpoint_failure = {"actor": actor, "candidate_index": index,
                                    "destination": [*destination, 20.0],
                                    "evidence_ref": frame.evidence_refs[-1],
                                    "action": copy.deepcopy(frame.state["last_action"]),
                                    "round": round_number, "sequence": attempts + 1}
            if outcome not in {"completed", "blocked"}:
                raise SkillExit(f"search_move_{outcome}")
            states[actor] = state = _state((yield PublicCall("get_drone_status", {"drone_id": actor})), actor)
            nearby = yield PublicCall("get_nearby_entities", {"drone_id": actor})
            if not isinstance(nearby, dict) or not isinstance(nearby.get("obstacles"), list):
                raise SkillExit("public_perception_unavailable")
            board.ingest_nearby(actor, nearby)
            if followup is not None:
                followup.observe(nearby, actor, frame.evidence_refs[-1])
                frame.state["observed_target_followup"] = followup.snapshot()
            if shared:
                _observe_static_obstacles(board, observed_geometry)
            actual_xy = (state["position"]["x"], state["position"]["y"])
            if outcome == "completed" and (math.dist(actual_xy, destination) > 1e-6 or abs(state["position"]["z"] - 20) > 1e-6):
                raise SkillExit("search_move_effect_mismatch")
            attempts += 1
            frame.cursor = attempts
            made_attempt = True
            new_obstacles = policy.accept(actor, index, outcome, actual_xy, nearby["obstacles"],
                                          round_number=round_number, sequence=attempts)
            if endpoint_failure is not None:
                if any(abs(state["position"][k] - before_move_position[k]) > 1e-6 for k in ("x", "y", "z")):
                    raise SkillExit("search_blocked_move_effect_mismatch")
                blocked_candidates[(actor, index)] = endpoint_failure
                frame.state["blocked_endpoints"] = list(blocked_candidates.values())
                if shared:
                    shared_endpoints.setdefault(tuple(endpoint_failure["destination"]), endpoint_failure)
                    frame.state["shared_blocked_endpoints"] = list(shared_endpoints.values())
            if outcome == "blocked":
                frame.repairs += 1
                if frame.repairs > frame.repair_budget:
                    raise SkillExit("local_repair_budget_exhausted")
            frame.state["outcomes"][outcome] = frame.state["outcomes"].get(outcome, 0) + 1
            frame.state["new_public_obstacles"] += len(new_obstacles)
            frame.state.update(attempts=attempts, round_number=round_number,
                               last_actor=actor, last_move_outcome=outcome)
            new_public_reach = (progress_gate is not None or confirm_only and progress >= 95) and bool(
                set(followup.positive_names) - positive_names_before)
            if attempts % PROGRESS_EVERY == 0 or new_public_reach:
                cached_progress = progress
                receipt = yield PublicCall("get_mission_progress")
                progress = checked_progress(receipt)
                frame.state["progress_percent"] = progress
                if confirm_only and new_public_reach and attempts % PROGRESS_EVERY:
                    frame.state["search_confirmation"]["extra_reads"].append({
                        "attempts": attempts, "cached_progress_percentage": cached_progress,
                        "progress_percentage": progress, "evidence_ref": frame.evidence_refs[-1],
                        "trigger": "new_trusted_reach_after_cached_progress_at_least_95"})
                gate_read(receipt, "periodic" if attempts % PROGRESS_EVERY == 0 else "new_public_reach",
                          attempts, policy, states, shared_endpoints)
            if progress <= 95:
                yield Checkpoint("blocked_candidate_handled" if outcome == "blocked" else "search_attempt_complete",
                                 failed=outcome == "blocked")
        if not made_attempt and progress <= 95:
            if revision in {"terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"}:
                # Exhausting candidates can occur between periodic progress
                # reads. Confirm once, through the same runtime budget/guards;
                # a missing read is never evidence that the task is complete.
                check = {"trigger": "no_public_candidate", "cached_progress_percent": progress,
                         "status": "awaiting_public_read"}
                frame.state["terminal_progress_check"] = check
                receipt = yield PublicCall("get_mission_progress")
                check.update(status="unavailable", evidence_ref=frame.evidence_refs[-1])
                if (not isinstance(receipt, dict) or receipt.get("error")
                        or receipt.get("harness_transport_error")
                        or receipt.get("status") not in (None, "success")
                        or (receipt.get("success") is not None and receipt["success"] is not True)
                        or (receipt.get("partial_success") is not None and receipt["partial_success"] is not False)
                        or receipt.get("session_id") != str(frame.scene_id)
                        or receipt.get("source_endpoint") != "/sessions/current"
                        or receipt.get("scope") != "public_session_aggregate_not_full_task_verdict"):
                    raise SkillExit("public_progress_unavailable")
                progress = checked_progress(receipt)
                frame.state["progress_percent"] = progress
                gate_read(receipt, "terminal", attempts, policy, states, shared_endpoints)
                check.update(status="confirmed", progress_percent=progress)
                if progress > 95:
                    frame.state["public_stop_condition"] = "session_progress_strictly_above_95"
                    return
            raise SkillExit("no_public_candidate")
    if attempts % PROGRESS_EVERY and (progress_gate is None and not confirm_only or progress <= 95):
        receipt = yield PublicCall("get_mission_progress")
        progress = checked_progress(receipt)
        frame.state["progress_percent"] = progress
        gate_read(receipt, "terminal", attempts, policy, states, shared_endpoints)
    if progress <= 95:
        raise SkillExit("round_limit_below_threshold")
    frame.state["public_stop_condition"] = "session_progress_strictly_above_95"


def make_skill_factory(board):
    def factory(name, plan_id, runtime):
        compiled = simple_plan(runtime.command)
        if name == "simple_chain" and compiled:
            steps = compiled["steps"]
            repair = "none; unknown action effect gets one state read then model fallback"
            stop = "ordered source actions plus public final state"
        elif name == "public_search" and search_supported(runtime.command):
            steps = [{"tool": "public_search_attempt", "repeat": "bounded_by_rounds_and_budget"}]
            repair = "replace blocked candidate; disable failed takeoff actor; never rewrite task threshold"
            stop = "public session progress > 95, not whole-task proof"
        else:
            raise ValueError("unsupported_skill_or_full_task_grammar")
        frame = DecisionFrame(plan_id, runtime.task_id, runtime.scene_id, runtime.scene_epoch, name,
                              [{"source_span": [0, len(runtime.command)], "text": runtime.command}], steps,
                              ["same_task_and_scene_epoch", "public_actor_binding", "verified_effect", "deadline"],
                              ["related_external_action", "unexpected_effect", "unhandled_failure", "budget_exhausted"],
                              repair, stop, runtime.deadline)
        return frame, (simple_chain(frame, compiled) if name == "simple_chain" else
                       public_search(frame, board, revision=runtime.revision,
                                     static_obstacle_contract=runtime.static_obstacle_contract))
    return factory
