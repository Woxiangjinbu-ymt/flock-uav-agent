# Harness 回合复用接口与自然 Agent 验证协议

日期：2026-10-25。当前阶段为**本地准备，不具备在线启动条件**。本协议承接[完整回合验收](results/harness-full-episode-20261025/REPORT_CN.md)，落实复用边界与下一批设计；不修改旧批次，不续跑360次，不启动Teacher生产或训练。

## 1. 本次要回答的问题

在当前 `observed_target_v7` 的明确适用范围内，模型从原任务和公开状态出发，是否会自主选择技能；技能真正暂停时能否继续正确计划；技能终止或不适用时能否使用原工具推进剩余义务，而不重放已经生效的动作？

以同一模型、Harness版本、工具和任务比较 `applicability` 与 `failure` 两种调度，只改变调度器。先记录机制是否实际被触发，再描述整题结果与成本；不预设一定出现暂停、收益或显著差异。该对照研究调度差异，**不直接回答“新Harness整体优于无Harness”或“v7优于legacy”**。

“模型负责选择/恢复/放弃技能及接手剩余工作”是本轮观察粒度及一个潜在可训练职责，不代表已冻结约2B学生的最终职责或论文创新点。确定性skill内部的API不是模型逐步决策标签，不能把每个物理动作自动转为SFT示范。

## 2. 现有证据能复用到哪里

| 依据 | 可复用内容 | 不能据此认定 |
| --- | --- | --- |
| [9回合完整链](results/harness-full-episode-20261025/REPORT_CN.md) | 新进程、完整初态、实际SDK字节归档、四种终态、真实退出后独立评分、封存；原预算明确 | 单任务脚本模型和虚拟时钟不是自然provider或真实墙钟运行验收 |
| [旧40次自然入口校准](results/minimal-harness-full100-calibration-20261016/FINAL_ANALYSIS_CN.md) | 有真实模型自主选技能和原工具接手轨迹，暴露入口及终止语义问题 | 只有6/40次技能提交；真实可恢复paused机会0/40。终止态上的resume不算恢复，不能代替当前v7验证 |
| [旧v2机制实验](results/minimal-harness-v2-mechanism-20261016/FINAL_ANALYSIS_CN.md) | 已选定技能后的机制对照及回执/游标审计方法 | 初始技能由夹具预提交；故障注入含临时增删障碍，不能直接接到v7静态障碍契约下 |
| [v7原7题离线配对](results/harness-observed-target-20261024/REPORT_CN.md) | 原fixture、公开失败与效果证据；不必改变地图即可审查候选失败检查点 | 显式脚本start不能证明模型会自主选技能，也不能预报自然运行中的机会数 |

只读的[候选检查点证据](results/harness-natural-agent-preparation-20261025/CHECKPOINT_EVIDENCE_CN.md)把既有公开失败与技能检查点连接起来；它是选题依据，不增加回合或恢复成绩。**不使用旧动态障碍Faults来“制造足够机会”**。本轮按真实轨迹统计；机会为零时写“不可估计”，不追加样本补足分母。

## 3. 已验收路径的最小复用约定

| 边界 | 必须保留的内容 | 可复用实现/需要避免的混用 |
| --- | --- | --- |
| 准备 | 原输入、源码、协议、任务序列hash；加载pickle前验证；原fixture只读 | [FullWorld](../scripts/harness_full100_world.py)及冻结输入；准备性读取只进私有诊断，不伪称模型观测 |
| 回合开始 | 每trial全新解释器/World/Agent；恢复原快照与timeline；新黑板、账本、runtime、归档；计时一次 | 清理记忆不是恢复世界。不得用同ID会话或同进程对象复用替代新进程隔离 |
| 自然入口 | 原任务文字、真实公开观测、相同系统提示和工具；初始无计划 | [full100的agent_class](../scripts/harness_full100_online.py)有自然入口基础；禁止v2的bootstrap、public_seed黑板预填、按case暗示skill、checkpoint/canary/初态私有字段入提示 |
| 行动与记录 | model logical/HTTP attempt/tool call ID、实际request/response bytes、工具回执、skill或model_tool来源、物理提交证据 | 默认关闭的归档只在本批显式开启；保留complete/partial和错误，不把missing正文写成空白成功 |
| 截止与取消 | 500秒动作和评分准入窗；截止快照不可变；取消flag后仍等执行与记录收尾；关闭后不得新派发动作 | — |
| 评分与下一回合 | worker真实退出→父进程封存→独立scorer→封存评分→下一worker | 独立scorer不导入Agent，私有树/admin日志只入evaluation；任务通过、Agent自报、技能状态分别保存 |

