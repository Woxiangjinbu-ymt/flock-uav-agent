"""Resume original requirements from sourced evidence, without gating tools.

Visits are historical actor-specific events; localization is separate. Landing
is a terminal route requirement and loses support after unexplained actions.
This bounded projection is advisory, never a whole-mission checker.
"""
from __future__ import annotations

import copy
import re
from collections import Counter

from command_results import command_outcome
from execution_memory_v4 import ExecutionMemoryLedgerV4
from task_evidence import encode, point
from task_evidence_r3 import receipt_points, _matches
from task_progress import _ACTIONS, _MOVEMENT

_SIMPLE_VISIT = re.compile(
    r"\s*(Drone \d+)\s+(?:must\s+|should\s+)?(?:visit|reach|navigate to|flies to|fly to)\s+"
    r"((?:(?:Fixed|Moving|Circle|Polygon) Target \d+|Waypoint \d+))\s*[.;]?\s*", re.I)
_POLICY = (
    "Keep sourced done requirements; continue only remaining/unverified original requirements. "
    "A visit receipt proves that actor's visit, not the target center. Missing localization or "
    "target is_reached=false does not undo that visit or require confirmation unless the original task asks. "
    "Sourced landing below resolves the older route view's unchecked landing only. "
    "If remaining and unverified are empty and nothing is omitted, report the evidenced result."
)


