# 已观察固定目标跟进：固定配对验证协议

日期：2026-10-24。状态：**READY，campaign NOT_RUN**。基准 `terminal_progress_v6`，独立 opt-in 候选 `observed_target_v7`；默认 legacy 不变。生产专项与回归共 532 项不重复检查已通过，生产独立审查无阻断；新 runner 后验诊断的 32 项离线自检通过，待根代理完成最终 runner 审查后冻结执行。本次不修改旧 runner、旧协议、fixture 或封存。

## 唯一候选变化

候选只检验一个问题：公开 nearby 已经给出可用的固定目标信息时，发现者能否在自己的一个正常动作槽内，进行一次有界定向尝试，替换本次探索动作。主线为当前 v6 的运行契约，继承飞行状态、静态障碍、同计划目的点负证据及无候选退出前公开确认；不同时改变 24ac 的路径策略、障碍近似、候选评分、动作预算、修复上限或调度。

仅使用**当前 plan 内**成功公开 nearby 中的固定目标及有效有限 XYZ、task radius；不从继承黑板或私有场景补充目标，不由 `is_reached=false` 推导“仍有任务未完成”。定向动作保持原固定高度 20 米，公开目标的高度及 task radius 必须支持该动作的任务半径语义；信息缺失、类型不明、坐标/radius 非法或变化、身份/名称模糊时退回原探索，不猜测目标位置和完成状态。

每个合格目标只分配给本 plan 的原发现者，最多一次定向尝试；不增加轮数、独立动作队列、跟进重试或跨 actor 交接。发现者无法执行时不为此扩展恢复策略。动作通过现有 PublicCall 与进度/修复流程，占用同一个 actor 的正常槽并计入原动作、API、repair 与时间限制；不得因为新增目标动作而把未执行的 lattice candidate 记为已覆盖或已选择。

成功 move 回执中的规范正向名称信息，仅可保守抑制后续同名定向尝试，**不作为某个 target ID 已完成、所有目标已完成或全局 remaining 数量的证明**。公开 `is_reached=True` 同样只记录 `positive_flag_ref` 并保守抑制定向尝试，与名称抑制分列；False 完全不当 remaining 依据。重复名称、未知身份以及矛盾/未知/错误回执不得被提升成可靠完成事实。真正停止仍依赖技能自己的有效公开 aggregate progress 严格 >95；整题 checker 仍独立评估。目标变化或证据模糊采用保守退回，不能根据更晚观测倒推早期策略已知。保留的公开障碍近似和目标资格检查只是候选筛选，不能保证实际到达；实际动作失败仍照原流程计入预算。

frame 的 `observed_target_followup` 保存 scope、targets、observed_name_ids、positive_names、attempts 和 directed_indices；跨 actor 名称歧义包含坐标无效的同名项。定向候选以独立追加 index 对应真实坐标；坐标等价的 tried edge 仍需遵守旧失败记忆，不得借新 index 重放。

## 样本、初态与固定预算

保持原 7 题及顺序：搜索 `7eba7390`、`24ac1838`、`b1ad4f3e`；短技能正对照 `b1eb0270`、`898cfdca`；完整语法负对照 `784d71f8`、`1905e0ad`。每题 v6/v7 各一次，共 14 次；偶数索引先基准后候选，奇数相反，每 trial 新进程。不得临时换题、补样、挑选成功结果或自动补跑。

使用原 full100 完整快照和时间线，先校验 manifest、任务、pickle、timeline 和场景来源 hash，再反序列化。公开准备核验后再次恢复，空黑板、新证据账本、同完整初态。两臂都是 `applicability`，**500 秒 deadline、2,000 API、16 次 repair、16 轮**，外层 90 秒 watchdog。不调用 LLM：驱动器显式选技能并启动直到 terminal，没有 raw 工具接手、自动重规划或额外恢复策略。结果不代表自然模型会选用此技能。

两臂均声明并落实同一静态障碍契约：本机独立 ASGI TestClient、不启动 lifespan/background simulator、socket 阻断、相同 task allowlist、admin/地图写入在进入 server 前被拒绝。冻结 timeline 仅允许既有 second/targets/environments/rng 项；**静态障碍不等于静态目标**，候选必须正确处理公开目标变化。障碍初尾几何由隔离 evaluator 检查且不进入 policy；隐藏检查树仅在技能结束后的 scoring 阶段使用。

