# Harness 飞行状态 v3：离线配对结果

日期：2026-10-23。固定批次：[`run-20261023-01`](run-20261023-01/summary.json)。按[执行前协议](PROTOCOL_CN.md)一次完成 7 题 × 2 revision，共 14 次；没有补样或重跑。

**最小修复消除了已观察的 `idle + z>0` 非法移动，受影响任务进度从 5% 提高到 33%，但仍未完成整题。** 三个搜索题在两臂均为原 checker 0/3 通过。两项简单链正对照维持 2/2，通过原任务；两项不支持语法均拒绝且零动作。这是明确选择技能之后的本机真实模拟器功能证据，不是 LLM 自主执行、独立泛化或模型能力提升结果。

实现改动集中在 [harness_skills.py](../../../agent4drone/harness_skills.py) 的公开搜索动作判定与起飞后确认；[harness_core.py](../../../agent4drone/harness_core.py) 注册 `flight_state_v3` 并继承 v2 控制语义，[harness_integration.py](../../../agent4drone/harness_integration.py) 继承 v2 接线与工具说明。显式设置 `UAV_HARNESS_REVISION=flight_state_v3` 可供后续 Harness 实验使用，默认仍为 `legacy`。新增定向回归见 [test_harness_flight_state.py](../../../agent4drone/tests/test_harness_flight_state.py)。

## 比较条件与完整性

基准 `entry_contract_v2` 与候选 `flight_state_v3` 均使用 `applicability`、相同原 full100 完整快照和时间线、空黑板、新 R2 账本、公开工具及原任务 checker。逐题交替版本顺序，每次新进程。驱动器显式 start，运行到 terminal；没有 LLM、原工具接手或额外恢复策略。

预算均为 500 秒 deadline、每计划 2,000 API、16 次局部修复预算、16 轮搜索；外层 90 秒 watchdog。实际没有触发 deadline/watchdog/API 上限。任务执行测得 0.013–3.148 秒，反映此离散模拟器的执行时间，不代表运行满 500 秒或实机飞行耗时。

- 14/14 trial 协议验证通过，7/7 配对初始结构 hash 一致；零网络连接尝试、零模型调用。
- 运行前冻结 76 份源码、协议以及 19 项原 fixture 依赖哈希；运行结束原 manifest 全部 410 项及冻结源码均无变化，工作区对应源码也无中途变化。
- [`campaign-seal.json`](run-20261023-01/campaign-seal.json) 的 273 项文件逐一重验无差异。原入口契约封存未改动。
- 28 次重复 start/终态 resume 探针均无世界、计划 frames、API 或物理事件变化；其中有计划的 20 次明确返回 `rejected/resumable=false`，其余 8 次是不支持语法/不存在 plan 的负对照。
- 本批 7 个 v2 结果与上次入口契约封存的 v2 在初态、规范化动作序列、公开进度、停止原因和完整 checker 返回均一致。时间戳、耗时不要求逐字一致；没有据此修改协议追求一致。见[历史一致性核对](run-20261023-01/historical-baseline-comparison.json)。

代码回归由主代理独立完成：既有 191 项 + 新飞行状态 68 项，共 **259 个不重复测试通过**。新专项早期 66 项是后续 68 项的子集，不重复计数。见[回归记录](regression-validation.json)、[既有回归日志](existing-regression.log)、[专项日志](flight-state-unit.log)。测试通过与下表整题任务结果分开记录。

## 固定 7 题结果

表中 `v2 → v3` 为本批同时冻结执行的两个版本；`检查` 为各题原 checker 子检查通过数。

| 任务 | 公开进度 | 检查 | 物理动作尝试 | 最终原因 |
| --- | --- | --- | --- | --- |
| `7eba7390` | 33% → 33%（1/3 目标） | 0/1 → 0/1 | 85 → 85 | 均 `round_limit_below_threshold` |
| `24ac1838` | 5% → 33%（5/100 → 33/100） | 0/1 → 0/1 | 45 → 137 | `search_move_error` → `local_repair_budget_exhausted` |
| `b1ad4f3e` | 75% → 75%（15/20 目标） | 0/1 → 0/1 | 108 → 108 | 均 `local_repair_budget_exhausted` |
| `b1eb0270` | 0% → 0% | 2/2 → 2/2 | 3 → 3 | 均 `skill_public_stop_condition` |
| `898cfdca` | 0% → 0% | 2/2 → 2/2 | 3 → 3 | 均 `skill_public_stop_condition` |
| `784d71f8` | 0% → 0% | 0/4 → 0/4 | 0 → 0 | 均 `unsupported_skill_or_full_task_grammar` |
| `1905e0ad` | 0% → 0% | 0/6 → 0/6 | 0 → 0 | 均 `unsupported_skill_or_full_task_grammar` |

简单链任务不要求搜索目标，公开 session progress 为 0 不代表简单链失败。负对照用于验证技能不吞掉额外义务，拒绝正确也不等于原整题完成。因此不把混合 7 题的 2/7 宣称为 Benchmark 成功率。

除 `24ac1838` 外，其余 6 对规范化动作请求序列完全相同；两个搜索题分别在第 16 轮耗尽及第 17 次障碍修复事件退出。`flight_state_v3` 没有改变这两项现有搜索限制。

## `24ac1838` 的直接修复证据

两臂前 44 次动作请求完全相同，**第 45 次动作请求首次分歧**。这里包含错误尝试，不等于成功执行了 44 个动作。两臂请求 198 的公开状态均为 actor `a83433e1`（Drone 30），`idle`、位置 `(405,1497,57)`、电量 100%。

