"""Natural skill choice from the original task; no fixture commits or fault injection."""
from __future__ import annotations
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import argparse
import json
import os
import pickle
import time
from types import SimpleNamespace

from harness_v2_world import World, sha, digest, write_json
from harness_full100_world import FullWorld, TaskDeadlineReached, origin
from harness_v2_online import BUDGET, client_class


def agent_class(world, output, *, harness_enabled=True):
    import uav_agent
    from harness_integration import HarnessMiddleware
    from langchain.agents.middleware import AgentMiddleware
    uav_agent.UAVAPIClient = client_class(world)

    class BaselineLoggingMiddleware(AgentMiddleware):
        """Same public-call provenance/deadline, without task evidence or plans."""
        def wrap_tool_call(self, request, handler):
            world.check_admission()
            call = request.tool_call
            with origin(kind='model_tool', tool=call['name'], tool_call_id=call['id']):
                return handler(request)

        async def awrap_tool_call(self, request, handler):
            world.check_admission()
            call = request.tool_call
            with origin(kind='model_tool', tool=call['name'], tool_call_id=call['id']):
                return await handler(request)

        def wrap_model_call(self, request, handler):
            world.check_admission()
            return handler(request)

        async def awrap_model_call(self, request, handler):
            world.check_admission()
            return await handler(request)

    class LoggedMiddleware(HarnessMiddleware):
        def wrap_tool_call(self, request, handler):
            world.check_admission()
            call = request.tool_call
            with origin(kind='model_tool', tool=call['name'], tool_call_id=call['id']):
                try:
                    response = super().wrap_tool_call(request, handler)
                except BaseException as exc:
                    self.harness.log.emit('tool_exception', tool_call_id=call['id'], error=type(exc).__name__)
                    raise
            if call['name'] == 'run_harness':
                self.harness.log.emit('control_result', tool_call_id=call['id'],
                                      content=str(getattr(response, 'content', response)))
            return response

        async def awrap_tool_call(self, request, handler):
            world.check_admission()
            call = request.tool_call
            with origin(kind='model_tool', tool=call['name'], tool_call_id=call['id']):
                response = await super().awrap_tool_call(request, handler)
            if call['name'] == 'run_harness':
                self.harness.log.emit('control_result', tool_call_id=call['id'],
                                      content=str(getattr(response, 'content', response)))
            return response

        def wrap_model_call(self, request, handler):
            world.check_admission()
            return super().wrap_model_call(request, handler)

        async def awrap_model_call(self, request, handler):
            world.check_admission()
            return await super().awrap_model_call(request, handler)

    class NaturalAgent(uav_agent.UAVControlAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.max_iterations = BUDGET['max_iterations']
            self.max_empty_response_retries = 0
            self.llm.max_retries = 0
            for key in ('root_client', 'root_async_client'):
                if getattr(self.llm, key, None) is not None:
                    getattr(self.llm, key).max_retries = 0

        def _recursion_limit_for_steps(self):
            return BUDGET['recursion_limit']

        def _initialize_task_progress(self, command, command_id):
            if not harness_enabled and self.task_progress_mode != 'off':
                raise ValueError('Baseline arm requires explicit task_progress_mode=off')
            super()._initialize_task_progress(command, command_id)
            if not harness_enabled:
                if self._task_progress is not None or getattr(self, '_harness', None) is not None:
                    raise RuntimeError('Baseline arm unexpectedly initialized evidence or Harness')
                self.client._harness_deadline = world.started + world.deadline_seconds
                self._task_progress_middleware = BaselineLoggingMiddleware()
                return
            if self._harness is None:
                raise RuntimeError('Harness initialization failed')
            self._harness.started = world.started
            self._harness.deadline = world.started + world.deadline_seconds
            self.client._harness_deadline = self._harness.deadline
            self._task_progress_middleware = LoggedMiddleware(self._task_progress, self._harness)
            invoke = self._harness.invoke
            def logged(call):
                frame = next((f for f in self._harness.frames.values() if f.status == 'running'), None)
                with origin(kind='skill', tool=call.tool, plan_id=frame.plan_id if frame else None):
                    return invoke(call)
            self._harness.invoke = logged
            self._harness.log.emit('full100_budget', **BUDGET)
            # No public-seed preloading or skill start. The unchanged agent chooses
            # what to observe and whether to invoke run_harness from the task.
            assert not self._harness.frames

        def _stream_updates_to_steps(self, command, config, step_callback):
            result = super()._stream_updates_to_steps(command, config, step_callback)
            self.execution_diagnostics = {'tool_steps': len(result.get('intermediate_steps', [])),
                'graph_error_type': type(result['error']).__name__ if result.get('error') else None,
                'stopped_due_to_step_limit': result.get('stopped_due_to_step_limit', False),
                'budget': BUDGET.copy()}
            write_json(output / 'execution-diagnostics.json', self.execution_diagnostics)
            return result

    return NaturalAgent


def run_trial(study, trial, output, workspace):
    from reproduction_metrics import install_agent_metrics
    from m1_progress_pilot import settings_for_run
    manifest = json.loads((study / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if sha((study / name).read_bytes()) != expected:
            raise ValueError(f'Frozen artifact changed: {name}')
    allowed = set(manifest['authorized_trial_ids'])
    if trial['trial_id'] not in allowed:
        raise ValueError('Outside authorized 40-execution calibration')
    output.mkdir(parents=True, exist_ok=False)
    fixture = json.loads((study / 'fixtures' / f"{trial['task_id']}.json").read_text())
    os.environ.update(UAV_HARNESS_SCHEDULER=trial['arm'], UAV_TASK_PROGRESS_MODE='evidence_harness',
                      UAV_MODEL_REQUEST_AUDIT='1')
    world = FullWorld(output=output)
    blob = (study / 'fixtures' / f"{trial['task_id']}.pkl").read_bytes()
    world.restore(blob, fixture['snapshot_sha256'])
    world.timeline = pickle.loads((study / 'fixtures' / fixture['timeline_file']).read_bytes())
    assert world.state_digest() == fixture['state_sha256']
    assert digest(world.public_seed()) == fixture['public_startup_sha256']
    world.restore(blob)
    import logging_config, toolchain_recorder
    logging_config.LOG_DIR = output / 'agent-logs'
    toolchain_recorder.TOOLCHAIN_LOG_DIR = output / 'agent-logs/toolchains'
    _, api_key = settings_for_run(workspace)
    Agent = agent_class(world, output)
    # The task clock includes constructor observations and all subsequent activity.
    world.start()
    write_json(output / 'start.json', {**trial, 'pid': os.getpid(), 'started_unix': world.started_unix,
        'initial_state_sha256': fixture['state_sha256'], 'budget': BUDGET,
        'forced_skill_commit': False, 'fault_injection': False, 'sdk_max_retries': 0})
    try:
        agent = Agent(base_url='http://testserver', llm_provider='openai-compatible', llm_model='deepseek-flash',
                      llm_api_key=api_key, llm_base_url='https://api.deepseek.com', temperature=0.1,
                      verbose=False, share_blackboard_by_session=False, toolchain_json_recording=True,
                      task_progress_mode='evidence_harness')
        steps = []
        def on_step(*args, **kwargs):
            steps.append({'elapsed': world.elapsed(), 'args': str(args), 'kwargs': str(kwargs)})
            write_json(output / 'partial-steps.json', steps)
        service = SimpleNamespace(process_agent_command=lambda job, command: agent.execute(command, step_callback=on_step))
        install_agent_metrics(service, output / 'reproduction-metrics.jsonl', 'https://api.deepseek.com')
        result = service.process_agent_command(trial['trial_id'], fixture['command'])
    finally:
        world.freeze_score()
    processing = world.elapsed()
    checked = world.evaluate(trial['task_id'], world.score_blob)
    events = [json.loads(x) for x in (output / 'reproduction-metrics.jsonl').read_text().splitlines()]
    llm = [e for e in events if e['event'] == 'llm_end']
    errors = [e for e in events if e['event'] in ('llm_error', 'http_error') or
              (e['event'] == 'http_end' and e['status_code'] != 200)]
    if checked['errors']:
        errors.append({'event': 'checker_error', 'details': checked['errors']})
    write_json(output / 'harness.json', agent._harness.snapshot() if agent._harness else {})
    write_json(output / 'score-world.json', World.inspect_blob(world.score_blob))
    write_json(output / 'post-drain-world.json', World.inspect_blob(world.snapshot()))
    summary = {**trial, 'status': 'failed' if errors else 'completed', 'completed_unix': time.time(),
        'checker': checked, 'agent_success': result.get('success'), 'agent_error': result.get('error'),
        'output': result.get('output'), 'ready_seconds': world.score_seconds,
        'processing_wall_seconds': processing, 'logical_calls': sum(e['event'] == 'llm_start' for e in events),
        'logical_calls_within_500': sum(e['event'] == 'llm_start' and e['timestamp'] <= world.started_unix + 500 for e in events),
        'http_attempts': sum(e['event'] == 'http_start' for e in events),
        'model_wall_seconds': sum(e.get('duration_seconds', 0) for e in events if e['event'] in ('llm_end', 'llm_error')),
        'usage': {k: sum((e.get('usage') or {}).get(k) or 0 for e in llm)
                  for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cache_hit_tokens', 'cache_miss_tokens')},
        'missing_usage_calls': sum(e.get('usage') is None for e in llm),
        'usage_missing_fields': {k: sum((e.get('usage') or {}).get(k) is None for e in llm)
                                 for k in ('prompt_tokens', 'completion_tokens', 'cache_hit_tokens', 'cache_miss_tokens')},
        'errors': errors, 'physical_cost': world.cost(),
        'execution_diagnostics': getattr(agent, 'execution_diagnostics', {}),
        'initial_state_sha256': fixture['state_sha256'], 'public_startup_sha256': fixture['public_startup_sha256'],
        'score_world_sha256': sha(world.score_blob)}
    write_json(output / 'result.json', summary)
    write_json(output / 'seal.json', {'status': summary['status'], 'files': {
        str(p.relative_to(output)): sha(p.read_bytes()) for p in output.rglob('*') if p.is_file()}})
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('study', 'trial', 'output', 'workspace'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    result = run_trial(a.study, json.loads(a.trial.read_text()), a.output, a.workspace)
    print(json.dumps({k: result[k] for k in ('trial_id', 'status', 'usage', 'ready_seconds')}))
    raise SystemExit(0 if result['status'] == 'completed' else 1)
