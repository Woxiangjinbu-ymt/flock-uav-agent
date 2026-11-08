"""Entity and progress observations from documented public tool result fields."""

import math
from collections import defaultdict


def object_value(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _entity_groups(payload):
    for plural in ("targets", "obstacles", "drones"):
        for row in rows(payload.get(plural)):
            yield plural[:-1], row.get("id")


def observations(tool, result):
    """Yield typed, named entities. Maps are allowlisted to exclude metadata IDs."""
    payload = object_value(result)
    if tool == "list_drones":
        for row in rows(result):
            yield "drone", row.get("id")
    elif tool == "get_drone_status":
        yield "drone", payload.get("id")
    elif tool == "get_target_info":
        yield "target", payload.get("id")
    elif tool == "run_harness":
        state = object_value(payload.get("state"))
        followup = object_value(state.get("observed_target_followup"))
        for row in rows(followup.get("targets")):
            yield "target", row.get("target_id")
        for field in ("last_verified_state", "unknown_effect_observation"):
            yield "drone", object_value(state.get(field)).get("id")
    elif tool.endswith("_and_sense") or tool in (
            "sense_nearby_entities", "get_nearby_entities", "get_blackboard"):
        yield from _entity_groups(payload)
        for field in ("nearby", "blackboard"):
            yield from _entity_groups(object_value(payload.get(field)))
        for group in object_value(payload.get("per_drone")).values():
            yield from _entity_groups(object_value(group))


def progress_read(call):
    """Return the public session key and a finite percent, if any."""
    result = object_value(call["result"])
    if call["tool"] == "get_mission_progress":
        value = result.get("progress_percentage", result.get("progress_percent"))
    elif call["tool"] == "run_harness":
        state = object_value(result.get("state"))
        value = state.get("progress_percent")
        # A subprogram may expose scene identity through its entry observations.
        scenes = {r["scene_id"] for r in rows(state.get("entry_observations"))
                  if isinstance(r.get("scene_id"), str)}
        if len(scenes) == 1 and result.get("session_id") is None:
            return next(iter(scenes)), value
    else:
        return None
    return result.get("session_id"), value


def update_information(batch, known, progress, complete_history, emit, gaps):
    """Evaluate parallel reads against the same baseline, then merge facts."""
    discovered = set()
    readings = defaultdict(list)
    for call in batch:
        if not call["observation_ok"]:
            continue
        facts = {(kind, ident) for kind, ident in observations(call["tool"], call["result"])
                 if isinstance(ident, str) and ident}
        observers = defaultdict(set)
        payload = object_value(call["result"])
        for aircraft, view in object_value(payload.get("per_drone")).items():
            if isinstance(aircraft, str) and aircraft:
                for kind, ident in _entity_groups(object_value(view)):
                    if isinstance(ident, str) and ident:
                        observers[(kind, ident)].add(aircraft)
        actor = call["arguments"].get("drone_id")
        direct = set()
        if isinstance(actor, str) and actor:
            for layer in (payload, object_value(payload.get("nearby"))):
                direct.update((kind, ident) for kind, ident in _entity_groups(layer)
                              if isinstance(ident, str) and ident)
            if call["tool"] in {"get_drone_status", "get_target_info"}:
                direct.update(facts)
        for kind, ident in sorted(facts - known):
            if not complete_history:
                gaps.append(f"{call['call_id']}:unknown_entity_history")
                continue
            # The shared blackboard may repeat the very same direct sighting.
            # Keep each identifiable observer once, or just the fleet reference.
            aircraft = observers[(kind, ident)]
            if (kind, ident) in direct:
                aircraft.add(actor)
            for observer in sorted(aircraft) or [None]:
                emit("E5", call, "new_entity", entity_type=kind, entity_id=ident, actor_id=observer)
        discovered.update(facts)
        read = progress_read(call)
        if read is not None:
            session, value = read
            if session is not None and not isinstance(session, str):
                gaps.append(f"{call['call_id']}:invalid_progress_session")
            elif finite(value) and 0 <= value <= 100:
                readings[session].append((value, call))
            elif value is not None or call["tool"] == "get_mission_progress":
                gaps.append(f"{call['call_id']}:invalid_progress_read")
    known.update(discovered)

    # All calls in an episode are for one task. Resolve an implicit session only
    # when the public reads identify at most one explicit session.
    explicit_sessions = (set(progress) | set(readings)) - {None}
    if len(explicit_sessions) == 1:
        session = next(iter(explicit_sessions))
        if None in progress:
            progress.setdefault(session, progress.pop(None))
        if None in readings:
            readings[session].extend(readings.pop(None))
    for session, group in readings.items():
        values = {value for value, _ in group}
        if len(values) != 1:
            gaps.extend(f"{call['call_id']}:conflicting_parallel_progress" for _, call in group)
            progress.pop(session, None)
            continue
        value = group[0][0]
        if session in progress and progress[session] != value and complete_history:
            for _, call in group:
                emit("E5", call, "progress_changed", session_id=session, actor_id=None,
                     previous_percent=progress[session], progress_percent=value)
        progress[session] = value
