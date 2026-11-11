"""Freeze gates and accounting preservation for the bounded eight-slot pilot."""
import ast
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import deepseek_hover_pilot as pilot
import deepseek_cost_pilot as previous
from harness_deepseek_adapter import httpx


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError('Freeze tests cannot dispatch network')
    for name in ('connect', 'connect_ex'):
        monkeypatch.setattr(socket.socket, name, reject)
    monkeypatch.setattr(socket, 'create_connection', reject)
    monkeypatch.setattr(socket, 'getaddrinfo', reject)


@pytest.fixture
def frozen(tmp_path):
    run = tmp_path / 'frozen'
    return run, pilot.prepare(run, local_smoke=True)


def reseal(run, manifest):
    pilot.write(run / 'manifest.json', manifest)
    (run / 'manifest.sha256').write_text(pilot.sha((run / 'manifest.json').read_bytes()) + '\n')


def test_fixed_eight_order_and_new_bounded_authorization():
    trials = pilot.trials()
    assert [(t['condition'], t['arm']) for t in trials] == [
        ('state_hover','control'), ('state_hover','candidate'),
        ('explicit_hover','candidate'), ('explicit_hover','control'),
        ('unknown_takeoff','control'), ('unknown_takeoff','candidate'),
        ('duration_hover','candidate'), ('duration_hover','control')]
    assert all(t['task_id']=='c74d7020' and t['input_layout']=='compact_tools_v1'
               and t['harness_revision']=='search_confirm_v9' for t in trials)
    assert pilot.RUNTIME == previous.RUNTIME
    auth = pilot.authorization(False)
    assert auth['episodes']==8 and auth['max_http_attempts']==96
    assert auth['batch_cost_limit_usd']=='1.50' and auth['trial_cost_limit_usd']=='0.50'
    assert pilot.authorization(True)['episodes']==0
    assert pilot.authorization(True)['batch_cost_limit_usd']=='0'


def test_prepare_binds_unchanged_initial_state_and_public_derivatives_without_credentials(frozen,monkeypatch):
    run,m=frozen
    monkeypatch.setattr(pilot,'credential',lambda *_:pytest.fail('credential read in prepare/verify'))
    assert pilot.load_manifest(run)==m
    base=json.loads((run/'inputs/fixtures/c74d7020.json').read_text())
    for variant in ('explicit_hover','duration_hover'):
        prefix=run/'inputs'/pilot.fixture_prefix(variant)/'fixtures'
        value=json.loads((prefix/'c74d7020.json').read_text())
        assert value==pilot.variant_fixture(base,variant)
        assert (prefix/'c74d7020.pkl').read_bytes()==(run/'inputs/fixtures/c74d7020.pkl').read_bytes()
        assert value['original_task']==base['original_task']
    assert not (run/'process-ledger.jsonl').exists()
    assert not (run/'cost-ledger.jsonl').exists()


@pytest.mark.parametrize('damage',[
    lambda m:m.update(scope=previous.SCOPE),
    lambda m:m.update(schema_version=2),
    lambda m:m.pop('online_variant_contract'),
    lambda m:m.update(online_variant_contract=previous.VARIANT_CONTRACT),
    lambda m:m.update(model='other'),
    lambda m:m.update(local_smoke=1),
    lambda m:m['trials'].reverse(),
    lambda m:m['trials'].pop(),
    lambda m:m['trials'].append(copy.deepcopy(m['trials'][0])),
    lambda m:m['trials'][0].update(completion_contract='hover_completion_v1'),
    lambda m:m['trials'][1].update(tool_input_contract='string_v1'),
    lambda m:m['trials'][0].update(input_layout='full_v1'),
    lambda m:m['trials'][1].update(harness_revision='observed_target_v7'),
    lambda m:m['trials'][2].update(fixture_variant='original'),
    lambda m:m['trials'][4]['api_fault'].update(occurrence=2),
    lambda m:m['trials'][0].update(api_fault=copy.deepcopy(m['trials'][4]['api_fault'])),
    lambda m:m['online_pilot'].update(batch_budget='2.00'),
    lambda m:m['online_pilot'].update(sdk_retries=1),
    lambda m:m['online_common_runtime']['request_limits'].update(max_http_attempts=13),
    lambda m:m['online_implementation_budget'].update(repair_budget=17),
    lambda m:m['authorization'].update(episodes=10),
    lambda m:m['authorization'].update(old_6_14_360_queues_resumed=True),
])
def test_rehashed_manifest_cannot_expand_or_mix_scope(frozen,damage):
    run,m=frozen;damage(m);reseal(run,m)
    with pytest.raises(ValueError):pilot.load_manifest(run)


