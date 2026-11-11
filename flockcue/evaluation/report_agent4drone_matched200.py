"""Independently score the registered evening Agent4Drone first pass on ten whole scenes."""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime

import reproduction as r
import reproduction_parallel as rp
from report_flockcue_deployment600 import (
    FROZEN, METRICS_FILE, PLAN_SHA256, ROOT, balance_change, collect_scene, cost_summary, events,
    fee_bounds, history_by_task, peak_factor, read, sha256, verify_seal, write_csv,
)
from reproduction_metrics import _latencies, extract_usage


NAME = 'agent4drone-matched200-v1'
PLAN = ROOT / 'experiments/plans' / f'{NAME}.json'
SUBSET_SHA = 'd1c252e1f377c8c79a612063594451f7d553960dcea67474ba7b16a9711b7ec3'
DAY = ROOT / 'docs/results/flockcue-deployment600-20261111'
DAY_TASK_SHA = '838e7d82b934a63893d4c1334b53274d2257cfceca39e8adc02a529c2d3264da'
DAY_CALL_SHA = 'e744a04cbc090caacaca5c27eb8b5749554baf8fd066add828ef7a269a8690ca'
DEFAULT_OUT = ROOT / 'docs/results/agent4drone-matched200-20261111'
LABELS = {'area_assignment_and_patrol': '巡逻', 'area_search': '搜索', 'target_assignment': '目标分配'}
CATEGORIES = ('both_pass', 'flockcue_only', 'agent4drone_only', 'both_fail')


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def read_references():
    for path, digest in [(FROZEN, PLAN_SHA256), (PLAN, SUBSET_SHA), (DAY/'tasks.csv', DAY_TASK_SHA),
                         (DAY/'calls.csv', DAY_CALL_SHA)]:
        if sha256(path) != digest:
            raise ValueError(f'Changed frozen comparison input: {path}')
    six = read(FROZEN)
    plan = read(PLAN)
    selection = read(ROOT/'experiments'/NAME/'selection.json')
    ids = selection['selected_scene_ids']
    if (len(set(ids)) != 10 or ids != [s['id'] for s in plan['sessions']]
            or plan['sessions'] != [s for s in six['sessions'] if s['id'] in ids]
            or plan['protocol'] != six['protocol'] or plan['expected_tasks'] != 200):
        raise ValueError('Unexpected registered subset or changed original tasks')
    history = history_by_task(six)
    day = {(v['session_id'], v['task_id']): v for v in csv_rows(DAY/'tasks.csv') if v['session_id'] in ids}
    expected = {(s['id'], t['id']) for s in plan['sessions'] for t in s['tasks']}
    if set(day) != expected or len(expected) != 200:
        raise ValueError('Daytime task rows do not cover the 200 registered seats')
    return plan, history, day


def response_metadata(folder, calls):
    """The evening observer adds provider facts to the original HTTP-end event."""
    received = {e['request_id']: e for e in events(folder/METRICS_FILE) if e['event'] == 'http_end'}
    for q in calls:
        event = received.get(q['request_id'], {})
        q.update(reported_model=event.get('reported_model'), fingerprint=event.get('system_fingerprint'),
                 reasoning_tokens=event.get('reasoning_tokens'))
    return calls


