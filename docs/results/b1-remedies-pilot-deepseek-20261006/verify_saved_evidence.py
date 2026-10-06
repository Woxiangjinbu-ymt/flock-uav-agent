"""Read-only validation of saved scores after interruption; no live API calls."""
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path('.')
ATTEMPT = ROOT / 'experiments/runs/20261006-134416-b1-remedies60-1ee84ea7'
RUNTIME = ATTEMPT / 'runtime'
sys.path.insert(0, str(RUNTIME / 'scripts'))
import reproduction as r
import reproduction_parallel as rp

r.ROOT, r.DATA = RUNTIME, RUNTIME / 'data'
def inventory():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ATTEMPT.rglob('*')) if p.is_file() and not p.is_symlink()}

before = inventory()
manifest = r.read_json(ATTEMPT / 'manifest.json')
plan = r.read_json(ATTEMPT / 'plan.json')
rp.verify_archive(r, ATTEMPT)
out = {'mode': 'interrupted_saved_evidence_audit', 'is_final': False,
       'archive_identity_verified': True, 'suite_seal_present': (ATTEMPT / 'suite-seal.json').exists(),
       'completed_scenes_verified': [], 'scored_tasks': 0, 'passed_tasks': 0,
       'saved_checks_verified': 0, 'tasks': [],
       'note': 'Only original saved check responses are evaluated offline. No live world query, new score, service, recovery or model call.'}
for selected in plan['sessions']:
    sid = selected['id']
    record = manifest['scenes'][sid]
    assert record['generation'] == 1
    directory = ATTEMPT / record['directory']
    if record['status'] == 'completed':
        rp.verify_scene(r, ATTEMPT, plan, selected, record)
        out['completed_scenes_verified'].append(sid)
    source_path = r.DATA / selected['file']
    assert r.digest(source_path.read_bytes()) == selected['sha256']
    tasks = {task['id']: task for task in r.read_json(source_path)['tasks']}
    for row in r.read_json(directory / 'results.json'):
        if row['status'] != 'scored':
            continue
        prefix = directory / row['task_id']
        evidence = {suffix: Path(str(prefix) + '.' + suffix + '.json') for suffix in rp.REQUIRED_SUFFIXES}
        assert all(path.is_file() for path in evidence.values())
        window, barrier, worker = [r.read_json(evidence[key]) for key in ('window', 'barrier', 'agent-state')]
        assert barrier['quiescent'] and barrier['inflight'] == 0 and barrier['simulation_paused']
        assert barrier['lease'] == window['lease'] and barrier['cutoff_at'] == window['cutoff_at']
        assert worker['worker_finished'] and worker['busy'] is False and worker['job_id'] == row['job_id']
        assert row['continuation_safe'] is True
        if row['agent_timed_out']:
            assert Path(str(prefix) + '.timeout.json').is_file()
        leaves = [d for d in r.read_json(evidence['checks']) if d.get('type') == 'leaf']
        remaining = iter(leaves)
        class SavedAPI:
            def api_check_execution(self, endpoint, parameters, show_error=False):
                leaf = next(remaining)
                assert endpoint == leaf['endpoint']
                assert parameters == {**leaf['parameters'], 'session_id': sid}
                return leaf['response']
        passed, checked = r.upstream_evaluate(SavedAPI(), tasks[row['task_id']]['execution_check_apis'], sid)
        actual = [d for d in checked if d.get('type') == 'leaf']
        assert not any(d.get('error') for d in checked)
        assert passed == row['passed'] == row['task_pass']
        assert len(actual) == len(leaves) == row['expected_checks']
        assert [(d.get('result'), d.get('actual')) for d in actual] == [(d.get('result'), d.get('actual')) for d in leaves]
        assert [(d.get('result'), d.get('actual')) for d in leaves] == [(d.get('result'), d.get('actual')) for d in row['leaves']]
        out['scored_tasks'] += 1
        out['passed_tasks'] += passed is True
        out['saved_checks_verified'] += len(leaves)
        out['tasks'].append({'scene_id': sid, 'task_id': row['task_id'], 'job_id': row['job_id'], 'passed': passed,
                             'checks': len(leaves), 'worker_and_lease_verified': True})
after = inventory()
assert before == after, 'Attempt evidence changed during read-only audit'
out['attempt_file_count'] = len(before)
out['attempt_files_unchanged'] = True
out['input_sha256'] = before
Path('/private/tmp/b1-interrupted-evidence-audit.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key: value for key, value in out.items() if key not in ('tasks', 'input_sha256')}, ensure_ascii=False))
