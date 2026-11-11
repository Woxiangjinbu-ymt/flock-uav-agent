"""Synthetic tariffs and MockTransport only; no real provider or credentials."""
from dataclasses import replace
from decimal import Decimal, localcontext
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sys
import threading
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness_live_cost as live

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)

@pytest.fixture(params=['httpx', 'httpx2'])
def httpx(request):
    return importlib.import_module(request.param)

@pytest.fixture
def policy():
    return live.LiveCostPolicy('USD', '2.00', '4.00', 1000, 100, 1000)

def ledger(tmp_path, policy, *, batch='0.1', trial='0.01'):
    book = live.LiveCostLedger.create(tmp_path / 'batch.jsonl', policy, batch)
    book.start_trial('t1', trial)
    return book

def request(httpx, **changes):
    body = {'model': 'synthetic', 'max_tokens': 100, 'stream': False}
    body.update(changes)
    return httpx.Request('POST', 'https://offline.invalid/v1/chat/completions', json=body)

def payload(**changes):
    value = {'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110},
             'choices': [{'message': {'role': 'assistant', 'content': 'synthetic'}}]}
    value.update(changes)
    return value

def transport(httpx, book, handler):
    return live.make_cost_transport(httpx, httpx.MockTransport(handler), book, 't1')

def test_reserve_fsynced_before_delegate_then_settle_release(tmp_path, policy, httpx):
    book, calls = ledger(tmp_path, policy), []
    req = request(httpx)
    def respond(sent):
        calls.append(sent)
        events = [json.loads(line) for line in book.path.read_text().splitlines()]
        head = json.loads(book._head_path.read_text())
        assert events[-1]['event']['kind'] == 'reserved'
        assert events[-1]['event']['request_body_sha256'] == hashlib.sha256(req.content).hexdigest()
        assert head['sha256'] == events[-1]['sha256']
        assert Decimal(book.snapshot()['reserved']) == Decimal('0.0024')
        return httpx.Response(200, json=payload(), request=sent)
    assert transport(httpx, book, respond).handle_request(req).json()['usage']['total_tokens'] == 110
    state = book.snapshot()
    assert calls == [req] and Decimal(state['settled_conservative_charge']) == Decimal('0.00024')
    assert Decimal(state['reserved']) == 0 and state['blocked'] is False
    assert state['attempts']['t1:1']['attempt_id'] == 1
    book.finish_trial('t1'); book.close()
    with live.LiveCostLedger.open(book.path, policy, '0.1') as reopened:
        assert reopened.snapshot()['trials']['t1']['status'] == 'finished'

@pytest.mark.parametrize('changes', [
    {'stream': True}, {'max_tokens': True}, {'max_tokens': 0}, {'max_tokens': 101},
    {'max_tokens': None}, {'max_completion_tokens': 99}, {'n': 2}, {'n': True},
    {'messages': [{'content': '🛰' * 400}]},
])
def test_bad_request_zero_delegate_zero_reservation(tmp_path, policy, httpx, changes):
    book, calls = ledger(tmp_path, policy), []
    with pytest.raises(live.LiveCostError, match='invalid_request'):
        transport(httpx, book, lambda req: calls.append(req)).handle_request(request(httpx, **changes))
    assert calls == [] and book.snapshot()['attempts'] == {}
    assert Decimal(book.snapshot()['reserved']) == 0

@pytest.mark.parametrize('batch,trial,reason', [('0', '1', 'batch_budget_insufficient'),
    ('1', '0', 'trial_budget_insufficient'), ('0.002399', '1', 'batch_budget_insufficient')])
def test_insufficient_money_persisted_zero_dispatch(tmp_path, policy, httpx, batch, trial, reason):
    book, calls = ledger(tmp_path, policy, batch=batch, trial=trial), []
    with pytest.raises(live.LiveCostError, match=reason):
        transport(httpx, book, lambda req: calls.append(req)).handle_request(request(httpx))
    state = book.snapshot()
    assert calls == [] and state['blocked'] and state['stop_reason'] == reason
    assert state['attempts'] == {} and book._blocked_path.exists()
    book.close()
    reopened = live.LiveCostLedger.open(book.path, policy, batch)
    with pytest.raises(live.LiveCostError, match='batch_blocked'):
        reopened.start_trial('next', '1')

@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 4},
    {'prompt_tokens': True, 'completion_tokens': 2, 'total_tokens': 3},
    {'prompt_tokens': 1001, 'completion_tokens': 0, 'total_tokens': 1001},
    {'prompt_tokens': 1, 'completion_tokens': 101, 'total_tokens': 102},
    {'prompt_tokens': -1, 'completion_tokens': 1, 'total_tokens': 0}])
