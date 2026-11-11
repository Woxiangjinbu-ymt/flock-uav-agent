# 最小 Harness：实现与离线验收

日期：2026-10-15。状态：最小实现、离线测试、历史快照接口验证及同技能实验准备完成；**没有启动新的在线模型实验**。

## 1. 实现范围

新增 `evidence_harness`，显式启用；原有默认模式与 R2 仍可运行。主模型通过 `run_harness` 选择并承诺一个技能，运行时维护有原指令来源的 `DecisionFrame`：task / scene / epoch、计划版本、责任 UAV、步骤、执行游标、证据引用、继续条件、失效条件、局部修复范围和预算。原始工具继续承担不支持的任务和退出后的修复。

这版的“计划”是由完整任务语法编译出的短技能，模型负责选择、提交、恢复或放弃。它**不是任意自然语言计划生成器**，也没有实现多分支自由调度、学习式门控或跨进程恢复。

| 文件 | 职责 |
|---|---|
| [harness_core.py](../../../agent4drone/harness_core.py) | 计划生命周期、可恢复执行、门控、预算、取消与日志 |
| [harness_skills.py](../../../agent4drone/harness_skills.py) | 简单动作链和公开搜索的统一生成器接口、公开效果检查 |
| [harness_integration.py](../../../agent4drone/harness_integration.py) | 主模型工具、R2 中间件、实际 API 调用接入 |
| [uav_agent.py](../../../agent4drone/uav_agent.py) | 显式模式、同步／异步执行图、结果与轨迹保存 |
| [validate_minimal_harness.py](../../../experiments/analysis/validate_minimal_harness.py) | 历史任务适用范围与本地真实服务器验证 |
| [minimal_harness_pilot.py](../../../scripts/minimal_harness_pilot.py) | 离线冻结、校验、预检；没有在线启动命令 |

统一接口：

```text
run_harness({"action":"start","skill":"simple_chain"})
run_harness({"action":"start","skill":"public_search"})
run_harness({"action":"resume","plan_id":"返回的计划 ID"})
run_harness({"action":"cancel","plan_id":"返回的计划 ID"})
```

实际 LangChain 工具参数是 `input_json` 字符串。每次 `run_harness` 必须单独占据一个工具批次，混合批次中的 harness 调用会被拒绝；已完成动作不会因 cancel 撤销。同一任务不能重新 start 一个已部分执行的同名技能。

简单链支持三个完整语法：指定单机起飞→旋转→悬停，起飞→爬升→悬停，起飞→悬停。额外要求、歧义绑定、未知效果不能被自动忽略。搜索只支持原来的完整聚合目标任务，继续使用原 `PublicSearchPolicy` 的候选点选择算法。适配器新增了状态重查、起飞效果确认、预算和退出接口，因此不能声称它与旧 worker 的全部 API 序列相同。四个新调度臂使用同一适配器，API 序列的等价性在它们之间验证。

## 2. 门控和日志

一个检查点是“一个简单动作及效果核验”，或“一次搜索移动尝试及状态／感知／周期进度查询”，不是一个底层 API 调用。每个 API 前后还检查取消与截止。

| 决策 | 最小实现 |
|---|---|
| CONTINUE | 在已承诺技能内推进；阻塞候选可被原搜索策略有界替换，失败起飞可禁用该 UAV 后处理其他 UAV |
| OBSERVE | 读取绑定对象、状态、感知或公开聚合进度；动作效果未知时只查证一次，再交回模型，不盲重放 |
| REPLAN | 状态或作用域变化、无法处理的效果／失败、API 或局部修复预算耗尽；原工具可处理剩余任务 |
| STOP | 已满足技能公开停止条件；不产生隐藏 Checker 判定 |

暂停期间，相关 UAV 的外部原始动作使对应计划失效；无关 UAV 的动作不使简单链失效。搜索计划依赖全队，因此其作用域更大。恢复时重新读取公开状态，不能只信暂停前的回执。未知情况没有被提升为全局禁飞条件。

