"""Decision-boundary reads of execution memory; no extra model or tool calls.

The existing immutable message decoration preserves earlier provider prefixes.
Selecting a view never changes the original command or certifies task completion.
"""
from __future__ import annotations

from dataclasses import asdict

from langchain_core.messages import ToolMessage

from task_evidence_r2 import TaskEvidenceR2Middleware


def prepare_memory_context(ledger, request, runtime=None):
    """Focus on the last completed batch, without guessing a target intention.

    Parallel actors retain an unfocused view. A current harness frame supplies
    continuation metadata, never proof that its planned actions actually ran.
    """
    replies = []
    for message in reversed(request.messages):
        if not isinstance(message, ToolMessage):
            break
        replies.append(message.tool_call_id)
    with ledger._lock:
        events = [event for event in ledger._events
                  if event["tool_call_id"] in replies and event["end_sequence"] is not None]
        actors = {str(event["args"]["drone_id"]) for event in events if event["args"].get("drone_id")}
        actor = next(iter(actors)) if len(actors) == 1 else None
        kind = "continue"
        if any(event["status"] in {"partial", "blocked", "error", "unknown"} for event in events):
            kind = "handoff"
        elif any(event["tool_name"] == "get_nearby_entities" or
                 event["tool_name"].endswith("_and_sense") for event in events):
            kind = "locate"
    plan = None
    if runtime is not None and runtime.frames:
        frame = next(reversed(runtime.frames.values()))
        plan = asdict(frame)
        if frame.status in {"paused", "needs_replan", "interrupted", "cancelled", "abandoned"}:
            kind = "handoff"
        # A raw action can concern another actor after a skill was invalidated.
        if not actors and len(frame.actor_bindings) == 1:
            actor = str(next(iter(frame.actor_bindings.values())))
    ledger.set_decision_context(actor_id=actor, decision_kind=kind, plan=plan)


class ExecutionMemoryMiddleware(TaskEvidenceR2Middleware):
    def __init__(self, ledger, mode="evidence_memory"):
        if mode != "evidence_memory":
            raise ValueError(f"Unsupported execution memory mode: {mode}")
        super().__init__(ledger, "evidence_r2")
        self.variant = mode

    def _prepare(self, request):
        prepare_memory_context(self.ledger, request)
        return super()._prepare(request)
