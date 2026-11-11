"""Local counterfactual metadata/assessment audit; never dispatch model or tools.

Historical scores and archives are inputs only. Output contains hashes/counts,
not raw prompts or hidden reasoning. Bytes are not token or cost estimates.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import copy
import hashlib
import json
import re

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
sys.path.insert(0, str(ROOT / 'agent4drone'))
import analyze_deepseek_hover_pilot as old
from hover_completion import assess_hover_completion
from tool_input_contract import adapt_input_json_tools, tool_input_rules
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import create_model


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def native_pair(control):
    """Build actual SDK schemas from the frozen base tool declarations, no calls."""
    v1, v2 = [], []
    for item in control:
        function = item['function']
        parameters = function['parameters']
        properties = parameters.get('properties', {})
        if 'input_json' not in properties:
            v1.append(copy.deepcopy(item))
            v2.append(copy.deepcopy(item))
            continue
        require(properties == {'input_json': {'type': 'string'}}
                and parameters.get('required') == ['input_json'], 'Unexpected frozen base envelope')
        schema = create_model('HistoricalInput', input_json=(str, ...))
        tool = StructuredTool.from_function(func=lambda input_json: None,
            name=function['name'], description=function['description'], args_schema=schema)
        require(convert_to_openai_tool(tool) == item, 'SDK changed base schema outside representation')
        variants = [convert_to_openai_tool(adapt_input_json_tools([tool], f'json_object_or_string_v{v}')[0])
                    for v in (1, 2)]
        v1.append(variants[0])
        v2.append(variants[1])
    return v1, v2


def validate_metadata(control, before, after):
    """Check SDK output against the precise description-only v2 allowlist."""
    expected_before, expected_after = copy.deepcopy(control), copy.deepcopy(control)
    for previous, current in zip(expected_before, expected_after):
        f1, f2 = previous['function'], current['function']
        props1 = f1['parameters'].get('properties', {})
        props2 = f2['parameters'].get('properties', {})
        if 'input_json' not in props1:
            continue
        props1['input_json'] = copy.deepcopy(old.INPUT_PROPERTY)
        f1['description'] = re.sub('JSON string', 'JSON object (preferred) or legacy JSON-object string',
                                   f1['description'], flags=re.I) + old.INPUT_NOTE
        props2['input_json'] = {'anyOf': [{'type': 'string'}, {'additionalProperties': True, 'type': 'object'}]}
        f2['description'] = f2['description'].replace('Input should be a JSON string with:', 'Parameters:')
    require(before == expected_before, 'Historical v1 metadata differs from frozen base')
    require(after == expected_after, 'Tool names/order/parameters/meaning changed outside v2 prose allowlist')


def project(body, expected_old, new_tools, control):
    require(body['tools'] == expected_old, 'Historical candidate native schema differs from reconstructed v1')
    validate_metadata(control, expected_old, new_tools)
    result = copy.deepcopy(body)
    result['tools'] = copy.deepcopy(new_tools)
    # Input rules are shared once; all dynamic evidence and history stay exact.
    previous, current = tool_input_rules('json_object_or_string_v1'), tool_input_rules('json_object_or_string_v2')
    systems = [m for m in result['messages'] if m.get('role') == 'system']
    require(len(systems) == 1 and systems[0]['content'].count(previous) == 1, 'Unexpected shared input rules')
    systems[0]['content'] = systems[0]['content'].replace(previous, current, 1)
    restored = copy.deepcopy(result)
    restored['tools'] = copy.deepcopy(body['tools'])
    system = next(m for m in restored['messages'] if m.get('role') == 'system')
    system['content'] = system['content'].replace(current, previous, 1)
    require(restored == body, 'Change outside metadata/shared rules')
    require([m for m in result['messages'] if m.get('role') != 'system'] ==
            [m for m in body['messages'] if m.get('role') != 'system'], 'Model/tool history changed')
    return result


def audit(run):
    run = Path(run).resolve()
    manifest, _, verification = old.verify_run(run)
    frozen = old.frozen_contract(run)
    assessments, requests, references = [], [], []
    all_views = {}
    for trial in manifest['trials']:
        ident = trial['trial_id']
        folder = run / 'trials' / ident
        archives = list((folder / 'policy/modelio').glob('*.jsonl'))
        require(len(archives) == 1, 'Expected one sealed archive')
        views = old.contract.request_views(archives[0])
        all_views[ident] = views
        state = json.loads((folder / 'policy/task-state.json').read_text())
        command = state['command']
        before = frozen['assess_hover_completion'](command, state)
        unchanged = assess_hover_completion(command, state, contract='hover_completion_v1')
        after = assess_hover_completion(command, state, contract='hover_completion_v2')
        require(before == unchanged, 'Current v1 assessment differs from sealed v1')
        assessments.append({'trial_id': ident, 'frozen_v1_status': before['status'],
            'frozen_v1_task_completed': before['task_completed'], 'v2_status': after['status'],
            'v2_task_completed': after['task_completed'], 'v1_reasons': before['reasons'],
            'v2_reasons': after['reasons'], 'current_v1_identical': True,
            'ledger_sha256': sha((folder / 'policy/task-state.json').read_bytes())})
    for trial in manifest['trials']:
        ident = trial['trial_id']
        if trial['arm'] != 'candidate':
            references.append({'trial_id': ident, 'unmodified_requests': len(all_views[ident])})
            continue
        control = all_views[trial['condition'] + '-control'][0]['body']['tools']
        v1, v2 = native_pair(control)
        for view in all_views[ident]:
            before = view['body']
            after = project(before, v1, v2, control)
            requests.append({'trial_id': ident, 'attempt_id': view['attempt_id'],
                'original_wire_sha256': view['body_sha256'],
                'canonical_before_sha256': sha(canonical(before)), 'canonical_after_sha256': sha(canonical(after)),
                'request_bytes_v1': len(canonical(before)), 'request_bytes_v2': len(canonical(after)),
                'native_bytes_v1': len(canonical(v1)), 'native_bytes_v2': len(canonical(v2)),
                'tool_count': len(v1), 'parameter_tool_count': sum('input_json' in x['function']['parameters'].get('properties', {}) for x in v1),
                'metadata_only': True, 'model_tool_history_unchanged': True})
    require(len(requests) == 26 and sum(x['unmodified_requests'] for x in references) == 27, 'Historical denominator drift')
    changes = [x['trial_id'] for x in assessments if x['frozen_v1_task_completed'] != x['v2_task_completed']]
    require(changes == ['state_hover-candidate'], 'Unexpected retrospective completion change')
    sums = {key: sum(x[key] for x in requests) for key in ('request_bytes_v1', 'request_bytes_v2', 'native_bytes_v1', 'native_bytes_v2')}
    sums['request_bytes_change_percent'] = 100 * (sums['request_bytes_v2'] / sums['request_bytes_v1'] - 1)
    sums['native_bytes_change_percent'] = 100 * (sums['native_bytes_v2'] / sums['native_bytes_v1'] - 1)
    return {'schema': 'hover-compact-counterfactual-v1', 'passed': True, 'provider_calls': 0,
        'actual_new_tokens': None, 'tokenizer_calls': 0, 'historical_scores_rewritten': False,
        'scope': 'all 26 candidate requests projected; 27 control requests unchanged reference; all 8 public ledgers',
        'limitations': ['bytes are not token/cost estimates', 'no new natural model behavior or success-rate evidence',
                        'historical development evidence, not unseen test'],
        'source_verification': verification, 'source_manifest_sha256': sha((run / 'manifest.json').read_bytes()),
        'implementation_sha256': {name: sha((ROOT / name).read_bytes()) for name in
            ['scripts/audit_hover_compact_contract.py', 'agent4drone/tool_input_contract.py', 'agent4drone/hover_completion.py']},
        'totals': sums, 'requests': requests, 'control_reference': references, 'assessments': assessments}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(args.run.resolve()), 'Use a new output outside historical run')
    result = audit(args.run)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'passed': result['passed'], 'requests': len(result['requests']), 'totals': result['totals']}))


if __name__ == '__main__':
    main()
