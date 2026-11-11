"""Compact V5 decision views; historical waypoints are not landing positions.

All completion and observation recording remains inherited from V5. Only the
model projection is deduplicated; no tool is called, blocked, or cached here.
"""
from __future__ import annotations

import copy

from execution_memory_v5 import ExecutionMemoryLedgerV5
from task_evidence import encode, point
from task_evidence_r3 import receipt_points

_POLICY = (
    "Continue remaining/unverified; keep sourced done. A visit is not localization: missing center/"
    "target is_reached=false does not undo it. With no remaining/unverified/omitted items, report the result. "
    "Historical route points are not landing coordinates. If landing position is unknown, report successful "
    "landing without coordinates; do not query just to fill it."
)
_SCOPE = "Done is scoped to current command/actor/epoch; clues may be historical. Empty is local, not absence/coverage. Plan is not proof."


class ExecutionMemoryLedgerV6(ExecutionMemoryLedgerV5):
    """Same V5 completion API and audit, with one model-facing requirement list."""

    def _pre_landing_position(self, actor, landing):
        """Latest actual movement endpoint or identity-bound public position."""
        candidates = []
        for event in self._events:
            if (event["end_sequence"] is None or event["end_sequence"] >= landing["start_sequence"]
                    or self._actor_conflict(event, actor)):
                continue
            payload = event["payload"]
            if str(event["args"].get("drone_id", "")) == actor:
                points = receipt_points(event)
                if points:
                    candidates.append((event["end_sequence"], points[-1]["actual_point"], event["evidence_id"]))
            rows = payload if event["tool_name"] == "list_drones" and isinstance(payload, list) else (
                [payload] if event["tool_name"] == "get_drone_status"
                and str(event["args"].get("drone_id", "")) == actor else [])
            matches = [r for r in rows if isinstance(r, dict) and str(r.get("id", "")) == actor]
            if (event["status"] == "observed" and len(matches) == 1 and not self._failed(matches[0])
                    and point(matches[0].get("position")) is not None):
                candidates.append((event["end_sequence"], point(matches[0]["position"]), event["evidence_id"]))
        if not candidates:
            return None
        _, xyz, evidence_id = max(candidates, key=lambda item: item[0])
        return {"position": xyz, "source": self._source(self.command_id, evidence_id)}

    def terminal_observations(self):
        """Sourced positions after supported landing, never a default altitude.

        No position in a successful land receipt means unknown, not the previous
        waypoint and not an inferred ground constant. A later failed/ambiguous
        position refresh is explicit rather than silently reusing an old read.
        """
        with self._lock:
            full = super().completion_context()
            if not full["scene_valid"]:
                return []
            result = []
            events = {event["evidence_id"]: event for event in self._events}
            for requirement in full["done"]:
                if requirement["kind"] != "landing":
                    continue
                actor = requirement["actor"]
                land = events[requirement["source"]["event"]]
                row = {"actor": actor, "landing_source": copy.deepcopy(requirement["source"]),
                       "position_state": "unknown", "scope": "post_landing_public_observation_only"}
                receipt = land["payload"]
                # These explicit returned fields are read as returned; requested
                # coordinates, messages and prior movement points are excluded.
                returned = [(key, point(receipt.get(key))) for key in ("final_position", "position")
                            if key in receipt]
                conflicts = []
                reference = self._pre_landing_position(actor, land)
                if returned:
                    row["reported_positions"] = [{"value": copy.deepcopy(receipt[key]),
                                                   "source": copy.deepcopy(requirement["source"]), "path": [key]}
                                                  for key, _ in returned]
                for key, xyz in returned:
                    if xyz is not None and (abs(xyz[2]) > .02 or reference and any(
                            abs(xyz[index] - reference["position"][index]) > .02 for index in (0, 1))):
                        conflicts.append({"reason": "landing_position_conflicts_with_public_endpoint_or_grounded_state",
                                          "source": copy.deepcopy(requirement["source"]), "path": [key],
                                          "reference": copy.deepcopy(reference)})
                if len(returned) == 2 and all(xyz is not None for _, xyz in returned) and not self._same_point(
                        returned[0][1], returned[1][1]):
                    conflicts.append({"reason": "landing_position_fields_disagree", "source": copy.deepcopy(requirement["source"])})
                if returned and all(xyz is not None for _, xyz in returned) and all(
                        self._same_point(xyz, returned[0][1]) for _, xyz in returned):
                    row.update(position_state="observed", position=returned[0][1],
                               position_source=copy.deepcopy(requirement["source"]),
                               position_path=[returned[0][0]])
                elif returned:
                    row["uncertainty"] = "invalid_or_conflicting_landing_position_fields"
                for event in sorted(self._events, key=lambda item: item["start_sequence"]):
                    if event["start_sequence"] <= land["end_sequence"]:
                        continue
                    tool, payload = event["tool_name"], event["payload"]
                    direct = tool == "get_drone_status" and str(event["args"].get("drone_id", "")) == actor
                    listing = tool == "list_drones"
                    if not direct and not listing:
                        continue
                    matches = [payload] if direct else ([item for item in payload if isinstance(item, dict)
                                                         and str(item.get("id", "")) == actor]
                                                        if isinstance(payload, list) else [])
                    observed = matches[0] if len(matches) == 1 else None
                    valid = (event["end_sequence"] is not None and event["status"] == "observed"
                             and not self._actor_conflict(event, actor) and not self._failed(observed)
                             and str(observed.get("id", "")) == actor
                             and observed.get("status") in {"idle", "landed"}
                             and point(observed.get("position")) is not None)
                    for key in ("position", "position_source", "position_path", "uncertainty", "latest_read_source"):
                        row.pop(key, None)
                    if valid:
                        actual = point(observed["position"])
                        if any(xyz is not None and any(abs(xyz[index] - actual[index]) > .02 for index in range(3))
                               for _, xyz in returned):
                            conflicts.append({"reason": "land_receipt_disagrees_with_post_landing_status",
                                              "source": self._source(self.command_id, event["evidence_id"]),
                                              "position": actual})
                        row.update(position_state="observed", position=point(observed["position"]),
                                   position_source=self._source(self.command_id, event["evidence_id"]),
                                   position_path=(["position"] if direct else [payload.index(observed), "position"]))
                    else:
                        row.update(position_state="unknown", uncertainty="latest_position_read_unverified",
                                   latest_read_source=self._source(self.command_id, event["evidence_id"]))
                if conflicts:
                    row["conflicts"] = conflicts
                    row["position_state"] = "conflicting"
                    if "position" in row:
                        row["latest_position_observation"] = {key: copy.deepcopy(row[key]) for key in
                                                              ("position", "position_source", "position_path") if key in row}
                    for key in ("position", "position_source", "position_path"):
                        row.pop(key, None)
                result.append(row)
            return result

    def _completion_rows(self, full):
        requirements = {r["id"]: r for r in self.projection()}
        terminal = {r["actor"]: r for r in self.terminal_observations()}
        routes = {f"route-{index}": r for index, r in enumerate(self.route_projection(), 1)}
        rows = {"done": [], "remaining": [], "unverified": []}
        for key in ("done", "remaining"):
            for original in full[key]:
                item = {k: copy.deepcopy(original[k]) for k in ("id", "kind", "actor", "target", "source",
                        "ordered", "total", "from_ordinal", "state", "after") if original.get(k) is not None}
                if original.get("actor") is None and original.get("required_actor"):
                    item["required_actor"] = original["required_actor"]
                if key == "remaining" and original["kind"] == "visit":
                    goal = self._goal_view(requirements[original["id"]])
                    item.update({k: copy.deepcopy(goal[k]) for k in ("knowledge", "target_id", "position", "source",
                                                                     "clue", "contact", "relation") if k in goal})
                if "goals" in original:
                    item["goals"] = copy.deepcopy(original["goals"][:4])
                    if len(original["goals"]) > 4:
                        item["goals_omitted"] = len(original["goals"]) - 4
                    route = routes[original["id"]]
                    later = [g["ordinal"] for g in route["goals"][route["ordered_prefix"] + 1:]
                             if g["observed_evidence"] is not None]
                    if later:
                        item["later_observed"] = later
                if key == "done" and original["kind"] == "landing":
                    observation = terminal[original["actor"]]
                    item["position"] = copy.deepcopy(observation.get("position", "unknown"))
                    if observation.get("conflicts"):
                        item["evidence_conflict"] = True
                        rows["unverified"].append({"kind": "terminal_evidence_conflict", "actor": original["actor"],
                            "source": copy.deepcopy(original["source"]),
                            "text": "Landing position evidence conflicts. Report uncertainty; do not substitute route coordinates."})
                    if "position_source" in observation:
                        item["position_source"] = copy.deepcopy(observation["position_source"])
                rows[key].append(item)
        for row in full["unverified"]:
            item = {"source_span": copy.deepcopy(row["source_span"]), "text": row["text"][:100]}
            if len(row["text"]) > 100:
                item["omitted_chars"] = len(row["text"]) - 100
            rows["unverified"].append(item)
        return rows

    def decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        with self._lock:
            base = super().decision_context(actor_id, decision_kind, plan)
            full = super().completion_context(actor_id)
            rows = self._completion_rows(full)
            result = {"schema": "execution-memory-v6", "command_id": self.command_id, "epoch": self.index_epoch,
                      "mission_completion": "not_evaluated", "scope": _SCOPE}
            if not full["scene_valid"]:
                result["scene_changed"] = True
            # Preserve the V5 query decision trigger, facts and contract exactly;
            # compression is not a relaxation of the query-reason requirement.
            for key in ("checked_here", "advice", "query_decision_contract", "last_query", "aggregate", "plan"):
                if key in base:
                    result[key] = copy.deepcopy(base[key])
            omitted = {key: value for key, value in base.get("omitted", {}).items()
                       if key not in {"goals", "routes"}}
            if omitted:
                result["source_omitted"] = omitted
            resume = {"done": [], "remaining": [], "unverified": [], "policy": _POLICY,
                      "omitted": {key: len(value) for key, value in rows.items() if value}}
            result["resume"] = resume
            cap = min(3900, self.max_summary_chars)
            if len(encode(result)) > cap:
                # A tiny cap cannot keep the existing contract plus even row
                # omission metadata. Preserve V5 rather than drop its evidence.
                return base
            for offset in range(max((len(value) for value in rows.values()), default=0)):
                for key in ("remaining", "done", "unverified"):
                    if offset >= len(rows[key]):
                        continue
                    candidate = copy.deepcopy(result)
                    candidate["resume"][key].append(rows[key][offset])
                    candidate["resume"]["omitted"][key] -= 1
                    if not candidate["resume"]["omitted"][key]:
                        candidate["resume"]["omitted"].pop(key)
                    if len(encode(candidate)) <= cap:
                        result = candidate
            if not result["resume"]["omitted"]:
                result["resume"].pop("omitted")
            return result

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "execution-memory-v6"
            result["terminal_observations"] = self.terminal_observations()
            return result
