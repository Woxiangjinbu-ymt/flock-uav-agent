"""Pure two-thread counterexample using the real execute/finalization bodies.

No UAVControlAgent constructor, provider, HTTP server, simulator, settings file,
or model is loaded. Backend steps are barrier-controlled fakes. The real
HarnessRuntime implements close/check_interrupt; source bodies are AST-extracted.
"""
from __future__ import annotations
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'agent4drone'))
from harness_core import HarnessRuntime

SOURCES = ['agent4drone/uav_agent.py', 'agent4drone/agent_api_service.py',
           'agent4drone/harness_core.py', 'agent4drone/harness_integration.py',
           'scripts/harness_v2_world.py', 'scripts/harness_full100_world.py']
hashes = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
entered_a, entered_b, finished_a = threading.Event(), threading.Event(), threading.Event()
runtimes, backend_records = {}, []


class NullLogger:
    def __getattr__(self, _):
        return lambda *args, **kwargs: None


class FakeBackend:
    llm_provider = 'fake-no-provider'
    llm_model = 'fake-no-model'
    debug = False
    max_iterations = 10
    max_empty_response_retries = 0
    toolchain_json_recording = False
    task_progress_mode = 'evidence_harness'

    def __init__(self):
        self.client = SimpleNamespace()

    def _blackboard_for_current_command(self):
        return SimpleNamespace(session_id='same-public-scene')

    def _create_tools(self, board):
        return []

    def _initialize_task_progress(self, command, command_id):
        self._task_progress = SimpleNamespace(command=command)
        runtime = HarnessRuntime(task_id=command, command=command,
            scene_id='same-public-scene', scene_epoch=0, ledger=SimpleNamespace(),
            invoke=lambda _: None, skill_factory=lambda *_: None,
            scope=lambda: ('same-public-scene',0), started=0., clock=lambda: 1.,
            cancel_check=self._harness_cancel_check, revision='terminal_progress_v6')
        runtimes[command] = runtime
        self._harness = runtime

    def _create_agent_runtime(self):
        return object()

    def _recursion_limit_for_steps(self):
        return 25

    def _run_agent_once(self, command, config, step_callback):
        if command == 'episode-A':
            entered_a.set()
            assert entered_b.wait(3), 'B did not reach fake backend'
        else:
            entered_b.set()
            assert finished_a.wait(3), 'A did not finalize'
            backend_records.append({'episode':'B','runtime_closed_before_B_return':runtimes[command]._closed})
            runtimes[command].check_interrupt()
        return {'messages':['Done [TASK DONE]']}

    def _extract_final_output(self, messages):
        return messages[-1]

    def _extract_intermediate_steps(self, messages):
        return []

    def _extract_token_usage(self, messages):
        return self._empty_token_usage()

    def _empty_token_usage(self):
        return {'prompt_tokens':0,'completion_tokens':0,'total_tokens':0,'llm_calls':0}

    def _extract_tool_names(self, steps):
        return []

    def _incomplete_output_reason(self, output):
        return None


namespace = dict(globals(), logger=NullLogger(), sanitize_for_log=lambda x:x,
                 empty_token_usage=lambda:{}, merge_token_usage=lambda *_:None,
                 format_token_usage_for_log=lambda *_:'no-model-usage',
                 ProviderTokenLoggingCallback=lambda **_:None)
tree = ast.parse((ROOT/'agent4drone/uav_agent.py').read_text())
original = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='UAVControlAgent')
methods = [n for n in original.body if isinstance(n,ast.FunctionDef)
           and n.name in {'execute','_record_toolchain_if_enabled'}]
assert len(methods)==2
klass = ast.ClassDef(name='AuditedAgent',bases=[ast.Name(id='FakeBackend',ctx=ast.Load())],
                     keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[klass],type_ignores=[])),
             'actual-UAVControlAgent-method-bodies','exec'),namespace)
agent = namespace['AuditedAgent']()

# Run the real service dispatch body too, with only external types stubbed.
tree = ast.parse((ROOT/'agent4drone/agent_api_service.py').read_text())
service_func = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='process_agent_command')
status = SimpleNamespace(QUEUED='queued',RUNNING='running',CANCELLED='cancelled',COMPLETED='completed',FAILED='failed')
jobs = {name:SimpleNamespace(status=status.QUEUED,result=None,error=None,completed_at=None)
        for name in ('job-A','job-B')}
class Cancelled(Exception):
    pass
class Callback:
    def __init__(self, job_id): self.job_id=job_id
    def _check_cancellation(self):
        if jobs[self.job_id].status==status.CANCELLED: raise Cancelled()
service_namespace = dict(logger=NullLogger(),jobs=jobs,agent_instance=agent,JobStatus=status,
                         CancellationCallback=Callback,JobCancelledException=Cancelled,
                         command_response_from_result=lambda x:x,datetime=datetime)
exec(compile(ast.Module(body=[service_func],type_ignores=[]),'actual-service-dispatch-body','exec'),service_namespace)
dispatch = service_namespace['process_agent_command']

def run_a():
    try: dispatch('job-A','episode-A')
    finally: finished_a.set()

a=threading.Thread(target=run_a);a.start()
assert entered_a.wait(3)
b=threading.Thread(target=dispatch,args=('job-B','episode-B'));b.start()
a.join(4);b.join(4)
assert not a.is_alive() and not b.is_alive()
result_a, result_b = jobs['job-A'].result, jobs['job-B'].result
checks = {
    'both_service_jobs_entered_shared_agent':entered_a.is_set() and entered_b.is_set(),
    'A_payload_contains_B_harness':result_a['harness']['task_id']=='episode-B',
    'A_own_harness_left_open':not runtimes['episode-A']._closed,
    'B_harness_closed_before_B_backend_return':backend_records[0]['runtime_closed_before_B_return'],
    'B_rejected_by_real_closed_runtime_guard':not result_b['success'] and 'harness task already closed' in result_b['output'],
    'sources_unchanged':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items()),
}
report = {'kind':'pure_shared_agent_episode_overlap_counterexample',
          'source_hashes':hashes,'checks':checks,'reproduced':all(checks.values()),
          'A_returned_harness_task_id':result_a['harness']['task_id'],
          'B_result_success':result_b['success'],'B_error':result_b['output'],
          'backend_records':backend_records,'model_calls':0,'simulator_calls':0,'network_calls':0,
          'scope':'Real service dispatch and unmodified execute/finalization AST bodies, actual HarnessRuntime, fake backend barriers; no live service traffic or full Agent constructor.'}
target=Path(__file__).with_name('shared-agent-overlap-counterexample.json')
target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
