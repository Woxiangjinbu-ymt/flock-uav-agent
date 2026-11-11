"""Opt-in M1-R2: actionable contact hints and scoped public progress feedback.

R1 remains unchanged. No checker, map snapshot, task score, automatic movement,
model call, retry loop or completion gate is introduced here. Only explicit
session-progress tasks receive the additional public metadata tool.
"""
from __future__ import annotations

import copy
import math
import re
from collections import defaultdict

from task_evidence import SessionEvidenceIndex, TaskEvidenceLedger, encode
from task_evidence_context import TaskEvidenceMiddleware
from task_progress import _ACTIONS, _primary_receipt

MODES = {"evidence_r2", "evidence_r2_hints", "evidence_r2_shadow"}
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_RELATIVE = re.compile(rf"^Drone moved ({_NUMBER})m to position "
                       rf"\(({_NUMBER}), ({_NUMBER}), ({_NUMBER})\), heading: ({_NUMBER})° ")
_PROGRESS = re.compile(
    r"\b(?:(?:session\s+(?:task\s+)?|overall\s+(?:task\s+)?)progress|(?<=until )progress)"
    r"\s+(exceeds?|above|greater than|at least|>=|>)\s*(\d+(?:\.\d+)?)\s*%", re.I)


def aggregate_requirements(command):
    """Only positive, explicit session progress percentages; no area inference."""
    result = []
    for match in _PROGRESS.finditer(command):
        # A bare 'until progress' needs a session-progress scope in the text.
        if match.group().lower().startswith("progress") and not re.search(
                r"\bsession\s+(?:task\s+)?progress\b", command, re.I):
            continue
        prefix = command[max(0, match.start() - 40):match.start()]
        if prefix.strip() and not re.search(r"\b(?:until|ensure(?: that)?|achieve|maintain)\s+(?:the\s+)?$", prefix, re.I):
            continue
        threshold = float(match.group(2))
        if not 0 <= threshold <= 100:
            continue
        result.append({"id": f"progress-{len(result) + 1}", "scope": "session_task_progress",
                       "source_span": [match.start(), match.end()], "threshold_percent": threshold,
                       "operator": ">=" if match.group(1).lower() in {"at least", ">="} else ">"})
    return result


def relative_segment(event):
    """A completed direct relative receipt certifies a rounded flight segment.

    Never use requested points from a failed/partial action, connect through a
    nested fallback, or treat the segment as a target center. The caller checks
    same-actor overlap. Concurrent independent actors are allowed.
    """
    if event["tool_name"] not in {"move_towards", "move_towards_and_sense"} or event["status"] != "completed":
        return None
    receipt = _primary_receipt(event["tool_name"], event["payload"])
    args = event["args"]
    if (not isinstance(receipt, dict) or receipt.get("command") != "move_towards"
            or receipt.get("status") != "success" or receipt.get("error")
            or str(receipt.get("drone_id", "")) != str(args.get("drone_id", ""))
            or not args.get("drone_id") or "heading" not in args
            or any(k in args for k in ("dx", "dy", "azimuth", "elevation"))):
        return None
    message = receipt.get("message")
    match = _RELATIVE.match(message) if isinstance(message, str) else None
    if not match:
        return None
    try:
        distance, x, y, z, observed_heading = map(float, match.groups())
        requested = [args["distance"], args["heading"], args.get("dz", 0)]
        if any(isinstance(v, bool) for v in requested):
            return None
        requested_distance, heading, dz = map(float, requested)
    except (TypeError, ValueError, KeyError):
        return None
    if (not all(math.isfinite(v) for v in (distance, x, y, z, observed_heading, requested_distance, heading, dz))
            or distance <= 0 or dz != 0 or abs(distance - requested_distance) > .011
            or abs((heading - observed_heading + 180) % 360 - 180) > .051):
        return None
    angle = math.radians(heading)
    return {"kind": "receipt_derived_relative_segment",
            "points": [[round(x - distance * math.sin(angle), 6), round(y - distance * math.cos(angle), 6), z], [x, y, z]],
            "start_evidence_id": None, "coordinate_rounding_m": .02,
            "meaning": "contact_during_action; not_target_center; no_radius_guarantee"}


