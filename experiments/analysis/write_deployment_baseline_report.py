"""Render the reconciled Chinese report from audited offline accounting."""
import collections
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'docs/results/baseline-deployment-20261005'
read=lambda p:json.loads(Path(p).read_text())
data=read(OUT/'results.json')
rows=read(OUT/'tasks.json')
main=data['summary']['first_attempt_no_replay']
d,q=(main[k]['metrics'] for k in ('deepseek','qwen'))
pct=lambda x:f'{x*100:.2f}%'
num=lambda x:f'{x:,}'
lines=[]
def add(s=''):lines.append(s)
def table(headers, values):
    add('| '+' | '.join(headers)+' |');add('|'+'|'.join('---' for _ in headers)+'|')
    for v in values:add('| '+' | '.join(str(x).replace('\n','；') for x in v)+' |')
    add()

add('# Agent4Drone 两模型 baseline 复现与部署失败计分实验报告\n')
add(f'归档日期：2026-10-05，北京时间。离线核验完成于 {data["created_at"]}。\n')
add('本轮已跑通 MultiUAV-Plat 的数据、Agent、并行仿真、截止控制、原检查评分及证据归档链路，并完成两种模型各 1500 条计划任务的失败计分收束。**“计分收束完成”不等于所有任务都实际执行完，也不等于原 runner 的 `valid_complete_run=true`。** Worker 无法结束而阻断的后续任务纳入失败分母，原始未评分记录保持原样。\n')
add(f'主口径采用每个场景的首次执行，不使用整场补跑改善成绩。DeepSeek 官方 `deepseek-flash` 的任务通过率为 **{pct(d["task_pass"])}**；Qwen `qwen3.8-flash` 为 **{pct(q["task_pass"])}**。要求检查通过且 Worker 已正常退出的严格部署成功率分别为 **{pct(d["deployment_pass"])} / {pct(q["deployment_pass"])}**。本报告另附已发生恢复后的收束结果，作为敏感性分析。\n')
add('本次停止已有补跑后进行离线核验，没有启动新的模型调用批次，没有修改 Agent、导航工具、仿真、检查器、数据、依赖或模型参数。Qwen 第三次补跑已于 **2026-10-05 13:42:38** 结束，自动恢复已暂停；该次人为停止的执行只计入实际开销，不把它新产生的未执行任务伪称为 Worker 故障。\n')

add('## 1 实验对象与复现边界\n')
table(['项目','配置与范围'],[
    ['基准','75 场景 × 20 任务 = 1500 任务；9396 个原始叶子检查'],
    ['场景组成','3 类任务族 × 5 档场景难度 × 5 个场景种子'],
    ['任务族','Area Assignment and Patrol、Area Search、Target Assignment；各 500 任务'],
    ['场景难度','Easy、Intermediate、Moderate、Hard、Extreme；各 300 任务'],
    ['单条任务标签','easy / medium / hard 与场景难度不同；不能称全量为 easy'],
    ['方法','Agent4Drone；本轮未训练或微调模型'],
    ['DeepSeek','官方 https://api.deepseek.com；模型 ID deepseek-flash；本地配置标注 V4.1 Flash'],
    ['Qwen','百炼 https://dashscope.aliyuncs.com/compatible-mode/v1；模型 ID qwen3.8-flash'],
    ['并发','8 个隔离场景 worker；同一场景的 20 个任务按原顺序串行'],
    ['任务预算','500 秒动作窗口；关闭新动作、在途请求排空上限 30 秒；等待 Worker 退出上限 120 秒'],
    ['任务间状态','每场景只从原始 JSON 初始化一次；每题落地、充电，保留位置及场景累计状态；按 session 共享黑板'],
    ['执行协议','agent4drone-v3-gui500-timeout-scoring；场景执行器 scene-parallel-v1'],
    ['本次离线计分协议','deployment-no-scene-replay-v1；事后明确追加的部署失败口径'],
])
add('两轮冻结归档中 `server/`、`controller/`、`agent4drone/` 源码一致；不同项在 runner、Qwen 接口适配、监测与测试目录中，详见 results.json 的 source_differences。Qwen 适配器保留并回传 reasoning_content；Qwen 运行增加了终止性服务错误后停止整批的保护，而旧 DeepSeek runner 可能将模型错误包装为已结束作业。因此两轮不是执行外围完全相同的受控重复实验。\n')
add('客户端发送 temperature=0.1。既有百炼准备记录披露了思考模式及服务端温度调整，不能宣称两模型有效采样温度、推理预算或后台负载一致。API 别名与本地模型名称也不能证明固定了服务端权重。模型日期、接口、适配与运行差异需随论文披露。\n')
add('数据与冻结归档：\n')
for m,a in data['attempts'].items():add(f'- {m}：`{a}`。')
add('\n原始 benchmark SHA-256：`b5040097d2bfdd44600f3bf486fdb43ee3eb1247fec9c67900b5fcda6feb94a3`。核验使用原计划和原 JSON，不删除困难任务或有争议检查。\n')

