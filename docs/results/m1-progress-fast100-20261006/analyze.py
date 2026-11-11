"""Read sealed M0/M1 evidence and write derived analysis only in this directory.

No model/API calls, replay, rescoring, or source mutations. CSV is exported by
the adjacent Artifact Tool builder from paired-tasks.json.
"""
import collections
import hashlib
import json
import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
INPUTS = {}


def read(path):
    path = Path(path)
    raw = path.read_bytes()
    INPUTS[str(path.relative_to(ROOT))] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def decode(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return x
    return x


def args(x):
    x = decode(x)
    if isinstance(x, dict) and set(x) == {'input_json'}:
        x = decode(x['input_json'])
    return x if isinstance(x, dict) else {}


def signature(tool, arguments, payload):
    return json.dumps([tool, args(arguments), decode(payload)], sort_keys=True, ensure_ascii=False)


def audit_trace(d, path):
    g = d.get('task_progress')
    out = {'trace': str(path.relative_to(ROOT)), 'trace_status': d['status'],
           'mode': d['runtime'].get('task_progress_mode'),
           'active': d['runtime'].get('task_progress_active'),
           'ledger_present': g is not None, 'tool_chain_steps': len(d['tool_chain'])}
    if g is None:
        return out
    issues = []
    events = g['events']; entities = g['entities']; clauses = g['clauses']
    eids = {e['evidence_id'] for e in events}; cids = {c['id'] for c in clauses}
    if len(eids) != len(events): issues.append('duplicate_evidence_id')
    if g['command_id'] != d['command_id'] or g['command'] != d['command']:
        issues.append('command_identity')
    for c in clauses:
        if d['command'][c['start']:c['end']] != c['text']: issues.append('source_span')
        if c['requirement_status'] != 'not_evaluated': issues.append('requirement_verdict')
    for name, binding in g['entity_bindings'].items():
        expected = sorted((e['kind'], e['id']) for e in entities.values() if e.get('name') == name)
        actual = sorted((e['kind'], e['id']) for e in binding['entities'])
        if expected != actual: issues.append('name_binding')
        state = 'exact' if len(expected) == 1 else 'ambiguous' if expected else 'unresolved'
        if binding['state'] != state: issues.append('binding_state')
    for entity in entities.values():
        for f in entity['facts'].values():
            if f['evidence_id'] != 'inherited' and f['evidence_id'] not in eids:
                issues.append('dangling_fact_evidence')
            if f.get('invalidated_by') and f['invalidated_by'] not in eids:
                issues.append('dangling_invalidation')
    for e in events:
        touched = {str(e['args'][k]) for k in ('drone_id','reference_drone_id','target_id','obstacle_id') if e['args'].get(k)}
        touched.update(e['entity_ids'])
        touched.update(str(x) for x in e['args'].get('drone_ids', []))
        expected = [c['id'] for c in clauses if any(
            ent['id'] in touched for n in c['entity_names'] for ent in g['entity_bindings'][n]['entities'])]
        if expected != e['source_clause_ids']: issues.append('clause_entity_link')
        if set(e['source_clause_ids']) - cids: issues.append('dangling_clause')
        if set(e['overlaps']) - eids: issues.append('dangling_overlap')
    # Compare whole receipts as multisets: parallel completion order can differ.
    recorded = collections.Counter(signature(t['tool'], t['tool_input'], t['observation']) for t in d['tool_chain'])
    observed = collections.Counter(signature(e['tool_name'], e['args'], e['payload']) for e in events if e['end_sequence'] is not None)
    matched = sum((recorded & observed).values())
    out.update(observer_errors=g['observer_errors'], events=len(events),
               pending_events=sum(e['end_sequence'] is None for e in events),
               matched_recorded_receipts=matched,
               unmatched_recorded_receipts=sum(recorded.values())-matched,
               ledger_events_without_recorded_toolchain=sum(observed.values())-matched,
               binding_counts=dict(collections.Counter(v['state'] for v in g['entity_bindings'].values())),
               clauses=len(clauses),
               clauses_without_explicit_entity=sum(not c['entity_names'] for c in clauses),
               events_linked_to_clause=sum(bool(e['source_clause_ids']) for e in events),
               fact_validity_counts=dict(collections.Counter(f['validity'] for e in entities.values() for f in e['facts'].values())),
               mission_completion=g['mission_completion'], structural_issues=issues)
    return out


def main():
    study = ROOT/'experiments/m1-progress-v1'
    runs = read(study/'runs.json'); job = read(study/'jobs/20261006-152635-7ddb4eb4/status.json')
    read(study/'jobs/20261006-152635-7ddb4eb4/request.json')
    frozen = read(study/'frozen/study.json'); fast = read(study/'frozen/fast100.json')
    assert job['status'] == 'completed'
    assert all(runs[a]['status'] == 'completed_and_verified' for a in ('m0','m1'))
    keys = [(s['id'], t['id']) for s in fast['sessions'] for t in s['tasks']]
    assert len(keys) == len(set(keys)) == 100
    arms = {}; taskdata = {}; audits = {}; cases = []
    for arm in ('m0','m1'):
        a = Path(runs[arm]['attempt']); manifest = read(a/'manifest.json')
        rows = read(a/'results.json'); perf = read(a/'performance.json'); score = read(a/'summary.json')
        for name in ('suite-seal.json','plan.json','method-freeze.json','execution-environment.json','source_hashes.json','fast100.json'):
            read(a/name)
        idx = {(r['session_id'],r['task_id']):r for r in rows}
        assert len(rows) == len(idx) == 100 and set(idx) == set(keys)
        assert all(r['status'] == 'scored' for r in rows)
        assert sum(r['expected_checks'] for r in rows) == 605
        traces = {}
        for scene in manifest['scenes']:
            read(a/scene/'complete.json'); read(a/scene/'performance.json')
            for p in sorted((a/scene/'agent-traces/toolchains').glob('*.json')):
                d = read(p); k = (scene,d['command_id'])
                assert k not in traces
                traces[k] = (d,p)
        vals = {}; arm_audits = []; cumulative = 0
        for scene,tid in keys:
            r = idx[scene,tid]
            w = read(a/scene/(tid+'.window.json')); b = read(a/scene/(tid+'.barrier.json'))
            saved_job = read(a/scene/(tid+'.job.json'))
            assert saved_job['job_id'] == r['job_id']
            matches = [(d,p) for (sc,_),(d,p) in traces.items() if sc==scene and d['command']==saved_job['command']
                       and w['opened_at'] <= datetime.fromisoformat(d['started_at']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() <= b['frozen_at']]
            assert len(matches) == 1, (arm,scene,tid,len(matches))
            d,p = matches[0]
            sec = b['frozen_at']-w['opened_at']; cumulative += sec
            usage = r['observed_token_usage']
            v = {'status': r['status'], 'pass': r['task_pass'], 'passed_checks': sum(x['result'] is True for x in r['leaves']),
                 'timeout': r['agent_timed_out'], 'scoring_ready_seconds': sec,
                 'job_id': r['job_id'], 'trace_status': d['status'], 'recorded_tool_steps': len(d['tool_chain']),
                 **usage}
            vals[scene,tid] = (v,r,d,p)
            au = audit_trace(d,p); au.update(session_id=scene,task_id=tid); arm_audits.append(au)
        g = perf['selected_generations']; u = g['llm']['usage']
        for field in ('prompt_tokens','completion_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens','calls_with_usage'):
            assert sum(v[0][field] for v in vals.values()) == u[field], (arm,field)
        assert g['llm']['failed_calls'] == 0 and g['http']['errors'] == 0
        arms[arm] = {'attempt': str(a.relative_to(ROOT)), 'method_commit': manifest['agent_commit'],
            'started_at': manifest['started_at'], 'finished_at': manifest['finished_at'],
            'tasks':100, 'checks':605, 'passed_tasks':sum(r['task_pass'] for r in rows),
            'task_success_rate':sum(r['task_pass'] for r in rows)/100,
            'passed_checks':sum(v[0]['passed_checks'] for v in vals.values()),
            'timeouts':sum(r['agent_timed_out'] for r in rows),
            'timeouts_passed':sum(r['agent_timed_out'] and r['task_pass'] for r in rows),
            'wall_seconds':sum(x['wall_seconds'] for x in perf['invocations']),
            'scoring_ready_seconds_sum':cumulative,
            'observed_tokens':u, 'cache_hit_ratio':u['cache_hit_tokens']/u['prompt_tokens'],
            'llm_calls':g['llm']['completed_calls'], 'llm_failed_calls':g['llm']['failed_calls'],
            'http_errors':g['http']['errors'], 'resource_sample_errors':g['resources']['errors'],
            'unscored':0, 'trace_count':len(traces)}
        taskdata[arm] = vals; audits[arm] = arm_audits
    paired = []
    for scene,tid in keys:
        v0,r0,d0,p0 = taskdata['m0'][scene,tid]; v1,r1,d1,p1 = taskdata['m1'][scene,tid]
        outcome = 'W' if not v0['pass'] and v1['pass'] else 'L' if v0['pass'] and not v1['pass'] else 'both_pass' if v0['pass'] else 'both_fail'
        row = {'session_id':scene,'task_id':tid,'name':r0['name'],'family':r0['family'],'difficulty':r0['difficulty'],'expected_checks':r0['expected_checks'],'pair_outcome':outcome}
        for arm,v,p in [('m0',v0,p0),('m1',v1,p1)]:
            row.update({arm+'_'+k:v[k] for k in ('status','pass','passed_checks','timeout','scoring_ready_seconds','prompt_tokens','completion_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens','calls_with_usage','trace_status','recorded_tool_steps')})
            row[arm+'_trace'] = str(p.relative_to(ROOT))
        paired.append(row)
        if outcome in ('W','L'):
            c = {'session_id':scene,'task_id':tid,'outcome':outcome,'command':d1['command']}
            for arm,r,d,p in [('m0',r0,d0,p0),('m1',r1,d1,p1)]:
                a=Path(runs[arm]['attempt']); before=read(a/scene/(tid+'.before.json')); after=read(a/scene/(tid+'.after.json'))
                target_states=[]
                for leaf in r['leaves']:
                    q=leaf['definition']['parameters']; target_id=q.get('target_id')
                    if not target_id: continue
                    rec={'check_path':leaf['path'],'endpoint':leaf['definition']['endpoint'],'target_id':target_id,'drone_id':q.get('drone_id'),'result':leaf['result']}
                    for label,world in [('before',before),('after',after)]:
                        target=next(t for t in world['targets'] if t['id']==target_id)
                        rec[label]={k:target.get(k) for k in ('name','is_reached','reached_by')}
                    target_states.append(rec)
                c[arm]={'trace':str(p.relative_to(ROOT)), 'failed_checks':[x for x in r['leaves'] if not x['result']],
                    'target_states':target_states,
                    'output':d['output'], 'tools':[{'tool':t['tool'],'args':args(t['tool_input'])} for t in d['tool_chain']],
                    'api_count':len(d['api_calls']), 'before_keys':list(before), 'after_keys':list(after),
                    'ledger_bindings': d.get('task_progress',{}).get('entity_bindings'),
                    'ledger_events':[{k:e.get(k) for k in ('evidence_id','tool_name','args','status','source_clause_ids')} for e in d.get('task_progress',{}).get('events',[])]}
            cases.append(c)
    scenes=[]
    for s in fast['sessions']:
        rs=[r for r in paired if r['session_id']==s['id']]; count=collections.Counter(r['pair_outcome'] for r in rs)
        scenes.append({'session_id':s['id'],'name':s['name'],'family':s['family'],'tasks':len(rs),
            'm0_pass':sum(r['m0_pass'] for r in rs),'m1_pass':sum(r['m1_pass'] for r in rs),
            'W':count['W'],'L':count['L'],'net':count['W']-count['L'],
            'm0_seconds_sum':sum(r['m0_scoring_ready_seconds'] for r in rs),'m1_seconds_sum':sum(r['m1_scoring_ready_seconds'] for r in rs),
            'm0_tokens':sum(r['m0_total_tokens'] for r in rs),'m1_tokens':sum(r['m1_total_tokens'] for r in rs)})
    counts=dict(collections.Counter(r['pair_outcome'] for r in paired))
    m1a=audits['m1']; agg={k:sum(x.get(k,0) for x in m1a) for k in ('events','pending_events','tool_chain_steps','matched_recorded_receipts','unmatched_recorded_receipts','ledger_events_without_recorded_toolchain','clauses','clauses_without_explicit_entity','events_linked_to_clause')}
    agg.update(traces=len(m1a),active_summary_traces=sum(x['mode']=='summary' and x['active'] is True for x in m1a),observer_errors=sum(len(x['observer_errors']) for x in m1a),structural_issues=sum(len(x['structural_issues']) for x in m1a),binding_counts=dict(sum((collections.Counter(x['binding_counts']) for x in m1a),collections.Counter())),mission_completion_counts=dict(collections.Counter(x['mission_completion'] for x in m1a)))
    result={'schema':1,'study':'m1-progress-fast100-v1','fixed_tasks':100,'fixed_checks':605,'arms':arms,'paired':counts,'net_tasks':counts['W']-counts['L'],'net_percentage_points':counts['W']-counts['L'],'scenes':scenes,'m1_audit':agg,
        'recommendation':'do_not_expand_frozen_m1_to_dev240_yet',
        'limitations':['development_set_5_scenes','scene_world_and_blackboard_carryover','fixed_m0_then_m1_order','one_run_per_arm','no_random_causal_or_significance_claim','source_clause_entity_links_are_not_task_completion_evidence','actual_per_request_injected_summary_not_saved','token_usage_is_observed_provider_usage_not_invoice_cost']}
    write('paired-tasks.json',paired);write('summary.json',result);write('evidence-audit.json',audits);write('changed-cases.json',cases)
    verifications={}
    for arm in arms:
        attempt=ROOT/arms[arm]['attempt']; runtime=attempt/'runtime'
        cmd=[str(runtime/'controller/.venv/bin/python'),'-B',str(runtime/'scripts/m1_progress_pilot.py'),'verify',str(attempt)]
        checked=subprocess.run(cmd,capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},check=True)
        result=json.loads(checked.stdout)
        assert result['valid_complete_run'] and result['scored_tasks']==100 and result['scored_checks']==605
        assert result['metrics']['passed_tasks']==arms[arm]['passed_tasks']
        verifications[arm]={'argv':cmd,'exit_code':checked.returncode,'result':result,'checked_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
    write('verification.json',{'frozen_verify':verifications,'paired_ledger_assertions':'100_unique_original_tasks_605_checks_full_pairing_and_provider_usage_totals_reconciled'})
    for arm in arms:
        a=ROOT/arms[arm]['attempt']
        for name in ('agent4drone/task_progress.py','agent4drone/uav_agent.py','scripts/m1_progress_pilot.py'):
            p=a/'runtime'/name
            if p.exists():INPUTS[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    write('input-sha256.json',INPUTS)
    print('W=2 L=4; 100 pairs; all observed usage totals reconcile; both frozen verifiers passed')


if __name__ == '__main__':
    main()
