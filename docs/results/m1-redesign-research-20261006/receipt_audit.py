"""Find legal tool-returned reach messages that lack a structured entity binding.
This audits all 100 tasks chronologically, without reading simulator state.
Names in errors, requests, assistant text and user text are not evidence.
"""
import collections, hashlib, json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
HASHES={}
def read(p):
 p=ROOT/p;b=p.read_bytes();HASHES[str(p.relative_to(ROOT))]=hashlib.sha256(b).hexdigest();return json.loads(b)
def scan(x,path=()):
 if isinstance(x,dict):
  message=x.get('message','')
  # Only the server's explicit positive reach-report phrase, not any name mention.
  if isinstance(message,str) and 'Reached target(s): ' in message:
   suffix=message.split('Reached target(s): ',1)[1]
   names=re.findall(r'\b(?:Fixed|Moving|Circle|Polygon) Target \d+\b|\bWaypoint \d+\b',suffix)
   for name in names:yield name,path,x
  for k,v in x.items():yield from scan(v,path+(k,))
 elif isinstance(x,list):
  for i,v in enumerate(x):yield from scan(v,path+(i,))
rows=read('docs/results/m1-progress-fast100-20261006/paired-tasks.json');missing=read('docs/results/m1-redesign-research-20261006/unresolved-references.json');idx=collections.defaultdict(list)
for v in missing:idx[v['scene'],v['task']].append(v)
history=collections.defaultdict(lambda:collections.defaultdict(list));allclaims=[];unresolved=[]
for r in rows:
 scene,tid=r['session_id'],r['task_id'];t=read(r['m1_trace']);g=t['task_progress'];current=collections.defaultdict(list)
 for e in g['events']:
  for name,path,v in scan(e['payload']):
   actor=v.get('drone_id',e['args'].get('drone_id'))
   rec={'scene':scene,'task':tid,'target_name':name,'evidence_id':e['evidence_id'],'tool':e['tool_name'],'tool_actor':e['args'].get('drone_id'),'receipt_actor':actor,'message_path':list(path)+['message'],'receipt_status':v.get('status'),'receipt_outcome':v.get('outcome'),'tool_status':e['status'],'message':v['message'],'args':e['args'],'structured_in_final_ledger':bool(g['entity_bindings'].get(name,{}).get('entities')) or any(z.get('name')==name for z in g['entities'].values()),'trace':r['m1_trace']}
   allclaims.append(rec);current[name].append(rec)
 for v in idx[scene,tid]:
  if v['target']:unresolved.append({**v,'earlier_task_reach_reports':list(history[scene].get(v['name'],[])),'current_task_reach_reports':list(current.get(v['name'],[]))})
 for name,rs in current.items():history[scene][name].extend(rs)
order={(r['session_id'],r['task_id']):i for i,r in enumerate(rows)}
for v in unresolved:
 for e in v['earlier_task_reach_reports']:
  assert e['scene']==v['scene'] and order[e['scene'],e['task']]<order[v['scene'],v['task']]
summary={'reach_message_mentions_including_nested_duplicates':len(allclaims),'distinct_task_target_pairs':len({(v['scene'],v['task'],v['target_name']) for v in allclaims}),'unresolved_target_references':len(unresolved),'unresolved_refs_with_earlier_task_report':sum(bool(v['earlier_task_reach_reports']) for v in unresolved),'unresolved_refs_with_current_task_report':sum(bool(v['current_task_reach_reports']) for v in unresolved),'tasks_with_prior_report_for_unresolved_target':len({(v['scene'],v['task']) for v in unresolved if v['earlier_task_reach_reports']}),'failed_tasks_with_prior_report_for_unresolved_target':len({(v['scene'],v['task']) for v in unresolved if v['earlier_task_reach_reports'] and not v['m1_pass']}),'success_tasks_with_prior_report_for_unresolved_target':len({(v['scene'],v['task']) for v in unresolved if v['earlier_task_reach_reports'] and v['m1_pass']})}
for fn,data in [('receipt-summary.json',summary),('receipt-unresolved.json',unresolved),('reach-reports.json',allclaims),('receipt-input-sha256.json',HASHES)]: (OUT/fn).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
for v in unresolved:
 if v['earlier_task_reach_reports'] or v['current_task_reach_reports']:print(v['scene'],v['task'],v['name'],v['outcome'],'prior',[(x['task'],x['evidence_id']) for x in v['earlier_task_reach_reports']],'current',len(v['current_task_reach_reports']))
