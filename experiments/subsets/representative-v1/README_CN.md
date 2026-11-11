# Agent4Drone 代表性主数据集 v1

**已冻结，作为后续模块实验与主要消融的默认数据集：15 个完整场景、300 条任务、1733 个原始叶子检查。** 主线是提升完整任务成功率，同时记录时间、token 与验证可靠性。

版本：`agent4drone-representative-v1`；冻结日期：2026-10-05（北京时间）。权威清单是 [main300.json](main300.json)，原始场景包是 [scenes-main300.zip](scenes-main300.zip)。

主清单 SHA-256：`9a284f005fcab6cffa59c2deacd7d43e8c9da0b506b40a11510db6d62894f261`。任何任务、检查、场景或顺序改变都需要新版本；不得根据新方法成绩替换难题。后续补充基线也不改变 v1 样本。

## 使用入口与固定划分

| 集合 | 场景 / 任务 | 用途 | 清单 |
|---|---:|---|---|
| 主数据集 | 15 / 300 | 后续主要模块比较与汇总结果 | [main300.json](main300.json) |
| 开发集 | 12 / 240 | 反复开发、调参与消融 | [dev240.json](dev240.json) |
| 阶段验证集 | 3 / 60 | 阶段性检查回归，不随每次调参反复查看 | [validation60.json](validation60.json) |
| 快速回归集 | 5 / 100 | 在开发集内高频筛选 | [fast100.json](fast100.json) |

100 条包含在 240 条中；240 与 60 条按整个场景分割，无交集，合计 300 条。300 条主集覆盖三个场景族 × 五个场景难度，每个组合各选一个完整场景；每个场景保留全部 20 条任务及原始顺序。各 split 内场景采用原文件名字典序。任务难度 easy/medium/hard 与场景难度不是同一标签。

项目默认入口记录在 [default.json](../default.json)。建议按 100 → 240 → 60 → 少量配置全量 1500 的顺序推进；阶段验证已参与初始选样，不能声称它从未被分析或是独立泛化测试。反复运行整个 main300 就意味着 validation60 也已用于开发，应如实披露。

## 固定场景名单

| 场景 ID | 场景 | 划分 | 100 条 | 首次基线 DS / Qwen | 选入依据 |
|---|---|---|---|---:|---|
| fb8971ec | Area Assignment and Patrol Easy 1 | 开发 | 否 | 19/20 · 18/20 | 最终相对距离与移动距离；双机编队 |
| ad34350a | Area Assignment and Patrol Extreme 5 | 开发 | 是 | 12/20 · 9/20 | 覆盖比例不足、困难巡逻与多条件执行 |
| 76802002 | Area Assignment and Patrol Hard 3 | 阶段验证 | 否 | 16/20 · 15/20 | 局部未发现目标与记忆状态；目标定位 |
| 00c63e73 | Area Assignment and Patrol Intermediate 1 | 开发 | 否 | 19/20 · 17/20 | 中等规模任务及近失败/成功回归对照 |
| 4e425a5b | Area Assignment and Patrol Moderate 1 | 开发 | 否 | 17/20 · 19/20 | 定向航段与净位移；路径动作语义 |
| 9ddae16c | Area Search Easy 3 | 开发 | 否 | 20/20 · 18/20 | 读取状态后提前退出；基础成功回归 |
| dc6b1d11 | Area Search Extreme 3 | 开发 | 否 | 18/20 · 15/20 | 高难区域搜索、覆盖与目标完成 |
| ef36e27a | Area Search Hard 1 | 开发 | 是 | 18/20 · 19/20 | 目标绑定与 coverage plan handle；搜索验证 |
| 0f23ff1e | Area Search Intermediate 1 | 开发 | 否 | 19/20 · 18/20 | 区域搜索中间难度及回归对照 |
| 13b94391 | Area Search Moderate 2 | 阶段验证 | 否 | 17/20 · 19/20 | 导航与搜索的近失败；阶段验证 |
| 3973d0d6 | Target Assignment Easy 5 | 开发 | 是 | 17/20 · 11/20 | 已发现目标集合与全局进度的区别 |
| 823c15d5 | Target Assignment Extreme 5 | 开发 | 是 | 10/20 · 7/20 | 高难多目标分配、零通过及执行收尾 |
| b3c8ded2 | Target Assignment Hard 2 | 阶段验证 | 否 | 17/20 · 14/20 | 困难目标分配及部分失败；阶段验证 |
| c5fb45ca | Target Assignment Intermediate 5 | 开发 | 否 | 16/20 · 14/20 | 目标分配中间难度与两模型差异 |
| 35ce64d8 | Target Assignment Moderate 1 | 开发 | 是 | 13/20 · 13/20 | 长航线完成后漏降落；另标 DeepSeek 服务错误 |

