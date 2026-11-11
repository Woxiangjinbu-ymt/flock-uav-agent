"""Read the official FlockCue first pass without changing the execution archive."""
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import base64
import csv
import gzip
import hashlib
import json
import random
import zlib
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
FROZEN = ROOT / 'experiments/plans/flockcue-600-first-pass.json'
HISTORY = ROOT / 'experiments/flockcue-benchmark-600-v1/historical-reference-600.json'
PLAN_SHA256 = '6589d8e1b4be6664cb3d651f09fe2cc54e41ffca7a361a6dae675ee455ec8823'
METRICS_FILE = 'agent-traces/reproduction-metrics.jsonl'


def read(path):
    return json.loads(path.read_bytes())


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_plan(attempt):
    if sha256(FROZEN) != PLAN_SHA256:
        raise ValueError('Frozen first-pass plan differs from registration')
    plan = read(FROZEN)
    if read(attempt / 'plan.json') != plan:
        raise ValueError('Attempt does not match the official 600-task order and protocol')
    return plan


def events(path, *, live=False):
    if not path.is_file():
        return
    with path.open('rb') as stream:
        for line in stream:
            if live and not line.endswith(b'\n'):
                break
            yield json.loads(line)


def status(attempt):
    """Stable scene files are published by rename. A JSONL tail might still be open."""
    plan = check_plan(attempt)
    manifest = read(attempt / 'manifest.json')
    scene_states, task_states, codes, error_types = Counter(), Counter(), Counter(), Counter()
    provider_names, actual = Counter(), Counter()
    active = []
    for scene in plan['sessions']:
        sid = scene['id']
        rec = manifest['scenes'][sid]
        scene_states[rec['status']] += 1
        directory = attempt / sid
        rows_path = directory / 'results.json'
        if not rows_path.exists():
            task_states['not_run'] += len(scene['tasks'])
            continue
        rows = read(rows_path)
        task_states.update(x['status'] for x in rows)
        for row in rows:
            if row['status'] == 'scored':
                actual['raw_checker_passed'] += row.get('passed') is True
            elif row['status'] in ('running', 'preparing'):
                active.append([sid, row['task_id'], row['status']])
        for ev in events(directory / METRICS_FILE, live=True):
            kind = ev['event']
            if kind == 'http_start':
                actual['http_dispatched'] += 1
            elif kind == 'http_end':
                codes[str(ev['status_code'])] += 1
                usage = ev.get('usage') or {}
                actual['http_usage_tokens'] += usage.get('total_tokens') or 0
            elif kind in ('http_error', 'llm_error'):
                error_types[f"{kind}/{ev.get('status_code')}/{ev.get('error_type')}"] += 1
            elif kind == 'llm_start':
                provider_names[ev.get('model') or 'unknown'] += 1
    return {'status': manifest['status'], 'planned': 600, 'scene_states': dict(scene_states),
            'task_states': dict(task_states), **actual, 'http_status_codes': dict(codes),
            'error_types': dict(error_types), 'requested_models': dict(provider_names),
            'active_tasks': active}


def history_by_task(plan):
    ref = read(HISTORY)
    path = ROOT / ref['historic_task_file']
    if sha256(path) != ref['historic_task_file_sha256']:
        raise ValueError('Historical task file changed')
    ids = {(s['id'], t['id']) for s in plan['sessions'] for t in s['tasks']}
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['model'] == ref['model'] and r['cohort'] == ref['cohort']
                and (r['session_id'], r['task_id']) in ids]
    lookup = {(r['session_id'], r['task_id']): r for r in rows}
    if set(lookup) != ids or len(rows) != 600 or sum(r['task_pass'] == 'True' for r in rows) != ref['task_pass']:
        raise ValueError('Could not verify historical 506/600')
    return lookup


def verify_seal(attempt, scene, record, plan, manifest):
    """Stream large model archives while checking the original scene seal."""
    import reproduction as r
    import reproduction_parallel as rp
    p = attempt / scene['id']
    marker = p / 'complete.json'
    if record['status'] != 'completed' and not marker.exists():
        return False
    seal = read(marker)
    if (seal['identity_sha256'] != manifest['identity_sha256']
            or seal['plan_digest'] != rp.json_digest(r, rp.scene_plan(plan, scene))
            or seal['session_id'] != scene['id']
            or (record.get('seal_sha256') and sha256(marker) != record['seal_sha256'])):
        raise ValueError(f'Incorrect completion seal for {scene["id"]}')
    files = {q.relative_to(p).as_posix() for q in p.rglob('*') if q.is_file() and q != marker}
    if files != set(seal['files']):
        raise ValueError(f'Files differ from completion seal in {p}')
    for name, expected in seal['files'].items():
        q = rp.safe_directory(p, name)
        if q.is_symlink() or sha256(q) != expected:
            raise ValueError(f'Sealed evidence changed: {q}')
    return True