`harness.events` 保存 `plan_commit`、`plan_update`、`gate`、`skill_enter/exit`、`public_call_start/end`、`plan_invalidated`、模型逻辑请求和 HTTP 尝试事件。底层真实 API 逐次进入 R2 ledger，通过 evidence ID 联接；控制工具本身不被伪装为无人机动作。返回内容包括最近真实动作回执、已验证状态及证据引用。

主模型请求有独立 ID，SDK 重试有 HTTP attempt ID；记录模型处理开始／结束、usage、工具调用 ID、HTTP 请求开始／响应头到达及未观察到响应头的尝试。**handler 墙钟不是模型内部思考时长；响应头到达不是完整响应接收完成。** 不记录密钥、认证头或隐藏思维链。

日志先保存在内存，任务正常结束或可捕获异常后写入现有 toolchain JSON；不是抗进程崩溃的持久事件流。取消发生在 API 边界，无法瞬间撤销进行中的网络请求。新模式的公开 API 请求使用至多 10 秒、且不超过剩余任务预算的 socket timeout；它不是严格的总请求墙钟上限。外部 500 秒评测协议继续有效。

## 3. 离线证据

完整 agent 与服务回归 **533 项通过**；冻结源码副本的专项预检 **69 项通过**。新增覆盖同步／流式／异步真实执行图、服务入口日志、四臂同动作、未知效果、部分效果、错误 actor、暂停恢复状态变化、局部失效、取消、截止、并发重复提交、实际 HTTP hooks 与预算。

完整回归有 3 条警告：一条既有 Starlette/AnyIO 弃用警告、两条非交互绘图提示。使用 `MPLBACKEND=Agg` 运行，避免既有覆盖路径测试的 `plt.show()` 等待 GUI。命令及日志见 [verification.json](verification.json)、[regression.log](regression.log)、[冻结预检日志](../../../experiments/minimal-harness-v1/preflight-tests.log)。

历史 fast100 的完整任务文本：7 题可匹配简单链、6 题可匹配搜索、87 题不支持，保留全量 100 题分母。这是覆盖率，不是成功率。

本地 FastAPI TestClient 恢复这 13 题各自历史开始快照，四调度臂共 **52 次执行**。策略只使用 AGENT 权限公开 API；ADMIN 只用于恢复夹具和执行结束后的原 Checker。所有 13 题在四臂间保持相同 API 调用序列、最终公开状态、退出状态及事后检查结果。完整数据见 [report.json](report.json)，每次计划、门控和真实回执见 `traces/`。

| 历史任务 | 适用性调度结果 | 动作数 | 最终公开进度 | 事后原检查 |
|---|---|---:|---:|---|
| 7 个简单链任务 | 全部完成并核实所需动作与最终状态 | 合计 19 | 不作为其任务判定 | 7 个通过 |
| `9c0b52a0`、`7eba7390` | 起始公开进度已达标，直接结束 | 0 | 100% | 通过；不能归功于新动作 |
| `5e743d21` | 局部修复预算耗尽，交回模型 | 90 | 67% | 未通过 |
| `24ac1838` | 局部修复预算耗尽，交回模型 | 123 | 81% | 未通过 |
| `7cdefb56` | 局部修复预算耗尽，交回模型 | 89 | 90% | 未通过 |
| `b1ad4f3e` | 搜索公开停止条件达标 | 195 | 100% | 通过 |

每臂合计 1,890 次技能内公开 API 调用、516 次动作。搜索初始化的公开观测种子另计每臂 121 次查询；它是四臂一致的测试夹具，不是在线自动预处理。没有隐藏目标位置进入策略。以下数量是脚本调用 `start/resume` 的次数：

| 调度 | 控制片段数 |
|---|---:|
| applicability | 13 |
| every_step | 413 |
| fixed，K=4 | 108 |
| failure | 68 |

