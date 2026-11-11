"""One fresh process per controlled trial. Same v1 skills; isolated v2 controls."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import pickle
import sys
import time
from types import SimpleNamespace

from harness_v2_world import ROOT, World, digest, sha, write_json

BUDGET = {"max_iterations": 600, "recursion_limit": 1800, "deadline_seconds": 500,
          "api_calls_per_plan": 2000, "repair_budget": 16, "search_rounds": 16}


def client_class(world):
    from uav_api_client import UAVAPIClient, _sanitize_command_response, _compact_command_response
    from command_results import normalize_command_result
    class LocalClient(UAVAPIClient):
        def _request(self, method, endpoint, **kwargs):
            if getattr(self, "_harness_deadline", float("inf")) is not None:
                if time.monotonic() >= getattr(self, "_harness_deadline", float("inf")):
                    raise TimeoutError("Harness deadline reached before public API request")
            record = self._start_api_call_record(method, endpoint, kwargs)
            try:
                result = _sanitize_command_response(endpoint, world.request(method, endpoint, **kwargs))
                if "/command/" in endpoint:
                    result = normalize_command_result(result)
                self._finish_api_call_record(record, success=True, status_code=200,
                                             response=_compact_command_response(result) or result)
                return result
            except Exception as exc:
                self._finish_api_call_record(record, success=False, error=type(exc).__name__)
                raise
    return LocalClient


def agent_class(world, case, output):
    import uav_agent
    from harness_integration import HarnessMiddleware
    from harness_v2_cases import Faults
    uav_agent.UAVAPIClient = client_class(world)

    class LoggedMiddleware(HarnessMiddleware):
        def wrap_tool_call(self, request, handler):
            response = super().wrap_tool_call(request, handler)
            if request.tool_call["name"] == "run_harness":
                self.harness.log.emit("control_result", tool_call_id=request.tool_call["id"],
                                      content=str(getattr(response, "content", response)))
            return response

        async def awrap_tool_call(self, request, handler):
            response = await super().awrap_tool_call(request, handler)
            if request.tool_call["name"] == "run_harness":
                self.harness.log.emit("control_result", tool_call_id=request.tool_call["id"],
                                      content=str(getattr(response, "content", response)))
            return response

    class ControlledAgent(uav_agent.UAVControlAgent):
        def __init__(self, **kwargs):
            self.bootstrap = None
            super().__init__(**kwargs)
            self.max_iterations = BUDGET["max_iterations"]
            self.max_empty_response_retries = 0
            # Equal in both arms. Preserve every first attempt; no SDK HTTP retries.
            self.llm.max_retries = 0
            for key in ("root_client", "root_async_client"):
                client = getattr(self.llm, key, None)
                if client is not None:
                    client.max_retries = 0

        def _recursion_limit_for_steps(self):
            return BUDGET["recursion_limit"]

        def _stream_updates_to_steps(self, command, config, step_callback):
            result = super()._stream_updates_to_steps(command, config, step_callback)
            self.execution_diagnostics = {
                "tool_steps": len(result.get("intermediate_steps", [])),
                "graph_error_type": type(result["error"]).__name__ if result.get("error") else None,
                "stopped_due_to_step_limit": result.get("stopped_due_to_step_limit", False),
                "budget": BUDGET.copy()}
            write_json(output / "execution-diagnostics.json", self.execution_diagnostics)
            return result

        def _initialize_task_progress(self, command, command_id):
            preparation = time.monotonic()
            seed = world.public_seed()
            for actor, nearby in seed["nearby"].items():
                self.blackboard.ingest_nearby(actor, nearby)
            super()._initialize_task_progress(command, command_id)
            if self._harness is None:
                raise RuntimeError("Harness failed to initialize")
            self._task_progress_middleware = LoggedMiddleware(self._task_progress, self._harness)
            self.faults = Faults(world, case)
            self._harness.invoke = self.faults.wrap(self._harness.invoke)
            self._harness.log.emit("v2_budget", **BUDGET)
            def begin_measured_phase():
                if hasattr(self, "online_started"):
                    return
                self.common_prefix_seconds = time.monotonic() - preparation
                self.common_prefix_calls = copy.deepcopy(self.faults.calls)
                self.online_started = time.monotonic()
                self.online_started_unix = time.time()
                self._harness.started = self.online_started
                self._harness.deadline = self.online_started + BUDGET["deadline_seconds"]
                for frame in self._harness.frames.values():
                    frame.deadline = self._harness.deadline
                self.client._harness_deadline = self._harness.deadline
                world.start()
            original_emit = self._harness.log.emit
            def emit(kind, **fields):
                # First checkpoint precedes either arm's scheduling decision.
                if kind == "plan_update":
                    begin_measured_phase()
                return original_emit(kind, **fields)
            self._harness.log.emit = emit
            original_gate = self._harness._gate
            def gate(frame, decision, reason, **kwargs):
                # Unknown receipt exits have no checkpoint; observation is the
                # shared prefix and the first handoff starts measured execution.
                if decision in {"REPLAN", "STOP"}:
                    begin_measured_phase()
                return original_gate(frame, decision, reason, **kwargs)
            self._harness._gate = gate
            # This is a real public skill commit, explicitly conditional on correct
            # initial skill choice. No checker or injected fault metadata is shown.
            self.bootstrap = self._harness.control("start", skill=case["skill"])
            self.bootstrap_calls = copy.deepcopy(self.faults.calls)
            self.bootstrap_seconds = time.monotonic() - preparation
            write_json(output / "bootstrap.json", {"public_seed": seed, "result": self.bootstrap,
                        "calls": self.bootstrap_calls, "seconds": self.bootstrap_seconds,
                        "common_prefix_calls": self.common_prefix_calls,
                        "common_prefix_seconds": self.common_prefix_seconds})

        def _build_system_prompt(self):
            prompt = super()._build_system_prompt()
            if self.bootstrap is not None:
                prompt += ("\n\nA public skill was committed as the common setup for this controlled task. "
                    "Its actual public result follows. Continue the original task from this state. "
                    "Use its plan_id when resuming a paused plan. A needs_replan plan cannot resume; "
                    "use the existing tools to inspect and satisfy remaining obligations. "
                    "This report is not a whole-task checker result.\n" +
                    json.dumps(self.bootstrap, ensure_ascii=False))
            return prompt

    return ControlledAgent


def run_trial(study, trial, output, workspace):
    from harness_v2_cases import checker
    from reproduction_metrics import install_agent_metrics
    manifest = json.loads((study / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if sha((study / name).read_bytes()) != expected:
            raise ValueError(f"Frozen file changed: {name}")
    case_id, arm = trial["case_id"], trial["arm"]
    case = json.loads((study / "fixtures" / f"{case_id}.json").read_text())
    output.mkdir(parents=True, exist_ok=False)
    os.environ["UAV_HARNESS_SCHEDULER"] = arm
    os.environ["UAV_TASK_PROGRESS_MODE"] = "evidence_harness"
    os.environ["UAV_MODEL_REQUEST_AUDIT"] = "1"
    world = World()
    blob = (study / "fixtures" / f"{case_id}.pkl").read_bytes()
    world.restore(blob, case["snapshot_sha256"])
    world.timeline = pickle.loads((study / "fixtures" / f"{case_id}.timeline.pkl").read_bytes())
    world.audit_path = output / "public-api.jsonl"
    world.fault_path = output / "faults.json"
    initial = world.state_digest()
    if initial != case["state_sha256"]:
        raise ValueError("Initial state mismatch")
    # Redirect native agent recordings before importing the agent implementation.
    import logging_config
    logging_config.LOG_DIR = output / "agent-logs"
    import toolchain_recorder
    toolchain_recorder.TOOLCHAIN_LOG_DIR = output / "agent-logs/toolchains"
    from m1_progress_pilot import settings_for_run
    _, api_key = settings_for_run(workspace)
    Agent = agent_class(world, case, output)
    agent = Agent(base_url="http://testserver", llm_provider="openai-compatible",
                  llm_model="deepseek-flash", llm_api_key=api_key, llm_base_url="https://api.deepseek.com",
                  temperature=0.1, verbose=False, share_blackboard_by_session=False,
                  toolchain_json_recording=True, task_progress_mode="evidence_harness")
    steps = []
    def on_step(*args, **kwargs):
        steps.append({"elapsed": time.monotonic() - agent.online_started, "args": str(args), "kwargs": str(kwargs)})
        write_json(output / "partial-steps.json", steps)
    service = SimpleNamespace(process_agent_command=lambda job, command: agent.execute(command, step_callback=on_step))
    install_agent_metrics(service, output / "reproduction-metrics.jsonl", "https://api.deepseek.com")
    write_json(output / "start.json", {**trial, "pid": os.getpid(), "started_unix": time.time(),
                "initial_state_sha256": initial, "snapshot_sha256": sha(blob), "budget": BUDGET,
                "transport": "real local ASGI simulator; real provider HTTP", "sdk_max_retries": 0})
    started = time.monotonic()
    result = service.process_agent_command(trial["trial_id"], case["command"])
    ready = time.monotonic() - getattr(agent, "online_started", started)
    world.advance(min(ready, BUDGET["deadline_seconds"]))
    checked = checker(world, case)
    events = [json.loads(line) for line in (output / "reproduction-metrics.jsonl").read_text().splitlines()]
    errors = [e for e in events if e["event"] in {"llm_error", "http_error"} or
              (e["event"] == "http_end" and e["status_code"] != 200)]
    fault_events = getattr(getattr(agent, "faults", None), "events", [])
    write_json(output / "faults.json", fault_events)
    write_json(output / "harness.json", agent._harness.snapshot() if agent._harness else {})
    write_json(output / "final-world.json", World.inspect_blob(world.snapshot()))
    llm = [e for e in events if e["event"] == "llm_end"]
    summary = {**trial, "completed_unix": time.time(), "status": "failed" if errors or agent.bootstrap is None else "completed",
        "checker": checked, "agent_success": result.get("success"), "agent_error": result.get("error"),
        "output": result.get("output"), "bootstrap_seconds": getattr(agent, "bootstrap_seconds", None),
        "common_prefix_seconds": getattr(agent, "common_prefix_seconds", None),
        "ready_seconds": min(ready, BUDGET["deadline_seconds"]), "processing_wall_seconds": ready,
        "total_execution_seconds": time.monotonic() - started,
        "logical_calls": sum(e["event"] == "llm_start" for e in events),
        "logical_calls_within_500": sum(e["event"] == "llm_start" and
                e["timestamp"] <= getattr(agent, "online_started_unix", 0) + 500 for e in events),
        "http_attempts": sum(e["event"] == "http_start" for e in events),
        "model_wall_seconds": sum(e.get("duration_seconds", 0) for e in events if e["event"] in {"llm_end", "llm_error"}),
        "usage": {k: sum((e.get("usage") or {}).get(k) or 0 for e in llm)
                  for k in ["prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens"]},
        "missing_usage_calls": sum(e.get("usage") is None for e in llm), "errors": errors,
        "usage_missing_fields": {k: sum((e.get("usage") or {}).get(k) is None for e in llm)
                                 for k in ["prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens"]},
        "tool_steps": len(result.get("intermediate_steps", [])), "budget": BUDGET,
        "execution_diagnostics": getattr(agent, "execution_diagnostics", {}),
        "initial_state_sha256": initial, "fault_events": len(fault_events)}
    write_json(output / "result.json", summary)
    write_json(output / "seal.json", {"status": summary["status"], "files": {
        str(p.relative_to(output)): sha(p.read_bytes()) for p in output.rglob("*") if p.is_file()}})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    result = run_trial(args.study, json.loads(args.trial.read_text()), args.output, args.workspace)
    print(json.dumps({k: result[k] for k in ("trial_id", "status", "logical_calls", "usage", "ready_seconds")}))
    raise SystemExit(0 if result["status"] == "completed" else 1)