## 指标及后验目标诊断

保留原 checker、技能 status/reason、公开 aggregate progress、所有动作和失败、严格跨 actor 目的点重复、终止确认、API、路径长度、耗电和墙钟成本。三个搜索题单列分母 3；两正对照与两语法负对照分列，不把混合 7 题比例称为 Benchmark 成功率。首个动作分歧须回溯其发生前的公开证据；所有正负结果和成本差额完整保留。

新增 `observed-target-diagnostics.json` 已接入，只在执行结束后读取 raw API 与 Harness snapshot，不能参与选择、奖励或终止。核对当前 plan 的目标观测、发现者、source ref/hash、候选/后备 grid 坐标、动作 ref/hash、公开 task radius、先观察后动作、move 序号、已试过的坐标等价边，以及正名称/True 标志来源；保留原始动作回执、提交标志、物理事件数与后读位置。任何目标 ID 关联都必须有原始公开观测支持；名字回执与 ID 完成严格分开。

`attempts` 在 choose 阶段先记 scheduled，起飞失败或 API 拦截可能根本没有 move；未知效果也可能已经发出 raw move，却尚无 `action_returned/action_ref`。因此单列 scheduled record、returned action、matched raw move、undispatched or unresolved，不能拿列表长度当实际定向 move 分母。没有明确 frame 记录时不凭“恰好移到目标附近”猜测意图。最终原始回执可能尚未被 generator 消费；末态名称集合仅核对已有记录的来源，在实际动作发生前再检查公开前缀的完整歧义/变化资格。没有保存 ledger 正文，使用 raw 回执投影与 Harness 调用顺序、evidence ref、摘要核验，并明确该限制。

[32 项 runner 离线自检](runner-diagnostics-check.json)包括旧七题 v6 兼容、正常返回、未派发/未知回执，以及错误引用、迟到观测、跨 plan、actor/command/XYZ、候选 index、重复尝试、非法半径/高度、正标志、类型/坐标、名称歧义和摘要反例；未运行新仿真。该分母与 532 项生产代码检查及未来 14 次真实仿真分别记录。

同预算意味着上限不变，不要求实际 API/路径/能耗相等；定向行动可能改变后续路径和结束时点。记录首次分歧、命中/阻断/退回以及其他探索动作的变化，不能把局部靠近目标或分数上涨自动当作学术创新。既有 `advance(elapsed)` 可能使完全相同动作跨越不同时间段，能耗差异不得被悄悄抹平。

四类旧终止进度边界与新目标字段、身份变化、一次尝试、正常动作槽、失败/未知及生命周期边界均需有意义的专项测试；真实 14 次固定任务不保证覆盖这些反例，不注入返回值制造真实任务覆盖。

## 验收、封存与执行界限

保持重复 start/terminal resume 的 before/after 快照检查：不能改变 frames、世界、API 或物理事件。基础设施、hash、网络、静态契约、诊断证据不一致或控制副作用停止后续派发；普通任务失败、预算耗尽或语法拒绝如实计分，不自动重试。

新 runner 为 `scripts/validate_harness_observed_target.py`。执行前复制同一份源码、协议、输入 manifest 并记录 hash；worker 只使用冻结源码。封存 raw API、物理事件、控制、Harness、现有诊断及新目标诊断、初尾静态契约、原 checker、成本、配对比较及历史基准比较。独立对照上一轮 `harness-terminal-progress-20261023/run-20261023-01` 中的 **v6**，旧结果不代替本批基准。报告和独立审计放在 run 外层。

预留新目录 `docs/results/harness-observed-target-20261024/run-20261024-01` 必须不存在。以下命令仅记录冻结后的入口；**READY 不代表 campaign 已执行**：

```sh
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python scripts/validate_harness_observed_target.py --output docs/results/harness-observed-target-20261024/run-20261024-01
```

不安装依赖、不连接模型或外网，不解封和覆盖任何旧批次。未来复现使用新的不存在目录。
