"""Recompute the SFT validation table from the frozen per-task scores (Python 3.11+)."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIELDS = (
    'checkpoint', 'context_tokens', 'generation_tokens', 'episode_tokens',
    'formal_passes', 'tasks', 'formal_percent', 'checker_passes',
    'passed_leaf_checks', 'leaf_checks', 'adapter_sha256', 'pairs_file',
)


def load(path):
    return json.loads(path.read_bytes())


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows_from(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def recompute():
    first = HERE.parent / 'student-validation-60/pairs.csv'
    first_records = rows_from(first)
    order = [(r['session_id'], r['ordinal'], r['task_id']) for r in first_records]
    require(len(set(order)) == len(order) == 60, 'A60 task identity')
    selection = load(HERE / 'continuation/selection/summary.json')
    require(digest(first) == selection['historical_pairs_sha256'], 'historical control digest')
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(FIELDS)

    def record(name, pairs, prefix, provider, expected):
        rows = rows_from(pairs)
        require(order == [(r['session_id'], r['ordinal'], r['task_id']) for r in rows], str(pairs))
        for row in rows:
            for field in ('pass', 'checker'):
                require(row[prefix + '_' + field] in ('True', 'False'), str(pairs))
        formal = sum(r[prefix + '_pass'] == 'True' for r in rows)
        checker = sum(r[prefix + '_checker'] == 'True' for r in rows)
        leaves = sum(int(r[prefix + '_passed_checks']) for r in rows)
        n = sum(int(r['checks']) for r in rows)
        require(n == 485 and (formal, checker) == expected, f'{pairs} {prefix}: scoring')
        metadata = load(provider)
        fingerprint = metadata.get('adapter_sha256')
        if name != 'base':
            adapter = ROOT / 'artifacts/adapters' / name
            require(fingerprint == load(adapter / 'adapter_config.json')['weights_sha256']
                    == digest(adapter / 'adapter.safetensors'), f'{name}: weight fingerprint')
        else:
            require(not fingerprint, 'base has an adapter')
        budget = metadata['budgets']
        writer.writerow([name, budget['context_tokens'], budget['generation_tokens'],
                         budget['episode_tokens'] or 'unlimited', formal, len(rows),
                         f'{100 * formal / len(rows):.2f}', checker, leaves, n,
                         fingerprint or '', pairs.relative_to(ROOT)])
        return formal, checker, leaves

    for arm, name in [('base', 'base'), ('sft', 'first-pass')]:
        entry = selection['base'] if arm == 'base' else selection['checkpoints'][0]
        record(name, first, arm, HERE / 'first-pass-validation' / arm / 'provider.json',
               (entry['formal_passes'], entry['original_checker_passes']))
    for n in range(1, 6):
        stage = HERE / f'continuation/validation/epoch-{n}'
        entry = selection['checkpoints'][n]
        totals = record(f'continuation-{n}', stage / 'pairs.csv', 'candidate',
                        stage / 'sft/provider.json', (entry['formal_passes'], entry['original_checker_passes']))
        require(totals[2] == entry['passed_leaf_checks'], str(stage))
    for protocol in ('32k-2048', '64k-2048', '64k-4096'):
        stage = HERE / 'budget-validation' / protocol
        summary = load(stage / 'summary.json')
        for arm, name in [('base', 'base'), ('sft', 'continuation-1')]:
            record(name, stage / 'pairs.csv', arm, stage / arm / 'provider.json',
                   (summary['arms'][arm], summary['original_checker_only']['arms'][arm]))
    return output.getvalue()


def training_table():
    first = HERE.parent / 'sft-a100-first-pass/epoch-1'
    continuation = HERE / 'continuation'
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['checkpoint', 'cumulative_epochs', 'data_pack', 'unique_decisions', 'steps',
                     'optimizer_updates', 'supervised_tokens', 'weighted_training_loss',
                     'training_step_seconds', 'peak_allocated_gib', 'adapter_sha256', 'step_log'])
    for folder, pack, epochs in [(first, 'qwen35-2b-first-pass', 1),
                                 (continuation, 'qwen35-2b-formal-mix', 5)]:
        data = load(ROOT / 'datasets/sft' / pack / 'manifest.json')
        steps_path = folder / 'steps.jsonl'
        steps = [json.loads(line) for line in steps_path.read_text().splitlines()]
        require([s['step'] for s in steps] == list(range(1, len(steps) + 1)), str(steps_path))
        for epoch in range(1, epochs + 1):
            summary = load((folder if epochs == 1 else folder / f'epoch-{epoch}') / 'summary.json')
            rows = [s for s in steps if s.get('epoch', 1) == epoch]
            tokens = sum(s['supervised_tokens'] for s in rows)
            updates = sum(s.get('update_applied', True) for s in rows)
            loss = sum(s['supervised_tokens'] * s['loss'] for s in rows) / tokens
            require(len(rows) == summary['steps'] == data['counts']['decisions']
                    and tokens == summary['supervised_tokens'] == data['counts']['supervised_tokens']
                    and updates == summary.get('optimizer_updates', summary['steps'])
                    and abs(loss - summary['mean_loss_per_supervised_token']) < 1e-10, str(folder))
            name = 'first-pass' if epochs == 1 else f'continuation-{epoch}'
            fingerprint = summary['adapter']['weights_sha256']
            require(fingerprint == load(ROOT / 'artifacts/adapters' / name /
                                        'adapter_config.json')['weights_sha256'], name)
            writer.writerow([name, 1 if epochs == 1 else 1 + epoch, pack,
                             data.get('unique_counts', data['counts'])['decisions'], len(rows),
                             updates, tokens, f'{loss:.6f}', f"{summary['training_step_seconds']:.2f}",
                             f"{summary['peak_allocated_gib']:.2f}", fingerprint, steps_path.relative_to(ROOT)])
    return output.getvalue()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', choices=('training', 'validation'), help='emit the specified CSV to stdout')
    mode = parser.parse_args().csv
    tables = {'training': training_table, 'validation': recompute}
    if mode:
        print(tables[mode](), end='')
    else:
        for name, table in tables.items():
            require(table().encode() == (HERE / f'{name}.csv').read_bytes(), f'{name}.csv differs')
        print('Verified six training epochs, 13 A60 rows, task order, denominators and adapter fingerprints.')