def pack_pair(new, day, old):
    if (new['index'] != int(day['index']) or new['index'] != int(old['task_index'])
            or new['historical_pass'] != (day['historical_pass'] == 'True')):
        raise ValueError('Historical task index or pass disagrees between sources')
    daytime, evening = day['task_pass'] == 'True', new['task_pass']
    return {
        'session_id': new['session_id'], 'task_id': new['task_id'], 'index': new['index'],
        'family': new['family'], 'scene_difficulty': new['scene_difficulty'],
        'october_agent4drone': new['historical_pass'], 'day_flockcue': daytime, 'evening_agent4drone': evening,
        'current_pair': {(True, True): 'both_pass', (True, False): 'flockcue_only',
                         (False, True): 'agent4drone_only', (False, False): 'both_fail'}[daytime, evening],
        'october_accounting': old['accounting_reason'], 'day_accounting': day['accounting_reason'],
        'evening_accounting': new['accounting_reason'], 'evening_started': new['started'],
        'evening_status': new['original_status'], 'evening_checker': new['checker_passed'],
        'evening_strict_pass': new['deployment_pass'], 'evening_timeout': new['agent_timed_out'],
        'evening_expected_leaves': new['expected_checks'], 'evening_scoring_ready_s': new['scoring_ready_seconds'],
        'evening_failed_checks': new['failed_original_checks'], 'evening_job': new['job_id'],
        'evening_sdk_errors': new['provider_errors'], 'evening_transport_errors': new['transport_attempt_errors'],
        'evening_http_failures': new['http_failure_statuses'], 'evening_scene_stop': new['scene_stop'],
        'evening_evidence': new['evidence'], 'day_evidence': day['evidence'], 'october_evidence': old['evidence_dir'],
    }


def scores(rows):
    night = sum(row['evening_agent4drone'] for row in rows)
    day = sum(row['day_flockcue'] for row in rows)
    octo = sum(row['october_agent4drone'] for row in rows)
    pairs = Counter(row['current_pair'] for row in rows)
    return {'n': len(rows), 'october_agent4drone': octo, 'day_flockcue': day, 'evening_agent4drone': night,
            'flockcue_minus_evening': day - night, 'evening_minus_october': night - octo,
            'current_pairs': {k: pairs[k] for k in CATEGORIES}}


def grouped(rows, field):
    buckets = defaultdict(list)
    for row in rows:
        buckets[row[field]].append(row)
    return {key: scores(bucket) for key, bucket in buckets.items()}


def scene_interval(rows):
    tiers = defaultdict(dict)
    for row in rows:
        sid = row['session_id']
        tier = tiers[row['scene_difficulty']]
        tier[sid] = tier.get(sid, 0) + int(row['day_flockcue']) - int(row['evening_agent4drone'])
    if len(tiers) != 5 or any(len(tier) != 2 for tier in tiers.values()):
        raise ValueError('The registered design has two whole scenes per difficulty')
    # Exactly 4 ordered resamples for each pair of scenes, giving 4^5 equally weighted outcomes.
    diffs = [0]
    for tier in tiers.values():
        a, b = tier.values()
        diffs = [total + inc for total in diffs for inc in (a+a, a+b, b+a, b+b)]
    distro = sorted(diff / len(rows) * 100 for diff in diffs)
    def percentile(p):
        q = (len(distro) - 1) * p
        i = int(q)
        return distro[i] + (distro[i+1] - distro[i]) * (q-i)
    return [percentile(.025), percentile(.975)]


def gateway(attempt):
    doc = read(attempt/'gateway_preflight.json')
    observations = []
    factor = peak_factor(datetime.fromisoformat(doc['checked_at']).timestamp())
    for name in ('first_response_metadata', 'final_response_metadata'):
        meta = doc.get('gateway', {}).get(name, {})
        usage = extract_usage(meta.get('token_usage'))
        observations.append({'name': name, 'model': meta.get('model_name'), 'fingerprint': meta.get('system_fingerprint'),
                             'usage': usage, **fee_bounds(usage, [factor])})
    return observations


