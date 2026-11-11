"""First evidenced transitions of named-aircraft clauses in a visible prefix."""
from __future__ import annotations

import re

from hover_completion import assess_hover_completion, parse_hover_contract
from task_execution_state import _ACTIONS

from .event_observations import finite, object_value, rows


_NUMBER = r"-?\d+(?:\.\d+)?"
_TRIPLE = rf"\((?P<x>{_NUMBER}),\s*(?P<y>{_NUMBER}),\s*(?P<z>{_NUMBER})\)"
_XYZ = rf"x=(?P<x>{_NUMBER})\s+y=(?P<y>{_NUMBER})\s+z=(?P<z>{_NUMBER})"
_ACTOR = r"(?P<name>Drone \d+)"
# Six complete quick_checkpoint templates. Only their coordinate clause is
# mechanically checked here; retain the flanking obligations in the denominator.
_CHECKPOINT = [
    (rf"Drone {_ACTOR} takes off to {_NUMBER} meters, ", rf"flies to checkpoint at {_TRIPLE}", ", and hovers briefly"),
    (rf"Send {_ACTOR} to verify checkpoint location: lift off to {_NUMBER} meters, ", rf"fly to position {_TRIPLE}", ", then hover briefly"),
    (rf"Command drone {_ACTOR} to ascend to {_NUMBER}m altitude, ", rf"navigate to waypoint coordinates {_TRIPLE}", ", and maintain hover"),
    (rf"{_ACTOR} checkpoint verification flight: take off reaching {_NUMBER} meters height, ", rf"proceed to position {_XYZ}", ", pause in place"),
    (rf"Execute quick point-to-point mission for {_ACTOR}: departure to {_NUMBER}m elevation, ", rf"travel to checkpoint {_TRIPLE}", ", hold position briefly"),
    (rf"Drone {_ACTOR} single waypoint visit: take off to altitude {_NUMBER} meters, ", rf"move to target location {_TRIPLE}", ", then hover momentarily"),
]


def _clause(task, span, kind, supported, status, **fields):
    start, end = span
    return {"clause_id": f"{start}:{end}", "span": [start, end], "text": task[start:end],
            "kind": kind, "supported": supported, "status": status, "actor_id": None, **fields}


def _parse(task):
    full = (0, len(task))
    for before, movement, after in _CHECKPOINT:
        match = re.fullmatch(before + f"(?P<movement>{movement})" + after + r"\.?", task, re.I)
        if match:
            start, end = match.span("movement")
            name = match["name"]
            return [
                _clause(task, (0, start), "checkpoint_takeoff", False, "outside_initial_grammar"),
                _clause(task, (start, end), "checkpoint_path", True, "unconfirmed", actor_name=name,
                        destination={k: float(match[k]) for k in ("x", "y", "z")}),
                _clause(task, (end, len(task)), "checkpoint_hold", False,
                        "elapsed_duration_unverified" if re.search("briefly|momentarily", after) else "outside_initial_grammar"),
            ]
    contract = parse_hover_contract(task)
    if contract["kind"] in {"state_goal", "action_required"}:
        return [_clause(task, full, contract["kind"], True, "unconfirmed", actor_name=contract["actor_name"])]
    if contract["kind"] == "duration_unverified":
        reason = "elapsed_duration_unverified"
    elif re.search(r"\b(it|that drone|the drone)\b", task, re.I) and not re.search(r"\bDrone \d+\b", task, re.I):
        reason = "ambiguous_actor"
    elif not re.search(r"\bDrone \d+\b", task, re.I):
        reason = "no_named_actor"
    else:
        reason = "outside_initial_grammar"
    return [_clause(task, full, contract["kind"], False, reason)]


