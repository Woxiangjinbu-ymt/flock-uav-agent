# v5 / v6 终止进度确认：独立批次审计

日期：2026-10-23。对象为 [run-20261023-01](run-20261023-01/campaign-seal.json)。只读取封存、源码与公开回执，未修改 run、生产代码或旧批次，未重跑仿真/模型。

**结论：退出报告缺口已在固定 b1ad 任务中修复，未增加物理动作；本批任务成功率没有改变。** v6 增加一次技能公开进度读取，用当次证据把缓存 95% 更新为 100%，正确返回 `completed / skill_public_stop_condition`。七对任务物理动作请求序列全部相同，三个搜索的原 checker 均为 **1/3→1/3**。两处历史耗电字段不完全复现，已经保留并解释，未作为成功率或节能收益。

## 完整性与协议

[独立脚本](independent_verify.py) 不导入 runner 或候选生产函数执行，通过标准库重算公开数据，结果为 [independent-verification.json](independent-verification.json)：**352 项不变量检查全部通过**；另 7 项历史 cost 逐字段一致性诊断为 5 项完全一致、2 项只有耗电差异。因此机器总数为 357 个相符、2 个历史差异，**不是两个实验无效或两个测试失败**。这些静态审计数不能与 14 个 trial、437 个代码测试相加。

| 检查 | 结果 |
| --- | --- |
| 本批封存 | 329 个 seal 文件哈希一致 |
| 源码与输入 | 76 个冻结源文件及审计时 live 源码一致；19 个选定输入、原 manifest 全部 410 个文件一致 |
| 旧批次 | 上轮 v4/v5 run 的 315 个文件保持封存哈希 |
| 执行 | 原 7 题 × 两版本、交替顺序、14 个独立 PID、14/14 verified、子进程返回 0 |
| 初态/语义 | 原快照结构与公开初态 hash 一致；原 command、完整 checker 检查树及分母一致；空黑板、新账本 |
| 预算 | 两臂 500 秒 / 2000 API / repair=16 / MAX_ROUNDS=16，90 秒 watchdog；没有增加恢复策略、LLM 或 raw 接手 |
| 静态契约 | 两臂均显式 True、相同 task writer allowlist、无违规请求；初尾及配对障碍 digest 一致；独立重算 evaluator 最终几何 digest 相符 |
| raw 核算 | 全部动作/API/错误、物理历史与路径、耗电汇总相符；严格跨 actor 及 unique 错误计数一致 |
| 终态 | 共 28 次重复 start / 终态 resume 探针，无额外调用、物理效果或 frame/world 修改 |

无 writer 隔离、冻结时间线与源码检查共同支撑本次静态条件；初尾 digest 本身不是动态世界从未改变的证明。私有状态仅用于隔离评估及完整性验证，新增策略读取仍来自公开 `/sessions/current`。

## 唯一新增的公开确认

`b1ad4f3e` 的关键对照如下：

| 项目 | 本批 v5 | 本批 v6 |
| --- | --- | --- |
| 原 checker / 目标数 | 通过；20/20 | 相同 |
| runtime | `needs_replan / no_public_candidate` | `completed / skill_public_stop_condition` |
| frame 缓存进度 | 95 | 100 |
| 物理动作请求 | 191 | 191，逐个 actor/工具/参数相同 |
| cursor / repair | 176 / 10 | 176 / 10 |
| 技能 API / 进度读取 | 817 / 18 | 818 / 19 |
| 全部 task API（含 evaluator 末次公开读） | 818 | 819 |

v6 的 **request834** 是技能来源、非 admin、已提交的 `GET /sessions/current`，`action=False`、`history_events=[]`、`energy=[]`，返回本场景 100%、20/20。对应 Harness 调用 `...:p1:818`、evidence **e1635**。按冻结公开适配器规则从 raw session 投影的 normalized 回执 SHA-256 为 `6c601a4d8aaeeedc7595a5b5fd8709d4731f7242c39a18a3f7156f4cf02b7ade`，与 `public_call_end` 记录一致。

时序为 Harness event **2810 call_start → 2811 call_end → 2813 completed plan_update → 2814 completed skill_exit**；确认读取之后没有技能动作。evaluator 的公开读取是后面的 request835，原 checker 为 request836，二者未被倒灌为技能证据。没有单独保存 ledger 正文；本审计使用 raw 回执、冻结投影规则及 Harness 的引用和回执 digest 交叉核验。

其余六对没有 `terminal_progress_check`，技能调用数、进度读取数与物理动作数均不变：`7eba7390` 仍 33%、轮数上限退出；`24ac1838` 仍 38%、repair 预算耗尽；两道简单正对照通过；两道完整语法负对照仍拒绝。新增确认没有顺带改变 round/repair 等其他停止分支。

## 四边界的分母

[437 项回归汇总](regression-validation.json)由原 376 项与新增 61 项构成，覆盖已完成、未完成、读取失败与预算/生命周期不足。生产静态契约见 [CONTRACT_AUDIT_CN.md](CONTRACT_AUDIT_CN.md)。本轮真实 14 次运行只在 b1ad 触发一次**成功确认**；不能把其余三类边界写成已在这些真实任务中逐一触发。

新增读仍占用原 API 和时间预算。合法读数恰好 95 时不成功；错误 session/source/scope、非法值或失败读取不能由旧缓存替代；预算不足不允许额外特权读取。`terminal_progress_check.status=confirmed` 表示读数已确认，完成结论还需严格 >95 和实际终态，不能单凭该字段计成功。

## 成本和历史复现的例外

同批动作、路径、成功/失败次数全部相同，但耗电出现细微差异：

| 比较 | v5 / 历史值（mAh） | v6 / 本批值（mAh） | 后者减前者 |
| --- | --- | --- | --- |
| 同批 24ac：v5→v6 | 89.855796108 | 89.785821351 | −0.069974756 |
| 同批 b1ad：v5→v6 | 171.585948488 | 171.575591072 | −0.010357416 |
| 24ac：历史 v5→本批 v5 | 89.853838761 | 89.855796108 | +0.001957346 |
| b1ad：历史 v5→本批 v5 | 171.635679898 | 171.585948488 | −0.049731410 |

[逐动作耗电诊断](energy-reproducibility-diagnosis.json)保留所有非零差额。以 1e−8 mAh 区分显著于浮点舍入的差异，同批分别 5 / 1 个动作、历史分别 9 / 7 个动作，**相同动作请求的回执时间均位于不同模拟整数秒**；剩余单步差异约为 1e−14。差额总和与汇总耗电一致。

冻结 `World.advance` 按 `int(elapsed)` 应用环境时间线，耗电计算读取当前环境。这支持墙钟推进跨环境时间边界的解释；没有读取或推断具体隐藏天气值。24ac 根本没有触发新确认，b1ad 的确认也在全部动作之后，因此不能把这些已有动作的耗电差当作新只读确认的节能效果。

七个本批 v5 均复现上轮的初态、动作序列、公开进度、停止原因及原 checker；非能耗 cost 字段一致。只有上述两个 consumed_mah 字段不逐字一致，elapsed 也不同。未为追求一致重跑或改预算。

本批支持一个受限功能结论：在既有执行已达到公开阈值、但缓存滞后的无候选出口，新增一次有预算的公开确认可以修正终止反馈。**它没有提高本批整题完成数，也未证明搜索能力、线上 Agent 行为、后训练收益或泛化。**
