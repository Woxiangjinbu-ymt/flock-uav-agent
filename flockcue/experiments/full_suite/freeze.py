"""Stage the verified v2 base; freeze only after full100 offline regression."""
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[3] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import json
import os
import shutil
import subprocess
import time
from harness_v2_world import sha, write_json


def stage(study, workspace):
    if (study / 'manifest.json').exists():
        raise ValueError('Already frozen')
    source = workspace / 'experiments/minimal-harness-v2'
    original = json.loads((source / 'manifest.json').read_text())
    runtime = study / 'frozen/runtime'
    for name, expected in original['files'].items():
        if not name.startswith('frozen/runtime/'):
            continue
        if sha((source / name).read_bytes()) != expected:
            raise ValueError('v2 frozen source changed: ' + name)
        target = study / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    additions = {f"scripts/{path.name}": path
                 for folder in ("scripts", "compat/scripts")
                 for path in (workspace / folder).glob("harness_full100_*.py")}
    test = "scripts/tests/test_harness_full100.py"
    additions[test] = workspace / test
    for name, path in additions.items():
        if path.exists():
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for component in ('agent4drone', 'server', 'controller'):
        env = runtime / component / '.venv'
        if not env.exists():
            env.symlink_to(workspace / component / '.venv', target_is_directory=True)
    return runtime


def freeze(study, workspace):
    runtime = stage(study, workspace)
    preflight = json.loads((study / 'fixture-preflight.json').read_text())
    assert preflight['passed'] and preflight['tasks'] == 100 and preflight['checks'] == 605
    tests = ['scripts/tests/test_harness_full100.py', 'scripts/tests/test_harness_v2.py',
             'agent4drone/tests/test_minimal_harness.py', 'agent4drone/tests/test_task_evidence_r2.py',
             'agent4drone/tests/test_task_progress_integration.py', 'scripts/tests/test_minimal_harness_runtime.py',
             'scripts/tests/test_reproduction_metrics.py']
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYGAME_HIDE_SUPPORT_PROMPT': '1',
           'FULL100_STUDY': str(study), 'FULL100_PLAN': str(workspace / 'experiments/minimal-harness-full100-plan')}
    result = subprocess.run([str(workspace / 'agent4drone/.venv/bin/python'), '-m', 'pytest', '-q',
                             '-p', 'no:cacheprovider', *tests], cwd=runtime, env=env, capture_output=True, text=True)
    (study / 'frozen/regression.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError('Frozen regression failed; inspect frozen/regression.log')
    for component in ('agent4drone', 'server', 'controller'):
        result = subprocess.run([str(runtime / component / '.venv/bin/python'), '-c',
            "import importlib.metadata as m; print('\\n'.join(sorted(d.metadata['Name']+'=='+d.version for d in m.distributions())))"],
            capture_output=True, text=True, check=True)
        (study / 'frozen' / f'{component}-dependencies.txt').write_text(result.stdout)
    schedule = json.loads((study / 'schedule.json').read_text())
    selected = [b for b in schedule['blocks'] if b['cost_calibration_block']]
    ids = [t['trial_id'] for b in selected for t in b['trials']]
    assert len(ids) == len(set(ids)) == 40
    files = [p for folder in ('fixtures', 'frozen') for p in (study / folder).rglob('*')
             if p.is_file() and '.venv' not in p.parts and '__pycache__' not in p.parts]
    files += [study / n for n in ('schedule.json', 'tasks.json', 'fixture-preflight.json', 'PROTOCOL_CN.md')]
    manifest = {'status': 'frozen_calibration_ready', 'frozen_unix': time.time(),
        'base_v2_manifest_sha256': sha((workspace / 'experiments/minimal-harness-v2/manifest.json').read_bytes()),
        'authorized_executions': 40, 'authorized_trial_ids': ids, 'remaining_authorized': False,
        'tasks_prepared': 100, 'model': 'deepseek-flash', 'temperature': 0.1,
        'files': {str(p.relative_to(study)): sha(p.read_bytes()) for p in sorted(files)}}
    write_json(study / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['stage', 'freeze'])
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--workspace', type=Path, required=True)
    a = p.parse_args()
    result = stage(a.study.resolve(), a.workspace.resolve()) if a.action == 'stage' else freeze(a.study.resolve(), a.workspace.resolve())
    print(json.dumps({'action': a.action, 'done': True, 'provider_calls': 0}))
