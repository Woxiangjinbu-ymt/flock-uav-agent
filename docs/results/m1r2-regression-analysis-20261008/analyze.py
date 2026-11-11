"""Offline causal-boundary diagnosis. No changes to methods, attempts or scores.

World snapshots are used only for diagnostic comparisons. The coordinate probe
accepts original command text and actual tool receipts, never checker data.
"""
import collections
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
INPUTS = {}
CASES = {'529c0622': ['Circle Target 6', 'Polygon Target 7'],
         'de19fecd': ['Waypoint 1'],
         'c6e28e3b': ['Fixed Target 59', 'Fixed Target 55', 'Fixed Target 31'],
         '22e1e1c5': ['Fixed Target 15', 'Fixed Target 9'], 'cf8ee51f': []}


def read(path):
    data = path.read_bytes()
    INPUTS[str(path.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
    return json.loads(data)


def write(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def named(value, names):
    if isinstance(value, dict):
        if value.get('name') in names:
            yield value
        for x in value.values():
            yield from named(x, names)
    elif isinstance(value, list):
        for x in value:
            yield from named(x, names)


def events(trace):
    if trace.get('task_progress', {}).get('events'):
        return [{'index': e['evidence_id'], 'tool': e['tool_name'], 'args': e['args'],
                 'payload': e['payload'], 'status': e['status']} for e in trace['task_progress']['events']]
    result = []
    for e in trace['tool_chain']:
        try:
            payload = json.loads(e['observation'])
        except (ValueError, TypeError):
            payload = e['observation']
        result.append({'index': e['index'], 'tool': e['tool'], 'args': e['tool_input'], 'payload': payload})
    return result


def receipts(value):
    if not isinstance(value, dict):
        return
    yield value
    for key in ('last_move_result', 'direct_move_result', 'move_result', 'navigation_result'):
        yield from receipts(value.get(key))
    if isinstance(value.get('last_step'), dict):
        for key in ('result', 'move_result'):
            yield from receipts(value['last_step'].get(key))
    for step in value.get('steps', []) or []:
        for key in ('result', 'move_result'):
            yield from receipts(step.get(key))


def point(value):
    if isinstance(value, dict): value = [value.get(k) for k in ('x', 'y', 'z')]
    if not isinstance(value, (tuple, list)) or len(value) != 3: return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value): return None
    return tuple(value)


def confirmed_points(event):
    """Only receipt positions, not requested arguments, route segments or plans."""
    actor = event['args'].get('drone_id')
    if not actor: return []
    result = []
    for sub in receipts(event['payload']):
        if sub.get('drone_id', actor) != actor: continue
        for p in sub.get('successful_points', []) or []:
            if point(p): result.append(point(p))
        if sub.get('arrival_verified') is True and sub.get('success') is True and point(sub.get('final_position')):
            result.append(point(sub['final_position']))
        message = sub.get('message', '')
        m = re.match(r'^Drone moved (?:to position|[-+.\d]+m to position) \(([-+.\d]+), ([-+.\d]+), ([-+.\d]+)\)', message)
        if m and sub.get('status') == 'success' and sub.get('outcome') == 'completed': result.append(tuple(map(float, m.groups())))
    return list(dict.fromkeys(result))


def coordinate_probe(command, seq):
    """Narrow explicit 'Drone N route:' grammar; coverage only, not route-order proof."""
    actors = {}
    for e in seq:
        if e['tool'] == 'list_drones' and isinstance(e['payload'], list):
            actors.update({v['name']: v['id'] for v in e['payload'] if isinstance(v, dict) and 'name' in v and 'id' in v})
    goals = []
    for match in re.finditer(r'(Drone \d+) route:\s*(.*?)(?=Drone \d+ route:|$)', command, re.S):
        actor = actors.get(match.group(1))
        for local, coord in enumerate(re.finditer(r'\(\s*([-+.\d]+)\s*,\s*([-+.\d]+)\s*,\s*([-+.\d]+)\s*\)', match.group(2)), 1):
            xyz = tuple(map(float, coord.groups()))
            evidence = [{'event': e['index'], 'tool': e['tool'], 'point': p} for e in seq if actor and e['args'].get('drone_id') == actor
                        for p in confirmed_points(e) if math.dist(p, xyz) <= .000001]
            # Public tool arrival contract can accept a detour ending near the
            # requested point. Bind that verified reply to this exact requested
            # goal; never borrow an ADMIN checker's tolerance or an arbitrary
            # nearby point. Preserve the actual point and reported distance.
            for e in seq:
                if e['tool'] not in ('navigate_to', 'navigate_to_and_sense') or e['args'].get('drone_id') != actor:
                    continue
                if point(e['args']) != xyz: continue
                for sub in receipts(e['payload']):
                    if sub.get('success') is True and sub.get('arrival_verified') is True and sub.get('drone_id') == actor and point(sub.get('final_position')):
                        evidence.append({'event':e['index'],'tool':e['tool'],'kind':'public_tool_verified_requested_goal',
                                         'actual_point':point(sub['final_position']),'distance_remaining':sub.get('distance_remaining')})
            goals.append({'actor': match.group(1), 'actor_id': actor, 'ordinal': local, 'point': xyz,
                          'source_span': [match.start(2)+coord.start(), match.start(2)+coord.end()], 'confirmed_receipts': evidence,
                          'nearest_recorded_position_distance': min((math.dist(p, xyz) for e in seq if e['args'].get('drone_id') == actor for p in confirmed_points(e)), default=None)})
    return {'supported': bool(goals), 'explicit_coordinate_goals': len(goals),
            'receipt_confirmed': sum(bool(g['confirmed_receipts']) for g in goals),
            'missing': [g for g in goals if not g['confirmed_receipts']], 'goals': goals,
            'limits': 'Exact receipt positions or explicit public navigation verified-arrival contract. No pass prediction, route-order proof, interpolation, hidden checker tolerance or scene snapshot input.'}


def main():
    pairs = read(ROOT/'docs/results/m1-revision2-fast100-20261007/paired-tasks.json')
    result, route_screen = [], []
    for p in pairs:
        trace = read(ROOT/p['m1r2_trace'])
        if re.search(r'Drone \d+ route:', trace['command']):
            route_screen.append({'session_id':p['session_id'],'task_id':p['task_id'],
                                 'probe':coordinate_probe(trace['command'],events(trace)),
                                 'external_score_for_diagnosis_only':p['m1r2_pass']})
    for p in pairs:
        if p['task_id'] not in CASES: continue
        record = {'session_id': p['session_id'], 'task_id': p['task_id'], 'arms': {}}
        for arm in ('m0', 'm1r', 'm1r2'):
            path = ROOT/p[arm+'_trace']; trace = read(path); attempt = path.parents[3]; seq = events(trace)
            before = read(attempt/p['session_id']/(p['task_id']+'.before.json'))
            after = read(attempt/p['session_id']/(p['task_id']+'.after.json'))
            ev = trace.get('task_progress', {}).get('task_evidence', {}); inj = ev.get('injections', [])
            first_entities, first_contacts = {}, {}
            for e in seq:
                for v in named(e['payload'], CASES[p['task_id']]):
                    first_entities.setdefault(v['name'], {'event': e['index'], 'tool': e['tool'], 'args': e['args'], 'entity': v})
                for sub in receipts(e['payload']):
                    message = sub.get('message', '')
                    if ' - Reached target(s): ' not in message: continue
                    for name in CASES[p['task_id']]:
                        if name in message.split(' - Reached target(s): ', 1)[1]:
                            first_contacts.setdefault(name, {'event': e['index'], 'tool': e['tool'], 'args': e['args'], 'receipt': sub})
            arm_data = {'trace': p[arm+'_trace'], 'command': trace['command'],
              'scored_success': p[{'m0':'m0_pass','m1r':'m1r_diagnostic_pass','m1r2':'m1r2_pass'}[arm]],
              'trace_event_coverage': 'observer_events' if trace.get('task_progress') else 'saved_tool_chain' if seq else 'no_tool_chain_in_exception_trace; do_not_infer_no_actions',
              'before_target_truth_DIAGNOSTIC_ONLY': list(named(before['targets'], CASES[p['task_id']])),
              'after_target_truth_DIAGNOSTIC_ONLY': list(named(after['targets'], CASES[p['task_id']])),
              'before_progress_DIAGNOSTIC_ONLY': before['statistics'].get('task_progress'),
              'first_visible_entities': first_entities, 'first_contact_receipts': first_contacts,
              'takeoff_actor_count': len({e['args'].get('drone_id') for e in seq if e['tool']=='take_off'}),
              'movement_actor_count': len({e['args'].get('drone_id') for e in seq if e['tool'].startswith(('move_', 'navigate_'))}),
              'tool_counts': dict(collections.Counter(e['tool'] for e in seq)),
              'status_counts': dict(collections.Counter(e.get('status','not_recorded') for e in seq)),
              'model_wire_requests': len(trace.get('model_requests_wire',[])) or None,
              'wire_tool_schema_sha256': trace['model_requests_wire'][0]['tools_sha256'] if trace.get('model_requests_wire') else None,
              'wire_first_system_message_sha256': trace['model_requests_wire'][0]['message_sha256'][0] if trace.get('model_requests_wire') else None,
              'receipt_json_chars': sum(len(json.dumps(e['payload'],ensure_ascii=False)) for e in seq),
              'injected_summary_chars': sum(len(i['text']) for i in inj),
              'r2_features_in_saved_views': any('Prioritize local sensing' in i['text'] or 'aggregate_requirements' in i['text'] for i in inj),
              'final_requirements': ev.get('requirements'), 'coordinate_probe': coordinate_probe(trace['command'], seq)}
            if p['task_id']=='de19fecd':
                arm_data['drone6_confirmed_positions'] = [{'event':e['index'],'positions':confirmed_points(e)} for e in seq if e['args'].get('drone_id')=='f985063a' and confirmed_points(e)]
                arm_data['target_information_calls'] = [{k:e[k] for k in ('index','tool','args','payload')} for e in seq if e['tool'] in ('get_target_info','generate_coverage_path')]
            record['arms'][arm] = arm_data
        r1, r2 = record['arms']['m1r'], record['arms']['m1r2']
        record['same_r1_r2_tool_schema_hash'] = r1['wire_tool_schema_sha256'] == r2['wire_tool_schema_sha256']
        record['same_r1_r2_system_message_hash'] = r1['wire_first_system_message_sha256'] == r2['wire_first_system_message_sha256']
        result.append(record)
    route = next(r for r in result if r['task_id']=='cf8ee51f')
    assert [route['arms'][a]['coordinate_probe']['explicit_coordinate_goals'] for a in ('m0','m1r','m1r2')] == [36,36,36]
    # M0's other unresolved goal is a verified-arrival failure at 1.414m;
    # the external checker accepts 3m. Keep this discrepancy, do not import
    # the checker's looser tolerance to make the probe match its score.
    assert [route['arms'][a]['coordinate_probe']['receipt_confirmed'] for a in ('m0','m1r','m1r2')] == [35,36,35]
    missing = route['arms']['m1r2']['coordinate_probe']['missing']
    assert len(missing)==1 and missing[0]['point']==(292.,920.,27.) and missing[0]['actor']=='Drone 7'
    assert not confirmed_points({'args':{'drone_id':'a','x':1,'y':2,'z':3},'payload':{'status':'error','message':'blocked'}})
    assert not confirmed_points({'args':{'drone_id':'a'},'payload':{'drone_id':'b','successful_points':[[1,2,3]]}})
    write('case-audit.json',result)
    write('explicit-route-screen.json',route_screen)
    write('summary.json',{'case_count':len(result),'r2_features_visible_in_regression_cases':sum(r['arms']['m1r2']['r2_features_in_saved_views'] for r in result),
      'coordinate_probe':{a:{k:route['arms'][a]['coordinate_probe'][k] for k in ('explicit_coordinate_goals','receipt_confirmed','missing')} for a in ('m0','m1r','m1r2')},
      'route_screen':{'tasks':len(route_screen),'goals':sum(x['probe']['explicit_coordinate_goals'] for x in route_screen),
                      'unconfirmed_goals':sum(len(x['probe']['missing']) for x in route_screen)},
      'all_five_tool_and_system_hashes_match_r1':all(r['same_r1_r2_tool_schema_hash'] and r['same_r1_r2_system_message_hash'] for r in result),
      'no_model_calls':True,'original_files_unchanged':True,'probe_not_installed_in_agent':True})
    write('input-sha256.json',INPUTS)
    print(json.dumps({'cases':len(result),'route_confirmed':[35,36,35],'regression_features_visible':0},ensure_ascii=False))


if __name__=='__main__': main()