def report(stage, out, before, after):
    plan, historical, day = read_references()
    attempts = sorted((stage/'experiments/runs').glob('*/manifest.json'))
    if len(attempts) != 1:
        raise ValueError(f'Expected one registered attempt in {stage}, found {len(attempts)}')
    attempt = attempts[0].parent
    manifest = read(attempt/'manifest.json')
    if manifest['status'] in ('created', 'running') or not manifest.get('finished_at'):
        raise ValueError('The evening run has not reached a terminal manifest')
    if out.is_relative_to(stage) or read(attempt/'plan.json') != plan:
        raise ValueError('Archive cannot be overwritten; plan must match the prespecified 200')
    if manifest['executor']['workers'] != 8 or set(manifest['scenes']) != {s['id'] for s in plan['sessions']}:
        raise ValueError('Unexpected roster or worker count')
    rp.verify_archive(r, attempt)
    registration = read(stage/'deployment-inputs.json')
    saved_sources = read(attempt/'source_hashes.json')
    if registration['plan_sha256'] != SUBSET_SHA or saved_sources != registration['archived_files']:
        raise ValueError('The executed plan or original-arm source differs from the staged registration')
    original = read(ROOT/'experiments/runs/20261003-143255-full-5c501118/source_hashes.json')
    if (set(original) != set(saved_sources) or
            any(saved_sources[k] != v for k,v in original.items() if k != 'scripts/reproduction_metrics.py')):
        raise ValueError('The executed policy or environment differs from October')
    rows, calls, seals = [], [], {}
    for scene in plan['sessions']:
        sid = scene['id']
        rec = manifest['scenes'][sid]
        if rec['generation'] > 1 or rec.get('history') or rec['directory'] not in (None, sid):
            raise ValueError(f'Unexpected replay or path in {sid}')
        seals[sid] = verify_seal(attempt, scene, rec, plan, manifest)
        batch, observed = collect_scene(attempt, scene, historical)
        calls.extend(response_metadata(attempt/sid, observed))
        for row in batch:
            key = sid, row['task_id']
            rows.append(pack_pair(row, day[key], historical[key]))
    resources = cost_summary(calls)
    ref_calls = [q for q in csv_rows(DAY/'calls.csv') if q['session_id'] in seals]
    ref_fps = Counter(q['fingerprint'] for q in ref_calls if q['fingerprint'])
    night_2xx = [q for q in calls if q.get('status_code') is not None and 200 <= q['status_code'] < 300]
    trace_models = Counter(q['reported_model'] for q in night_2xx if q['reported_model'])
    trace_fps = Counter(q['fingerprint'] for q in night_2xx if q['fingerprint'])
    day_2xx = [q for q in ref_calls if q['status_code'] and 200 <= int(q['status_code']) < 300]
    reference_fingerprint = read(DAY/'summary.json')['gateway_fingerprints']['november']
    by_family = grouped(rows, 'family')
    consensus = [q for q in rows if q['day_accounting'] == q['evening_accounting'] == 'observed_checker']
    all_checked = [q for q in consensus if q['october_accounting'] == 'observed_checker']
    data = {
        'status': manifest['status'], 'started_at': manifest['started_at'], 'finished_at': manifest['finished_at'],
        'attempt': str(attempt), 'identity_sha256': manifest['identity_sha256'], 'plan_sha256': SUBSET_SHA,
        'scene_seals': seals, 'totals': scores(rows), 'by_scene': grouped(rows, 'session_id'),
        'by_family': by_family, 'by_difficulty': grouped(rows, 'scene_difficulty'),
        'equal_family_delta_pp': 100 * sum(v['flockcue_minus_evening']/v['n'] for v in by_family.values())/3,
        'whole_scene_descriptive_95_pp': scene_interval(rows), 'common_checked_current': scores(consensus),
        'common_checked_all_three': scores(all_checked),
        'evening_accounting': dict(Counter(q['evening_accounting'] for q in rows)),
        'evening_unscored': sum(q['evening_status'] != 'scored' for q in rows),
        'evening_unstarted': sum(not q['evening_started'] for q in rows),
        'evening_strict': sum(q['evening_strict_pass'] for q in rows),
        'evening_timeouts': sum(q['evening_timeout'] is True for q in rows),
        'evening_observed_checks': sum(q['evening_expected_leaves'] for q in rows if q['evening_status']=='scored'),
        'expected_checks': plan['expected_checks'],
        'scoring_ready_s': _latencies(q['evening_scoring_ready_s'] for q in rows),
        'day_scoring_ready_s': _latencies(float(q['scoring_ready_seconds']) for q in day.values() if q['scoring_ready_seconds']),
        'day_http_latency_s': _latencies(float(q['latency_seconds']) for q in ref_calls if q['latency_seconds']),
        'gateway_evening': gateway(attempt), 'day_preflight_fingerprints': reference_fingerprint,
        'day_task_fingerprints': dict(ref_fps), 'day_2xx': len(day_2xx),
        'day_http_with_usage': sum(bool(q['total_tokens']) for q in ref_calls),
        'evening_2xx': len(night_2xx), 'evening_2xx_fingerprints': dict(trace_fps), 'evening_2xx_models': dict(trace_models),
        'evening_transport_errors': dict(Counter(q['http_error'] for q in calls if q['http_error'])),
        'known_task_usage': resources, 'account_window': balance_change(before, after),
    }
    out.mkdir(exist_ok=True, parents=True)
    write_csv(out/'pairs.csv', rows)
    (out/'summary.json').write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    (out/'REPORT_CN.md').write_text(markdown(data))
    return data


