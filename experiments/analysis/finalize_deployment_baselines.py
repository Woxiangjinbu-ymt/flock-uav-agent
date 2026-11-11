"""Offline, outcome-independent reconciliation; never changes raw run evidence.

First-attempt deployment results are primary. Missing outcomes are policy
failures, NOT fabricated checker responses. Run with controller/.venv/bin/python.
"""
import collections
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import reproduction as r
import reproduction_parallel as rp
import reproduction_metrics as rm

OUT = ROOT / 'docs/results/baseline-deployment-20261005'
RUNS = {
    'deepseek': ROOT / 'experiments/runs/20261003-143255-full-5c501118',
    'qwen': ROOT / 'experiments/runs/20261004-142512-full-aca112a7',
}
Q_SNAPSHOT = ROOT / 'experiments/recovery/qwen38-resume-20261005-133052-03-before/manifest.json'
EVIDENCE = {}
CACHE = {}
VERIFICATION = collections.Counter()


def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def record(p):
    p = Path(p)
    key = str(p.relative_to(ROOT))
    if key not in EVIDENCE:
        EVIDENCE[key] = sha(p)
    return EVIDENCE[key]


def read(p):
    record(p)
    return json.loads(Path(p).read_text())


def save(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def metrics(rows):
    n = len(rows)
    checks = sum(x['expected_checks'] for x in rows)
    passed = sum(x['task_pass'] for x in rows)
    strict = sum(x['deployment_pass'] for x in rows)
    return dict(tasks=n, checks=checks, passed_tasks=passed, task_pass=passed/n,
                deployment_passed_tasks=strict, deployment_pass=strict/n,
                passed_checks=sum(x['credited_checks'] for x in rows),
                avg_check=sum(x['credited_checks']/x['expected_checks'] for x in rows)/n,
                global_check=sum(x['credited_checks'] for x in rows)/checks,
                zero_check_tasks=sum(x['credited_checks']==0 for x in rows),
                total_failed=sum(x['credited_checks']==0 for x in rows)/n,
                agent_timeouts=sum(x['agent_timed_out'] is True for x in rows),
                timeouts_with_task_pass=sum(x['agent_timed_out'] is True and x['task_pass'] for x in rows),
                checker_scored_tasks=sum(x['original_status']=='scored' for x in rows),
                imputed_failure_tasks=sum(x['accounting_reason']!='observed_checker' for x in rows),
                unscored_runtime_failure_tasks=sum(x['accounting_reason']=='unscored_execution_stop' for x in rows),
                provider_failure_tasks=sum(x['accounting_reason']=='terminal_provider_error' for x in rows),
                scored_without_worker_confirmation=sum(x['original_status']=='scored' and not x['worker_confirmed'] for x in rows),
                agent_success_but_check_fail=sum(x['agent_success'] is True and x['original_passed'] is False for x in rows),
                near_miss_observed=sum(x['accounting_reason']=='observed_checker' and not x['task_pass'] and 0<x['credited_checks']==x['expected_checks']-1 for x in rows))


def verify_part(model, scene, directory):
    key = str(directory)
    if key in CACHE:
        return CACHE[key]
    a = RUNS[model]
    rows = read(directory/'results.json')
    state = read(directory/'scene.json')
    source = read(r.DATA/scene['file'])
    assert sha(r.DATA/scene['file']) == scene['sha256']
    tasks = {t['id']:t for t in source['tasks']}
    assert [(x['session_id'],x['task_id']) for x in rows] == [(scene['id'],t['id']) for t in scene['tasks']]
    seal = None
    if (directory/'complete.json').exists():
        seal = read(directory/'complete.json')
        assert seal['session_id'] == scene['id']
        assert seal['identity_sha256'] == read(a/'manifest.json')['identity_sha256']
        assert seal['plan_digest'] == rp.json_digest(r, rp.scene_plan(read(a/'plan.json'),scene))
        VERIFICATION['available_scene_seals_checked'] += 1
    for row, task in zip(rows,scene['tasks']):
        assert row['expected_checks'] == len(task['leaves'])
        assert [{'path':x['path'],'definition':x['definition']} for x in row['leaves']] == task['leaves']
        if row['status'] != 'scored':
            continue
        tid = row['task_id']
        for suffix in ('before','after','checks','job','barrier','window'):
            record(directory/f'{tid}.{suffix}.json')
        barrier = read(directory/f'{tid}.barrier.json')
        window = read(directory/f'{tid}.window.json')
        assert barrier['quiescent'] is True and barrier['inflight']==0 and barrier['simulation_paused'] is True
        assert barrier['lease']==window['lease'] and barrier['cutoff_at']==window['cutoff_at']
        if row.get('continuation_safe'):
            worker = read(directory/f'{tid}.agent-state.json')
            assert worker['worker_finished'] is True and worker['busy'] is False and worker['job_id']==row['job_id']
        else:
            assert 'Worker did not exit' in state.get('error','')
            VERIFICATION['scored_rows_with_world_barrier_but_no_worker_confirmation'] += 1
        if row['agent_timed_out']:
            record(directory/f'{tid}.timeout.json')
        saved = read(directory/f'{tid}.checks.json')
        leaves = [x for x in saved if x.get('type')=='leaf']
        remaining = iter(leaves)

        class SavedAPI:
            def api_check_execution(self, endpoint, parameters, show_error=False):
                leaf = next(remaining)
                assert endpoint == leaf['endpoint']
                assert parameters == {**leaf['parameters'], 'session_id':scene['id']}
                return leaf['response']

        passed, checked = r.upstream_evaluate(SavedAPI(),tasks[tid]['execution_check_apis'],scene['id'])
        assert next(remaining,None) is None
        actual = [x for x in checked if x.get('type')=='leaf']
        assert passed == row['passed'] == row['task_pass']
        assert not any(x.get('error') for x in checked)
        assert [(x.get('result'),x.get('actual')) for x in actual] == [(x.get('result'),x.get('actual')) for x in row['leaves']]
        VERIFICATION['original_checker_replayed_scored_rows'] += 1
    telemetry = directory/'agent-traces'/rm.METRICS_FILENAME
    record(telemetry)
    perf = rm.summarize_performance([telemetry])
    assert perf['malformed_records']==0
    errors = collections.defaultdict(list)
    for line in telemetry.read_text().splitlines():
        e = json.loads(line)
        if e['event']=='llm_error' and isinstance(e.get('status_code'),int) and e['status_code']>=400:
            errors[e.get('job_id')].append({k:e.get(k) for k in ('timestamp','error_type','status_code','run_id')})
    if seal:
        # All consumed sealed evidence, including checker inputs and telemetry,
        # must still match its original seal. Large unconsumed traces are not rehashed.
        for path,checksum in list(EVIDENCE.items()):
            full = ROOT/path
            if full.is_relative_to(directory) and full != directory/'complete.json':
                assert seal['files'][str(full.relative_to(directory))] == checksum, path
    CACHE[key] = rows,state,perf,errors
    return CACHE[key]


def cohort(model, selection, label, plan):
    result = []; scenes = []; files = []
    for scene in plan['sessions']:
        directory = RUNS[model]/selection[scene['id']]
        raw,state,perf,errors = verify_part(model,scene,directory)
        files.append(directory/'agent-traces'/rm.METRICS_FILENAME)
        part = []
        for index,row in enumerate(raw,1):
            job = row.get('job_id')
            bad = bool(errors.get(job))
            scored = row['status']=='scored'
            if bad:
                reason='terminal_provider_error'
            elif not scored:
                assert ('Worker did not exit' in state.get('error','') or
                        'cannot assume execution has stopped' in state.get('error','')), state
                reason='unscored_execution_stop'
            else:
                reason='observed_checker'
            counted = scored and not bad
            passed = bool(row.get('passed')) if counted else False
            usage = perf['by_job'].get(job,{}).get('llm',{}).get('usage',{})
            seconds = None
            if scored:
                b=read(directory/f"{row['task_id']}.barrier.json")
                w=read(directory/f"{row['task_id']}.window.json")
                seconds=b['frozen_at']-w['opened_at']
            item=dict(model=model,cohort=label,session_id=scene['id'],scene_name=scene['name'],
                      family=scene['family'],scene_difficulty=re.search(r' (Easy|Intermediate|Moderate|Hard|Extreme) \d+$',scene['name']).group(1),
                      generation=1 if selection[scene['id']]==scene['id'] else int(directory.name),
                      evidence_dir=str(directory.relative_to(ROOT)),task_index=index,task_id=row['task_id'],name=row['name'],
                      task_difficulty=row['difficulty'],original_status=row['status'],original_passed=row.get('passed'),
                      expected_checks=row['expected_checks'],observed_passed_checks=sum(x['result'] is True for x in row['leaves']) if scored else None,
                      credited_checks=sum(x['result'] is True for x in row['leaves']) if counted else 0,
                      task_pass=passed,deployment_pass=passed and row.get('continuation_safe') is True,
                      worker_confirmed=row.get('continuation_safe') is True,agent_timed_out=row['agent_timed_out'],
                      agent_success=row.get('agent_success'),accounting_reason=reason,
                      failure_origin=state.get('error') if not scored or not row.get('continuation_safe') else None,
                      provider_errors=errors.get(job,[]),job_id=job,scoring_ready_seconds=seconds,observed_usage=usage,
                      original_leaves=row['leaves'])
            part.append(item)
        result.extend(part)
        scenes.append(dict(model=model,cohort=label,session_id=scene['id'],name=scene['name'],
                           generation=part[0]['generation'],evidence_dir=part[0]['evidence_dir'],
                           original_scene_status=state['status'],original_error=state.get('error'),**metrics(part)))
    assert len(result)==1500 and sum(x['expected_checks'] for x in result)==9396
    perf = rm.summarize_performance(files)
    timed=[x['scoring_ready_seconds'] for x in result if x['scoring_ready_seconds'] is not None]
    stats=dict(metrics=metrics(result),
               by_scene_difficulty={k:metrics([x for x in result if x['scene_difficulty']==k]) for k in ('Easy','Intermediate','Moderate','Hard','Extreme')},
               by_family={k:metrics([x for x in result if x['family']==k]) for k in sorted({x['family'] for x in result})},
               failure_reasons=dict(collections.Counter(x['accounting_reason'] for x in result)),
               scoring_ready_seconds=rm._latencies(timed),
               performance={k:v for k,v in perf.items() if k!='by_job'},
               observed_failed_checks_by_endpoint=dict(collections.Counter(y['definition']['endpoint'] for x in result if x['accounting_reason']=='observed_checker' for y in x['original_leaves'] if y['result'] is False).most_common()),
               imputed_failed_checks_by_endpoint=dict(collections.Counter(y['definition']['endpoint'] for x in result if x['accounting_reason']!='observed_checker' for y in x['original_leaves']).most_common()),
               scenes_with_execution_stop=sum(bool(s['original_error']) for s in scenes))
    return result, scenes, stats


def csv_write(path, rows, fields):
    with Path(path).open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for row in rows:
            w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items() if k in fields})