add('## 2 从数据到最终计分的流程\n')
add('1. 固定 benchmark、75 个场景、原始任务顺序、模型配置、源码与依赖快照。\n2. 调度器为场景分配隔离的 Server、Agent 和日志目录；最多同时执行 8 个场景。\n3. 载入原环境并建立该 session 的黑板；逐题执行 set-current、落地和充电准备。\n4. Agent 接收自然语言指令，经模型调用选择合法感知、导航、覆盖、拍照等工具，与仿真环境交互。8 workers 是实验并发数，不代表单任务采用 8 个相互协作的独立 LLM。\n5. Agent 完成或 500 秒到期后，关闭该任务的动作准入，排空已接收请求并暂停环境演化，保存 after、barrier 和 window。\n6. 使用原隐藏检查树评估冻结环境，保存逐叶响应和整树通过结果；随后等待 Worker 确认结束，决定是否能安全进入下一题。\n7. 本报告保留已经形成的原检查结果；执行中断后未评分的后续题按失败补齐计分分母。首次执行口径不整场重跑，不拼接不同代次的最好结果。\n')
add('检查树包含 AND、OR 和反向期望；原数据有 26 条任务包含 OR。Task Pass 使用原树返回值，不能用“所有叶子为真”替代。已有动作窗口结束后不再允许模型获得额外动作预算；排空、检查及清理耗时仍计入墙钟成本。\n')

add('## 3 为什么未封存，以及本次怎样计失败\n')
add('这不是已完成结果在磁盘中丢失。runner 在等待 Worker 退出之前已经保存环境与评分；如果 Worker 在额外等待 120 秒后仍未确认结束，runner 为避免旧线程污染下一题而停止该场景，因此后续任务没有执行。作业状态 cancelled 只是取消标记，不能证明执行线程已返回。\n')
add('本轮进一步核对发现：Qwen 首次执行的 **13 个 Worker 未退出案例均有 1 个 HTTP 请求开始事件，而没有对应结束或错误事件**。取消在模型/工具回调边界检查，不能保证即时打断一个尚未返回的同步请求。这支持“未返回的模型请求阻碍协作式取消”的解释；遥测不能进一步判定是服务端推理、网络等待还是客户端阻塞，也不能据此断言模型规划错误。证据见 worker-stop-evidence.json。\n')
table(['对象','首次执行中已评分','因执行停止而未评分','其他独立问题'],[
    ['DeepSeek','1483 / 1500','17 任务 / 144 检查；1 个场景','37 个任务发生终止性模型服务错误：36 个 HTTP 402，1 个最终 HTTP 429'],
    ['Qwen','1444 / 1500','56 任务 / 587 检查；13 个场景发生 Worker 未退出','首轮没有终止性 API 错误；13 个未结束请求单列'],
])
add('DeepSeek 的 17 条缺口来自 `Unexpected/cancelled job; cannot assume execution has stopped`，与 Qwen 的 `Worker did not exit` 不是同一个错误字符串，但同属无法确认安全继续执行；本报告分别保留原因。DeepSeek 37 个服务失败任务中有 12 个原检查通过，说明继承的环境状态可能满足检查；部署口径将这些服务执行失败任务记零，其原检查响应仍保留。\n')
table(['情况','Task Pass 与检查分','严格部署成功'],[
    ['正常完成并有检查','保留原检查树与逐叶结果','检查通过且 Worker 确认退出时成功'],
    ['500 秒超时，但环境已冻结且已有检查','保留原检查，不自动全零','检查通过且 Worker 确认退出时成功'],
    ['环境已冻结、检查已保存，但 Worker 未结束','保留原检查与部分分数','失败；即使环境检查通过也不算可靠收尾'],
    ['上述中断导致后续任务未执行或未评分','任务失败；计分贡献为 0，保留全部任务和检查分母','失败'],
    ['终止性模型服务错误','首次部署口径任务与检查贡献为 0；保留原观察值','失败，另标服务故障，不称模型能力失败'],
])
add('补零只发生在派生结果的 credited_checks、task_pass 等字段。原始 leaves、status=not_run/invalid、原日志和封存文件没有被改成伪造的实测失败。`accounting_complete=true` 表示 1500 个计划任务都有明确计分归属；原 runner 的有效完成标志保持不变。\n')
add('本次协议变更发生在观察结果之后，应作为事后审计披露，不能声称事前注册。后续对照与消融需在运行前固定同一失败规则，禁止通过事后挑选口径制造改进。\n')