## 覆盖情况与限制

覆盖原基准 50 种实际出现模板中的 **48 种**、全部六个实际出现的生成器类别，以及全量首次基线 19 种实际失败端点中的 **16 种**。类别分布：

| 生成器类别 | 任务数 |
|---|---:|
| Basic Operations | 34 |
| Navigation | 71 |
| Search | 72 |
| Multi-Drone Coordination | 99 |
| Delivery | 16 |
| Communication | 8 |

任务难度为 easy 49、medium 112、hard 139。两模型均正常获得检查且通过的共同成功对照有 **210 条**；共同失败 34 条，仅 DeepSeek 成功 38 条，仅 Qwen 成功 12 条；另有 6 条受服务故障影响，单列而不解释为模型能力失败。

按“任一模型出现”统计，包含 **33 条部分完成但仅差一个检查**、**17 条多检查任务零通过**、9 条单检查任务 0/1。上述分组可能因两个模型结果不同而重叠，不能相加作为互斥总数。零通过不等于未执行动作。

12 条任务包含几何冲突检查候选；标记针对叶子条件，不把 OR 分支冲突自动当作整题无解。原始分母、指令与检查全部保留。未纳入的两种模板为 `Cross Target Swap After Discovery`、`Two Find Two Support`。首轮中少见的配送失败、起飞/返航/降落历史端点失败，以及指令起飞高度与检查阈值冲突案例，未被本版全部覆盖；`drone_on_ground` 收尾遗漏和照片、进度、目标定位等主流失败已有覆盖。缺口见 [coverage.json](coverage.json)，不宣称覆盖所有失败机制。

此集合是面向研究目标的分层开发集；选样参考了既有结果，300 条分数不能作为全量准确率的无偏估计。代表性指任务结构与主要失败类型，不等于与全量保持相同失败比例。最终结果仍需全量验证，并披露其中包含开发题。

## 基线口径

本版采用已收束的 [两模型首次部署报告](../../../docs/results/baseline-deployment-20261005/REPORT_CN.md)，统一使用 `deployment-no-scene-replay-v1 / first_attempt_no_replay`。这替代了早先“必须等 Qwen 原 runner 有效跑完”的构建等待条件：当前两轮已完成 1500 条计分归属，Qwen 后续补跑已停止，`valid_complete_run` 仍为 false。旧设计保留在 [selection-spec.proposed.json](selection-spec.proposed.json)，修订依据在 [selection-spec.json](selection-spec.json)。

选择时，在每个场景族/难度组合内优先保留首次执行未评分数最少的候选。最终 **两模型各 300 条均有实际原检查记录**，没有未执行任务的政策补零；DeepSeek 仍有 6 条终止性服务错误，按已收束口径记失败，保留其原检查值。Qwen 有 1 条已评分但 Worker 未确认结束的失败任务。首次结果不混入后续恢复代次，也不采用旧快照的 85.53% 等恢复分数作为本集基线。

