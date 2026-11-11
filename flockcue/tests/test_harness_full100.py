"""Offline full100 contract tests using the real controller and scripted model."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from harness_full100_world import FullWorld, TaskDeadlineReached, origin
from harness_v2_cases import build_case, checker
from harness_v2_world import sha, digest, write_json
import harness_full100_online as online
from harness_full100_campaign import calibration_blocks, campaign, verify
sys.path.insert(0, str(ROOT / 'agent4drone/tests'))
from test_task_progress_integration import ScriptedModel, call
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class Model(ScriptedModel):
    max_retries: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        reply = self.replies[len(self.requests) - 1]
        if callable(reply):
            reply = reply(messages)
        return ChatResult(generations=[ChatGeneration(message=copy.deepcopy(reply))])


def make_world(tmp_path, family='valid_successful_checkpoint', number=1):
    c = build_case({'case_id': f'{family}-{number:02d}', 'family': family})
    w = FullWorld(output=tmp_path)
    w.import_session(c['scene'])
    w.bake_timeline()
    return w, c


def make_agent(monkeypatch, tmp_path, w, replies, arm='applicability'):
    monkeypatch.setenv('UAV_HARNESS_SCHEDULER', arm)
    cls = online.agent_class(w, tmp_path)
    model = Model(replies=replies)
    monkeypatch.setattr(cls, '_create_llm', lambda *a, **k: model)
    w.start(timer=False)
    agent = cls(base_url='http://testserver', llm_provider='offline', llm_model='scripted',
                verbose=False, share_blackboard_by_session=False, task_progress_mode='evidence_harness')
    return agent


@pytest.mark.parametrize('use_skill', [True, False])
def test_natural_selection_no_bootstrap_and_raw_tools_remain_available(monkeypatch, tmp_path, use_skill):
    w, c = make_world(tmp_path)
    replies = ([call('run_harness', 'start', action='start', skill='simple_chain')] if use_skill else [
        call('take_off', 'take', drone_id='drone-1', altitude=25),
        call('rotate', 'rot', drone_id='drone-1', heading=105), call('hover', 'hov', drone_id='drone-1')])
    replies.append(AIMessage(content='Done [TASK DONE]'))
    a = make_agent(monkeypatch, tmp_path, w, replies)
    assert not [r for r in w.requests if r['action']]
    result = a.execute(c['command'], step_callback=lambda x: None)
    assert result['success'] and checker(w, c)['passed']
    frames = a._harness.snapshot()['frames']
    assert len(frames) == int(use_skill)
    assert not hasattr(a, 'bootstrap')
    first = str(a.llm.requests[0])
    assert 'A public skill was committed' not in first
    assert 'snapshot_sha256' not in first and 'eligibility' not in first
    assert all(r['relative_seconds'] >= 0 for r in w.requests if r['phase'] == 'task')
    actions = [r for r in w.requests if r['action']]
    assert len(actions) == 3
    assert all(r['origin']['kind'] == ('skill' if use_skill else 'model_tool') for r in actions)
    if use_skill:
        assert all(r['origin']['plan_id'] == frames[0]['plan_id'] for r in actions)


def test_invalid_control_wrapper_reply_is_recorded(monkeypatch, tmp_path):
    w, c = make_world(tmp_path)
    a = make_agent(monkeypatch, tmp_path, w, [call('run_harness', 'bad', action='start', skill='simple_chain', extra=1),
        AIMessage(content='Cannot execute')])
    a.execute(c['command'], step_callback=lambda x: None)
    results = [e for e in a._harness.snapshot()['events'] if e['kind'] == 'control_result']
    assert len(results) == 1 and json.loads(results[0]['content'])['status'] == 'rejected'
    assert not a._harness.frames and not [r for r in w.requests if r['action']]


@pytest.mark.parametrize('arm', ['applicability', 'failure'])
def test_natural_start_and_same_id_resume_after_actual_takeoff_failure(monkeypatch, tmp_path, arm):
    from harness_v2_cases import Faults
    w, c = make_world(tmp_path, 'recoverable_takeoff_actor_disabled')
    def follow(messages):
        response = json.JSONDecoder().raw_decode(next(m.content for m in reversed(messages) if isinstance(m, ToolMessage)))[0]
        return call('run_harness', 'resume', action='resume', plan_id=response['plan_id']) if response['status'] == 'paused' else AIMessage(content='Done [TASK DONE]')
    a = make_agent(monkeypatch, tmp_path, w, [call('list_drones', 'list'),
        call('sense_nearby_entities', 'sense', detail='full'),
        call('run_harness', 'start', action='start', skill='public_search'),
        follow, AIMessage(content='Done [TASK DONE]')], arm)
    initialize = a._initialize_task_progress
    def inject_offline_fault(command, command_id):
        initialize(command, command_id)
        a._harness.invoke = Faults(w, c).wrap(a._harness.invoke)
    monkeypatch.setattr(a, '_initialize_task_progress', inject_offline_fault)
    a.execute(c['command'], step_callback=lambda x: None)
    events = a._harness.snapshot()['events']
    enters = [e for e in events if e['kind'] == 'skill_enter']
    assert len(enters) == (2 if arm == 'failure' else 1)
    assert len({e['plan_id'] for e in enters}) == 1
    assert checker(w, c)['passed']


def test_natural_unknown_effect_takeover_does_not_replay(monkeypatch, tmp_path):
    w, c = make_world(tmp_path)
    request = w.request
    lost = []
    def drop(method, endpoint, **kwargs):
        result = request(method, endpoint, **kwargs)
        if endpoint.endswith('/command/take_off') and not lost:
            lost.append(True)
            raise TimeoutError('offline lost receipt after actual effect')
        return result
    monkeypatch.setattr(w, 'request', drop)
    a = make_agent(monkeypatch, tmp_path, w, [call('run_harness', 'start', action='start', skill='simple_chain'),
        call('rotate', 'rot', drone_id='drone-1', heading=105), call('hover', 'hov', drone_id='drone-1'),
        AIMessage(content='Done [TASK DONE]')])
    a.execute(c['command'], step_callback=lambda x: None)
    assert checker(w, c)['passed']
    assert a._harness.snapshot()['frames'][0]['status'] == 'needs_replan'
    assert len([e for e in w.physical_events if e['history']['event'] == 'take_off']) == 1


def test_partial_route_logs_only_executed_distance_and_charge_separately(tmp_path):
    from models.obstacle import Obstacle, ObstacleType
    from models.target import Target, TargetType
    w, _ = make_world(tmp_path)
    w.start(timer=False)
    w.request('POST', '/drones/drone-1/command/take_off', params={'altitude': 25})
    w.server.obstacle_controller.obstacles['stop'] = Obstacle('Stop second segment', ObstacleType.CIRCLE,
        position={'x': 280, 'y': 250, 'z': 0}, radius=2, height=100, obstacle_id='stop')
    receipt = w.request('POST', '/drones/drone-1/command/move_along_path', json={
        'waypoints': [{'x': 260, 'y': 250, 'z': 25}, {'x': 300, 'y': 250, 'z': 25}], 'allow_partial_move': True})
    assert receipt['status'] == 'partial_success'
    cost = w.cost()
    assert cost['length_xy'] == pytest.approx(10)
    assert cost['length_xyz'] == pytest.approx(35)
    assert cost['vertical_distance'] == pytest.approx(25)
    assert cost['consumed_mah'] > 0
    w.request('POST', '/drones/drone-1/command/return_home')
    w.request('POST', '/drones/drone-1/command/land')
    before_charge = w.cost()['consumed_mah']
    w.server.target_controller.targets['charger'] = Target('Charger', TargetType.WAYPOINT,
        {'x': 250, 'y': 250, 'z': 0}, radius=10, target_id='charger')
    w.request('POST', '/drones/drone-1/command/charge', params={'charge_amount': 10})
    assert w.cost()['charged_mah'] > 0
    assert w.cost()['consumed_mah'] == before_charge


def test_history_sidecar_survives_native_500_record_limit(tmp_path):
    w, _ = make_world(tmp_path)
    w.start(timer=False)
    w.request('POST', '/drones/drone-1/command/take_off', params={'altitude': 25})
    for _ in range(510):
        w.request('POST', '/drones/drone-1/command/hover')
    assert len(w.server.drone_controller.drones['drone-1'].history_status) == 500
    assert len(w.physical_events) == 511
    assert len({e['event_id'] for e in w.physical_events}) == 511
    assert w.cost()['length_xyz'] == 25


def test_strict_cutoff_freezes_before_late_response_and_forbids_new_action(tmp_path):
    w, _ = make_world(tmp_path)
    clock = [0.0]
    w._monotonic = lambda: clock[0]
    w.start(timer=False)
    clock[0] = 1
    w.request('POST', '/drones/drone-1/command/take_off', params={'altitude': 25})
    clock[0] = 500
    frozen = w.freeze_score(500, 'deadline')
    clock[0] = 602
    with pytest.raises(TaskDeadlineReached):
        w.request('POST', '/drones/drone-1/command/rotate', params={'heading': 105})
    assert w.score_blob == frozen
    assert w.server.drone_controller.drones['drone-1'].heading == 0
    assert w.score_seconds == 500


def test_crossing_atomic_command_cannot_leak_late_effect_into_score(monkeypatch, tmp_path):
    w, _ = make_world(tmp_path)
    clock = [0.0]
    w._monotonic = lambda: clock[0]
    w.start(timer=False)
    request = w.client.request
    def slow(*args, **kwargs):
        result = request(*args, **kwargs)
        clock[0] = 501
        return result
    monkeypatch.setattr(w.client, 'request', slow)
    with pytest.raises(TaskDeadlineReached):
        w.request('POST', '/drones/drone-1/command/take_off', params={'altitude': 25})
    assert w.server.drone_controller.drones['drone-1'].position['z'] == 0
    assert w.score_seconds == 500
    assert w.cost()['uncommitted_actions'] == 1
    assert w.cost()['length_xyz'] == 0 and w.cost()['consumed_mah'] == 0


@pytest.mark.parametrize('steps,recursion', [(2, 1800), (600, 3)])
def test_natural_entry_loop_budgets(monkeypatch, tmp_path, steps, recursion):
    monkeypatch.setitem(online.BUDGET, 'max_iterations', steps)
    monkeypatch.setitem(online.BUDGET, 'recursion_limit', recursion)
    w, c = make_world(tmp_path)
    a = make_agent(monkeypatch, tmp_path, w, [call('get_drone_status', f'r{i}', drone_id='drone-1') for i in range(10)])
    a.execute(c['command'], step_callback=lambda x: None)
    d = a.execution_diagnostics
    assert d['stopped_due_to_step_limit'] if steps == 2 else d['graph_error_type'] == 'GraphRecursionError'


def test_full_schedule_dry_run_and_calibration_scope():
    plan = Path(os.environ['FULL100_PLAN'])
    schedule = json.loads((plan / 'schedule.proposed.json').read_text())
    selected = calibration_blocks(schedule)
    all_ids = []
    for repeat in (1, 2):
        for worker in (0, 1):
            queue = sorted([b for b in schedule['blocks'] if b['repeat'] == repeat and b['worker'] == worker],
                           key=lambda b: b['rank_within_worker_repeat'])
            assert [b['rank_within_worker_repeat'] for b in queue] == list(range(1, 51))
            all_ids.extend(t['trial_id'] for b in queue for t in b['trials'])
    assert len(all_ids) == len(set(all_ids)) == 400
    authorized = [t['trial_id'] for b in selected for t in b['trials']]
    assert len(authorized) == len(set(authorized)) == 40
    assert len(set(all_ids) - set(authorized)) == 360
    broken = copy.deepcopy(schedule)
    broken['blocks'][0]['worker'] = 2
    with pytest.raises(AssertionError):
        calibration_blocks(broken)


def test_all100_fixtures_restore_and_original_checker_parity():
    study = Path(os.environ['FULL100_STUDY'])
    report = json.loads((study / 'fixture-preflight.json').read_text())
    assert report['passed'] and report['tasks'] == 100 and report['checks'] == 605 and report['restores'] == 400
    for path in sorted((study / 'fixtures').glob('*.json')):
        f = json.loads(path.read_text())
        w = FullWorld()
        blob = (study / 'fixtures' / f"{f['task_id']}.pkl").read_bytes()
        w.restore(blob, f['snapshot_sha256'])
        assert w.state_digest() == f['state_sha256']
        assert w.evaluate(f['task_id'], blob) == f['initial_checker']


def test_coordinator_refuses_existing_attempt_and_tampering(tmp_path):
    schedule = json.loads((Path(os.environ['FULL100_PLAN']) / 'schedule.proposed.json').read_text())
    write_json(tmp_path / 'schedule.json', schedule)
    ids = [t['trial_id'] for b in calibration_blocks(schedule) for t in b['trials']]
    write_json(tmp_path / 'manifest.json', {'authorized_executions': 40, 'authorized_trial_ids': ids,
        'files': {'schedule.json': sha((tmp_path / 'schedule.json').read_bytes())}})
    write_json(tmp_path / 'runs.json', {})
    with pytest.raises(ValueError, match='Existing attempt'):
        campaign(tmp_path, ROOT)
    (tmp_path / 'schedule.json').write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        verify(tmp_path)


@pytest.mark.parametrize('fail_at', [None, 3])
def test_actual_coordinator_dispatches_only40_and_stops_on_failure(monkeypatch, tmp_path, fail_at):
    import itertools
    import harness_full100_campaign as queue
    schedule = json.loads((Path(os.environ['FULL100_PLAN']) / 'schedule.proposed.json').read_text())
    write_json(tmp_path / 'schedule.json', schedule)
    allowed = [t['trial_id'] for b in calibration_blocks(schedule) for t in b['trials']]
    write_json(tmp_path / 'manifest.json', {'authorized_executions': 40, 'authorized_trial_ids': allowed,
        'files': {'schedule.json': sha((tmp_path / 'schedule.json').read_bytes())}})
    counter = itertools.count(1)
    class FakeProcess:
        def __init__(self, cmd, **kwargs):
            self.number = next(counter)
            self.pid = 900000 + self.number
            self.code = 1 if self.number == fail_at else 0
            trial = json.loads(Path(cmd[cmd.index('--trial') + 1]).read_text())
            assert trial['trial_id'] in allowed
            output = Path(cmd[cmd.index('--output') + 1]); output.mkdir(parents=True)
            if self.code == 0:
                write_json(output / 'result.json', {**trial, 'status': 'completed',
                    'usage': {'total_tokens': 100}, 'missing_usage_calls': 0,
                    'initial_state_sha256': trial['task_id'], 'public_startup_sha256': trial['task_id']})
                write_json(output / 'seal.json', {'files': {'result.json': sha((output / 'result.json').read_bytes())}})
        def wait(self, **kwargs):
            return self.code
    monkeypatch.setattr(queue.subprocess, 'Popen', FakeProcess)
    campaign(tmp_path, ROOT)
    runs = json.loads((tmp_path / 'runs.json').read_text())
    state = json.loads((tmp_path / 'campaign.json').read_text())
    if fail_at is None:
        assert len(runs) == 40 and set(runs) == set(allowed)
        assert state['status'] == 'calibration_complete_paused_pending_user_decision'
    else:
        assert state['status'] == 'failed' and len(runs) < 40
        assert sum(r['status'] == 'failed' for r in runs.values()) == 1
