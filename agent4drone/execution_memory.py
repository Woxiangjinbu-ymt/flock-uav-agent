"""Opt-in evidence memory for local sensing and interrupted task handoff.

This observer reuses R2/R3 evidence semantics. It performs no actions, completion
checks, retries, task rewriting, or model calls. A plan cursor is control state,
never proof that an original requirement was satisfied.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict, is_dataclass

from task_evidence import encode, point
from task_evidence_r2 import SessionEvidenceIndexR2
from task_evidence_r3 import TaskEvidenceLedgerR3, receipt_points
from task_progress import _ACTIONS, _MOVEMENT

_SENSE = {"get_nearby_entities", "sense_nearby_entities",
          "move_to_and_sense", "move_towards_and_sense",
          "navigate_to_and_sense", "move_along_path_and_sense"}
_KINDS = {"continue", "locate", "act", "confirm", "handoff", "replan", "finish"}
_SCOPE = ("Advisory public evidence only. Local empty sensing is not target absence or "
          "area coverage. Historical contact is not current position; another actor's "
          "visit is not this task's completion. Actual effects are historical receipts. "
          "Plan state is not execution evidence.")


class SessionEvidenceIndexMemory(SessionEvidenceIndexR2):
    """Same-scene bounded observation history, separate from task completion."""

    def __init__(self, session_id=None):
        super().__init__(session_id)
        self.observation_attempts = []
        self.observation_attempt_count = 0

    def clear(self):
        with self.lock:
            super().clear()
            self.observation_attempts.clear()
            self.observation_attempt_count = 0

    def remember_observation(self, record):
        with self.lock:
            if record["epoch"] != self.epoch:
                return
            self.observation_attempts.append(copy.deepcopy(record))
            self.observation_attempts = self.observation_attempts[-64:]
            self.observation_attempt_count += 1


class ExecutionMemoryLedger(TaskEvidenceLedgerR3):
    """Receipt ledger with bounded, decision-specific views of original work."""

    def __init__(self, command, command_id="", *, index=None, **kwargs):
        index = index if index is not None else SessionEvidenceIndexMemory()
        if not isinstance(index, SessionEvidenceIndexMemory):
            raise TypeError("ExecutionMemoryLedger requires SessionEvidenceIndexMemory")
        self._memory_focus = {"actor_id": None, "decision_kind": "continue", "plan": None}
        self._localization_links = []
        super().__init__(command, command_id, index=index, **kwargs)
        with self.index.lock:
            self._observation_attempts = copy.deepcopy(self.index.observation_attempts)
            self._observation_attempt_count = self.index.observation_attempt_count

    def _consume(self, event):
        super()._consume(event)
        if self.index_epoch != self.index.epoch or event["tool_name"] not in _SENSE:
            return
        payload = event["payload"]
        actor = str(event["args"].get("drone_id", ""))
        if event["tool_name"] == "sense_nearby_entities":
            per_drone = payload.get("per_drone") if isinstance(payload, dict) else None
            if isinstance(per_drone, dict):
                entries = [(str(key), value, ["per_drone", str(key)]) for key, value in per_drone.items()]
            else:
                ids = event["args"].get("drone_ids", [])
                entries = [(str(key), payload, []) for key in ids] if isinstance(ids, list) else []
                if not entries:
                    entries = [("", payload, [])]
        elif event["tool_name"] == "get_nearby_entities":
            entries = [(actor, payload, [])]
        else:
            nearby = payload.get("nearby") if isinstance(payload, dict) else None
            entries = [(actor, nearby, ["nearby"])]
        for observer, nearby, path in entries:
            self._record_observation(event, observer, nearby, path)

    def _record_observation(self, event, actor, nearby, path):
        failed = (isinstance(nearby, dict) and bool(nearby.get("error") or nearby.get("tool_exception")))
        failed = failed or (isinstance(nearby, dict) and
                            (nearby.get("success") is False or nearby.get("status") in
                             {"error", "failed", "failure", "blocked", "cancelled", "timeout"}))
        failed = failed or (isinstance(nearby, str) and nearby.lower().startswith("error"))
        # The composite movement may fail while its subsequent local sensing
        # succeeds. Only the sensing payload determines this observation state.
        targets = nearby.get("targets") if isinstance(nearby, dict) else None
        valid = not failed and isinstance(targets, list) and all(isinstance(t, dict) for t in targets)
        state = "failed" if failed else "targets_observed" if valid and targets else "empty_targets" if valid else "unknown"
        outer = event["payload"]
        if (not valid and isinstance(outer, dict) and (outer.get("error") or outer.get("tool_exception"))):
            state = "failed"
        if not valid and isinstance(outer, str) and outer.lower().startswith("error"):
            state = "failed"
        mismatch = isinstance(outer, dict) and outer.get("drone_id") is not None and str(outer["drone_id"]) != actor
        if mismatch:
            state, valid = "unknown_observer", False
        conflicting = bool(actor and self._actor_conflict(event, actor))
        # A rejected action can have moved the drone. Never backfill a failed
        # composite query with the pre-action position or requested destination.
        position = self._positions.get(actor) if actor and not conflicting and not mismatch else None
        if event["tool_name"] in _ACTIONS and not receipt_points(event):
            position = None
        observed = [{k: target[k] for k in ("id", "name") if k in target}
                    for target in targets] if valid else []
        record = {
            "session_id": self.index.session_id, "epoch": self.index_epoch,
            "command_id": self.command_id, "evidence_id": event["evidence_id"],
            "tool_call_id": event["tool_call_id"], "tool": event["tool_name"],
            "observer_id": actor or None, "receipt_path": path,
            "start_sequence": event["start_sequence"], "end_sequence": event["end_sequence"],
            "state": state, "ordering": "uncertain" if conflicting else "certain",
            "position": copy.deepcopy(position), "targets": observed[:16],
            "targets_omitted": max(0, len(observed) - 16),
            "scope": "local_query_only; coverage_extent_unknown; no_global_absence",
        }
        self._observation_attempts.append(record)
        self._observation_attempts = self._observation_attempts[-64:]
        self._observation_attempt_count += 1
        self.index.remember_observation(record)

    def note_localization_attempt(self, evidence_id, obligation_id):
        """Associate a real query with an explicit caller intention, not a fact.

        No target purpose is guessed from proximity or from all unresolved goals.
        This linkage cannot change target knowledge, route, or visit evidence.
        """
        with self._lock:
            if self.index_epoch != self.index.epoch:
                raise ValueError("Scene changed")
            if obligation_id not in {r["id"] for r in self.requirements}:
                raise ValueError("Unknown original obligation")
            if not any(r["command_id"] == self.command_id and r["evidence_id"] == evidence_id
                       for r in self._observation_attempts):
                raise ValueError("No recorded observation for this evidence")
            link = {"command_id": self.command_id, "evidence_id": evidence_id,
                    "obligation_id": obligation_id, "scope": "caller_intent_not_observation_proof"}
            if link not in self._localization_links:
                self._localization_links.append(link)

    @staticmethod
    def _plan_view(plan):
        if is_dataclass(plan) and not isinstance(plan, type):
            plan = asdict(plan)
        if not isinstance(plan, dict):
            return None
        # No steps/state/done field is accepted as a substitute for receipts.
        keys = ("plan_id", "plan_version", "skill", "status", "reason", "cursor")
        view = {key: copy.deepcopy(plan[key]) for key in keys
                if key in plan and isinstance(plan[key], (str, int, float, bool, type(None)))}
        view = {key: value for key, value in view.items() if not isinstance(value, float) or math.isfinite(value)}
        view["scope"] = "control_metadata_not_task_evidence"
        return view

    @staticmethod
    def _on_hint(position, hint):
        """Point/segment coincidence only, never a sensor coverage radius."""
        points = hint.get("points", [])
        if not points or any(point(p) is None for p in points):
            return False
        tolerance = hint.get("coordinate_rounding_m", 0.000001)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance):
            return False
        # R2 relative receipts explicitly declare 0.02 m rounding. Other
        # confirmed paths use a numerical equality tolerance only.
        tolerance = min(0.02, max(0.000001, tolerance))
        if any(math.dist(position, p) <= tolerance for p in points):
            return True
        if hint.get("kind") not in {"executed_segment", "executed_polyline", "receipt_derived_relative_segment"}:
            return False  # Isolated successful waypoints do not certify edges.
        for left, right in zip(points, points[1:]):
            delta = [b - a for a, b in zip(left, right)]
            length2 = sum(v * v for v in delta)
            if not length2:
                continue
            fraction = sum((p - a) * v for p, a, v in zip(position, left, delta)) / length2
            nearest = [a + min(1.0, max(0.0, fraction)) * v for a, v in zip(left, delta)]
            if math.dist(position, nearest) <= tolerance:
                return True
        return False

    def _clue_checks(self, requirements):
        checks = []
        for requirement in requirements:
            report = requirement.get("report")
            hint = report.get("localization_hint") if report else None
            if not hint or not requirement["target_name"].startswith(("Fixed Target ", "Waypoint ")):
                continue
            for observation in self._observation_attempts:
                location = observation.get("position")
                if (observation["state"] not in {"empty_targets", "targets_observed"}
                        or observation["ordering"] != "certain" or not location
                        or point(location.get("point")) is None
                        or not self._on_hint(location["point"], hint)):
                    continue
                checks.append({"obligation_id": requirement["id"], "target_name": requirement["target_name"],
                    "required_actor": requirement["required_actor"], "observer_id": observation["observer_id"],
                    "clue": {"command_id": report["command_id"], "evidence_id": report["evidence_id"],
                             "receipt_path": report["message_path"]},
                    "check": {"command_id": observation["command_id"], "evidence_id": observation["evidence_id"],
                              "receipt_path": observation["receipt_path"]},
                    "position": copy.deepcopy(location), "state": observation["state"],
                    "temporal_relation": ("after_contact" if observation["start_sequence"] >= report["end_sequence"] else "before_or_during_contact")
                                          if observation["command_id"] == report["command_id"] else "different_command",
                    "scope": "query_at_historical_contact_path; intent_and_coverage_not_inferred"})
        return list(reversed(checks))

    def set_decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        if decision_kind not in _KINDS:
            raise ValueError("Unsupported memory decision kind")
        with self._lock:
            self._memory_focus = {"actor_id": str(actor_id) if actor_id is not None else None,
                                  "decision_kind": decision_kind, "plan": self._plan_view(plan)}

    def _observation_view(self, record):
        view = copy.deepcopy(record)
        if record["command_id"] != self.command_id:
            freshness = "historical_other_command"
        elif record["ordering"] != "certain":
            freshness = "order_unknown"
        elif any(e["tool_name"] in _ACTIONS and str(e["args"].get("drone_id", "")) == record["observer_id"]
                 and e["start_sequence"] > record["start_sequence"] for e in self._events):
            freshness = "stale_after_action"
        else:
            freshness = "at_observation_time_only"
        view["freshness"] = freshness
        view["associated_obligations"] = [l["obligation_id"] for l in self._localization_links
                                            if l["command_id"] == record["command_id"]
                                            and l["evidence_id"] == record["evidence_id"]]
        return view

    def _full_context(self, actor_id, decision_kind, plan):
        valid = self.index_epoch == self.index.epoch
        requirements = self.projection() if valid else []
        routes = self.route_projection() if valid else []
        focused = lambda row: actor_id is None or row.get("actor_id") == actor_id
        obligations = []
        for requirement in requirements:
            if not focused(requirement):
                continue
            keys = ("id", "source_span", "required_actor", "actor_id", "target_name", "target_id",
                    "relation", "knowledge_state", "execution_state", "execution_evidence_id", "position")
            item = {key: copy.deepcopy(requirement[key]) for key in keys}
            report = requirement.get("report")
            if report:
                item["contact_hint"] = {key: copy.deepcopy(report[key]) for key in (
                    "command_id", "evidence_id", "actor_id", "message_path", "localization_hint")}
            obligations.append(item)
        route_views = []
        for route in routes:
            if not focused(route):
                continue
            next_goal = route["next_unconfirmed"]
            route_views.append({
                "required_actor": route["required_actor"], "actor_id": route["actor_id"],
                "source_span": route["source_span"], "ordered_prefix": route["ordered_prefix"],
                "total": len(route["goals"]),
                "remaining_original_goals": [{k: g[k] for k in ("ordinal", "point", "source_span")}
                                             for g in route["goals"][route["ordered_prefix"]:]],
                "next_unconfirmed": {k: next_goal[k] for k in ("ordinal", "point", "source_span")} if next_goal else None,
                "later_observed_ordinals": [g["ordinal"] for g in route["goals"][route["ordered_prefix"] + 1:]
                                            if g["observed_evidence"] is not None],
                "scope": "coordinate_receipts_only; landing_and_other_clauses_not_verified",
            })
        route_views.sort(key=lambda r: r["ordered_prefix"] == r["total"])
        effects = []
        if valid:
            for event in self._events:
                actor = str(event["args"].get("drone_id", ""))
                if actor_id is not None and actor != actor_id:
                    continue
                if self._actor_conflict(event, actor):
                    continue
                for proof in receipt_points(event):
                    effects.append({"actor_id": actor, "command_id": self.command_id,
                                    "evidence_id": event["evidence_id"], "tool_call_id": event["tool_call_id"],
                                    "actual_point": proof["actual_point"], "receipt_path": proof["receipt_path"],
                                    "kind": proof["kind"], "scope": "historical_effect_not_current_position"})
        relevant = [r for r in requirements if focused(r)]
        clue_checks = self._clue_checks(relevant) if valid else []
        relevant_names = {r["target_name"] for r in relevant}
        clue_observations = {(c["check"]["command_id"], c["check"]["evidence_id"]) for c in clue_checks}
        observations = [self._observation_view(r) for r in self._observation_attempts
                        if valid and (actor_id is None or r["observer_id"] == actor_id
                            or any(t.get("name") in relevant_names for t in r["targets"])
                            or (r["command_id"], r["evidence_id"]) in clue_observations)]
        # Keep unsupported clauses visible. These parsers never certify every
        # clause (even a recognized route's landing remains outside R3).
        residual = [{"source_span": [c["start"], c["end"]], "text": c["text"],
                     "status": "not_fully_evaluated"} for c in self._clauses]
        return {"schema": "execution-memory-v1", "decision_kind": decision_kind,
                "command_id": self.command_id, "actor_id": actor_id,
                "scene_valid": valid, "scope": _SCOPE, "mission_completion": "not_evaluated",
                "original_task_controls": True, "original_command": self.command,
                "original_command_chars": len(self.command), "original_command_omitted_chars": 0,
                "plan": self._plan_view(plan), "obligations": obligations, "routes": route_views,
                "actual_effects": list(reversed(effects)), "observations": list(reversed(observations)),
                "clue_checks": clue_checks,
                "aggregate_requirements": self.aggregate_projection() if valid else [],
                "unparsed_or_unverified_clauses": residual,
                "omitted": {"obligations": len(requirements) - len(obligations),
                            "routes": len(routes) - len(route_views), "actual_effects": 0,
                            "observations": max(0, self._observation_attempt_count - len(observations)) if valid else 0,
                            "clue_checks": 0,
                            "aggregate_requirements": 0, "unparsed_or_unverified_clauses": 0}}

    def _bounded(self, full):
        data = copy.deepcopy(full)
        cap = self.max_summary_chars
        # Original instruction remains intact in ledger.command and the user
        # message. Truncated excerpts explicitly retain their source extent.
        for clause in data["unparsed_or_unverified_clauses"]:
            text = clause["text"]
            clause["text"] = text[:80]
            clause["omitted_chars"] = len(text) - len(clause["text"])
        original = data["original_command"]
        data["original_command"] = original[:160]
        data["original_command_omitted_chars"] = len(original) - len(data["original_command"])
        for route in data["routes"]:
            goals = route["remaining_original_goals"]
            route["remaining_goals_omitted"] = max(0, len(goals) - 4)
            route["remaining_original_goals"] = goals[:4]
        for row in data["observations"]:
            for key in ("session_id", "epoch", "tool_call_id", "start_sequence", "end_sequence", "scope", "ordering"):
                row.pop(key, None)
        for row in data["actual_effects"]:
            for key in ("scope", "tool_call_id", "command_id"):
                row.pop(key, None)
        for row in data["clue_checks"]:
            row.pop("scope", None)
        data["clue_check_scope"] = "Query at a historical contact path; intent, covered area and absence are not inferred."
        # Give the original work and its evidence an opportunity before long
        # histories; then allocate round-robin instead of filling one list.
        priorities = (["obligations", "routes", "clue_checks", "observations", "actual_effects"]
                      if data["decision_kind"] == "locate" else
                      ["routes", "obligations", "observations", "clue_checks", "actual_effects"])
        priorities += ["aggregate_requirements", "unparsed_or_unverified_clauses"]
        # Allocate in priority order rather than let a large history crowd out
        # the first current obligation. Each list retains original ordering.
        originals = {key: data[key] for key in priorities}
        for key in priorities:
            data[key] = []
            data["omitted"][key] += len(originals[key])
        if len(encode(data)) > cap:
            data["original_command"] = ""
            data["original_command_omitted_chars"] = len(original)
        for offset in range(max((len(rows) for rows in originals.values()), default=0)):
            for key in priorities:
                if offset >= len(originals[key]):
                    continue
                item = originals[key][offset]
                data[key].append(item)
                data["omitted"][key] -= 1
                if len(encode(data)) > cap:
                    data[key].pop()
                    data["omitted"][key] += 1
        if len(encode(data)) <= cap:
            return data
        minimal = {"schema": "execution-memory-v1", "truncated": True,
                   "mission_completion": "not_evaluated", "original_task_controls": True}
        return minimal if len(encode(minimal)) <= cap else {}

    def decision_context(self, actor_id=None, decision_kind="continue", plan=None):
        if decision_kind not in _KINDS:
            raise ValueError("Unsupported memory decision kind")
        with self._lock:
            return self._bounded(self._full_context(str(actor_id) if actor_id is not None else None,
                                                   decision_kind, plan))

    def handoff(self, plan=None):
        return self.decision_context(decision_kind="handoff", plan=plan)

    def summary(self):
        with self._lock:
            value = encode(self.decision_context(**self._memory_focus))
            return value if len(value) <= self.max_summary_chars else ""

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "execution-memory-v1"
            valid = self.index_epoch == self.index.epoch
            result["execution_memory"] = {
                "decision_focus": copy.deepcopy(self._memory_focus),
                "observation_attempts": copy.deepcopy(self._observation_attempts) if valid else [],
                "observation_attempts_omitted": max(0, self._observation_attempt_count - len(self._observation_attempts)) if valid else 0,
                "localization_links": copy.deepcopy(self._localization_links) if valid else [],
                "full_handoff": self._full_context(None, "handoff", self._memory_focus["plan"]),
            }
            return result
