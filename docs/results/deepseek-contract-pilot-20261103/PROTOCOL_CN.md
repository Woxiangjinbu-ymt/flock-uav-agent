# 新版 DeepSeek 执行契约行为 pilot 协议

日期：2026-11-03。用户已明确授权这一次有界真实 DeepSeek 小批。固定 **4 个条件 × 2 臂 × 1 次，共 8 个计划槽**；首次派发前封存本协议、实际源码、配置、输入与槽顺序。真实模型通过官方 API 运行，任务环境仍是每槽隔离的本地 ASGI 仿真。本协议不授权扩量、旧批补跑、第五项 Teacher 生产、训练或远端无人机部署。

## 研究问题与解释边界

观察新版执行契约下真实模型的原工具选择、子程序选择、失败后决策和预算结束行为。特别检查：一次起飞实际成功但回执丢失后，模型下一次实际收到什么公开信息、是否核查状态、是否重放已应用起飞、是否完成剩余悬停义务。

| 臂 | 配置 | 可归因范围 |
| --- | --- | --- |
| B / baseline | — | 同期原工具系统 |
| I / integrated | `evidence_harness + evidence_r2 + observed_target_v7 + task_execution_state_v1`，`applicability`，静态障碍合同 | 状态、工具、子程序和反馈的整合包 |

不叠加 `execution_memory_v7`。I 的工具 schema、结构化状态和提示与 B 不同，显式进度题还可能增加公开进度工具。因此本批不是单独的调度、状态表示或共享消融；当前 `applicability` 与同守卫子程序持续执行的关系沿用[方法定义](../../HARNESS_METHOD_CN.md)。原工具成功同样计任务成功，不强迫模型提交子程序，不预置计划。

## 固定条件、来源与顺序

任务来自已物化的 [18 题已知开发集](../../../experiments/task-coverage-v1-study/manifest.json)，manifest SHA-256 为 `6386876e9e6172655abb899c836b4199909e438846b83be515a5f75aadb48bae`。保留原任务文字、完整 snapshot、timeline 和原 checker；不按历史成功率或低成本替换任务。该旧池全部为 development。本批是 **3 个任务、3 个场景、4 个条件**；第 4 条复用短链任务，不能当作第 4 个独立任务或场景。

| 条件 | task / scene | 固定原指令摘要与边界 | 槽顺序 |
| --- | --- | --- | --- |
| `short_chain` | `c74d7020` / `fb8971ec` | Drone 4 起飞到 12m 并悬停；完整短链语法。原 2 叶 checker 检查高度/离地，未独立检查悬停 | B → I |
| `navigation` | `6c3f98ce` / `5aabad33` | Drone 3 起飞到 14m，访问 `(454,380,14)` 并悬停；坐标访问超出短链完整语法。原 1 叶检查访问位置，未完整检查起飞/悬停顺序 | I → B |
| `search` | `fdd21809` / `be5d648b` | 起飞必要 UAV，分配至目标直至公开会话进度严格超过 95%；完整搜索语法。原 1 叶进度 checker 保持原样，额外审计严格 `>95%` 与主体/动作证据 | B → I |
| `unknown_takeoff` | `c74d7020` / `fb8971ec` | 同短链原文与初态；只增加下述一次回执丢失探针 | I → B |

每槽新 worker、独立世界/黑板；同条件两臂恢复相同初态、使用相同模型参数、权限、预算和 scorer。单次重复只供描述性行为观察；不计算稳定改善或显著性，不合并成独立泛化成绩。正常与故障短链条件分别报告。

## 回执丢失探针

仅 `unknown_takeoff` 使用受控扰动：对冻结任务主体的 `POST /drones/{actor_id}/command/take_off`，在本槽**第一次实际返回成功的提交**之后，丢失该次模型侧/子程序侧回执并抛出中性超时文本 `Request timed out before response was received`。它按公开动作语义和固定主体匹配，原工具及子程序路径使用同一规则；失败起飞不消耗成功触发计数。每槽最多触发一次，不注入正确恢复动作、计划、私有位置或“动作已成功”的提示。

实际已提交的 API 回执、动作/位置事件与探针记录供私有评估关联；策略只通过原工具错误、子程序公开退出、实际后续查询及其可见状态获取信息。策略 wire 不得出现 `synthetic_fault`、`after_public_api_return` 或 `Local controlled response loss` 等注入身份字段/文本。未尝试正确主体起飞或未成功则记“未暴露”，保留槽位；不补注入、不补跑。