def replay_score(folder, source, raw, scene_id):
    """Replay the upstream boolean tree against the same frozen endpoint responses."""
    import reproduction as r
    tid = raw['task_id']
    barrier = read(folder / f'{tid}.barrier.json')
    window = read(folder / f'{tid}.window.json')
    if (barrier['lease'] != window['lease'] or barrier['cutoff_at'] != window['cutoff_at']
            or barrier['quiescent'] is not True or barrier['simulation_paused'] is not True or barrier['inflight'] != 0):
        raise ValueError(f'Cannot verify freeze for {scene_id}/{tid}')
    if raw.get('continuation_safe'):
        worker = read(folder / f'{tid}.agent-state.json')
        if worker['job_id'] != raw['job_id'] or worker['worker_finished'] is not True or worker['busy'] is not False:
            raise ValueError(f'Cannot verify worker exit for {scene_id}/{tid}')
    saved = [leaf for leaf in read(folder / f'{tid}.checks.json') if leaf['type'] == 'leaf']
    items = iter(saved)

    class SavedAPI:
        def api_check_execution(self, endpoint, parameters, show_error=False):
            leaf = next(items)
            if endpoint != leaf['endpoint'] or parameters != {**leaf['parameters'], 'session_id': scene_id}:
                raise ValueError(f'Checker request differs for {scene_id}/{tid}')
            return leaf['response']

    passed, checked = r.upstream_evaluate(SavedAPI(), source['execution_check_apis'], scene_id)
    replayed = [leaf for leaf in checked if leaf['type'] == 'leaf']
    observation = lambda leaves: [(v['result'], v['actual']) for v in leaves]
    if (next(items, None) is not None or passed != raw['passed'] or passed != raw['task_pass']
            or any(x.get('error') for x in checked) or observation(saved) != observation(replayed)
            or observation(saved) != observation(raw['leaves'])):
        raise ValueError(f'Independent checker replay failed for {scene_id}/{tid}')
    return barrier['frozen_at'] - window['opened_at']


def provider_payload(raw, header):
    if header['body_scope'] == 'httpx_stream_before_content_decoding':
        enc = header['content_encoding']
        if enc == 'gzip':
            raw = gzip.decompress(raw)
        elif enc == 'deflate':
            raw = zlib.decompress(raw)
        elif enc != 'identity':
            raise ValueError(f'Unsupported wire content-encoding {enc}')
    if raw.lstrip().startswith((b'data:', b':')):
        return [json.loads(line[5:].strip()) for line in raw.splitlines()
                if line.startswith(b'data:') and line[5:].strip() not in (b'', b'[DONE]')]
    return [json.loads(raw)]


def archive_usage(path):
    """Keep per-response facts only; discard each response body after validating its hash."""
    cmd = None
    attempts, replies, tools = {}, [], Counter()
    first_request = None
    ending, issues = {}, Counter()
    request_count = 0
    with path.open('rb') as stream:
        for line in stream:
            # Large wire requests/model inputs precede their JSON payload with this event header.
            header = line[:800].split(b',"data":', 1)[0]
            is_request = b'"kind":"http_request"' in header
            if is_request:
                request_count += 1
            if not (is_request and request_count == 1) and not any(key in header for key in (b'"kind":"http_response_', b'"kind":"tool_start"',
                                                   b'"kind":"archive_start"', b'"kind":"archive_end"')):
                continue
            if not line.endswith(b'\n'):
                issues['unclosed_jsonl_tail'] += 1
                break
            event = json.loads(line)
            kind, data = event['kind'], event['data']
            if kind == 'archive_start':
                cmd = event['command_id']
            elif kind == 'http_request':
                capture = data.get('body')
                if capture is not None:
                    blob = base64.b64decode(capture['base64'], validate=True)
                    if len(blob) != capture['length'] or hashlib.sha256(blob).hexdigest() != capture['sha256']:
                        raise ValueError(f'Cannot verify first model request in {path}')
                    request = json.loads(blob)
                    functions = {x['function']['name']: x['function'] for x in request.get('tools', [])}
                    sense = functions.get('sense_nearby_entities', {})
                    first_request = {
                        'request_sha256': capture['sha256'], 'model': request.get('model'),
                        'temperature': request.get('temperature'), 'has_harness': 'run_harness' in functions,
                        'sense_description_sha256': hashlib.sha256(sense.get('description','').encode()).hexdigest(),
                        'sense_required': sense.get('parameters',{}).get('required', []),
                        'pilot_overrides_present': sorted({'thinking', 'reasoning_effort', 'max_tokens',
                                                          'max_completion_tokens'} & request.keys()),
                    }
            elif kind == 'tool_start':
                tools[data['input']['name']] += 1
            elif kind == 'archive_end':
                ending = data
            elif kind == 'http_response_start':
                attempts[data['attempt_id']] = (data, bytearray())
            elif kind == 'http_response_chunk':
                attempts[data['attempt_id']][1].extend(base64.b64decode(data['body']['base64'], validate=True))
            else:
                item = attempts.pop(data['attempt_id'], None)
                if item is None or not data['complete']:
                    issues['incomplete_response'] += 1
                    continue
                info, body = item
                if len(body) != data['length'] or hashlib.sha256(body).hexdigest() != data['sha256']:
                    raise ValueError(f'Captured response hash mismatch in {path}')
                reply = {'attempt_id': data['attempt_id'], 'status_code': info['status_code'], 'body_sha256': data['sha256']}
                if 200 <= info['status_code'] < 300:
                    try:
                        chunks = provider_payload(bytes(body), info)
                    except (ValueError, OSError, zlib.error) as exc:
                        issues['unparsed_response_' + type(exc).__name__] += 1
                        chunks = []
                    for chunk in chunks:
                        for key in ('usage', 'model', 'system_fingerprint'):
                            if chunk.get(key) is not None:
                                reply[key] = chunk[key]
                        for choice in chunk.get('choices', []):
                            if choice.get('finish_reason'):
                                reply['finish_reason'] = choice['finish_reason']
                replies.append(reply)
    if attempts:
        issues['incomplete_response'] += len(attempts)
    return cmd, {'responses': replies, 'tools': tools, 'closed': bool(ending), 'complete': ending.get('complete'),
                 'archive_end': ending, 'request_count': request_count,
                 'issues': dict(issues), 'archive': path.name, 'first_request': first_request}