class ExecutionMemoryLedgerV5(ExecutionMemoryLedgerV4):
    """V4-compatible recorder plus a bounded completion/remaining projection."""

    def _landing_evidence(self, route):
        actor = route.get("actor_id")
        if not actor or route["ordered_prefix"] != len(route["goals"]):
            return None
        final = route["goals"][-1]["ordered_evidence"]
        boundary = final["end_sequence"]
        actions = [e for e in self._events if str(e["args"].get("drone_id", "")) == actor
                   and e["tool_name"] in _ACTIONS and e["start_sequence"] > boundary]
        if not actions:
            return None

        def successful_land(event):
            receipt = event["payload"]
            return (event["tool_name"] == "land" and event["end_sequence"] is not None
                    and event["status"] == "completed" and not self._actor_conflict(event, actor)
                    and isinstance(receipt, dict) and receipt.get("command") == "land"
                    and str(receipt.get("drone_id", "")) == actor and command_outcome(receipt) == "completed")

        event = actions[-1]
        if not successful_land(event):
            return None
        endpoint = route["goals"][-1]["point"]
        final_event = next(e for e in self._events if e["evidence_id"] == final["evidence_id"])
        final_points = receipt_points(final_event)
        anchors = ([(boundary, final_points[-1]["actual_point"])]
                   if final_points and _matches(final_points[-1], endpoint) else [])
        reads = []
        for read in self._events:
            if read["start_sequence"] <= boundary or read["end_sequence"] is None:
                continue
            if str(read["args"].get("drone_id", "")) == actor and not self._actor_conflict(read, actor):
                proofs = receipt_points(read)
                if proofs and _matches(proofs[-1], endpoint) and read["end_sequence"] < event["start_sequence"]:
                    anchors.append((read["end_sequence"], proofs[-1]["actual_point"]))
            payload = read["payload"]
            rows = payload if read["tool_name"] == "list_drones" and isinstance(payload, list) else (
                [payload] if read["tool_name"] == "get_drone_status" and
                str(read["args"].get("drone_id", "")) == actor else [])
            for row in rows:
                if (read["status"] != "observed" or self._actor_conflict(read, actor)
                        or self._failed(row) or str(row.get("id", "")) != actor):
                    continue
                xyz = point(row.get("position"))
                reads.append((read, row, xyz))
                if (xyz is not None and read["end_sequence"] < event["start_sequence"]
                        and (self._same_point(xyz, endpoint) or self._same_point(xyz, final["actual_point"]))):
                    anchors.append((read["end_sequence"], xyz))
        if not anchors:
            return None
        anchor_sequence, anchor_position = max(anchors, key=lambda value: value[0])
        # A retry may recover from an earlier failed/unknown action only after
        # a fresh trusted endpoint read or executed return establishes position.
        if any(action["start_sequence"] > anchor_sequence and not successful_land(action) for action in actions):
            return None
        for read, row, xyz in reads:
            if read["start_sequence"] <= anchor_sequence:
                continue
            if xyz is not None:
                if abs(xyz[0] - anchor_position[0]) > .02 or abs(xyz[1] - anchor_position[1]) > .02:
                    return None
                if read["start_sequence"] > event["end_sequence"] and abs(xyz[2]) > .02:
                    return None
            if (read["start_sequence"] > event["end_sequence"] and
                    row.get("status") in {"flying", "hovering", "taking_off", "moving", "landing"}):
                return None
        return self._source(self.command_id, event["evidence_id"])

    def completion_context(self, actor_id=None):
        """Full audit view; model notes and plan.done never enter this evidence."""
        with self._lock:
            valid = self.index_epoch == self.index.epoch
            requirements = self.projection()
            routes = self.route_projection()
            done, remaining = [], []
            duplicate = Counter((r["required_actor"], r["target_name"]) for r in requirements)
            for requirement in requirements:
                span = requirement["source_span"]
                explicit = _SIMPLE_VISIT.fullmatch(self.command[span[0]:span[1]])
                item = {"id": requirement["id"], "kind": "visit", "actor": requirement.get("actor_id"),
                        "required_actor": requirement["required_actor"], "target": requirement["target_name"],
                        "source_span": copy.deepcopy(span)}
                unique = duplicate[(requirement["required_actor"], requirement["target_name"])] == 1
                eid = requirement.get("execution_evidence_id")
                report = next((r for r in self._reports.get(requirement["target_name"], [])
                               if valid and r["epoch"] == self.index_epoch and r["command_id"] == self.command_id
                               and r["actor_id"] == requirement.get("actor_id") and r["evidence_id"] == eid
                               and r["ordering"] == "certain"), None)
                # Reused command IDs are not task identity: require the cited
                # positive receipt to be present in this ledger's own events.
                local = next((e for e in self._events if e["evidence_id"] == eid), None)
                if report and local:
                    receipt = local["payload"]
                    try:
                        for key in report["message_path"][:-1]:
                            receipt = receipt[key]
                        value = receipt[report["message_path"][-1]]
                    except (KeyError, IndexError, TypeError):
                        value = None
                    if (local["tool_name"] not in _MOVEMENT or local["end_sequence"] is None
                            or command_outcome(receipt) not in {"completed", "partial"}
                            or str(local["args"].get("drone_id", "")) != requirement.get("actor_id")
                            or str(receipt.get("drone_id", "")) != requirement.get("actor_id")
                            or self._actor_conflict(local, requirement.get("actor_id"))
                            or value != report["message"]):
                        report = None
                else:
                    report = None
                if (explicit and unique and requirement["relation"] == "visit" and report
                        and requirement["execution_state"] == "evidenced"):
                    done.append({**item, "source": self._source(self.command_id, eid),
                                 "scope": "actor_visit_only; localization_not_required_by_this_requirement"})
                else:
                    remaining.append({**item, "state": "unconfirmed" if explicit and unique else "unverified_interpretation"})
            for ordinal, route in enumerate(routes, 1):
                item = {"id": f"route-{ordinal}", "actor": route["actor_id"],
                        "required_actor": route["required_actor"], "source_span": copy.deepcopy(route["source_span"])}
                prefix = route["ordered_prefix"] if valid else 0
                if prefix:
                    last = route["goals"][prefix - 1]["ordered_evidence"]
                    done.append({**item, "kind": "route_coordinates", "ordered": prefix, "total": len(route["goals"]),
                                 "source": self._source(self.command_id, last["evidence_id"])})
                if prefix < len(route["goals"]):
                    remaining.append({**item, "kind": "route_coordinates", "from_ordinal": prefix + 1,
                                      "goals": [{"ordinal": g["ordinal"], "point": copy.deepcopy(g["point"])}
                                                for g in route["goals"][prefix:]]})
                landing = self._landing_evidence(route) if valid else None
                if landing:
                    done.append({**item, "kind": "landing", "source": landing})
                else:
                    remaining.append({**item, "kind": "landing", "state": "unconfirmed",
                                      "after": "ordered_route_coordinates"})
            # Only a fully matched simple visit, or complete R3 route blocks,
            # consume source text. Extra height/photo/order/conditional clauses
            # remain literal unverified spans, never silently completed.
            covered = [r["source_span"] for r in routes]
            if _SIMPLE_VISIT.fullmatch(self.command) and len(requirements) == 1:
                covered = [[0, len(self.command)]]
            residual, cursor = [], 0
            for start, end in sorted(covered):
                if self.command[cursor:start].strip():
                    residual.append({"source_span": [cursor, start], "text": self.command[cursor:start]})
                cursor = max(cursor, end)
            if self.command[cursor:].strip():
                residual.append({"source_span": [cursor, len(self.command)], "text": self.command[cursor:]})
            key = lambda row: actor_id is not None and row.get("actor") != str(actor_id)
            done.sort(key=key)
            remaining.sort(key=key)
            return {"scene_valid": valid, "command_id": self.command_id, "epoch": self.index_epoch,
                    "mission_completion": "not_evaluated", "done": done, "remaining": remaining,
                    "unverified": residual, "scope": "same_command_actor_epoch; original_task_controls"}

    def _resume_view(self, full, allowance):
        data = {"done": [], "remaining": [], "unverified": [], "omitted": {}, "policy": _POLICY}
        rows = {}
        for key in ("done", "remaining"):
            rows[key] = []
            for row in full[key]:
                item = {k: copy.deepcopy(row[k]) for k in ("id", "kind", "actor", "source", "ordered", "total",
                                                          "from_ordinal", "state", "after") if row.get(k) is not None}
                rows[key].append(item)
        rows["unverified"] = [{"source_span": r["source_span"], "text": r["text"][:100],
                               **({"omitted_chars": len(r["text"]) - 100} if len(r["text"]) > 100 else {})}
                              for r in full["unverified"]]
        data["omitted"] = {key: len(value) for key, value in rows.items() if value}
        # Explicit omitted counts are retained whenever a bounded view cannot
        # fit every row, so an empty visible remaining list never means done.
        for offset in range(max((len(v) for v in rows.values()), default=0)):
            for key in ("remaining", "done", "unverified"):
                if offset >= len(rows[key]):
                    continue
                candidate = copy.deepcopy(data)
                candidate[key].append(rows[key][offset])
                candidate["omitted"][key] -= 1
                if not candidate["omitted"][key]:
                    candidate["omitted"].pop(key)
                if len(encode(candidate)) <= allowance:
                    data = candidate
        if not data["omitted"]:
            data.pop("omitted")
        return data if len(encode(data)) <= allowance else None

    def decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        with self._lock:
            base = super().decision_context(actor_id, decision_kind, plan)
            full = self.completion_context(actor_id)
            # V4 facts and query contract retain their original allocation.
            available = min(3900, self.max_summary_chars) - len(encode(base)) - len(',"resume":')
            resume = self._resume_view(full, min(1100, available))
            if resume is not None:
                return {**base, "resume": resume}
            candidate = {**base, "resume_omitted": True}
            return candidate if len(encode(candidate)) <= min(3900, self.max_summary_chars) else base

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "execution-memory-v5"
            result["completion_memory"] = self.completion_context()
            return result
