"""Build 100 canonical task snapshots and check all original scoring leaves offline."""
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import argparse
import copy
import json
import pickle

from harness_v2_world import ROOT, EPOCH, digest, sha, write_json
from harness_full100_world import FullWorld


def prepare(study, plan, workspace):
    if (study / 'manifest.json').exists():
        raise ValueError('Cannot modify frozen fixtures')
    catalog = json.loads((plan / 'tasks.proposed.json').read_text())
    fixtures = study / 'fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    reports = []
    for scene in catalog['scenes']:
        source = workspace / scene['source']
        assert sha(source.read_bytes()) == scene['sha256']
        raw = json.loads(source.read_text())
        # Source metadata is preserved. Task-relative simulator time, weather RNG
        # and private controller timers start from one shared canonical epoch.
        world = FullWorld(seed=2026101602 ^ int(scene['id'], 16))
        world.import_session(raw)
        blob = world.snapshot()
        state_hash = world.state_digest()
        timeline = world.bake_timeline(501)
        assert world.state_digest() == state_hash
        timeline_path = fixtures / f"scene-{scene['id']}.timeline.pkl"
        timeline_path.write_bytes(timeline)
        public = world.public_seed()
        world.restore(blob)
        tasks = [t for t in catalog['tasks'] if t['scene_id'] == scene['id']]
        for t in tasks:
            ident = t['task_id']
            original = next(x for x in raw['tasks'] if x['id'] == ident)
            assert sha(original['content'].encode()) == t['command_sha256']
            def flatten(node, path='root'):
                if node.get('checks'):
                    return [x for i, child in enumerate(node['checks']) for x in flatten(child, path + f'.{i}')]
                return [{'path': path, 'definition': node}]
            assert flatten(original['execution_check_apis']) == t['checks']
            initial = world.evaluate(ident, blob)
            assert initial['checks_expected'] == t['n_checks'] and not initial['errors']
            (fixtures / f'{ident}.pkl').write_bytes(blob)
            for _ in range(4):
                world.restore(blob, sha(blob))
                assert world.state_digest() == state_hash
                assert digest(world.public_seed()) == digest(public)
                assert world.evaluate(ident, blob) == initial
                world.server.session_controller.get_current_session_ref().request_history.append({'contamination': True})
                first = next(iter(world.server.drone_controller.drones.values()))
                first.history_status.append({'dirty': True})
                first.battery_volume = 0
                world.rng.random(); world.id_rng.random()
                world.advance(19)
            world.restore(blob)
            fixture = {**t, 'original_task': original, 'state_status': 'materialized',
                'snapshot_sha256': sha(blob), 'state_sha256': state_hash,
                'public_startup_sha256': digest(public), 'timeline_file': timeline_path.name,
                'timeline_sha256': sha(timeline), 'initial_checker': initial,
                'preparation_script': 'none; original scene import, no landing/charging/target visits',
                'original_source_metadata_preserved': True, 'task_clock_epoch': EPOCH}
            write_json(fixtures / f'{ident}.json', fixture)
            reports.append({'task_id': ident, 'scene_id': scene['id'], 'checks': t['n_checks'],
                'initial_passed': initial['passed'], 'initial_checks_passed': initial['checks_passed'],
                'restore_checks': 4, 'private_mutation_discarded': True, 'passed': True})
        print(json.dumps({'scene': scene['id'], 'snapshots': len(reports)}), flush=True)
    schedule = json.loads((plan / 'schedule.proposed.json').read_text())
    schedule.update(status='prepared_calibration_only', authorized_executions=40,
                    remaining_executions_authorized=False)
    write_json(study / 'schedule.json', schedule)
    write_json(study / 'tasks.json', catalog)
    report = {'passed': True, 'tasks': len(reports), 'checks': sum(r['checks'] for r in reports),
        'restores': 4 * len(reports), 'provider_calls': 0, 'rows': reports,
        'fully_satisfied_initially': [r['task_id'] for r in reports if r['initial_passed']]}
    assert report['tasks'] == 100 and report['checks'] == 605
    write_json(study / 'fixture-preflight.json', report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--workspace', type=Path, required=True)
    a = p.parse_args()
    prepare(a.study.resolve(), a.plan.resolve(), a.workspace.resolve())