def peak_factor(timestamp):
    local = datetime.fromtimestamp(timestamp, ZoneInfo('Asia/Shanghai'))
    if (local.year, local.month) != (2026, 11):
        raise ValueError('Recheck fee schedule for this run date')
    return 1 if local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18) else 0.5


def fee_bounds(usage, factors):
    if not usage or usage.get('prompt_tokens') is None or usage.get('completion_tokens') is None:
        return {}
    pin, out = usage['prompt_tokens'], usage['completion_tokens']
    hit, miss = usage.get('cache_hit_tokens'), usage.get('cache_miss_tokens')
    if hit is None and miss is not None:
        hit = pin - miss
    if miss is None and hit is not None:
        miss = pin - hit
    if hit is None:
        low, high = (pin * .04 + out * 8), (pin * 2 + out * 8)
    else:
        if hit + miss != pin or min(hit, miss) < 0:
            raise ValueError('Invalid cache split for provider response')
        low = high = hit * .04 + miss * 2 + out * 8
    return {'fee_low_cny': low * min(factors) / 1e6, 'fee_high_cny': high * max(factors) / 1e6}


def telemetry(folder):
    from reproduction_metrics import extract_usage
    by_job = defaultdict(list)
    cmd_to_job, wire_responses, receipts = {}, {}, {}
    for ev in events(folder / METRICS_FILE):
        job = ev.get('job_id')
        if job:
            by_job[job].append(ev)
            if ev.get('command_id'):
                cmd = ev['command_id']
                if cmd in cmd_to_job and cmd_to_job[cmd] != job:
                    raise ValueError('A command maps to multiple jobs')
                cmd_to_job[cmd] = job
    for path in sorted((folder / 'agent-traces/modelio').glob('model-io-*.jsonl')):
        cmd, result = archive_usage(path)
        if cmd not in cmd_to_job:
            # A command may end during initialization, before its first LLM callback.
            if result['responses']:
                raise ValueError(f'Unattributed wire responses in {path}')
            continue
        job = cmd_to_job[cmd]
        if job in receipts:
            raise ValueError(f'Duplicate model archive for {job}')
        receipts[job] = result
    calls = []
    for job, entries in by_job.items():
        starts = [e for e in entries if e['event'] == 'http_start']
        http_ends = [e for e in entries if e['event'] == 'http_end']
        ends = {e['request_id']: e for e in entries if e['event'] in ('http_end', 'http_error')}
        receipt = receipts.get(job, {})
        wires = receipt.get('responses', [])
        # The capture numbers *outbound attempts* in dispatch order, including transport
        # errors and retries. A missing response must not shift the following response IDs.
        indexed = {f'attempt-{i}': ends.get(e['request_id']) for i, e in enumerate(starts, 1)}
        count_matches = receipt.get('request_count') == len(starts)
        for wire in wires:
            ev = indexed.get(wire['attempt_id'])
            if not count_matches or ev is None or ev.get('status_code') != wire['status_code']:
                receipt['issues']['wire_http_binding_unverified'] = receipt['issues'].get('wire_http_binding_unverified', 0) + 1
                continue
            observed, archived = ev.get('usage'), extract_usage(wire.get('usage'))
            if observed and archived and observed != archived:
                raise ValueError(f'HTTP telemetry and archived usage disagree for {job}')
            wire_responses[ev['request_id']] = wire
        logical_usage = {e['run_id']: e.get('usage') for e in entries if e['event'] == 'llm_end'}
        ok_count = Counter(e['run_id'] for e in http_ends if 200 <= e['status_code'] < 300)
        for start in starts:
            ident = start['request_id']
            end = ends.get(ident, {})
            wire = wire_responses.get(ident, {})
            raw = wire.get('usage') or {}
            usage = extract_usage(raw) or end.get('usage')
            if not usage and end.get('event') == 'http_end' and 200 <= end['status_code'] < 300 and ok_count[start['run_id']] == 1:
                usage = logical_usage.get(start['run_id'])
            row = {'job_id': job, 'request_id': ident, 'start_unix': start['timestamp'], 'end_unix': end.get('timestamp'),
                   'latency_seconds': end.get('duration_seconds'), 'status_code': end.get('status_code'),
                   'http_error': end.get('error_type'), 'retry_index': start.get('retry_index'),
                   'reported_model': wire.get('model'), 'fingerprint': wire.get('system_fingerprint'),
                   'finish_reason': wire.get('finish_reason'),
                   'reasoning_tokens': (raw.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                   'response_body_sha256': wire.get('body_sha256')}
            row.update(usage or {})
            if usage and end.get('timestamp'):
                row.update(fee_bounds(usage, [peak_factor(start['timestamp']), peak_factor(end['timestamp'])]))
            calls.append(row)
    return by_job, receipts, calls

def collect_scene(attempt, scene, historical, *, report_date=True):
    import reproduction as r
    folder = attempt / scene['id']
    expected = scene['tasks']
    path = folder / 'results.json'
    raw = read(path) if path.exists() else r.initial_results({'sessions': [scene]})
    if [(x['session_id'], x['task_id']) for x in raw] != [(scene['id'], x['id']) for x in expected]:
        raise ValueError(f'Unexpected first-pass task order in {folder}')
    scene_source = r.DATA / scene['file']
    if sha256(scene_source) != scene['sha256']:
        raise ValueError(f'Original scene changed: {scene_source}')
    source = read(scene_source)
    if report_date:
        by_job, receipts, calls = telemetry(folder)
    else:
        by_job, receipts, calls = defaultdict(list), {}, []
        for ev in events(folder / METRICS_FILE):
            by_job[ev.get('job_id')].append(ev)
    calls_by_job = defaultdict(list)
    for call in calls:
        calls_by_job[call['job_id']].append(call)
        call['session_id'] = scene['id']
    rows = []
    scene_state = read(folder / 'scene.json') if (folder / 'scene.json').exists() else {}
    for index, (row, task, definition) in enumerate(zip(raw, expected, source['tasks'], strict=True), 1):
        sid, tid, job = scene['id'], task['id'], row.get('job_id')
        prior = historical[sid, tid]
        if (definition['id'] != tid or len(task['leaves']) != row['expected_checks'] or
                [{'path': v['path'], 'definition': v['definition']} for v in row['leaves']] != task['leaves']):
            raise ValueError(f'Incorrect raw row binding for {sid}/{tid}')
        scored = row['status'] == 'scored'
        worker = row.get('continuation_safe') is True
        errors = [e for e in by_job.get(job, []) if e['event'] == 'llm_error']
        terminal = [e for e in errors if isinstance(e.get('status_code'), int) and e['status_code'] >= 400]
        reason = 'terminal_provider_error' if terminal else 'observed_checker' if scored else 'unscored_execution_stop'
        passed = bool(scored and row['passed'] and not terminal)
        hist = prior['task_pass'] == 'True'
        details = receipts.get(job, {})
        requests = calls_by_job.get(job, [])
        for q in requests:
            q['task_id'] = tid
        failed = [v['definition'] for v in row['leaves'] if scored and v['result'] is False]
        sdk_errors = [f"{e.get('error_type')}/{e.get('status_code')}" for e in errors]
        job_events = by_job.get(job, [])
        transport = [e for e in job_events if e['event'] == 'http_error']
        transport_runs = {e['run_id'] for e in transport if e.get('run_id')}
        http_failures = [e for e in job_events if e['event'] == 'http_end' and e['status_code'] >= 400]
        # A later successful retry can recover a failed HTTP attempt. Tag a transport-related
        # SDK failure only if its own logical call failed, or the SDK reports a connection/timeout error.
        sdk_transport = [e for e in errors if e.get('run_id') in transport_runs or e.get('error_type') in {
            'APIConnectionError', 'APITimeoutError', 'OpenAIConnectionError', 'OpenAITimeoutError'}]
        rows.append({'session_id': sid, 'task_id': tid, 'index': index, 'family': scene['family'],
                     'scene_difficulty': prior['scene_difficulty'], 'fleet_size': len(source['drones']),
                     'task_difficulty': task['difficulty'], 'name': task['name'], 'job_id': job,
                     'started': bool(job or (folder/f'{tid}.window.json').exists()),
                     'original_status': row['status'], 'checker_passed': row.get('passed') if scored else None,
                     'historical_pass': hist, 'historical_accounting_reason': prior['accounting_reason'],
                     'historical_checker_passed': {'True': True, 'False': False}.get(prior['original_passed']),
                     'historical_timed_out': {'True': True, 'False': False}.get(prior['agent_timed_out']),
                     'task_pass': passed, 'deployment_pass': passed and worker,
                     'pair': ('both_pass' if passed and hist else 'flockcue_only' if passed else
                              'historical_only' if hist else 'both_fail'),
                     'accounting_reason': reason, 'worker_confirmed': worker, 'agent_timed_out': row['agent_timed_out'],
                     'agent_success': row.get('agent_success'), 'expected_checks': row['expected_checks'],
                     'observed_passed_checks': row['expected_checks'] - len(failed) if scored else None,
                     'credited_checks': row['expected_checks'] - len(failed) if reason == 'observed_checker' else 0,
                     'failed_original_checks': failed,
                     'scoring_ready_seconds': replay_score(folder, definition, row, sid) if scored else None,
                     'provider_errors': sdk_errors, 'transport_attempt_errors': [e.get('error_type') for e in transport],
                     'http_failure_statuses': [e['status_code'] for e in http_failures],
                     'sdk_transport_errors': [e['error_type'] for e in sdk_transport],
                     'agent_status': row.get('agent_status'), 'agent_final_status': row.get('agent_final_status'),
                     'archive_end': details.get('archive_end'), 'scene_stop': scene_state.get('error') if not scored or not worker else None,
                     'http_attempts': len(requests), 'calls_with_usage': sum(v.get('total_tokens') is not None for v in requests),
                     'calls_with_cost_estimate': sum(v.get('fee_low_cny') is not None for v in requests),
                     'known_cost_estimate_cny_low': round(sum(v.get('fee_low_cny', 0) for v in requests), 8),
                     'known_cost_estimate_cny_high': round(sum(v.get('fee_high_cny', 0) for v in requests), 8),
                     'total_tokens': sum(v.get('total_tokens') or 0 for v in requests),
                     'reasoning_tokens_known': sum(v.get('reasoning_tokens') or 0 for v in requests),
                     'calls_with_reasoning_usage': sum(v.get('reasoning_tokens') is not None for v in requests),
                     'model_archive': details.get('archive'), 'archive_complete': details.get('complete'),
                     'archive_issues': details.get('issues'), 'tool_starts': details.get('tools'),
                     'first_model_request': details.get('first_request'),
                     'historical_evidence': prior['evidence_dir'], 'evidence': str(folder.relative_to(ROOT))
                     if folder.is_relative_to(ROOT) else str(folder)})
    return rows, calls


def task_totals(rows):
    return {'planned': len(rows), 'started': sum(r['started'] for r in rows),
            'not_started': sum(not r['started'] for r in rows),
            'unscored': sum(r['original_status'] != 'scored' for r in rows),
            'scored': sum(r['original_status'] == 'scored' for r in rows),
            'historical': sum(r['historical_pass'] for r in rows),
            'passed': sum(r['task_pass'] for r in rows), 'strict': sum(r['deployment_pass'] for r in rows),
            'improved': sum(r['pair'] == 'flockcue_only' for r in rows),
            'regressed': sum(r['pair'] == 'historical_only' for r in rows),
            'provider_failure': sum(r['accounting_reason'] == 'terminal_provider_error' for r in rows),
            'checker_failed': sum(r['checker_passed'] is False and r['accounting_reason'] == 'observed_checker' for r in rows),
            'timed_out': sum(r['agent_timed_out'] is True for r in rows),
            'scored_timeouts': sum(r['agent_timed_out'] is True and r['original_status'] == 'scored' for r in rows),
            'passing_timeouts': sum(r['agent_timed_out'] is True and r['task_pass'] for r in rows)}


def groups(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return {k: task_totals(v) for k, v in grouped.items()}


def pilot_overlap(rows):
    """Recompute the two original pilot scenes in each of the three deployments."""
    audit = ROOT / 'docs/audit/harness-paired40-20261111/tasks.csv'
    official = {(r['session_id'],r['task_id']): r for r in rows}
    by_scene = defaultdict(Counter)
    with audit.open(encoding='utf-8-sig', newline='') as stream:
        pilot = list(csv.DictReader(stream))
    keys = {(p['session_id'], p['task_id']) for p in pilot}
    if len(pilot) != 40 or len(keys) != 40 or not keys.issubset(official):
        raise ValueError('Invalid original pilot task set')
    for p in pilot:
        row = official[p['session_id'],p['task_id']]
        if row['index'] != int(p['index']) or row['historical_pass'] != bool(int(p['oct_pass'])):
            raise ValueError('Pilot comparison is not bound to the original October result')
        by_scene[row['session_id']].update(tasks=1, historical=row['historical_pass'], limited_pilot=int(p['nov_pass']),
            official=row['task_pass'], official_scored=row['original_status'] == 'scored')
    if sorted(s['tasks'] for s in by_scene.values()) != [20,20]:
        raise ValueError('Expected two complete pilot scenes')
    return dict(by_scene)


def paired_interval(rows):
    """Percentile interval resampling both entire matched scenes within each of 15 strata."""
    strata = defaultdict(lambda: defaultdict(int))
    for r in rows:
        strata[r['family'], r['scene_difficulty']][r['session_id']] += int(r['task_pass']) - int(r['historical_pass'])
    if len(strata) != 15 or any(len(s) != 2 for s in strata.values()):
        raise ValueError('Incorrect whole-scene strata')
    rng = random.Random(20261111)
    values = [list(s.values()) for s in strata.values()]
    deltas = sorted(sum(s[rng.randrange(2)] + s[rng.randrange(2)] for s in values) / len(rows) * 100
                    for _ in range(20000))

    def pct(frac):
        pos = (len(deltas) - 1) * frac
        i = int(pos)
        return deltas[i] + (pos-i) * (deltas[i+1] - deltas[i])
    return {'unit': 'percentage points', 'method': 'paired whole-scene percentile bootstrap within 15 strata',
            'resamples': 20000, 'seed': 20261111, 'low': pct(.025), 'high': pct(.975)}


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})


