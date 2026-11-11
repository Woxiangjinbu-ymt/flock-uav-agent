"""One-time evidence-based repair of interrupted full round 1.

Uses the frozen, unchanged runner and original task/check protocol. Only scenes
with recorded terminal provider failures or incomplete execution are replayed.
Preserves all old scene seals/artifacts and a before-repair aggregate snapshot.
"""
import collections
import copy
import hashlib
import json
from pathlib import Path
import shutil
import signal
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import reproduction as r
import reproduction_parallel as rp

ATTEMPT = ROOT / 'experiments/runs/20261003-143255-full-5c501118'
AUDIT = ATTEMPT / 'recovery-audit/0001'

def classify(plan, manifest):
    targets = {}
    for selected in plan['sessions']:
        sid = selected['id']
        record = manifest['scenes'][sid]
        directory = rp.safe_directory(ATTEMPT, record['directory'])
        errors = []
        for line in (directory / 'agent-traces/reproduction-metrics.jsonl').open():
            event = json.loads(line)
            if event.get('event') == 'llm_error' and event.get('status_code') in (402, 429):
                errors.append({k: event.get(k) for k in ('event', 'timestamp', 'job_id', 'status_code', 'error_type')})
        if errors or record['status'] != 'completed':
            targets[sid] = {'name': selected['name'], 'previous_directory': record['directory'],
                            'previous_status': record['status'], 'terminal_provider_errors': errors,
                            'reason': 'terminal provider failure; preserve whole-scene state by replay' if errors else record.get('error'),
                            'replay_tasks': len(selected['tasks'])}
    return targets

def inspect():
    manifest = r.read_json(ATTEMPT / 'manifest.json')
    plan = r.read_json(ATTEMPT / 'plan.json')
    settings, _, key = r.selected_config()
    rp.verify_provenance(r, ATTEMPT, plan, settings)
    rows = {}
    for selected in plan['sessions']:
        record = manifest['scenes'][selected['id']]
        directory = rp.safe_directory(ATTEMPT, record['directory'])
        if record['status'] == 'completed':
            rows[selected['id']] = rp.verify_scene(r, ATTEMPT, plan, selected, record)
        else:
            rows[selected['id']] = r.read_json(directory / 'results.json')
    return manifest, plan, settings, key, rows

def run():
    with rp.runner_lock(r), rp.attempt_lock(ATTEMPT):
        if AUDIT.exists():
            raise ValueError('Recovery audit already exists; inspect state and use ordinary resume for incomplete generations')
        manifest, plan, settings, key, rows = inspect()
        targets = classify(plan, manifest)
        # Freeze the observed failure set before any new results exist. No score-based selection.
        expected = {'ae503ad1', 'ec1f47f4', '35ce64d8', 'd23eb117', 'dcae50c1', '6702871a', '1a9c1abc'}
        if set(targets) != expected or not key:
            raise ValueError('Evidence/credential differs from audited recovery plan')
        for sid in targets:
            rp.ensure_stopped(r, rp.safe_directory(ATTEMPT, manifest['scenes'][sid]['directory']))
        AUDIT.mkdir(parents=True, exist_ok=False)
        for name in ('manifest.json', 'results.json', 'summary.json', 'performance.json'):
            shutil.copy2(ATTEMPT / name, AUDIT / name)
        shutil.copy2(__file__, AUDIT / 'recovery_driver.py')
        audit = {'version': 1, 'created_at': rp.timestamp(), 'reason': 'user-authorized recovery after exhausted balance and interrupted scene',
                 'selection_rule': 'all terminal HTTP 402/429 LLM failures plus all incomplete scenes, independent of task pass/fail',
                 'targets': targets, 'preserved_scenes': len(plan['sessions']) - len(targets),
                 'replay_tasks': sum(v['replay_tasks'] for v in targets.values()),
                 'frozen_protocol_unchanged': True, 'frozen_source_unchanged': True,
                 'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 'original_aggregate_sha256': {name: hashlib.sha256((AUDIT/name).read_bytes()).hexdigest() for name in ('manifest.json','results.json','summary.json','performance.json')}}
        r.save_json(AUDIT / 'recovery-plan.json', audit)
        manifest.setdefault('exceptional_recoveries', []).append({'audit': 'recovery-audit/0001/recovery-plan.json',
                                                                  'scene_ids': list(targets), 'created_at': audit['created_at']})
        for sid, evidence in targets.items():
            # Preserve existing marker and directory. coordinate creates a new generation
            # and records this old generation in history before selecting new results.
            manifest['scenes'][sid].update(status='aborted', error='Externally invalidated: '+evidence['reason'],
                                           invalidation_audit='recovery-audit/0001/recovery-plan.json')
        print(json.dumps({'replaying_scenes': list(targets), 'replaying_tasks': audit['replay_tasks'],
                          'preserved_scenes': audit['preserved_scenes']}, ensure_ascii=False), flush=True)
        return rp.coordinate(r, ATTEMPT, plan, settings, key, manifest, rows)

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('SIGTERM')))
    if len(sys.argv) != 2 or sys.argv[1] != 'run':
        raise SystemExit('Usage: controller/.venv/bin/python experiments/recovery/round1_repair.py run')
    raise SystemExit(0 if run() else 2)
