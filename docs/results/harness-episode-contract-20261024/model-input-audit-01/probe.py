"""Pure logging-contract probes; no provider, SDK transport, world or model run."""
import ast
import hashlib
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(BASE / 'source/agent4drone'))

def forbidden(*args, **kwargs):
    raise AssertionError('Network forbidden in read-only logger contract audit')

socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden

from model_request_audit import request_record
from toolchain_recorder import tool_chain_from_steps
from harness_core import HarnessRuntime
import httpx

# Execute existing extraction helpers and audit hooks only, without constructing
# an agent, loading credentials, configuring logging or importing any provider.
module = ast.parse((BASE / 'source/agent4drone/uav_agent.py').read_text())
original = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'UAVControlAgent')
names = {'_normalize_tool_input', '_message_text', '_extract_intermediate_steps',
         '_audit_model_request', '_audit_model_response'}
methods = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(methods) == 5
stub = ast.ClassDef(name='ExtractOnlyAgent', bases=[], keywords=[], body=methods, decorator_list=[])
tree = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), stub], type_ignores=[])
namespace = {'json': json, 'SimpleNamespace': SimpleNamespace}
exec(compile(ast.fix_missing_locations(tree), '<frozen-extract-only>', 'exec'), namespace)
extractor = namespace['ExtractOnlyAgent']()

payload = {'model': 'synthetic-public-fixture', 'temperature': 0.1, 'stream': False,
           'messages': [{'role': 'system', 'content': 'Public UAV fixture rules.'},
                        {'role': 'user', 'content': 'Read drone u1 status.'}],
           'tools': [{'type': 'function', 'function': {'name': 'get_drone_status',
                     'description': 'Public state only', 'parameters': {'type': 'object',
                     'properties': {'drone_id': {'type': 'string'}}, 'required': ['drone_id']}}}]}
body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
request = SimpleNamespace(content=body, method='POST', url=SimpleNamespace(path='/v1/chat/completions'), extensions={})
record = request_record(request)
assert record['body_sha256'] == hashlib.sha256(body).hexdigest()
assert record['tools_sha256'] and len(record['message_sha256']) == 2
assert not any(k in record for k in ('body', 'messages', 'tools', 'temperature', 'headers', 'authorization'))
assert 'Public UAV fixture rules.' not in json.dumps(record)

def history(call_id):
    return [SimpleNamespace(content='Observe first.', tool_calls=[{'name': 'get_drone_status',
        'id': call_id, 'args': {'drone_id': 'u1'}}]),
        SimpleNamespace(content='{"id":"u1","status":"hovering"}', tool_call_id=call_id),
        SimpleNamespace(content='Observed. [TASK DONE]')]

export_a = tool_chain_from_steps(extractor._extract_intermediate_steps(history('public-call-A')))
export_b = tool_chain_from_steps(extractor._extract_intermediate_steps(history('public-call-B')))
assert export_a == export_b
assert all('tool_call_id' not in item and 'message_log' not in item for item in export_a)
assert 'Observed. [TASK DONE]' not in json.dumps(export_a)

def runtime(task_id='synthetic-command'):
    return HarnessRuntime(task_id=task_id, command='Read status', scene_id='public-scene',
        scene_epoch=0, ledger=None, invoke=forbidden, skill_factory=forbidden,
        scope=lambda: ('public-scene', 0), started=0, clock=lambda: 0)

single = runtime()
serial_id = single.model_start(0)
single_record = dict(record)
single.wire_start(SimpleNamespace(extensions={}), single_record)
assert single_record['model_request_id'] == serial_id

overlap = runtime()
a, b = overlap.model_start(0), overlap.model_start(1)
a_request, b_request = SimpleNamespace(extensions={}), SimpleNamespace(extensions={})
a_record, b_record = dict(record), dict(record)
overlap.wire_start(a_request, a_record)
overlap.model_end(a)
overlap.wire_start(b_request, b_record)
assert a_record['model_request_id'] == b and b_record['model_request_id'] is None

class HeadersOnlyResponse:
    status_code = 200
    request = a_request
    @property
    def content(self):
        raise AssertionError('Response hook should not consume response body')

overlap.wire_response(HeadersOnlyResponse())
assert overlap.log.events[-1]['kind'] == 'http_response_headers'
assert overlap.log.events[-1]['measurement'] == 'headers_arrived_not_full_body_completion'