def public_progress(metadata, expected_session):
    """Allowlist existing AGENT-visible metadata; never return entity payloads."""
    if not isinstance(metadata, dict) or not expected_session or str(metadata.get("id")) != str(expected_session):
        return {"error": "Session progress unavailable or session changed; no completion inference."}
    stats = metadata.get("statistics")
    value = stats.get("task_progress") if isinstance(stats, dict) else None
    percent = value.get("progress_percentage") if isinstance(value, dict) else None
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 0 <= percent <= 100:
        return {"error": "Public session progress is missing or invalid; known targets are not the total."}
    details = value.get("details", {})
    counts = {k: details[k] for k in ("total_targets", "visited_targets", "unvisited_targets")
              if isinstance(details, dict) and isinstance(details.get(k), int) and not isinstance(details[k], bool) and details[k] >= 0}
    if ("total_targets" in counts and "visited_targets" in counts and
            (counts["visited_targets"] > counts["total_targets"] or
             "unvisited_targets" in counts and counts["total_targets"] - counts["visited_targets"] != counts["unvisited_targets"])):
        return {"error": "Public progress counters are inconsistent; no completion inference."}
    if (value.get("task_type") == "target_assignment" and counts.get("total_targets", 0) > 0
            and "visited_targets" in counts
            and int((counts["visited_targets"] / counts["total_targets"]) * 100) != percent):
        return {"error": "Public progress percentage and counters disagree; no completion inference."}
    return {"session_id": str(expected_session), "task_type": value.get("task_type"),
            "progress_percentage": percent, "details": counts,
            "source_endpoint": "/sessions/current", "scope": "public_session_aggregate_not_full_task_verdict"}


def progress_tool(client, session_id):
    from langchain_core.tools import tool

    @tool
    def get_mission_progress() -> str:
        """Read current public session task progress and aggregate counts.

        Use for an explicit session-progress threshold, including before claiming
        that threshold is met. Discovered targets are not the global denominator.
        This gives no hidden target positions and does not verify other clauses.
        """
        try:
            return encode(public_progress(client.get_current_session(), session_id))
        except Exception as exc:
            return encode({"error": f"Public session progress unavailable ({type(exc).__name__}); do not infer completion."})
    return get_mission_progress


class SessionEvidenceIndexR2(SessionEvidenceIndex):
    def __init__(self, session_id=None):
        super().__init__(session_id)
        self.relative_contacts = defaultdict(list)

    def clear(self):
        with self.lock:
            super().clear()
            self.relative_contacts.clear()


