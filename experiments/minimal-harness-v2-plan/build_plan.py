"""Build a proposed schedule and empirical budget. No execution or model access."""
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
SEED = 20261016


def read(p):
    return json.loads(p.read_text())


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


fast_path = ROOT / 'experiments/minimal-harness-v1/frozen/fast100.json'
elig_path = ROOT / 'experiments/minimal-harness-v1/frozen/eligibility.json'
comparison_path = ROOT / 'docs/results/minimal-harness-online-20261015/comparison.json'
fast, comparison = read(fast_path), read(comparison_path)
eligible = {x['task_id']: x['skill'] for x in read(elig_path)['entries']}
task_specs = []
for scene in fast['sessions']:
    source = ROOT / 'experiments/minimal-harness-v1/frozen/data/scenes' / scene['source_file']
    assert digest(source) == scene['sha256']
    for task in scene['tasks']:
        task_specs.append(dict(case_id=task['id'], scene_id=scene['id'], eligibility=eligible[task['id']],
            task_content_sha256=task['content_sha256'], scene_source=str(source.relative_to(ROOT)),
            scene_source_sha256=digest(source),
            fixture_id='canonical-v2-' + task['id'],
            prepared_snapshot_sha256=None, public_startup_sha256=None,
            fixture_status='planned_not_materialized'))
assert len(task_specs) == 100
assert sum(x['eligibility'] != 'unsupported' for x in task_specs) == 13

# The first two families isolate the actual A/F scheduling difference.
families = [
    ('recoverable_blocked_candidate', 8, 'A continue locally; F scheduling pause; both retain mandatory guards'),
    ('recoverable_takeoff_actor_disabled', 4, 'A continue with another actor; F scheduling pause'),
    ('valid_successful_checkpoint', 4, 'both continue'),
    ('invalid_dependency_or_exhausted_repair', 4, 'both needs_replan before another stale action'),
    ('unknown_effect_receipt', 4, 'both observe once then needs_replan; never replay uncertain action'),
]
mechanism = [dict(case_id=f'{family}-{i:02d}', family=family, expected=expected,
                  fixture_status='planned_not_materialized', fixture_sha256=None)
             for family, count, expected in families for i in range(1,count+1)]

# Each case reverses arm order and worker on repeat 2. Four-cell cases use
# near-balanced Latin orders, not claimed to be exactly balanced for 13 cases.
four_orders = [
    ['A_H','F_H','F_L','A_L'], ['F_H','A_L','A_H','F_L'],
    ['A_L','F_L','F_H','A_H'], ['F_L','A_H','A_L','F_H'],
]
rng = random.Random(SEED)
schedule = []
for stage, cases in [('mechanism',mechanism), ('main_and_budget',task_specs)]:
    shuffled = list(cases)
    rng.shuffle(shuffled)
    counter = 0
    for case_index, case in enumerate(shuffled):
        four_cell = stage == 'main_and_budget' and case['eligibility'] != 'unsupported'
        if four_cell:
            order = four_orders[counter % 4]
            counter += 1
        else:
            order = ['A_H','F_H'] if case_index % 2 == 0 else ['F_H','A_H']
        for repeat in [1,2]:
            cells = order if repeat == 1 else list(reversed(order))
            trials = []
            for position, cell in enumerate(cells, 1):
                arm, level = cell.split('_')
                trials.append(dict(position=position, arm='applicability' if arm=='A' else 'failure',
                    budget='high' if level=='H' else 'legacy',
                    analysis_set='mechanism' if stage=='mechanism' else ('main100' if level=='H' else 'budget13')))
            schedule.append(dict(stage=stage, block_id=f"{stage}:{case['case_id']}:r{repeat}",
                case_id=case['case_id'], repeat=repeat, worker=(case_index+repeat-1)%2,
                trials=trials, execution_status='not_launched'))
    # Shuffle block order within each repeat, preserving all cells in a block.
    for repeat in [1,2]:
        blocks=[x for x in schedule if x['stage']==stage and x['repeat']==repeat]
        rng.shuffle(blocks)
        for position, b in enumerate(blocks,1):
            b['dispatch_rank_within_stage_repeat']=position
schedule.sort(key=lambda x:(0 if x['stage']=='mechanism' else 1,x['repeat'],x['dispatch_rank_within_stage_repeat']))
counts = Counter(t['analysis_set'] for b in schedule for t in b['trials'])
assert counts == {'mechanism':96,'main100':400,'budget13':52}
for stage, cases in [('mechanism',mechanism), ('main_and_budget',task_specs)]:
    for case in cases:
        blocks=[b for b in schedule if b['stage']==stage and b['case_id']==case['case_id']]
        assert len(blocks)==2 and blocks[0]['worker']!=blocks[1]['worker']
        assert [(x['arm'],x['budget']) for x in blocks[0]['trials']] == list(reversed([(x['arm'],x['budget']) for x in blocks[1]['trials']]))
save('case_catalog.proposed.json',dict(status='proposal_not_execution_freeze',tasks=task_specs,mechanism=mechanism))
save('schedule.proposed.json',dict(status='not_launched',seed=SEED,workers=2,counts=dict(counts),blocks=schedule))

