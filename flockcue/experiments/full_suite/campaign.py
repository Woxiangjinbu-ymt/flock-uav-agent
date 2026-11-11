"""Hard-scoped first 20 pairs only. No resume command and no remaining-study dispatch."""
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import subprocess
import threading
import time
from harness_v2_world import sha, write_json


def read(path):
    return json.loads(Path(path).read_text())


def calibration_blocks(schedule):
    blocks = schedule['blocks']
    assert len(blocks) == 200 and sum(len(b['trials']) for b in blocks) == 400
    assert sorted({b['worker'] for b in blocks}) == [0, 1]
    selected = [b for b in blocks if b['cost_calibration_block']]
    assert len(selected) == 20
    assert all(b['repeat'] == 1 and b['rank_within_worker_repeat'] <= 10 for b in selected)
    assert len({t['trial_id'] for b in selected for t in b['trials']}) == 40
    for w in (0, 1):
        assert sorted(b['rank_within_worker_repeat'] for b in selected if b['worker'] == w) == list(range(1, 11))
    return selected


def verify(study):
    manifest = read(study / 'manifest.json')
    for name, value in manifest['files'].items():
        if sha((study / name).read_bytes()) != value:
            raise ValueError('Frozen artifact changed: ' + name)
    blocks = calibration_blocks(read(study / 'schedule.json'))
    assert manifest['authorized_executions'] == 40
    assert set(manifest['authorized_trial_ids']) == {t['trial_id'] for b in blocks for t in b['trials']}
    return blocks


def summarize(study):
    runs = read(study / 'runs.json') if (study / 'runs.json').exists() else {}
    results, valid = [], True
    for ident, run in runs.items():
        if run['status'] != 'completed':
            continue
        output = study / 'runs' / ident
        seal = read(output / 'seal.json')
        expected_files = {str(p.relative_to(output)) for p in output.rglob('*') if p.is_file() and p.name != 'seal.json'}
        valid &= expected_files == set(seal['files']) and all(sha((output / n).read_bytes()) == v for n, v in seal['files'].items())
        results.append(read(output / 'result.json'))
    pairs = []
    for block in calibration_blocks(read(study / 'schedule.json')):
        group = {r['arm']: r for r in results if r['block_id'] == block['block_id']}
        if len(group) == 2:
            a, f = group['applicability'], group['failure']
            pairs.append({'block_id': block['block_id'],
                'same_initial_state': a['initial_state_sha256'] == f['initial_state_sha256'],
                'same_public_startup': a['public_startup_sha256'] == f['public_startup_sha256']})
    summary = {'updated_unix': time.time(), 'authorized': 40, 'remaining_unauthorized': 360,
        'completed': len(results), 'failed': sum(r['status'] == 'failed' for r in runs.values()),
        'running': sum(r['status'] == 'running' for r in runs.values()), 'seals_valid': valid,
        'actual_tokens': sum(r['usage']['total_tokens'] for r in results),
        'missing_usage_calls': sum(r['missing_usage_calls'] for r in results),
        'complete_pairs': len(pairs), 'pairs': pairs, 'results': results}
    write_json(study / 'summary.json', summary)
    return summary