新在线runner仍需实现并通过本地验证，**不能把上一批脚本模型换个名称就直接上线**：

- `harness_episode_local.py` 固定MockTransport、虚拟时钟、`timer=False`和按请求序号返回技能；上一批父协调器的60秒watchdog、9回合及1/3动作断言只适用于本地验收。
- 旧 `harness_full100_online.run_trial` 仍在执行进程里评分，未直接满足当前完整正文与独立评分边界；不得原样续跑。
- 正式provider路径需真实单调时钟、真实500秒截止，并落实调用前硬准入、单请求超时、有界排空、父进程watchdog和累计成本停线。模型观测hook中的异常是fail-open，**不能把预算拦截塞进审计hook并以为调用会被阻止**。
- 2,000 API是**每技能计划**上限，不是整个episode的raw工具全局上限。此次不暗改已有范围；

## 4. 预定任务与两臂

全部沿用 `experiments/minimal-harness-full100-v1/fixtures` 中已经冻结的原任务、初态与timeline。7题属于7个问题组、4个场景，均是反复分析过的开发实例，不称留出测试或泛化样本，也不按7个独立场景统计；不选取其中最佳结果拼接新成绩。

| 固定顺序 | task ID | 评估侧分组 | 关注点 | 预定先后 |
| --- | --- | --- | --- | --- |
| 1 | 7eba7390 | public_search | 自主进入、部分完成及失败后接手 | A→F |
| 2 | 24ac1838 | public_search | 障碍修复、失败检查点与接手 | F→A |
| 3 | b1ad4f3e | public_search | 共享证据、完整停止与冗余行动 | A→F |
| 4 | b1eb0270 | simple_chain | 自主选择短链、完成后不重放 | F→A |
| 5 | 898cfdca | simple_chain | 不同短链、游标与实际效果 | A→F |
| 6 | 784d71f8 | unsupported_full_grammar | 完整任务超出skill语法时的原工具执行 | F→A |
| 7 | 1905e0ad | unsupported_full_grammar | 不漏额外义务，不把局部skill完成当整题完成 | A→F |

A=`applicability`，F=`failure`。拟定7对、14个slot、每臂每题1次、单worker串行。先后为A先4对/F先3对，**不宣称完全平衡**，也不为凑平衡额外增加重复。该序列属于拟议设计，当前已授权在线执行数仍为0；不从旧360次队列提取“剩余授权”。

两臂共同：`evidence_harness`、`observed_target_v7`、静态障碍契约为true，不混入execution-memory v7；原raw工具仍可用。模型候选沿用旧研究中的`deepseek-flash`、temperature=0.1作为可比性依据，但实际endpoint、模型标识/版本、可用性、上下文与输出限额尚需核对。模型seed支持性不得猜测：不支持时记null并保留采样变异限制。不得逐题改变提示、技能描述或输出限额。

static=true必须对应障碍writer确实隔离：本地独占World、无共享服务写入、无动态障碍注入或工具写入口；只设置环境变量不是证明。模型的真实首个HTTP正文与工具schema应在同对中按明确身份字段映射后核验；后续轨迹允许因调度与选择不同而分叉，不要求逐字相同。


原执行约束保持：500秒任务窗、每计划2,000 API、16次repair、16轮search、600工具步、1,800递归、SDK零重试。未启动执行之前就冻结，不因失败补时或补预算。14个任务窗的串行总上界为116分40秒，仅是500×14，不含启动、排空、评分，也不是耗时预测。

未填不表示无限，不能自动用旧预算填充。

旧校准中这7题里已有自然轨迹的5对合计约1,801万token；另两题没有该批自然成本。新方案需要在每对开始前核对剩余额度与冻结的预留规则；SDK超时后的缺失usage要标未知并停后续派发，不能当零成本。

未来允许启动后的停批规则：