def markdown(d):
    t = d['totals']
    n = t['n']
    pairs = t['current_pairs']
    cost = d['known_task_usage']
    def latency(x):
        return '无观测' if not x['count'] else f"{x['p50_seconds']:.2f} / {x['p95_seconds']:.2f} 秒"
    def fingerprints(counts):
        return '、'.join(f'{key}（{value}）' for key,value in counts.items()) or '无观测'
    def row(name, v):
        return (f"| {name} | {v['n']} | {v['october_agent4drone']} | {v['day_flockcue']} | "
                f"{v['evening_agent4drone']} | {v['flockcue_minus_evening']:+d} |")
    comparable = d['common_checked_current']
    clean = d['common_checked_all_three']
    lo, hi = d['whole_scene_descriptive_95_pp']
    ref = set(d['day_task_fingerprints'])
    actual = set(d['evening_2xx_fingerprints'])
    n_covered = sum(d['evening_2xx_fingerprints'].values())
    if actual and actual != ref:
        service = '两臂实测任务指纹集合不同，当前结果包含后端配置差异。'
    elif (n_covered != d['evening_2xx'] or sum(d['day_task_fingerprints'].values()) != d['day_2xx']
          or not actual):
        service = '部分任务响应缺少可验证的后端指纹，同版本比较的覆盖有限。'
    else:
        service = '两臂有成功响应的全部任务请求均观察到相同的后端指纹集合。'
    lines = [
        '# 原版 Agent4Drone 与 FlockCue 同日 200 题对照', '',
        f"晚间原版首遍 {d['started_at']} 至 {d['finished_at']}，运行终态为 `{d['status']}`。"
        f"同题白天 FlockCue 通过 **{t['day_flockcue']}/{n}**，晚间 Agent4Drone 通过 "
        f"**{t['evening_agent4drone']}/{n}**，FlockCue 净差 **{t['flockcue_minus_evening']:+d} 题"
        f"（{100*t['flockcue_minus_evening']/n:+.2f} 个百分点）**。十月原版在同题通过 {t['october_agent4drone']}/{n}。",
        '', f"日夜均通过 {pairs['both_pass']} 题、仅 FlockCue 通过 {pairs['flockcue_only']} 题、"
        f"仅晚间原版通过 {pairs['agent4drone_only']} 题、两者均失败 {pairs['both_fail']} 题。"
        f"两臂都按实际终态检查计分的 {comparable['n']} 题净差 {comparable['flockcue_minus_evening']:+d} 题。"
        f"三次都按终态检查计分的 {clean['n']} 题中，十月原版、白天 FlockCue 和晚间原版"
        f"分别通过 {clean['october_agent4drone']}、{clean['day_flockcue']}、{clean['evening_agent4drone']} 题。", '',
        '| 组别 | 题数 | 十月原版 | 白天 FlockCue | 晚间原版 | FlockCue 相对晚间净差 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |',
        *[row(LABELS.get(k,k),v) for k,v in d['by_family'].items()],
        *[row(k,v) for k,v in d['by_scene'].items()], '',
        f"任务族等权净差 {d['equal_family_delta_pp']:+.2f} 个百分点。按每个难度层的两场整场配对重抽，"
        f"描述性 95% 区间为 [{lo:+.2f}, {hi:+.2f}] 个百分点。主指标使用固定 200 题分母。"
        "10 场按结构选定，同场各题继承各自轨迹的状态。对未抽取 20 场的差异尚无本轮实测。",
        '', '## 评分和模型服务', '',
        f"完成校验的场景封条 {sum(d['scene_seals'].values())}/10，重放终态检查树 {n-d['evening_unscored']}/{n}，"
        f"叶检查 {d['evening_observed_checks']}/{d['expected_checks']}。未启动 {d['evening_unstarted']} 题，"
        f"已启动未评分 {d['evening_unscored']-d['evening_unstarted']} 题；"
        f"终止性供应商 HTTP 错误计败 {d['evening_accounting'].get('terminal_provider_error',0)} 题，"
        f"超时 {d['evening_timeouts']} 题，要求进程确认结束的严格成功为 {d['evening_strict']}/{n}。",
        '', f"白天预检指纹 {', '.join(d['day_preflight_fingerprints']) or '无观测'}；"
        f"晚间两次预检指纹 {', '.join(q['fingerprint'] or '未知' for q in d['gateway_evening'])}。"
        f"白天同题观测到的任务响应指纹为 {fingerprints(d['day_task_fingerprints'])}。"
        f"晚间 {d['evening_2xx']} 次 HTTP 2xx 响应中，服务指纹为 {fingerprints(d['evening_2xx_fingerprints'])}，"
        f"报告模型为 {fingerprints(d['evening_2xx_models'])}。{service}两臂在不同时间运行，"
        "服务负载和模型随机性会影响 500 秒内完成的执行量。",
        '', '## 时延与费用', '',
        f"白天／晚间单次 HTTP 耗时 P50/P95 分别为 {latency(d['day_http_latency_s'])}、"
        f"{latency(cost['latency_seconds'])}。动作窗口开启至环境冻结分别为 "
        f"{latency(d['day_scoring_ready_s'])}、{latency(d['scoring_ready_s'])}。",
        '', f"晚间派发 {cost['http_dispatched']} 次，含 {cost['http_retries']} 次重试。"
        f"{cost['http_with_usage']} 次返回用量，实测缓存命中输入 {cost['cache_hit_tokens']}、"
        f"未命中输入 {cost['cache_miss_tokens']}、输出 {cost['completion_tokens']}、已知推理 {cost['reasoning_tokens']} token。"
        f"有计价依据的任务请求估算 ¥{cost['known_cost_estimate_cny_low']:.4f}–"
        f"{cost['known_cost_estimate_cny_high']:.4f}，预检另计。两次预检已知用量估算 "
        f"¥{sum(q.get('fee_low_cny',0) for q in d['gateway_evening']):.5f}–"
        f"{sum(q.get('fee_high_cny',0) for q in d['gateway_evening']):.5f}。"
        "[官方人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)",
    ]
    if d['account_window']:
        lines.extend(['',f"批次窗口人民币账户净减少 ¥{d['account_window']['account_decrease_cny']}。"])
    lines.extend(['', '[200 题配对及计分原因](pairs.csv) · [观测与复算摘要](summary.json) · '
                  '[白天 600 题报告](../flockcue-deployment600-20261111/REPORT_CN.md)', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, default=ROOT/'outputs'/NAME)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--balance-before', type=Path)
    parser.add_argument('--balance-after', type=Path)
    args = parser.parse_args()
    if bool(args.balance_before) != bool(args.balance_after):
        parser.error('Supply both account snapshots or neither')
    if args.balance_before is None:
        before = args.output/'balance-before.json'
        after = args.output/'balance-after.json'
        if before.is_file() and after.is_file():
            args.balance_before, args.balance_after = before, after
    try:
        data = report(args.stage.resolve(), args.output.resolve(), args.balance_before, args.balance_after)
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f'{exc}\n')
    print(json.dumps({'output': str(args.output), 'status': data['status'], **data['totals']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