这是**受控故障条件下的真实模型后续行为**，不称环境自然发生故障。只读恢复建议不构成 raw 路径的强制防重放器；模型可能重试、忽略或提前结束，均保留。技能内部查询后退出与模型收到退出后另发原工具，分别统计。

## 预算、身份与停批

| 项目 | 固定值 |
| --- | --- |
| 官方 endpoint / 模型候选 | `https://api.deepseek.com` / `deepseek-flash`；官方文档当前标识 `deepseek-flash`，实际响应 model/fingerprint/request ID 单独记录，滚动别名不等于固定权重 |
| 请求参数 | `thinking={type:enabled}`、`reasoning_effort=high`；SDK 重试 0；仅 chat completion，不使用另收费的 search/fetch |
| 每槽时限 / 请求 / 排空 / watchdog | 500s / 60s / 5s / 515s；真实单调时钟 |
| 每槽请求 / usage / 输出 | 最多 12 HTTP attempts；累计 200,000 usage token 在完整响应后停止后续准入；每请求最多 8,192 输出 token |
| 请求正文大小 | 200,000 bytes；字节限制不是 token 估计 |
| 物理执行预算 | 600 iteration/工具步设置、1,800 recursion；每计划 2,000 API、16 repairs、16 search rounds，保留原实现作用域，不冒充 raw 路径总 API 上限 |
| — | USD；单槽 0.50，整批 2.00；peak/cache-miss 保守结算，不预支缓存/离峰折扣 |
| 预留上界 | — |

费率与模型资料已按 2026-11-03 [官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)核对，推理参数按[官方 thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)核对。原始响应 usage 含思考成本时按供应商实际字段结算，不把可见最终文本长度当输出 token。分析报告不复制大段 reasoning。

每槽保存派发前预留、已知 usage 结算或未知 usage 锁额及批次状态。不自动重试当前槽。具体 termination reason/decision 逐槽归档，不把所有失败统一当成正常可继续。

停批保留全部 8 槽；未运行不记为任务失败，未评分不猜 checker 结果，半对不算完整对照。不能为补配对、凑恢复机会或花完剩余额度擅自增加请求/槽位。历史 11-02 已停批及其额度、分母不改。

## 预先固定的分析

分别报告 `run / public_evidence / independent_evaluation`：正常返回、子程序终态和模型声明不能代替任务成功。原 checker 结果与额外公开义务核验并列；短链必须关联正确 UAV 的实际起飞/高度和后续 hover 事件，导航还核位置访问及悬停顺序，搜索严格阈值缺证据时标 unknown。额外审计不修改原 checker。

| 项目 | 证据与口径 |
| --- | --- |
| 模型决策 | 原始响应 assistant `tool_calls`/最终 content，原 call ID、logical/attempt ID；由 tool span 关联执行，不把内部技能动作冒作模型决策 |
| 机会与恢复 | 先有实际丢失回执/公开 unknown，再有后续实际模型请求及其反馈、随后模型行动；记录查询、raw 接续、重放尝试、已应用效果次数和剩余义务完成 |
| 程序控制 | `start/resume/cancel`、拒绝、frame/终态；真实 ready/paused 且可恢复的程序机会单列，不把 raw 恢复算 resume，不把自动局部处理算模型恢复 |
| 成本与错误 | — |
| 完整性 | — |

机会为 0 时计数为 0、比率为 null/不可估计；未运行行显示未运行，不能伪装成零事件。下一请求存在仅证明模型有后续输入，不能证明理解、因果归因或成功恢复。同一 assistant 回复已并行选定的起飞/悬停不算看到错误后的新决策；必须关联后续实际 wire 请求、其中的错误回执、新 assistant 决策及对应执行。若该时点已无剩余悬停义务，则不建立剩余义务恢复分母。技能内部状态查询单列程序观察。实际到达/起飞事件与公开回执共同核验；未再次产生副作用与“没有任何重放尝试”分别列出。悬停审计只证明公开 hover 动作/实际事件及顺序，不额外保证未量化的 `briefly` 物理持续时间。

输出保留 8 个计划槽、启动/退出/评分/失败/异常/未运行各分母，按四条件配对；报告只针对这三道旧开发任务和本次固定扰动。全量源证据封存后，由只读 `scripts/analyze_deepseek_contract_pilot.py` 输出独立目录分析；后续 Teacher 准入和训练仍需另行授权及冻结。
