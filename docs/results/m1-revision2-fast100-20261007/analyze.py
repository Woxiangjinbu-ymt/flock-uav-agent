"""Read-only saved-evidence audit; writes only this new report directory.

No model calls, rescoring changes, run recovery, source/seal/status edits.
Run after the frozen scripts/m1_revision2_pilot.py verify entry succeeds.
"""
import collections
import csv
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
A = ROOT / 'experiments/runs/20261007-211050-m1r2-fast100-f5985403'
S = ROOT / 'experiments/m1-revision2-v1'
J = S / 'jobs/20261007-211050-guard-a754c424'
OLD = ROOT / 'docs/results/m1-revision-fast100-20261006'
BASE = ROOT / 'docs/results/m1-progress-fast100-20261006'
INPUTS = {}
ACTIONS = {'move_to', 'move_to_and_sense', 'navigate_to', 'navigate_to_and_sense',
 'move_towards', 'move_towards_and_sense', 'move_along_path', 'move_along_path_and_sense',
 'move_within_distance', 'take_off', 'land', 'return_home', 'change_altitude',
 'hover', 'rotate', 'set_home', 'calibrate', 'take_photo', 'send_message', 'broadcast', 'charge'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def raw(p):
    data = p.read_bytes()
    INPUTS[str(p.relative_to(ROOT))] = sha(data)
    return data


def read(p):
    return json.loads(raw(p))


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def label(old, new):
    return 'W' if new and not old else 'L' if old and not new else 'both_pass' if new else 'both_fail'


def at(message, path):
    for part in path:
        message = message[part]
    return message


def main():
    study = read(S / 'frozen/study.json')
    registry = read(S / 'runs.json')
    for p in J.iterdir():
        if p.is_file():
            raw(p)
    reference = read(A / 'historical-m0.json')
    plan, manifest, rows, perf = [read(A / n) for n in ('plan.json', 'manifest.json', 'results.json', 'performance.json')]
    verified = read(OUT / 'frozen-verify.json')
    assert registry['m1r2']['status'] == 'completed_and_verified' and manifest['status'] == 'completed'
    assert verified['historical_comparison']['m1r2_passed'] == sum(r['passed'] for r in rows)
    expected = [(s['id'], t['id']) for s in plan['sessions'] for t in s['tasks']]
    assert len(expected) == len(set(expected)) == 100
    assert [(r['session_id'], r['task_id']) for r in rows] == expected
    assert [(r['session_id'], r['task_id']) for r in reference['tasks']] == expected
    assert all(r['status'] == 'scored' and type(r['passed']) is bool for r in rows)
    assert sum(r['expected_checks'] for r in rows) == 605
    suite = read(A / 'suite-seal.json')['files']
    current = {str(p.relative_to(A)): sha(raw(p)) for p in A.rglob('*') if p.is_file() and p.name != 'suite-seal.json'}
    assert current == suite, 'Terminal suite seal mismatch'
    scene_audits = {}
    for s in plan['sessions']:
        directory = A / s['id']
        seal = read(directory / 'complete.json')
        files = {str(p.relative_to(directory)): sha(raw(p)) for p in directory.rglob('*') if p.is_file() and p.name != 'complete.json'}
        assert files == seal['files'], f"Scene seal mismatch: {s['id']}"
        scene_audits[s['id']] = {'matches': True, 'files': len(files), 'completed_at': seal['completed_at']}
    historical_artifacts = {}
    for directory in (OLD, BASE):
        checksums = read(directory / 'artifact-sha256.json')
        for name, checksum in checksums.items():
            assert sha(raw(directory / name)) == checksum, f'Historical report changed: {name}'
        historical_artifacts[str(directory.relative_to(ROOT))] = len(checksums)
    previous_summary = read(OLD / 'summary.json')
    old_pairs = {(p['session_id'], p['task_id']): p for p in read(OLD / 'paired-tasks.json')}
    base_summary = read(BASE / 'summary.json')
    base_pairs = {(p['session_id'], p['task_id']): p for p in read(BASE / 'paired-tasks.json')}
    assert sha(raw(BASE / 'summary.json')) == reference['source_summary_sha256']
    assert sha(raw(BASE / 'paired-tasks.json')) == reference['source_pairs_sha256']
    traces = [(s['id'], read(p), p) for s in plan['sessions']
              for p in sorted((A / s['id'] / 'agent-traces/toolchains').glob('*.json'))]
    assert len(traces) == 100
    events = {(sid, t['command_id']): {e['evidence_id']: e for e in t['task_progress']['events']} for sid, t, _ in traces}
    ordinal, task_traces, pairs = {}, [], []
    for order, (r, old) in enumerate(zip(rows, reference['tasks'])):
        sid, tid = r['session_id'], r['task_id']
        saved = read(A / sid / (tid + '.job.json'))
        w, b = [read(A / sid / (tid + suffix)) for suffix in ('.window.json', '.barrier.json')]
        matches = [(t, p) for sc, t, p in traces if sc == sid and t['command'] == saved['command'] and
                   w['opened_at'] <= datetime.fromisoformat(t['started_at']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() <= b['frozen_at']]
        assert len(matches) == 1
        t, path = matches[0]
        ordinal[sid, t['command_id']] = order
        task_traces.append((r, t, path))
        assert sha(t['command'].encode()) == next(x['content_sha256'] for sc in plan['sessions'] if sc['id'] == sid for x in sc['tasks'] if x['id'] == tid)
        p1, p0, u = old_pairs[sid, tid], base_pairs[sid, tid], r['observed_token_usage']
        pairs.append({'session_id': sid, 'task_id': tid, 'name': r['name'], 'family': r['family'], 'difficulty': r['difficulty'],
          'expected_checks': r['expected_checks'], 'm0_pass': old['passed'], 'm1_v1_pass': p0['m1_pass'],
          'm1r_diagnostic_pass': p1['m1r_observed_pass'], 'm1r2_pass': r['passed'],
          'pair_vs_m0': label(old['passed'], r['passed']), 'pair_vs_m1r_diagnostic': label(p1['m1r_observed_pass'], r['passed']),
          'old_pair_m1r_vs_m0': p1['pair_outcome_diagnostic'], 'm1r2_scoring_status': r['status'], 'm1r2_run_accepted': True,
          'm1r_run_accepted': False, 'm0_passed_checks': old['passed_checks'], 'm1r_passed_checks': p1['m1r_passed_checks'],
          'm1r2_passed_checks': sum(x['result'] is True for x in r['leaves']),
          'm0_timeout': p1['m0_timeout'], 'm1r_timeout': p1['m1r_timeout'], 'm1r2_timeout': r['agent_timed_out'],
          'm0_scoring_ready_seconds': p1['m0_scoring_ready_seconds'], 'm1r_scoring_ready_seconds': p1['m1r_scoring_ready_seconds'],
          'm1r2_scoring_ready_seconds': b['frozen_at'] - w['opened_at'],
          'm0_total_tokens': p1['m0_total_tokens'], 'm1r_total_tokens': p1['m1r_total_tokens'], 'm1r2_total_tokens': u['total_tokens'],
          'm1r2_prompt_tokens': u['prompt_tokens'], 'm1r2_completion_tokens': u['completion_tokens'],
          'm1r2_cache_hit_tokens': u['cache_hit_tokens'], 'm1r2_cache_miss_tokens': u['cache_miss_tokens'],
          'm0_trace': old['trace'], 'm1r_trace': p1['m1r_trace'], 'm1r2_trace': str(path.relative_to(ROOT))})
    audits, agg_tasks, cases = [], [], []
    for r, t, path in task_traces:
        sid, tid, cid = r['session_id'], r['task_id'], t['command_id']
        progress = t['task_progress']; ev = progress['task_evidence']; emap = events[sid, cid]
        issues = []; report_count = relative_count = next_step_count = 0
        views = [(ev['requirements'], ev.get('aggregate_requirements', []), None)]
        for injection in ev['injections']:
            text = injection['text']; payload = json.loads(text[text.index('{'):])
            views.append((payload['items'], payload.get('aggregate_requirements', []), injection))
            if len(text) > 4000 or len(payload['items']) > 4 or len(text[text.index('{'):]) > 3900:
                issues.append('view_budget')
            if payload['mission_completion'] != 'not_evaluated': issues.append('mission_verdict')
        aggregate_states = collections.Counter()
        for view, aggregates, injection in views:
            for item in view:
                lo, hi = item['source_span']
                if not (0 <= lo <= hi <= len(t['command'])) or item['target_name'] not in t['command'][lo:hi]:
                    issues.append('source_span')
                rep = item.get('report')
                if rep:
                    report_count += 1; key = (sid, rep['command_id']); event = events.get(key, {}).get(rep['evidence_id'])
                    if rep['session_id'] != sid or ordinal.get(key, 999) > ordinal[sid, cid] or event is None:
                        issues.append('report_scope'); continue
                    if at(event['payload'], rep['message_path']) != rep['message'] or event['tool_call_id'] != rep['tool_call_id'] or str(event['args'].get('drone_id')) != rep['actor_id']:
                        issues.append('report_receipt_binding')
                    hint = rep.get('localization_hint')
                    if hint:
                        if hint.get('start_evidence_id') and hint['start_evidence_id'] not in events[key]: issues.append('hint_origin')
                        if hint['kind'] == 'receipt_derived_relative_segment':
                            relative_count += 1
                            if event['tool_name'] not in {'move_towards', 'move_towards_and_sense'} or event['status'] != 'completed': issues.append('relative_source_status')
                            m = re.match(r'^Drone moved ([-+.\d]+)m to position \(([-+.\d]+), ([-+.\d]+), ([-+.\d]+)\), heading: ([-+.\d]+)° ', rep['message'])
                            if not m: issues.append('relative_source_format')
                            else:
                                dist, x, y, z, heading = map(float, m.groups())
                                theta = math.radians(float(event['args']['heading']))
                                pts = [[round(x - dist * math.sin(theta), 6), round(y - dist * math.cos(theta), 6), z], [x, y, z]]
                                if pts != hint['points'] or abs(dist - float(event['args']['distance'])) > .011 or float(event['args'].get('dz', 0)) != 0:
                                    issues.append('relative_geometry')
                                if any(str(events[key][eid]['args'].get('drone_id')) == rep['actor_id'] for eid in event['overlaps']): issues.append('relative_actor_overlap')
                    if item.get('next_step'):
                        next_step_count += 1
                        if item['relation'] != 'visit' or not item['actor_id'] or item['execution_state'] == 'evidenced' or item['knowledge_state'] != 'reported_only' or not hint:
                            issues.append('next_step_scope')
                proof = emap.get(item.get('execution_evidence_id'))
                if item.get('execution_evidence_id') and (proof is None or str(proof['args'].get('drone_id')) != item['actor_id']): issues.append('execution_actor_binding')
                if item['execution_state'] == 'evidenced' and (item['relation'] != 'visit' or item['execution_evidence_kind'] != 'current_actor_positive_contact_receipt'): issues.append('completion_scope')
            for ag in aggregates:
                aggregate_states[ag['freshness'] + '/' + ag['threshold_state']] += 1
                lo, hi = ag['source_span']
                if not 0 <= lo < hi <= len(t['command']) or 'progress' not in t['command'][lo:hi].lower(): issues.append('aggregate_span')
                obs = ag['observation']
                if obs:
                    event = emap.get(obs['evidence_id'])
                    if not event or event['tool_name'] != 'get_mission_progress' or event['status'] != 'observed': issues.append('progress_source')
                    else:
                        for k in ('session_id', 'task_type', 'progress_percentage', 'details', 'source_endpoint', 'scope'):
                            if obs[k] != event['payload'].get(k): issues.append('progress_payload_binding')
                        if obs['command_id'] != cid or obs['session_id'] != sid or obs['tool_call_id'] != event['tool_call_id'] or obs['end_sequence'] != event['end_sequence']: issues.append('progress_event_binding')
                        if set(event['payload']) != {'session_id', 'task_type', 'progress_percentage', 'details', 'source_endpoint', 'scope'}: issues.append('progress_allowlist')
                        if set(obs['details']) - {'total_targets', 'visited_targets', 'unvisited_targets'}: issues.append('progress_detail_allowlist')
                    expected_state = 'unknown'
                    if ag['freshness'] == 'observed':
                        met = obs['progress_percentage'] >= ag['threshold_percent'] if ag['operator'] == '>=' else obs['progress_percentage'] > ag['threshold_percent']
                        expected_state = 'observed_threshold_met' if met else 'observed_threshold_not_met'
                    if ag['threshold_state'] != expected_state: issues.append('progress_threshold')
                elif ag['freshness'] != 'unknown' or ag['threshold_state'] != 'unknown': issues.append('progress_unknown')
        # Exact freshness at the final saved state, including pending/failed refresh.
        observations = ev.get('progress_observations', [])
        queries = [e for e in emap.values() if e['tool_name'] == 'get_mission_progress']
        last_query = max(queries, key=lambda e: e['start_sequence']) if queries else None
        for ag in ev.get('aggregate_requirements', []):
            last = observations[-1] if observations else None
            if last and last_query and last_query['evidence_id'] != last['evidence_id']: last = None
            stale = bool(last and any(e['tool_name'] in ACTIONS and (e['end_sequence'] is None or e['end_sequence'] > last['start_sequence']) for e in emap.values()))
            expected_freshness = 'unknown' if not last else 'stale_after_action_or_overlap' if stale else 'observed'
            if ag['freshness'] != expected_freshness or ag['observation'] != last: issues.append('final_progress_refresh')
        if queries and not ev.get('aggregate_requirements'): issues.append('unscoped_progress_tool')
        wire = t['model_requests_wire']; middleware = ev['model_requests']; eligible = preserved = 0
        for i, req in enumerate(wire):
            if req['scope'] != 'serialized_http_request_body' or not re.fullmatch('[a-f0-9]{64}', req['body_sha256']) or req['body_bytes'] <= 0 or not req['message_sha256']:
                issues.append('wire_audit')
            if req['tools_sha256'] != wire[0]['tools_sha256']: issues.append('within_task_tools_changed')
            if i:
                prev = wire[i - 1]['message_sha256']; common = 0
                for x, y in zip(prev, req['message_sha256']):
                    if x != y: break
                    common += 1
                if common != req['common_prefix_messages'] or len(prev) != req['previous_message_count']: issues.append('wire_prefix_metadata')
                eligible += 1; preserved += int(common == len(prev))
        if len(wire) != len(middleware): issues.append('wire_middleware_count')
        audit = {'session_id': sid, 'task_id': tid, 'mode': t['runtime']['task_progress_mode'], 'active': t['runtime']['task_progress_active'],
          'revision': progress['revision'], 'observer_errors': progress['observer_errors'], 'request_audit_errors': t['model_request_audit_errors'],
          'issues': issues, 'source_references_checked_with_duplicates': report_count, 'relative_hints_checked_with_duplicates': relative_count,
          'next_step_items_with_duplicates': next_step_count, 'aggregate_states_with_duplicates': dict(aggregate_states),
          'progress_tool_calls': len(queries), 'progress_valid_observations': len(observations),
          'wire_requests': len(wire), 'wire_prefix_preserved': preserved, 'wire_prefix_eligible': eligible,
          'mission_completion': progress['mission_completion']}
        audits.append(audit)
        if ev.get('aggregate_requirements'):
            agg_tasks.append({'session_id': sid, 'task_id': tid, 'passed': r['passed'], 'command': t['command'],
              'observations': observations, 'final_requirements': ev['aggregate_requirements'], 'states_with_duplicates': dict(aggregate_states)})
        previous = old_pairs[sid, tid]
        if previous['pair_outcome_diagnostic'] in {'W', 'L'} or r['passed'] != previous['m0_pass']:
            snapshots = {suffix: read(A / sid / (tid + '.' + suffix + '.json')) for suffix in ('before', 'after')}
            pair = next(p for p in pairs if p['session_id'] == sid and p['task_id'] == tid)
            cases.append({'pair': pair, 'command': t['command'], 'output': t['output'],
              'failed_checks': [leaf for leaf in r['leaves'] if leaf['result'] is False],
              'before_progress': snapshots['before']['statistics'].get('task_progress'),
              'after_progress': snapshots['after']['statistics'].get('task_progress'),
              'requirements': ev['requirements'], 'first_injection': ev['injections'][0] if ev['injections'] else None,
              'progress_observations': observations, 'aggregate_requirements': ev.get('aggregate_requirements', []),
              'event_counts': dict(collections.Counter(e['tool_name'] for e in emap.values())),
              'event_status_counts': dict(collections.Counter(e['status'] for e in emap.values()))})
    write('evidence-audit.json', audits)
    assert all(not a['issues'] and not a['observer_errors'] and not a['request_audit_errors'] for a in audits), 'Review audit findings'
    assert all(a['mode'] == 'evidence_r2' and a['active'] and a['revision'] == 'm1-r2' and a['mission_completion'] == 'not_evaluated' for a in audits)
    counts = collections.Counter(p['pair_vs_m0'] for p in pairs)
    counts_r1 = collections.Counter(p['pair_vs_m1r_diagnostic'] for p in pairs)
    scenes = []
    for s in plan['sessions']:
        ps = [p for p in pairs if p['session_id'] == s['id']]; c = collections.Counter(p['pair_vs_m0'] for p in ps)
        scenes.append({'session_id': s['id'], 'name': s['name'], 'tasks': len(ps),
          'm0_pass': sum(p['m0_pass'] for p in ps), 'm1_v1_pass': sum(p['m1_v1_pass'] for p in ps),
          'm1r_diagnostic_pass': sum(p['m1r_diagnostic_pass'] for p in ps), 'm1r2_pass': sum(p['m1r2_pass'] for p in ps),
          'W': c['W'], 'L': c['L'], 'net': c['W'] - c['L'], 'scene_seal_valid': True,
          **{f'{arm}_{kind}': sum(p[f'{arm}_{source}'] for p in ps) for arm in ('m0', 'm1r', 'm1r2') for kind, source in [('task_seconds', 'scoring_ready_seconds'), ('tokens', 'total_tokens')]}})
    usage = perf['selected_generations']['llm']['usage']
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cache_hit_tokens', 'cache_miss_tokens'):
        assert sum(r['observed_token_usage'][key] for r in rows) == usage[key]
    observed = [json.loads(line) for line in raw(J / 'observations.jsonl').splitlines()]
    eligible = [o for o in observed if 40 <= o['scored_pairs'] < 100]
    guard = read(J / 'guard-status.json'); policy = read(J / 'guard-policy.json')
    assert not (J / 'stop-trigger.json').exists() and not any(o['triggered'] for o in observed)
    assert guard['status'] == 'completed' and guard['worker_exit_code'] == 0
    assert all(o['success_deficit'] < 5 for o in eligible)
    old_w = [p for p in pairs if p['old_pair_m1r_vs_m0'] == 'W']; old_l = [p for p in pairs if p['old_pair_m1r_vs_m0'] == 'L']
    cost = {'observed_tokens': usage, 'cache_hit_ratio': usage['cache_hit_tokens'] / usage['prompt_tokens'],
      'wall_seconds': sum(x['wall_seconds'] for x in perf['invocations']),
      'scoring_ready_seconds_sum': sum(p['m1r2_scoring_ready_seconds'] for p in pairs),
      'passed_checks': sum(p['m1r2_passed_checks'] for p in pairs), 'timeouts': sum(r['agent_timed_out'] for r in rows),
      'timeouts_passed': sum(r['agent_timed_out'] and r['passed'] for r in rows),
      'llm_completed_calls': perf['selected_generations']['llm']['completed_calls'],
      'llm_failed_calls': perf['selected_generations']['llm']['failed_calls'], 'http_errors': perf['selected_generations']['http']['errors']}
    summary = {'status': 'completed_and_verified', 'run_accepted': True, 'fixed_tasks': 100, 'scored_tasks': 100, 'unscored': 0,
      'checks': 605, 'm1r2_passed': sum(r['passed'] for r in rows), 'success_rate': sum(r['passed'] for r in rows) / 100,
      'm0_historical_passed': 80, 'm1_v1_historical_passed': 78, 'm1r_diagnostic_passed': 81, 'm1r_run_accepted': False,
      'pair_vs_m0': dict(counts), 'net_vs_m0': counts['W'] - counts['L'], 'pair_vs_m1r_diagnostic': dict(counts_r1),
      'old_W': {'total': len(old_w), 'retained': sum(p['m1r2_pass'] for p in old_w)},
      'old_L': {'total': len(old_l), 'recovered': sum(p['m1r2_pass'] for p in old_l)},
      'scenes': scenes, 'method_commit': study['methods']['m1r2']['agent_commit'], 'attempt': str(A.relative_to(ROOT)),
      'registry_status': registry['m1r2']['status'], 'manifest_status': manifest['status'],
      'started_at': manifest['started_at'], 'finished_at': manifest['finished_at'],
      'm0': reference['summary'], 'm1_v1': base_summary['arms']['m1'], 'm1r_diagnostic': previous_summary['m1r'], 'm1r2': cost,
      'guard': {'triggered': False, 'policy': policy, 'observations': len(observed),
        'max_observed_eligible_deficit': max(o['success_deficit'] for o in eligible), 'final_deficit': guard['latest']['success_deficit'],
        'trigger_snapshot': None, 'extra_scored_after_trigger': None, 'no_retries': guard['automatic_retries'] == 0,
        'guard_sha256': sha(raw(ROOT / 'experiments/analysis/m1_revision2_guard.py')), 'policy_sha256': sha(raw(J / 'guard-policy.json'))},
      'evidence_audit': {'traces': len(audits), 'active': sum(a['active'] for a in audits),
        **{k: sum(a[k] for a in audits) for k in ('source_references_checked_with_duplicates', 'relative_hints_checked_with_duplicates',
          'next_step_items_with_duplicates', 'progress_tool_calls', 'progress_valid_observations', 'wire_requests', 'wire_prefix_preserved', 'wire_prefix_eligible')},
        'observer_errors': 0, 'request_audit_errors': 0, 'binding_issues': 0, 'aggregate_tasks': len(agg_tasks)},
      'recommendation': 'do_not_launch_dev240_yet; retain_r2_as_integrity_valid_candidate; diagnose_new_losses_offline',
      'limitations': ['five_development_scenes', 'world_and_blackboard_carryover', 'historical_controls_not_contemporaneous',
        'new_progress_tool_changes_observation_protocol', 'no_prompt_or_observation_control', 'single_attempt_remote_model_sampling',
        'no_randomized_causal_or_significance_claim', 'old_R1_diagnostic_only_integrity_failure',
        'observed_tokens_not_invoice_cost', 'body_hashes_recorded_not_recomputed_from_raw_bodies',
        'injection_source_binding_and_threshold_checked; exact_freshness_recomputed_at_final_snapshots_only']}
    write('summary.json', summary); write('paired-tasks.json', pairs); write('case-changes.json', cases); write('aggregate-progress-audit.json', agg_tasks)
    with (OUT / 'paired-tasks.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(pairs[0])); writer.writeheader(); writer.writerows(pairs)
    write('verification.json', {'checked_at': datetime.now().astimezone().isoformat(), 'run_accepted': True,
      'frozen_verify_command': 'controller/.venv/bin/python -B scripts/m1_revision2_pilot.py verify ' + str(A.relative_to(ROOT)),
      'frozen_verify_exit_code': 0, 'frozen_verify_sha256': sha(raw(OUT / 'frozen-verify.json')),
      'terminal_suite_seal': {'matches': True, 'files': len(suite)}, 'scene_seals': scene_audits,
      'historical_report_artifacts_verified': historical_artifacts, 'model_calls_during_analysis': 0,
      'original_artifacts_modified': False, 'old_R1_run_accepted': False, 'old_R1_acceptance_state_preserved': True})
    write('input-sha256.json', INPUTS)
    print(json.dumps({k: summary[k] for k in ('status', 'm1r2_passed', 'pair_vs_m0', 'pair_vs_m1r_diagnostic', 'old_W', 'old_L', 'm1r2', 'evidence_audit', 'guard')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
