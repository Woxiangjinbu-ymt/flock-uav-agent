"""Prepare reviewable full100 design and empirical budget; no runtime/model imports."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import statistics

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
SEED = 2026101602
ARMS = ('applicability', 'failure')


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


frozen = ROOT / 'experiments/minimal-harness-v1/frozen'
fast = read(frozen / 'fast100.json')
eligibility = {e['task_id']: e for e in read(frozen / 'eligibility.json')['entries']}
tasks, scenes = [], []
for scene in fast['sessions']:
    source = frozen / 'data/scenes' / scene['source_file']
    assert sha(source) == scene['sha256']
    raw = read(source)
    originals = {t['id']: t for t in raw['tasks']}
    scenes.append({'id': scene['id'], 'name': scene['name'], 'n_tasks': len(scene['tasks']),
                   'checks': sum(len(t['leaves']) for t in scene['tasks']),
                   'source': str(source.relative_to(ROOT)), 'sha256': sha(source),
                   'is_distance_3d': raw['is_distance_3d'],
                   'moving_targets': sum(bool(t.get('velocity') or t.get('moving_path')) for t in raw['targets'])})
    for task in scene['tasks']:
        command = originals[task['id']]['content']
        assert hashlib.sha256(command.encode()).hexdigest() == task['content_sha256']
        assert command == eligibility[task['id']]['command']
        # Same scene, command and checker are one effective problem even if IDs differ.
        cluster = value_sha({'scene': scene['id'], 'command': command, 'leaves': task['leaves']})
        tasks.append({'task_id': task['id'], 'name': task['name'], 'scene_id': scene['id'],
                      'command': command, 'command_sha256': task['content_sha256'],
                      'checks': task['leaves'], 'n_checks': len(task['leaves']),
                      'eligibility': eligibility[task['id']]['skill'], 'problem_cluster': cluster,
                      'source': str(source.relative_to(ROOT)), 'source_sha256': sha(source),
                      'snapshot_sha256': None, 'public_startup_sha256': None,
                      'timeline_sha256': None, 'state_status': 'not_materialized',
                      'initial_obligation_audit': 'pending_offline_preparation'})
assert len(tasks) == 100 and sum(t['n_checks'] for t in tasks) == 605
assert Counter(t['eligibility'] for t in tasks) == {'simple_chain': 7, 'public_search': 6, 'unsupported': 87}
clusters = defaultdict(list)
for t in tasks:
    clusters[t['problem_cluster']].append(t['task_id'])

# Exact scene x worker x first-arm balance (5 per cell); near balance by skill.
rng = random.Random(SEED)
for draw in range(10000):
    allocations = {}
    for scene in scenes:
        group = [t for t in tasks if t['scene_id'] == scene['id']]
        rng.shuffle(group)
        cells = [(w, first) for w in (0, 1) for first in ARMS for _ in range(5)]
        rng.shuffle(cells)
        allocations.update({t['task_id']: cell for t, cell in zip(group, cells)})
    good = True
    for skill in ('simple_chain', 'public_search', 'unsupported'):
        subset = [t for t in tasks if t['eligibility'] == skill]
        for index, choices in ((0, (0, 1)), (1, ARMS)):
            counts = Counter(allocations[t['task_id']][index] for t in subset)
            good &= abs(counts[choices[0]] - counts[choices[1]]) <= 1
    if good:
        break
else:
    raise AssertionError('Could not balance proposed schedule')

blocks = []
for repeat in (1, 2):
    for scene in scenes:
        for t in [t for t in tasks if t['scene_id'] == scene['id']]:
            worker, first = allocations[t['task_id']]
            order = [first, next(a for a in ARMS if a != first)]
            if repeat == 2:
                worker, order = 1 - worker, order[::-1]
            blocks.append({'block_id': f"full100:{t['task_id']}:r{repeat}",
                           'task_id': t['task_id'], 'scene_id': t['scene_id'],
                           'repeat': repeat, 'worker': worker,
                           'trials': [{'trial_id': f"{t['task_id']}-r{repeat}-{a}",
                                       'arm': a, 'position': i + 1, 'budget': 'high'} for i, a in enumerate(order)],
                           'execution_status': 'not_launched'})
    for worker in (0, 1):
        # Two blocks per scene in each ten-block wave. First wave across workers
        # gives 20 preselected pairs for cost calibration, included in the 400.
        by_scene = {}
        for s in scenes:
            group = [b for b in blocks if b['repeat'] == repeat and b['worker'] == worker and b['scene_id'] == s['id']]
            rng.shuffle(group)
            by_scene[s['id']] = group
        ordered = []
        for wave in range(5):
            group = [b for s in scenes for b in by_scene[s['id']][wave * 2:wave * 2 + 2]]
            rng.shuffle(group)
            ordered.extend(group)
        for rank, b in enumerate(ordered, 1):
            b['rank_within_worker_repeat'] = rank
            b['cost_calibration_block'] = repeat == 1 and rank <= 10
blocks.sort(key=lambda b: (b['repeat'], b['rank_within_worker_repeat'], b['worker']))
assert len(blocks) == 200
assert len({t['trial_id'] for b in blocks for t in b['trials']}) == 400
assert sum(b['cost_calibration_block'] for b in blocks) == 20
for t in tasks:
    pair = [b for b in blocks if b['task_id'] == t['task_id']]
    assert len(pair) == 2 and pair[0]['worker'] != pair[1]['worker']
    assert [x['arm'] for x in pair[0]['trials']] == [x['arm'] for x in pair[1]['trials']][::-1]
for repeat in (1, 2):
    for scene in scenes:
        cells = Counter((b['worker'], b['trials'][0]['arm']) for b in blocks
                        if b['repeat'] == repeat and b['scene_id'] == scene['id'])
        assert set(cells.values()) == {5} and len(cells) == 4

source = ROOT / 'docs/results/minimal-harness-online-20261015/comparison.json'
comparison = read(source)
observed, arm_details, usage = {}, {}, Counter()
token_fields = ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cache_hit_tokens', 'cache_miss_tokens')
for arm in ARMS:
    a = next(a for a in comparison['arms'] if a['arm'] == 'same_skill_' + arm)
    command_to_task = {read(ROOT / t['trace'])['command_id']: t for t in a['tasks']}
    task_usage = defaultdict(Counter)
    for path in Path(a['attempt']).glob('*/agent-traces/reproduction-metrics.jsonl'):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event['event'] == 'llm_end':
                task_usage[command_to_task[event['command_id']]['task_id']].update(event['usage'])
    assert len(task_usage) == 100
    for key in token_fields:
        assert sum(v[key] for v in task_usage.values()) == a['observed_usage'][key]
        usage[key] += a['observed_usage'][key]
    for t in a['tasks']:
        observed[(t['task_id'], arm)] = {'usage': dict(task_usage[t['task_id']]),
            'calls': t['logical_model_requests_in_window'], 'ready_seconds': t['scoring_ready_seconds']}
    values = sorted(v['total_tokens'] for v in task_usage.values())
    arm_details[arm] = {'n': 100, 'usage': {k: a['observed_usage'][k] for k in token_fields},
        'tokens_median': statistics.median(values), 'tokens_p90_nearest_rank': values[89],
        'tokens_p95_nearest_rank': values[94], 'tokens_max': values[-1]}
base_calls = 2 * sum(v['calls'] for v in observed.values())
base_seconds = 2 * sum(v['ready_seconds'] for v in observed.values())
base_usage = {k: 2 * usage[k] for k in token_fields}
loads = {(repeat, worker): sum(observed[(b['task_id'], t['arm'])]['ready_seconds'] for b in blocks
          if b['repeat'] == repeat and b['worker'] == worker for t in b['trials'])
         for repeat in (1, 2) for worker in (0, 1)}
replayed_hours = sum(max(loads[(r, w)] for w in (0, 1)) for r in (1, 2)) / 3600
calibration = [b for b in blocks if b['cost_calibration_block']]
calibration_tokens = sum(observed[(b['task_id'], t['arm'])]['usage']['total_tokens'] for b in calibration for t in b['trials'])
calibration_hours = max(sum(observed[(b['task_id'], t['arm'])]['ready_seconds'] for b in calibration
                          if b['worker'] == w for t in b['trials']) for w in (0, 1)) / 3600
budget = {'status': 'estimate_not_spend_authorization', 'source': str(source.relative_to(ROOT)),
    'source_sha256': sha(source), 'v1_observed_200': {'usage': dict(usage), 'arms': arm_details},
    'full400_unadjusted': {'usage': base_usage, 'logical_calls': base_calls,
        'sum_ready_seconds': base_seconds, 'ideal_two_worker_hours': base_seconds / 7200,
        'proposed_schedule_replay_hours': replayed_hours,
        'worker_load_seconds': {f'r{r}-w{w}': seconds for (r, w), seconds in loads.items()}},
    'planning_scenarios': {'token_multiplier_vs_v1': [1.25, 2.0],
        'tokens': [round(base_usage['total_tokens'] * f) for f in (1.25, 2.0)],
        'online_hours': [12, 20], 'offline_preparation_workdays': [1, 2],
        'basis': 'Reset removes inherited completion; higher loop limits and service variance; assumptions, not confidence bounds'},
    'proposed_token_soft_stop': 1_000_000_000,
    'calibration_first20_pairs': {'executions': 40, 'v1_replayed_tokens': calibration_tokens,
        'tokens_scenario': [round(calibration_tokens * f) for f in (1.25, 2.0)],
        'v1_replayed_two_worker_hours': calibration_hours,
        'not_extra_experiments': True},
    'stress_workload_hours': {'400_at_500_seconds_two_workers': 400 * 500 / 7200,
        '400_at_630_seconds_two_workers': 400 * 630 / 7200,
        'note': 'Task slot workload only; not a calendar guarantee; overhead and interruptions excluded'},
    'v2_mechanism_cost_not_extrapolated': {'executions': 96, 'tokens': 7293646, 'wall_seconds': 1493.2765820026398}}
save('budget.json', budget)
save('tasks.proposed.json', {'status': 'not_materialized', 'tasks': tasks, 'scenes': scenes,
     'unique_problem_clusters': len(clusters), 'duplicate_problem_groups': [v for v in clusters.values() if len(v) > 1]})
save('schedule.proposed.json', {'status': 'not_launched', 'seed': SEED, 'workers': [0, 1],
     'repeats': 2, 'executions': 400, 'blocks': blocks,
     'repeat_barrier': True, 'calibration_barrier_after_rank': 10})
save('design.proposed.json', {'status': 'proposal_not_launch_ready', 'date': '2026-10-16',
     'arms': list(ARMS), 'tasks': 100, 'checks_per_arm_per_repeat': 605,
     'executions': 400, 'repeats': 2, 'model': 'deepseek-flash', 'temperature': 0.1,
     'forced_skill_commit': False, 'fault_injection': False,
     'budget': {'task_seconds': 500, 'tool_steps': 600, 'graph_recursion_limit': 1800,
                'api_per_plan': 2000, 'local_repairs': 16, 'search_rounds': 16,
                'sdk_retries': 0, 'empty_response_retries': 0, 'local_watchdog_seconds': 630},
     'time_zero': 'after restoration/hash/preflight, before first task-specific public observation or model call',
     'score_boundary': 'end of task or 500 seconds, whichever comes first; immutable snapshot before drain',
     'policy_changes': 'scheduler arm only; identical prompt, skills, compiler, mandatory guards and raw tools',
     'online_calls_during_planning': 0, 'launch_authorized': False,
     'source_sha256': {'fast100': sha(frozen / 'fast100.json'), 'eligibility': sha(frozen / 'eligibility.json')},
     'exclusions': ['new mechanism runs', 'legacy budget supplement', 'validation60', 'R2', 'every_step'],
     'launch_blockers': ['100 canonical full snapshots and preconditions audited',
         'natural skill selection adapter without bootstrap and prompt additions',
         'full original checker parity and task lifecycle at strict time boundary',
         'append-only physical segment/action/energy telemetry without policy changes',
         '400-entry queue dry run and 0/1 worker/repeat/calibration barriers',
         'frozen source/config/order/scoring/usage verification and explicit launch decision']})
save('validation.json', {'passed': True, 'scope': 'planning artifacts only; no simulator or model execution',
     'unique_tasks': 100, 'checks_per_arm_per_repeat': 605, 'unique_trial_ids': 400,
     'balanced_scene_worker_arm_first_cells': 5, 'every_task_reverses_arm_order_and_worker': True,
     'near_balanced_first_arm_and_worker_within_skill': True,
     'calibration_pairs': 20, 'unique_problem_clusters': len(clusters),
     'duplicate_problem_groups': [v for v in clusters.values() if len(v) > 1],
     'file_sha256': {name: sha(OUT / name) for name in
                    ('budget.json', 'tasks.proposed.json', 'schedule.proposed.json', 'design.proposed.json')}})
print(json.dumps({'executions': 400, 'unique_problem_clusters': len(clusters),
    'duplicates': [v for v in clusters.values() if len(v) > 1],
    'budget': budget['planning_scenarios'], 'schedule_replay_hours': replayed_hours,
    'calibration': budget['calibration_first20_pairs']}, ensure_ascii=False, indent=2))