@pytest.mark.parametrize('variant', ['original','explicit_hover','duration_hover'])
@pytest.mark.parametrize('kind',['command','snapshot'])
def test_rehashed_inputs_cannot_change_origin_or_derivative(frozen,variant,kind):
    run,m=frozen;prefix='inputs/'+pilot.fixture_prefix(variant)+'fixtures/'
    p=run/(prefix+'c74d7020.'+('json' if kind=='command' else 'pkl'))
    if kind=='command':
        value=json.loads(p.read_text());value['command']+=' Extra requirement.'
        value['command_sha256']=pilot.sha(value['command'].encode());pilot.write(p,value)
    else:p.write_bytes(p.read_bytes()+b'changed')
    m['inputs'][str(p.relative_to(run/'inputs'))]=pilot.sha(p.read_bytes());reseal(run,m)
    with pytest.raises(ValueError):pilot.load_manifest(run)


def test_no_overwrite_and_unfrozen_inventory_rejected(frozen):
    run,_=frozen
    with pytest.raises(FileExistsError):pilot.prepare(run,local_smoke=True)
    (run/'inputs/extra.json').write_text('{}')
    with pytest.raises(ValueError,match='inventory'):pilot.load_manifest(run)


def test_accounting_and_worker_close_functions_preserve_previous_contract():
    assert pilot.cost_policy()==previous.cost_policy()
    for name in ('finish_worker_cost','read_cost_snapshot'):
        values=[]
        for module in (pilot,previous):
            tree=ast.parse(Path(module.__file__).read_text())
            values.append(ast.dump(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)))
        assert values[0]==values[1]


@pytest.mark.parametrize('condition', ['state_hover','explicit_hover','unknown_takeoff','duration_hover'])
def test_smoke_decisions_equivalent_but_candidate_objects_are_not_reencoded(condition):
    sequences=[]
    for t in [x for x in pilot.trials() if x['condition']==condition]:
        transport=pilot.mock_transport(httpx,t)
        names=['list_drones','take_off','hover','get_drone_status','run_harness']
        body={'tools':[{'function':{'name':n}} for n in names], 'messages':[{'role':'tool','content':json.dumps([{'id':'actor','name':'Drone 4'}])}]}
        sequence=[]
        for _ in range(6):
            message=transport.handle_request(httpx.Request('POST','https://offline.invalid',json=body)).json()['choices'][0]['message']
            if 'tool_calls' not in message:break
            call=message['tool_calls'][0]['function'];args=json.loads(call['arguments'])
            if 'input_json' in args:
                assert isinstance(args['input_json'],str if t['arm']=='control' else dict)
                if isinstance(args['input_json'],str):args['input_json']=json.loads(args['input_json'])
            sequence.append((call['name'],args))
        transport.close();sequences.append(sequence)
    assert sequences[0]==sequences[1]
    assert sequences[0][-1][0]=='get_drone_status'


def test_readonly_module_does_not_import_simulator_or_pickle():
    code="import sys;sys.path.insert(0,'scripts');import deepseek_hover_pilot;assert not {'pickle','uav_agent','harness_natural_runner','harness_full100_world'} & set(sys.modules)"
    subprocess.run([sys.executable,'-c',code],cwd=ROOT,check=True,capture_output=True)