1. 输入/源码/hash、权限、初态或首请求可比性不成立，停止并保留材料，不进入收益比较。
2. 隐藏评分泄漏、worker未排空、归档闭合/关联损坏、评分器异常或缺失usage，停止后续派发并报告基础设施问题。
3. 合法任务失败、任务500秒截止、技能不适用属于应保留的任务结果；符合冻结规则时继续下一slot，不能只保留通过样本。若实际完整正文缺失导致有效性不足，整条记录保留并按上述质量规则停批，而非删除失败。
4. 停在半对时保留已开始回合的结果/成本，未运行slot明确列出；只在完整配对上作配对差值，不将未运行题冒充自然执行失败或擅自补跑。
5. 机会不足时不扩大队列。完成拟定14slot也只做描述性开发分析，不许自动进入full100、数据生产或训练。

## 6. 机制与结果的审计定义

| 指标 | 必须具备的证据 | 不计入的情况 |
| --- | --- | --- |
| 自主技能选择 | 实际模型输出的start调用与原call ID；真实plan_commit/skill_enter；无bootstrap或私有预填 | 计划目录中的评估标签、脚本选skill、入口拒绝、只有文本说“使用技能” |
| 可恢复机会 | 实际返回ready/paused且resumable=true；同scene/epoch、仍有效计划及公开剩余工作 | terminal needs_replan/completed、抛错后无有效计划、仅按旧轨迹推测会暂停 |
| 恢复尝试 | 此机会之后模型调用同一有效plan_id的resume | 重复start、错误plan_id、终止态resume |
| 有效恢复 | 确实重新skill_enter，游标/证据继续；已提交动作未重放；后续公开效果可连接 | API接受了resume但未执行、原cursor不变的空恢复、只据模型自述 |
| 原工具接手尝试 | terminal/unsupported、不能安全resume，或模型显式放弃/取消有效暂停计划后，实际发起与原任务义务相关的raw工具；按触发原因分列 | 随便调用只读状态不等于义务已推进；只说“我来接手”；有效paused转raw不混算同plan恢复 |
| 接手后的任务完成 | 公开行动/历史与剩余义务可连接，独立原checker通过 | 只有一个叶子通过、progress升高、skill completed或TASK DONE |
| 重放/错误恢复 | 已生效动作被重复发起、终止计划restart/resume、重复失败等逐条证据 | 不把合法的新高度/新目的点调整统称重复 |

恢复及接手机会必须分别报告轨迹数、事件数、独立task数与scene/problem_cluster；同一轨迹多次暂停不能当多个独立任务。A/F机会数可能由策略本身改变，条件恢复率只作机制描述，不能替代所有预定任务的端到端比较。模型未选skill但用raw工具完成任务仍记任务成功；unsupported任务未选或被拒绝也不自动代表完成。若两臂同题都选择相同技能且公共前缀一致，再单列可比较机制段；不得删除没有选技能的回合来制造总体收益。

主要结果为原任务端到端checker；另列Agent自报、技能终态、有效工具调用、总usage及cache组成、实际HTTP/模型调用、墙钟/排空、API/动作、提交/未提交物理效果。自然provider请求失败、Agent执行失败、任务未完成与评分基础设施失败分列。

## 7. 准备包与后续进入条件

[准备器](../scripts/prepare_harness_natural_agent.py)只提供 `prepare` / `verify`，不含run/launch入口，不导入Agent/SDK/模拟器，不连接网络、不反序列化pickle。它检查原410文件、7题引用、已验收回合的302文件封存与80份源码，并将本协议、公开任务目录、评估注释、拟议顺序和引用hash写入独立计划目录。公开目录只保留task_id、scene_id、command及其hash四字段；problem_cluster、开发集标签、适用性和旧脚本所选skill均留评估侧，不交给模型。

`experiments/harness-natural-agent-plan-v1`是本地准备快照，`authorized_executions=0`、`authorized_trial_ids=[]`、`online_ready=false`。完整性校验通过仅说明材料一致，不证明provider运行器已实现。重新修改授权或任务后即使重算文件hash，也不应通过该固定准备器的语义校验；新执行协议必须另行版本化。

接下来按顺序完成：

1. 实现真实墙钟的自然入口worker、独立scorer及硬准入，先用MockTransport在同边界上做本地测试；不把失败回合阈值硬编码成成功率指标。
2. 明确具体模型与新增批次资源限制，核验请求超时、排空、usage和停批，形成可启动的冻结执行版本；当前不发provider请求。
3. 获得自然选择/恢复/接手的真实证据后再决定是否收束该问题。Teacher数据生产前另定学生职责及场景级划分，SFT/RL仍保持规划状态。