class TaskEvidenceLedgerR2(TaskEvidenceLedger):
    def __init__(self, command, command_id="", *, index=None, aggregate_feedback=True, **kwargs):
        index = index if index is not None else SessionEvidenceIndexR2()
        self.aggregate = aggregate_requirements(str(command)) if aggregate_feedback else []
        self.progress_observations = []
        self._progress_query_id = None
        with index.lock:
            # A reset cannot interleave the inherited R1 facts and R2 hints.
            super().__init__(command, command_id, index=index, **kwargs)
            self._relative_contacts = copy.deepcopy(index.relative_contacts)

    def begin_tool(self, tool_name, args, tool_call_id=""):
        with self._lock:
            evidence_id = super().begin_tool(tool_name, args, tool_call_id)
            if tool_name == "get_mission_progress" and self.aggregate:
                self._progress_query_id = evidence_id
            return evidence_id

    def _consume(self, event):
        super()._consume(event)
        if self.index_epoch != self.index.epoch:
            return
        if event["tool_name"] == "get_mission_progress" and self.aggregate:
            value = event["payload"]
            if (event["status"] == "observed" and isinstance(value, dict)
                    and value.get("session_id") == self.index.session_id
                    and value.get("source_endpoint") == "/sessions/current"):
                # Validate again: malformed fixture/tool replies cannot certify a threshold.
                validated = public_progress({"id": value["session_id"], "statistics": {"task_progress": value}}, self.index.session_id)
                if "error" not in validated:
                    self.progress_observations.append({**validated, "evidence_id": event["evidence_id"],
                                                       "tool_call_id": event["tool_call_id"], "command_id": self.command_id,
                                                       "start_sequence": event["start_sequence"], "end_sequence": event["end_sequence"]})
            return
        if self._actor_conflict(event, str(event["args"].get("drone_id", ""))):
            return
        hint = relative_segment(event)
        if not hint:
            return
        receipt_path = ["message"]
        if event["tool_name"].endswith("_and_sense"):
            key = "navigation_result" if "navigation_result" in event["payload"] else "move_result" if "move_result" in event["payload"] else None
            if key:
                receipt_path.insert(0, key)
        for name, reports in self._reports.items():
            if not name.startswith(("Fixed Target ", "Waypoint ")):
                continue
            for report in reports:
                if (report["command_id"] == self.command_id and report["evidence_id"] == event["evidence_id"]
                        and report["message_path"] == receipt_path):
                    # Direct relative receipts only; source paths remain R1's exact paths.
                    candidate = copy.deepcopy(report)
                    candidate["localization_hint"] = hint
                    self._relative_contacts[name].append(candidate)
                    with self.index.lock:
                        if self.index_epoch == self.index.epoch:
                            self.index.relative_contacts[name].append(copy.deepcopy(candidate))
                            self.index.relative_contacts[name] = self.index.relative_contacts[name][-8:]

    def _view(self, requirement):
        result = super()._view(requirement)
        if (self.index_epoch != self.index.epoch or result["relation"] != "visit"
                or not result["actor_id"] or result["knowledge_state"] != "reported_only"
                or result["execution_state"] == "evidenced"
                or not result["target_name"].startswith(("Fixed Target ", "Waypoint "))):
            return result
        report = result["report"]
        if not report or not report["localization_hint"]:
            candidates = self._relative_contacts.get(result["target_name"], [])
            if candidates:
                # Prefer the shortest witnessed segment, latest on ties. No map
                # or expected target position is consulted in this ranking.
                report = min(reversed(candidates), key=lambda r: math.dist(*r["localization_hint"]["points"]))
                result["report"] = copy.deepcopy(report)
        if report and report["localization_hint"]:
            result["next_step"] = (
                "Prioritize local sensing along this historical contact path to resolve the exact target ID/position, "
                "then let the required actor visit. Use legal obstacle-aware tools; the path may not be reachable now. "
                "Keep original order, photos and later movements. Do not claim absence from failed searches or transfer another actor's visit."
            )
        return result

    def aggregate_projection(self):
        with self._lock:
            last = self.progress_observations[-1] if self.progress_observations else None
            stale = bool(last and any(e["tool_name"] in _ACTIONS and
                         (e["end_sequence"] is None or e["end_sequence"] > last["start_sequence"])
                         for e in self._events))
            valid_epoch = self.index_epoch == self.index.epoch
            if last and last["evidence_id"] != self._progress_query_id:
                last = None  # Pending/failed refresh never silently reuses an old reading.
            observation = copy.deepcopy(last) if valid_epoch else None
            freshness = "unknown" if not observation else "stale_after_action_or_overlap" if stale else "observed"
            result = []
            for requirement in self.aggregate:
                state = "unknown"
                if freshness == "observed":
                    value, threshold = observation["progress_percentage"], requirement["threshold_percent"]
                    met = value >= threshold if requirement["operator"] == ">=" else value > threshold
                    state = "observed_threshold_met" if met else "observed_threshold_not_met"
                result.append({**requirement, "observation": observation, "freshness": freshness, "threshold_state": state,
                               "next_step": "Read get_mission_progress after coverage actions and before claiming the threshold. If below it, continue feasible search/coverage within the existing budget; known targets are not the total. Other original clauses still apply."})
            return result

    def summary(self):
        if not self.aggregate:
            return super().summary()  # R1 bytes when no eligible contact hint changes.
        data = {"schema": "m1-r2", "items": [], "omitted": len(self.requirements),
                "aggregate_requirements": [], "aggregate_omitted": len(self.aggregate),
                "mission_completion": "not_evaluated", "original_task_controls": True}
        for item in self.aggregate_projection():
            data["aggregate_requirements"].append(item); data["aggregate_omitted"] -= 1
            if len(encode(data)) > self.max_summary_chars:
                data["aggregate_requirements"].pop(); data["aggregate_omitted"] += 1
        for item in self.projection():
            if len(data["items"]) >= 4:
                break
            data["items"].append(item); data["omitted"] -= 1
            if len(encode(data)) > self.max_summary_chars:
                data["items"].pop(); data["omitted"] += 1
        return encode(data) if len(encode(data)) <= self.max_summary_chars else ""

    def snapshot(self):
        with self._lock:
            result = super().snapshot()
            result["revision"] = "m1-r2"
            result["task_evidence"]["aggregate_requirements"] = self.aggregate_projection()
            result["task_evidence"]["progress_observations"] = copy.deepcopy(self.progress_observations)
            return result


class TaskEvidenceR2Middleware(TaskEvidenceMiddleware):
    def __init__(self, ledger, mode="evidence_r2"):
        if mode not in MODES:
            raise ValueError(f"Unsupported R2 mode: {mode}")
        super().__init__(ledger, "evidence_shadow" if mode == "evidence_r2_shadow" else "evidence")
        self.variant = mode

    def _model_request(self, request):
        result = super()._model_request(request)
        if self.ledger.model_requests:
            self.ledger.model_requests[-1]["variant"] = self.variant
        return result