def campaign(study, workspace):
    study, workspace = study.resolve(), workspace.resolve()
    lock = (study / 'campaign.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    blocks = verify(study)
    if (study / 'runs.json').exists() or (study / 'campaign.json').exists():
        raise ValueError('Existing attempt; no automatic rerun or resume')
    mutex, stop = threading.RLock(), threading.Event()
    runs = {}
    state = {'status': 'running_calibration_only', 'pid': os.getpid(), 'started_unix': time.time(),
        'authorized_executions': 40, 'remaining_unauthorized': 360, 'workers': [0, 1],
        'auto_rerun': False, 'token_soft_stop': 1_000_000_000}
    for folder in ('logs', 'dispatch'):
        (study / folder).mkdir(exist_ok=True)
    write_json(study / 'campaign.json', state)
    write_json(study / 'runs.json', runs)

    def worker(worker_id):
        queue = sorted([b for b in blocks if b['worker'] == worker_id], key=lambda b: b['rank_within_worker_repeat'])
        try:
            for block in queue:
                with mutex:
                    if stop.is_set():
                        return
                    if sum(r.get('total_tokens', 0) for r in runs.values()) >= state['token_soft_stop']:
                        state.update(status='paused_token_soft_stop', stop_reason='No new pair admitted')
                        stop.set()
                        return
                for arm in block['trials']:
                    with mutex:
                        if stop.is_set():
                            return
                        ident = arm['trial_id']
                        if ident in runs:
                            raise ValueError('Duplicate trial refused')
                        trial = {**arm, 'task_id': block['task_id'], 'scene_id': block['scene_id'],
                            'repeat': block['repeat'], 'worker': worker_id, 'block_id': block['block_id'],
                            'rank_within_worker_repeat': block['rank_within_worker_repeat'],
                            'status': 'running', 'started_unix': time.time()}
                        runs[ident] = trial
                        write_json(study / 'runs.json', runs)
                        dispatch = study / 'dispatch' / f'{ident}.json'
                        write_json(dispatch, trial)
                    cmd = [str(workspace / 'agent4drone/.venv/bin/python'),
                           str(study / 'frozen/runtime/scripts/harness_full100_online.py'),
                           '--study', str(study), '--trial', str(dispatch), '--output', str(study / 'runs' / ident),
                           '--workspace', str(workspace)]
                    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
                           'PYGAME_HIDE_SUPPORT_PROMPT': '1', 'MPLBACKEND': 'Agg',
                           'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false'}
                    with (study / 'logs' / f'{ident}.log').open('x') as logfile:
                        process = subprocess.Popen(cmd, cwd=study / 'logs', env=env, stdout=logfile, stderr=subprocess.STDOUT)
                        with mutex:
                            trial['pid'] = process.pid
                            write_json(study / 'runs.json', runs)
                        try:
                            code = process.wait(timeout=630)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill(); process.wait()
                            code = -999
                    with mutex:
                        trial.update(exit_code=code, ended_unix=time.time(), status='failed')
                        output = study / 'runs' / ident
                        if code == 0 and (output / 'seal.json').exists():
                            result = read(output / 'result.json')
                            seal = read(output / 'seal.json')
                            if result['status'] == 'completed' and all(sha((output / n).read_bytes()) == v for n, v in seal['files'].items()):
                                trial.update(status='completed', total_tokens=result['usage']['total_tokens'])
                        if trial['status'] != 'completed':
                            if output.exists():
                                write_json(output / 'failure-seal.json', {'files': {
                                    str(p.relative_to(output)): sha(p.read_bytes()) for p in output.rglob('*') if p.is_file()}})
                            state.update(status='failed', stop_reason=f'First attempt failed: {ident}')
                            stop.set()
                        write_json(study / 'runs.json', runs)
                        write_json(study / 'campaign.json', state)
                        report = summarize(study)
                        print(json.dumps({'trial': ident, 'status': trial['status'],
                            'completed': report['completed'], 'tokens': report['actual_tokens']}), flush=True)
        except Exception as exc:
            with mutex:
                state.update(status='failed', stop_reason=f'{type(exc).__name__}: {exc}')
                stop.set()
                write_json(study / 'campaign.json', state)
            raise

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, w) for w in (0, 1)]
            for f in futures:
                f.result()
    finally:
        report = summarize(study)
        if report['completed'] == 40 and report['seals_valid'] and all(p['same_initial_state'] for p in report['pairs']):
            state['status'] = 'calibration_complete_paused_pending_user_decision'
        elif state['status'] == 'running_calibration_only':
            state.update(status='failed', stop_reason='Incomplete calibration')
        state['ended_unix'] = time.time()
        write_json(study / 'campaign.json', state)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['run', 'summarize', 'verify'])
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--workspace', type=Path)
    a = p.parse_args()
    if a.action == 'run':
        campaign(a.study, a.workspace)
    elif a.action == 'verify':
        print(json.dumps({'authorized_pairs': len(verify(a.study))}))
    else:
        r = summarize(a.study)
        print(json.dumps({k: r[k] for k in ('authorized', 'completed', 'failed', 'running', 'actual_tokens', 'seals_valid')}))
