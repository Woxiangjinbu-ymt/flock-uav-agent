"""Frozen public receipt/policy reconstruction only; no simulator or model."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'source/agent4drone'))
from coordinated_search_r2 import PublicSearchPolicy
from blackboard import PerceptionBlackboard
from shapely.geometry import Point, LineString
import shapely


def main():
    seal = json.loads((ROOT / 'input-seal.json').read_text())
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert all(sha(ROOT / p) == digest for p,digest in seal['files'].items())
    rows = [json.loads(line) for line in (ROOT/'inputs/public-task-api.jsonl').read_text().splitlines()]
    start = json.loads((ROOT/'inputs/start.json').read_text())
    frame = json.loads((ROOT/'inputs/harness.json').read_text())['frames'][0]
    fleet = next(r['response'] for r in rows if r['origin']['tool'] == 'list_drones')
    board = PerceptionBlackboard(start['scene_id'])
    states = {d['id']:d for d in fleet}
    policy = PublicSearchPolicy(fleet, [])  # Archived entry observations are all empty.
    entry_rows = [r for r in rows if r['origin']['tool']=='get_nearby_entities'][:len(fleet)]
    assert len(entry_rows)==len(fleet) and all(not r['response']['obstacles'] for r in entry_rows)
    observations, movements, receipts, pending, progress_reads = [], [], [], None, []
    matches = []
    for row in rows:
        tool, response = row['origin']['tool'], row['response']
        if tool=='get_mission_progress':
            progress_reads.append({'request_id':row['request_id'], 'distance_3d':response.get('is_distance_3d'),
                                   'canvas':[response.get('canvas_width'),response.get('canvas_height')],
                                   'progress':response['statistics']['task_progress']})
        if tool=='get_drone_status':
            states[response['id']] = response
        if tool=='move_to':
            actor=row['endpoint'].split('/')[2]
            choice=policy.choose(actor)
            matches.append(choice is not None and tuple(choice[1])==tuple(row['params'][k] for k in ('x','y')))
            assert matches[-1]
            p=states[actor]['position'];dest=row['params']
            pending={'request_id':row['request_id'],'actor':actor,'index':choice[0],
                     'start_xyz':[p[k] for k in ('x','y','z')], 'destination_xyz':[dest[k] for k in ('x','y','z')],
                     'receipt':response,'covered_before':len(policy.covered)}
            assert response['status']=='success'
            if 'Reached target(s): ' in response.get('message',''):
                receipts.append({'request_id':row['request_id'],'actor':actor,
                                 'names':response['message'].split('Reached target(s): ',1)[1].split(', '),
                                 'message':response['message']})
        if tool=='get_nearby_entities':
            actor=row['endpoint'].split('/')[2]
            board.ingest_nearby(actor,response)
            if pending:
                assert pending['actor']==actor
                policy.accept(actor,pending['index'],'completed',tuple(states[actor]['position'][k] for k in ('x','y')),
                              response['obstacles'],round_number=len(movements)//len(fleet)+1,sequence=len(movements)+1)
                pending['covered_after']=len(policy.covered)
                movements.append(pending);pending=None
            for target in response['targets']:
                position=target['position']; observer=states[actor]['position'];point=(position['x'],position['y'])
                segment=LineString([(observer['x'],observer['y']), point])
                observations.append({'request_id':row['request_id'],'actor':actor,'target':target,
                                     'observer_state':states[actor],
                                     'center_distance_xy':math.dist((observer['x'],observer['y']),point),
                                     'known_obstacle_ids_at_observation':sorted(policy.known_obstacles),
                                     'endpoint_blocked_by_public_policy':policy.blocked.intersects(Point(*point)),
                                     'direct_edge_blocked_by_public_policy':policy.blocked.intersects(segment),
                                     'candidate_count':len(policy.candidates),'covered_score_cells':len(policy.covered)})
    assert pending is None and len(matches)==frame['cursor']==80 and all(matches)
    archived_board=json.loads((ROOT/'inputs/board-final.json').read_text())
    assert json.loads(json.dumps(board.full()))==archived_board
    width,height=progress_reads[0]['canvas']
    def outside(xy):return xy[0]<0 or xy[0]>width or xy[1]<0 or xy[1]>height
    targets=[]
    for tid in sorted({o['target']['id'] for o in observations}):
        entries=[o for o in observations if o['target']['id']==tid];target=entries[-1]['target'];xy=tuple(target['position'][k] for k in ('x','y'))
        reached=[r for r in receipts if target['name'] in r['names']]
        nearest=min(enumerate(policy.candidates),key=lambda v:math.dist(v[1],xy))
        nearest_moves=sorted(({'request_id':m['request_id'],'actor':m['actor'],
                             'center_distance_to_public_segment':LineString([m['start_xyz'][:2],m['destination_xyz'][:2]]).distance(Point(*xy))}
                            for m in movements),key=lambda r:r['center_distance_to_public_segment'])
        next_moves=[]
        for o in entries:
            following=next((m for m in movements if m['actor']==o['actor'] and m['request_id']>o['request_id']),None)
            next_moves.append({'observation_request_id':o['request_id'],'next_same_actor_move':following})
        targets.append({'id':tid,'name':target['name'],'public_position':target['position'],'public_type':target['type'],
                        'radius':target.get('radius'),'observations':entries,'positive_reached_receipts':reached,
                        'latest_public_is_reached':target.get('is_reached'),
                        'interpretation':'positive action receipt overrides false entity flag' if reached else 'observed fixed center; no positive reach receipt in this task',
                        'nearest_grid_candidate':{'index':nearest[0],'xy':nearest[1],'distance_xy':math.dist(nearest[1],xy)},
                        'grid_candidates_within_20m':sum(math.dist(p,xy)<=20 for p in policy.candidates),
                        'minimum_public_move_segment_distance':nearest_moves[0],
                        'direct_center_move_request_count':sum(tuple(m['destination_xyz'][:2])==xy for m in movements),
                        'next_moves_after_observation':next_moves})
    result={'method':'Replay deterministic pure policy choices from public fleet and sequential nearby obstacles; no simulator/model, private targets, checker, or world state.',
            'shapely_version':shapely.__version__,'scene_id':start['scene_id'],'policy_choice_matches':sum(matches),'policy_move_count':len(matches),
            'board_exactly_reconstructed':True,'targets':targets,'positive_receipts':receipts,'public_progress_reads':progress_reads,
            'policy_metrics':{'bounds':policy.bounds,'canvas_from_public_session':[width,height],
                              'sensing_radius':policy.radius,'public_task_radii':sorted({d['task_radius'] for d in fleet}),
                              'candidate_count':len(policy.candidates),'selected_count':len(policy.selected),
                              'score_cells':len(policy.score_cells),'initial_covered':policy.initial_covered_count,
                              'final_covered':len(policy.covered),'coverage_fraction':len(policy.covered)/len(policy.score_cells),
                              'uncovered_score_cells':len(policy.score_cells)-len(policy.covered),
                              'candidate_points_outside_public_canvas':sum(outside(p) for p in policy.candidates),
                              'actual_moves_outside_public_canvas':[m['request_id'] for m in movements if outside(m['destination_xyz'])],
                              'remaining_candidates_with_novel_cells':sum(i not in policy.selected and bool(policy.candidate_cover[i]-policy.covered) for i in range(len(policy.candidates))),
                              'final_public_known_obstacle_ids':sorted(policy.known_obstacles)},
            'runtime':{k:frame[k] for k in ['status','reason','cursor','api_calls','api_budget','repairs','repair_budget']},
            'limitations':['Score-cell coverage is a sensing-radius endpoint heuristic, not task completion, target reachability, or complete world coverage.',
                           'Public failure-free direct geometry does not prove real reachability; only already observed obstacles are checked.',
                           'No position or identity is inferred for the third target absent from public perception.',
                           'is_reached=False is contradicted by positive action receipts for Target1 and is unsafe as the sole unfinished filter.']}
    (ROOT/'diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'choices':sum(matches),'metrics':result['policy_metrics'],
                      'targets':[{'id':t['id'],'name':t['name'],'nearest_grid':t['nearest_grid_candidate'],
                                  'minimum_path':t['minimum_public_move_segment_distance'],'reached_requests':[r['request_id'] for r in t['positive_reached_receipts']]} for t in targets]},ensure_ascii=False))


if __name__=='__main__':main()