| 集合 | DeepSeek 首次任务通过 | Qwen 首次任务通过 |
|---|---:|---:|
| main300 | 248/300 = 82.67% | 226/300 = 75.33% |
| dev240 | 198/240 = 82.50% | 178/240 = 74.17% |
| validation60 | 50/60 = 83.33% | 48/60 = 80.00% |
| fast100 | 70/100 = 70.00% | 59/100 = 59.00% |

主集 DeepSeek 的 52 条失败包括 16 条近失败、18 条其他部分失败、5 条多检查零通过、7 条单检查零通过和 6 条服务错误；Qwen 的 74 条失败包括 25 条近失败、27 条其他部分失败、14 条多检查零通过和 8 条单检查零通过。两模型超时分别为 8/300 和 52/300。

这些是历史配对子集基线，不是新方法改进的因果证明。后续新实验应预先固定服务故障与执行停止的处理规则，建立一次相同配置的当前 B0/B1 对照后复用；尤其不能把服务恢复带来的分数变化全部归功于 planner 或 memory。未授权启动新模型实验，本次仅完成离线构建与核验。

## 成本与计时

| 集合 | DS / Qwen 任务评分就绪时间累计 | DS / Qwen 已观察累计 token |
|---|---:|---:|
| main300 | 9.05 / 18.21 小时 | 259.66 / 287.84 百万 |
| dev240 | 7.07 / 14.50 小时 | 203.19 / 228.64 百万 |
| validation60 | 1.98 / 3.71 小时 | 56.47 / 59.20 百万 |
| fast100 | 4.14 / 7.93 小时 | 116.12 / 124.26 百万 |

时间累计为各题 `barrier.frozen_at − window.opened_at` 的和，包含轮询与排空；不是 8 workers 实际墙钟，也不含任务间准备、完整清理或真实无人机飞行。不能直接除以并发数承诺完成时间。100 条快速集偏重困难任务，任务数虽只有 300 条的三分之一，成本并非同比下降。

token 来自已观察 usage，包含反复传入和缓存命中的输入，不等于付费金额。DeepSeek 296 条有 usage，另 4 条缺失；总数是可核实开销，不补造缺失成本。选择算法对缺失/服务错误行使用分层中位数仅作候选成本估计，不计入上述实测汇总。

## 复现与冻结规则

选样算法先保证 15 个分层组合及观测完整性，再最大化有上限的失败机制、类别、模板、两模型差异与成功对照覆盖，并轻度约束成本和服务异常占比。采用固定种子 20261005、32 个起点及逐格替换搜索；这是可复现启发式，未声称全局最优。全部 75 个候选的得分与特征、权重、最终分组见 [selection-audit.json](selection-audit.json)。早期人工深读案例只有在轨迹代次与首次基线一致时才参与机制标签。

已离线重放原 checker 核验 **600 条模型任务记录**，检查 AND/OR/反向期望语义；已复核 **2506 个消费证据文件**的归档哈希。15 个场景文件逐字节保留原 benchmark 内容，任务、指令、检查、顺序没有改动。

在仓库根目录验证：

```bash
controller/.venv/bin/python experiments/analysis/build_representative_subset.py experiments/subsets/representative-v1 --verify
```

重新执行选样只允许输出到一个新目录，脚本会拒绝覆盖已冻结的主清单：

```bash
controller/.venv/bin/python experiments/analysis/build_representative_subset.py /tmp/representative-v1-rebuild
```

数据包可供独立实验入口加载；当前冻结 `scripts/reproduction.py` 的内置计划校验不接受任意子集名，本次未绕过或修改该校验。接入实验时读取清单的 `sessions` 和 `scenes/`，保留原整场状态、任务准备及顺序，并在首轮运行前核验评分一致性。隐藏检查、离线失败标签及基线输出不进入 Agent 提示或 memory。

主要文件：[任务清单及标签](tasks.csv)、[场景清单](scenes.csv)、[完整任务审计](tasks.json)、[两模型基线](baseline-summary.json)、[600 条基线记录](baseline-tasks.json)、[校验记录](verification.json)、[数据校验和](DATA_SHA256SUMS.json)。