def late_response_probe(*, reuse_id=False, closed=False):
    old = runtime('public-episode-A')
    current = runtime('public-episode-A' if reuse_id else 'public-episode-B')
    agent = namespace['ExtractOnlyAgent']()
    agent._model_wire_requests, agent._model_wire_errors, agent._harness = [], [], old
    old.model_start(0)
    old_request = httpx.Request('POST', 'https://synthetic.invalid/v1/chat/completions', json=payload)
    agent._audit_model_request(old_request)
    if closed:
        old.close('synthetic_ended')
    agent._harness = current
    current.model_start(0)
    current_request = httpx.Request('POST', 'https://synthetic.invalid/v1/chat/completions', json=payload)
    agent._audit_model_request(current_request)
    old_count, current_count = len(old.log.events), len(current.log.events)
    agent._audit_model_response(httpx.Response(200, json={'synthetic': True}, request=old_request))
    return {'task_ids_unique': not reuse_id, 'old_runtime_closed': closed,
            'old_runtime_new_events': len(old.log.events)-old_count,
            'current_runtime_new_events': len(current.log.events)-current_count,
            'old_headers_seen': old._wire[old_request.extensions['uav_harness_attempt']]['headers_seen'],
            'current_headers_seen': current._wire[current_request.extensions['uav_harness_attempt']]['headers_seen'],
            'errors': agent._model_wire_errors}

late_unique = late_response_probe()
late_closed = late_response_probe(closed=True)
late_reused = late_response_probe(reuse_id=True)
assert late_unique['old_runtime_new_events'] == late_unique['current_runtime_new_events'] == 0
assert not late_unique['old_headers_seen'] and not late_unique['current_headers_seen']
assert late_closed['old_runtime_new_events'] == late_closed['current_runtime_new_events'] == 0
assert late_reused['current_runtime_new_events'] == 1 and late_reused['current_headers_seen'] is True
assert late_reused['old_headers_seen'] is False

# Binding an old owner alone is insufficient to keep already closed snapshots
# immutable: the existing runtime response method still emits after close.
old = runtime('closed-owner')
old.model_start(0)
closed_request = httpx.Request('POST', 'https://synthetic.invalid/v1/chat/completions', json=payload)
old.wire_start(closed_request, dict(record))
old.close('synthetic_ended')
before_closed = len(old.log.events)
old.wire_response(httpx.Response(200, request=closed_request))
assert len(old.log.events) == before_closed + 1

output = {
    'scope': 'Synthetic public-only fixtures and frozen pure production methods; no model/provider/HTTP call, no simulation, no private checker or headers.',
    'wire_input': {'actual_fixture_body_sha256': hashlib.sha256(body).hexdigest(), 'record': record,
                   'body_matches_hash': True, 'body_or_messages_or_tools_not_archived': True},
    'tool_call_identity_loss': {'input_call_ids': ['public-call-A', 'public-call-B'],
        'exports_identical': export_a == export_b, 'persisted_tool_chain': export_a,
        'final_assistant_message_not_in_tool_chain': True},
    'logical_request_interleaving': {'serial_ok': True, 'logical_a': a, 'logical_b': b,
        'request_for_a_recorded_as': a_record['model_request_id'],
        'request_for_b_after_a_end_recorded_as': b_record['model_request_id'],
        'scope_limit': 'Deterministic overlapping model-call interleaving only. Does not claim historical serial campaigns had concurrency.'},
    'response_hook': {'response_body_accessed': False, 'event': overlap.log.events[-1]},
    'late_response_hook': {'unique_episode_ids': late_unique, 'unique_ids_old_closed': late_closed,
        'reused_ids_defensive_case': late_reused,
        'binding_old_owner_without_closed_guard_appends_after_task_end': True,
        'scope_limit': 'Real request/response hook methods with httpx objects only; no HTTP send. Normal execute UUIDs avoid the reused-ID collision case, but unique-ID late responses are silently lost.'},
    'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'all_expected_current_behavior_assertions_passed': True,
}
(BASE / 'probe-results.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'expected_current_behavior_assertions': 'passed', 'network_or_model_calls': 0,
                  'output': str(BASE / 'probe-results.json')}))
