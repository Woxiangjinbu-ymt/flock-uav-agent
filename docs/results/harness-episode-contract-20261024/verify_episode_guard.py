"""Offline shared-Agent lifecycle comparison: actual AST bodies, controlled fakes.

Does not import uav_agent/service modules, instantiate a provider, or touch a
simulator/network. Both versions use the same backend and thread schedule.
Original baseline evidence remains unchanged. Only this audit's output is written.
"""
from __future__ import annotations
import ast
import asyncio
from contextlib import contextmanager
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
BASELINE = Path('/tmp/harness-episode-contract-before-20261024')
sys.path.insert(0, str(ROOT / 'agent4drone'))
from harness_core import HarnessRuntime

WAIT = 5
SOURCES = ('agent4drone/uav_agent.py', 'agent4drone/agent_api_service.py')
SHARED = ('agent4drone/harness_core.py', 'agent4drone/harness_integration.py',
          'scripts/harness_v2_world.py', 'scripts/harness_full100_world.py')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

class NullLogger:
    def __getattr__(self, _):
        return lambda *a, **k: None

class ProbeAbort(BaseException):
    pass

class JobCancelled(Exception):
    pass

class HTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail

class Board:
    session_id = 'same-public-scene'
    def __init__(self):
        self.clear_count = 0
        self.contents = ['prior-evidence']
    def clear(self):
        self.clear_count += 1
        self.contents.clear()

class Schedule:
    def __init__(self):
        self.entered_a = threading.Event()
        self.entered_b = threading.Event()
        self.release_a = threading.Event()
        self.finished_a = threading.Event()
        self.b_settled = threading.Event()
        self.finalizing = threading.Event()
        self.release_finalizer = threading.Event()
        self.block_finalizer = False
        self.runtimes = {}
        self.backend_entries = []
        self.closed_when_b_returned = []
        self.abort = None
        self.cleanup_failure = None
        self.failed_close_calls = 0

class Client:
    def __init__(self, schedule):
        self.schedule = schedule
        self.recorder_calls = []
        self.api_calls = []
        self._harness_deadline = 'idle-deadline'
        self.current_command = None
        self.session = {'id':'same-public-scene', 'created_at':'new',
                        'statistics':{'environment_id':'env','total_commands_executed':0}}
    def stop_api_call_recording(self):
        self.recorder_calls.append({'method':'stop','command':self.current_command})
        if self.schedule.cleanup_failure == 'recorder' and len(self.recorder_calls) >= 2:
            raise ProbeAbort('recorder cleanup failure')
        if self.schedule.block_finalizer and len(self.recorder_calls) == 2:
            self.schedule.finalizing.set()
            assert self.schedule.release_finalizer.wait(WAIT), 'finalization not released'
    def get_current_session(self):
        self.api_calls.append('get_current_session')
        return self.session
    def get_task_progress(self):
        self.api_calls.append('get_task_progress')
        return {'progress_percentage':0}
    def list_drones(self):
        self.api_calls.append('list_drones')
        return []

class FakeBackend:
    llm_provider = 'fake-no-provider'
    llm_model = 'fake-no-model'
    debug = False
    verbose = False
    max_iterations = 10
    max_empty_response_retries = 0
    toolchain_json_recording = False
    task_progress_mode = 'evidence_harness'
    def __init__(self, schedule):
        # Deliberately follows old __new__ adapters: no gate is pre-initialized.
        self.schedule = schedule
        self.client = Client(schedule)
        self.blackboard = Board()
        self._session_blackboards = {'same-public-scene':self.blackboard}
    def _blackboard_for_current_command(self):
        return self.blackboard
    def _create_tools(self, board):
        return []
    def _initialize_task_progress(self, command, command_id):
        self._task_progress = SimpleNamespace(command=command)
        self._model_wire_requests.append({'command':command, 'fake':True})
        self.client.current_command = command
        self.client._harness_deadline = command + '-deadline'
        runtime = HarnessRuntime(task_id=command, command=command,
            scene_id='same-public-scene', scene_epoch=0, ledger=SimpleNamespace(),
            invoke=lambda _: None, skill_factory=lambda *_: None,
            scope=lambda: ('same-public-scene',0), started=0., clock=lambda: 1.,
            cancel_check=self._harness_cancel_check, revision='terminal_progress_v6')
        self.schedule.runtimes[command] = runtime
        self._harness = runtime
        if self.schedule.cleanup_failure == 'harness':
            def failed_close(reason):
                self.schedule.failed_close_calls += 1
                raise ProbeAbort('harness cleanup failure')
            runtime.close = failed_close
    def _create_agent_runtime(self):
        return object()
    def _recursion_limit_for_steps(self):
        return 25
    def _run_agent_once(self, command, config, step_callback):
        schedule = self.schedule
        schedule.backend_entries.append(command)
        if schedule.abort is not None:
            exc = schedule.abort
            schedule.abort = None
            raise exc
        if command == 'episode-A':
            schedule.entered_a.set()
            assert schedule.release_a.wait(WAIT), 'A backend not released'
        elif command == 'episode-B' and not schedule.finished_a.is_set():
            schedule.entered_b.set()
            schedule.b_settled.set()
            assert schedule.finished_a.wait(WAIT), 'A did not finalize'
            schedule.closed_when_b_returned.append(schedule.runtimes[command]._closed)
        schedule.runtimes[command].check_interrupt()
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

