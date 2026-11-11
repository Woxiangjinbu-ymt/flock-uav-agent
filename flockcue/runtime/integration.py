"""Opt-in R2 integration. Raw tools remain available; no synthetic tool receipts."""
from __future__ import annotations

from importlib import import_module

import json
import os

from langchain_core.messages import ToolMessage, SystemMessage
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
        self.execution_state_enabled = os.environ.get("UAV_TASK_EXECUTION_STATE") == "1"

    def _prepare(self, request):
        # Decorate before TaskEvidenceMiddleware records final request hashes.
        prepared = super()._prepare(request)
        if not self.execution_state_enabled:
            return prepared
        from task_execution_state import build_task_execution_state, concise_recovery_projection
        from task_evidence_context import append_content
        state = build_task_execution_state(self.ledger.command, self.ledger.command_id,
            self.ledger.snapshot(), self.harness.snapshot())
        projection = concise_recovery_projection(state)
        text = "\n\n[Execution recovery contract: advisory public evidence, not task completion]\n" + json.dumps(projection, ensure_ascii=False)
        original = prepared.system_message
        system = append_content(original, text) if original else SystemMessage(content=text)
        return prepared.override(system_message=system)

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

    if runtime.revision in {"entry_contract_v2", "flight_state_v3", "blocked_candidate_v4", "shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"}:
        # Keep the legacy system prompt and tool description byte-for-byte
        # unchanged; expose the opt-in contract on this tool instance only.
        run_harness.description += "\n" + ENTRY_CONTRACT_RULES
    if runtime.revision in {"shared_endpoint_v5", "terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"}:
        run_harness.description += ("\nShared endpoint v5 requires the operator's static-obstacle "
                                    "contract. Only exact XYZ endpoint conflicts are shared within "
                                    "this plan; observed geometry changes stop the plan.")
    if runtime.revision in {"terminal_progress_v6", "observed_target_v7", "search_progress_v8", "search_confirm_v9"}:
        run_harness.description += ("\nTerminal progress v6 checks public session progress once before "
                                    "exiting for no available candidate, within the existing budget. "
                                    "A failed or unavailable read does not confirm completion.")
    if runtime.revision in {"observed_target_v7", "search_progress_v8", "search_confirm_v9"}:
        run_harness.description += ("\nObserved target v7 may use the discovering actor's normal move "
                                    "slot to try a freshly observed fixed target coordinate once per ID. "
                                    "Name-only reach receipts suppress optional follow-ups, not certify "
                                    "target IDs. No extra API, round or repair budget is granted.")
    if runtime.revision == "search_progress_v8":
        run_harness.description += ("\nSearch progress v8 keeps the v7 skills and guards. After positive partial "
                                    "progress, two unchanged public-progress checks at the existing 10-move "
                                    "cadence may return needs_replan/search_progress_stalled. Progress gains "
                                    "or newly observed fixed targets reset the window; pending actionable "
                                    "target follow-ups prevent that handoff. A new trusted reached-name receipt "
                                    "only triggers a public aggregate check; strictly greater than 95% confirms "
                                    "the search stop condition. Stagnation is not proof of impossibility or task "
                                    "completion. Review remaining original obligations using raw tools; never "
                                    "resume or restart the terminal skill. No action, API, round or repair "
                                    "budget is added.")
    if runtime.revision == "search_confirm_v9":
        run_harness.description += ("\nSearch confirm v9 preserves v7 exploration, rounds and guards; it has no "
                                    "stagnation exit. Only when cached same-scope public progress is at least "
                                    "95% and a successful, effect-verified move adds a new trusted reached-name "
                                    "receipt does it request one extra public aggregate confirmation. Strictly "
                                    "greater than 95% confirms the search stop condition; the name alone does "
                                    "not. Below 95%, the original periodic and terminal checks remain unchanged. "
                                    "All checks use the existing API and time budgets; no budget is increased.")
    agent._harness = runtime
    agent.client._harness_deadline = runtime.deadline
    agent.tools = [*agent.tools, run_harness]
    if execution_memory and memory_variant.endswith("_harness"):
        policy = _MEMORY_POLICY.get(memory_variant.removesuffix("_harness"))
        if policy and policy[3]:
            middleware_type = getattr(import_module(policy[0] + "_context"), policy[3])
            return middleware_type(ledger, runtime, memory_variant)
    return (MemoryHarnessMiddleware(ledger, runtime, memory_variant) if execution_memory
            else HarnessMiddleware(ledger, runtime))