add('## 4 首次部署结果 不整场重跑\n')
table(['指标','DeepSeek','Qwen'],[
    ['计划任务 / 检查','1500 / 9396','1500 / 9396'],
    ['Task Pass 原检查通过并对缺失及服务失败记零',f'{d["passed_tasks"]}/1500 = {pct(d["task_pass"])}',f'{q["passed_tasks"]}/1500 = {pct(q["task_pass"])}'],
    ['严格部署成功 额外要求 Worker 结束',f'{d["deployment_passed_tasks"]}/1500 = {pct(d["deployment_pass"])}',f'{q["deployment_passed_tasks"]}/1500 = {pct(q["deployment_pass"])}'],
    ['Avg. Check',pct(d['avg_check']),pct(q['avg_check'])],
    ['Global Check',f'{d["passed_checks"]}/9396 = {pct(d["global_check"])}',f'{q["passed_checks"]}/9396 = {pct(q["global_check"])}'],
    ['Total Failed 零检查贡献',f'{d["zero_check_tasks"]}/1500 = {pct(d["total_failed"])}',f'{q["zero_check_tasks"]}/1500 = {pct(q["total_failed"])}'],
    ['Agent 超时',f'{d["agent_timeouts"]}（{pct(d["agent_timeouts"]/1500)}）',f'{q["agent_timeouts"]}（{pct(q["agent_timeouts"]/1500)}）'],
    ['超时但原检查任务通过',d['timeouts_with_task_pass'],q['timeouts_with_task_pass']],
    ['已有检查但未确认 Worker 退出',d['scored_without_worker_confirmation'],q['scored_without_worker_confirmation']],
])
add('Qwen 的两个成功指标相差 1 条：`cc14d231/181df0da` 环境检查 4/4，但 Worker 未确认退出。保留这一差别可以同时评价任务效果与执行可靠性。Total Failed 包括政策补零，不能解读成所有这些任务都实际执行且一项也没做对；它也不是 1−Task Pass。\n')
add('指标定义：Task Pass 为整棵树通过的任务数 / 1500；Avg. Check 为每题叶子通过比例的平均；Global Check 为通过叶子数 / 9396；Total Failed 为计入后通过叶子数为零的任务比例。严格部署成功在 Task Pass 基础上还要求 Worker 安全结束，无终止性服务错误。\n')
add('### 按场景难度\n')
table(['场景难度','任务数','DeepSeek Task Pass','Qwen Task Pass','DeepSeek / Qwen Global Check'],[
    [level,300,f'{main["deepseek"]["by_scene_difficulty"][level]["passed_tasks"]}/300 = {pct(main["deepseek"]["by_scene_difficulty"][level]["task_pass"])}',
     f'{main["qwen"]["by_scene_difficulty"][level]["passed_tasks"]}/300 = {pct(main["qwen"]["by_scene_difficulty"][level]["task_pass"])}',
     f'{pct(main["deepseek"]["by_scene_difficulty"][level]["global_check"])} / {pct(main["qwen"]["by_scene_difficulty"][level]["global_check"])}']
    for level in ('Easy','Intermediate','Moderate','Hard','Extreme')])
add('### 按场景族\n')
table(['场景族','DeepSeek Task Pass','Qwen Task Pass'],[
    [family,f'{main["deepseek"]["by_family"][family]["passed_tasks"]}/500 = {pct(main["deepseek"]["by_family"][family]["task_pass"])}',
     f'{main["qwen"]["by_family"][family]["passed_tasks"]}/500 = {pct(main["qwen"]["by_family"][family]["task_pass"])}']
    for family in ('area_assignment_and_patrol','area_search','target_assignment')])