| 公开请求 | v2 | v3 |
| --- | --- | --- |
| 199 | 直接 `move_to(455,1575,20)`，回执 `Cannot move from current status: DroneStatus.IDLE` | `take_off(20)`，回执 success |
| 200 | 结束后的公开进度读取 | 再读该 actor：`hovering`、`(405,1497,20)`、电量 99.5% |
| 201 | 隔离 checker | `move_to(455,1575,20)`，回执 success |
| 202 | — | 再读该 actor：`hovering`、`(455,1575,20)` |

这里调用的是模拟器的 `take_off` API；真实回执/状态显示高度从 57 变成 20，不能把 API 名称解释成高度必然上升。策略依据公开状态确认命令合法性及执行效果，没有改写初态。

正高度 idle 状态触发在每臂均只出现 1 次。v2 未起飞，v3 成功起飞；v2 的 IDLE move 错误 1 次，v3 为 0 次。v3 其余搜索起飞来自零高度 idle，两个简单链保持原逻辑；本组没有 ready、异常状态或外部中途状态切换的真实模拟器实例，那些情形只有专项功能测试，不能借此扩大本组覆盖范围。

证据见 [v2 公开请求](run-20261023-01/trials/24ac1838-entry_contract_v2/public-api.jsonl)、[v3 公开请求](run-20261023-01/trials/24ac1838-flight_state_v3/public-api.jsonl)、[v3 状态诊断](run-20261023-01/trials/24ac1838-flight_state_v3/flight-state-diagnostics.json)，以及[配对首次分歧](run-20261023-01/summary.json)。诊断只连接技能的公开读取与后续动作，不读取私有状态来解释失败。

## 修复后的停止原因与代价

| 指标 | v2 | v3 |
| --- | ---: | ---: |
| 技能 API / 含最终公开评估读取的 API | 168 / 169 | 532 / 533 |
| 起飞次数（全部成功） | 22 | 30 |
| 移动尝试 | 23 | 107 |
| 全部动作成功 / 错误 | 38 / 7 | 120 / 17 |
| 横向累计路径 | 1,660.751 m | 9,858.463 m |
| 累计垂直位移 | 440 m | 617 m |
| 累计耗电 | 18.899 mAh | 73.510 mAh |
| cursor / 完成 unit / repairs | 22 / 22 / 6 | 107 / 106 / 17 |

v3 消除提前退出后继续执行更多动作，进度上涨伴随明显成本增加。`repairs=17` 表示观察到第 17 次错误后退出；配置仍是 16，未提高预算。失败退出发生在第 4 轮，早于 16 轮上限；最后事件尚未完成 checkpoint，因此 unit 为 106、cursor 为 107。

v3 的 17 次错误全部是障碍回执：**14 次目的点冲突、3 次路径冲突**。按对象类型为 polygon 9、circle 6、ellipse 2。`Circle Obstacle 18`、`Polygon Obstacle 47`、`Polygon Obstacle 7` 各 3 次，共 9/17；其余 8 个错误分散在 8 个对象上。v2 前 6 次障碍错误在 v3 的共同前缀内保留。

最后 4 次错误请求为 528（Polygon 7）、532（Polygon 47）、553（Polygon 7）、561（Polygon 19）。请求 560/562 的公开状态均为 `hovering`、电量约 97.13%，故最后一次失败有明确障碍回执，不应再归因为 IDLE 或低电量。请求 561 是第 17 次修复事件，随后保存终态；原 checker 仍为 0/1。

发现两组“同 actor、同目的点再次失败”的公开证据：`4e22b83f` 在请求 293/532 再次前往 `(1715,175,20)`，均报 Polygon 47；`869a8e16` 在请求 313/553 再次前往 `(1715,1155,20)`，均报 Polygon 7。它们是下一步检查失败候选记忆、有效期与共享范围的线索；本轮未验证修改这些机制能否完成整题。

## 目标发现与访问的边界

只统计 task 阶段技能 `get_nearby_entities` 中返回的唯一目标 ID，v2 发现 24 个，v3 发现 47 个；最终公开 session aggregate 的已访问目标分别是 5/100、33/100。

v3 有 33 条含 `Reached target(s)` 的动作回执，共出现 41 次目标名称、33 个唯一名称，与公开访问总数一致。按公开名称集合交叉核对：其中 32 个名称也出现在 nearby，15 个 nearby 名称没有达到回执，另 1 个达到名称未出现在 nearby。因此 **47 − 33 不能直接解释为“还有 14 个已发现未访问目标”**。这里只描述现有公开日志的集合关系；没有利用私有目标位置补全视野，也没有证明改进目标利用策略就能完成任务。

原始公开行路径、SHA、每个首次发现请求、达到回执和障碍分类保存在[公开证据诊断](public-evidence-diagnosis.json)。它是封存运行结束后的派生分析，不是策略输入。

## 保留判断与下一步

`flight_state_v3` 在 opt-in 版本中保留：它修复了公开状态与动作合法性之间的明确错误，在固定对照组未改变非触发任务的动作序列，且通过回归；默认版本没有改变。该结论只支持功能修复，不支持“搜索已稳定完成”或“训练环境已经充分可靠”。

下一项候选工作先诊断**已知障碍与失败候选重复选择**：重建上述两组重复失败的公开证据流，检查候选屏蔽及失效条件，再决定是否存在可用同组固定样本验证的最小修复。本轮到此停止，不增加预算、不混入目标利用策略、不追加新 trial，也不续跑原 360 次。

程序入口为 [`scripts/validate_harness_flight_state.py`](../../../scripts/validate_harness_flight_state.py)，完整条件见[协议](PROTOCOL_CN.md)，全部试验结果见[`results.json`](run-20261023-01/results.json)。

从仓库根目录重现时指定一个尚不存在的新目录，保留本次封存：

```sh
agent4drone/.venv/bin/python scripts/validate_harness_flight_state.py --output docs/results/harness-flight-state-20261023/run-new
```
