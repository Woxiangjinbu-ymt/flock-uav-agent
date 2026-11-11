"""Opt-in M1-R3: source-bound remaining coordinate routes, layered on R2.

This is an advisory receipt ledger, not a planner or task checker. It never
executes, retries or vetoes a command. Unsupported language keeps R2's inputs.
No map, benchmark answer, checker tolerance or earlier command's route proof
is consumed. R2 contact hints and public progress retain their implementation.
"""
from __future__ import annotations

import copy
import json
import math
import re

from command_results import command_outcome
from task_evidence import encode, point
from task_evidence_context import TaskEvidenceMiddleware
from task_evidence_r2 import TaskEvidenceLedgerR2
from task_progress import _primary_receipt

MODES = {"evidence_r3", "evidence_r3_shadow"}
_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_XYZ = rf"\(\s*({_NUM})\s*,\s*({_NUM})\s*,\s*({_NUM})\s*\)"
_COORD = re.compile(_XYZ)
_HEADER = re.compile(r"\b(Drone \d+) route:\s*")
_BODY = re.compile(
    rf"fly to and start at\s*{_XYZ},\s*then waypoints\s*{_XYZ}"
    rf"(?:,\s*{_XYZ})*,\s*then end\s*{_XYZ}\s+and land\.\s*", re.I)
_INTRO = re.compile(
    rf"Drones? Drone \d+(?:(?:,\s*|,?\s+and\s+)Drone \d+)* "
    rf"perform a (?:(?:dual|triple) )?marathon flight at {_NUM} meters\.\s*", re.I)
_MESSAGE_POSITION = re.compile(rf"^Drone moved (?:to position|{_NUM}m to position) {_XYZ}(?:,|\s|$)")
_ROUTE_NOTE = (
    "Coordinate receipts only; landing and other clauses are not evaluated. "
    "next_unconfirmed is the first gap in original actor/order, not proof of failure. "
    "Later observed points cannot close an earlier gap. Keep original order and legal "
    "obstacle-aware tools within the existing budget; do not repeat a blocked move "
    "solely to clear this view. Report unresolved evidence honestly."
)


def explicit_routes(command):
    """All-or-none parser for explicit start / waypoints / end-and-land blocks.

    Limited affirmative grammar deliberately rejects conditional, quoted,
    negated, malformed or mixed-action instructions. All clauses still remain
    in the original task. Recognition is syntax-based, never task-ID based.
    """
    if len(command) > 32000:
        return [], "unsupported_length"
    headers = list(_HEADER.finditer(command))
    if not headers:
        return [], "no_explicit_route"
    prefix = command[:headers[0].start()].strip()
    if prefix and not _INTRO.fullmatch(prefix):
        return [], "unsupported_scope"
    if len(headers) > 8 or len({h.group(1) for h in headers}) != len(headers):
        return [], "ambiguous_or_excess_actors"
    routes = []
    for n, header in enumerate(headers):
        stop = headers[n + 1].start() if n + 1 < len(headers) else len(command)
        body = command[header.end():stop]
        if not _BODY.fullmatch(body):
            return [], "unsupported_route_language"
        goals = []
        for coord in _COORD.finditer(body):
            xyz = list(map(float, coord.groups()))
            if not all(math.isfinite(v) for v in xyz):
                return [], "nonfinite_coordinate"
            goals.append({"ordinal": len(goals) + 1, "point": xyz,
                          "source_span": [header.end() + coord.start(), header.end() + coord.end()]})
        if len(goals) > 64:
            return [], "excess_waypoints"
        routes.append({"required_actor": header.group(1), "actor_source_span": list(header.span(1)),
                       "source_span": [header.start(), stop], "goals": goals})
    return routes, "recognized_coordinate_routes"


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _path_points(receipt, actor, path):
    """Partial movement certifies only returned successful points, in order."""
    if (not isinstance(receipt, dict) or receipt.get("command") != "move_along_path"
            or str(receipt.get("drone_id", "")) != actor
            or command_outcome(receipt) not in {"completed", "partial"}):
        return []
    points = receipt.get("successful_points")
    count = receipt.get("successful_points_count")
    if (not isinstance(points, list) or not isinstance(count, int) or isinstance(count, bool)
            or count != len(points) or any(point(p) is None for p in points)):
        return []
    return [{"actual_point": point(p), "receipt_path": [*path, "successful_points", i],
             "kind": "successful_path_point", "rounding_m": 0.000001} for i, p in enumerate(points)]