def cost_summary(calls):
    from reproduction_metrics import _latencies
    fields = ('prompt_tokens','completion_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens','reasoning_tokens')
    observed = {k: sum(v[k] for v in calls if v.get(k) is not None) if any(v.get(k) is not None for v in calls)
                else None for k in fields}
    return {'http_dispatched': len(calls), 'status_codes': dict(Counter(q['status_code'] for q in calls)),
            'http_with_usage': sum(q.get('total_tokens') is not None for q in calls),
            'http_with_reasoning_usage': sum(q.get('reasoning_tokens') is not None for q in calls),
            'http_with_wire_response_facts': sum(bool(q.get('response_body_sha256')) for q in calls),
            'http_retries': sum((q.get('retry_index') or 0) > 0 for q in calls),
            'models': dict(Counter(q['reported_model'] for q in calls if q.get('reported_model'))),
            'fingerprints': dict(Counter(q['fingerprint'] for q in calls if q.get('fingerprint'))),
            'finish_reasons': dict(Counter(q['finish_reason'] for q in calls if q.get('finish_reason'))),
            'latency_seconds': _latencies([q['latency_seconds'] for q in calls]),
            'http_with_cost_estimate': sum(q.get('fee_low_cny') is not None for q in calls),
            'known_cost_estimate_cny_low': round(sum(q.get('fee_low_cny', 0) for q in calls), 8),
            'known_cost_estimate_cny_high': round(sum(q.get('fee_high_cny', 0) for q in calls), 8), **observed}


