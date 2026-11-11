"""Read sealed calibration evidence; write derived audit files only (no model calls)."""
from pathlib import Path
from collections import Counter, defaultdict
import datetime
import hashlib
import json
import math
import statistics

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
STUDY = ROOT / 'experiments/minimal-harness-full100-v1'
ARMS = ('applicability', 'failure')

def read(p):
    return json.loads(p.read_text())

def lines(p):
    return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

manifest = read(STUDY / 'manifest.json')
launch = read(STUDY / 'launch.json')
campaign = read(STUDY / 'campaign.json')
registry = read(STUDY / 'runs.json')
schedule = read(STUDY / 'schedule.json')['blocks']
catalog = read(STUDY / 'tasks.json')
tasks = {t['task_id']: t for t in catalog['tasks']}
assert sha(STUDY / 'manifest.json') == launch['manifest_sha256']
assert set(registry) == set(manifest['authorized_trial_ids']) and len(registry) == 40
assert campaign['status'] == 'calibration_complete_paused_pending_user_decision'
assert all(v['status'] == 'completed' and v['exit_code'] == 0 for v in registry.values())
for name, expected in manifest['files'].items():
    assert sha(STUDY / name) == expected, name

rows, controls, gates = [], [], {}
quality = Counter()
history_types = Counter()
for tid, reg in registry.items():
    d = STUDY / 'runs' / tid
    seal = read(d / 'seal.json')
    assert {str(f.relative_to(d)) for f in d.rglob('*') if f.is_file() and f.name != 'seal.json'} == set(seal['files'])
    for name, expected in seal['files'].items():
        assert sha(d / name) == expected, (tid, name)
    r = read(d / 'result.json')
    h = read(d / 'harness.json')
    metrics = lines(d / 'reproduction-metrics.jsonl')
    requests = lines(d / 'public-api.jsonl')
    motion = lines(d / 'physical-events.jsonl')
    fixture = read(STUDY / 'fixtures' / (r['task_id'] + '.json'))
    assert r['initial_state_sha256'] == fixture['state_sha256']
    assert r['public_startup_sha256'] == fixture['public_startup_sha256']
    assert sha(d / 'score-world.pkl') == r['score_world_sha256']
    assert (d / 'score-world.json').read_bytes() == (d / 'post-drain-world.json').read_bytes()
    assert len({e['event_id'] for e in motion}) == len(motion)
    assert len({q['request_id'] for q in requests}) == len(requests)
    assert len(motion) == sum(len(q['history_events']) for q in requests)
    by_request = {q['request_id']: q for q in requests}
    for e in motion:
        q = by_request[e['request_id']]
        assert e['history'] == q['history_events'][int(e['event_id'].split(':')[1])]['history']
        delta = [e['history']['position'][k] - e['history']['start_position'][k] for k in ('x','y','z')]
        assert math.isclose(e['length_xy'], math.hypot(*delta[:2]), abs_tol=1e-8)
        assert math.isclose(e['length_xyz'], math.sqrt(sum(v*v for v in delta)), abs_tol=1e-8)
        if e['phase'] == 'task' and e['committed']:
            assert e['effect_seconds'] < 500
            history_types[e['history']['event']] += 1
    actions = [q for q in requests if q['phase'] == 'task' and q['action'] and not q['admin']]
    committed = [e for e in motion if e['phase'] == 'task' and e['committed']]
    pc = r['physical_cost']
    assert len(actions) == pc['action_attempts']
    assert len(committed) == pc['physical_history_events']
    for k in ('length_xy','length_xyz','vertical_distance'):
        assert math.isclose(sum(e[k] for e in committed), pc[k], abs_tol=1e-6)
    for k in ('consumed_mah','charged_mah'):
        assert math.isclose(sum(e[k] for q in actions for e in q['energy']), pc[k], abs_tol=1e-6)
    usage = Counter()
    for e in metrics:
        if e['event'] == 'llm_end':
            usage.update(e.get('usage') or {})
    assert all(usage[k] == v for k,v in r['usage'].items())
    assert sum(e['event'] == 'http_start' for e in metrics) == r['http_attempts']
    assert sum(e['event'] == 'llm_start' for e in metrics) == r['logical_calls']
    quality['missing_usage_calls'] += r['missing_usage_calls']
    quality['checker_errors'] += len(r['checker']['errors'])
    quality['uncommitted_actions'] += pc['uncommitted_actions']
    quality['partial_success_actions'] += pc['action_receipts']['partial_success']
    quality['actions_with_unclassified_receipt'] += len(actions)-sum(pc['action_receipts'].values())
    boundary = read(d / 'score-boundary.json')
    for e in h['events']:
        if e['kind'] in ('model_control_tool','control_result','control_rejected','plan_rejected','skill_enter','skill_exit','plan_update'):
            controls.append({'trial_id':tid, **e})
    gates[tid] = [{k:e[k] for k in ('decision','reason','cursor','next_call') if k in e}
                  for e in h['events'] if e['kind'] == 'gate']
    kinds = Counter(e['kind'] for e in h['events'])
    starts = [e for e in h['events'] if e['kind']=='model_control_tool' and e['args'].get('action')=='start']
    resumes = [e for e in h['events'] if e['kind']=='model_control_tool' and e['args'].get('action')=='resume']
    rejected = [json.loads(e['content']) for e in h['events'] if e['kind']=='control_result' and json.loads(e['content']).get('status') in ('rejected','unsupported')]
    # Attribute full returned usage of a crossing request to a separate category;
    # no streaming token timestamps are available to split tokens at the cutoff.
    t0 = next(e['monotonic_s'] for e in h['events'] if e['kind']=='task_start')
    deadline = next(e['deadline'] for e in h['events'] if e['kind']=='task_start')
    crossing_ends = [e for e in h['events'] if e['kind']=='model_request_end' and e['monotonic_s'] > deadline]
    crossing_usage = sum(u.get('total_tokens',0) for e in crossing_ends for u in e.get('usage',[]))
    row = {k:r[k] for k in ('trial_id','task_id','scene_id','arm','worker','position','repeat','ready_seconds','processing_wall_seconds','logical_calls','logical_calls_within_500','http_attempts','model_wall_seconds','usage','physical_cost','execution_diagnostics')}
    row.update(eligibility=tasks[r['task_id']]['eligibility'], passed=r['checker']['passed'],
               checks_passed=r['checker']['checks_passed'],checks_expected=r['checker']['checks_expected'],
               boundary=boundary, initial_state_sha256=r['initial_state_sha256'],public_startup_sha256=r['public_startup_sha256'],
               first_body_sha256=next(e['body_sha256'] for e in h['events'] if e['kind']=='http_attempt_start'),
               starts=len(starts), resumes=len(resumes), committed_plans=len(h['frames']),rejections=rejected,
               plan_statuses=[f['status'] for f in h['frames']], plan_skills=[f['skill'] for f in h['frames']],
               skill_enters=kinds['skill_enter'], paused_exits=sum(e['kind']=='skill_exit' and e['status']=='paused' for e in h['events']),
               needs_replan_exits=sum(e['kind']=='skill_exit' and e['status']=='needs_replan' for e in h['events']),
               request_usage_returning_after_cutoff=crossing_usage, requests_returning_after_cutoff=len(crossing_ends),
               simulator_effects_origin=dict(Counter(e['origin']['kind'] for e in committed)),
               recorded_action_origins=dict(Counter(q['origin']['kind'] for q in actions)))
    rows.append(row)