add('严格部署口径下，Qwen 的 Extreme 为 146/300（48.67%），Target Assignment 为 306/500（61.20%），其余分组不变。DeepSeek 的 Moderate 和 Target Assignment 同时受服务故障影响，不能直接将该分组下降解释成任务难度规律。\n')
pairs=data['paired_first_attempt_deployment']
add(f'同任务 ID 配对的严格部署结果：共同通过 {pairs["both_pass"]} 条，仅 DeepSeek 通过 {pairs["deepseek_only"]} 条，仅 Qwen 通过 {pairs["qwen_only"]} 条，共同失败 {pairs["both_fail"]} 条。两模型每场景内的轨迹和继承状态不同，配对不是保证每道题起点完全一致；单轮结果也不能证明统计显著性或稳定优劣。\n')

add('## 5 历史补跑结果怎样收束\n')
table(['统计口径','DeepSeek Task Pass','Qwen Task Pass','适用解释'],[
    ['A 首次执行 不整场重跑',pct(d['task_pass']),pct(q['task_pass']),'本报告部署主表；包含真实服务故障'],
    ['B 仅保留既有服务故障场景补跑，执行停止不补跑',pct(data['summary']['provider_recovered_runtime_no_replay']['deepseek']['metrics']['task_pass']),pct(q['task_pass']),'隔离已知付费/服务中断影响的辅助对照，仍不是完全单次运行'],
    ['C 已有恢复快照收束，剩余未评分记失败',pct(data['summary']['recovery_snapshot_remaining_fail']['deepseek']['metrics']['task_pass']),pct(data['summary']['recovery_snapshot_remaining_fail']['qwen']['metrics']['task_pass']),'直接满足本次停止补跑后的结果落盘；Qwen 包含此前整场重跑'],
    ['历史已发布 DeepSeek 恢复成绩','85.53%','不适用','保留历史，不再作为首次部署主成绩'],
])
add('口径 B/C 的 DeepSeek 保留 6 个明确服务故障场景的既有修复版本，但撤回了 `ae503ad1` 运行中断场景的补跑收益：保留首次已完成的 3 条，后续 17 条失败。历史 1283 次成功变为 1268 次，减少 15 次成功，**85.53% → 84.53%**。再完全撤回 6 个服务场景补跑，并将首次 37 个服务失败任务记零，得到主表 1243 次成功、82.87%。\n')
add('口径 C 的 Qwen 固定使用第三次补跑启动前的完整快照：68 个封存场景加 7 个 generation 3 中断场景；1482 条已有评分、18 条执行缺口记失败，得到 **1163/1500 = 77.53%**，Avg. Check **87.03%**、Global Check **80.69%**、Total Failed **5.80%**。未根据后续 generation 4 的任务成败挑选结果。首次部署则统一回到所有场景 generation 1，56 条缺口全部记失败。\n')
add('因此不能只把最后剩下的 18 条补零，就称它为“从未重跑”的实验。C 是已经发生恢复的工程收束结果；A 才对应取消整场恢复收益的部署问题。若后续论文主要研究 Agent 算法，可同时报告 A 与 B，并固定基础设施异常处理规则；不能只挑更有利的一个。\n')

