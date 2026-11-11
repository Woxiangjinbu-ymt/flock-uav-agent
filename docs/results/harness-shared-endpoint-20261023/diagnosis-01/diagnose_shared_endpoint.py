#!/usr/bin/env python3
"""Read frozen, public task receipts only. No simulator/model/network execution."""
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
PATTERN = re.compile(r"Target position conflicts with obstacle: Position conflicts with (circle|ellipse|polygon) obstacle '([^']+)'")
GEOMETRY = ('type', 'position', 'radius', 'vertices', 'width', 'length', 'height')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def xyz(value):
    if not isinstance(value, dict):
        return None
    values = tuple(value.get(k) for k in ('x', 'y', 'z'))
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        return values
    return None


def state(row):
    return {'request_id': row['request_id'], 'relative_seconds': row['relative_seconds'],
            'drone': row['response']}


def positions(row):
    response = row['response']
    tool = row.get('origin', {}).get('tool')
    if tool == 'get_drone_status':
        return [response]
    if tool == 'list_drones':
        return response
    if tool == 'get_nearby_entities':
        return response.get('drones', [])
    return []


def analyze(task):
    folder = ROOT / 'inputs' / task
    rows = [json.loads(line) for line in (folder / 'public-task-api.jsonl').read_text().splitlines()]
    start = json.loads((folder / 'start.json').read_text())
    harness = json.loads((folder / 'harness.json').read_text())
    frame = harness['frames'][0]
    assert all(r['phase'] == 'task' and not r['admin'] for r in rows)
    assert all(r.get('origin', {}).get('kind') == 'skill' for r in rows)
    assert len(harness['frames']) == 1 and harness['scene_epoch'] == frame['scene_epoch'] == 0
    assert all(r['origin']['plan_id'] == frame['plan_id'] for r in rows)
    latest_states, observations, changes, last_geometry, failures = {}, [], [], {}, []
    masks = {m['action']['receipt']['command_id']: m for m in frame['state']['blocked_endpoints']}
    for i, row in enumerate(rows):
        tool, receipt = row['origin'].get('tool'), row['response']
        if tool == 'get_drone_status':
            latest_states[receipt['id']] = row
        if tool == 'get_nearby_entities':
            for obstacle in receipt.get('obstacles', []):
                geom = {k: obstacle.get(k) for k in GEOMETRY}
                current = {'request_id': row['request_id'], 'observer': row['endpoint'].split('/')[2],
                           'id': obstacle['id'], 'name': obstacle.get('name'), 'geometry': geom}
                previous = last_geometry.get(obstacle['id'])
                if previous and previous['geometry'] != geom:
                    changes.append({'before': previous, 'after': current})
                observations.append(current)
                last_geometry[obstacle['id']] = current
        if tool != 'move_to' or not isinstance(receipt, dict):
            continue
        actor = row['endpoint'].split('/')[2]
        match = PATTERN.fullmatch(receipt.get('message', ''))
        if receipt.get('status') != 'error' or receipt.get('command') != 'move_to' or receipt.get('drone_id') != actor or not match:
            continue
        dest = xyz(row['params'])
        assert dest is not None
        before = latest_states[actor]
        after = next(r for r in rows[i + 1:] if r['origin'].get('tool') == 'get_drone_status' and r['response']['id'] == actor)
        assert not any(r['action'] and r['endpoint'].split('/')[2] == actor
                       for r in rows[i + 1:] if r['request_id'] < after['request_id'])
        mask = masks[receipt['command_id']]
        assert tuple(mask['destination']) == dest and mask['actor'] == actor
        assert xyz(before['response']['position']) == xyz(after['response']['position'])
        failures.append({'request_id': row['request_id'], 'relative_seconds': row['relative_seconds'],
                         'actor': actor, 'destination': dest, 'obstacle_type': match[1], 'obstacle_name': match[2],
                         'receipt': receipt, 'before': state(before), 'after': state(after),
                         'public_xyz_unchanged': True, 'mask': mask})

    def evidence_between(dest, low, high, obstacle_name):
        interval = [r for r in rows if low < r['request_id'] < high]
        moves = [r for r in interval if r['origin'].get('tool') == 'move_to' and xyz(r.get('params')) == dest]
        successes = [{'request_id': r['request_id'], 'actor': r['endpoint'].split('/')[2], 'receipt': r['response']}
                     for r in moves if r['response'].get('status') == 'success']
        occupying = [{'request_id': r['request_id'], 'actor': d['id'], 'position': d['position'], 'tool': r['origin']['tool']}
                     for r in interval for d in positions(r) if xyz(d.get('position')) == dest]
        observed = [o for o in observations if low < o['request_id'] < high and o['name'] == obstacle_name]
        revisions = [c for c in changes if low < c['after']['request_id'] < high]
        return {'successful_moves_to_exact_xyz': successes, 'public_positions_at_exact_xyz': occupying,
                'same_name_obstacle_observations': observed, 'all_public_same_id_geometry_changes': revisions,
                'public_api_mutations': [r['request_id'] for r in interval if r['method'] != 'GET' and not r['action']]}

    by_xyz = {}
    for failure in failures:
        by_xyz.setdefault(failure['destination'], []).append(failure)
    groups, consumers = [], []
    for dest, entries in by_xyz.items():
        if len({e['actor'] for e in entries}) < 2:
            continue
        source = entries[0]
        group = {'destination': dest, 'first_source': source, 'consumers': []}
        for j, entry in enumerate(entries):
            prior = [e for e in entries[:j] if e['actor'] != entry['actor']]
            if not prior:
                continue
            first = prior[0]
            detail = dict(entry)
            detail['first_other_actor_source_request_id'] = first['request_id']
            detail['prior_other_actor_failure_request_ids'] = [e['request_id'] for e in prior]
            detail['source_to_consumer'] = evidence_between(dest, first['request_id'], entry['request_id'], first['obstacle_name'])
            detail['consumer_to_task_end'] = evidence_between(dest, entry['request_id'], rows[-1]['request_id'] + 1, entry['obstacle_name'])
            group['consumers'].append(detail)
            consumers.append(detail)
        group['full_task_same_name_observations'] = [o for o in observations if o['name'] == source['obstacle_name']]
        group['source_to_task_end'] = evidence_between(dest, source['request_id'], rows[-1]['request_id'] + 1, source['obstacle_name'])
        groups.append(group)
    return {'task_id': task, 'scene_id': start['scene_id'], 'scene_epoch': frame['scene_epoch'], 'plan_id': frame['plan_id'],
            'task_public_rows': len(rows), 'task_last_request_id': rows[-1]['request_id'],
            'endpoint_failure_count': len(failures), 'cross_actor_later_failure_event_count': len(consumers),
            'cross_actor_exact_xyz_group_count': len(groups), 'groups': groups,
            'public_obstacle_observations': len(observations), 'public_obstacle_unique_ids': len(last_geometry),
            'public_same_id_geometry_changes': changes,
            'all_physical_actions_skill_origin': True, 'single_plan_and_epoch': True,
            'protocol': {k: start[k] for k in ('model', 'scripted_start', 'deadline_seconds', 'watchdog_seconds', 'initial_state_sha256')},
            'api_budget': frame['api_budget'], 'repair_budget': frame['repair_budget']}