def balance_change(before, after):
    from decimal import Decimal
    if before is None or after is None:
        return None
    b, a = read(before), read(after)
    if b['currency'] != 'CNY' or a['currency'] != 'CNY' or datetime.fromisoformat(b['captured_at']) >= datetime.fromisoformat(a['captured_at']):
        raise ValueError('Invalid account snapshot interval')
    return {'before': b, 'after': a, 'account_decrease_cny': str(Decimal(b['total_balance']) - Decimal(a['total_balance']))}


def report(attempt, out, before, after):
    import reproduction as r
    import reproduction_parallel as rp
    from reproduction_metrics import _latencies, extract_usage
    plan = check_plan(attempt)
    manifest = read(attempt / 'manifest.json')
    if manifest['status'] in ('created','running') or not manifest.get('finished_at'):
        raise ValueError('Use status for a live attempt; report requires a terminal manifest')
    if out.is_relative_to(attempt):
        raise ValueError('Write report outside the execution archive')
    rp.verify_archive(r, attempt)
    if manifest['executor']['workers'] != 8 or set(manifest['scenes']) != {s['id'] for s in plan['sessions']}:
        raise ValueError('Unexpected executor or scene roster')
    lookup = history_by_task(plan)
    rows, calls, seals = [], [], {}
    for scene in plan['sessions']:
        sid = scene['id']
        rec = manifest['scenes'][sid]
        if rec['generation'] > 1 or rec['directory'] not in (None,sid) or rec.get('history'):
            raise ValueError('Unexpected replay in the official one-pass attempt')
        seals[sid] = verify_seal(attempt, scene, rec, plan, manifest)
        batch, measured = collect_scene(attempt, scene, lookup)
        rows.extend(batch)
        calls.extend(measured)
    totals = task_totals(rows)
    checker_pairs = [r for r in rows if r['historical_accounting_reason'] == 'observed_checker'
                     and r['accounting_reason'] == 'observed_checker']
    historical_faults = [r for r in rows if r['historical_accounting_reason'] == 'terminal_provider_error']
    first = [row['first_model_request'] for row in rows if row['first_model_request']]
    wire_pass = [q for q in first if q['model'] == 'deepseek-flash' and q['temperature'] == .1 and q['has_harness']
                 and not q['sense_required'] and not q['pilot_overrides_present'] and q['sense_description_sha256']
                 == '27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227']
    preflight = read(attempt/'gateway_preflight.json')
    gateway = preflight.get('gateway', {})
    gateway_usage = {k: gateway.get(k,{}).get('token_usage') for k in ('first_response_metadata','final_response_metadata')}
    preflight_factor = peak_factor(datetime.fromisoformat(preflight['checked_at']).timestamp())
    preflight_fees = {k: fee_bounds(extract_usage(v), [preflight_factor]) for k,v in gateway_usage.items()}
    historical_gateway = read(ROOT/'experiments/runs/20261003-143255-full-5c501118/gateway_preflight.json')['gateway']
    gateway_fingerprints = {period: sorted({p[key]['system_fingerprint'] for key in gateway_usage
                                            if p.get(key, {}).get('system_fingerprint')})
                            for period, p in [('october', historical_gateway), ('november', gateway)]}
    by_scene = groups(rows, 'session_id')
    scene_calls = defaultdict(list)
    for call in calls:
        scene_calls[call['session_id']].append(call)
    for sid, scene in by_scene.items():
        scene.update(cost_summary(scene_calls[sid]))
    summary = {'runner_status': manifest['status'], 'attempt': str(attempt.relative_to(ROOT)) if attempt.is_relative_to(ROOT) else str(attempt),
               'model': 'deepseek-flash', 'protocol': plan['protocol']['id'],
               'started_at': manifest['started_at'], 'finished_at': manifest['finished_at'],
               'original_score_replays': totals['scored'], 'captured_first_wire_requests': len(first),
               'first_wire_requests_matching_protocol': len(wire_pass),
               'complete_scene_seals_verified': sum(seals.values()), 'scene_seals': seals,
               'totals': totals, 'pairs': dict(Counter(v['pair'] for v in rows)),
               'both_counted_from_checks': task_totals(checker_pairs),
               'on_historical_provider_failures': task_totals(historical_faults),
               'raw_historical_pass_on_provider_failures': sum(r['historical_checker_passed'] is True for r in historical_faults),
               'by_family': groups(rows, 'family'), 'by_difficulty': groups(rows, 'scene_difficulty'),
               'by_fleet': groups(rows, 'fleet_size'), 'by_scene': by_scene, 'pilot_overlap': pilot_overlap(rows),
               'paired_rate_delta_pp': (totals['passed'] - totals['historical'])/600*100,
               'descriptive_interval': paired_interval(rows) if totals['scored'] == 600 else None,
               'scoring_ready_latency_seconds': _latencies([q['scoring_ready_seconds'] for q in rows]),
               'billed_tasks': cost_summary(calls), 'gateway_usage_separate': gateway_usage,
               'gateway_fee_estimates_separate': preflight_fees, 'gateway_fingerprints': gateway_fingerprints,
               'account_window': balance_change(before, after),
               'check_failures_by_endpoint': dict(Counter(f.get('endpoint', f.get('path')) for row in rows
                                            if not row['task_pass'] for f in row['failed_original_checks']).most_common()),
               'sdk_errors': dict(Counter(e for row in rows for e in row['provider_errors'])),
               'sdk_transport_error_tasks': sum(bool(row['sdk_transport_errors']) for row in rows),
               'failed_tasks_with_sdk_transport_error': sum(not row['task_pass'] and bool(row['sdk_transport_errors']) for row in rows),
               'http_attempt_errors': dict(Counter(e for row in rows for e in row['transport_attempt_errors'])),
               'archive_status': dict(Counter('none' if r['archive_complete'] is None else str(r['archive_complete']) for r in rows))}
    out.mkdir(parents=True,exist_ok=True)
    write_csv(out/'tasks.csv', rows)
    write_csv(out/'calls.csv', calls)
    write_csv(out/'scenes.csv', [dict(session_id=k,**v) for k,v in summary['by_scene'].items()])
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out/'REPORT_CN.md').write_text(markdown(summary), encoding='utf-8')
    return {'output': str(out), **totals, 'runner_status': manifest['status']}