add('## 6 时间 token 与实际开销\n')
table(['项目','DeepSeek 首次执行','Qwen 首次执行'],[
    ['8 workers 实测墙钟',f'{data["costs"]["deepseek"]["first_invocation_wall_seconds"]/3600:.2f} 小时',f'{data["costs"]["qwen"]["first_invocation_wall_seconds"]/3600:.2f} 小时'],
    ['已有原检查任务数',d['checker_scored_tasks'],q['checker_scored_tasks']],
    ['形成原检查的吞吐',f'{d["checker_scored_tasks"]/(data["costs"]["deepseek"]["first_invocation_wall_seconds"]/3600):.1f} 任务/小时',f'{q["checker_scored_tasks"]/(data["costs"]["qwen"]["first_invocation_wall_seconds"]/3600):.1f} 任务/小时'],
    ['严格部署成功吞吐',f'{d["deployment_passed_tasks"]/(data["costs"]["deepseek"]["first_invocation_wall_seconds"]/3600):.1f} 成功/小时',f'{q["deployment_passed_tasks"]/(data["costs"]["qwen"]["first_invocation_wall_seconds"]/3600):.1f} 成功/小时'],
    ['模型逻辑调用次数',*[num(main[m]['performance']['llm']['started_calls']) for m in ('deepseek','qwen')]],
    ['模型调用 p50 / p95',*[f'{main[m]["performance"]["llm"]["latency"]["p50_seconds"]:.2f} / {main[m]["performance"]["llm"]["latency"]["p95_seconds"]:.2f} 秒' for m in ('deepseek','qwen')]],
    ['任务评分就绪 p50 / p95',*[f'{main[m]["scoring_ready_seconds"]["p50_seconds"]:.2f} / {main[m]["scoring_ready_seconds"]["p95_seconds"]:.2f} 秒' for m in ('deepseek','qwen')]],
    ['已观察输入 token',*[num(main[m]['performance']['llm']['usage']['prompt_tokens']) for m in ('deepseek','qwen')]],
    ['已观察输出 token',*[num(main[m]['performance']['llm']['usage']['completion_tokens']) for m in ('deepseek','qwen')]],
    ['已观察总 token',*[num(main[m]['performance']['llm']['usage']['total_tokens']) for m in ('deepseek','qwen')]],
    ['其中缓存命中输入 token',*[num(main[m]['performance']['llm']['usage']['cache_hit_tokens']) for m in ('deepseek','qwen')]],
    ['无结束记录的模型请求',*[main[m]['performance']['llm']['unfinished_calls'] for m in ('deepseek','qwen')]],
])
add('任务评分就绪时延 = barrier.frozen_at − window.opened_at，样本仅含已有评分任务；包含轮询和排空，不等于无人机物理飞行时间，也不含 Checker 随后的计算时间。没有执行的题不伪填 500 秒。模型延迟分位数只覆盖有结束记录的调用；Qwen 长尾中还有 13 条没有结束记录，不能被这些分位数代表。\n')
add('### 所有已经发生的执行成本 含失败和被放弃的补跑\n')
table(['项目','DeepSeek','Qwen'],[
    ['执行调用批次','2：首次 + 1 次补跑','4：首次 + 3 次补跑，最后一次人工停止'],
    ['累计活跃墙钟',*[f'{data["costs"][m]["all_invocations_wall_seconds"]/3600:.2f} 小时' for m in ('deepseek','qwen')]],
    ['从首次启动到停止的日历耗时',*[f'{data["costs"][m]["calendar_elapsed_seconds"]/3600:.2f} 小时' for m in ('deepseek','qwen')]],
    ['全部模型逻辑调用',*[num(data['costs'][m]['all_executed_performance']['llm']['started_calls']) for m in ('deepseek','qwen')]],
    ['已观察输入 token',*[num(data['costs'][m]['all_executed_performance']['llm']['usage']['prompt_tokens']) for m in ('deepseek','qwen')]],
    ['已观察输出 token',*[num(data['costs'][m]['all_executed_performance']['llm']['usage']['completion_tokens']) for m in ('deepseek','qwen')]],
    ['已观察总 token',*[num(data['costs'][m]['all_executed_performance']['llm']['usage']['total_tokens']) for m in ('deepseek','qwen')]],
    ['其中缓存命中输入 token',*[num(data['costs'][m]['all_executed_performance']['llm']['usage']['cache_hit_tokens']) for m in ('deepseek','qwen')]],
    ['全部 HTTP 状态',*[str(data['costs'][m]['all_executed_performance']['http']['status_counts']) for m in ('deepseek','qwen')]],
    ['SDK HTTP 重试 / 传输错误',*[f'{data["costs"][m]["all_executed_performance"]["http"]["retry_attempts"]} / {data["costs"][m]["all_executed_performance"]["http"]["errors"]}' for m in ('deepseek','qwen')]],
    ['无结束记录的模型请求',*[data['costs'][m]['all_executed_performance']['llm']['unfinished_calls'] for m in ('deepseek','qwen')]],
    ['采样 fleet RSS 峰值',*[f'{data["costs"][m]["fleet_resources"]["peak_sampled_tree_rss_kib"]/1024**2:.2f} GiB' for m in ('deepseek','qwen')]],
])
for m,label in [('deepseek','DeepSeek'),('qwen','Qwen')]:
    first=main[m]['performance']['llm']['usage']['total_tokens'];all_=data['costs'][m]['all_executed_performance']['llm']['usage']['total_tokens']
    add(f'{label} 的全部补跑额外消耗已观察 token **{num(all_-first)}（相对首次 +{(all_/first-1)*100:.2f}%）**，额外活跃墙钟 **{(data["costs"][m]["all_invocations_wall_seconds"]-data["costs"][m]["first_invocation_wall_seconds"])/3600:.2f} 小时**。\n')
