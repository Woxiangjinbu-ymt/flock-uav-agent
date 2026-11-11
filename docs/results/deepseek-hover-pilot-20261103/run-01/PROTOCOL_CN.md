# v4 悬停与输入修复：DeepSeek 同期对照冻结协议

2026-11-03。用户明确授权修复后 v4 的小规模 DeepSeek 同期验证，统一完成标准，检查悬停、input_json、恢复与真实 token，暂不进入第五项。本协议在任何真实派发前固定；不沿用旧队列或额度，不覆盖旧结果，不通过改标准追改历史4/5。

## 固定范围、版本与预算

8 槽、4 条件、每条件两臂各一次；单一已知开发场景 fb8971ec/c74d7020。顺序如下，模型自主选择原工具或子程序，不预提交plan、不强制object、不脚本改写真实参数、不强制后续hover：

| 条件 | 公开指令/故障 | 顺序 |
| --- | --- | --- |
| state_hover | 原起飞到12米并原地悬停，无持续时间 | control → candidate |
| explicit_hover | 同初态派生公开指令：起飞到12米，然后明确执行hover命令 | candidate → control |
| unknown_takeoff | 原状态目标；同主体首次成功起飞提交后丢失回执一次 | control → candidate |
| duration_hover | 同初态派生公开指令：起飞到12米并原地悬停5秒 | candidate → control |

两臂均 evidence_harness/evidence_r2、task_execution_state_v1、compact_tools_v1、search_confirm_v9、applicability、静态障碍、相同权限和预算。control 为修复前 v3 选择器 off/string_v1；candidate 为修复后 v4 hover_completion_v1/json_object_or_string_v1。新限定线上 variant 显式接线，不将 local-only AgentProfile 升格成通用在线授权。当前生产修复源码共同冻结，旧臂关闭新功能；应用默认不变。

两臂完整公开任务相同，派生仅修改新输入的command/hash和来源元数据，原场景/初态/task/checker不改。新训练/测试集不使用。完成提示与schema/适配是被评价的修复包，不是两个独立机制消融；共同评分不把候选在线gate直接当真值，也不额外给旧臂补查询。

官方 deepseek-flash，thinking enabled / reasoning_effort high，同步非流式，SDK retries=0。2026-11-03核对[官方定价](https://api-docs.deepseek.com/quick_start/pricing/)与[thinking参数](https://api-docs.deepseek.com/guides/thinking_mode/)：页面标注 deepseek-flash；实际响应model/fingerprint另记录，不以滚动别名证明固定内部权重。

每槽500秒、最多12 HTTP、200,000 usage token响应后停线、每次8192最大输出、单请求60秒、watchdog515秒；实现max_iterations600/recursion1800、每plan2000API/16repair/16search不改。每次调用预留完整上下文上界并持久结算；未结算usage/锁额、未排空、异常停批；已知正常HTTP/token预算终止在确认退出/结算后可继续预定剩余槽。无自动重试、补槽、补额度或扩量；时限结束按既有保守终止契约处理。

## 共同完成标准：三层分开

1. run：正常返回、预算结束、错误、未运行分别记录，不作完成标签。
2. common_public_assessment：对两臂同一冻结纯函数 assess_hover_completion(command,本回合task-state) 离线执行，不增加动作/API。state目标须正确主体、匹配起飞尝试、起飞前XY公开锚点、之后新鲜公开status确认空中hovering/12m/原XY/speed如有为0，位置/速度容差1e-6；显式hover另需成功回执及正确顺序。unknown历史不改。缺字段/错主体/并行顺序不明/过期状态保守未验证。candidate在线assessment单列并核对一致性，control没有在线gate。
3. unified_completion：隔离scorer从退出后同一封存snapshot读取终态，并与当回合真实提交API/物理history交叉核验。状态目标必须有真实takeoff效果、最终空中hovering/高度12/初始XY/零速度、无后续旅行；明确动作额外需takeoff之后成功hover实际事件。私有真值不进入策略/下一请求。原c74高度/离地checker仍保留作auxiliary。

duration当前仅参数回显/历史事件，没有持续保持证据，两臂统一 task_completed=null、不可确证。它是诊断题，不并入支持任务完成率分母；检查原模型最终文字、在线gate展示是否如实未验证，不能把不执行/提前报告限制造成的省token称执行效率提升。

可能存在独立真值true而公开未验证，表示证据缺口；不把null改false。支持任务主分母每臂3槽，故障机会每臂最多1次。初态、条件、工具权限、模型、轮次预算、退出评分相同。

## 预注册观测与分析

- 悬停：任务真值、共同公开判定、模型原始结束文本与gate后的展示、实际takeoff/hover/状态查询及顺序；状态任务的额外hover单列，明确动作不得省略。
- input_json：从每次真实响应原始function.arguments统计object/string/array/scalar/缺失/非法外层等，按真实参数工具调用机会计数，不重复数后续请求历史；无参工具不进分母。关联原call ID、ToolMessage与API，拆表示错误/工具字段语义错误/环境执行失败/unknown传输和不能确定的错误。修正由真实后续模型决定；零错误只代表本批未观察到。
- 恢复：成功后的丢回执真实触发、unknown反馈进入下一模型请求、新回复发起正确主体状态查询、共同公开目标验证、历史unknown保留、无重复起飞。程序查询、同一旧回复预选动作与见错后新模型决策分别统计；状态目标不要求补hover。未触发/无后续机会均不补跑。
按配对、共同成功子集另列，所有失败成本保留。不能跨批挑最好值。
- 归因：同一已知场景的一次开发pilot，只报告描述性差异，不做稳定性/泛化或显著性主张。两项修复一起变化，不能拆出各自因果贡献；本轮不重新验证搜索v9收益。

## 运行与证据保留

先通过配置拒绝/真实SDK对象schema与历史/独立评分正反例/旧隔离恢复预算回归，再跑全8槽local-smoke；smoke模型回复和usage均脚本合成、网络阻断。实际批次重新冻结源码/全部输入/协议和同一8槽，然后只派发一次。每槽新worker、退出后新独立scorer，原始request/response/工具消息与call ID不改，私有canary及检查定义不得进入策略。

所有失败、停批、未启动、partial归档及预算结算完整保留。运行中不改冻结实现、提示或评分；发现缺陷先保留并结束该批，修正后仅可在已有明确授权范围内评估是否需另行批次，不能静默补跑。本次授权只覆盖这8个真实回合；本地修复可继续，无真实扩量。Teacher、SFT、RL与第五项均不启动。