METHODS = {'execute', '_execute_command', '_exclusive_episode',
           '_record_toolchain_if_enabled', 'reset_task_evidence',
           '_clear_task_evidence', 'get_session_summary', '_get_session_summary',
           '_current_session_id', 'refresh_session_context', '_refresh_session_context'}

def load_version(source_root):
    text = (source_root / SOURCES[0]).read_text()
    tree = ast.parse(text)
    original = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='UAVControlAgent')
    methods = [n for n in original.body if isinstance(n,ast.FunctionDef) and n.name in METHODS]
    extra = [n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AgentBusyError']
    namespace = dict(globals(), logger=NullLogger(),
        _EPISODE_GATE_INITIALIZATION=threading.Lock(), sanitize_for_log=lambda x:x,
        empty_token_usage=lambda:{}, merge_token_usage=lambda *_:None,
        format_token_usage_for_log=lambda *_:'no-model-usage',
        ProviderTokenLoggingCallback=lambda **_:None)
    klass=ast.ClassDef(name='AuditedAgent',bases=[ast.Name(id='FakeBackend',ctx=ast.Load())],
                      keywords=[],body=methods,decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=extra+[klass],type_ignores=[])),
                 str(source_root/SOURCES[0]),'exec'),namespace)
    namespace['extracted_methods']=[n.name for n in methods]
    return namespace

STATUS=SimpleNamespace(QUEUED='queued',RUNNING='running',CANCELLED='cancelled',COMPLETED='completed',FAILED='failed')

def service_for(source_root, namespace, agent):
    jobs={}
    class Callback:
        def __init__(self, job_id): self.job_id=job_id
        def _check_cancellation(self):
            if jobs[self.job_id].status==STATUS.CANCELLED: raise JobCancelled('cancelled by test')
    service=dict(logger=NullLogger(), jobs=jobs, agent_instance=agent, JobStatus=STATUS,
        CancellationCallback=Callback, JobCancelledException=JobCancelled,
        AgentBusyError=namespace.get('AgentBusyError', type('UnusedBusyError',(RuntimeError,),{})),
        command_response_from_result=lambda x:x, datetime=datetime,
        CommandRequest=SimpleNamespace, HTTPException=HTTPError,
        SessionSummaryResponse=lambda **kw:kw)
    names={'process_agent_command','execute_command','get_session_summary','cancel_job'}
    tree=ast.parse((source_root/SOURCES[1]).read_text())
    funcs=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    for node in funcs: node.decorator_list=[]
    exec(compile(ast.fix_missing_locations(ast.Module(body=funcs,type_ignores=[])),
                 str(source_root/SOURCES[1]),'exec'),service)
    def dispatch(job_id, command):
        jobs[job_id]=SimpleNamespace(status=STATUS.QUEUED,result=None,error=None,completed_at=None)
        service['process_agent_command'](job_id,command)
    service['dispatch']=dispatch
    return service