# Keys are public experiment profile IDs. Only the selected implementation is
# imported so optional observation workers keep their original loading behavior.
_MEMORY_POLICY = {
    "evidence_memory": ("execution_memory", "ExecutionMemoryLedger", "ExecutionMemoryMiddleware", None),
    "evidence_memory_v2": ("execution_memory_v2", "ExecutionMemoryLedgerV2", "ExecutionMemoryV2Middleware", None),
    "evidence_memory_v3": ("execution_memory_v3", "ExecutionMemoryLedgerV3", "ExecutionMemoryV3Middleware", "ExecutionMemoryV3HarnessMiddleware"),
    "evidence_memory_v4": ("execution_memory_v4", "ExecutionMemoryLedgerV4", "ExecutionMemoryV4Middleware", "ExecutionMemoryV4HarnessMiddleware"),
    "evidence_memory_v5": ("execution_memory_v5", "ExecutionMemoryLedgerV5", "ExecutionMemoryV5Middleware", "ExecutionMemoryV5HarnessMiddleware"),
    "evidence_memory_v6": ("execution_memory_v6", "ExecutionMemoryLedgerV6", "ExecutionMemoryV6Middleware", "ExecutionMemoryV6HarnessMiddleware"),
    "evidence_memory_v7": ("execution_memory_v7", "ExecutionMemoryLedgerV7", "ExecutionMemoryV7Middleware", "ExecutionMemoryV7HarnessMiddleware"),
}


def initialize_evidence_progress(agent, command, command_id, mode, inherited):
    """Attach command-local evidence, an optional worker and the chosen memory policy."""
    from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2, TaskEvidenceR2Middleware, progress_tool

    policy = _MEMORY_POLICY.get(mode.removesuffix("_harness"))
    if policy is None and mode not in {
        "evidence_r2", "evidence_r2_hints", "evidence_r2_shadow", "evidence_r3", "evidence_r3_shadow",
        "evidence_r2_worker", "evidence_r2_search", "evidence_harness",
    }:
        return False
    ledger_type, middleware_type = TaskEvidenceLedgerR2, TaskEvidenceR2Middleware
    if mode in {"evidence_r3", "evidence_r3_shadow"}:
        from task_evidence_r3 import TaskEvidenceLedgerR3, TaskEvidenceR3Middleware
        ledger_type, middleware_type = TaskEvidenceLedgerR3, TaskEvidenceR3Middleware
    if policy is not None:
        from execution_memory import SessionEvidenceIndexMemory
        module, ledger, middleware, _ = policy
        ledger_type = getattr(import_module(module), ledger)
        middleware_type = getattr(import_module(module + "_context"), middleware)
    if mode == "evidence_r2_worker":
        from observation_worker_r2 import (TaskEvidenceWorkerLedger,
                                           TaskEvidenceWorkerMiddleware,
                                           run_observation_worker)
        ledger_type, middleware_type = TaskEvidenceWorkerLedger, TaskEvidenceWorkerMiddleware
    if mode == "evidence_r2_search":
        from coordinated_search_worker_r2 import (TaskEvidenceSearchLedger,
                                                   TaskEvidenceSearchMiddleware,
                                                   run_public_search)
        ledger_type, middleware_type = TaskEvidenceSearchLedger, TaskEvidenceSearchMiddleware
    index = getattr(agent.blackboard, "task_evidence_index", None)
    if index is None:
        index = (SessionEvidenceIndexMemory if policy is not None else SessionEvidenceIndexR2)(agent.blackboard.session_id)
        agent.blackboard.task_evidence_index = index
    if not isinstance(index, SessionEvidenceIndexR2):
        raise ValueError("Changing evidence revision requires a fresh agent/blackboard")
    if policy is not None and not isinstance(index, SessionEvidenceIndexMemory):
        raise ValueError("Execution memory requires a fresh agent/blackboard")
    ledger = ledger_type(
        command, command_id, index=index, aggregate_feedback=mode != "evidence_r2_hints",
        inherited_blackboard=inherited, max_summary_chars=3900,
    )
    if mode.endswith("_harness"):
        middleware = install_harness(agent, ledger, execution_memory=policy is not None,
                                     memory_variant=mode if policy is not None else "evidence_memory_harness")
    elif mode == "evidence_r2_worker":
        middleware = middleware_type(
            ledger,
            lambda candidate, run: run_observation_worker(agent, ledger, candidate, run),
            agent._observation_worker_task_started)
    elif mode == "evidence_r2_search":
        middleware = middleware_type(
            ledger,
            lambda candidate, run: run_public_search(agent, ledger, candidate, run),
            agent._public_search_task_started)
    else:
        middleware = middleware_type(ledger, mode)
    # Only explicit aggregate tasks gain this tool. No hidden API
    # call occurs here; the model chooses reads within its budget.
    if ledger.aggregate and (policy is not None or mode in {"evidence_r2", "evidence_r3", "evidence_r2_search", "evidence_harness"}):
        agent.tools = [*agent.tools, progress_tool(agent.client, agent.blackboard.session_id)]
    agent._task_progress_middleware = middleware
    agent._task_progress = ledger
    return True
