"""Write the resource comparison after the official 600-task report is available."""
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from collections import Counter
import csv

from report_flockcue_deployment600 import (
    METRICS_FILE, ROOT, check_plan, events, history_by_task, read,
)
from reproduction_metrics import _latencies, _summarize_events


RESULT = ROOT / 'docs/results/flockcue-deployment600-20261111'


def job_events(directories):
    return [event for directory in directories for event in events(directory / METRICS_FILE)
            if event.get('job_id')]


def count(value):
    return '未知' if value is None else f'{value:,}'


def latency(values):
    if not values['count']:
        return '无观测'
    return f"{values['p50_seconds']:.2f} / {values['p95_seconds']:.2f}（n={values['count']:,}）"


def report():
    formal = read(RESULT / 'summary.json')
    attempt = ROOT / formal['attempt']
    plan = check_plan(attempt)
    registered = read(RESULT / 'registration.json')
    if (attempt != ROOT / registered['attempt'] or formal['totals']['planned'] != 600
            or not formal.get('finished_at') or formal['runner_status'] in ('created', 'running')):
        raise ValueError('Expected the terminal report for the registered first pass')
    history = history_by_task(plan)
    old_dirs = {ROOT / r['evidence_dir'] for r in history.values()}
    if len(old_dirs) != 30:
        raise ValueError('Expected one original directory per selected scene')
    old_events = job_events(sorted(old_dirs))
    old = _summarize_events(old_events)
    old_http, old_usage = old['http'], old['llm']['usage']
    new_events = job_events([attempt / s['id'] for s in plan['sessions']])
    new = formal['billed_tasks']
    if sum(e['event'] == 'http_start' for e in new_events) != new['http_dispatched']:
        raise ValueError('Formal task report and HTTP starts differ')
    old_scores = _latencies(float(r['scoring_ready_seconds']) for r in history.values())
    old_faults = sum(r['accounting_reason'] == 'terminal_provider_error' for r in history.values())
    old_status = Counter(e.get('status_code') for e in old_events if e['event'] == 'llm_error')
    with (RESULT / 'calls.csv').open(encoding='utf-8-sig', newline='') as f:
        new_cache = sum(bool(r['cache_hit_tokens'] and r['cache_miss_tokens']) for r in csv.DictReader(f))
    old_worker = _latencies(e['duration_seconds'] for e in old_events if e['event'] == 'job_end')
    new_worker = _latencies(e['duration_seconds'] for e in new_events if e['event'] == 'job_end')
    table = [
        ('已启动评测题数', count(sum(e['event'] == 'job_start' for e in old_events)), count(formal['totals']['started'])),
        ('模型 HTTP 尝试数', count(old_http['attempts']), count(new['http_dispatched'])),
        ('返回 usage 的请求数', count(old_usage['calls_with_usage']), count(new['http_with_usage'])),
        ('有缓存拆分的请求数', count(old_usage['calls_with_cache_usage']), count(new_cache)),
    ]
    for label, key in [('输入 token', 'prompt_tokens'), ('其中缓存命中', 'cache_hit_tokens'),
                       ('其中缓存未命中', 'cache_miss_tokens'), ('输出 token', 'completion_tokens'),
                       ('已知总 token', 'total_tokens')]:
        table.append((label, count(old_usage[key]), count(new[key])))
    table.extend([
        ('其中已知思考 token', '未记录', f"{count(new['reasoning_tokens'])}（n={new['http_with_reasoning_usage']:,}）"),
        ('单次 HTTP 耗时 P50 / P95（秒）', latency(old_http['latency']), latency(new['latency_seconds'])),
        ('worker 作业耗时 P50 / P95（秒）', latency(old_worker), latency(new_worker)),
        ('动作窗口开启至环境冻结 P50 / P95（秒）', latency(old_scores), latency(formal['scoring_ready_latency_seconds'])),
        ('终止性供应商 HTTP 错误计败题数', count(old_faults), count(formal['totals']['provider_failure'])),
        ('传输层错误尝试数', count(old_http['errors']), count(sum(formal['http_attempt_errors'].values()))),
        ('重试 HTTP 尝试数', count(old_http['retry_attempts']), count(new['http_retries'])),
    ])
    lines = ['# 同一 600 题的用量与耗时', '',
             '两期均取冻结的 30 个场景、各 20 题的首次部署。请求数和 token 只计评测题，网关预检单列在主报告中。', '',
             '| 指标 | 十月 Agent4Drone | 十一月 FlockCue |', '| --- | ---: | ---: |',
             *[f'| {label} | {a} | {b} |' for label, a, b in table], '']
    if new['total_tokens'] is not None:
        change = (new['total_tokens'] / old_usage['total_tokens'] - 1) * 100
        direction = '增加' if change >= 0 else '减少'
        lines += [f'按已返回的用量，本次合计 token 比十月{direction} {abs(change):.2f}%。'
                  '输入含每次重发的上下文。十月只留下归一化 usage，无法比较两期思考 token 的变化。'
                  '[十月逐题历史数据](../baseline-deployment-20261005/tasks.csv) · [本次逐请求数据](calls.csv)', '']
    lines += [
        'HTTP 时延覆盖有结束或错误事件的尝试。worker 耗时取作业启动至结束的单调时钟，'
        '环境冻结耗时取 frozen_at − opened_at，包含动作窗口关闭及在途请求排空。'
        'P50、P95 在全部有观测值的样本上采用线性插值，n 表示该项实际样本数。'
        '[原始计量代码](../../../scripts/reproduction_metrics.py) · [本次评分及冻结时间](tasks.csv)', '',
        f"十月记录 {old_http['status_counts'].get('429', 0)} 次 HTTP 429，均属于同一次逻辑调用，"
        f"该调用最终返回 402。十月因终止性 402 计败的有 {old_status[402]} 题。"
        '本期传输错误可能由 SDK 重试恢复，统计尝试和统计最终失败题时分别计数。'
        '十月缺少该子集实际账户结算。十一月费用、余额核对及主评分见正式报告。'
        '[十月原始日志](../../../experiments/runs/20261003-143255-full-5c501118/) · [正式报告](REPORT_CN.md) · [汇总原表](summary.json)', '',
    ]
    output = RESULT / 'RESOURCE_COMPARISON_CN.md'
    output.write_text('\n'.join(lines), encoding='utf-8')
    print(output)


if __name__ == '__main__':
    report()
