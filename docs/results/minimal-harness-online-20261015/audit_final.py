"""Offline audit of sealed evidence; writes only this result directory, no model calls."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def totals(tasks):
    return dict(n=len(tasks), passed=sum(t['task_pass'] for t in tasks),
                calls=sum(t['logical_model_requests_in_window'] for t in tasks),
                http=sum(t['http_attempts_in_window'] for t in tasks),
                ready_seconds=sum(t['scoring_ready_seconds'] for t in tasks),
                handler_seconds=sum(t['logical_model_handler_seconds_full_trace'] for t in tasks))


def progress(snapshot):
    return snapshot.get('statistics', {}).get('task_progress', {}).get('progress_percentage')


c = read(OUT / 'comparison.json')
assert c['complete_arms'] == 5
audit = {'generated_at': datetime.now().astimezone().isoformat(),
         'scope': 'offline sealed evidence only; no model/API calls',
         'comparison_sha256': hashlib.sha256((OUT / 'comparison.json').read_bytes()).hexdigest(),
         'arms': [], 'handoffs': [], 'search_plans': [], 'simple_plans': [],
         'resume_anomalies': [], 'control_rejections': [], 'recursion_failures': []}
for a in c['arms']:
    arm = a['arm']
    stats = {'arm': arm, 'subgroups': {}, 'http_status': Counter(), 'telemetry_events': Counter(),
             'resume_categories': Counter(), 'resume_entries': 0, 'resume_cursor_mismatches': 0,
             'committed_plans': 0, 'wrong_skill_commits': 0, 'simple_sequence_mismatches': 0,
             'simple_actor_mismatches': 0,
             'unsupported_start_tasks': [], 'eligible_without_commit': [], 'empty_tool_chain_tasks': []}
    for group in ['simple_chain', 'public_search', 'eligible', 'unsupported']:
        ts = [t for t in a['tasks'] if (t['eligibility'] != 'unsupported' if group == 'eligible'
                                      else t['eligibility'] == group)]
        stats['subgroups'][group] = totals(ts)
    for path in sorted(Path(a['attempt']).glob('*/agent-traces/reproduction-metrics.jsonl')):
        for line in path.read_text().splitlines():
            e = json.loads(line)
            stats['telemetry_events'][e['event']] += 1
            if e['event'] == 'http_end':
                stats['http_status'][str(e.get('status_code'))] += 1
    for t in a['tasks']:
        d = read(ROOT / t['trace'])
        ev = (d.get('harness') or {}).get('events', [])
        frames = (d.get('harness') or {}).get('frames', [])
        commits = [e['frame'] for e in ev if e['kind'] == 'plan_commit']
        common = dict(arm=arm, scene=t['session_id'], task=t['task_id'], trace=t['trace'],
                      task_pass=t['task_pass'], trace_status=d['status'])
        if not d['tool_chain']:
            stats['empty_tool_chain_tasks'].append(t['task_id'])
        if t['eligibility'] != 'unsupported' and not commits and arm != 'r2_reference':
            stats['eligible_without_commit'].append(t['task_id'])
        starts = [e for e in ev if e['kind'] == 'model_control_tool' and e.get('args', {}).get('action') == 'start']
        if t['eligibility'] == 'unsupported' and starts:
            stats['unsupported_start_tasks'].append(t['task_id'])
        for v in d['tool_chain']:
            if v['tool'] != 'run_harness':
                continue
            try:
                obs = json.loads(v['observation'])
            except (ValueError, TypeError):
                continue
            if isinstance(obs, dict) and (obs.get('status') == 'rejected' or 'error' in obs):
                audit['control_rejections'].append(dict(**common, index=v['index'],
                    control=v['tool_input'], observation=obs))
        state, pending = {}, None
        for i, e in enumerate(ev):
            kind = e['kind']
            if kind in ['plan_commit', 'plan_update']:
                f = e['frame']
                state[f['plan_id']] = dict(f)
            elif kind == 'skill_exit' and e['plan_id'] in state:
                state[e['plan_id']]['status'] = e['status']
            elif kind == 'model_control_tool' and e.get('args', {}).get('action') == 'resume':
                pid = e['args'].get('plan_id')
                f = state.get(pid, {})
                category = f.get('status', 'unknown_plan')
                stats['resume_categories'][category] += 1
                pending = (pid, f.get('cursor'), category, e['sequence'])
                if category != 'paused':
                    audit['resume_anomalies'].append(dict(**common, plan_id=pid,
                        prior_status=category, sequence=e['sequence']))
            elif kind == 'skill_enter' and pending:
                pid, cursor, category, sequence = pending
                stats['resume_entries'] += 1
                if e['plan_id'] != pid or e['cursor'] != cursor or category != 'paused':
                    stats['resume_cursor_mismatches'] += 1
                pending = None
        for f in commits:
            pid = f['plan_id']
            final = next(x for x in frames if x['plan_id'] == pid)
            stats['committed_plans'] += 1
            stats['wrong_skill_commits'] += f['skill'] != t['eligibility']
            calls = [e['call'] for e in ev if e['kind'] == 'public_call_start' and e['plan_id'] == pid]
            actions = [x for x in calls if x['action']]
            if f['skill'] == 'simple_chain':
                actual = [dict(tool=x['tool'], args={k:v for k,v in x['args'].items() if k != 'drone_id'}) for x in actions]
                matches = actual == f['steps']
                stats['simple_sequence_mismatches'] += not matches
                bindings = final['actor_bindings']
                public_drones = [p['response'] for p in d['api_calls'] if p['endpoint']=='/drones' and isinstance(p.get('response'), list)]
                actor_matches = len(bindings)==1 and all(
                    name in d['command'] and all(x['args'].get('drone_id')==drone_id for x in actions)
                    and any(any(v.get('id')==drone_id and v.get('name')==name for v in ds) for ds in public_drones)
                    for name,drone_id in bindings.items())
                stats['simple_actor_mismatches'] += not actor_matches
                audit['simple_plans'].append(dict(**common, plan_id=pid, source=d['command'],
                    steps=f['steps'], actual_actions=actions, actor_bindings=final['actor_bindings'],
                    matches_committed_steps=matches, actor_matches_public_binding=actor_matches,
                    final_status=final['status']))
            if f['skill'] == 'public_search':
                prefix = Path(a['attempt']) / t['session_id'] / t['task_id']
                audit['search_plans'].append(dict(**common, plan_id=pid, harness_actions=len(actions),
                    final_status=final['status'], final_reason=final['reason'], cursor=final['cursor'],
                    before_progress=progress(read(str(prefix)+'.before.json')),
                    after_progress=progress(read(str(prefix)+'.after.json')),
                    last_skill_progress=final['state'].get('progress_percent'),
                    calls=t['logical_model_requests_in_window']))
        for i, e in enumerate(ev):
            if e['kind'] != 'skill_exit' or e.get('status') != 'needs_replan':
                continue
            f = next(x for x in frames if x['plan_id'] == e['plan_id'])
            later = ev[i+1:]
            actions = [x for x in later if x['kind'] == 'external_tool_start' and x['action']]
            chain = d['tool_chain']
            handoff_indices = [j for j,v in enumerate(chain) if v['tool']=='run_harness'
                               and 'needs_replan' in str(v['observation'])]
            post = chain[handoff_indices[0]+1:] if handoff_indices else []
            public_progress = []
            action_receipts = []
            unseen_targets = []
            for v in post:
                if v['tool'] == 'get_mission_progress':
                    obs = json.loads(v['observation'])
                    public_progress.append(dict(index=v['index'], progress=obs.get('progress_percentage')))
                if v['tool'] == 'get_target_info':
                    obs = json.loads(v['observation'])
                    if obs.get('is_reached') is False:
                        unseen_targets.append(dict(index=v['index'], target_id=obs.get('id'), name=obs.get('name'), position=obs.get('position')))
                if any(x['tool']==v['tool'] for x in actions):
                    try:
                        obs = json.loads(v['observation'])
                    except (ValueError, TypeError):
                        continue
                    if isinstance(obs, dict):
                        result = obs.get('navigation_result', obs.get('move_result',obs))
                        action_receipts.append(dict(index=v['index'], tool=v['tool'],
                            actor=v['tool_input'].get('drone_id'),
                            outcome=result.get('outcome'), status=result.get('status'),
                            arrival_verified=result.get('arrival_verified'),
                            message=result.get('message'),
                            last_move_message=result.get('last_move_result',{}).get('message')))
            prefix = Path(a['attempt']) / t['session_id'] / t['task_id']
            checks = read(str(prefix)+'.checks.json')
            audit['handoffs'].append(dict(**common, plan_id=e['plan_id'], reason=e['reason'],
                sequence=e['sequence'], skill_progress=f['state'].get('progress_percent'),
                remaining_seconds=f['deadline']-e['monotonic_s'], later_raw_actions=len(actions),
                later_raw_reads=sum(x['kind']=='external_tool_start' and not x['action'] for x in later),
                later_model_requests=sum(x['kind']=='model_request_start' for x in later),
                first_raw_actions=[dict(tool=x['tool'], args=x['args']) for x in actions[:3]],
                observed_unreached_targets=unseen_targets, action_receipts=action_receipts,
                public_progress_after=public_progress,
                sealed_after_progress=progress(read(str(prefix)+'.after.json')),
                checker_leaves=[v for v in checks if v['type']=='leaf'],
                observation_coverage='tool_chain available' if chain else 'exception: use event/API receipts and sealed snapshot; no tool_chain observations'))
        if 'Recursion limit of 450' in str(d.get('output')):
            audit['recursion_failures'].append(dict(**common, output=d['output'],
                ready_seconds=t['scoring_ready_seconds'], model_calls=t['logical_model_requests_in_window'],
                final_frames=[dict(plan_id=f['plan_id'],cursor=f['cursor'],status=f['status'],reason=f['reason']) for f in frames]))
    assert stats['telemetry_events']['llm_start'] == a['metric_totals']['logical_model_requests_full_trace']['sum']
    assert stats['telemetry_events']['http_start'] == a['metric_totals']['http_attempts_full_trace']['sum']
    assert not stats['wrong_skill_commits'] and not stats['simple_sequence_mismatches'] and not stats['simple_actor_mismatches']
    assert not stats['resume_cursor_mismatches']
    audit['arms'].append(stats)
(OUT / 'final_trajectory_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in audit.items() if k in ['arms','resume_anomalies']},ensure_ascii=False,indent=2))
