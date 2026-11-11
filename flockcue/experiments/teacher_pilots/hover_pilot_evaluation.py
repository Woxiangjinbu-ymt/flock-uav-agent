"""Independent, fixed-pilot goal scoring; never imported by the policy.

The scorer supplies an already restored, frozen world. Public policy queries and
the production completion assessor do not establish the goal truth here. Source
and artifact hashes are checked by the caller before entering this module.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


ACTOR = "3d229034"
ACTOR_NAME = "Drone 4"
TASK = "c74d7020"
ALTITUDE = 12.0
TOLERANCE = 1e-6
CONDITIONS = ("state_hover", "explicit_hover", "unknown_takeoff", "duration_hover")
DURATION_REASON = "elapsed_hover_duration_not_established_by_instantaneous_simulator_state_or_history_parameter"


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value):
    return isinstance(value, dict) and all(_finite(value.get(k)) for k in ("x", "y", "z"))


def _near(a, b, keys=("x", "y", "z")):
    return _point(a) and _point(b) and all(abs(a[k] - b[k]) <= TOLERANCE for k in keys)


def _verified_events(public, physical):
    """Bind each physical event to the exact committed/uncommitted transaction.

    Inconsistent logs are an evaluation integrity error, not a failed mission.
    A missing physical sidecar is valid only when no task history was emitted.
    """
    if not isinstance(public, list) or not isinstance(physical, list):
        raise ValueError("Expected public and physical event lists")
    requests, expected, events = {}, {}, {}
    previous = 0
    for row in public:
        if not isinstance(row, dict) or type(row.get("request_id")) is not int or row["request_id"] <= previous:
            raise ValueError("Malformed or unordered public request identity")
        previous = row["request_id"]
        if row.get("phase") != "task" or row.get("admin") is not False:
            continue
        requests[row["request_id"]] = row
        history = row.get("history_events")
        if not isinstance(history, list) or type(row.get("committed")) is not bool:
            raise ValueError("Malformed public transaction history")
        for index, item in enumerate(history):
            if not isinstance(item, dict) or not isinstance(item.get("history"), dict):
                raise ValueError("Malformed public physical history")
            expected[f"{row['request_id']}:{index}"] = (row, item)
    for event in physical:
        if not isinstance(event, dict):
            raise ValueError("Malformed physical event")
        if event.get("phase") != "task":
            continue
        ident = event.get("event_id")
        if not isinstance(ident, str) or ident not in expected or ident in events:
            raise ValueError("Unbound or duplicate physical event")
        row, item = expected[ident]
        if (event.get("request_id") != row["request_id"]
                or event.get("committed") is not row["committed"]
                or event.get("actor") != item.get("actor")
                or event.get("history") != item["history"]
                or event.get("origin") != row.get("origin")):
            raise ValueError("Physical event disagrees with its public transaction")
        if item.get("actor") == ACTOR and (not _point(item["history"].get("start_position"))
                                            or not _point(item["history"].get("position"))):
            raise ValueError("Malformed actor position history")
        events[ident] = event
    if set(events) != set(expected):
        raise ValueError("Physical event sidecar is incomplete")
    return requests, list(events.values())


def evaluate_evidence(condition, initial, final, public, physical):
    """Pure independent truth function for the four predeclared conditions."""
    if condition not in CONDITIONS:
        raise ValueError("Unsupported hover pilot condition")
    if (not isinstance(initial, dict) or initial.get("id") != ACTOR
            or initial.get("name") != ACTOR_NAME or not _point(initial.get("position"))):
        raise ValueError("Unbound or invalid frozen initial actor")
    requests, events = _verified_events(public, physical)
    result = {"schema": "hover-pilot-independent-v1", "condition": condition,
        "actor_id": ACTOR, "task_completed": None, "status": "not_evaluable",
        "reasons": [], "checks": {}, "evidence_refs": [],
        "position_tolerance_meters": TOLERANCE, "speed_tolerance": TOLERANCE,
        "scope": "fixed_frozen_environment_goal_truth_not_public_verification_or_run_status",
        "policy_status_query_required": False, "elapsed_duration_verified": False}
    if condition == "duration_hover":
        result["reasons"] = [DURATION_REASON]
        return result
    if (not isinstance(final, dict) or final.get("id") != ACTOR or final.get("name") != ACTOR_NAME
            or not _point(final.get("position")) or not _finite(final.get("speed"))
            or not isinstance(final.get("status"), str) or final.get("error")):
        raise ValueError("Malformed final snapshot actor state")

    target = {**initial["position"], "z": ALTITUDE}
    actor_events = [e for e in events if e.get("actor") == ACTOR and e["committed"] is True]

    def effect(event, command, history_name):
        row = requests[event["request_id"]]
        receipt = row.get("response")
        return (row.get("action") is True and row.get("method") == "POST"
            and row.get("endpoint") == f"/drones/{ACTOR}/command/{command}"
            and not row.get("error") and isinstance(receipt, dict)
            and receipt.get("drone_id") == ACTOR and receipt.get("command") == command
            and receipt.get("status") == "success" and not receipt.get("error")
            and receipt.get("success", True) is True
            and receipt.get("partial_success", False) is False
            and receipt.get("outcome", "completed") == "completed"
            and event["history"].get("event") == history_name)

    takeoffs = []
    for event in actor_events:
        row = requests[event["request_id"]]
        params = row.get("params") or {}
        altitude = params.get("altitude") if isinstance(params, dict) else None
        if (effect(event, "take_off", "take_off") and _finite(altitude)
                and abs(altitude - ALTITUDE) <= TOLERANCE
                and _near(event["history"]["position"], target)
                and _near(event["history"]["start_position"], initial["position"], ("x", "y"))):
            takeoffs.append(event)
    takeoffs.sort(key=lambda e: e["request_id"])
    takeoff = takeoffs[0] if takeoffs else None
    later = [e for e in actor_events if takeoff and e["request_id"] > takeoff["request_id"]]
    travel = [e for e in later if not _near(e["history"]["start_position"], e["history"]["position"])]
    hovers = [e for e in later if effect(e, "hover", "hovering")
              and _near(e["history"]["position"], target)]
    checks = {
        "same_actor_takeoff_to_requested_altitude": bool(takeoffs),
        "final_airborne_hovering": final["status"] == "hovering" and final["position"]["z"] > 0,
        "final_stationary": abs(final["speed"]) <= TOLERANCE,
        "final_altitude_and_initial_xy": _near(final["position"], target),
        "no_intervening_actual_travel": takeoff is not None and not travel,
    }
    if condition == "explicit_hover":
        checks["ordered_committed_hover_effect"] = bool(hovers)
    result.update(checks=checks, task_completed=all(checks.values()),
        status="satisfied" if all(checks.values()) else "not_satisfied",
        reasons=[key for key, passed in checks.items() if not passed],
        evidence_refs=[e["event_id"] for e in ([takeoff] if takeoff else [])
                       + (hovers[:1] if condition == "explicit_hover" else [])],
        takeoff_effect_count=sum(effect(e, "take_off", "take_off") for e in actor_events),
        matching_takeoff_effect_count=len(takeoffs),
        hover_effect_count=sum(effect(e, "hover", "hovering") for e in actor_events),
        ordered_matching_hover_effect_count=len(hovers),
        intervening_travel_event_ids=[e["event_id"] for e in travel])
    return result


def _read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_worker_log(folder, name, count_field):
    """Absence is zero activity only when the sealed worker records it as such."""
    relative = "policy/worldlogs/" + name
    path = folder / relative
    if path.exists():
        return _read_rows(path)
    try:
        worker = json.loads((folder / "worker-result.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("Missing world log without worker provenance: " + name) from exc
    cost = worker.get("physical_cost", {}) if isinstance(worker, dict) else {}
    files = worker.get("files") if isinstance(worker, dict) else None
    count = cost.get(count_field) if isinstance(cost, dict) else None
    if not isinstance(files, dict) or relative in files or type(count) is not int or count != 0:
        raise ValueError("Missing expected world log: " + name)
    return []


def evaluate_snapshot(world, fixture, trial, run, folder):
    """Scorer-only adapter; reads the frozen scene, logs, and final world state."""
    if (fixture.get("task_id") != TASK or trial.get("task_id") != TASK
            or fixture.get("scene_id") != "fb8971ec" or trial.get("condition") not in CONDITIONS):
        raise ValueError("Unexpected fixed hover pilot identity")
    if getattr(world, "phase", None) != "scoring":
        raise ValueError("Hover goal truth may only be evaluated in the scoring phase")
    root = (Path(run) / "inputs/dataset").resolve()
    scene_path = (root / fixture["scene_file"]).resolve()
    if not scene_path.is_relative_to(root):
        raise ValueError("Scene path escapes sealed inputs")
    scene = json.loads(scene_path.read_text())
    actors = [d for d in scene.get("drones", []) if isinstance(d, dict)
              and (d.get("id") == ACTOR or d.get("name") == ACTOR_NAME)]
    if len(actors) != 1:
        raise ValueError("Frozen scene actor binding is ambiguous or missing")
    folder = Path(folder)
    public = _read_worker_log(folder, "public-api.jsonl", "api_requests")
    physical = _read_worker_log(folder, "physical-events.jsonl", "physical_history_events")
    final = world.request("GET", f"/drones/{ACTOR}", admin=True)
    return evaluate_evidence(trial["condition"], actors[0], final, public, physical)