def receipt_points(event):
    """Read only documented primary movement results / executed fallback paths.

    A verified navigation endpoint can match its *exact requested goal* under
    the tool's public arrival tolerance. No nearby goal borrows that tolerance.
    Path points use numerical equality; rounded direct messages allow 0.02 m.
    """
    if event["end_sequence"] is None:
        return []
    name, args, payload = event["tool_name"], event["args"], event["payload"]
    actor = str(args.get("drone_id", ""))
    if not actor or not isinstance(payload, dict):
        return []
    receipt = _primary_receipt(name, payload)
    path = []
    if name.endswith("_and_sense"):
        path = ["navigation_result"] if "navigation_result" in payload else ["move_result"] if "move_result" in payload else []
    if not isinstance(receipt, dict) or str(receipt.get("drone_id", "")) != actor:
        return []
    tool = name.removesuffix("_and_sense")
    if tool == "move_along_path":
        return _path_points(receipt, actor, path)
    if tool in {"move_to", "move_towards"}:
        if receipt.get("command") != tool or command_outcome(receipt) != "completed":
            return []
        message = receipt.get("message")
        match = _MESSAGE_POSITION.match(message) if isinstance(message, str) else None
        xyz = point(list(map(float, match.groups()))) if match else None
        return [{"actual_point": xyz, "receipt_path": [*path, "message"],
                 "kind": "rounded_direct_receipt", "rounding_m": .02}] if xyz else []
    if tool != "navigate_to":
        return []
    # Only the executed final fallback is read, not an earlier rejected direct
    # request, planned_waypoints, sensing payload or arbitrary nested messages.
    result = _path_points(receipt.get("last_move_result"), actor, [*path, "last_move_result"])
    actual, goal = point(receipt.get("final_position")), point(args)
    tolerance, distance = args.get("arrival_tolerance", 1.0), receipt.get("distance_remaining")
    if (actual and goal and _finite(tolerance) and tolerance > 0 and _finite(distance)
            and distance >= 0 and receipt.get("success") is True
            and receipt.get("arrival_verified") is True and receipt.get("outcome") == "completed"
            and command_outcome(receipt) == "completed"
            and math.isclose(math.dist(actual, goal), distance, rel_tol=1e-7, abs_tol=1e-6)
            and distance <= tolerance):
        proof = {"actual_point": actual, "requested_goal": goal,
                 "receipt_path": [*path, "final_position"], "kind": "public_verified_arrival",
                 "arrival_tolerance_m": tolerance, "distance_remaining_m": distance, "rounding_m": 0.000001}
        if result and math.dist(result[-1]["actual_point"], actual) <= 0.000001:
            # The path endpoint and its arrival confirmation are one visit.
            result[-1] = proof
        else:
            result.append(proof)
    return result


def _matches(proof, goal):
    return (proof.get("requested_goal") == goal or
            math.dist(proof["actual_point"], goal) <= proof["rounding_m"])