add('Qwen 首次总 token 与 DeepSeek 接近，但墙钟约为两倍；这说明总 token 不能代替等待时间、长尾请求和场景串行依赖的成本。不能仅用减少输出 token 推断更快，也不能把未返回的请求当作零费用。全部 Qwen 记录还有 36 条请求没有结束回执，已观察 token 是可核实的下界。\n')
add('token 汇总优先取每次 HTTP 响应的 usage，并避免与 LLM 回调重复计数；累计输入包括反复发送的上下文和缓存命中，不能解读为独立文本量。货币费用没有直接读取供应商账单，**本报告不捏造已实付人民币金额**。DeepSeek 全执行缓存未命中输入为 74,645,424 token；Qwen 没有直接回传 cache_miss，输入减缓存命中为 165,281,950，仅是非命中输入的差值推算。\n')
add('对固定单价层，费用可按 `(缓存命中输入×命中价 + 未命中输入×未命中价 + 输出×输出价)/1,000,000` 估算；若存在上下文长度阶梯、思考计价、折扣或未返回请求计费，应逐请求使用实际价层并与账单核对。不同供应商 token 数与计价不可直接互换。本包保留 usage、缓存和所有代次遥测，便于后续对账。\n')
add('8 workers 已完成全量级并发运行；此前同一 160 条校准子集从 4 路到 8 路的实测墙钟由 81 分 50 秒降至 56 分 9 秒，约 1.46 倍吞吐。两次运行时段不同，该值不是普适加速比，不能直接移植到 Qwen 长尾。详见已有 [8 workers 校准报告](../../CALIBRATION8_RESULTS_20261003_CN.md)。\n')

add('## 7 初步失败案例分析\n')
add('以下案例按原始首次执行证据核对；机制分析是代表性证据，不把所有失败自动归入单一根因。先区分运行可靠性、规划执行、验证语义与数据规格，后续局部修复始终应占用同一 500 秒预算。\n')
table(['类型与任务','观察结果','解释与可研究方向'],[
    ['Worker 长尾未退出\n79b207ff/9349f4d1','Qwen 原检查 0/20，随后 5 条任务未执行','最后一个模型 HTTP 请求约在动作窗口第 194 秒开始，未见结束回执；截止、评分与退出是三个不同阶段。需研究请求截止与执行器取消，不是反复重跑求通过'],
    ['检查通过但执行未收尾\ncc14d231/181df0da','Qwen 4/4，但 Worker 未退出；后续 1 条缺失','严格部署成功必须有结束确认；世界状态通过不足以证明执行器可靠结束'],
    ['最终距离与数值余量\nfb8971ec/82077301','两模型均 2/3；距离上限 19，DeepSeek 实测 19.012，Qwen 194.378','确定性终态计算与动作语义校验；把移动 19 米与最终相距 19 米区分'],
    ['障碍内航点\ncc05a425/25d30560','两模型均 3/4；指定点在 height=0 圆障碍内，深入程度超过 3 米容差','局部几何不可达证据；不能把这种未通过全部归为模型能力，也不应为提高分数向 Agent 泄露隐藏规则'],
    ['指令与检查阈值不一致\nc350908c/f30cc643','两模型均 3/4；指令起飞至 1 米，检查起飞历史阈值为 5 米','属于任务规格冲突候选，需单列数据审计；本轮保留原检查，不事后删题'],
    ['完成主动作却漏收尾\n35ce64d8/cf8ee51f','Qwen 38/39；三机长航线完成，最后一架未降落便耗尽预算','给收尾动作预留预算、以任务条件账本跟踪未满足项；此题 DeepSeek 首轮受服务故障影响，不引用其补跑成功冒充首轮对照'],
    ['未发现不等于不存在\n76802002/55b61526','两模型均 2/3；原场景存在 Waypoint 1，局部搜索未找到','黑板应区分未发现、已观测与推断；探索覆盖和实体解析比盲目重复查询更值得测试'],
    ['拍照动作成功不等于拍到指定目标\n93831d6d/7af66f50','Qwen 3/4，缺照片内指定目标；DeepSeek 0/4','区分到达事件、拍照 API 成功和目标在摄影范围内；验证需要与任务目标绑定'],
])
add('案例证据索引见 cases.json，既有深读轨迹见 [失败案例集](../failure-analysis-20261005/CASEBOOK_CN.md)。旧案例集统计采用当时不同快照；本报告只复用经核对的具体案例，不把旧分母下的比例移植为本轮最终统计。\n')
add(f'首次口径中，已有部分分数且只差一个叶子检查的失败任务：DeepSeek **{d["near_miss_observed"]}** 条，Qwen **{q["near_miss_observed"]}** 条。这是候选集，不是承诺能挽回的增量；其中包含几何冲突、规格问题和预算耗尽。零检查贡献还混有政策补零及单检查任务阈值未达，不能等同于完全没有动作。\n')
table(['原检查中观察到的失败端点','DeepSeek','Qwen'],[
    [e,main['deepseek']['observed_failed_checks_by_endpoint'].get(e,0),main['qwen']['observed_failed_checks_by_endpoint'].get(e,0)]
    for e in ('/check/drone_has_visited_position','/check/drone_has_moved_directed_distance','/check/target_is_reached_by_drone','/check/target_in_photo_taken_by_drone','/check/drone_on_ground')])