def markdown(s):
    t, p, cost = s['totals'], s['pairs'], s['billed_tasks']
    def num(v):
        return '未报告' if v is None else f'{v:,}'
    def latency(d):
        if not d['count']:
            return '无观测'
        return f"{d['p50_seconds']:.2f} / {d['p95_seconds']:.2f} 秒（n={d['count']}）"
    sections = ['# FlockCue + DeepSeek 600 题首次部署', '',
        f"正式运行从 {s['started_at']} 至 {s['finished_at']}。按十月 1,500 题首遍的部署计分协议，"
        f"FlockCue 在冻结的 600 题上通过 **{t['passed']}/600（{t['passed']/6:.2f}%）**，"
        f"同题历史 Agent4Drone 为 506/600（84.33%），净差 **{s['paired_rate_delta_pp']:+.2f} 个百分点**。"
        f"两期模型别名均为 `deepseek-flash`。十月预检指纹为 `{', '.join(s['gateway_fingerprints']['october'])}`，"
        f"十一月为 `{', '.join(s['gateway_fingerprints']['november'])}`，运行跨越两个服务时间窗。",
        '', f"30 个连续会话共启动 {t['started']}/600 题，从冻结终态保存的检查 API 回执独立重放 {t['scored']} 棵原检查树，"
        f"{s['complete_scene_seals_verified']} 场完成封存核验。执行器终态为 `{s['runner_status']}`。"
        f"同时要求工作进程正常退出的严格部署成功为 {t['strict']}/600。"
        f"有 {t['provider_failure']} 题发生终止性服务错误，{t['checker_failed']} 题有可计分的失败检查。"
        f"共有 {t['timed_out']} 题达到 500 秒动作窗口，其中 {t['scored_timeouts']} 题取得终态评分、{t['passing_timeouts']} 题通过。"
        f"未启动 {t['not_started']} 题，未取得终态评分共 {t['unscored']} 题。", '',
        '| 配对 | 题数 |','| --- | ---: |', *[f"| {label} | {p.get(key,0)} |" for label,key in
         [('都过','both_pass'),('仅原版过','historical_only'),('仅 FlockCue 过','flockcue_only'),('都失','both_fail')]],
        '', '## 按原基准分组', '', '| 组别 | 题数 | 原版 | FlockCue | 净增题 |',
        '| --- | ---: | ---: | ---: | ---: |']
    labels = {'area_assignment_and_patrol':'区域巡逻','area_search':'区域搜索','target_assignment':'目标分配'}
    for group in ('by_family','by_difficulty'):
        for name, results in s[group].items():
            sections.append(f"| {labels.get(name,name)} | {results['planned']} | {results['historical']} | {results['passed']} | "
                            f"{results['passed']-results['historical']:+d} |")
    if ci := s['descriptive_interval']:
        sections += ['', f"按 15 个任务族与场景难度分层，在每层两场内用整场配对重抽，"
                     f"净差的描述性 95% 区间为 [{ci['low']:.2f}, {ci['high']:.2f}] 个百分点"
                     f"（20,000 次，种子 {ci['seed']}）。此区间描述已知分层内的场间差异，每层只有两场。"]
    valid, faults = s['both_counted_from_checks'], s['on_historical_provider_failures']
    sections += ['', f"十月有 {faults['planned']} 题按终止性服务错误计败，其中原始检查曾有 {s['raw_historical_pass_on_provider_failures']} 题通过。"
        f"本轮在这 {faults['planned']} 题中取得 {faults['passed']} 个部署通过。"
        f"仅看两期均按终态检查计分的 {valid['planned']} 对，原版通过 {valid['historical']}、FlockCue 通过 {valid['passed']}，"
        f"旧过新失 {valid['regressed']}、旧失新过 {valid['improved']}。主指标始终保留 600 题。"]
    sections += ['', '## 两个既有试采场景', '',
        '| 场景 | 十月原版 | 十一月限额试采 | 本次正式首遍 | 本次取得评分 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for sid, v in s['pilot_overlap'].items():
        sections.append(f"| {sid} | {v['historical']}/20 | {v['limited_pilot']}/20 | {v['official']}/20 | {v['official_scored']}/20 |")
    sections += ['', '这 40 题已包含在正式 600 题内。前次试采另有调用和 token 限额，两场有开发暴露。'
        '[试采逐题配对与设置](../../audit/harness-paired40-20261111/REPORT_CN.md)']
    sections += ['', '## 模型用量、时延与费用', '',
        f"评测题实际派发 {cost['http_dispatched']:,} 次 HTTP，其中 {cost['http_with_usage']:,} 次有用量。"
        f"已知输入 {num(cost['prompt_tokens'])} token、输出 {num(cost['completion_tokens'])} token，"
        f"合计 {num(cost['total_tokens'])} token。输出中可取得的思考 token 合计 {num(cost['reasoning_tokens'])}，"
        f"对应 {cost['http_with_reasoning_usage']:,} 次有该字段的回复。"
        f"其中 {s['first_wire_requests_matching_protocol']}/{s['captured_first_wire_requests']} 份题首实际请求符合登记的模型、温度、C 版感知与 Harness 工具配置。"
        f"终态冻结时长 P50/P95 为 {latency(s['scoring_ready_latency_seconds'])}，"
        f"HTTP 耗时 P50/P95 为 {latency(cost['latency_seconds'])}。", '',
        f"依当日官方峰谷价格和真实缓存拆分，{cost['http_with_cost_estimate']:,} 条可核算任务请求的金额为 "
        f"¥{cost['known_cost_estimate_cny_low']:.4f}–{cost['known_cost_estimate_cny_high']:.4f}。"
        "跨峰谷边界的请求按两档价格给区间。网关预检的用量在 summary.json 单列。"
        "[官方人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)"]
    if acc := s['account_window']:
        preflight = s['gateway_fee_estimates_separate'].values()
        low = cost['known_cost_estimate_cny_low'] + sum(v.get('fee_low_cny',0) for v in preflight)
        high = cost['known_cost_estimate_cny_high'] + sum(v.get('fee_high_cny',0) for v in preflight)
        sections += ['', f"两次模型预检按预检时间的费率计入后，已知用量合计估算 ¥{low:.4f}–{high:.4f}。"
                     f"同期人民币账户在批次前后分别为 ¥{acc['before']['total_balance']} 和 ¥{acc['after']['total_balance']}，余额减少 ¥{acc['account_decrease_cny']}。"
                     "同时期账户有其他收支时应核对供应商流水。"]
    regressions = sorted(s['by_scene'].items(), key=lambda kv:kv[1]['passed']-kv[1]['historical'])
    sections += ['', '## 失败复核', '',
        '逐题记录包括原检查失败的端点及参数、模型工具跨度、工作进程状态、模型错误、归档完整性和历史证据目录。'
        f"归档中记录到 SDK 传输错误的有 {s['sdk_transport_error_tasks']} 题，其中 {s['failed_tasks_with_sdk_transport_error']} 题未通过。"
        '逐请求 HTTP 失败以及 SDK 重试后的最终结果分别保留，便于结合世界快照和动作回执定位失败。',
        '', '| 净降场景 | 原版 | FlockCue | 旧过新失 | 旧失新过 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for key, stats in regressions:
        if stats['passed'] < stats['historical']:
            sections.append(f"| {key} | {stats['historical']}/20 | {stats['passed']}/20 | {stats['regressed']} | {stats['improved']} |")
    if all(q['passed'] >= q['historical'] for _,q in regressions):
        sections.append('| 无 | — | — | — | — |')
    sections += ['', '[600 题配对与失败检查](tasks.csv) · [逐场成绩](scenes.csv) · [逐请求计量](calls.csv) · [复算摘要](summary.json)',
                 '', '[不一致题的执行证据](MECHANISMS_CN.md) · [原两场 40 题的三次部署](FOCAL40_CN.md) · [两期用量与时延对照](RESOURCE_COMPARISON_CN.md)', '']
    return '\n'.join(sections)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'report'])
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--balance-before', type=Path)
    parser.add_argument('--balance-after', type=Path)
    args = parser.parse_args()
    if args.command == 'status':
        result = status(args.run.resolve())
    else:
        if not args.output:
            parser.error('report requires --output')
        result = report(args.run.resolve(), args.output.resolve(), args.balance_before, args.balance_after)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
