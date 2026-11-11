"""Verify and publish complete first-round results after scene repair (no API calls)."""
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import reproduction as r
import reproduction_parallel as rp
P=ROOT/'experiments/runs/20261003-143255-full-5c501118'
OUT=ROOT/'docs/results/full-round1'

def metrics(rows):
    counts=[sum(leaf['result'] is True for leaf in row['leaves']) for row in rows]
    n=len(rows);k=sum(row['expected_checks'] for row in rows)
    return {'tasks':n,'checks':k,'passed_tasks':sum(row['passed'] for row in rows),
            'passed_checks':sum(counts),'task_pass':sum(row['passed'] for row in rows)/n,
            'avg_check':sum(c/row['expected_checks'] for c,row in zip(counts,rows))/n,
            'global_check':sum(counts)/k,'fully_failed_tasks':sum(c==0 for c in counts),
            'total_failed':sum(c==0 for c in counts)/n,
            'agent_timed_out_tasks':sum(row['agent_timed_out'] for row in rows)}

def main():
    manifest=r.read_json(P/'manifest.json');plan=r.read_json(P/'plan.json')
    if manifest['status']!='completed':raise ValueError('Run is not complete; no final results published')
    with rp.attempt_lock(P,exclusive=False):
        rp.verify_provenance(r,P,plan,r.selected_config()[0])
        rows=[];groups=collections.defaultdict(list);families=collections.defaultdict(list);scene_results=[]
        for s in plan['sessions']:
            record=manifest['scenes'][s['id']]
            part=rp.verify_scene(r,P,plan,s,record)
            directory=rp.safe_directory(P,record['directory'])
            for line in (directory/'agent-traces/reproduction-metrics.jsonl').open():
                event=json.loads(line)
                if event.get('event')=='llm_error':
                    raise ValueError('Terminal provider failure remains in selected scene: '+s['id'])
            rows+=part
            difficulty=re.search(r' (Easy|Intermediate|Moderate|Hard|Extreme) \d+$',s['name']).group(1)
            groups[difficulty]+=part;families[s['family']]+=part
            scene_results.append({'session_id':s['id'],'name':s['name'],'family':s['family'],'difficulty':difficulty,
                                  'generation':record['generation'],'directory':record['directory'],**metrics(part)})
        summary=r.summarize(plan,rows,'completed')
        assert summary['valid_complete_run'] and rows==r.read_json(P/'results.json') and summary==r.read_json(P/'summary.json')
        perf=rp._performance(r,P,manifest);assert perf==r.read_json(P/'performance.json')
        before=r.read_json(P/'recovery-audit/0001/manifest.json');recovery=r.read_json(P/'recovery-audit/0001/recovery-plan.json')
        for sid,old in before['scenes'].items():
            if old.get('seal_sha256'):
                assert r.digest((P/old['directory']/'complete.json').read_bytes())==old['seal_sha256']
            if sid not in recovery['targets']:
                assert manifest['scenes'][sid]['directory']==old['directory']
                assert manifest['scenes'][sid]['seal_sha256']==old['seal_sha256']
        wall=sum(v['wall_seconds'] for v in manifest['invocations'])
        final={'attempt':str(P.relative_to(ROOT)),'verified_at':rp.timestamp(),'status':'complete_verified',
               'identity_sha256':manifest['identity_sha256'],'protocol':manifest['protocol'],'executor':manifest['executor'],
               'configuration':r.read_json(P/'configuration.json'),
               'source_hashes_sha256':r.digest((P/'source_hashes.json').read_bytes()),
               'verification':{'sealed_scenes':75,'original_checker_replayed_tasks':1500,'checks':9396,
                               'summary_and_performance_match':True,'preserved_68_scenes_unchanged':True,
                               'original_seals_unchanged':True,'selected_terminal_provider_failures':0},
               'summary':summary,'metrics':metrics(rows),'scene_difficulty':{k:metrics(v) for k,v in groups.items()},
               'family':{k:metrics(v) for k,v in families.items()},'scenes':scene_results,
               'failed_checks_by_endpoint':dict(collections.Counter(leaf['definition']['endpoint'] for row in rows for leaf in row['leaves'] if leaf['result'] is False).most_common()),
               'recovery':{'replayed_scenes':len(recovery['targets']),'replayed_tasks':recovery['replay_tasks'],
                           'original_snapshot':'recovery-audit/0001','selection_policy':recovery['selection_rule'],
                           'invocations':manifest['invocations']},'active_wall_seconds_including_repair':wall,
               'performance':{k:({a:b for a,b in v.items() if a!='by_job'} if k in ('selected_generations','all_executed_generations') else v) for k,v in perf.items()},
               'paper_reference':{'task_pass':.696,'avg_check':.834,'global_check':.8076,'total_failed':.068,
                                  'comparability':'Historical deepseek-flash; local model is current official deepseek-flash. Not identical model/protocol.'}}
        OUT.mkdir(parents=True,exist_ok=True)
        r.save_json(OUT/'results.json',final)
        scene_map={s['session_id']:s for s in scene_results}
        with (OUT/'tasks.csv').open('w',newline='',encoding='utf-8-sig') as f:
            fields=['session_id','scene_name','scene_difficulty','family','generation','task_id','name','passed','passed_checks','expected_checks','agent_timed_out']
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for row in rows:
                scene=scene_map[row['session_id']]
                w.writerow({**{k:row.get(k) for k in ['session_id','task_id','name','passed','expected_checks','agent_timed_out']},
                            'scene_name':scene['name'],'scene_difficulty':scene['difficulty'],'family':scene['family'],
                            'generation':scene['generation'],'passed_checks':sum(x['result'] for x in row['leaves'])})
        with (OUT/'scenes.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(scene_results[0]));w.writeheader();w.writerows(scene_results)
        m=final['metrics'];sperf=perf['selected_generations'];allperf=perf['all_executed_generations']
        pct=lambda x:f'{x*100:.2f}%'
        lines=['# 第一轮全量 baseline 结果（已核验）','',f"运行：`{P.name}`。核验时间：{final['verified_at']}。",'',
               '75 场景、1500 任务、9396 项原检查全部有效完成。模型为当前官方 `deepseek-flash`，Agent4Drone 原工具/导航，500 秒预算，执行器配置 8 workers。','',
               '| 指标 | 本轮 | 论文 Agent4Drone + V4 Flash |','|---|---:|---:|',
               f"| Task Pass | {m['passed_tasks']}/1500，{pct(m['task_pass'])} | 69.60% |",
               f"| Avg. Check | {pct(m['avg_check'])} | 83.40% |",
               f"| Global Check | {m['passed_checks']}/9396，{pct(m['global_check'])} | 80.76% |",
               f"| Total Failed | {m['fully_failed_tasks']}/1500，{pct(m['total_failed'])} | 6.80% |",
               f"| Agent 超时 | {m['agent_timed_out_tasks']}/1500 | 无同口径对照 |",'',
               '模型版本、服务条件和超时评分边界与历史论文不完全一致。论文数字仅作历史参考；后续创新与消融应与本轮同条件 baseline 对比。','',
               '## 场景难度','', '| 难度 | 通过任务 / 任务 | Task Pass | Global Check |','|---|---:|---:|---:|']
        for k in ['Easy','Intermediate','Moderate','Hard','Extreme']:
            v=final['scene_difficulty'][k];lines.append(f"| {k} | {v['passed_tasks']}/{v['tasks']} | {pct(v['task_pass'])} | {pct(v['global_check'])} |")
        lines+=['','## 场景族','', '| 任务族 | 通过任务 / 任务 | Task Pass | Global Check |','|---|---:|---:|---:|']
        for k,v in final['family'].items():lines.append(f"| {k} | {v['passed_tasks']}/{v['tasks']} | {pct(v['task_pass'])} | {pct(v['global_check'])} |")
        lines+=['','## 未通过检查的分布','', '| 原检查端点 | 未通过次数 |','|---|---:|']
        for endpoint,count in final['failed_checks_by_endpoint'].items():lines.append(f'| `{endpoint}` | {count} |')
        lines+=['','端点失败是结果分类，不等同于根因；例如航点未到达可能来自预算、导航、规划或原任务可满足性，需要结合轨迹进一步区分。','','## 故障与补跑边界','',
                '初次执行统计为 1483 条已评分、17 条缺失或无效，但这不等于仅剩 17 条可直接补齐。6 个已封存场景中共有 37 条任务遭遇终止性模型错误（36 个 402、1 个重试耗尽的 429）；另一场景在余额耗尽前因截止附近的取消状态异常而中断。',
                '中断时原始已评分部分为 1255 / 1483 通过（84.63%），Avg. Check 92.77%、Global Check 89.14%、Total Failed 3.24%。这些值混入服务故障且分母不完整，仅为中断记录，不能作为正式全量成绩。',
                '恢复依据在补跑前固定：重跑全部 6 个受服务故障影响场景与 1 个不完整场景，共 140 条；保留其他 68 场景、1360 条。包含正常失败的保留场景没有为了提高分数重跑。任务按原顺序重建环境与黑板，新旧执行代次分别保存，不能挑选两次最好成绩。',
                '原始中断汇总保存在运行目录 `recovery-audit/0001/`，新场景版本在 `retries/<scene_id>/0002/`。此次属于显式记录的基础设施故障修复，不是完全无故障的一次运行；后续方法比较应采用相同的基础设施异常处理口径。','',
                '## 时间与开销','',f"累计活跃运行墙钟（含初次执行和补跑，不含两次之间等待）：{wall/3600:.2f} 小时。",'',
                f"最终选定版本模型调用：{sperf['llm']['started_calls']}；token：{sperf['llm']['usage']['total_tokens']:,}。",
                f"所有执行版本实际模型调用（含被替代场景）：{allperf['llm']['started_calls']}；token：{allperf['llm']['usage']['total_tokens']:,}。",'',
                '性能和费用分析必须区分最终选定版本与所有执行版本。Token 不直接等于费用，还受缓存和服务计价影响。历史 402/429 仍保留在全部执行开销中，不能把它们从故障记录删除。','',
                '## 核验证据与使用','',
                '全部选定场景通过封存文件校验和原检查重放，汇总、分母与遥测重算一致；68 个复用场景及原封存标记保持不变。',
                '[机器可读结果](results.json) · [1500 条任务明细](tasks.csv) · [75 场景明细](scenes.csv)','',
                '这是当前模型与本地协议下的第一轮完整 baseline。后续以同模型、同预算、同并发做方法对照；小幅收益需通过重复与场景层面的配对分析判断，不能用当前分数与历史论文之差归因于新增方法。']
        lines+=['','## 请求与资源核验','', '| 项目 | 正式选定版本 | 所有执行版本 |','|---|---:|---:|']
        for label,key in [('HTTP 请求','attempts'),('HTTP 429','rate_limited_429'),('HTTP 5xx','server_errors_5xx'),('传输错误','errors'),('SDK 重试','retry_attempts')]:
            lines.append(f"| {label} | {sperf['http'][key]} | {allperf['http'][key]} |")
        lines.append(f"| HTTP 402 | {sperf['http']['status_counts'].get('402',0)} | {allperf['http']['status_counts'].get('402',0)} |")
        lines+=['',f"正式选定版本模型调用延迟 p50 / p95：{sperf['llm']['latency']['p50_seconds']:.2f} / {sperf['llm']['latency']['p95_seconds']:.2f} 秒。",
                f"所有运行阶段的 fleet 采样 RSS 峰值：{perf['fleet_resources']['peak_sampled_tree_rss_kib']/1024**2:.2f} GiB；CPU 峰值：{perf['fleet_resources']['peak_sampled_tree_cpu_percent']:.2f}%（100% 为一个核心）。",
                '可恢复的单次传输错误与重试仍保留；正式选定版本不得残留终止性的余额不足或限流失败。采样资源峰值不保证捕获全部瞬时峰值。']
        lines+=['','## 本轮结论与后续实验','',
                f"当前全量仍有 {m['tasks']-m['passed_tasks']} 条任务未通过，其中 {m['tasks']-m['passed_tasks']-m['fully_failed_tasks']} 条属于部分完成。Extreme 场景通过率为 {pct(final['scene_difficulty']['Extreme']['task_pass'])}，目标分配类通过率为 {pct(final['family']['target_assignment']['task_pass'])}；全量结果没有达到评测上限，后续仍有明确提升空间。",
                f"Agent 超时 {summary['agent_timed_out_tasks']} 条，其中 {summary['timed_out_but_passed_tasks']} 条任务仍通过。完全失败任务数与 Agent 超时是独立统计口径，即使数值相同也不是同一组任务。",
                '目标分配更难、复杂场景更容易失败的分布与论文定性观察相容，但模型版本不同，不能把相对论文报告值的差异归因于方法改进。后续创新应对照本轮同模型、同预算、同并发的 baseline。',
                '后续优先对目标分配和 Extreme 的失败轨迹做根因分类；航点/导航工具与原任务可满足性问题需单独审计，导航修复另立修正版 baseline。',
                '余额错误曾被 Agent 包装成 completed 作业与 success=False 返回，runner 仍按世界状态评分，因而存在模型服务失败但任务检查通过的记录。以后应在运行层识别终止性服务错误并阻断受影响场景的正式计分；本轮使用事前固定范围的故障补跑修复，未修改冻结执行代码。']
        (OUT/'REPORT_CN.md').write_text('\n'.join(lines)+'\n')
        hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(OUT.iterdir()) if f.is_file() and f.name!='SHA256SUMS.json'}
        r.save_json(OUT/'SHA256SUMS.json',hashes)
        print(json.dumps({'output':str(OUT),'metrics':m,'active_wall_hours':wall/3600,'scene_difficulty':final['scene_difficulty']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