pair_checks=[]
for block in schedule:
    if not block['cost_calibration_block']: continue
    pair=[next(r for r in rows if r['trial_id']==t['trial_id']) for t in block['trials']]
    assert pair[0]['initial_state_sha256']==pair[1]['initial_state_sha256']
    assert pair[0]['public_startup_sha256']==pair[1]['public_startup_sha256']
    assert pair[0]['first_body_sha256']==pair[1]['first_body_sha256']
    for t in block['trials']:
        reg=registry[t['trial_id']]
        assert all(reg[k]==block[k] for k in ('worker','repeat','task_id','block_id','rank_within_worker_repeat'))
        assert reg['position']==t['position'] and reg['arm']==t['arm']
    pair_checks.append({'task_id':block['task_id'],'initial_equal':True,'public_equal':True,'first_wire_body_equal':True,
                        'gate_sequences_equal':gates[pair[0]['trial_id']]==gates[pair[1]['trial_id']]})
for worker in (0,1):
    expected=[t['trial_id'] for b in sorted(schedule,key=lambda b:b['rank_within_worker_repeat']) if b['cost_calibration_block'] and b['worker']==worker for t in b['trials']]
    actual=sorted([r for r in registry.values() if r['worker']==worker],key=lambda r:r['started_unix'])
    assert expected==[r['trial_id'] for r in actual]
    assert all(a['ended_unix'] <= b['started_unix'] for a,b in zip(actual,actual[1:]))

