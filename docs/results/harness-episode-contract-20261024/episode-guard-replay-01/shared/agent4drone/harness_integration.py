"""Opt-in R2 integration. Raw tools remain available; no synthetic tool receipts."""
from __future__ import annotations

import json
import os

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from harness_core import HarnessRuntime
from harness_skills import make_skill_factory
from task_evidence_r2 import TaskEvidenceR2Middleware, public_progress
from task_progress import _ACTIONS, _arguments


HARNESS_RULES = """Minimal committed-plan harness:
run_harness supports only the complete original task grammars described by that tool.
You explicitly commit a source-linked plan with action=start. The runtime executes
public calls, checks effects, and may return paused at a shared skill checkpoint.
Resume only the returned plan_id; cancel abandons it. Never restart a partially
executed skill. Call run_harness alone in its tool batch. Existing raw tools remain
available for unsupported tasks and repair; related raw actions invalidate a paused
plan. A skill's completed status is only its public stop condition, not a whole-task
verdict. Read the actual returned state and remaining obligations before reporting.
"""

ENTRY_CONTRACT_RULES = """
Entry contract v2 is enabled. With an empty obstacle blackboard, an explicitly
started public_search first makes one public nearby read per fleet drone, without
moving it; valid empty observations permit the bounded search to continue.
Resume only a plan whose response says resumable=true (ready or paused).
needs_replan, completed, cancelled and other terminal plans cannot resume or
restart. A rejected control preserves plan_status, plan_reason, cursor and state;
use the original tools for remaining work, without repeating applied actions.
"""


def _usage(response):
    return [message.usage_metadata for message in getattr(response, "result", [])
            if getattr(message, "usage_metadata", None)]


def _tool_ids(response):
    return [call["id"] for message in getattr(response, "result", []) for call in getattr(message, "tool_calls", [])]


class HarnessMiddleware(TaskEvidenceR2Middleware):
    def __init__(self, ledger, runtime):
        super().__init__(ledger, "evidence_r2")
        self.variant, self.harness = "evidence_harness", runtime

    def _control_batch_error(self, request):
        if request.tool_call.get("name") != "run_harness":
            return None
        messages = request.state.get("messages", [])
        batch = next((m.tool_calls for m in reversed(messages) if getattr(m, "tool_calls", None)), [])
        if len(batch) != 1:
            self.harness.log.emit("control_rejected", reason="harness_must_be_only_call_in_batch",
                                  tool_call_id=request.tool_call["id"])
            return ToolMessage(content=json.dumps({"status": "rejected", "reason": "run_harness_must_be_called_alone"}),
                               tool_call_id=request.tool_call["id"])
        return None

    def wrap_tool_call(self, request, handler):
        self.harness.check_interrupt()
        rejected = self._control_batch_error(request)
        if rejected:
            return rejected
        call = request.tool_call
        if call["name"] == "run_harness":
            self.harness.log.emit("model_control_tool", tool_call_id=call["id"], args=_arguments(call.get("args", {})))
            return handler(request)
        self.harness.external_begin(call["id"], call["name"], _arguments(call.get("args", {})), call["name"] in _ACTIONS)
        try:
            return super().wrap_tool_call(request, handler)
        finally:
            self.harness.external_end(call["id"])

    async def awrap_tool_call(self, request, handler):
        self.harness.check_interrupt()
        rejected = self._control_batch_error(request)
        if rejected:
            return rejected
        call = request.tool_call
        if call["name"] == "run_harness":
            self.harness.log.emit("model_control_tool", tool_call_id=call["id"], args=_arguments(call.get("args", {})))
            return await handler(request)
        self.harness.external_begin(call["id"], call["name"], _arguments(call.get("args", {})), call["name"] in _ACTIONS)
        try:
            return await super().awrap_tool_call(request, handler)
        finally:
            self.harness.external_end(call["id"])

    def wrap_model_call(self, request, handler):
        prepared = self._model_request(request)
        ident = self.harness.model_start(len(self.ledger.model_requests) - 1)
        try:
            response = handler(prepared)
        except BaseException as exc:
            self.harness.model_end(ident, error=type(exc).__name__)
            raise
        self.harness.model_end(ident, usage=_usage(response), tool_call_ids=_tool_ids(response))
        return response

    async def awrap_model_call(self, request, handler):
        prepared = self._model_request(request)
        ident = self.harness.model_start(len(self.ledger.model_requests) - 1)
        try:
            response = await handler(prepared)
        except BaseException as exc:
            self.harness.model_end(ident, error=type(exc).__name__)
            raise
        self.harness.model_end(ident, usage=_usage(response), tool_call_ids=_tool_ids(response))
        return response


class MemoryHarnessMiddleware(HarnessMiddleware):
    def __init__(self, ledger, runtime, variant="evidence_memory_harness"):
        super().__init__(ledger, runtime)
        self.variant = variant

    def _prepare(self, request):
        from execution_memory_context import prepare_memory_context
        prepare_memory_context(self.ledger, request, self.harness)
        return super()._prepare(request)


