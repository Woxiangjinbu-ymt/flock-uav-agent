# Harness 收束接口与下一步优先级审查

日期：2026-10-24。依据当前[研究状态](../../RESEARCH_STATUS_CN.md)、[Roadmap](../../CSCWD2027_INCREMENTAL_RESEARCH_ROADMAP_CN.md)及下列源码，只读核对配置、运行入口和训练复用边界。本次未调用模型、未运行仿真或测试、未修改生产代码及历史实验；这是接口审查，不增加实验样本或通过数。两道剩余搜索题的机制诊断由本目录其他独立材料记录，本文不预判尚未完成的修复。

**审计时点说明：**以下正文保留于 `observed_target_v7` 实施前；其中“候选未定、待诊断”等描述是当时状态。同日后续已完成有界目标跟进及14次配对，最新结果见[目标跟进报告](../harness-observed-target-20261024/REPORT_CN.md)。下一步以研究状态/Roadmap为准，本文保留 reset、轨迹、终止与评分接口的审查依据，不再作为继续同题添加搜索机制的任务单。

## 当前可保留配置与证据

| Harness revision | 相对前版新增的窄能力 | 已验证边界 |
| --- | --- | --- |
| `entry_contract_v2` | 显式 start 后接受成功的空公开观察；明确终态不可 resume/restart | [固定7题×2](../harness-entry-contract-20261023/REPORT_CN.md)，搜索仍0/3；不代表自然模型会选技能 |
| `flight_state_v3` | 依据 fresh status 决定起飞，修正 idle/ready 正高度直接 move | [固定7题×2](../harness-flight-state-20261023/REPORT_CN.md)，一个已知搜索进度5%→33%，搜索仍0/3 |
| `blocked_candidate_v4` | 原公开错误及后读位置确认后，屏蔽本计划同 actor 的精确目的点 | [固定7题×2](../harness-blocked-candidate-20261023/REPORT_CN.md)，重复消除、部分进度提高，搜索仍0/3，成本增加 |
| `shared_endpoint_v5` | 静态障碍写入隔离下跨 actor 共享精确 XYZ 目的点证据 | [固定7题×2](../harness-shared-endpoint-20261023/REPORT_CN.md)，搜索原 checker 0/3→1/3；其余失败保留，成本增加 |
| `terminal_progress_v6` | 仅在无候选退出前作一次有效公开进度确认 | [固定7题×2](../harness-terminal-progress-20261023/REPORT_CN.md)，搜索原 checker 仍1/3；仅已成功的 b1ad 技能终态改为 completed，多1次 API，动作不变 |

上述是对同一已知任务集合逐轮开发的代码路径验证，没有 LLM、自然技能选择或 raw-tool 接手，不能拼成独立样本、Benchmark 总成功率或稳定泛化。v6 的437项不重复代码测试与14次模拟器 trial 是不同分母。旧研究另有自然模型实验，但不能替代当前 v2–v6 组合的自然决策验证；执行记忆 `v7` 也不是 Harness 的下一 revision。

当前入口需要同时核对三层，**没有自动提升默认版本**：

- `UAVControlAgent` 的任务进度模式默认 `off`；只有 `evidence_harness` 或明确的 `evidence_memory*_harness` 才安装 Harness。只设 `UAV_HARNESS_REVISION` 不生效。当前离线配对使用新 R2 账本，不据此保证与 execution-memory v7 组合后的模型行为。
- `HarnessRuntime` / `install_harness` 默认 revision=`legacy`、scheduler=`applicability`、static=False。可复核的集成参考为 `UAV_TASK_PROGRESS_MODE=evidence_harness`、`UAV_HARNESS_REVISION=terminal_progress_v6`；v5/v6 的 public_search 另需精确 `UAV_HARNESS_STATIC_OBSTACLES=1`，否则首 API 前退出。standalone 对应显式 `revision="terminal_progress_v6", static_obstacle_contract=True`。
- 静态标志是调用方提供的契约，不能凭 scene ID 或该开关声称动态地图安全。只有验证障碍 writer 隔离等条件后才可传 True；公开变化守卫不能发现不可见地图更新。旧 `harness_v2_online.py` 显式设置 evidence_harness，但没有显式固定新 revision/static；其旧配置、继承环境及受控 bootstrap 不能直接充当当前 v6 自然决策协议，也不应续跑旧360队列。

入口依据：[uav_agent.py](../../../agent4drone/uav_agent.py)、[harness_integration.py](../../../agent4drone/harness_integration.py)、[harness_core.py](../../../agent4drone/harness_core.py)、[harness_skills.py](../../../agent4drone/harness_skills.py)、[旧在线 runner](../../../scripts/harness_v2_online.py)。本段是后续复用时的显式配置参考，不是本次启用或修改默认。

## 训练复用接口的近期缺口

