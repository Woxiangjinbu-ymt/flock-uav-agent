"""Compact decision views and advisory reuse of recent local sensing evidence.

V1 remains the event recorder and full audit source. V2 neither caches tool
results nor rejects queries. Identical empty scans are a conditional planning
hint for static-target localization, not proof of coverage or target absence.
"""
from __future__ import annotations

import copy
import math

from execution_memory import ExecutionMemoryLedger, SessionEvidenceIndexMemory, _KINDS, _SENSE
from task_evidence import encode, point
from task_progress import _ACTIONS

_ADVICE = ("For this static-target search, if scene/sensing conditions are unchanged, avoid another immediate "
           "identical scan; use a different feasible point along the witnessed path if available. Refresh after "
           "failure/movement, changed conditions, a dynamic target, or if purpose differs. Queries remain allowed.")
_SCOPE = ("Evidence only: empty is local, not absence or coverage. Visits do not transfer actors/tasks. "
          "Plan state is not proof; unlisted original clauses remain unverified.")


class ExecutionMemoryLedgerV2(ExecutionMemoryLedger):
    """V1-compatible APIs; compact projection with optional derived advice."""

    def __init__(self, command, command_id="", *, guidance_enabled=True, **kwargs):
        self.guidance_enabled = bool(guidance_enabled)
        super().__init__(command, command_id, **kwargs)

    @staticmethod
    def _default_query(event, actor):
        args = event["args"]
        return (event["tool_name"] == "get_nearby_entities"
                and str(args.get("drone_id", "")) == actor
                and set(args) <= {"drone_id", "detail"}
                and ("detail" not in args or args["detail"] == "summary"))

    @staticmethod
    def _same_point(left, right):
        return point(left) is not None and point(right) is not None and math.dist(left, right) <= 0.000001

    @staticmethod
    def _failed(payload):
        return (not isinstance(payload, dict) or bool(payload.get("error") or payload.get("tool_exception"))
                or payload.get("success") is False or payload.get("status") in
                {"error", "failed", "failure", "blocked", "cancelled", "timeout"})

    def _sensing_signature(self, actor, before_sequence):
        """Known public radius only; unknown is not an unchanged-world claim."""
        signature = {}
        for event in self._events:
            if event["end_sequence"] is None or event["end_sequence"] >= before_sequence:
                continue
            payload = event["payload"]
            rows = payload if event["tool_name"] == "list_drones" and isinstance(payload, list) else (
                [payload] if event["tool_name"] == "get_drone_status" and event["status"] == "observed" else [])
            for row in rows:
                if not self._failed(row) and str(row.get("id", "")) == actor and "perceived_radius" in row:
                    signature["perceived_radius"] = copy.deepcopy(row["perceived_radius"])
        return signature

    def _actor_events(self, actor):
        for event in self._events:
            args = event["args"]
            ids = args.get("drone_ids")
            direct = str(args.get("drone_id", "")) == actor
            collective = event["tool_name"] == "sense_nearby_entities" and (
                ids is None or isinstance(ids, list) and actor in [str(v) for v in ids])
            if direct or collective:
                yield event

    def empty_query_checks(self, actor_id=None):
        """Return conservative current-command runs of successful empty scans.

        Counts cover retained observations only. Any same-actor action, failed,
        pending, non-default or different sensing, or changed public position
        ends the run. This does not assert the calls had the same intention.
        """
        with self._lock:
            if self.index_epoch != self.index.epoch:
                return []
            candidates = [r for r in self.projection()
                          if r["relation"] == "visit"
                          and r["target_name"].startswith(("Fixed Target ", "Waypoint "))
                          and r["knowledge_state"] in {"unknown", "reported_only"}
                          and r["execution_state"] != "evidenced"]
            if not candidates:
                return []
            actors = {str(r["observer_id"]) for r in self._observation_attempts
                      if r["command_id"] == self.command_id and r["observer_id"]}
            if actor_id is not None:
                actors &= {str(actor_id)}
            by_event = {r["evidence_id"]: r for r in self._observation_attempts
                        if r["command_id"] == self.command_id and r["epoch"] == self.index_epoch
                        and r["tool"] == "get_nearby_entities"}
            result = []
            for actor in sorted(actors):
                actor_goals = [r for r in candidates if r.get("actor_id") == actor]
                if not actor_goals:
                    continue  # A scout's purpose is not inferred from another actor's task.
                current = self._positions.get(actor)
                if not current or point(current.get("point")) is None:
                    continue
                current_radius = self._sensing_signature(actor, self._sequence + 1)
                fact = self._entities.get(f"drone:{actor}", {}).get("facts", {}).get("position")
                if fact and fact["validity"] == "observed" and not self._same_point(point(fact["value"]), current["point"]):
                    continue
                run = []
                for event in reversed(list(self._actor_events(actor))):
                    # Even a rejected action may have side effects. Do not
                    # reuse emptiness across movement away and back, either.
                    if event["tool_name"] in _ACTIONS:
                        break
                    if event["tool_name"] == "get_drone_status":
                        payload = event["payload"]
                        if (event["end_sequence"] is None or event["status"] != "observed" or self._failed(payload)
                                or str(payload.get("id", "")) != actor
                                or not self._same_point(point(payload.get("position")), current["point"])):
                            break
                    if event["tool_name"] not in _SENSE:
                        continue
                    record = by_event.get(event["evidence_id"])
                    if (not self._default_query(event, actor) or not record
                            or event["end_sequence"] is None or event["status"] != "observed"
                            or record["state"] != "empty_targets" or record["ordering"] != "certain"
                            or not record.get("position")
                            or self._sensing_signature(actor, event["start_sequence"]) != current_radius
                            or not self._same_point(record["position"]["point"], current["point"])):
                        break
                    # Unknown same-actor overlap, including non-movement
                    # observations, is conservatively ineligible.
                    if self._actor_conflict(event, actor):
                        break
                    run.append(record)
                if not run or self._sequence - run[0]["end_sequence"] > 32:
                    continue
                result.append({"actor": actor, "position": copy.deepcopy(current["point"]),
                    "retained_count": len(run), "last": {"command_id": self.command_id,
                                                   "evidence_id": run[0]["evidence_id"]},
                    "source_events": [r["evidence_id"] for r in run],
                    "applicable_goals": [{"id": r["id"], "target": r["target_name"]} for r in actor_goals],
                    "scope": "current_command_retained_empty_queries; static_target_localization_only; intent_not_inferred"})
            return result

    @staticmethod
    def _source(command_id, evidence_id):
        return {key: value for key, value in (("command", command_id), ("event", evidence_id)) if value is not None}

    def _goal_view(self, requirement):
        item = {"id": requirement["id"], "target": requirement["target_name"],
                "knowledge": requirement["knowledge_state"], "visit": requirement["execution_state"]}
        if requirement.get("required_actor"):
            item["actor"] = requirement["required_actor"]
        if requirement.get("target_id"):
            item["target_id"] = requirement["target_id"]
        if requirement.get("position"):
            fact = requirement["position"]
            item["position"] = copy.deepcopy(fact["value"])
            item["source"] = self._source(fact.get("command_id"), fact.get("evidence_id"))
        report = requirement.get("report")
        hint = report.get("localization_hint") if report else None
        if hint:
            points = hint["points"]
            item["clue"] = {"kind": hint["kind"], "by": report["actor_id"],
                            "source": self._source(report["command_id"], report["evidence_id"]),
                            "points": copy.deepcopy(points[:4]), "not_target_center": True}
            if len(points) > 4:
                item["clue"]["points_omitted"] = len(points) - 4
        elif report:
            item["contact"] = {"by": report["actor_id"],
                               "source": self._source(report["command_id"], report["evidence_id"])}
        if requirement.get("execution_evidence_id"):
            item["visit_source"] = self._source(self.command_id, requirement["execution_evidence_id"])
        if requirement["relation"] != "visit":
            item["relation"] = "unparsed_reference"
        return item

    def _compact_context(self, actor_id, decision_kind, plan):
        valid = self.index_epoch == self.index.epoch
        requirements = self.projection() if valid else []
        # Prefer focused work but retain other actors' obligations and clues;
        # the last sensing actor may be helping a different required actor.
        requirements.sort(key=lambda r: (r["execution_state"] == "evidenced",
                                         actor_id is not None and r.get("actor_id") != actor_id))
        checks = self.empty_query_checks(actor_id) if valid else []
        goals = [self._goal_view(r) for r in requirements]
        routes = []
        route_projection = self.route_projection() if valid else []
        for route in route_projection:
            item = {"actor": route["required_actor"], "ordered": route["ordered_prefix"],
                    "total": len(route["goals"]), "unchecked": "landing_and_other_clauses"}
            next_goal = route["next_unconfirmed"]
            if next_goal:
                item["next"] = {"ordinal": next_goal["ordinal"], "point": next_goal["point"]}
                remaining = route["goals"][route["ordered_prefix"]:]
                item["remaining"] = [{"ordinal": g["ordinal"], "point": g["point"]} for g in remaining[:4]]
                if len(remaining) > 4:
                    item["remaining_omitted"] = len(remaining) - 4
                later = [g["ordinal"] for g in remaining[1:] if g["observed_evidence"] is not None]
                if later:
                    item["later_observed"] = later
            if route["ordered_prefix"]:
                last = route["goals"][route["ordered_prefix"] - 1]["ordered_evidence"]
                item["source"] = self._source(last["command_id"], last["evidence_id"])
            routes.append(item)
        routes.sort(key=lambda r: (r["ordered"] == r["total"],
                                  actor_id is not None and not any(q.get("actor_id") == actor_id and
                                                                 q["required_actor"] == r["actor"] for q in route_projection)))
        queries = [r for r in reversed(self._observation_attempts) if valid and r["command_id"] == self.command_id
                   and (actor_id is None or r["observer_id"] == actor_id)]
        query_view = []
        if queries and not checks:
            last = self._observation_view(queries[0])
            query_view = [{"actor": last["observer_id"], "state": last["state"],
                           "freshness": last["freshness"],
                           "source": self._source(last["command_id"], last["evidence_id"])}]
            query_view = [{k: v for k, v in row.items() if v is not None} for row in query_view]
        check_views = []
        for check in checks:
            row = {k: copy.deepcopy(check[k]) for k in ("actor", "position", "retained_count", "last")}
            row["applicable_goals"] = copy.deepcopy(check["applicable_goals"][:2])
            if len(check["applicable_goals"]) > 2:
                row["goals_omitted"] = len(check["applicable_goals"]) - 2
            check_views.append(row)
        data = {"schema": "execution-memory-v2", "mission_completion": "not_evaluated",
                "scope": _SCOPE, "goals": goals, "routes": routes,
                "checked_here": check_views, "last_query": query_view,
                "aggregate": [{"threshold": r["threshold_percent"], "operator": r["operator"],
                               "state": r["threshold_state"], "freshness": r["freshness"],
                               **({"percent": r["observation"]["progress_percentage"]} if r["observation"] else {})}
                              for r in self.aggregate_projection()] if valid else []}
        checked_actors = {c["actor"] for c in checks}
        ambiguous_purpose = any(r["relation"] != "visit" or
                                r.get("actor_id") in checked_actors and
                                not r["target_name"].startswith(("Fixed Target ", "Waypoint "))
                                for r in requirements)
        if checks and not ambiguous_purpose:
            data["advice"] = _ADVICE
        if not valid:
            data["scene_changed"] = True
        plan_view = self._plan_view(plan)
        if plan_view:
            data["plan"] = {k: v for k, v in plan_view.items() if k in {"plan_id", "status", "cursor"} and v is not None}
        return data

    def _pack(self, data):
        """Small common cases stay below 1,400 chars; never exceed 3,900."""
        cap = min(3900, self.max_summary_chars)
        soft = min(1400, cap)
        keys = ["checked_here", "goals", "routes", "last_query", "aggregate"]
        # Advice comes first in the serialized, insertion-ordered JSON object;
        # sort_keys in the inherited encoder also puts 'advice' first.
        packed = {k: copy.deepcopy(v) for k, v in data.items() if k not in keys}
        packed["omitted"] = {}
        rows = {key: data[key] for key in keys}
        for key in keys:
            if rows[key]:
                packed["omitted"][key] = len(rows[key])
        for offset in range(max((len(v) for v in rows.values()), default=0)):
            for key in keys:
                if offset >= len(rows[key]):
                    continue
                candidate = copy.deepcopy(packed)
                candidate.setdefault(key, []).append(rows[key][offset])
                candidate["omitted"][key] -= 1
                if candidate["omitted"][key] == 0:
                    del candidate["omitted"][key]
                # Preserve a first actionable goal/route/check even when a
                # sourced clue makes it modestly exceed the soft target.
                limit = cap if offset == 0 and key in {"checked_here", "goals", "routes"} else soft
                if len(encode(candidate)) <= limit:
                    packed = candidate
        if not packed["omitted"]:
            packed.pop("omitted")
        if len(encode(packed)) <= cap:
            return packed
        minimal = {"schema": "execution-memory-v2", "truncated": True, "mission_completion": "not_evaluated"}
        return minimal if len(encode(minimal)) <= cap else {}

    def decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        if decision_kind not in _KINDS:
            raise ValueError("Unsupported memory decision kind")
        with self._lock:
            value = self._pack(self._compact_context(str(actor_id) if actor_id is not None else None,
                                                     decision_kind, plan))
            # Ablation changes advice only, never reallocates its saved space
            # to extra evidence or changes which goals survive the budget.
            if not self.guidance_enabled:
                value.pop("advice", None)
            return value

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "execution-memory-v2"
            result["execution_memory_v2"] = {
                "guidance_enabled": self.guidance_enabled,
                "empty_query_checks": self.empty_query_checks(),
                "context": self.decision_context(**self._memory_focus),
                "scope": "compact_advisory_projection; no_query_blocking_or_cached_tool_results",
            }
            return result
