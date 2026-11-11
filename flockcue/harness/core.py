"""Small in-process, interruptible skill runtime. No model or world-truth access."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
import hashlib
import json
import threading
import time


@dataclass(frozen=True)
class PublicCall:
    tool: str
    args: dict = field(default_factory=dict)
    action: bool = False


@dataclass(frozen=True)
class Checkpoint:
    reason: str = "unit_complete"
    failed: bool = False


class SkillExit(Exception):
    def __init__(self, reason, status="needs_replan"):
        super().__init__(reason)
        self.reason, self.status = reason, status


class HarnessInterrupted(RuntimeError):
    pass


class HarnessLog:
    def __init__(self, task_id, clock=time.monotonic):
        self.task_id, self.clock = task_id, clock
        self.events = []
        self._lock = threading.RLock()

    def emit(self, kind, **fields):
        with self._lock:
            row = {"sequence": len(self.events) + 1, "task_id": self.task_id,
                   "kind": kind, "monotonic_s": self.clock(), **copy.deepcopy(fields)}
            self.events.append(row)
            return row["sequence"]

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.events)


@dataclass
class DecisionFrame:
    plan_id: str
    task_id: str
    scene_id: str
    scene_epoch: int
    skill: str
    source_obligations: list
    steps: list
    continue_guards: list
    invalidators: list
    local_repair_scope: str
    stop_condition: str
    deadline: float
    api_budget: int = 2000
    repair_budget: int = 16
    plan_version: int = 1
    origin: str = "explicit_model_tool_commit"
    actor_bindings: dict = field(default_factory=dict)
    evidence_refs: list = field(default_factory=list)
    cursor: int = 0
    state: dict = field(default_factory=dict)
    status: str = "ready"
    reason: str = "committed"
    api_calls: int = 0
    repairs: int = 0
    units: int = 0


class HarnessRuntime:
    """All scheduling arms share the same generators, receipts and checkpoints.

    A checkpoint is one verified simple action or one search attempt plus its
    state/sensing/progress reads, not one low-level API call. Cancellation and
    deadline checks also occur between the individual API calls in that unit.
    """
    POLICIES = {"applicability", "every_step", "fixed", "failure"}
    REVISIONS = {"search_confirm_v9"}

    def __init__(self, *, task_id, command, scene_id, scene_epoch, ledger, invoke,
                 skill_factory, scope, started, scheduler="applicability", fixed_k=4,
                 cancel_check=lambda: None, clock=time.monotonic, handoff_provider=None,
                 revision="search_confirm_v9", static_obstacle_contract=False):
        if scheduler not in self.POLICIES or isinstance(fixed_k, bool) or not isinstance(fixed_k, int) or fixed_k < 1:
            raise ValueError("Invalid harness scheduling configuration")
        if revision not in self.REVISIONS:
            raise ValueError("Invalid harness revision")
        if not isinstance(static_obstacle_contract, bool):
            raise ValueError("static_obstacle_contract must be boolean")
        self.revision = revision
        self.static_obstacle_contract = static_obstacle_contract
        self.task_id, self.command = task_id, command
        self.scene_id, self.scene_epoch = scene_id, scene_epoch
        self.ledger, self.invoke, self.skill_factory, self.scope = ledger, invoke, skill_factory, scope
        self.started, self.deadline = started, started + 500.0
        self.scheduler, self.fixed_k = scheduler, fixed_k
        self.cancel_check, self.clock = cancel_check, clock
        self.handoff_provider = handoff_provider
        self.log = HarnessLog(task_id, clock)
        self.frames, self.generators = {}, {}
        self._lock = threading.RLock()
        self._closed = False
        self._interrupted = None
        self._active_external = {}
        self._external_version = 0
        self._logical_request = None
        self._model_count = 0
        self._wire = {}
        self.log.emit("task_start", scene_id=scene_id, scene_epoch=scene_epoch,
                      deadline=self.deadline, scheduler=scheduler, fixed_k=fixed_k,
                      scope="in_process_resume_only")
        self.log.emit("harness_revision", revision=self.revision)
        self.log.emit("shared_endpoint_contract", static_obstacle_contract=static_obstacle_contract,
                      scope="same_plan_scene_epoch_exact_xyz_no_obstacle_writers")

    def check_interrupt(self):
        if self._interrupted:
            raise HarnessInterrupted(self._interrupted)
        try:
            self.cancel_check()
        except BaseException:
            self._interrupted = "cancelled"
            self.log.emit("task_interrupt", reason="cancelled")
            raise
        if self.clock() >= self.deadline:
            self._interrupted = "deadline"
            self.log.emit("task_interrupt", reason="deadline")
            raise HarnessInterrupted("harness task deadline reached")
        if self._closed:
            raise HarnessInterrupted("harness task already closed")

    def external_begin(self, call_id, name, args, action):
        with self._lock:
            self._active_external[call_id] = (name, args, action)
            if action:
                self._external_version += 1
                actors = {str(args["drone_id"])} if args.get("drone_id") else set(map(str, args.get("drone_ids", [])))
                for frame in self.frames.values():
                    if frame.status not in {"ready", "paused"}:
                        continue
                    bound = set(frame.actor_bindings.values())
                    if frame.skill == "public_search" or not actors or not bound or actors & bound:
                        frame.status, frame.reason = "needs_replan", "external_action_changed_dependencies"
                        self.log.emit("plan_invalidated", plan_id=frame.plan_id, reason=frame.reason,
                                      actors=sorted(actors), tool_call_id=call_id)
            self.log.emit("external_tool_start", tool_call_id=call_id, tool=name, args=args, action=action)

    def external_end(self, call_id):
        with self._lock:
            self._active_external.pop(call_id, None)
            self.log.emit("external_tool_end", tool_call_id=call_id)

    def _gate(self, frame, decision, reason, **extra):
        self.log.emit("gate", plan_id=frame.plan_id, plan_version=frame.plan_version,
                      decision=decision, reason=reason, cursor=frame.cursor,
                      actors=copy.deepcopy(frame.actor_bindings), evidence_refs=frame.evidence_refs[-8:],
                      evidence_version=getattr(self.ledger, "_sequence", None),
                      external_version=self._external_version,
                      remaining_s=max(0, self.deadline - self.clock()), **extra)

    def _response(self, frame):
        result = {"plan_id": frame.plan_id, "skill": frame.skill, "status": frame.status,
                "reason": frame.reason, "cursor": frame.cursor, "state": copy.deepcopy(frame.state),
                "actor_bindings": copy.deepcopy(frame.actor_bindings), "api_calls": frame.api_calls,
                "evidence_refs": frame.evidence_refs[-8:],
                "remaining_s": max(0, self.deadline - self.clock()),
                "mission_completion": "not_evaluated",
                "next_control": "resume with this plan_id" if frame.status == "paused" else
                                "use original tools to resolve remaining task obligations"}
        resumable = frame.status in {"ready", "paused"}
        result.update(resumable=resumable,
                      next_control=("resume with this plan_id, or cancel to abandon the plan" if resumable else
                                    "do not resume or restart this skill; use original tools to resolve remaining task obligations"))
        if self.handoff_provider is not None:
            try:
                result["memory_handoff"] = self.handoff_provider(asdict(frame))
            except Exception as exc:
                # Memory failure must not replay or obscure an applied action.
                result["memory_handoff_error"] = type(exc).__name__
        return result

    def _reject_plan_control(self, frame, action, reason):
        """Keep the underlying plan evidence intact when rejecting a control."""
        result = self._response(frame)
        result.update(status="rejected", reason=reason, control_action=action,
                      plan_status=frame.status, plan_reason=frame.reason)
        self.log.emit("control_rejected", plan_id=frame.plan_id, action=action,
                      reason=reason, plan_status=frame.status, cursor=frame.cursor)
        return result

    def control(self, action, *, skill=None, plan_id=None):
        # Reject overlapping skill controls rather than allowing duplicate side effects.
        if not self._lock.acquire(blocking=False):
            return {"status": "busy", "reason": "another_skill_control_is_running"}
        try:
            self.check_interrupt()
            if self._active_external:
                return {"status": "deferred", "reason": "external_tools_in_flight"}
            if self.scope() != (self.scene_id, self.scene_epoch):
                self.log.emit("control_rejected", plan_id=plan_id, reason="scene_scope_changed")
                return {"status": "rejected", "reason": "scene_scope_changed"}
            if action == "start":
                # At most one committed instance per supported skill per command:
                # do not silently restart a partially executed action sequence.
                existing = next((f for f in self.frames.values() if f.skill == skill), None)
                if existing is not None:
                    reason = ("skill_already_committed_resume_existing_plan"
                              if existing.status in {"ready", "paused"} else
                              "skill_already_committed_not_resumable")
                    return self._reject_plan_control(existing, action, reason)
                    return {"status": "rejected", "reason": "skill_already_committed_use_plan_id"}
                ident = f"{self.task_id}:p{len(self.frames) + 1}"
                try:
                    frame, generator = self.skill_factory(skill, ident, self)
                except (ValueError, SkillExit) as exc:
                    self.log.emit("plan_rejected", skill=skill, reason=str(exc))
                    return {"status": "unsupported", "reason": str(exc)}
                self.frames[ident], self.generators[ident] = frame, generator
                self.log.emit("plan_commit", frame=asdict(frame))
            else:
                frame = self.frames.get(plan_id)
                if frame is None:
                    return {"status": "rejected", "reason": "unknown_or_other_task_plan_id"}
                if action == "cancel":
                    frame.status, frame.reason = "cancelled", "explicit_model_cancel"
                    self.generators[frame.plan_id].close()
                    self.log.emit("skill_exit", plan_id=frame.plan_id, status=frame.status, reason=frame.reason)
                    return self._response(frame)
                if action != "resume":
                    return {"status": "rejected", "reason": "unknown_control_action"}
                if frame.status not in {"ready", "paused"}:
                    return self._reject_plan_control(frame, action, "plan_not_resumable")
                    return self._response(frame)
            return self._drive(frame)
        finally:
            self._lock.release()

    def _drive(self, frame):
        frame.status, frame.reason = "running", "skill_enter"
        self.log.emit("skill_enter", plan_id=frame.plan_id, cursor=frame.cursor)
        generator = self.generators[frame.plan_id]
        sent, units_this_slice = None, 0
        try:
            while True:
                self.check_interrupt()
                if self.scope() != (frame.scene_id, frame.scene_epoch):
                    raise SkillExit("scene_scope_changed")
                item = generator.send(sent)
                sent = None
                if isinstance(item, Checkpoint):
                    frame.units += 1
                    units_this_slice += 1
                    self.log.emit("plan_update", frame=asdict(frame))
                    pause = (self.scheduler == "every_step" or
                             self.scheduler == "fixed" and units_this_slice >= self.fixed_k or
                             self.scheduler == "failure" and item.failed)
                    if pause:
                        frame.status, frame.reason = "paused", f"{self.scheduler}_checkpoint"
                        self._gate(frame, "REPLAN", frame.reason, applicability="valid_or_locally_repairable",
                                   scheduling_only=True, checkpoint_reason=item.reason)
                        self.log.emit("skill_exit", plan_id=frame.plan_id, status=frame.status, reason=frame.reason)
                        return self._response(frame)
                    self._gate(frame, "CONTINUE", item.reason, applicability="valid_or_locally_repairable")
                    continue
                if not isinstance(item, PublicCall):
                    raise SkillExit("invalid_skill_instruction")
                if frame.api_calls >= frame.api_budget:
                    raise SkillExit("api_budget_exhausted")
                frame.state["pending_call"] = asdict(item)
                self._gate(frame, "CONTINUE" if item.action else "OBSERVE",
                           "committed_action" if item.action else "resolve_public_dependency", next_call=asdict(item))
                call_id = f"harness:{frame.plan_id}:{frame.api_calls + 1}"
                evidence_id = self.ledger.begin_tool(item.tool, item.args, call_id)
                self.log.emit("public_call_start", plan_id=frame.plan_id, call_id=call_id,
                              evidence_id=evidence_id, call=asdict(item))
                frame.api_calls += 1
                try:
                    sent = self.invoke(item)
                except BaseException as exc:
                    self.ledger.end_tool(evidence_id, error=exc)
                    frame.evidence_refs.append(evidence_id)
                    self.log.emit("public_call_end", plan_id=frame.plan_id, call_id=call_id,
                                  evidence_id=evidence_id, effect="unknown", error_type=type(exc).__name__)
                    # Never replay a possibly applied action. A side-effect-free
                    # status read can inform the model, but not certify history.
                    sent = {"harness_transport_error": type(exc).__name__}
                    if not isinstance(exc, Exception):
                        raise
                else:
                    self.ledger.end_tool(evidence_id, sent)
                    frame.evidence_refs.append(evidence_id)
                    self.log.emit("public_call_end", plan_id=frame.plan_id, call_id=call_id,
                                  evidence_id=evidence_id, receipt_sha256=hashlib.sha256(
                                      json.dumps(sent, sort_keys=True, default=str).encode()).hexdigest())
                self.check_interrupt()
                frame.state.pop("pending_call", None)
        except StopIteration:
            frame.status, frame.reason = "completed", "skill_public_stop_condition"
            self._gate(frame, "STOP", frame.reason)
        except SkillExit as exc:
            frame.status, frame.reason = exc.status, exc.reason
            self._gate(frame, "REPLAN", exc.reason)
        except BaseException as exc:
            frame.status = "interrupted" if self._interrupted else "needs_replan"
            frame.reason = self._interrupted or f"skill_exception:{type(exc).__name__}"
            self.log.emit("skill_exit", plan_id=frame.plan_id, status=frame.status, reason=frame.reason)
            if self.handoff_provider is not None:
                self.log.emit("interruption_handoff", result=self._response(frame))
            generator.close()
            if self._interrupted or not isinstance(exc, Exception):
                raise
            return self._response(frame)
        generator.close()
        self.log.emit("plan_update", frame=asdict(frame))
        self.log.emit("skill_exit", plan_id=frame.plan_id, status=frame.status, reason=frame.reason)
        return self._response(frame)

    def model_start(self, middleware_index):
        self.check_interrupt()
        self._model_count += 1
        ident = f"{self.task_id}:m{self._model_count}"
        self._logical_request = ident
        self.log.emit("model_request_start", model_request_id=ident, middleware_index=middleware_index,
                      plans=[self._response(f) for f in self.frames.values()])
        return ident

    def model_end(self, ident, *, error=None, usage=None, tool_call_ids=None):
        self.log.emit("model_request_end", model_request_id=ident, error_type=error, usage=usage,
                      tool_call_ids=tool_call_ids or [],
                      measurement="handler_wall_time_not_internal_reasoning")
        for attempt, value in self._wire.items():
            if value["model_request_id"] == ident and not value["headers_seen"]:
                self.log.emit("http_attempt_unresolved", attempt_id=attempt, model_request_id=ident,
                              reason="no_response_headers_observed")
        self._logical_request = None

    def wire_start(self, request, record):
        attempt = f"{self.task_id}:http{len(self._wire) + 1}"
        request.extensions["uav_harness_attempt"] = attempt
        self._wire[attempt] = {"model_request_id": self._logical_request, "headers_seen": False}
        record.update(attempt_id=attempt, model_request_id=self._logical_request, started_monotonic=self.clock())
        self.log.emit("http_attempt_start", attempt_id=attempt, model_request_id=self._logical_request,
                      body_sha256=record["body_sha256"])

    def wire_response(self, response):
        attempt = response.request.extensions.get("uav_harness_attempt")
        if attempt in self._wire:
            self._wire[attempt]["headers_seen"] = True
            self.log.emit("http_response_headers", attempt_id=attempt,
                          model_request_id=self._wire[attempt]["model_request_id"], status_code=response.status_code,
                          measurement="headers_arrived_not_full_body_completion")

    def close(self, reason):
        if self._closed:
            return
        for frame in self.frames.values():
            if frame.status in {"ready", "paused", "running"}:
                frame.status, frame.reason = "abandoned", "task_ended_with_unfinished_plan"
            self.generators[frame.plan_id].close()
        self._closed = True
        self.log.emit("task_end", reason=reason, plans=[asdict(f) for f in self.frames.values()])

    def snapshot(self):
        result = {"schema": "minimal-harness-v1", "scheduler": self.scheduler, "fixed_k": self.fixed_k,
                "task_id": self.task_id, "scene_id": self.scene_id, "scene_epoch": self.scene_epoch,
                "frames": [copy.deepcopy(asdict(f)) for f in self.frames.values()], "events": self.log.snapshot()}
        if self.handoff_provider is not None:
            result["handoffs"] = [self._response(frame) for frame in self.frames.values()]
        result["revision"] = self.revision
        return result