def install_harness(agent, ledger, *, execution_memory=False, memory_variant="evidence_memory_harness"):
    board = agent.blackboard
    allowed = {"list_drones", "get_drone_status", "take_off", "change_altitude", "rotate",
               "hover", "move_to", "get_nearby_entities"}

    def invoke(call):
        if call.tool == "get_mission_progress":
            return public_progress(agent.client.get_current_session(), board.session_id)
        if call.tool not in allowed:
            raise ValueError("Skill requested a tool outside the public allowlist")
        return getattr(agent.client, call.tool)(**call.args)

    runtime = HarnessRuntime(task_id=ledger.command_id, command=ledger.command,
                             scene_id=board.session_id, scene_epoch=ledger.index.epoch,
                             ledger=ledger, invoke=invoke, skill_factory=make_skill_factory(board),
                             scope=lambda: (board.session_id, ledger.index.epoch),
                             started=agent._harness_task_started,
                             scheduler=os.environ.get("UAV_HARNESS_SCHEDULER", "applicability"),
                             fixed_k=int(os.environ.get("UAV_HARNESS_FIXED_K", "4")),
                             cancel_check=agent._harness_cancel_check,
                             handoff_provider=ledger.handoff if execution_memory else None,
                             revision=os.environ.get("UAV_HARNESS_REVISION", "legacy"),
                             static_obstacle_contract=os.environ.get("UAV_HARNESS_STATIC_OBSTACLES") == "1")

    @tool
    def run_harness(input_json: str) -> str:
        """Commit/resume/cancel a bounded public-tool skill, alone in a tool batch.

        JSON start: {"action":"start","skill":"simple_chain"} or public_search.
        simple_chain supports only a single explicitly named drone taking off,
        then rotating or climbing and hovering, or taking off and hovering in place.
        public_search supports only the complete task to take off necessary drones
        and assign them to all targets until session task progress exceeds 95%.
        Extra obligations or unsupported wording fall back to the original tools.
        A paused response requires {"action":"resume","plan_id":"returned ID"}.
        Cancel with {"action":"cancel","plan_id":"returned ID"}; cancellation
        abandons the plan, never reverses completed actions. Actual receipts and
        public state are returned; completion of this skill is not a task verdict.
        """
        try:
            value = json.loads(input_json)
            if not isinstance(value, dict) or set(value) - {"action", "skill", "plan_id"}:
                raise ValueError("Unexpected control fields")
            action = value.get("action")
            if action not in {"start", "resume", "cancel"}:
                raise ValueError("Expected start, resume or cancel")
            if action == "start" and (set(value) != {"action", "skill"} or not isinstance(value["skill"], str)):
                raise ValueError("Start requires only a skill name")
            if action != "start" and (set(value) != {"action", "plan_id"} or not isinstance(value["plan_id"], str)):
                raise ValueError("Resume/cancel require only a plan_id")
        except (ValueError, TypeError) as exc:
            return json.dumps({"status": "rejected", "reason": str(exc)})
        return json.dumps(runtime.control(**value), ensure_ascii=False)

    if runtime.revision in {"entry_contract_v2", "flight_state_v3", "blocked_candidate_v4", "shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7"}:
        # Keep the legacy system prompt and tool description byte-for-byte
        # unchanged; expose the opt-in contract on this tool instance only.
        run_harness.description += "\n" + ENTRY_CONTRACT_RULES
    if runtime.revision in {"shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7"}:
        run_harness.description += ("\nShared endpoint v5 requires the operator's static-obstacle "
                                    "contract. Only exact XYZ endpoint conflicts are shared within "
                                    "this plan; observed geometry changes stop the plan.")
    if runtime.revision in {"terminal_progress_v6", "observed_target_v7"}:
        run_harness.description += ("\nTerminal progress v6 checks public session progress once before "
                                    "exiting for no available candidate, within the existing budget. "
                                    "A failed or unavailable read does not confirm completion.")
    if runtime.revision == "observed_target_v7":
        run_harness.description += ("\nObserved target v7 may use the discovering actor's normal move "
                                    "slot to try a freshly observed fixed target coordinate once per ID. "
                                    "Name-only reach receipts suppress optional follow-ups, not certify "
                                    "target IDs. No extra API, round or repair budget is granted.")
    agent._harness = runtime
    agent.client._harness_deadline = runtime.deadline
    agent.tools = [*agent.tools, run_harness]
    if execution_memory and memory_variant == "evidence_memory_v3_harness":
        from execution_memory_v3_context import ExecutionMemoryV3HarnessMiddleware
        return ExecutionMemoryV3HarnessMiddleware(ledger, runtime, memory_variant)
    if execution_memory and memory_variant == "evidence_memory_v4_harness":
        from execution_memory_v4_context import ExecutionMemoryV4HarnessMiddleware
        return ExecutionMemoryV4HarnessMiddleware(ledger, runtime, memory_variant)
    if execution_memory and memory_variant == "evidence_memory_v5_harness":
        from execution_memory_v5_context import ExecutionMemoryV5HarnessMiddleware
        return ExecutionMemoryV5HarnessMiddleware(ledger, runtime, memory_variant)
    if execution_memory and memory_variant == "evidence_memory_v6_harness":
        from execution_memory_v6_context import ExecutionMemoryV6HarnessMiddleware
        return ExecutionMemoryV6HarnessMiddleware(ledger, runtime, memory_variant)
    if execution_memory and memory_variant == "evidence_memory_v7_harness":
        from execution_memory_v7_context import ExecutionMemoryV7HarnessMiddleware
        return ExecutionMemoryV7HarnessMiddleware(ledger, runtime, memory_variant)
    return (MemoryHarnessMiddleware(ledger, runtime, memory_variant) if execution_memory
            else HarnessMiddleware(ledger, runtime))
