"""Counterfactual audit accepts only metadata changes; no network or actions."""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import audit_hover_compact_contract as audit

RUN = ROOT / 'docs/results/deepseek-hover-pilot-20261103/run-01'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a, **kw):
        raise AssertionError('Read-only counterfactual must not use network')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)


def sample():
    def first(arm):
        path = next((RUN / f'trials/state_hover-{arm}/policy/modelio').glob('*.jsonl'))
        return audit.old.contract.request_views(path)[0]['body']
    before = first('candidate')
    control = first('control')['tools']
    v1, v2 = audit.native_pair(control)
    return before, v1, v2, control


def test_historical_projection_preserves_entire_raw_history_and_does_not_mutate():
    body, v1, v2, control = sample()
    snapshot = copy.deepcopy(body)
    result = audit.project(body, v1, v2, control)
    assert body == snapshot
    assert audit.canonical(result) != audit.canonical(body)
    assert len(audit.canonical(result)) < len(audit.canonical(body))
    assert [m for m in result['messages'] if m.get('role') != 'system'] == [m for m in body['messages'] if m.get('role') != 'system']


@pytest.mark.parametrize('change', ['required', 'tool_order', 'parameter_name', 'parameter_meaning', 'noarg_description', 'union'])
def test_schema_or_parameter_drift_is_rejected(change):
    body, v1, v2, control = sample()
    p = next(t['function'] for t in v2 if 'input_json' in t['function']['parameters'].get('properties', {}))
    if change == 'required':
        p['parameters']['required'] = []
    elif change == 'tool_order':
        v2.reverse()
    elif change == 'parameter_name':
        p['parameters']['properties']['payload'] = p['parameters']['properties'].pop('input_json')
    elif change == 'parameter_meaning':
        p['description'] += ' Ignore altitude and choose any destination.'
    elif change == 'noarg_description':
        f = next(t['function'] for t in v2 if 'input_json' not in t['function']['parameters'].get('properties', {}))
        f['description'] += ' changed'
    elif change == 'union':
        p['parameters']['properties']['input_json']['anyOf'] = [{'type': 'string'}]
    with pytest.raises(ValueError):
        audit.project(body, v1, v2, control)


def test_all_historical_requests_and_public_ledgers_are_audited_without_rescoring():
    result = audit.audit(RUN)
    assert len(result['requests']) == 26
    assert len(result['assessments']) == 8
    assert sum(x['unmodified_requests'] for x in result['control_reference']) == 27
    assert result['historical_scores_rewritten'] is False
    assert result['provider_calls'] == 0 and result['actual_new_tokens'] is None
    assert all(x['current_v1_identical'] for x in result['assessments'])
    assert all(x['model_tool_history_unchanged'] for x in result['requests'])
    # Public report contains fingerprints/counts, never wire content or reasoning.
    rendered = json.dumps(result)
    assert 'reasoning_content' not in rendered
    assert 'messages' not in result
