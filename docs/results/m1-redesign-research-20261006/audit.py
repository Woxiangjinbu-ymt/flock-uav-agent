"""Offline diagnosis only: no agent/API calls, source changes or rescoring.
Reads sealed fast100 results and legal agent traces; hidden state is used only
to name post-hoc failed checks, never as a proposed online input.
"""
import collections, hashlib, json, math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
HASHES={}
def read(p):
 p=ROOT/p if not Path(p).is_absolute() else Path(p)
 b=p.read_bytes();HASHES[str(p.relative_to(ROOT))]=hashlib.sha256(b).hexdigest();return json.loads(b)
def write(p,d): (OUT/p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def objects(x):
 if isinstance(x,dict):
  if x.get('id') and x.get('name'): yield x
  for v in x.values():yield from objects(v)
 elif isinstance(x,list):
  for v in x:yield from objects(v)
def xy(v):
 if isinstance(v,dict) and v.get('x') is not None and v.get('y') is not None:return float(v['x']),float(v['y'])
 if isinstance(v,(list,tuple)) and len(v)>=2:return float(v[0]),float(v[1])
def family(command):
 if 'marathon flight' in command:return 'marathon'
 if 'triangle formation' in command:return 'triangle_formation'
 if 'coordinated patrol' in command:return 'coordinate_patrol'
 if 'task progress exceeds' in command:return 'global_progress'
 if 'coordinated area coverage' in command:return 'area_coverage'
 if 'target sweep' in command:return 'target_sweep'
 if 'Fixed Target' in command or 'Circle Target' in command or 'Polygon Target' in command:return 'named_target_other'
 return 'other'
rows=read('docs/results/m1-progress-fast100-20261006/paired-tasks.json')
run=read('experiments/m1-progress-v1/runs.json')
runs={arm:Path(run[arm]['attempt']) for arm in ('m0','m1')}
results={arm:{(x['session_id'],x['task_id']):x for x in read(runs[arm]/'results.json')} for arm in runs}
allrows=[];unresolved=[];failed_targets=[];last={};failedend=collections.Counter();groups=collections.Counter();allgroups=collections.Counter()
for row in rows:
 scene,tid=row['session_id'],row['task_id'];key=(scene,tid)
 t=read(row['m1_trace']);g=t['task_progress'];d=results['m1'][key];f=family(t['command']);allgroups[f]+=1
 state=read(runs['m1']/scene/(tid+'.agent-state.json'));bb=state.get('blackboards',{}).get(scene,{})
 prev=last.get(scene,{});last[scene]=bb
 known={e['name']:e for e in g['entities'].values() if e.get('name')}
 raw={}
 for event in g['events']:
  for obj in objects(event['payload']):
   raw.setdefault(obj['name'],[]).append({'evidence_id':event['evidence_id'],'tool':event['tool_name'],'id':obj['id'],'end_sequence':event['end_sequence']})
 missing=[]
 for name,binding in g['entity_bindings'].items():
  if binding['state']=='unresolved':
   v={'scene':scene,'task':tid,'name':name,'target':'Target' in name or name.startswith('Waypoint'),'m1_pass':row['m1_pass'], 'outcome':row['pair_outcome'], 'structured_tool_hits':raw.get(name,[]),'previous_blackboard_hits':[o['id'] for o in objects(prev) if o['name']==name], 'final_blackboard_hits':[o['id'] for o in objects(bb) if o['name']==name]}
   missing.append(name);unresolved.append(v)
 fail=[v for v in d['leaves'] if v['result'] is not True]
 if not row['m1_pass']:
  groups[f]+=1;failedend.update(v['definition']['endpoint'] for v in fail)
 before=read(runs['m1']/scene/(tid+'.before.json'));after=read(runs['m1']/scene/(tid+'.after.json'))
 targets={x['id']:x for x in before['targets']};drones={x['id']:x for x in before['drones']}
 for leaf in fail:
  p=leaf['definition']['parameters'];target=targets.get(p.get('target_id'))
  if not target:continue
  name=target['name'];ent=known.get(name);actor=p.get('drone_id');v={'scene':scene,'task':tid,'endpoint':leaf['definition']['endpoint'],'target_id':target['id'],'target':name,'actor_id':actor,'actor':drones.get(actor,{}).get('name'),'binding':g['entity_bindings'].get(name,{}).get('state','not_explicit'),'legal_first_receipt':raw.get(name,[None])[0], 'in_previous_blackboard':any(o['name']==name for o in objects(prev)), 'exact_target_coordinate_calls':[]}
  pos=xy(ent['facts'].get('position',{}).get('value')) if ent else None
  if ent and actor:
   for e in g['events']:
    a=e['args'];pts=[xy(a)]+[xy(w) for w in a.get('waypoints',[]) if isinstance(w,(dict,list,tuple))]
    if a.get('drone_id')==actor and (a.get('target_id')==target['id'] or pos and any(q and math.dist(q,pos)<1 for q in pts)):
     receipt=e['payload']; receipt=receipt.get('navigation_result',receipt.get('move_result',receipt)) if isinstance(receipt,dict) and e['tool_name'].endswith('_and_sense') else receipt
     v['exact_target_coordinate_calls'].append({'evidence_id':e['evidence_id'],'tool':e['tool_name'],'status':e['status'],'args':a,'arrival_verified':receipt.get('arrival_verified') if isinstance(receipt,dict) else None,'message':receipt.get('message') if isinstance(receipt,dict) else None})
  failed_targets.append(v)
 allrows.append({'scene':scene,'task':tid,'command':t['command'],'family':f,'m0_pass':row['m0_pass'],'m1_pass':row['m1_pass'],'outcome':row['pair_outcome'],'m1_timeout':row['m1_timeout'],'failed_check_count':len(fail),'failed_endpoints':dict(collections.Counter(v['definition']['endpoint'] for v in fail)),'unresolved':missing,'tool_events':len(g['events']),'action_status':dict(collections.Counter(e['status'] for e in g['events'] if e['scope']=='action_receipt')),'output':t['output'],'trace':row['m1_trace']})
summary={'tasks':len(rows),'failed_tasks':sum(not r['m1_pass'] for r in rows),'failed_checks':sum(failedend.values()),'all_task_families':dict(allgroups),'failed_task_families':dict(groups),'failed_endpoints':dict(failedend),'unresolved_references':len(unresolved),'unresolved_target_references':sum(v['target'] for v in unresolved),'unresolved_target_tasks':len({(v['scene'],v['task']) for v in unresolved if v['target']}),'unresolved_with_structured_tool_entity':sum(bool(v['structured_tool_hits']) for v in unresolved),'unresolved_with_previous_blackboard_entity':sum(bool(v['previous_blackboard_hits']) for v in unresolved),'unresolved_with_final_blackboard_entity':sum(bool(v['final_blackboard_hits']) for v in unresolved),'failed_target_checks':len(failed_targets),'failed_target_check_binding':dict(collections.Counter(v['binding'] for v in failed_targets))}
write('diagnostic-summary.json',summary);write('task-audit.json',allrows);write('unresolved-references.json',unresolved);write('failed-target-checks.json',failed_targets)
for source in ['agent4drone/task_progress.py','agent4drone/blackboard.py','agent4drone/uav_agent.py','agent4drone/uav_langchain_tools.py','server/controllers/drone_controller.py','docs/results/b0-failure-reassessment-20261006/NEW_DEEPSEEK_CASES_CN.md','docs/results/m1-progress-fast100-20261006/REPORT_CN.md','docs/CSCWD2027_INCREMENTAL_RESEARCH_ROADMAP_CN.md','docs/改进意见.md']:
 p=ROOT/source;HASHES[source]=hashlib.sha256(p.read_bytes()).hexdigest()
write('input-sha256.json',HASHES)
print(json.dumps(summary,ensure_ascii=False,indent=2))
print('EXACT FAILED TARGETS')
for v in failed_targets:
 if v['binding']=='exact':print(v['task'],v['target'],v['actor'],[(e['evidence_id'],e['tool'],e['status']) for e in v['exact_target_coordinate_calls']])