这些**不是实测模型调用数，也不是已证明的调用节省**。模型尚未参与这些恢复选择。本地模型调用为 0；不能用上述差值直接宣称线上节省比例。

三个搜索退出案例说明：调用更少不等于任务更成功，局部修复预算和模型接手后的行为需要在线验证。没有根据这些结果放宽预算或删除困难任务。此次修正了一个边界：先查公开进度，已达标时无需因缺少障碍观测而要求重规划。

## 4. 同技能对照包

[冻结清单](../../../experiments/minimal-harness-v1/frozen/study.json)包含 180 个源码／依赖描述文件、原 5 个场景与 fast100 清单、运行环境、每臂计划、适用范围和哈希；[预检](../../../experiments/minimal-harness-v1/preflight.json)已通过。使用单一共享源码包，包含本次未提交的新 Python 文件，不依赖仅有 HEAD 的快照。

| 臂 | 作用 |
|---|---|
| R2 reference | 原模型循环参照，使用同一源码版本下的 `evidence_r2` |
| same-skill every_step | 相同技能，每个检查点交回模型 |
| same-skill fixed4 | 相同技能，每 4 个检查点交回模型 |
| same-skill failure | 相同技能，可处理失败后交回模型 |
| same-skill applicability | 技能仍适用且失败可局部修复时继续，否则交回模型 |

四个技能臂共享动作、观察、公开状态检查、强制失效条件、修复策略和提示；**failure 不是忽略所有非失败失效条件的裸基线**。这轮先隔离检查点调度与局部修复后交接的差异，不能把所有收益归因于独立的适用性监控模块。后续若要证明后者，需要另设非失败失效事件的受控消融。

模型显式选择技能作为四臂共同激活规则，取消旧搜索 worker 的 400 秒触发策略。模型可以不用技能；未调用必须纳入全量结果。离线夹具中的强制 start/resume 不用于线上计分。

每臂 100 题、605 检查点、5 场景，保留原场景与题序、场景内继承、500 秒截止、原 Checker、两场景 worker、无自动重跑。第一轮探索固定 1 次，共 500 次任务执行；顺序已写入清单。该开发集单次试验不能支持显著性、泛化或非劣性结论。后续确认实验需另行冻结验证集、重复次数与平衡顺序。

主指标仍是 500 秒内原任务成功率及配对 W/L；同时记录完整检查点分母、超时、逻辑模型请求、HTTP 尝试、usage、工具负担、技能选择／退出、错误继续和不必要交接。先报完整分母，再给适用／调用子组分析。

## 5. 下一步研究判据

1. 验证模型是否会正确选择 `start/resume`，是否存在过早报告完成、重复 start 或放弃未完成计划。离线脚本不能代替这一步。
2. 在相同技能下检验交回模型的频率能否下降，同时保留任务成功；专门追踪上述 3 个修复预算退出案例是否得到有效接手。
3. 分析非失败状态变化、局部阻塞和未知效果三类事件的交接必要性。现有共享强制检查不构成独立门控收益消融。
4. 若适用任务太少或收益仅来自搜索算法，不扩张论文主张。当前可陈述的实现贡献是“显式计划—公开证据—可中断技能—可审计交接”的原型；性能与研究新颖性仍待实验和相关工作比较。

复现离线验证：

```bash
MPLBACKEND=Agg agent4drone/.venv/bin/python -m pytest -q agent4drone/tests scripts/tests/test_m1_revision_runtime.py scripts/tests/test_m1_revision3_runtime.py scripts/tests/test_minimal_harness_runtime.py
PYTHONPATH=agent4drone:agent4drone/.venv/lib/python3.11/site-packages:server server/.venv/bin/python experiments/analysis/validate_minimal_harness.py
agent4drone/.venv/bin/python scripts/minimal_harness_pilot.py verify
agent4drone/.venv/bin/python scripts/minimal_harness_pilot.py preflight
```

已存在的冻结包不能被 `prepare` 覆盖。重新验证会更新离线结果；若改变实现或协议，应建立新版本实验包。