def state(agent):
    # Compare identity and mutable values, not just task_id; no model payloads.
    objects={key:id(getattr(agent,key,None)) for key in (
        '_harness','_task_progress','_model_wire_requests','_model_wire_errors',
        'blackboard','tools','agent','_harness_cancel_check')}
    return {'identities':objects,
        'task_id':getattr(getattr(agent,'_harness',None),'task_id',None),
        'task_started':getattr(agent,'_harness_task_started',None),
        'deadline':agent.client._harness_deadline,
        'wire':json.loads(json.dumps(getattr(agent,'_model_wire_requests',[]))),
        'recorder_calls':list(agent.client.recorder_calls),
        'api_calls':list(agent.client.api_calls),
        'board_contents':list(agent.blackboard.contents),
        'board_clear_count':agent.blackboard.clear_count,
        'session_context':getattr(agent,'session_context',None),
        'session_board_keys':list(agent._session_blackboards)}

def same_schedule(label, source_root):
    namespace=load_version(source_root)
    schedule=Schedule()
    agent=namespace['AuditedAgent'](schedule)
    service=service_for(source_root,namespace,agent)
    errors=[]
    def run_a():
        try: service['dispatch']('job-A','episode-A')
        except BaseException as exc: errors.append(type(exc).__name__)
        finally: schedule.finished_a.set()
    def run_b():
        try: service['dispatch']('job-B','episode-B')
        except BaseException as exc: errors.append(type(exc).__name__)
        finally: schedule.b_settled.set()
    a=threading.Thread(target=run_a,name=label+'-A')
    b=threading.Thread(target=run_b,name=label+'-B')
    a.start()
    assert schedule.entered_a.wait(WAIT)
    before=state(agent)
    b.start()
    assert schedule.b_settled.wait(WAIT)
    after=state(agent)
    busy_boundaries={}
    if label=='candidate':
        for operation in ('reset_task_evidence','get_session_summary','refresh_session_context'):
            snapshot=state(agent)
            try: getattr(agent,operation)()
            except namespace['AgentBusyError'] as exc:
                busy_boundaries[operation]={'rejected':True,'code':exc.code,'no_effects':snapshot==state(agent)}
            else: busy_boundaries[operation]={'rejected':False,'no_effects':snapshot==state(agent)}
        for operation, args in (('execute_command',(SimpleNamespace(command='sync-B'),)),('get_session_summary',())):
            snapshot=state(agent)
            try: asyncio.run(service[operation](*args))
            except HTTPError as exc:
                busy_boundaries['service_'+operation]={'status_code':exc.status_code,'code':exc.detail['code'],'no_effects':snapshot==state(agent)}
            else: busy_boundaries['service_'+operation]={'status_code':200,'no_effects':snapshot==state(agent)}
    schedule.release_a.set()
    a.join(WAIT); b.join(WAIT)
    assert not a.is_alive() and not b.is_alive()
    job_a,job_b=service['jobs']['job-A'],service['jobs']['job-B']
    initial_runtimes={key: {'closed':value._closed} for key,value in schedule.runtimes.items()}
    initial_entries=list(schedule.backend_entries)
    service['dispatch']('job-B-after-A','episode-B')
    post=service['jobs']['job-B-after-A']
    return {'label':label,'extracted_methods':namespace['extracted_methods'],
        'schedule':'A reaches fake backend; B attempts; inspect; release A; wait A/B return; resubmit B',
        'thread_errors':errors, 'B_attempt_kept_A_state_exactly':before==after,
        'B_backend_entered_during_A':schedule.entered_b.is_set(),
        'A_job_status':job_a.status,'A_success':job_a.result['success'],
        'A_payload_harness_task_id':job_a.result['harness']['task_id'],
        'B_job_status':job_b.status,'B_error':job_b.error,
        'B_result':None if job_b.result is None else {'success':job_b.result['success'],'output':job_b.result['output']},
        'runtimes_after_first_pair':initial_runtimes,'initial_backend_entries':initial_entries,
        'B_runtime_closed_before_backend_return':schedule.closed_when_b_returned,
        'B_resubmitted_after_A':{'status':post.status,'success':post.result['success'],'task_id':post.result['harness']['task_id'],'closed':schedule.runtimes['episode-B']._closed},
        'busy_boundaries':busy_boundaries}

