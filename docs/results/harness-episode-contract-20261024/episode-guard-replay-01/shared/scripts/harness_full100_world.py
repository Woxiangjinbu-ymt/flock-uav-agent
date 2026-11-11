"""Task-local simulator with immutable deadline snapshots and sidecar motion logs."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import json
import math
from pathlib import Path
import threading
import time

from harness_v2_world import World, EPOCH, digest, sha, write_json

ORIGIN = ContextVar('full100_origin', default={'kind': 'agent_initialization'})


class TaskDeadlineReached(TimeoutError):
    pass


@contextmanager
def origin(**fields):
    token = ORIGIN.set(fields)
    try:
        yield
    finally:
        ORIGIN.reset(token)


class FullWorld(World):
    active = None

    def __init__(self, seed=2026101602, output=None, deadline_seconds=500):
        super().__init__(seed)
        self.output = Path(output) if output else None
        self.deadline_seconds = deadline_seconds
        self.closed = False
        self.score_blob = None
        self.score_seconds = None
        self.phase = 'preparation'
        self.requests = []
        self.physical_events = []
        self._active_request = None
        self._timer = None
        self._monotonic = time.monotonic
        FullWorld.active = self
        from models.drone import Drone
        if not hasattr(Drone, '_full100_original_history'):
            Drone._full100_original_history = Drone.record_history_event
            def record(drone, *args, **kwargs):
                result = Drone._full100_original_history(drone, *args, **kwargs)
                w = FullWorld.active
                if (w is not None and w._active_request is not None
                        and w.server.drone_controller.drones.get(drone.id) is drone):
                    w._active_request['history_events'].append({
                        'actor': drone.id, 'history': copy.deepcopy(drone.history_status[-1])})
                return result
            Drone.record_history_event = record

    def elapsed(self):
        return max(0.0, self._monotonic() - self.started) if self.started is not None else 0.0

    def start(self, timer=True):
        if self.started is not None or self.closed:
            raise ValueError('Task clock cannot restart')
        self.phase = 'task'
        self.started = self._monotonic()
        self.started_unix = time.time()
        if timer:
            self._timer = threading.Timer(self.deadline_seconds, self.freeze_score,
                                          args=(self.deadline_seconds, 'deadline'))
            self._timer.daemon = True
            self._timer.start()

    def freeze_score(self, seconds=None, reason='agent_finished'):
        with self.lock:
            if self.score_blob is not None:
                return self.score_blob
            cutoff = min(self.elapsed() if seconds is None else seconds, self.deadline_seconds)
            self.advance(cutoff)
            self.closed = True
            self.score_seconds = cutoff
            self.score_blob = self.snapshot()
            if self._timer:
                self._timer.cancel()
            if self.output:
                self.output.mkdir(parents=True, exist_ok=True)
                (self.output / 'score-world.pkl').write_bytes(self.score_blob)
                write_json(self.output / 'score-boundary.json', {
                    'seconds': cutoff, 'reason': reason, 'snapshot_sha256': sha(self.score_blob),
                    'state_sha256': digest(self.inspect_blob(self.score_blob)),
                    'atomic_command_commit': 'response-complete before cutoff; crossing command uncommitted'})
            return self.score_blob

    def check_admission(self):
        if self.phase == 'task' and (self.closed or self.elapsed() >= self.deadline_seconds):
            self.freeze_score(self.deadline_seconds, 'deadline')
            raise TaskDeadlineReached('Task admission window closed')

    def _append(self, name, value):
        if self.output:
            self.output.mkdir(parents=True, exist_ok=True)
            with (self.output / name).open('a') as stream:
                stream.write(json.dumps(value, ensure_ascii=False, default=str) + '\n')

    def request(self, method, endpoint, *, admin=False, **kwargs):
        with self.lock:
            if self.phase == 'task':
                self.check_admission()
                self.advance(min(self.elapsed(), self.deadline_seconds))
            if not admin and (endpoint.startswith('/check/') or '/tasks/' in endpoint and endpoint.endswith('/check')):
                raise PermissionError('Private evaluation is unavailable to experimental agent')
            action = method.upper() not in ('GET', 'HEAD') and not admin
            before_blob = self.snapshot() if action and self.phase == 'task' else None
            before_energy = {k: d.battery_volume for k, d in self.server.drone_controller.drones.items()}
            row = {'request_id': len(self.requests) + 1, 'phase': self.phase,
                   'relative_seconds': self.elapsed(), 'method': method, 'endpoint': endpoint,
                   'params': copy.deepcopy(kwargs.get('params')), 'body': copy.deepcopy(kwargs.get('json')),
                   'admin': admin, 'action': action, 'origin': copy.deepcopy(ORIGIN.get()), 'history_events': []}
            self._active_request = row
            kwargs.pop('timeout', None)
            kwargs.pop('headers', None)
            error = None
            try:
                response = self.client.request(method, endpoint,
                    headers=self.admin_headers if admin else self.agent_headers, **kwargs)
                response.raise_for_status()
                result = response.json() if response.status_code != 204 else None
                row['response'] = result
            except Exception as exc:
                error = exc
                row['error'] = type(exc).__name__
            finally:
                self._active_request = None
            row['ended_seconds'] = self.elapsed()
            row['committed'] = True
            # Simulator API commands are atomic transactions. A command that has
            # not completed by the cutoff cannot leak late effects into scoring.
            if before_blob and row['ended_seconds'] >= self.deadline_seconds:
                started = self.started
                self.restore(before_blob)
                self.started = started
                row['committed'] = False
                row['deadline_disposition'] = 'atomic_command_not_committed'
                self.freeze_score(self.deadline_seconds, 'crossing_command')
                error = TaskDeadlineReached('Atomic simulator command crossed cutoff; not committed')
            row['energy'] = [{'actor': k, 'before_mah': v,
                'after_mah': self.server.drone_controller.drones[k].battery_volume,
                'consumed_mah': max(0, v - self.server.drone_controller.drones[k].battery_volume),
                'charged_mah': max(0, self.server.drone_controller.drones[k].battery_volume - v)}
                for k, v in before_energy.items() if self.server.drone_controller.drones[k].battery_volume != v]
            for index, e in enumerate(row['history_events']):
                h = e['history']
                delta = {k: h['position'][k] - h['start_position'][k] for k in ('x', 'y', 'z')}
                event = {**e, 'event_id': f"{row['request_id']}:{index}", 'request_id': row['request_id'],
                    'phase': self.phase, 'origin': row['origin'], 'committed': row['committed'],
                    'effect_seconds': row['ended_seconds'], 'length_xy': math.hypot(delta['x'], delta['y']),
                    'length_xyz': math.sqrt(sum(v*v for v in delta.values())), 'vertical_distance': abs(delta['z'])}
                self.physical_events.append(event)
                self._append('physical-events.jsonl', event)
            self.requests.append(row)
            self._append('public-api.jsonl', row)
            if self.phase == 'task' and self.elapsed() >= self.deadline_seconds:
                self.freeze_score(self.deadline_seconds, 'deadline')
            if error:
                raise error
            return result

    def evaluate(self, task_id, blob=None):
        """Original task/check tree on a restored snapshot; no feedback to agent."""
        with self.lock:
            saved, started, phase = self.snapshot(), self.started, self.phase
            try:
                self.restore(blob or self.score_blob or saved)
                self.phase = 'scoring'
                result = self.request('GET', f'/sessions/current/tasks/{task_id}/check', admin=True,
                                      params={'since_timestamp': EPOCH})
                def leaves(node):
                    if node.get('checks'):
                        return [leaf for child in node['checks'] for leaf in leaves(child)]
                    return [node]
                checks = leaves(result['details'])
                return {'passed': result['result'], 'checks_passed': sum(bool(c['result']) for c in checks),
                        'checks_expected': len(checks), 'details': result['details'],
                        'errors': [c for c in checks if c.get('type') == 'error']}
            finally:
                self.restore(saved)
                self.started, self.phase = started, phase

    def cost(self):
        requests = [r for r in self.requests if r['phase'] == 'task' and not r['admin']]
        actions = [r for r in requests if r['action']]
        events = [e for e in self.physical_events if e['phase'] == 'task' and e['committed']]
        return {'api_requests': len(requests), 'action_attempts': len(actions),
            'action_receipts': {s: sum((r.get('response') or {}).get('status') == s for r in actions)
                                for s in ('success', 'partial_success', 'error')},
            'uncommitted_actions': sum(not r['committed'] for r in actions),
            'physical_history_events': len(events),
            'length_xy': sum(e['length_xy'] for e in events),
            'length_xyz': sum(e['length_xyz'] for e in events),
            'vertical_distance': sum(e['vertical_distance'] for e in events),
            'consumed_mah': sum(e['consumed_mah'] for r in actions for e in r['energy']),
            'charged_mah': sum(e['charged_mah'] for r in actions for e in r['energy'])}