add('上表只统计实际观察到的失败，不把未执行任务的政策补零当成这些端点真的被执行失败。补零端点另列于 results.json。既有导航契约审计还发现 Server 的 status 与工具 success 判定不一致；本轮没有修复它。导航修正版需单独建立 baseline，不能把修复后结果混入本轮原工具结果。\n')

add('## 8 与原论文的关系及下一步\n')
add('已重新读取本地归档论文第 10 页表 4。原论文 Agent4Drone + deepseek-flash 为 Task Pass 69.60%、Avg. Check 83.40%、Global Check 80.76%；它不是当前官方 deepseek-flash 服务别名或 Qwen3.8 Flash。当前主表使用部署失败追加计分，模型、接口适配及故障处理不同。因此本次是**在原平台与任务上建立当前模型 baseline**，不能声称严格复现了历史模型数值，也不能由本轮更高/更低的数值证明原论文结果可靠或不可靠。\n')
add('已有证据能确认：平台链路可运行、原检查可离线重放、当前模型可区分表现，Target Assignment 与长序列任务仍有明显失败空间。当前还缺同一协议下的多次独立重复、稳定的供应商故障条件、ReAct 对照与模块消融，因此没有足够证据把模型差异归因于某个方法模块。\n')
add('下一阶段先冻结 no-scene-replay 规则与服务故障处理，再独立实现预算内取消、执行条件验证和局部修复。先用固定开发子集验证机制和成本；正式方法与消融使用相同 1500 任务、原顺序、500 秒、8 workers 和模型配置，并同时报告任务成功、可靠收尾、检查部分分、调用时延及全部实际开销。需要改导航契约、重置语义或检查定义时另设修正版协议，不改写本报告历史记录。\n')
add('## 9 核验与归档清单\n')
v=data['verification']
add(f'已核验两轮冻结归档身份、配置/源码/依赖文件哈希、原场景数据与任务/检查顺序；对所有统计口径涉及的 **{v["original_checker_replayed_scored_rows"]} 条不同代次已评分记录**，使用保存响应重放原 Checker。对 **{v["available_scene_seals_checked"]} 个存在封存标记的场景代次**，核对计划/归档身份及实际消费证据的封存哈希；20 条未确认 Worker 的已评分记录具备环境静止屏障。未声称把原本未封存场景补成了 runner 原生完成场景。\n')
add('所有已执行代次的 telemetry 重新汇总，与最后落盘 performance.json 一致；输入输出总 token 可相加核对，没有同时累加 HTTP 与回调的重复 usage。任务集为两模型同一 1500 个 task key，不混入 smoke/pilot/calibration。原场景日志保持只读，派生文件独立归档。\n')
table(['文件','用途'],[
    ['[results.json](results.json)','三种计分口径、难度/任务族拆分、时间/token/缓存、协议和核验信息'],
    ['[tasks.csv](tasks.csv)','9000 行：3 个口径 × 2 个模型 × 1500；使用前按 cohort 与 model 过滤'],
    ['[scenes.csv](scenes.csv)','450 行：3 个口径 × 2 个模型 × 75 场景'],
    ['[failure_accounting.csv](failure_accounting.csv)','所有补零与 Worker 未结束记录，附原状态、代次、错误原因和证据目录'],
    ['[tasks.json](tasks.json)','原叶子结果与派生计分并列，不覆盖原始检查'],
    ['[cases.json](cases.json)','代表失败案例在首次执行中的结果与证据路径'],
    ['[worker-stop-evidence.json](worker-stop-evidence.json)','13 个 Worker 未结束案例的未返回 HTTP 与环境屏障证据'],
    ['[verification.json](verification.json)','核验数量与输入稳定性检查'],
    ['[evidence-sha256.json](evidence-sha256.json)','使用过的原始输入证据哈希'],
    ['[SHA256SUMS.json](SHA256SUMS.json)','本报告及所有派生成果的校验和'],
])
add('离线重建命令（不调用模型、不重跑任务）：\n\n```bash\ncontroller/.venv/bin/python experiments/analysis/finalize_deployment_baselines.py\ncontroller/.venv/bin/python experiments/analysis/write_deployment_baseline_report.py\n```\n')
add('默认正式引用主口径 `first_attempt_no_replay`；与当前剩余任务收束直接对应的口径为 `recovery_snapshot_remaining_fail`。每次引用分数都应同时写出口径，避免将 82.87%、84.53%、85.53% 当作同一种实验。\n')

