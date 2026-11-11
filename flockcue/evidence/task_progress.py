"""M1: command-local, advisory task evidence; never an execution gate.

Only the original instruction, existing blackboard and actual tool replies are
consumed. This module does not call a model, an API or a checker, select an action,
retry a tool, or decide whether a mission is complete.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from command_results import command_outcome


_ENTITY_NAME = re.compile(
    r"\b(?:Drone\s+\d+|(?:Fixed|Moving|Circle|Polygon)\s+Target\s+\d+|"
    r"Waypoint\s+\d+|(?:Circle|Polygon|Ellipse)\s+Obstacle\s+\d+)\b"
)
_CLAUSE = re.compile(r".+?(?:[;。；\n]|(?<!\d)\.(?=\s|$)|$)", re.S)
_KINDS = {"drones": "drone", "targets": "target", "obstacles": "obstacle"}
_ENTITY_FIELDS = (
    "position", "status", "battery_level", "heading", "task_radius",
    "perceived_radius", "radius", "vertices", "width", "height", "dimensions",
    "type", "is_reached", "reached_by", "coverage_percentage", "home_position",
)
_MOVEMENT = {
    "move_to", "move_to_and_sense", "navigate_to", "navigate_to_and_sense",
    "move_towards", "move_towards_and_sense", "move_along_path",
    "move_along_path_and_sense", "move_within_distance", "take_off", "land",
    "return_home", "change_altitude",
}
_ACTIONS = _MOVEMENT | {
    "hover", "rotate", "set_home", "calibrate", "take_photo", "send_message",
    "broadcast", "charge",
}
_ADVISORY = (
    "M1 advisory evidence, not a completion verdict. Original task controls. "
    "Known targets are a subset, not a global total. Photo receipts do not prove "
    "their subject. Inherited/stale/order_unknown facts are not current proof. "
    "This summary neither blocks nor requires actions.\n"
)


def _safe(value: Any) -> Any:
    """Copy JSON data without retaining mutable tool-owned references."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _decode(value: Any) -> Any:
    if hasattr(value, "content"):
        value = value.content
    if isinstance(value, list) and value and all(
        isinstance(item, dict) and item.get("type") == "text" for item in value
    ):
        value = "\n".join(str(item.get("text", "")) for item in value)
    if isinstance(value, str):
        try:
            return json.loads(value, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (ValueError, TypeError):
            return value
    return _safe(value)


def _arguments(value: Any) -> dict:
    value = _decode(value)
    if isinstance(value, dict) and set(value) == {"input_json"}:
        value = _decode(value["input_json"])
    return value if isinstance(value, dict) else {}


def _primary_receipt(tool: str, payload: Any) -> Any:
    # Relative movement reports the relation at top level; its navigation_result
    # is optional supporting detail (None when already within the bound).
    if isinstance(payload, dict) and tool.endswith("_and_sense"):
        return payload.get("navigation_result", payload.get("move_result", payload))
    return payload


class TaskProgressLedger:
    """A fresh ledger per execute(); retries within that execute may reuse it.

    Clauses preserve the source text, not an inferred task contract. Entity
    binding is exact and can remain ambiguous. Facts have individual evidence
    and freshness; action receipts remain separate historical events.
    """

    def __init__(self, command: str, command_id: str = "", *,
                 inherited_blackboard: dict | None = None,
                 max_summary_chars: int = 4000):
        self.command = str(command)
        self.command_id = str(command_id)
        self.max_summary_chars = max(0, int(max_summary_chars))
        self._lock = threading.RLock()
        self._entities: dict[str, dict] = {}
        self._events: list[dict] = []
        self._active: set[str] = set()
        self._invalidations: dict[str, dict[str, str]] = {}
        self._sequence = 0
        self._observer_errors: list[str] = []
        self._clauses = [
            {"id": f"clause-{i + 1}", "start": match.start(), "end": match.end(),
             "text": match.group(),
             "entity_names": list(dict.fromkeys(_ENTITY_NAME.findall(match.group())))}
            for i, match in enumerate(_CLAUSE.finditer(self.command)) if match.group().strip()
        ]
        if inherited_blackboard:
            try:
                self._ingest_collection(inherited_blackboard, "inherited", inherited=True)
            except Exception as exc:
                self.observer_error(exc)

    def observer_error(self, error: Any) -> None:
        with self._lock:
            self._observer_errors.append(str(error)[:300])

    def begin_tool(self, tool_name: str, args: Any, tool_call_id: str = "") -> str:
        with self._lock:
            self._sequence += 1
            evidence_id = f"e{self._sequence}"
            event = {
                "evidence_id": evidence_id, "tool_call_id": str(tool_call_id),
                "tool_name": str(tool_name), "args": _arguments(args),
                "start_sequence": self._sequence, "end_sequence": None,
                "status": "pending", "ordering": "uncertain" if self._active else "certain",
                "overlaps": sorted(self._active), "payload": None,
                "entity_ids": [],
                "scope": "action_receipt" if tool_name in _ACTIONS else "observation",
            }
            for previous in self._events:
                if previous["evidence_id"] in self._active:
                    previous["ordering"] = "uncertain"
                    previous["overlaps"].append(evidence_id)
            self._events.append(event)
            self._active.add(evidence_id)
            return evidence_id

    def end_tool(self, evidence_id: str, output: Any = None, *, error: Any = None) -> None:
        with self._lock:
            event = next(item for item in self._events if item["evidence_id"] == evidence_id)
            if event["end_sequence"] is not None:
                return
            self._sequence += 1
            event["end_sequence"] = self._sequence
            self._active.discard(evidence_id)
            try:
                payload = _decode(output) if error is None else {"tool_exception": str(error)}
                event["payload"] = _safe(payload)
                tool = event["tool_name"]
                receipt = _primary_receipt(tool, payload)
                event["status"] = self._receipt_outcome(receipt, tool)
                if error is not None:
                    event["status"] = "unknown"
                # Unknown tool outcomes may include partial side effects. Never
                # convert a parsing/transport error into proof that nothing moved.
                if tool in _ACTIONS:
                    self._invalidate_action(event)
                self._ingest_reply(event, payload, receipt)
            except Exception as exc:
                event["status"] = "unknown"
                if event["tool_name"] in _ACTIONS:
                    self._invalidate_action(event)
                self.observer_error(exc)

    def observe_tool(self, tool_name: str, args: Any, output: Any,
                     tool_call_id: str = "") -> str:
        evidence_id = self.begin_tool(tool_name, args, tool_call_id)
        self.end_tool(evidence_id, output)
        return evidence_id

    @staticmethod
    def _receipt_outcome(receipt: Any, tool: str) -> str:
        if tool not in _ACTIONS:
            if isinstance(receipt, dict) and receipt.get("error"):
                return "error"
            if isinstance(receipt, str) and receipt.startswith("Error"):
                return "error"
            return "observed" if isinstance(receipt, (dict, list)) else "unknown"
        outcome = command_outcome(receipt)
        # B1 navigation/relative-distance results are derived receipts, without
        # server status. Preserve their declared outcome, including unknown.
        if isinstance(receipt, dict) and receipt.get("outcome") in {
            "completed", "partial", "blocked", "error", "unknown"
        } and ("arrival_verified" in receipt or "within_distance" in receipt):
            outcome = receipt["outcome"]
        return outcome

    def _invalidate_action(self, event: dict) -> None:
        drone_id = event["args"].get("drone_id")
        if not drone_id:
            return
        entity = self._entities.get(f"drone:{drone_id}")
        fields = {"battery_level"}
        # Even a non-movement command can trigger the server's emergency
        # landing and then return error. Rejection is not no-side-effect proof.
        if event["tool_name"] in _MOVEMENT or event["status"] in {"error", "blocked", "unknown"}:
            fields |= {"position", "status", "heading"}
        if event["tool_name"] in {"hover", "calibrate"}:
            fields.add("status")
        if event["tool_name"] == "rotate":
            fields.add("heading")
        if event["tool_name"] == "set_home":
            fields.add("home_position")
        invalidations = self._invalidations.setdefault(f"drone:{drone_id}", {})
        for field in fields:
            invalidations[field] = event["evidence_id"]
            if entity is not None and field in entity["facts"]:
                fact = entity["facts"][field]
                fact["validity"] = "order_unknown" if fact["evidence_id"] in event["overlaps"] else "stale"
                entity["facts"][field]["invalidated_by"] = event["evidence_id"]

    def _entity(self, kind: str, value: dict, evidence_id: str, *,
                inherited: bool = False) -> None:
        if not isinstance(value, dict) or not value.get("id"):
            return
        key = f"{kind}:{value['id']}"
        old = self._entities.get(key)
        if inherited and old is not None:
            return
        entity = self._entities.setdefault(key, {
            "id": str(value["id"]), "kind": kind, "name": None,
            "origin": "inherited" if inherited else "observed", "facts": {},
        })
        if value.get("name"):
            entity["name"] = str(value["name"])
        if not inherited:
            entity["origin"] = "observed"
        facts = value.get("facts", value)
        event = next((e for e in self._events if e["evidence_id"] == evidence_id), None)
        if event is not None and not inherited and entity["id"] not in event["entity_ids"]:
            event["entity_ids"].append(entity["id"])
        for field in _ENTITY_FIELDS:
            if field not in facts or facts[field] is None:
                continue
            previous = entity["facts"].get(field, {})
            conflicting = event is not None and any(
                candidate in event["overlaps"] for candidate in (
                    previous.get("evidence_id"), self._invalidations.get(key, {}).get(field),
                )
            )
            entity["facts"][field] = {
                "value": _safe(facts[field]), "evidence_id": evidence_id,
                "validity": "inherited" if inherited else "order_unknown" if conflicting else "observed",
            }
            if kind == "target" and field in {"is_reached", "reached_by"}:
                entity["facts"][field]["scope"] = "session_history_not_current_position_or_current_task"

    def _ingest_collection(self, collection: dict, evidence_id: str, *,
                           inherited: bool = False) -> None:
        if not isinstance(collection, dict):
            return
        for plural, kind in _KINDS.items():
            values = collection.get(plural, [])
            if isinstance(values, list):
                for value in values:
                    self._entity(kind, value, evidence_id, inherited=inherited)

    def _ingest_reply(self, event: dict, payload: Any, receipt: Any) -> None:
        tool, args, eid = event["tool_name"], event["args"], event["evidence_id"]
        if tool == "list_drones" and isinstance(payload, list):
            for drone in payload:
                self._entity("drone", drone, eid)
        if not isinstance(payload, dict):
            return
        if tool in {"get_drone_status", "get_target_info", "get_obstacle_info"}:
            kind = {"get_drone_status": "drone", "get_target_info": "target",
                    "get_obstacle_info": "obstacle"}[tool]
            if not payload.get("error"):
                self._entity(kind, payload, eid)
        # A returned blackboard is still accumulated last-known knowledge. Only
        # nearby/per_drone fields establish observations from this tool call.
        self._ingest_collection(payload.get("blackboard", {}), eid, inherited=True)
        self._ingest_collection(payload.get("nearby", {}), eid)
        if tool == "get_nearby_entities":
            self._ingest_collection(payload, eid)
        per_drone = payload.get("per_drone", {})
        if isinstance(per_drone, dict):
            for nearby in per_drone.values():
                self._ingest_collection(nearby, eid)
        if tool not in _ACTIONS or not isinstance(receipt, dict):
            return
        drone_id = receipt.get("drone_id", args.get("drone_id"))
        if drone_id and isinstance(receipt.get("final_position"), dict):
            self._entity("drone", {"id": drone_id, "position": receipt["final_position"]}, eid)
        if tool == "move_within_distance":
            for id_key, position_key in (("drone_id", "drone_position_after"),
                                         ("reference_drone_id", "reference_position_after")):
                entity_id = receipt.get(id_key, args.get(id_key))
                if entity_id and isinstance(receipt.get(position_key), dict):
                    self._entity("drone", {"id": entity_id, "position": receipt[position_key]}, eid)
        if tool == "take_photo":
            event["photo_subject"] = "unknown"

    def _bindings(self) -> dict:
        names = list(dict.fromkeys(name for clause in self._clauses for name in clause["entity_names"]))
        bindings = {}
        for name in names:
            matches = [
                {"id": e["id"], "kind": e["kind"], "origin": e["origin"]}
                for e in self._entities.values() if e["name"] == name
            ]
            bindings[name] = {"state": "exact" if len(matches) == 1 else "ambiguous" if matches else "unresolved",
                              "entities": matches}
        return bindings

    def snapshot(self) -> dict:
        with self._lock:
            bindings = self._bindings()
            clauses = copy.deepcopy(self._clauses)
            for clause in clauses:
                states = [bindings[name]["state"] for name in clause["entity_names"]]
                clause["binding_state"] = "exact" if states and all(s == "exact" for s in states) else "unresolved"
                clause["requirement_status"] = "not_evaluated"
            events = copy.deepcopy(self._events)
            for event in events:
                touched = {str(event["args"][key]) for key in (
                    "drone_id", "reference_drone_id", "target_id", "obstacle_id"
                ) if event["args"].get(key)}
                touched.update(event["entity_ids"])
                if isinstance(event["args"].get("drone_ids"), list):
                    touched.update(str(value) for value in event["args"]["drone_ids"])
                event["source_clause_ids"] = [clause["id"] for clause in clauses if any(
                    entity["id"] in touched for name in clause["entity_names"]
                    for entity in bindings[name]["entities"]
                )]
            return _safe({
                "schema_version": 1, "command_id": self.command_id, "command": self.command,
                "clauses": clauses, "entity_bindings": bindings, "entities": self._entities,
                "events": events, "observer_errors": self._observer_errors,
                "mission_completion": "not_evaluated",
                "limits": ["known_targets_not_global_total", "photo_subject_not_inferred",
                           "clauses_are_source_spans_not_a_complete_task_interpretation"],
            })

    def summary(self) -> str:
        snapshot = self.snapshot()
        # Each section receives its own budget, so many entity names cannot
        # evict every action or position. Always emit whole JSON values.
        bindings = [{"name": name, **value, "source_clause_ids": [
            c["id"] for c in snapshot["clauses"] if name in c["entity_names"]
        ]} for name, value in snapshot["entity_bindings"].items()]
        clauses = [{"id": c["id"], "source_excerpt": c["text"][:140],
                    "excerpt_omitted_chars": max(0, len(c["text"]) - 140)} for c in snapshot["clauses"]]
        current = []
        for key, entity in snapshot["entities"].items():
            if entity["name"] not in snapshot["entity_bindings"] and entity["kind"] != "drone":
                continue
            facts = {field: value for field, value in entity["facts"].items()
                     if field in {"position", "status", "battery_level", "is_reached", "reached_by", "coverage_percentage"}}
            if facts:
                current.append({"entity": key, "name": entity["name"], "facts": facts})
        recent = []
        actions = [e for e in snapshot["events"] if e["scope"] == "action_receipt"][-5:]
        selected_ids = {e["evidence_id"] for e in actions + snapshot["events"][-3:]}
        for event in reversed(snapshot["events"]):
            if event["evidence_id"] not in selected_ids:
                continue
            item = {key: event[key] for key in ("evidence_id", "tool_name", "status", "ordering", "source_clause_ids")}
            item["args"] = {key: value for key, value in event["args"].items() if key in {
                "drone_id", "target_id", "reference_drone_id", "x", "y", "z", "distance", "heading",
            }}
            payload = event["payload"]
            if isinstance(payload, dict):
                receipt = _primary_receipt(event["tool_name"], payload)
                if isinstance(receipt, dict):
                    item["effects"] = {key: receipt[key] for key in (
                        "arrival_verified", "within_distance", "distance_remaining", "distance_after",
                        "final_position", "successful_points_count", "unsuccessful_points_count",
                    ) if key in receipt}
            if event.get("photo_subject"):
                item["photo_subject"] = event["photo_subject"]
            recent.append(item)
        candidates = {"clauses": clauses, "entity_bindings": bindings,
                      "recent_tool_evidence": recent, "last_known_entities": current}
        data = {key: [] for key in candidates}
        data["omitted"] = {key: len(items) for key, items in candidates.items()}
        data["observer_error_count"] = len(snapshot["observer_errors"])
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        available = self.max_summary_chars - len(_ADVISORY) - len(encode(data))
        if available < 0:
            fallback = '{"advisory":"M1 summary omitted: character budget","omitted":true}'
            return fallback if len(fallback) <= self.max_summary_chars else "{}" if self.max_summary_chars >= 2 else ""
        weights = {"clauses": .15, "entity_bindings": .25,
                   "recent_tool_evidence": .35, "last_known_entities": .25}
        used = {}
        for key, items in candidates.items():
            remaining = int(available * weights[key])
            used[key] = set()
            for index, item in enumerate(items):
                cost = len(encode(item)) + 1
                if cost > remaining:
                    continue
                data[key].append(item)
                data["omitted"][key] -= 1
                used[key].add(index)
                remaining -= cost
        # Redistribute unused space in a round-robin pass, keeping omissions
        # explicit. No string slicing can produce an apparently complete list.
        for key in ("recent_tool_evidence", "last_known_entities", "entity_bindings", "clauses"):
            for index, item in enumerate(candidates[key]):
                if index in used[key]:
                    continue
                data[key].append(item)
                data["omitted"][key] -= 1
                if len(_ADVISORY) + len(encode(data)) > self.max_summary_chars:
                    data[key].pop()
                    data["omitted"][key] += 1
                else:
                    used[key].add(index)
        clause_order = {clause["id"]: index for index, clause in enumerate(clauses)}
        data["clauses"].sort(key=lambda clause: clause_order[clause["id"]])
        return _ADVISORY + encode(data)


class TaskProgressMiddleware(AgentMiddleware):
    """Observe each original tool exactly once; optionally supply a fresh summary."""

    def __init__(self, ledger: TaskProgressLedger, mode: str = "shadow"):
        if mode not in {"shadow", "summary"}:
            raise ValueError("TaskProgressMiddleware mode must be shadow or summary; off must not instantiate it")
        self.ledger = ledger
        self.mode = mode

    def _begin(self, request: Any) -> str | None:
        try:
            call = request.tool_call
            return self.ledger.begin_tool(call.get("name", "unknown"), call.get("args", {}), call.get("id", ""))
        except Exception as exc:
            self._error(exc)
            return None

    def _error(self, exc: Any) -> None:
        try:
            self.ledger.observer_error(exc)
        except Exception:
            pass

    def _end(self, evidence_id: str | None, result: Any = None, error: Any = None) -> None:
        if evidence_id is not None:
            try:
                self.ledger.end_tool(evidence_id, result, error=error)
            except Exception as exc:
                self._error(exc)

    def wrap_tool_call(self, request, handler):
        evidence_id = self._begin(request)
        try:
            result = handler(request)
        except BaseException as exc:
            self._end(evidence_id, error=exc)
            raise
        self._end(evidence_id, result)
        return result

    async def awrap_tool_call(self, request, handler):
        evidence_id = self._begin(request)
        try:
            result = await handler(request)
        except BaseException as exc:
            self._end(evidence_id, error=exc)
            raise
        self._end(evidence_id, result)
        return result

    def _model_request(self, request):
        if self.mode == "shadow":
            return request
        try:
            summary = self.ledger.summary()
            if not summary:
                return request
            original = request.system_message
            # Build an ephemeral request; never append to graph message history
            # or mutate the caller's system message (including content blocks).
            if original is None:
                system = SystemMessage(content=summary)
            elif isinstance(original.content, str):
                system = original.model_copy(update={"content": original.content + "\n\n" + summary})
            else:
                system = original.model_copy(update={"content": [*original.content, {"type": "text", "text": summary}]})
            return request.override(system_message=system)
        except Exception as exc:
            self._error(exc)
            return request

    def wrap_model_call(self, request, handler):
        return handler(self._model_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._model_request(request))