def cancellation_check(namespace):
    schedule=Schedule(); agent=namespace['AuditedAgent'](schedule)
    service=service_for(ROOT,namespace,agent)
    def run_a():
        try: service['dispatch']('cancel-A','episode-A')
        finally: schedule.finished_a.set()
    a=threading.Thread(target=run_a); a.start(); assert schedule.entered_a.wait(WAIT)
    asyncio.run(service['cancel_job']('cancel-A'))
    before=state(agent)
    service['dispatch']('cancel-B','episode-B')
    during=service['jobs']['cancel-B']
    unchanged=before==state(agent)
    still_active=not schedule.finished_a.is_set()
    schedule.release_a.set(); a.join(WAIT); assert not a.is_alive()
    a_closed=schedule.runtimes['episode-A']._closed
    service['dispatch']('cancel-after','episode-B')
    return {'cancelled_status_retained':service['jobs']['cancel-A'].status==STATUS.CANCELLED,
        'A_still_active_when_B_refused':still_active,'B_failed_busy':during.status==STATUS.FAILED and 'agent_busy' in during.error,
        'busy_no_effects':unchanged,'A_closed_after_actual_return':a_closed,
        'B_after_return_success':service['jobs']['cancel-after'].result['success']}

def finalization_check(namespace):
    schedule=Schedule(); schedule.block_finalizer=True; schedule.release_a.set()
    agent=namespace['AuditedAgent'](schedule)
    result={}
    def run_a(): result['A']=agent.execute('episode-A')
    a=threading.Thread(target=run_a);a.start();assert schedule.finalizing.wait(WAIT)
    snapshot=state(agent)
    try: agent.execute('episode-B')
    except namespace['AgentBusyError']: rejected=True
    else: rejected=False
    unchanged=snapshot==state(agent)
    closed=schedule.runtimes['episode-A']._closed
    schedule.release_finalizer.set();a.join(WAIT);assert not a.is_alive()
    return {'A_runtime_closed_while_finalizer_still_active':closed,
        'B_still_rejected':rejected,'no_effects':unchanged,'A_returned_own_harness':result['A']['harness']['task_id']=='episode-A'}

def exception_check(namespace, exc):
    schedule=Schedule();schedule.abort=exc; agent=namespace['AuditedAgent'](schedule)
    caught=None; first=None
    try: first=agent.execute('abort-A')
    except BaseException as error: caught=type(error).__name__
    lock_free=agent._episode_lock.acquire(blocking=False)
    if lock_free: agent._episode_lock.release()
    abort_closed=schedule.runtimes['abort-A']._closed
    second=agent.execute('after-abort')
    return {'exception':type(exc).__name__,'escaped':caught,'lease_released':lock_free,
        'first_result_success':first and first['success'],
        'aborted_runtime_closed':abort_closed,'subsequent_execution_success':second['success'],
        'subsequent_harness_task_id':second['harness']['task_id']}

def internal_reset_check(namespace):
    schedule=Schedule();agent=namespace['AuditedAgent'](schedule)
    board=agent.blackboard
    agent._evidence_session_markers={'same-public-scene':('old','env',100)}
    with agent._exclusive_episode('test-command-owner'):
        sid=agent._current_session_id()
    internal={'session_id':sid,'board_cleared':not board.contents,
              'session_cache_removed':'same-public-scene' not in agent._session_blackboards,
              'one_public_metadata_read':agent.client.api_calls==['get_current_session']}
    agent.blackboard=Board();agent._session_blackboards={'same-public-scene':agent.blackboard}
    before_api=list(agent.client.api_calls)
    agent.reset_task_evidence('same-public-scene')
    idle={'board_cleared':not agent.blackboard.contents,'session_cache_removed':not agent._session_blackboards,
          'no_world_or_public_api':before_api==agent.client.api_calls}
    return {'internal_visible_marker_reset':internal,'idle_public_memory_reset':idle}