class ClauseTracker:
    def __init__(self, task):
        self.task = task
        self.coverage = _parse(task)
        self.bindings = {}
        self.history = []
        self.by_id = {}

    def _observe_names(self, call):
        payload = object_value(call["result"])
        if call["tool"] == "run_harness":
            bindings = object_value(payload.get("actor_bindings"))
            for name, ident in bindings.items():
                if isinstance(ident, str) and ident:
                    self.bindings.setdefault(name, set()).add(ident)
        elif call["observation_ok"]:
            states = (rows(call["result"]) if call["tool"] == "list_drones" else
                      [payload] if call["tool"] == "get_drone_status" else [])
            for state in states:
                name, ident = state.get("name"), state.get("id")
                if isinstance(name, str) and isinstance(ident, str) and name and ident:
                    self.bindings.setdefault(name, set()).add(ident)

    def _visible_intervals(self, batch):
        # These ranks encode only the ordering available to the model. Every
        # pair in a parallel batch overlaps; all earlier batches precede it.
        # An executing subprogram hides its internal action ordering. Rebuild
        # hover anchors after that boundary; only a returned last action can be
        # projected explicitly. A rejected invocation performed no work.
        if any(call["tool"] == "run_harness" and
               object_value(call["result"]).get("status") not in {"unsupported", "rejected"}
               for call in batch):
            self.history.clear()
        offset = 2 * len(self.history) + 1
        ids = [call["call_id"] for call in batch]
        for i, call in enumerate(batch):
            cid, args = call["call_id"], call["arguments"]
            self.by_id[cid] = call
            effect = next((code for code, _ in call["effects"]), None)
            action = call["tool"] in _ACTIONS
            status = {"E1": "blocked", "E2": "unknown", "E3": "partial", "E4": "error"}.get(effect)
            if status is None:
                status = ("completed" if action else "observed") if call["success"] else "unknown"
            name = call["tool"]
            actors = [args["drone_id"]] if args.get("drone_id") else []
            payload = call["result"]
            if name == "run_harness":
                last = object_value(object_value(object_value(payload).get("state")).get("last_action"))
                if last.get("tool") in _ACTIONS and "receipt" in last:
                    name, args, payload = last["tool"], object_value(last.get("args")), last["receipt"]
                    actors = [args["drone_id"]] if args.get("drone_id") else []
                    status = "completed" if call.get("last_action_success") else {
                        "E1": "blocked", "E2": "unknown", "E3": "partial", "E4": "error",
                    }.get(call.get("last_action_event"), "unknown")
                    action = True
            overlaps = [other for other in ids if other != cid]
            self.history.append({"evidence_id": cid, "tool_name": name, "args": args,
                                 "payload": payload, "entity_ids": actors, "status": status,
                                 "scope": "action_receipt" if action else "observation",
                                 "start_sequence": offset + i, "end_sequence": offset + len(ids) + i,
                                 "overlaps": overlaps, "ordering": "uncertain" if overlaps else "certain"})

    def update(self, batch, emit, *, history_incomplete):
        for call in batch:
            self._observe_names(call)
        self._visible_intervals(batch)
        for clause in self.coverage:
            if not clause["supported"] or clause["status"] in {"completed", "original_path_blocked"}:
                continue
            actors = self.bindings.get(clause["actor_name"], set())
            if len(actors) != 1:
                clause["status"] = "ambiguous_actor" if actors else "no_public_binding"
                continue
            actor, = actors
            clause["actor_id"] = actor
            if history_incomplete:
                clause["status"] = "incomplete_public_history"
                continue
            if clause["kind"] == "checkpoint_path":
                self._checkpoint(batch, actor, clause, emit)
            else:
                self._hover(batch, actor, clause, emit)

    def _checkpoint(self, batch, actor, clause, emit):
        clause["status"] = "unconfirmed"
        for call in batch:
            args = call["arguments"]
            if (call["tool"] in {"move_to", "move_to_and_sense", "navigate_to", "navigate_to_and_sense"}
                    and args.get("drone_id") == actor and any(code == "E1" for code, _ in call["effects"])
                    and all(finite(args.get(k)) and args[k] == v for k, v in clause["destination"].items())):
                clause["status"] = "original_path_blocked"
                emit("E6", call, "original_path_blocked", actor_id=actor, clause_id=clause["clause_id"])
                return

    def _hover(self, batch, actor, clause, emit):
        snapshot = {"command": self.task, "observer_errors": [], "events": self.history,
                    "entity_bindings": {clause["actor_name"]: {"state": "exact",
                                        "entities": [{"kind": "drone", "id": actor}]}}}
        assessment = assess_hover_completion(self.task, snapshot, contract="hover_completion_v2")
        if assessment["status"] != "verified":
            clause["status"] = assessment["reasons"][0]
            return
        refs = assessment["evidence_refs"]
        current = {call["call_id"] for call in batch}
        if refs and refs[-1] in current:
            clause["status"] = "completed"
            emit("E6", self.by_id[refs[-1]], "named_hover_confirmed", actor_id=actor,
                 clause_id=clause["clause_id"], supporting_calls=refs)
