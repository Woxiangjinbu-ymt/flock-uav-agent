"""M1-R: scoped action evidence, never a task checker or an action policy.

Only original instructions, local blackboard entries and returned tool data enter
this module. Name-only reach reports are deliberately NOT target coordinates.
The frozen v1 ledger remains the receipt observer and can still run unchanged.
"""
from __future__ import annotations

import copy
import json
import math
import re
import threading
from collections import defaultdict

from flockcue.evidence.receipts import command_outcome
from flockcue.evidence.progress import TaskProgressLedger, _ACTIONS, _CLAUSE, _MOVEMENT, _primary_receipt

_TARGET = re.compile(r"\b(?:(?:Fixed|Moving|Circle|Polygon) Target \d+|Waypoint \d+)\b")
_DRONE = re.compile(r"\bDrone \d+\b")
_VISIT = re.compile(r"\b(?:visit|reach|navigate to|flies to|fly to|surveys?)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|never|avoid|instead|unless|if|whether)\b", re.I)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def point(value):
    if isinstance(value, dict):
        value = [value.get(k) for k in ("x", "y", "z")]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        return None
    return list(value)


def goal_xy(value):
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y")]
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    xy = value[:2]
    return list(xy) if all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in xy) else None


def references(command):
    """Extract explicit visit pairs; retain unsupported references without actors.

    This is intentionally a small source-span reader, not a language contract.
    Multi-actor lists and negated/conditional language never imply a pairing.
    Other task clauses remain in the original instruction, not 'completed'.
    """
    result = []
    for clause in _CLAUSE.finditer(command):
        text = clause.group()
        actors = list(_DRONE.finditer(text))
        for target in _TARGET.finditer(text):
            preceding = [a for a in actors if a.end() <= target.start()]
            actor = preceding[-1].group() if preceding else None
            start = preceding[-1].start() if preceding else 0
            fragment = text[start:target.end()]
            # An earlier actor list is ambiguous unless this actor has its own
            # explicit verb ("Drone 1 flies to ... , Drone 2 flies to ...").
            separate = len(actors) <= 1 or bool(re.match(
                r"Drone \d+\s+(?:must\s+|should\s+)?(?:flies to|fly to|surveys?|visits?|navigates? to)\b",
                fragment, re.I))
            if len(preceding) > 1 and not _TARGET.search(text[preceding[-2].end():start]):
                separate = False  # "Drone 1 and Drone 2 visit ..." is a group.
            explicit = bool(actor and separate and _VISIT.search(fragment) and not _NEGATION.search(text))
            result.append({"id": f"ref-{len(result) + 1}", "target_name": target.group(),
                           "required_actor": actor if explicit else None,
                           "relation": "visit" if explicit else "unparsed_reference",
                           "source_span": [clause.start() + start, clause.start() + target.end()]})
    return result


class SessionEvidenceIndex:
    """A sidecar owned by one blackboard, never loaded from experiment results."""

    def __init__(self, session_id=None):
        self.session_id = session_id
        self.lock = threading.RLock()
        self.entities = {}
        self.reports = defaultdict(list)
        self.report_counts = defaultdict(int)
        self.epoch = 0

    def clear(self):
        with self.lock:
            self.entities.clear()
            self.reports.clear()
            self.report_counts.clear()
            self.epoch += 1

    def remember(self, report):
        with self.lock:
            name = report["target_name"]
            self.reports[name].append(copy.deepcopy(report))
            self.report_counts[name] += 1
            # Bound retained events per name, with an explicit omission count.
            self.reports[name] = self.reports[name][-8:]


