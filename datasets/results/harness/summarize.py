"""Recompute same-task comparisons from the released first-pass records."""
import argparse
import csv
import gzip
import json
from collections import Counter
from dataclasses import dataclass
from io import StringIO
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent
QWEN = 'Agent4Drone / qwen3.8-flash'
DEEPSEEK = 'Agent4Drone / deepseek-flash'
FLOCK = 'FlockCue / deepseek-flash'


def read(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def indexed(rows, expected):
    result = {(row['session_id'], row['task_id']): row for row in rows}
    if len(result) != len(rows) or len(result) != expected:
        raise ValueError(f'Expected {expected} unique scene/task pairs')
    scene_sizes = Counter(scene for scene, task in result)
    if any(size != 20 for size in scene_sizes.values()):
        raise ValueError('Expected complete 20-task scenes')
    return result


def passed(value):
    return {'True': True, 'False': False}[value]


@dataclass
class Cohort:
    name: str
    tasks: dict
    outcomes: dict


def load_runs():
    full = read(RESULTS / 'benchmark-1500/published-tasks.csv.gz')
    models = {}
    for label, key in [(QWEN, 'qwen'), (DEEPSEEK, 'deepseek')]:
        first = [r for r in full if r['model'] == key and r['cohort'] == 'first_attempt_no_replay']
        models[label] = indexed(first, 1500)
    deep = models[DEEPSEEK]
    core = indexed(read(RESULTS / 'benchmark-600/tasks.csv'), 600)
    repeat = indexed(read(RESULTS / 'paired-200/pairs.csv'), 200)
    validation = indexed(read(RESULTS / 'student-validation-60/pairs.csv'), 60)
    if not (models[QWEN].keys() == deep.keys() and core.keys() <= deep.keys()
            and repeat.keys() <= core.keys() and validation.keys() <= core.keys()):
        raise ValueError('Task sets disagree')
    for k, r in core.items():
        if r['historical_pass'] != deep[k]['task_pass']:
            raise ValueError(f'Core control mismatch: {k}')
    for k, r in repeat.items():
        if (r['october_agent4drone'], r['day_flockcue']) != (deep[k]['task_pass'], core[k]['task_pass']):
            raise ValueError(f'200-task projection mismatch: {k}')
    outcomes = {label: {k: passed(r['task_pass']) for k, r in rows.items()} for label, rows in models.items()}
    core_control = dict(outcomes[DEEPSEEK])
    core_control.update({k: passed(r['evening_agent4drone']) for k, r in repeat.items()})
    outcomes[FLOCK] = {k: passed(r['task_pass']) for k, r in core.items()}
    audit = json.loads((RESULTS.parent / 'teacher/2026-11-15-expansion/audit.json').read_text())
    expansion = indexed([dict(r, session_id=r['scene_id']) for r in audit['episodes']], 60)
    if core.keys() & expansion.keys() or not expansion.keys() <= deep.keys():
        raise ValueError('Unexpected teacher expansion membership')
    outcomes[FLOCK].update({k: r['formal_pass'] for k, r in expansion.items()})
    core_outcomes = {**outcomes, DEEPSEEK: core_control}
    cohorts = [
        Cohort('full-1500', deep, {QWEN: outcomes[QWEN], DEEPSEEK: outcomes[DEEPSEEK]}),
        Cohort('core-600', core, core_outcomes),
        Cohort('internal-60', {key: core[key] for key in validation}, core_outcomes),
        Cohort('expansion-60', {key: deep[key] for key in expansion}, outcomes),
    ]
    return cohorts, repeat


def merged_table(core, repeat):
    out = StringIO(newline='')
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(['session_id', 'task_id', 'ordinal', 'family', 'scene_difficulty', 'fleet_size',
                     'agent4drone_qwen_pass', 'agent4drone_deepseek_pass', 'flockcue_deepseek_pass',
                     'agent4drone_deepseek_source'])
    for key, row in core.tasks.items():
        source = 'paired-200/pairs.csv' if key in repeat else 'benchmark-1500/tasks.csv'
        writer.writerow([*key, row['index'], row['family'], row['scene_difficulty'], row['fleet_size'],
                         *(core.outcomes[arm][key] for arm in (QWEN, DEEPSEEK, FLOCK)), source])
    return out.getvalue()


def comparisons(cohorts):
    out = StringIO(newline='')
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(['task_set', 'group_by', 'group', 'reference', 'candidate', 'tasks', 'reference_pass',
                     'candidate_pass', 'reference_pct', 'candidate_pct', 'net_tasks', 'delta_pp',
                     'both_pass', 'gained', 'regressed', 'both_fail'])
    for cohort in cohorts:
        groups = {('overall', 'all'): list(cohort.tasks)}
        for key, task in cohort.tasks.items():
            for dimension in ('family', 'scene_difficulty', 'session_id', 'fleet_size'):
                if dimension in task:
                    groups.setdefault((dimension, task[dimension]), []).append(key)
        for (dimension, group), keys in sorted(groups.items()):
            for ref, candidate in combinations(cohort.outcomes, 2):
                counts = Counter((cohort.outcomes[ref][k], cohort.outcomes[candidate][k]) for k in keys)
                both, gain = counts[True, True], counts[False, True]
                loss, neither = counts[True, False], counts[False, False]
                n = len(keys)
                writer.writerow([cohort.name, dimension, group, ref, candidate, n, both + loss, both + gain,
                                 f'{100 * (both + loss) / n:.2f}', f'{100 * (both + gain) / n:.2f}', gain - loss,
                                 f'{100 * (gain - loss) / n:.2f}', both, gain, loss, neither])
    return out.getvalue()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', choices=['comparisons', 'core-600'], help='print a generated table')
    selected = parser.parse_args().csv
    cohorts, repeat = load_runs()
    core = next(cohort for cohort in cohorts if cohort.name == 'core-600')
    tables = {'comparisons': comparisons(cohorts), 'core-600': merged_table(core, repeat)}
    if selected:
        print(tables[selected], end='')
    else:
        for name, table in tables.items():
            if table.encode() != (HERE / f'{name}.csv').read_bytes():
                raise SystemExit(f'{name}.csv differs from per-task results')
        print('Merged 600 tasks and all Harness comparisons match the per-task records.')