def failed_cleanup_check(namespace, failed_component):
    schedule=Schedule(); schedule.abort=ProbeAbort('original backend abort')
    schedule.cleanup_failure=failed_component
    agent=namespace['AuditedAgent'](schedule)
    caught=None
    try: agent.execute('cleanup-abort')
    except BaseException as exc: caught={'type':type(exc).__name__,'message':str(exc)}
    lock_free=agent._episode_lock.acquire(blocking=False)
    if lock_free: agent._episode_lock.release()
    boundaries={}
    for operation,args in (('execute',('next-command',)),('reset_task_evidence',()),
                           ('get_session_summary',()),('refresh_session_context',())):
        before=state(agent)
        try: getattr(agent,operation)(*args)
        except namespace['AgentBusyError'] as exc:
            boundaries[operation]={'refused':True,'code':exc.code,
                'requires_fresh_instance':'fresh agent instance' in str(exc),
                'no_effects':before==state(agent)}
        else: boundaries[operation]={'refused':False,'no_effects':before==state(agent)}
    return {'failed_component':failed_component,'original_exception_preserved':caught=={'type':'ProbeAbort','message':'original backend abort'},
        'cleanup_failure_flag':getattr(agent,'_episode_cleanup_failed',False),
        'lease_released':lock_free,'recorder_cleanup_attempted':len(agent.client.recorder_calls)==2,
        'harness_cleanup_attempted':schedule.failed_close_calls==1 if failed_component=='harness' else schedule.runtimes['cleanup-abort']._closed,
        'protected_operations':boundaries}

def cancelled_before_dispatch_check(namespace):
    schedule=Schedule(); agent=namespace['AuditedAgent'](schedule)
    service=service_for(ROOT,namespace,agent)
    job=SimpleNamespace(status=STATUS.CANCELLED,result=None,error=None,completed_at=None)
    service['jobs']['pre-cancelled']=job
    before=state(agent)
    service['process_agent_command']('pre-cancelled','never-enter')
    return {'status_retained':job.status==STATUS.CANCELLED,
        'completed_at_set_after_worker_return':isinstance(job.completed_at,datetime),
        'no_execution_or_shared_state_effects':not schedule.backend_entries and before==state(agent)}

def unchanged_bodies():
    def methods(path):
        tree=ast.parse(path.read_text())
        klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='UAVControlAgent')
        return {node.name:node for node in klass.body if isinstance(node,ast.FunctionDef)}
    before,after=methods(BASELINE/SOURCES[0]),methods(ROOT/SOURCES[0])
    def normalized(node):
        node.name='same'; node.decorator_list=[]
        if isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
            node.body=node.body[1:]
        return ast.dump(node,include_attributes=False)
    pairs={'execute':'_execute_command','reset_task_evidence':'_clear_task_evidence',
        'get_session_summary':'_get_session_summary','refresh_session_context':'_refresh_session_context',
        '_record_toolchain_if_enabled':'_record_toolchain_if_enabled'}
    return {old:normalized(before[old])==normalized(after[new]) for old,new in pairs.items()}