def main():
    input_seal = json.loads((ROOT / 'input-seal.json').read_text())
    assert all(sha(ROOT / p) == digest for p, digest in input_seal['files'].items()), 'frozen input changed'
    source_run = Path(input_seal['source_campaign'])
    original_seal = json.loads((source_run / 'campaign-seal.json').read_text())
    assert sha(source_run / 'campaign-seal.json') == input_seal['source_campaign_seal_sha256']
    mismatches = [p for p, digest in original_seal['files'].items() if sha(source_run / p) != digest]
    assert not mismatches, mismatches
    tasks = [analyze(t) for t in ('24ac1838', 'b1ad4f3e')]
    assert [(t['cross_actor_later_failure_event_count'], t['cross_actor_exact_xyz_group_count']) for t in tasks] == [(1, 1), (7, 4)]
    result = {'schema': 'shared-endpoint-public-diagnosis-v1', 'method': 'static frozen public task receipts + frozen source inspection; no simulator/model',
              'source_campaign_files_verified': len(original_seal['files']), 'frozen_input_files_verified': len(input_seal['files']),
              'definitions': {'qualified_failure': 'Public move_to error whose full receipt matches endpoint-conflict grammar and actor; finite exact XYZ; next public status confirms unchanged XYZ before another same-actor action.',
                              'cross_actor_later_failure_event': 'A qualified failure with an earlier qualified failure in the same plan/scene/epoch at exact XYZ by a different actor. Count each later request once, irrespective of number of sources.',
                              'group': 'One exact XYZ in a plan/scene/epoch with qualified failures by at least two actors.',
                              'geometry_change': 'Same public obstacle ID later observed with different type/position/radius/vertices/width/length/height. Empty nearby lists or absent names do not retract an obstacle.',
                              'counterevidence': 'Later successful move to exact XYZ or public drone position at exact XYZ; scanned separately from named obstacle observations/changes.'},
              'tasks': tasks, 'limitations': ['Only archived public evidence is reconstructed; absence of a public update does not prove immutable hidden world.',
                                            'A name in an error provides no geometry or unique obstacle ID. No region, path, other height, or scene transfer follows.',
                                            'This audit contains no candidate execution, task-success or generalization result.']}
    output = ROOT / 'diagnosis.json'
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'verified_source_files': len(original_seal['files']), 'tasks': [{k: t[k] for k in ('task_id', 'cross_actor_later_failure_event_count', 'cross_actor_exact_xyz_group_count', 'public_obstacle_unique_ids', 'public_same_id_geometry_changes')} for t in tasks]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