def test_unknown_usage_locks_full_reservation(tmp_path, policy, httpx, usage):
    book, calls = ledger(tmp_path, policy), []
    def respond(req):
        calls.append(req)
        return httpx.Response(200, json=payload(usage=usage), request=req)
    bounded = transport(httpx, book, respond)
    with pytest.raises(live.LiveCostError, match='unknown_or_invalid_usage'):
        bounded.handle_request(request(httpx))
    state = book.snapshot()
    assert len(calls) == 1 and state['blocked'] and state['attempts']['t1:1']['status'] == 'locked'
    assert Decimal(state['locked_reservation']) == Decimal('0.0024')
    assert Decimal(state['settled_conservative_charge']) == 0
    with pytest.raises(live.LiveCostError, match='batch_blocked'):
        bounded.handle_request(request(httpx))
    assert len(calls) == 1

@pytest.mark.parametrize('kind', ['timeout', 'oserror', 'http_error', 'stream', 'malformed'])
def test_transport_and_http_errors_lock_cost(tmp_path, policy, httpx, kind):
    book = ledger(tmp_path, policy)
    def respond(req):
        if kind == 'timeout': raise httpx.ReadTimeout('synthetic', request=req)
        if kind == 'oserror': raise OSError('synthetic')
        if kind == 'stream': return httpx.Response(200, content=b'data: synthetic\n\n', headers={'content-type': 'text/event-stream'}, request=req)
        if kind == 'malformed': return httpx.Response(200, content=b'{', request=req)
        return httpx.Response(500, json={'error': 'synthetic'}, request=req)
    with pytest.raises((live.LiveCostError, OSError, httpx.ReadTimeout)):
        transport(httpx, book, respond).handle_request(request(httpx))
    state = book.snapshot()
    assert state['blocked'] and Decimal(state['locked_reservation']) == Decimal('0.0024')
    book.close()
    assert live.LiveCostLedger.open(book.path, policy, '0.1').snapshot()['blocked']

def test_batch_budget_across_fresh_instances(tmp_path, policy, httpx):
    book = ledger(tmp_path, policy, batch='0.0025')
    transport(httpx, book, lambda req: httpx.Response(200, json=payload(), request=req)).handle_request(request(httpx))
    book.finish_trial('t1'); book.close()
    other = live.LiveCostLedger.open(book.path, policy, '0.0025')
    other.start_trial('t2', '0.01')
    calls = []
    bounded = live.make_cost_transport(httpx, httpx.MockTransport(lambda req: calls.append(req)), other, 't2')
    with pytest.raises(live.LiveCostError, match='batch_budget_insufficient'):
        bounded.handle_request(request(httpx))
    assert calls == [] and Decimal(other.snapshot()['settled_conservative_charge']) == Decimal('0.00024')

def test_trial_budget_cumulative(tmp_path, policy, httpx):
    book, calls = ledger(tmp_path, policy, trial='0.0025'), []
    def respond(req):
        calls.append(req)
        return httpx.Response(200, json=payload(), request=req)
    bounded = transport(httpx, book, respond)
    bounded.handle_request(request(httpx))
    with pytest.raises(live.LiveCostError, match='trial_budget_insufficient'):
        bounded.handle_request(request(httpx))
    assert len(calls) == 1

def test_duplicate_settle_finish_trial_no_mutation(tmp_path, policy, httpx):
    book = ledger(tmp_path, policy)
    reservation = book.reserve('t1', request(httpx))
    body = json.dumps(payload()).encode()
    book.settle(reservation['reservation_id'], body)
    before = book.path.read_bytes()
    with pytest.raises(live.LiveCostError, match='already_settled'): book.settle(reservation['reservation_id'], body)
    assert book.path.read_bytes() == before
    book.finish_trial('t1')
    before = book.path.read_bytes()
    with pytest.raises(live.LiveCostError, match='already_finished'): book.finish_trial('t1')
    with pytest.raises(live.LiveCostError, match='duplicate'): book.start_trial('t1', '0.01')
    assert book.path.read_bytes() == before

def test_reopen_unknown_reservation_never_releases(tmp_path, policy, httpx):
    book = ledger(tmp_path, policy)
    book.reserve('t1', request(httpx))
    reopened = live.LiveCostLedger.open(book.path, policy, '0.1')
    assert reopened.snapshot()['blocked'] and Decimal(reopened.snapshot()['locked_reservation']) == Decimal('0.0024')
    with pytest.raises(live.LiveCostError, match='batch_blocked'): book.settle('t1:1', json.dumps(payload()).encode())