def main():
    source_hashes={'baseline':{p:digest(BASELINE/p) for p in SOURCES},
                   'candidate':{p:digest(ROOT/p) for p in SOURCES},
                   'shared':{p:digest(ROOT/p) for p in SHARED}}
    baseline=same_schedule('baseline',BASELINE)
    candidate=same_schedule('candidate',ROOT)
    namespace=load_version(ROOT)
    cancellation=cancellation_check(namespace)
    finalization=finalization_check(namespace)
    exceptions=[exception_check(namespace,RuntimeError('fake ordinary failure')),
                exception_check(namespace,ProbeAbort('fake BaseException'))]
    resets=internal_reset_check(namespace)
    failed_cleanup=[failed_cleanup_check(namespace,component) for component in ('harness','recorder')]
    cancelled_before_dispatch=cancelled_before_dispatch_check(namespace)
    unchanged=unchanged_bodies()
    checks={
        'baseline_reproduced_cross_episode_archive':baseline['A_payload_harness_task_id']=='episode-B',
        'baseline_left_A_open':not baseline['runtimes_after_first_pair']['episode-A']['closed'],
        'baseline_B_closed_prematurely':baseline['B_runtime_closed_before_backend_return']==[True],
        'baseline_B_failed_real_runtime_guard':baseline['B_result']['success'] is False and 'harness task already closed' in baseline['B_result']['output'],
        'candidate_B_busy_failed_no_result':candidate['B_job_status']==STATUS.FAILED and 'agent_busy' in candidate['B_error'] and candidate['B_result'] is None,
        'candidate_B_never_entered_backend':not candidate['B_backend_entered_during_A'] and candidate['initial_backend_entries']==['episode-A'],
        'candidate_B_zero_shared_state_effects':candidate['B_attempt_kept_A_state_exactly'],
        'candidate_A_owns_archive':candidate['A_payload_harness_task_id']=='episode-A' and candidate['A_success'],
        'candidate_only_A_runtime_created_and_closed':candidate['runtimes_after_first_pair']=={'episode-A':{'closed':True}},
        'candidate_B_can_run_after_A_returns':candidate['B_resubmitted_after_A']=={'status':STATUS.COMPLETED,'success':True,'task_id':'episode-B','closed':True},
    }
    for name,result in candidate['busy_boundaries'].items():
        checks['busy_'+name]=result['no_effects'] and (result.get('rejected',False) or result.get('status_code')==409) and result.get('code')=='agent_busy'
    for name,result in cancellation.items(): checks['cancellation_'+name]=result
    for name,result in finalization.items(): checks['finalization_'+name]=result
    for result in exceptions:
        key=result['exception']
        checks[key+'_lease_released']=result['lease_released']
        checks[key+'_next_command_succeeds']=result['subsequent_execution_success'] and result['subsequent_harness_task_id']=='after-abort'
    checks['ordinary_exception_closed_runtime']=exceptions[0]['aborted_runtime_closed']
    checks['BaseException_propagates']=exceptions[1]['escaped']=='ProbeAbort'
    checks['BaseException_cleanup_closed_runtime']=exceptions[1]['aborted_runtime_closed']
    checks['BaseException_did_not_fabricate_command_result']=exceptions[1]['first_result_success'] is None
    for group,results in resets.items():
        for key,value in results.items():
            if key!='session_id': checks[group+'_'+key]=value
    for result in failed_cleanup:
        prefix='failed_'+result['failed_component']+'_cleanup_'
        for key,value in result.items():
            if key not in {'failed_component','protected_operations'}: checks[prefix+key]=value
        for key,value in result['protected_operations'].items():
            checks[prefix+key+'_refuses_without_effects']=value['refused'] and value['no_effects'] and value['requires_fresh_instance'] and value['code']=='agent_busy'
    for key,value in cancelled_before_dispatch.items(): checks['cancelled_before_dispatch_'+key]=value
    for key,value in unchanged.items(): checks['original_body_unchanged_'+key]=value
    checks['source_hashes_unchanged']=all(digest((BASELINE if group=='baseline' else ROOT)/p)==value for group,entries in source_hashes.items() for p,value in entries.items())
    report={'schema_version':1,'kind':'pure_shared_agent_episode_guard_before_after',
        'script_sha256':digest(Path(__file__)),'source_hashes':source_hashes,
        'method':'Unmodified AST method bodies and real HarnessRuntime; no full Agent constructor or service/server import. Only endpoint decorators and external I/O/provider collaborators are replaced.',
        'baseline':baseline,'candidate':candidate,'cancellation':cancellation,'finalization':finalization,
        'exceptions':exceptions,'failed_cleanup':failed_cleanup,'reset':resets,
        'cancelled_before_dispatch':cancelled_before_dispatch,
        'original_bodies_ast_equal_except_name_docstring':unchanged,'checks':checks,
        'check_count':len(checks),'failed_checks':[key for key,value in checks.items() if value is not True],
        'model_calls':0,'simulator_calls':0,'network_calls':0,
        'limitations':['Memory clearing does not restore the simulator, world, RNG, score, or already-dispatched remote actions.',
            'No transport or provider tested; ordinary failure/cancellation use controlled backend errors.',
            'BaseException cleanup closes the runtime and stops the recorder, but does not synthesize a successfully archived command result. Cleanup failure rejects future use of that instance.',
            'Same-instance gate is not a global shared-world or multi-process mutex; source and regression tests must cover other entry points.']}
    path=Path(__file__).with_name('episode-guard-before-after.json')
    if path.exists(): raise FileExistsError(path)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(path),'checks':len(checks),'failures':report['failed_checks'],'exceptions':exceptions},ensure_ascii=False,indent=2))
    if report['failed_checks']: raise SystemExit(1)

if __name__=='__main__': main()