def _receipts(value, path=()):
    """Only known execution-result containers; never arbitrary nested messages."""
    if not isinstance(value, dict):
        return
    yield path, value
    for key in ("last_move_result", "direct_move_result", "move_result", "navigation_result"):
        yield from _receipts(value.get(key), (*path, key))
    step = value.get("last_step")
    if isinstance(step, dict):
        for key in ("result", "move_result"):
            yield from _receipts(step.get(key), (*path, "last_step", key))
    for i, step in enumerate(value.get("steps", []) or []):
        if isinstance(step, dict):
            for key in ("result", "move_result"):
                yield from _receipts(step.get(key), (*path, "steps", i, key))


class TaskEvidenceLedger(TaskProgressLedger):
    def __init__(self, command, command_id="", *, index=None, **kwargs):
        self.index = index if index is not None else SessionEvidenceIndex()
        self.index_epoch = self.index.epoch
        with self.index.lock:
            self._reports = copy.deepcopy(self.index.reports)
            self._report_counts = copy.deepcopy(self.index.report_counts)
        self._positions = {}
        self._starts = {}
        self._consumed = set()
        self.model_requests = []
        self.injections = []
        self.requirements = references(str(command))
        super().__init__(command, command_id, **kwargs)
        with self.index.lock:
            for key, entity in self.index.entities.items():
                if key not in self._entities:
                    inherited = copy.deepcopy(entity)
                    for fact in inherited["facts"].values():
                        fact["validity"] = "inherited"
                    inherited["origin"] = "inherited"
                    self._entities[key] = inherited

    def _entity(self, kind, value, evidence_id, *, inherited=False):
        super()._entity(kind, value, evidence_id, inherited=inherited)
        if not isinstance(value, dict) or not value.get("id"):
            return
        key = f"{kind}:{value['id']}"
        entity = self._entities.get(key)
        if entity is None or self.index_epoch != self.index.epoch:
            return
        for fact in entity["facts"].values():
            if fact["evidence_id"] == evidence_id and "command_id" not in fact:
                fact["command_id"] = None if inherited else self.command_id
        with self.index.lock:
            if not inherited or key not in self.index.entities:
                saved = copy.deepcopy(entity)
                saved["identity_source"] = {"command_id": self.command_id, "evidence_id": evidence_id}
                self.index.entities[key] = saved

    def begin_tool(self, tool_name, args, tool_call_id=""):
        with self._lock:
            eid = super().begin_tool(tool_name, args, tool_call_id)
            event = self._events[-1]
            actor = str(event["args"].get("drone_id", ""))
            self._starts[eid] = copy.deepcopy(self._positions.get(actor))
            # Even a rejected photo/hover may trigger emergency landing on the
            # server. Do not bridge a future path across any unmeasured action.
            if tool_name in _ACTIONS:
                self._positions.pop(actor, None)
            return eid

    def end_tool(self, evidence_id, output=None, *, error=None):
        with self._lock:
            super().end_tool(evidence_id, output, error=error)
            if evidence_id in self._consumed:
                return
            self._consumed.add(evidence_id)
            try:
                self._consume(next(e for e in self._events if e["evidence_id"] == evidence_id))
            except Exception as exc:
                self.observer_error(exc)

    def _actor_conflict(self, event, actor):
        return any(e["evidence_id"] in event["overlaps"] and
                   str(e["args"].get("drone_id", "")) == actor for e in self._events)

    def _consume(self, event):
        if self.index_epoch != self.index.epoch:
            return  # A cleared scene cannot be repopulated by an in-flight reply.
        actor = str(event["args"].get("drone_id", ""))
        conflict = self._actor_conflict(event, actor)
        receipt = _primary_receipt(event["tool_name"], event["payload"])
        if event["tool_name"] == "get_drone_status" and event["status"] == "observed":
            position = point(receipt.get("position")) if isinstance(receipt, dict) else None
            if position and not conflict and str(receipt.get("id")) == actor:
                self._positions[actor] = {"point": position, "evidence_id": event["evidence_id"]}
        if event["tool_name"] not in _MOVEMENT or not actor:
            return
        end_position = point(receipt.get("final_position")) if isinstance(receipt, dict) else None
        seen = set()
        receipt_path = ()
        if event["tool_name"].endswith("_and_sense") and isinstance(event["payload"], dict):
            if "navigation_result" in event["payload"]:
                receipt_path = ("navigation_result",)
            elif "move_result" in event["payload"]:
                receipt_path = ("move_result",)
        for path, sub in _receipts(receipt):
            outcome = command_outcome(sub)
            if outcome not in {"completed", "partial"} or str(sub.get("drone_id", actor)) != actor:
                continue
            message = sub.get("message", "")
            if not isinstance(message, str) or " - Reached target(s): " not in message:
                continue
            names = _TARGET.findall(message.split(" - Reached target(s): ", 1)[1])
            for name in names:
                identity = (name, sub.get("command_id") or message)
                if identity in seen:
                    continue
                seen.add(identity)
                hint = None
                if not conflict and (name.startswith("Fixed Target ") or name.startswith("Waypoint ")):
                    hint = self._path_hint(event, sub, path, outcome)
                report = {
                    "session_id": self.index.session_id, "epoch": self.index_epoch,
                    "command_id": self.command_id, "evidence_id": event["evidence_id"],
                    "tool_call_id": event["tool_call_id"], "tool": event["tool_name"],
                    "start_sequence": event["start_sequence"], "end_sequence": event["end_sequence"],
                    "target_name": name, "actor_id": actor, "message_path": list(receipt_path + path) + ["message"],
                    "message": message, "ordering": "uncertain" if conflict else "certain",
                    "scope": "historical_contact_not_current_position_or_full_task",
                    "localization_hint": hint,
                }
                self.index.remember(report)
                self._reports[name].append(report)
                self._report_counts[name] += 1
        # A completed straight move certifies its requested endpoint, not a
        # target center. Partial/path receipts must return actual points.
        if not end_position and event["status"] == "completed" and event["tool_name"] in {"move_to", "move_to_and_sense"}:
            end_position = point(event["args"])
        if not end_position and isinstance(receipt, dict):
            points = receipt.get("successful_points")
            if isinstance(points, list) and points:
                end_position = point(points[-1])
        if end_position and not conflict:
            self._positions[actor] = {"point": end_position, "evidence_id": event["evidence_id"]}
        elif conflict:
            self._positions.pop(actor, None)

    def _path_hint(self, event, receipt, path, outcome):
        points = receipt.get("successful_points")
        kind = "confirmed_waypoints_only"
        origin = None
        if isinstance(points, list) and points:
            points = [point(p) for p in points]
            if any(p is None for p in points) or len(points) > 32:
                return None
            # Omitted internal movement before a nested receipt makes its
            # starting point unknown. Never connect across that missing path.
            if not path and self._starts.get(event["evidence_id"]):
                origin = self._starts[event["evidence_id"]]
                points = [origin["point"], *points]
                kind = "executed_polyline"
        elif outcome == "completed" and receipt.get("command") == "move_to":
            top = _primary_receipt(event["tool_name"], event["payload"])
            direct = not path or (isinstance(top, dict) and top.get("movement_strategy") == "move_to")
            origin = self._starts.get(event["evidence_id"])
            end = point(top.get("final_position")) if isinstance(top, dict) else None
            if not end and not path:
                end = point(event["args"])
            if not direct or not origin or not end:
                return None
            points, kind = [origin["point"], end], "executed_segment"
        else:
            return None
        return {"kind": kind, "points": points,
                "start_evidence_id": origin["evidence_id"] if origin else None,
                "meaning": "contact_during_action; not_target_center; no_radius_guarantee"}

    def _view(self, requirement):
        name = requirement["target_name"]
        entities = self._entities.values() if self.index_epoch == self.index.epoch else ()
        matches = [e for e in entities if e["kind"] == "target" and e["name"] == name]
        actors = [e["id"] for e in entities
                  if e["kind"] == "drone" and e["name"] == requirement["required_actor"]]
        actor = actors[0] if len(actors) == 1 else None
        reports = copy.deepcopy(self._reports.get(name, [])) if self.index_epoch == self.index.epoch else []
        count = self._report_counts.get(name, 0) if reports else 0
        pos = matches[0]["facts"].get("position") if len(matches) == 1 else None
        localized = bool(pos and point(pos["value"]) and
                         (pos["validity"] == "observed" or name.startswith(("Fixed Target ", "Waypoint "))))
        knowledge = "ambiguous" if len(matches) > 1 else "localized" if localized else "reported_only" if reports or matches else "unknown"
        current = [r for r in reports if r["command_id"] == self.command_id and
                   actor and r["actor_id"] == actor and r["ordering"] == "certain"]
        state, evidence, evidence_kind = "unattempted", None, None
        target_id = matches[0]["id"] if len(matches) == 1 else None
        xy = goal_xy(pos["value"]) if localized else None
        xy_matches = [e for e in entities if e["kind"] == "target" and xy and
                      goal_xy(e["facts"].get("position", {}).get("value")) == xy]
        for event in self._events:
            if not actor or str(event["args"].get("drone_id")) != actor or event["tool_name"] not in _MOVEMENT:
                continue
            args = event["args"]
            aimed = bool(target_id and args.get("target_id") == target_id)
            link_kind = "explicit_target_id" if aimed else None
            # A flight goal can share a ground target's XY at another altitude.
            # This is an action association, never proof of intent or arrival.
            if not aimed and len(xy_matches) == 1 and goal_xy(args) == xy:
                aimed, link_kind = True, "requested_xy_matches_unique_known_target_not_arrival"
            if aimed:
                state = event["status"] if event["status"] in {"partial", "blocked"} else "attempted"
                evidence = event["evidence_id"]
                evidence_kind = link_kind
        if current and requirement["relation"] == "visit" and len(matches) <= 1:
            state, evidence = "evidenced", current[-1]["evidence_id"]
            evidence_kind = "current_actor_positive_contact_receipt"
        report = reports[-1] if reports else None
        # Prefer a bounded, usable path over a newer name-only report. Both
        # retain their original command/actor; no completion is transferred.
        hints = [r for r in reports if r["localization_hint"]]
        if hints:
            report = hints[-1]
        return {**requirement, "actor_id": actor, "target_id": target_id,
                "knowledge_state": knowledge, "position": pos if localized else None,
                "execution_state": state, "execution_evidence_id": evidence,
                "execution_evidence_kind": evidence_kind,
                "execution_scope": "current_command_actor_visit_only; other_clauses_not_evaluated",
                "report": report, "reports_omitted": max(0, count - int(bool(report)))}

    def projection(self):
        with self._lock:
            items = [self._view(r) for r in self.requirements]
            # Evidence useful to unresolved work precedes unsupported refs and
            # already evidenced visits. This never removes source obligations.
            items.sort(key=lambda r: (r["execution_state"] == "evidenced", r["relation"] != "visit",
                                      r["knowledge_state"] != "reported_only"))
            return items

    def summary(self):
        data = {"schema": "m1-r1", "items": [], "omitted": len(self.requirements),
                "mission_completion": "not_evaluated", "original_task_controls": True}
        for item in self.projection():
            if len(data["items"]) >= 4:
                break
            data["items"].append(item)
            data["omitted"] -= 1
            if len(encode(data)) > self.max_summary_chars:
                data["items"].pop()
                data["omitted"] += 1
        return encode(data) if len(encode(data)) <= self.max_summary_chars else ""

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "m1-r1"
            result["task_evidence"] = {"session_id": self.index.session_id, "epoch": self.index_epoch,
                                       "requirements": self.projection(), "summary": self.summary(),
                                       "model_requests": copy.deepcopy(self.model_requests),
                                       "injections": copy.deepcopy(self.injections)}
            return result