| 接口 | 现成能力 | 尚需验收的最小约定 |
| --- | --- | --- |
| episode / reset | FullWorld 支持完整 snapshot/restore、单次 start 与评分截止；Agent 有 `reset_task_evidence`，可检测部分公开 reset 标记 | 世界恢复、黑板/账本 epoch、runtime、计时器和日志必须由同一 episode 边界协调。`reset_task_evidence` 仅清记忆，不重置世界；同 ID 且标记未变的 reload 仍需显式清理。已验证新进程隔离不能自动证明进程池复用安全 |
| 工具与轨迹 | 可选 toolchain JSON 包含任务、工具调用/公开回执、账本、Harness、usage；公开 API 与物理事件可区分来源。OpenAI-compatible 的 wire hook 记录实际序列化请求 hash，并连接模型请求/工具 ID | 需验收每个模型决策点可重建其完整可见输入、最终 system/tools schema、输出及 call/receipt 映射。wire 与 middleware 的 hash 本身不能还原正文；toolchain 默认关闭，hook setup 失败会记录错误而继续，不应以“有日志”视作全 provider 采集完整。Ollama 分支不经过该 wire hook |
| 终止与恢复 | v2+ 非可恢复终态明确拒绝重放；v6 对一个特定停止分支确认公开进度；runtime 有 deadline/cancel/scene-epoch 守卫 | 保留原始状态与 reason，区分技能 completed、任务结束、超时/取消、unsupported、不可确认效果和独立整题成功。当前 snapshot 记录的是 `in_process_resume_only`，没有据此验收跨进程恢复；不必为训练先建设通用持久化 plan 引擎 |
| 评估与奖励 | FullWorld 截止快照、committed 标志与独立原 checker；策略不可读隐藏检查。真实公开进度与原 checker 已并列归档 | 先固定任务级标签及 cutoff，随后再设计奖励，不直接把公开 progress、技能 completed 或 checker 子项比例当通用 RL reward。跨截止动作回滚是本机原子仿真契约，不等于远程 HTTP/实机可撤销；wall-time 推进环境使同初态能耗并非逐字可复现 |

依据：[FullWorld](../../../scripts/harness_full100_world.py)、[工具链记录](../../../agent4drone/toolchain_recorder.py)、[请求审计](../../../agent4drone/model_request_audit.py)、[证据中间件](../../../agent4drone/task_evidence_context.py)及上述 Agent/runtime。这里指出的是所审接口尚未完成统一复用验收，不能据此声称整个仓库不存在其他辅助脚本。

训练样本还需明确**决策归属**：本批脚本选择 start，之后大量 API 是确定性 skill 内部动作；它们不是强模型逐步作出的 Teacher 决策。必须先选定学生学习“选择/恢复/接手技能”还是更细工具决策，并记录当时实际可见信息，再决定样本粒度。公开物理动作日志可用于效果验证，不能无条件全部转成模型 assistant tool-call 标签；私有完整初态、checker 和审计几何只留在隔离评估侧。

## 按依赖排列的三项工作

1. **先结束剩余两题的公开失败诊断。** 以 `terminal_progress_v6` 为显式参考，判断覆盖不足与修复预算耗尽是否存在一个公开证据充分、潜在任务收益明确且成本可控的缺口。只选择一个修复分支；无这样的依据便保留失败边界，不加 rounds/repair/API、不扩大完整语法、不因两个开发实例转向通用规划。
2. **随后验收一条完整 episode/trajectory/evaluation 链。** 复用现有 reset、日志、cutoff 和 checker，先列出一个样本从初态恢复到模型可见输入、动作来源、终止及独立评分的必要字段与缺失项，再补最小缺口。优先保证可重建和隔离，暂不建设通用 RL 环境框架、跨进程恢复或大批数据生产。
3. **最后准备小规模自然 Agent 对照协议。** 明确学生/Teacher 决策职责和适用任务，固定配置、预算、模型、种子及评分，检查模型是否自然选择技能、正确接手剩余工作，以及真实 token/工具成本。已有开发题仍标为开发；独立划分先于 Teacher 采集。不把本次建议视为已经授权/启动新模型批次，更不直接开始 SFT、RL 或旧360次实验。

## 7eba 公开固定目标跟进：仅候选审计建议

若两题诊断最终支持该方向，最小下一步可只冻结“哪些公开 fixed target 有资格被考虑”的证据表，不急于改算法或重跑。每项保留原任务依据、同 scene/epoch 的 nearby 回执与 target ID、固定位置、既有正向到访回执及未知项；不能补出未观察的第三目标，也不借私有初态确定候选位置。

`is_reached=False` 不得直接解释为“剩余任务”：状态应写成**已观察、尚无本任务可绑定的正向到访证据**，其中“无证据”不等于“已证未完成”。正向 reached 回执应保留，不能被之后的 false 字段覆盖；只有目标名称而不能与公开 ID 唯一绑定时继续标未知。公开进度可判断整体是否达到停止条件，不能替代单目标身份和历史事件证据。

若后续确认值得实施，应作为独立 opt-in 的**候选调度变化**，在原 API、轮次、修复和时间预算内有界跟进，实际动作数量与成本单列；不同时扩大候选区域、增加高度搜索或绕障恢复。公开几何最多支持候选检查，不能保证真实可达；新失败沿原契约退出并记账。模型未参与的目标选择与执行仍属于确定性 Harness 行为，不能包装成 Teacher 轨迹或模型学习收益。本段仅给审查边界，不表示本轮已选择、实现或验证该候选。

本次审查支持继续按证据收束 Harness；不把接口审查写成训练环境已验收，也不从局部功能通过推出论文创新或模型能力提升。