arms = [a for a in comparison['arms'] if a['arm'] in ['same_skill_applicability','same_skill_failure']]
usage = Counter()
eligible_usage = Counter()
eligible_calls = 0
eligible_seconds = 0
for a in arms:
    usage.update(a['observed_usage'])
    command_to_task = {read(ROOT/t['trace'])['command_id']:t for t in a['tasks']}
    for p in Path(a['attempt']).glob('*/agent-traces/reproduction-metrics.jsonl'):
        for line in p.read_text().splitlines():
            e=json.loads(line)
            if e['event']=='llm_end' and command_to_task[e['command_id']]['eligibility']!='unsupported':
                eligible_usage.update(e['usage'])
    for t in a['tasks']:
        if t['eligibility']!='unsupported':
            eligible_calls+=t['logical_model_requests_in_window']
            eligible_seconds+=t['scoring_ready_seconds']
base_tokens=2*usage['total_tokens']
base_seconds=2*sum(a['metric_totals']['scoring_ready_seconds']['sum'] for a in arms)
base_calls=2*sum(a['metric_totals']['logical_model_requests_in_window']['sum'] for a in arms)
main_tokens=[base_tokens*1.25,base_tokens*2.0]
budget_tokens=[2*eligible_usage['total_tokens']*1.25,2*eligible_usage['total_tokens']*2.5]
mechanism_tokens=[30_000_000,100_000_000]
core_tokens=[sum(x[i] for x in [main_tokens,budget_tokens,mechanism_tokens]) for i in [0,1]]
budget=dict(status='planning_estimate_not_spend_authorization',source=str(comparison_path.relative_to(ROOT)),
    source_sha256=digest(comparison_path), observed_pair_tasks=200,observed_pair_usage=dict(usage),
    observed_eligible_pair_usage=dict(eligible_usage),
    main400_unadjusted=dict(total_tokens=base_tokens,logical_calls=base_calls,
        input_cache_hit_tokens=2*usage['cache_hit_tokens'], input_cache_miss_tokens=2*usage['cache_miss_tokens'],
        output_tokens=2*usage['completion_tokens'],sum_ready_seconds=base_seconds,
        ideal_two_worker_hours=base_seconds/2/3600),
    assumptions=dict(main_multiplier=[1.25,2.0],budget13_multiplier=[1.25,2.5],
        mechanism_tokens='30M to 100M judgment allowance; no measured equivalent',
        cache_hit_rate_not_guaranteed=True,worker_count=2,operational_time_includes_reset_and_imbalance=True),
    phases=[dict(name='mechanism24',executions=96,tokens=mechanism_tokens,hours=[1,3]),
            dict(name='main100_high',executions=400,tokens=main_tokens,hours=[12,20]),
            dict(name='budget13_legacy_extra',executions=52,tokens=budget_tokens,hours=[1,2])],
    core=dict(executions=548,tokens=core_tokens,hours=[14,25],
        planning_token_reserve=1_100_000_000,
        warning='Token reserve is proposed, not a hard guaranteed cap; in-flight usage can overshoot.'),
    optional_validation60=dict(executions=240,tokens=[200_000_000,350_000_000],hours=[6,10],
        caveat='Existing stage validation split has prior baseline/selection exposure; not virgin holdout.'),
    with_optional_validation=dict(executions=788,tokens=[core_tokens[0]+200_000_000,core_tokens[1]+350_000_000],hours=[20,35]),
    timeout_stress=dict(core_548_at_500s_ideal_two_worker_hours=548*500/2/3600,
        note='38.1h task-window workload only; drain/startup/service outage not included; not a hard completion bound.'))
save('budget.json',budget)
save('design.proposed.json',dict(status='proposal_not_launch_ready',date='2026-10-16',seed=SEED,
    purpose='Independent incremental value of continuing locally repairable failures versus handing back to model',
    arms=['applicability','failure'], model='deepseek-flash',temperature=0.1,workers=2,
    time_budget_seconds=500,budgets=dict(high=dict(max_iterations=600,recursion_limit=1800),legacy=dict(max_iterations=150,recursion_limit=450)),
    shared_skill_budgets=dict(api_calls_per_plan=2000,local_repairs=16,search_rounds=16),
    sources=dict(fast100_sha256=digest(fast_path),eligibility_sha256=digest(elig_path)),
    phases=dict(mechanism=96,main100=400,budget13_extra=52,optional_validation60=240),
    launch_blockers=['Materialize and validate complete canonical task snapshots and public startup state',
                    'Deterministic exogenous simulation timeline anchored to task start',
                    'Configurable/logged graph and tool-step limits in isolated v2 adapter',
                    'Complete control rejection, handoff, actual resume and partial-usage logs',
                    'Offline state isolation, checker parity and boundary regression pass',
                    'Freeze runtime, fixtures, order, analysis and budget before authorized launch'],
    online_model_calls_during_planning=0))
print(json.dumps(dict(schedule_counts=dict(counts),core_budget=budget['core'],baseline=budget['main400_unadjusted']),ensure_ascii=False,indent=2))