def aggregate(group):
    out={'n':len(group),'successes':sum(r['passed'] for r in group),'checks_passed':sum(r['checks_passed'] for r in group),'checks_expected':sum(r['checks_expected'] for r in group)}
    for k in ('logical_calls','logical_calls_within_500','http_attempts','ready_seconds','processing_wall_seconds','model_wall_seconds'):
        vals=[r[k] for r in group];out[k]={'sum':sum(vals),'mean':statistics.mean(vals),'median':statistics.median(vals),'max':max(vals)}
    out['usage']={k:sum(r['usage'][k] for r in group) for k in group[0]['usage']}
    out['physical_cost']={k:sum(r['physical_cost'][k] for r in group) for k in group[0]['physical_cost'] if k!='action_receipts'}
    out['action_receipts']={k:sum(r['physical_cost']['action_receipts'][k] for r in group) for k in ('success','partial_success','error')}
    out['cutoff_tasks']=[r['trial_id'] for r in group if r['ready_seconds']==500]
    out['tool_budget_hits']=sum(r['execution_diagnostics']['stopped_due_to_step_limit'] for r in group)
    out['max_tool_steps']=max(r['execution_diagnostics']['tool_steps'] for r in group)
    out['starts']=sum(r['starts'] for r in group);out['resumes']=sum(r['resumes'] for r in group)
    out['committed_plans']=sum(r['committed_plans'] for r in group)
    out['needs_replan_exits']=sum(r['needs_replan_exits'] for r in group)
    out['paused_exits']=sum(r['paused_exits'] for r in group)
    out['request_usage_returning_after_cutoff']=sum(r['request_usage_returning_after_cutoff'] for r in group)
    return out

def paired(group):
    ids=sorted({r['task_id'] for r in group});out=Counter();differences=defaultdict(list)
    for task in ids:
        a,f=[next(r for r in group if r['task_id']==task and r['arm']==arm) for arm in ARMS]
        out['W' if a['passed'] and not f['passed'] else 'L' if f['passed'] and not a['passed'] else 'both_success' if a['passed'] else 'both_failure']+=1
        vals={'tokens':(a['usage']['total_tokens'],f['usage']['total_tokens'])}
        vals.update({k:(a[k],f[k]) for k in ('logical_calls','ready_seconds','model_wall_seconds')})
        vals.update({k:(a['physical_cost'][k],f['physical_cost'][k]) for k in ('action_attempts','length_xy','length_xyz','consumed_mah')})
        for k,(v,w) in vals.items(): differences[k].append(v-w)
    return {'n_pairs':len(ids),'success':dict(out),'cost_differences_A_minus_F':{k:{'sum':sum(v),'mean':statistics.mean(v),'median':statistics.median(v),'lower':sum(x < -1e-7 for x in v),'higher':sum(x > 1e-7 for x in v),'equal':sum(abs(x)<=1e-7 for x in v)} for k,v in differences.items()}}

report={'audit_time':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
        'campaign':campaign,'wall_seconds':campaign['ended_unix']-campaign['started_unix'],
        'manifest_files':len(manifest['files']),'seals_valid':40,'pairs_valid':pair_checks,'quality':dict(quality),
        'history_event_types':dict(history_types),'all':aggregate(rows),'arms':{a:aggregate([r for r in rows if r['arm']==a]) for a in ARMS},
        'paired':paired(rows),'scenes':{},'eligibility':{},
        'worker_seconds':{str(w):max(r['ended_unix'] for r in registry.values() if r['worker']==w)-min(r['started_unix'] for r in registry.values() if r['worker']==w) for w in (0,1)}}
for key in ('scene_id','eligibility'):
    for v in sorted({r[key] for r in rows}):
        subset=[r for r in rows if r[key]==v]
        report['scenes' if key=='scene_id' else 'eligibility'][v]={'arms':{a:aggregate([r for r in subset if r['arm']==a]) for a in ARMS},'paired':paired(subset)}