(OUT/'REPORT_CN.md').write_text('\n'.join(lines),encoding='utf-8')
casekeys=[('79b207ff','9349f4d1'),('cc14d231','181df0da'),('fb8971ec','82077301'),('cc05a425','25d30560'),('c350908c','f30cc643'),('35ce64d8','cf8ee51f'),('76802002','55b61526'),('93831d6d','7af66f50')]
cases=[{k:x[k] for k in ('model','session_id','task_id','name','generation','original_status','original_passed','observed_passed_checks','expected_checks','accounting_reason','evidence_dir','original_leaves')} for x in rows if x['cohort']=='first_attempt_no_replay' and (x['session_id'],x['task_id']) in casekeys]
(OUT/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
# This diagnostic was generated from the same read-only generation-1 events.
# Reconstruct it here so the complete report is reproducible without /tmp state.
worker=[]
for x in rows:
    if x['cohort']!='first_attempt_no_replay' or x['model']!='qwen' or x['original_status']!='scored' or x['worker_confirmed']:continue
    directory=ROOT/x['evidence_dir'];tid=x['task_id'];w=read(directory/f'{tid}.window.json')
    events=[json.loads(line) for line in (directory/'agent-traces/reproduction-metrics.jsonl').read_text().splitlines()]
    events=[e for e in events if e.get('job_id')==x['job_id']]
    starts={e['request_id']:e for e in events if e['event']=='http_start'}
    ends={e['request_id'] for e in events if e['event'] in ('http_end','http_error')}
    worker.append(dict(session_id=x['session_id'],task_id=tid,evidence_dir=x['evidence_dir'],
                       task_pass=x['task_pass'],pending_http_requests=[dict(request_id=rid,
                       started_seconds_after_window_open=e['timestamp']-w['opened_at'],age_at_cutoff=w['cutoff_at']-e['timestamp']) for rid,e in starts.items() if rid not in ends],
                       barrier=read(directory/f'{tid}.barrier.json')))
assert len(worker)==13 and all(len(x['pending_http_requests'])==1 for x in worker)
(OUT/'worker-stop-evidence.json').write_text(json.dumps(worker,ensure_ascii=False,indent=2)+'\n')
evidence=read(OUT/'evidence-sha256.json')
for p in [Path(__file__),ROOT/'docs/results/failure-analysis-20261005/REPORT_CN.md',ROOT/'docs/results/failure-analysis-20261005/CASEBOOK_CN.md',ROOT/'docs/results/failure-analysis-20261005/navigation-contract-probe.json',ROOT/'docs/CALIBRATION8_RESULTS_20261003_CN.md',ROOT/'docs/QWEN38_FULL_PREPARATION_CN.md',*list((ROOT/'docs/related-paper').glob('Zhang*MultiUAV*'))]:
    evidence[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
(OUT/'evidence-sha256.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='SHA256SUMS.json'}
(OUT/'SHA256SUMS.json').write_text(json.dumps(hashes,indent=2)+'\n')
print(OUT/'REPORT_CN.md')