def test_outer_timeout_blocks_late_success(tmp_path, policy, httpx):
    book = ledger(tmp_path, policy)
    entered, release, errors = threading.Event(), threading.Event(), []
    def respond(req):
        entered.set()
        assert release.wait(2)
        return httpx.Response(200, json=payload(), request=req)
    def call():
        try: transport(httpx, book, respond).handle_request(request(httpx))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=call)
    thread.start()
    assert entered.wait(2)
    book.block('request_timeout')
    release.set(); thread.join(2)
    assert not thread.is_alive() and len(errors) == 1
    state = book.snapshot()
    assert state['stop_reason'] == 'request_timeout' and Decimal(state['locked_reservation']) == Decimal('0.0024')
    assert Decimal(state['settled_conservative_charge']) == 0

@pytest.mark.parametrize('damage', ['edit', 'truncate_line', 'truncate_event', 'append', 'head', 'policy', 'budget'])
def test_corruption_or_config_change_blocks_reopen(tmp_path, policy, damage):
    book = ledger(tmp_path, policy)
    book.finish_trial('t1'); book.close()
    data = book.path.read_bytes()
    if damage == 'edit': book.path.write_bytes(data.replace(b'trial_finished', b'trial_changed'))
    elif damage == 'truncate_line': book.path.write_bytes(data[:-1])
    elif damage == 'truncate_event': book.path.write_bytes(b'\n'.join(data.splitlines()[:-1]) + b'\n')
    elif damage == 'append':
        with book.path.open('ab') as stream: stream.write(b'{}\n')
    elif damage == 'head': book._head_path.write_text('{}\n')
    selected = replace(policy, currency='CNY') if damage == 'policy' else policy
    with pytest.raises(live.LiveCostError):
        live.LiveCostLedger.open(book.path, selected, '0.2' if damage == 'budget' else '0.1')
    assert book._blocked_path.exists()

def test_fsync_error_zero_delegate(tmp_path, policy, httpx, monkeypatch):
    book, calls = ledger(tmp_path, policy), []
    def broken(_): raise OSError('synthetic disk failure')
    monkeypatch.setattr(live.os, 'fsync', broken)
    with pytest.raises(OSError): transport(httpx, book, lambda req: calls.append(req)).handle_request(request(httpx))
    assert calls == []

@pytest.mark.parametrize('field,value', [('currency', 'EUR'), ('input_per_million', '0'),
    ('input_per_million', float('nan')), ('output_per_million', 'Infinity'), ('output_per_million', '-1'),
    ('max_input_tokens', True), ('max_output_tokens', 1001), ('max_request_bytes', 0)])
def test_invalid_policy_rejected(policy, field, value):
    with pytest.raises(ValueError): replace(policy, **{field: value})

def test_decimal_context_cannot_round_cost(policy):
    with localcontext() as context:
        context.prec = 2
        assert policy.request_reservation == Decimal('0.0024')
        assert policy.cost(100, 10) == Decimal('0.00024')

def test_durable_marker_without_block_event_still_locks_pending(tmp_path, policy, httpx):
    book = ledger(tmp_path, policy)
    book.reserve('t1', request(httpx))
    book._marker('synthetic_interruption_between_marker_and_event')
    state = book.snapshot()
    assert state['blocked'] and state['attempts']['t1:1']['status'] == 'locked'
    assert Decimal(state['locked_reservation']) == Decimal('0.0024')
    with pytest.raises(live.LiveCostError, match='batch_blocked'):
        book.assert_dispatchable('t1:1')

@pytest.mark.parametrize('status', [None, '200', True, 0])
def test_invalid_status_type_persistently_blocks(tmp_path, policy, httpx, status):
    book = ledger(tmp_path, policy)
    book.reserve('t1', request(httpx))
    with pytest.raises(live.LiveCostError, match='http_error_unknown_cost'):
        book.settle('t1:1', json.dumps(payload()).encode(), status_code=status)
    assert book.snapshot()['blocked']

def test_block_during_reservation_fsync_prevents_delegate(tmp_path, policy, httpx, monkeypatch):
    book, calls = ledger(tmp_path, policy), []
    reserve = book.reserve
    def reserve_then_block(*args):
        result = reserve(*args)
        book.block('cancelled')
        return result
    monkeypatch.setattr(book, 'reserve', reserve_then_block)
    with pytest.raises(live.LiveCostError, match='batch_blocked'):
        transport(httpx, book, lambda req: calls.append(req)).handle_request(request(httpx))
    assert calls == [] and Decimal(book.snapshot()['locked_reservation']) == Decimal('0.0024')
