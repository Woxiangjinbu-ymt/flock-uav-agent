"""Once-per-ID follow-up of freshly observed fixed target coordinates.

Candidate scheduling, not a remaining-task oracle. Reach names only suppress
this optimization; they never prove which target ID completed. Outcomes and
session completion still belong to the existing search and runtime.
"""
from __future__ import annotations

import copy
import math
import re

from shapely.geometry import LineString, Point

from command_results import command_outcome

_NAME = re.compile(r"Fixed Target [1-9]\d*")
_REACHED_LIST = re.compile(r"(?:Fixed|Moving|Circle|Polygon) Target [1-9]\d*|Waypoint [1-9]\d*")
_MARKER = " - Reached target(s): "


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _geometry(item):
    position = item.get("position")
    if not isinstance(position, dict) or not all(_number(position.get(k)) for k in ("x", "y", "z")):
        return None
    return tuple(position[k] for k in ("x", "y", "z"))


class ObservedTargetFollowup:
    def __init__(self):
        self.targets = {}
        self.positive_names = {}
        self.name_ids = {}
        self.directed_indices = set()
        self.attempts = []

    def observe(self, nearby, actor, evidence_ref):
        # Direct receipts only, never inherited blackboard targets lacking a
        # current-plan observation reference. Malformed reads add no candidate.
        if (not evidence_ref or not isinstance(nearby, dict)
                or nearby.get("status") not in (None, "success")
                or nearby.get("error") or nearby.get("harness_transport_error")
                or (nearby.get("success") is not None and nearby["success"] is not True)
                or (nearby.get("partial_success") is not None and nearby["partial_success"] is not False)
                or not all(isinstance(nearby.get(k), list) for k in ("targets", "obstacles", "drones"))):
            return
        for item in nearby["targets"]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            valid_id = isinstance(item.get("id"), str) and bool(item["id"])
            if isinstance(name, str) and _NAME.fullmatch(name):
                self.name_ids.setdefault(name, set()).add(item["id"] if valid_id else None)
            if not valid_id:
                continue
            tid, name, geometry = item["id"], item.get("name"), _geometry(item)
            previous = self.targets.get(tid)
            if previous is not None:
                if (item.get("type") != "fixed" or name != previous["name"]
                        or geometry != tuple(previous["position"])):
                    previous["disabled_reason"] = "observed_identity_or_geometry_changed"
                if item.get("is_reached") is True:
                    previous.setdefault("positive_flag_ref", evidence_ref)
                continue
            if (item.get("type") != "fixed" or geometry is None or not isinstance(name, str)
                    or _NAME.fullmatch(name) is None):
                continue
            entry = {"target_id": tid, "name": name, "position": list(geometry), "owner": actor,
                     "observation_ref": evidence_ref, "scheduled": False}
            if item.get("is_reached") is True:
                entry["positive_flag_ref"] = evidence_ref
            self.targets[tid] = entry
        for item in self.targets.values():
            ids = self.name_ids[item["name"]]
            if len(ids) > 1 or None in ids:
                item["disabled_reason"] = "ambiguous_public_name"

    def observe_move(self, action, actor, evidence_ref):
        receipt = action.get("receipt")
        if (not evidence_ref or action.get("tool") != "move_to"
                or action.get("args", {}).get("drone_id") != actor
                or not isinstance(receipt, dict) or receipt.get("drone_id") != actor
                or receipt.get("command") != "move_to" or receipt.get("status") != "success"
                or receipt.get("error") or receipt.get("harness_transport_error")
                or (receipt.get("partial_success") is not None and receipt["partial_success"] is not False)
                or (receipt.get("success") is not None and receipt["success"] is not True)
                or command_outcome(receipt) != "completed"):
            return
        message = receipt.get("message")
        if not isinstance(message, str) or not message.startswith("Drone moved to position ") or message.count(_MARKER) != 1:
            return
        names = message.split(_MARKER)[1].split(", ")
        if not names or any(_REACHED_LIST.fullmatch(name) is None for name in names):
            return
        for name in names:
            if _NAME.fullmatch(name):
                self.positive_names.setdefault(name, {"evidence_ref": evidence_ref, "actor": actor,
                    "scope": "name_only_suppression_not_target_id_completion"})

    def choose(self, actor, state, policy, shared_endpoints, fallback, *, round_number, sequence):
        radius = state.get("task_radius")
        if actor in policy.disabled or not _number(radius) or radius <= 0:
            return None
        for item in self.targets.values():
            if (item["owner"] != actor or item["scheduled"] or item.get("disabled_reason")
                    or item.get("positive_flag_ref") or item["name"] in self.positive_names
                    or abs(20.0 - item["position"][2]) > radius):
                continue
            destination = tuple(item["position"][:2])
            if any(a == actor and start == policy.xy[actor] and policy.candidates[i] == destination
                   for a, start, i in policy.tried_edges):
                continue  # A new index must not bypass a tried geometric edge.
            # Conservative height qualification; no assumption of 2D task mode.
            # Public approximate geometry is not proof of a free edge.
            if ((*destination, 20.0) in shared_endpoints
                    or policy.blocked.intersects(Point(*destination))
                    or policy.blocked.intersects(LineString([policy.xy[actor], destination]))):
                continue
            index = len(policy.candidates)
            policy.candidates.append(destination)
            policy.candidate_cover.append(frozenset(i for i, cell in enumerate(policy.score_cells)
                                                     if math.dist(destination, cell) <= policy.radius))
            self.directed_indices.add(index)
            item["scheduled"] = True
            attempt = {"target_id": item["target_id"], "target_name": item["name"],
                "observed_position": list(item["position"]), "actor": actor,
                "observation_ref": item["observation_ref"], "candidate_index": index,
                "destination": [*destination, 20.0], "round": round_number, "sequence": sequence,
                "status": "scheduled", "fallback_exploration": None if fallback is None else {
                    "candidate_index": fallback[0], "destination": [*fallback[1], 20.0]}}
            self.attempts.append(attempt)
            return (index, destination, math.dist(policy.xy[actor], destination))
        return None

    def record_action(self, index, outcome, evidence_ref):
        for item in self.attempts:
            if item["candidate_index"] == index:
                item.update(status="action_returned", outcome=outcome, action_ref=evidence_ref)
                break

    def pending_ids(self, states, policy, shared_endpoints):
        """Read-only actionable frontier for v8; do not schedule or claim reach.

        Mirror the existing once-per-ID geometry/owner guards. A pending public
        target prevents the progress handoff from cutting off its normal slot.
        This method is never called by v7 and does not mutate its candidates.
        """
        result = []
        for item in self.targets.values():
            actor = item["owner"]
            radius = states.get(actor, {}).get("task_radius")
            if (actor in policy.disabled or actor not in policy.xy or not _number(radius) or radius <= 0
                    or item["scheduled"] or item.get("disabled_reason") or item.get("positive_flag_ref")
                    or item["name"] in self.positive_names or abs(20.0 - item["position"][2]) > radius):
                continue
            destination = tuple(item["position"][:2])
            if any(a == actor and start == policy.xy[actor] and policy.candidates[i] == destination
                   for a, start, i in policy.tried_edges):
                continue
            if ((*destination, 20.0) in shared_endpoints or policy.blocked.intersects(Point(*destination))
                    or policy.blocked.intersects(LineString([policy.xy[actor], destination]))):
                continue
            result.append(item["target_id"])
        return sorted(result)

    def snapshot(self):
        return copy.deepcopy({"scope": "current_plan_public_observations_once_per_id_no_completion_claim",
            "targets": list(self.targets.values()), "positive_names": self.positive_names,
            "observed_name_ids": {name: sorted(ids, key=lambda value: (value is not None, value or ""))
                                  for name, ids in self.name_ids.items()},
            "attempts": self.attempts, "directed_indices": sorted(self.directed_indices)})
