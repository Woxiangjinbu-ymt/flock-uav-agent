"""Offline diagnosis only. Reads frozen evidence, writes this report directory.

Does not edit seals/status/scores, run models, restore attempts or certify the run.
"""
import collections
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
A = ROOT / 'experiments/runs/20261006-214358-m1r-fast100-b4e9b6f3'
S = ROOT / 'experiments/m1-revision-v1'
J = S / 'jobs/20261006-214358-guard-7c59a6fa'
INPUTS = {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def raw(path):
    data = path.read_bytes()
    INPUTS[str(path.relative_to(ROOT))] = sha(data)
    return data


def read(path):
    return json.loads(raw(path))


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    study = read(S / 'frozen/study.json')
    registry = read(S / 'runs.json')
    for p in J.iterdir():
        if p.is_file(): raw(p)
    read(S / 'preflight.json')
    reference = read(A / 'historical-m0.json')
    plan, manifest, rows, perf = [read(A / n) for n in ('plan.json', 'manifest.json', 'results.json', 'performance.json')]
    expected = [(s['id'], t['id']) for s in plan['sessions'] for t in s['tasks']]
    assert len(expected) == len(set(expected)) == 100
    assert [(r['session_id'], r['task_id']) for r in rows] == expected
    assert [(r['session_id'], r['task_id']) for r in reference['tasks']] == expected
    assert all(r['status'] == 'scored' and type(r['passed']) is bool for r in rows)
    assert sum(r['expected_checks'] for r in rows) == 605
    suite = read(A / 'suite-seal.json')['files']
    current = {str(p.relative_to(A)): sha(raw(p)) for p in A.rglob('*') if p.is_file() and p.name != 'suite-seal.json'}
    assert current == suite, 'The terminal suite seal must remain untouched'
    scene_audits = {}
    for s in plan['sessions']:
        sid = s['id']; directory = A / sid; seal = read(directory / 'complete.json')
        files = {str(p.relative_to(directory)): sha(raw(p)) for p in directory.rglob('*') if p.is_file() and p.name != 'complete.json'}
        changed = [name for name, checksum in seal['files'].items() if files.get(name) != checksum]
        scene_audits[sid] = {'sealed_files': len(seal['files']), 'changed': changed,
                            'added': sorted(set(files) - set(seal['files'])), 'completed_at': seal['completed_at']}
        for name in changed:
            data = raw(directory / name); h = hashlib.sha256(); offset = 0
            for i, line in enumerate(data.splitlines(keepends=True), 1):
                h.update(line); offset += len(line)
                if h.hexdigest() == seal['files'][name]:
                    suffix = data[offset:]
                    scene_audits[sid]['append_only_difference'] = {
                        'path': name, 'sealed_prefix_lines': i, 'sealed_prefix_bytes': offset,
                        'sealed_prefix_sha256': h.hexdigest(), 'appended_bytes': len(suffix),
                        'appended_sha256': sha(suffix), 'appended_records': [json.loads(x) for x in suffix.splitlines()]}
    assert scene_audits['ef36e27a']['changed'] == ['metrics/reproduction-metrics.jsonl']
    assert sum(len(x['changed']) + len(x['added']) for x in scene_audits.values()) == 1

    # Execute only archived, read-only component validators; deny socket connections.
    code = '''import json,sys,socket
from pathlib import Path
def denied(*args, **kwargs): raise RuntimeError("Network disabled in offline diagnosis")
socket.socket.connect = denied
socket.create_connection = denied
a=Path(sys.argv[1]); sys.path.insert(0,str(a/'runtime/scripts'))
import m1_revision_pilot as p
plan,meta=p.assert_method_identity(a)
p.r.DATA=a/'runtime/data'
p.rp.verify_archive(p.r,a)
manifest=p.r.read_json(a/'manifest.json'); result={}
for selected in plan['sessions']:
 sid=selected['id']; rec=manifest['scenes'][sid]; d=a/rec['directory']; v={}
 for name,call in [('full_scene_verify',lambda:p.rp.verify_scene(p.r,a,plan,selected,rec)),
                  ('saved_scoring_and_barriers',lambda:p.rp.validate_scene_evidence(p.r,plan,selected,d))]:
  try: v[name]={'passed':True,'tasks':len(call())}
  except Exception as e: v[name]={'passed':False,'error':str(e),'type':type(e).__name__}
 result[sid]=v
audit=p.audit_traces(a,plan)
print(json.dumps({'method_identity':True,'archive_identity':True,'method_audit':audit,'scenes':result,'network_connections_allowed':False}))
'''
    command = [str(A/'runtime/controller/.venv/bin/python'), '-B', '-c', code, str(A)]
    checked = subprocess.run(command, cwd=A/'runtime', capture_output=True, text=True,
                             env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, check=True)
    component_verification = json.loads(checked.stdout)
    assert all(v['saved_scoring_and_barriers']['passed'] for v in component_verification['scenes'].values())
    assert sum(v['full_scene_verify']['passed'] for v in component_verification['scenes'].values()) == 4

    # Preserve and verify historical evidence; use no fresh baseline execution.
    old_report = ROOT / 'docs/results/m1-progress-fast100-20261006'
    for filename, base in [('input-sha256.json', ROOT), ('artifact-sha256.json', old_report)]:
        for name, checksum in read(old_report/filename).items():
            assert sha(raw(base/name)) == checksum, 'Historical evidence changed'
    historical_pairs = {(p['session_id'], p['task_id']): p for p in read(old_report/'paired-tasks.json')}
    previous_summary = read(old_report/'summary.json')
    traces = []
    for s in plan['sessions']:
        for path in sorted((A/s['id']/'agent-traces/toolchains').glob('*.json')):
            t = read(path); traces.append((s['id'], t, path))
    assert len(traces) == 100
    trace_map = {(sid, t['command_id']): (t, path) for sid, t, path in traces}
    event_map = {(sid,t['command_id']): {e['evidence_id']: e for e in t['task_progress']['events']} for sid,t,_ in traces}
    audits=[]; pairs=[]; ordinal={}; task_traces=[]
    for order, (r, old) in enumerate(zip(rows, reference['tasks'])):
        sid, tid = r['session_id'], r['task_id']
        saved = read(A/sid/(tid+'.job.json')); w=read(A/sid/(tid+'.window.json')); b=read(A/sid/(tid+'.barrier.json'))
        matches=[(t,p) for sc,t,p in traces if sc==sid and t['command']==saved['command']
                 and w['opened_at'] <= datetime.fromisoformat(t['started_at']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() <= b['frozen_at']]
        assert len(matches)==1
        t,path=matches[0]; ordinal[sid,t['command_id']]=order; task_traces.append((r,t,path))
        assert sha(t['command'].encode()) == next(x['content_sha256'] for sc in plan['sessions'] if sc['id']==sid for x in sc['tasks'] if x['id']==tid)
        label='W' if not old['passed'] and r['passed'] else 'L' if old['passed'] and not r['passed'] else 'both_pass' if r['passed'] else 'both_fail'
        usage=r['observed_token_usage']; previous=historical_pairs[sid,tid]
        pairs.append({'session_id':sid,'task_id':tid,'name':r['name'],'family':r['family'],'difficulty':r['difficulty'],
            'expected_checks':r['expected_checks'],'m0_pass':old['passed'],'m1r_observed_pass':r['passed'],
            'pair_outcome_diagnostic':label,'m1r_scoring_status':r['status'],
            'm1r_scene_seal_valid':component_verification['scenes'][sid]['full_scene_verify']['passed'],
            'run_accepted':False,'m0_passed_checks':old['passed_checks'],'m1r_passed_checks':sum(x['result'] is True for x in r['leaves']),
            'm0_timeout':previous['m0_timeout'],'m1r_timeout':r['agent_timed_out'],
            'm0_scoring_ready_seconds':previous['m0_scoring_ready_seconds'],'m1r_scoring_ready_seconds':b['frozen_at']-w['opened_at'],
            'm0_total_tokens':previous['m0_total_tokens'],'m1r_total_tokens':usage['total_tokens'],
            'm1r_prompt_tokens':usage['prompt_tokens'],'m1r_completion_tokens':usage['completion_tokens'],
            'm1r_cache_hit_tokens':usage['cache_hit_tokens'],'m1r_cache_miss_tokens':usage['cache_miss_tokens'],
            'm0_trace':old['trace'],'m1r_trace':str(path.relative_to(ROOT))})
    for r,t,path in task_traces:
        sid=r['session_id']; cid=t['command_id']; progress=t['task_progress']; evidence=progress['task_evidence']; issues=[]; reports=0; scopes=collections.Counter()
        views=[evidence['requirements']]
        for injection in evidence['injections']:
            text=injection['text']; payload=json.loads(text[text.index('{'):]); views.append(payload['items'])
            if len(text)>4000 or len(payload['items'])>4: issues.append('view_budget')
            if payload['mission_completion']!='not_evaluated': issues.append('mission_verdict')
        for view in views:
            for item in view:
                lo,hi=item['source_span']
                if not (0<=lo<=hi<=len(t['command'])) or item['target_name'] not in t['command'][lo:hi]: issues.append('source_span')
                rep=item.get('report')
                if rep:
                    reports+=1; key=(sid,rep['command_id']); event=event_map.get(key,{}).get(rep['evidence_id'])
                    if rep['session_id']!=sid or key not in ordinal or ordinal.get(key,999)>ordinal[sid,cid] or event is None:
                        issues.append('report_scope'); continue
                    message=event['payload']
                    for k in rep['message_path']: message=message[k]
                    if message!=rep['message'] or event['tool_call_id']!=rep['tool_call_id'] or str(event['args'].get('drone_id'))!=rep['actor_id']: issues.append('report_receipt_binding')
                    if rep.get('localization_hint',{}):
                        origin=rep['localization_hint'].get('start_evidence_id')
                        if origin and origin not in event_map[key]: issues.append('hint_origin')
                proof_id=item.get('execution_evidence_id')
                if proof_id:
                    proof=event_map[sid,cid].get(proof_id)
                    if proof is None or str(proof['args'].get('drone_id'))!=item['actor_id']: issues.append('execution_actor_binding')
                if item['execution_state']=='evidenced' and (item['relation']!='visit' or item['execution_evidence_kind']!='current_actor_positive_contact_receipt'):
                    issues.append('completion_scope')
        for item in evidence['requirements']: scopes[item['execution_state']]+=1
        wire=t['model_requests_wire']; middleware=evidence['model_requests']; prefix_eligible=prefix_preserved=0
        for i,req in enumerate(wire):
            if req['scope']!='serialized_http_request_body' or len(req['body_sha256'])!=64 or not req['message_sha256']: issues.append('wire_audit')
            if i:
                prev=wire[i-1]['message_sha256']; common=0
                for x,y in zip(prev,req['message_sha256']):
                    if x!=y: break
                    common+=1
                if common!=req['common_prefix_messages'] or len(prev)!=req['previous_message_count']: issues.append('wire_prefix_metadata')
                prefix_eligible+=1; prefix_preserved+=int(common==len(prev))
        audits.append({'session_id':sid,'task_id':r['task_id'],'mode':t['runtime']['task_progress_mode'],
          'active':t['runtime']['task_progress_active'],'observer_errors':progress['observer_errors'],
          'request_audit_errors':t['model_request_audit_errors'],'binding_issues':issues,'references_checked_with_duplicates':reports,
          'internal_execution_states_diagnostic_only':dict(scopes),'wire_requests':len(wire),'middleware_requests':len(middleware),
          'wire_previous_prefix_preserved':prefix_preserved,'wire_previous_prefix_eligible':prefix_eligible,
          'mission_completion':progress['mission_completion']})
    assert all(not a['binding_issues'] for a in audits), 'Review audit findings before publishing report'
    counts=collections.Counter(p['pair_outcome_diagnostic'] for p in pairs)
    scenes=[]
    for s in plan['sessions']:
        rs=[p for p in pairs if p['session_id']==s['id']]; c=collections.Counter(p['pair_outcome_diagnostic'] for p in rs)
        scenes.append({'session_id':s['id'],'name':s['name'],'m0_pass':sum(p['m0_pass'] for p in rs),
                       'm1r_observed_pass':sum(p['m1r_observed_pass'] for p in rs),'W':c['W'],'L':c['L'],
                       'net':c['W']-c['L'],'scene_seal_valid':rs[0]['m1r_scene_seal_valid'],
                       'm0_seconds':sum(p['m0_scoring_ready_seconds'] for p in rs),'m1r_seconds':sum(p['m1r_scoring_ready_seconds'] for p in rs),
                       'm0_tokens':sum(p['m0_total_tokens'] for p in rs),'m1r_tokens':sum(p['m1r_total_tokens'] for p in rs)})
    usage=perf['selected_generations']['llm']['usage']
    for k in ('prompt_tokens','completion_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens'):
        assert sum(r['observed_token_usage'][k] for r in rows)==usage[k]
    summary={'status':'diagnostic_only_integrity_failure','run_accepted':False,'fixed_tasks':100,'scored_tasks':100,'unscored':0,
      'checks':605,'observed_m1r_pass':81,'historical_m0_pass':80,'observed_success_rate':.81,
      'paired_diagnostic':dict(counts),'net_tasks_diagnostic':counts['W']-counts['L'],'scenes':scenes,
      'method_commit':study['methods']['m1r']['agent_commit'],'attempt':str(A.relative_to(ROOT)),
      'registry_status':registry['m1r']['status'],'manifest_status':manifest['status'],
      'ended_at':manifest['finished_at'],'stop_triggered':False,'unscored_imputed_as_failures':False,
      'm0':reference['summary'],'m1_v1_historical':previous_summary['arms']['m1'],
      'm1r':{'observed_tokens':usage,'cache_hit_ratio':usage['cache_hit_tokens']/usage['prompt_tokens'],
              'wall_seconds':sum(x['wall_seconds'] for x in perf['invocations']),
              'scoring_ready_seconds_sum':sum(p['m1r_scoring_ready_seconds'] for p in pairs),
              'passed_checks':sum(p['m1r_passed_checks'] for p in pairs),'timeouts':sum(r['agent_timed_out'] for r in rows),
              'llm_completed_calls':perf['selected_generations']['llm']['completed_calls'],
              'llm_failed_calls':perf['selected_generations']['llm']['failed_calls'],'http_errors':perf['selected_generations']['http']['errors']},
      'evidence_audit':{'traces':len(audits),'active_evidence':sum(a['mode']=='evidence' and a['active'] for a in audits),
          'observer_errors':sum(len(a['observer_errors']) for a in audits),'binding_issues':sum(len(a['binding_issues']) for a in audits),
          'wire_requests':sum(a['wire_requests'] for a in audits),'middleware_requests':sum(a['middleware_requests'] for a in audits),
          'wire_previous_prefix_preserved':sum(a['wire_previous_prefix_preserved'] for a in audits),
          'wire_previous_prefix_eligible':sum(a['wire_previous_prefix_eligible'] for a in audits)},
      'recommendation':'do_not_launch_dev240_yet; retain_diagnostic_results; review_integrity_and_three_regressions_first',
      'limitations':['historical_M0_not_contemporaneous','five_development_scenes','world_and_blackboard_carryover',
                     'single_attempt','budget_stop_policy_not_significance_test','full_suite_not_accepted',
                     'observed_provider_tokens_not_invoice_cost','hashes_recorded_not_full_wire_payload_reconstruction']}
    assert sum(r['passed'] for r in rows)==summary['observed_m1r_pass']
    write('summary.json',summary);write('paired-tasks.json',pairs);write('evidence-audit.json',audits)
    with (OUT/'paired-tasks.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(pairs[0]));writer.writeheader();writer.writerows(pairs)
    write('verification.json',{'checked_at':datetime.now().astimezone().isoformat(),'run_accepted':False,
      'frozen_full_verify_not_run_reason':'registry is interrupted, not completed_and_verified; full_scene_verify below reproduces failure',
      'terminal_suite_seal':{'matches':True,'files':len(suite)},'scene_seals':scene_audits,
      'archived_component_validation':component_verification,'model_calls_during_analysis':0,
      'original_artifacts_modified':False,'guard_source_sha256':sha(raw(ROOT/'experiments/analysis/m1_revision_guard.py')),
      'guard_policy_sha256':sha(raw(J/'guard-policy.json')),'stop_trigger_exists':(J/'stop-trigger.json').exists()})
    write('input-sha256.json',INPUTS)
    print(json.dumps({'status':summary['status'],'paired':dict(counts),'scenes':scenes,'m1r':summary['m1r'],'audit':summary['evidence_audit'],'inputs':len(INPUTS)},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