def run():
    OUT.mkdir(exist_ok=True,parents=True)
    with rp.runner_lock(r),rp.attempt_lock(RUNS['deepseek'],exclusive=False),rp.attempt_lock(RUNS['qwen'],exclusive=False):
        plans={k:read(a/'plan.json') for k,a in RUNS.items()}
        assert plans['deepseek']==plans['qwen']
        manifests={k:read(a/'manifest.json') for k,a in RUNS.items()}
        assert manifests['qwen']['status']=='interrupted'
        assert all(m['status']!='running' for m in manifests.values())
        for a in RUNS.values():
            rp.verify_archive(r,a)
            for f in ('identity.json','source_hashes.json','configuration.json','summary.json','performance.json'):
                record(a/f)
        plan=plans['qwen']
        q_before=read(Q_SNAPSHOT)
        assert q_before['status']=='aborted'
        gen1={s['id']:s['id'] for s in plan['sessions']}
        ds_selected={sid:rec['directory'] for sid,rec in manifests['deepseek']['scenes'].items()}
        q_selected={sid:rec['directory'] for sid,rec in q_before['scenes'].items()}
        # Runtime interruptions are never replaced in this audit's DS closure.
        # Six explicit billing/rate-limit recovery scenes remain a separate sensitivity cohort.
        ds_runtime_no_replay=dict(ds_selected)
        for scene in plan['sessions']:
            sid=scene['id']; state=read(RUNS['deepseek']/sid/'scene.json')
            if 'cannot assume execution has stopped' in state.get('error',''):
                ds_runtime_no_replay[sid]=sid
        specs={
            'first_attempt_no_replay':{'deepseek':gen1,'qwen':gen1},
            'provider_recovered_runtime_no_replay':{'deepseek':ds_runtime_no_replay,'qwen':gen1},
            'recovery_snapshot_remaining_fail':{'deepseek':ds_runtime_no_replay,'qwen':q_selected},
        }
        allrows=[];allscenes=[];summary={};first={}
        for label,selections in specs.items():
            summary[label]={}
            for model,selection in selections.items():
                rows,scenes,stats=cohort(model,selection,label,plan)
                allrows.extend(rows);allscenes.extend(scenes);summary[label][model]=stats
                if label=='first_attempt_no_replay':first[model]=rows
                print(label,model,json.dumps(stats['metrics']),flush=True)
        # Raw historical recovered score retained explicitly, not silently overwritten.
        legacy=read(ROOT/'docs/results/full-round1/results.json')
        summary['legacy_deepseek_recovered']={'metrics':legacy['metrics'],'source':'docs/results/full-round1/results.json'}
        pairs=collections.Counter()
        for d,q in zip(first['deepseek'],first['qwen']):
            assert (d['session_id'],d['task_id'])==(q['session_id'],q['task_id'])
            pairs['both_pass' if d['deployment_pass'] and q['deployment_pass'] else 'deepseek_only' if d['deployment_pass'] else 'qwen_only' if q['deployment_pass'] else 'both_fail']+=1
        costs={}
        for model,a in RUNS.items():
            m=manifests[model]
            allperf=rm.summarize_performance(a)
            assert allperf['malformed_records']==0
            stored=read(a/'performance.json')['all_executed_generations']
            assert allperf==stored
            for p in a.rglob(rm.METRICS_FILENAME):record(p)
            costs[model]=dict(first_invocation_wall_seconds=m['invocations'][0]['wall_seconds'],
                              all_invocations_wall_seconds=sum(x['wall_seconds'] for x in m['invocations']),
                              calendar_elapsed_seconds=(dt.datetime.fromisoformat(m['finished_at'])-dt.datetime.fromisoformat(m['started_at'])).total_seconds(),
                              invocations=m['invocations'],all_executed_performance={k:v for k,v in allperf.items() if k!='by_job'},
                              fleet_resources=read(a/'performance.json')['fleet_resources'])
        frozen={model:read(a/'source_hashes.json') for model,a in RUNS.items()}
        source_differences=[k for k in set(frozen['qwen'])|set(frozen['deepseek']) if frozen['qwen'].get(k)!=frozen['deepseek'].get(k)]
        assert not [k for k in source_differences if k.startswith(('agent4drone/','server/','controller/'))]
        report=dict(protocol='deployment-no-scene-replay-v1',created_at=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
                    accounting_complete=True,planned_scenes=75,planned_tasks=1500,planned_checks=9396,
                    note='Post-hoc protocol amendment explicitly requested by the user. Raw outcomes and seals unchanged; not an original valid_complete_run.',
                    original_attempt_status={k:m['status'] for k,m in manifests.items()},
                    original_valid_complete_run={k:read(a/'summary.json')['valid_complete_run'] for k,a in RUNS.items()},
                    attempts={k:str(a.relative_to(ROOT)) for k,a in RUNS.items()},configuration={k:read(a/'configuration.json') for k,a in RUNS.items()},
                    frozen_protocol=plan['protocol'],executor={k:m['executor'] for k,m in manifests.items()},
                    summary=summary,costs=costs,paired_first_attempt_deployment=dict(pairs),
                    source_differences=sorted(source_differences),verification=dict(VERIFICATION),
                    policy=dict(main_selection='Every scene generation 1; no task splicing, no best-of selection',
                                missing='Any unscored task downstream of documented worker/continuation interruption contributes zero checks and fails',
                                provider='Terminal failed model API jobs contribute zero checks and fail in first-attempt deployment',
                                observed='Preserve saved checker scores for world-quiescent tasks, even if worker did not exit',
                                deployment_pass='Task check passed AND worker exit confirmed AND no terminal provider failure',
                                qwen_closure='Use fixed pre-generation-4 snapshot, no outcomes from manually stopped generation 4',
                                cost='All executed generations, including discarded and manually stopped replay, remain in actual observed usage'))
        save(OUT/'results.json',report)
        save(OUT/'tasks.json',allrows)
        fields=['model','cohort','session_id','scene_name','family','scene_difficulty','generation','task_index','task_id','name','task_difficulty','original_status','original_passed','expected_checks','observed_passed_checks','credited_checks','task_pass','deployment_pass','worker_confirmed','agent_timed_out','agent_success','accounting_reason','failure_origin','scoring_ready_seconds','observed_usage','provider_errors','evidence_dir']
        csv_write(OUT/'tasks.csv',allrows,fields)
        csv_write(OUT/'scenes.csv',allscenes,list(allscenes[0]))
        csv_write(OUT/'failure_accounting.csv',[x for x in allrows if x['accounting_reason']!='observed_checker' or not x['worker_confirmed']],fields)
        record(Path(__file__))
        save(OUT/'evidence-sha256.json',EVIDENCE)
        # Verify consumed inputs are stable once all outputs have been assembled.
        assert all(sha(ROOT/p)==digest for p,digest in EVIDENCE.items())
        save(OUT/'verification.json',{'all_consumed_evidence_hashes_rechecked':True,'evidence_files':len(EVIDENCE),**dict(VERIFICATION)})
        print('OFFLINE ACCOUNTING COMPLETE',OUT,flush=True)


if __name__=='__main__':
    run()