class TaskEvidenceLedgerR3(TaskEvidenceLedgerR2):
    def __init__(self, command, command_id="", **kwargs):
        self.routes, self.route_parse_status = explicit_routes(str(command))
        super().__init__(command, command_id, **kwargs)

    def route_projection(self):
        with self._lock:
            result = []
            for route in self.routes:
                valid = self.index_epoch == self.index.epoch
                actors = [e for e in self._entities.values() if valid and
                          e["kind"] == "drone" and e["name"] == route["required_actor"]]
                actor = actors[0]["id"] if len(actors) == 1 else None
                prefix, goals = 0, copy.deepcopy(route["goals"])
                for goal in goals:
                    goal.update(ordered_evidence=None, observed_evidence=None)
                for event in sorted(self._events, key=lambda e: e["start_sequence"]):
                    if (not actor or str(event["args"].get("drone_id", "")) != actor
                            or self._actor_conflict(event, actor)):
                        continue
                    for proof in receipt_points(event):
                        evidence = {**proof, "session_id": self.index.session_id, "command_id": self.command_id,
                                    "actor_id": actor, "evidence_id": event["evidence_id"],
                                    "tool_call_id": event["tool_call_id"], "end_sequence": event["end_sequence"]}
                        for goal in goals:
                            if goal["observed_evidence"] is None and _matches(proof, goal["point"]):
                                goal["observed_evidence"] = evidence
                        # One returned physical point can advance at most one
                        # ordinal, including when the command repeats a point.
                        if prefix < len(goals) and _matches(proof, goals[prefix]["point"]):
                            goals[prefix]["ordered_evidence"] = evidence
                            prefix += 1
                result.append({**route, "goals": goals, "actor_id": actor,
                               "identity_state": "bound" if actor else "ambiguous" if len(actors) > 1 else "unresolved",
                               "ordered_prefix": prefix,
                               "coordinates_with_receipt": sum(g["observed_evidence"] is not None for g in goals),
                               "state": "coordinate_sequence_evidenced" if prefix == len(goals) else "unconfirmed",
                               "next_unconfirmed": copy.deepcopy(goals[prefix]) if prefix < len(goals) else None})
            return result

    def summary(self):
        base = super().summary()
        if not self.routes:
            return base  # Byte-for-byte R2 projection on unsupported tasks.
        data = json.loads(base) if base else {"items": [], "omitted": len(self.requirements),
                                             "aggregate_requirements": [], "aggregate_omitted": len(self.aggregate)}
        data.update(schema="m1-r3", routes=[], routes_omitted=len(self.routes),
                    route_scope=_ROUTE_NOTE, mission_completion="not_evaluated", original_task_controls=True)
        for route in self.route_projection():
            next_goal = route["next_unconfirmed"]
            view = {k: route[k] for k in ("required_actor", "actor_id", "identity_state", "ordered_prefix", "coordinates_with_receipt", "state")}
            view.update(total=len(route["goals"]),
                        next_unconfirmed={k: next_goal[k] for k in ("ordinal", "point", "source_span")} if next_goal else None,
                        later_observed_ordinals=[g["ordinal"] for g in route["goals"][route["ordered_prefix"] + 1:]
                                                 if g["observed_evidence"] is not None])
            if route["ordered_prefix"]:
                last = route["goals"][route["ordered_prefix"] - 1]["ordered_evidence"]
                view["last_ordered_evidence"] = {k: last[k] for k in ("evidence_id", "tool_call_id", "receipt_path")}
            data["routes"].append(view); data["routes_omitted"] -= 1
        # Preserve explicit omission counts for every R2/R3 field. Route gaps
        # take precedence only for recognized route tasks under the same cap.
        for key, omitted in (("items", "omitted"), ("aggregate_requirements", "aggregate_omitted"), ("routes", "routes_omitted")):
            while len(encode(data)) > self.max_summary_chars and data.get(key):
                data[key].pop(); data[omitted] += 1
        return encode(data) if len(encode(data)) <= self.max_summary_chars else ""

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "m1-r3"
            result["task_evidence"]["route_parse_status"] = self.route_parse_status
            result["task_evidence"]["coordinate_routes"] = self.route_projection()
            return result


class TaskEvidenceR3Middleware(TaskEvidenceMiddleware):
    def __init__(self, ledger, mode="evidence_r3"):
        if mode not in MODES:
            raise ValueError(f"Unsupported R3 mode: {mode}")
        super().__init__(ledger, "evidence_shadow" if mode == "evidence_r3_shadow" else "evidence")
        self.variant = mode

    def _model_request(self, request):
        result = super()._model_request(request)
        if self.ledger.model_requests:
            self.ledger.model_requests[-1]["variant"] = self.variant
        return result