save('audit.json',report);save('trial-metrics.json',rows);save('control-events.json',controls)

# Frozen forecast rule: scene x static skill actual/old ratio, floor 1.25;
# unseen groups retain 2.0, and observed ratios have no upper clipping.
comparison=read(ROOT/'docs/results/minimal-harness-online-20261015/comparison.json')
old={}
for arm in ARMS:
    a=next(a for a in comparison['arms'] if a['arm']=='same_skill_'+arm)
    command_to_task={read(ROOT/t['trace'])['command_id']:t['task_id'] for t in a['tasks']}
    usage=defaultdict(Counter)
    for f in Path(a['attempt']).glob('*/agent-traces/reproduction-metrics.jsonl'):
        for e in lines(f):
            if e['event']=='llm_end':usage[command_to_task[e['command_id']]].update(e['usage'])
    assert sum(u['total_tokens'] for u in usage.values())==a['observed_usage']['total_tokens']
    for t in a['tasks']:
        old[(t['task_id'],arm)]={'tokens':usage[t['task_id']]['total_tokens'],'seconds':t['scoring_ready_seconds']}

ratios={}
for t in tasks.values():
    key=(t['scene_id'],t['eligibility'])
    if key in ratios:continue
    selected=[r for r in rows if (r['scene_id'],r['eligibility'])==key]
    observed_tokens=sum(r['usage']['total_tokens'] for r in selected)
    old_tokens=sum(old[(r['task_id'],r['arm'])]['tokens'] for r in selected)
    observed_seconds=sum(r['processing_wall_seconds'] for r in selected)
    old_seconds=sum(old[(r['task_id'],r['arm'])]['seconds'] for r in selected)
    ratios[key]={'observed_n':len(selected),'actual_tokens':observed_tokens,'old_tokens':old_tokens,
                 'raw_token_ratio':observed_tokens/old_tokens if old_tokens else None,
                 'token_multiplier':max(1.25,observed_tokens/old_tokens) if old_tokens else 2.0,
                 'time_multiplier':max(1.25,observed_seconds/old_seconds) if old_seconds else 2.0}
remaining_tokens=0;loads=Counter();unclipped_loads=Counter();remaining=[]
for b in schedule:
    if b['cost_calibration_block']:continue
    ratio=ratios[(b['scene_id'],tasks[b['task_id']]['eligibility'])]
    for t in b['trials']:
        v=old[(b['task_id'],t['arm'])]
        tokens=v['tokens']*ratio['token_multiplier'];seconds=v['seconds']*ratio['time_multiplier']
        remaining_tokens+=tokens
        # Separate an uncapped planning envelope from the fixed watchdog slot replay.
        loads[(b['repeat'],b['worker'])]+=min(630,seconds)
        unclipped_loads[(b['repeat'],b['worker'])]+=seconds
        remaining.append({'trial_id':t['trial_id'],'tokens':tokens,'seconds_uncapped':seconds,'slot_seconds':min(630,seconds)})
forecast={'rule':'scene x static skill observed/old; observed floor 1.25; unseen 2.0; token ratios not upper clipped',
          'strata':[{'scene_id':k[0],'eligibility':k[1],**v} for k,v in ratios.items()],
          'remaining_executions':len(remaining),'remaining_tokens':round(remaining_tokens),
          'total_tokens_including_calibration':round(remaining_tokens+report['all']['usage']['total_tokens']),
          'token_soft_stop':1000000000,
          'remaining_schedule_hours_slot_capped':sum(max(loads[(r,w)] for w in (0,1)) for r in (1,2))/3600,
          'remaining_schedule_hours_uncapped_planning':sum(max(unclipped_loads[(r,w)] for w in (0,1)) for r in (1,2))/3600,
          'remaining_worker_load_seconds':{f'r{r}-w{w}':v for (r,w),v in loads.items()},
          'stress_hours_500':360*500/7200,'stress_hours_630':360*630/7200,
          'note':'Planning extrapolation only, not a confidence interval or launch authorization. Time multiplier uses processing wall vs old ready; capped slots are not a completion guarantee.'}
save('remaining-budget.json',forecast)
print(json.dumps({'arms':report['arms'],'paired':report['paired'],'quality':report['quality'],'wall_seconds':report['wall_seconds'],'forecast':forecast},ensure_ascii=False,indent=2))
