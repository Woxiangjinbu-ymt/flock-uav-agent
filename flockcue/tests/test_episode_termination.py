"""Pure report-level decisions; no provider, ledger open, filesystem or mutation."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
from episode_termination import classify_episode_termination, should_block_cost_on_request_stop


def worker(*, stop=None, complete=True, success=True, online=False):
    usage = {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}
    value = {
        'status': 'completed', 'trial_id': 'episode-1', 'drained': True,
        'termination_context': {'worker_exited': True, 'exit_seal_verified': True},
        'limits': {'closed': True, 'needs_process_termination': False, 'threads_still_alive': [],
            'stop_reason': stop, 'admitted_http_attempts': 1, 'delegated_http_attempts': 1,
            'unknown_usage_attempts': 0, 'unknown_usage_attempt_ids': [], 'known_total_tokens': 10,
            'attempts': [{'attempt_id': 1, 'delegated': True, 'delivered_to_sdk': True, 'usage': usage,
                'usage_status': 'known', 'request_body_sha256': 'a'*64, 'request_body_length': 100}]},
        'isolation': {key: True for key in ('archive_unchanged_after_drain', 'world_unchanged_after_drain',
            'late_action_rejected', 'canary_absent', 'checker_definitions_absent')},
        'agent': {'success': success, 'error_type': None, 'harness_closed': True,
            'archive_info': {'closed': True, 'complete': complete, 'path': '/sealed/policy/modelio/model.jsonl'},
            'archive_verify': {'valid': True, 'complete': complete}},
        'snapshot': {'path': 'private/score-world.pkl', 'sha256': 'b'*64, 'state_sha256': 'c'*64, 'score_seconds': 10.0},
        'files': {'private/score-world.pkl': 'b'*64, 'policy/modelio/model.jsonl': 'd'*64},
        'network_attempts': None if online else [],
        'lifecycle': {'cancelled': False}, 'runtime': {'task_seconds': 500},
        'elapsed': 10.1, 'clock': 'time.monotonic', 'timer_enabled': True,
    }
    if online: value['online_arm'] = {'arm': 'integrated', 'model': 'example'}
    return value


def cost(w, *, finished=True):
    trial = w['trial_id']
    request = w['limits']['attempts'][0]
    return {'blocked': False, 'stop_reason': None, 'currency': 'USD', 'reserved': '0', 'locked_reservation': '0',
        'active_trial': None if finished else trial, 'batch_budget': '1.00', 'settled_conservative_charge': '0.01',
        'unallocated_budget': '0.99',
        'trials': {trial: {'status': 'finished' if finished else 'active', 'budget': '0.50',
            'attempt_ids': [trial+':1'], 'settled_conservative_charge': '0.01'}},
        'attempts': {trial+':1': {'reservation_id': trial+':1', 'trial_id': trial, 'status': 'settled',
            'usage': deepcopy(request['usage']), 'request_body_sha256': request['request_body_sha256'],
            'request_body_length': request['request_body_length'], 'settled_conservative_charge': '0.01'}}}


@pytest.mark.parametrize('online', [False, True])
def test_normal_return_candidates_are_not_success_or_teacher_approval(online):
    w = worker(online=online)
    result = classify_episode_termination(w, cost(w) if online else None)
    assert result['category'] == 'normal_return' and result['batch_action'] == 'continue'
    assert result['score_eligible'] and result['archive_training_eligible']
    assert result['task_success'] == 'not_evaluated'
    assert 'not_teacher_approval' in result['archive_training_eligibility_scope']
    assert 'remaining_pre_authorized_manifest_slots_only' in result['continuation_scope']


@pytest.mark.parametrize('reason', ['token_limit', 'http_attempt_limit', 'task_deadline'])
@pytest.mark.parametrize('complete', [True, False])
@pytest.mark.parametrize('online', [True, False])
def test_known_settled_budget_ending_keeps_score_and_remaining_manifest(reason, complete, online):
    w = worker(stop=reason, complete=complete, success=False, online=online)
    result = classify_episode_termination(w, cost(w) if online else None)
    assert result['category'] == 'budget_exhausted' and result['reason'] == reason
    assert result['score_eligible'] and result['batch_action'] == 'continue'
    assert not result['archive_training_eligible']


@pytest.mark.parametrize('finished', [True, False])
def test_active_fully_settled_trial_can_be_classified_before_finish(finished):
    w = worker(stop='token_limit', complete=False, success=False, online=True)
    assert classify_episode_termination(w, cost(w, finished=finished))['batch_action'] == 'continue'


@pytest.mark.parametrize('reason', ['token_limit', 'http_attempt_limit', 'task_deadline', None])
@pytest.mark.parametrize('blocked_reason', ['token_limit', 'request_timeout', 'unknown_usage', 'unfinished_trial_after_reopen'])
def test_existing_block_never_unlocked_by_normal_stop(reason, blocked_reason):
    w = worker(stop=reason, online=True)
    c = cost(w); c.update(blocked=True, stop_reason=blocked_reason)
    before = deepcopy(c)
    result = classify_episode_termination(w, c)
    assert result['score_eligible'] and result['batch_action'] == 'stop'
    assert result['reason'] == 'cost_ledger_blocked' and result['cost_stop_reason'] == blocked_reason
    assert not result['archive_training_eligible'] and c == before


@pytest.mark.parametrize('field', ['reserved', 'locked_reservation'])
def test_unsettled_money_blocks_even_false_block_flag(field):
    w = worker(stop='token_limit', online=True)
    c = cost(w); c[field] = '0.3244032'
    assert classify_episode_termination(w, c)['reason'] == 'cost_unsettled_or_locked'


@pytest.mark.parametrize('kind', ['missing_usage', 'invalid_sum', 'unknown_counter', 'concealed_unknown', 'counter_mismatch'])
def test_unknown_or_invalid_usage_never_continue(kind):
    w = worker(stop='token_limit', complete=False, success=False)
    attempt = w['limits']['attempts'][0]
    if kind == 'missing_usage': attempt['usage'] = None
    elif kind == 'invalid_sum': attempt['usage']['total_tokens'] = 11
    elif kind == 'unknown_counter':
        attempt.update(usage=None, usage_status='unknown')
        w['limits'].update(unknown_usage_attempts=1, unknown_usage_attempt_ids=[1], known_total_tokens=0)
    elif kind == 'concealed_unknown': attempt.update(usage=None, usage_status='unknown')
    else: w['limits']['known_total_tokens'] = 9
    result = classify_episode_termination(w)
    assert result['score_eligible'] is (kind not in {'concealed_unknown', 'counter_mismatch'})
    assert result['batch_action'] == 'stop' and not result['archive_training_eligible']


@pytest.mark.parametrize('damage', ['no_context', 'unjoined', 'unverified_seal', 'incomplete', 'not_drained',
    'live_thread', 'needs_termination', 'controller_open', 'archive_invalid', 'archive_open', 'harness_open',
    'snapshot_hash', 'missing_snapshot', 'missing_archive_inventory', 'isolation_false', 'isolation_missing'])
def test_scoring_barriers_stop_all_actions(damage):
    w = worker()
    if damage == 'no_context': w.pop('termination_context')
    elif damage == 'unjoined': w['termination_context']['worker_exited'] = False
    elif damage == 'unverified_seal': w['termination_context']['exit_seal_verified'] = False
    elif damage == 'incomplete': w['status'] = 'incomplete'
    elif damage == 'not_drained': w['drained'] = False
    elif damage == 'live_thread': w['limits']['threads_still_alive'] = [1]
    elif damage == 'needs_termination': w['limits']['needs_process_termination'] = True
    elif damage == 'controller_open': w['limits']['closed'] = False
    elif damage == 'archive_invalid': w['agent']['archive_verify']['valid'] = False
    elif damage == 'archive_open': w['agent']['archive_info']['closed'] = False
    elif damage == 'harness_open': w['agent']['harness_closed'] = False
    elif damage == 'snapshot_hash': w['files']['private/score-world.pkl'] = 'f'*64
    elif damage == 'missing_snapshot': w.pop('snapshot')
    elif damage == 'missing_archive_inventory': w['files'].pop('policy/modelio/model.jsonl')
    elif damage == 'isolation_false': w['isolation']['canary_absent'] = False
    elif damage == 'isolation_missing': w['isolation'].pop('world_unchanged_after_drain')
    result = classify_episode_termination(w)
    assert not result['score_eligible'] and result['batch_action'] == 'stop'
    assert not result['archive_training_eligible']


@pytest.mark.parametrize('online,network', [(True, None), (True, []), (False, None), (False, ['attempt'])])
def test_online_or_unproven_local_context_requires_costs(online, network):
    w = worker(online=online); w['network_attempts'] = network
    result = classify_episode_termination(w)
    assert result['score_eligible'] and result['reason'] == 'missing_cost_snapshot' and result['batch_action'] == 'stop'


@pytest.mark.parametrize('reason', ['cancelled', 'http_error', 'request_timeout', 'transport_error', 'unsupported_async', 'unknown_stop'])
def test_cancellation_and_errors_stop_batch_but_allow_sealed_scoring(reason):
    w = worker(stop=reason, complete=False, success=False)
    result = classify_episode_termination(w)
    assert result['score_eligible'] and result['batch_action'] == 'stop' and not result['archive_training_eligible']
    assert result['category'] == ('cancelled' if reason == 'cancelled' else 'model_or_api_error')


def test_cancellation_flag_overrides_budget_reason():
    w = worker(stop='token_limit', complete=False, success=False)
    w['lifecycle']['cancelled'] = True
    assert classify_episode_termination(w)['category'] == 'cancelled'


def test_public_world_deadline_without_model_admission_stop():
    w = worker(complete=False, success=False)
    w.update(elapsed=500.1)
    w['snapshot']['score_seconds'] = 500
    result = classify_episode_termination(w)
    assert result['category'] == 'budget_exhausted' and result['reason'] == 'task_deadline'
    assert result['score_eligible'] and result['batch_action'] == 'continue'


def test_error_or_partial_archive_without_normal_budget_stops():
    w = worker(success=False)
    assert classify_episode_termination(w)['reason'] == 'agent_execution_error'
    w = worker(complete=False)
    assert classify_episode_termination(w)['reason'] == 'partial_archive_without_normal_budget_stop'
    w = worker(stop='token_limit'); w['agent']['error_type'] = 'APIError'
    assert classify_episode_termination(w)['reason'] == 'budget_stop_with_unrelated_error'


@pytest.mark.parametrize('damage', ['trial_missing', 'wrong_active_trial', 'request_missing', 'request_hash',
    'usage_mismatch', 'settled_total', 'budget_exceeded', 'remaining_money', 'nan_money', 'truthy_block', 'locked_attempt'])
def test_inconsistent_cost_snapshots_fail_closed(damage):
    w = worker(stop='token_limit', online=True); c = cost(w)
    attempt = c['attempts']['episode-1:1']
    if damage == 'trial_missing': c['trials'] = {}
    elif damage == 'wrong_active_trial': c['active_trial'] = 'other'
    elif damage == 'request_missing': c['attempts'] = {}
    elif damage == 'request_hash': attempt['request_body_sha256'] = 'f'*64
    elif damage == 'usage_mismatch': attempt['usage'] = {'prompt_tokens': 6, 'completion_tokens': 4, 'total_tokens': 10}
    elif damage == 'settled_total': c['settled_conservative_charge'] = '0.02'
    elif damage == 'budget_exceeded': c['trials']['episode-1']['budget'] = '0.001'
    elif damage == 'remaining_money': c['unallocated_budget'] = '1.00'
    elif damage == 'nan_money': c['reserved'] = 'NaN'
    elif damage == 'truthy_block': c['blocked'] = 'false'
    elif damage == 'locked_attempt': attempt['status'] = 'locked'
    result = classify_episode_termination(w, c)
    assert result['score_eligible'] and result['batch_action'] == 'stop' and not result['archive_training_eligible']


@pytest.mark.parametrize('reason,expected', [('token_limit', False), ('http_attempt_limit', False),
    ('task_deadline', True), ('request_timeout', True), ('cancelled', True), ('unknown_usage', True),
    ('http_error', True), ('transport_error', True), ('token_limit ', True), (None, True), (True, True), ([], True)])
def test_immediate_cost_hook_only_exempts_two_known_caps(reason, expected):
    assert should_block_cost_on_request_stop(reason) is expected


def test_inputs_unchanged_and_plain_json_return():
    import json
    w = worker(stop='token_limit', online=True); c = cost(w)
    original = deepcopy((w, c))
    result = classify_episode_termination(w, c)
    assert (w,c) == original and json.loads(json.dumps(result)) == result


@pytest.mark.parametrize('value', [None, [], 3, '', True])
def test_invalid_worker_returns_explicit_stop(value):
    result = classify_episode_termination(value)
    assert result['category'] == 'infrastructure_error' and result['batch_action'] == 'stop' and not result['score_eligible']

@pytest.mark.parametrize('damage', ['attempt_not_dict', 'attempt_id_bool', 'stop_list', 'huge_time', 'bad_error_type'])
def test_malformed_reports_return_stop_without_throwing(damage):
    w = worker(stop='token_limit')
    if damage == 'attempt_not_dict': w['limits']['attempts'] = [None]
    elif damage == 'attempt_id_bool': w['limits']['attempts'][0]['attempt_id'] = True
    elif damage == 'stop_list': w['limits']['stop_reason'] = []
    elif damage == 'huge_time': w['snapshot']['score_seconds'] = 10**1000
    else: w['agent']['error_type'] = []
    result = classify_episode_termination(w)
    assert result['batch_action'] == 'stop' and not result['archive_training_eligible']
