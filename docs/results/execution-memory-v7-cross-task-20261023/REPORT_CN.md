# execution_memory v7 跨任务离线忠实性审计

日期：2026-10-23。状态：**一次固定输入离线审计完成；未运行模型、真实工具或仿真器，未修改记忆算法。**

## 结论与适用边界

固定的 14 道原文任务全部触发 v7 的 v5 回退分支：**compact 资格为 0/14**。在 355 条真实归档公开工具回执及初始状态组成的 369 个前缀读取点，v5/v7 的决策上下文逐字一致；账本、actor、路线顺序、完成/剩余、公开观察和来源检查均无差异或检查失败。因此，本批验证支持普通回退兼容性，也暴露了 compact 对这类带模板导语的任务**没有覆盖**。它没有验证原题上的 compact 收益。

另外单列 14×6＝84 个 route-block-only **合成派生探针**，在 388 条合成回执、472 个读取点校验 compact 的语义边界。独立预期与来源检查无失败。字符总量减少 16.35%，但 12 个读取点变长，最大增加 218 字符；这不是在线 token、成本、延迟或任务成功率结果。

两组均保留 `mission_completion=not_evaluated`。未发现本次检查能识别的 v7 相对 v5 实质回归；这不等于记忆语义已被穷尽验证，更不证明新任务在线泛化。

## 输入、冻结及重放方法

- 执行前保存 [协议](PROTOCOL_CN.md) 和 [固定队列](cohort.json)。主样本是 `docs/audit/r3-disjoint-feasibility-20261009/screen.json` 中 **全部** 14 个 `all_route_points_locally_legal_at_nominal_altitude=true` 任务，来自 11 个场景；没有根据本次输出筛题。
- 真实输入来自 `experiments/runs/20261003-143255-full-5c501118/<scene>/<task>.job.json`。仅使用完整原始 command，以及 `intermediate_steps` 的工具名、参数和公开返回值；未使用模型解释、隐藏 checker、before/after 私有世界或几何筛查详情填补 ledger。
- 每题从独立空 `SessionEvidenceIndexMemory` 开始，**没有预载真实运行前的状态或继承 blackboard**。公开 `list_drones` 提供 actor 绑定；31/31 条路线最终均能绑定，合成探针无缺失公开 actor 来源。
- 归档没有可恢复的原始 tool-call ID、开始/结束时序及并发分组。按 `intermediate_steps` 的顺序串行重放，使用 `audit-call-N`。这是声明的档案解释，不能声称精确重建原在线运行。
- v5/v7 接收相同输入；每题初始读一次、每条回执后读一次，统一 3900 字符上限。这里的“读取点”是审计人为选择的每个回执前缀，不是历史模型的真实读取频率。
- [完整性清单](integrity.json) 校验 14 个 job、12 个记忆/证据实现文件、screen 和归档索引，共 28 个输入/源码文件执行前后 hash 不变。脚本阻断 socket 连接；provider、工具执行和 simulator 调用均为 0。

原命令均含“多机 marathon flight at N meters”导语。R3 能识别其后显式路线，但 v7 compact 资格要求路线 span 覆盖全部非空白命令；未覆盖导语触发回退。本轮保留导语，不扩大语法，也不把删除导语后的派生命令算作原题。

## 真实公开回执：回退一致与证据缺口

| 原任务（场景/任务） | 归档回执 / 读取点 | 最终有序坐标前缀 | 有到达回执的坐标 | 路线后 landing 条款 |
|---|---:|---:|---:|---:|
| `4aa67fd7/d4e2d192` | 27 / 28 | 24/24 | 24/24 | 2/2 |
| `4aa67fd7/bf0fda5d` | 30 / 31 | 24/24 | 24/24 | 2/2 |
| `3ec31656/a803550e` | 22 / 23 | 12/24 | 23/24 | 1/2 |
| `640b66d4/0cc74972` | 42 / 43 | 2/24 | 22/24 | 0/2 |
| `09aae7d9/91701224` | 37 / 38 | 1/36 | 26/36 | 0/3 |
| `5aabad33/0cfbbd09` | 19 / 20 | 0/24 | 22/24 | 0/2 |
| `9ddae16c/2106aa9c` | 28 / 29 | 24/24 | 24/24 | 2/2 |
| `9ddae16c/63491937` | 26 / 27 | 24/36 | 35/36 | 2/3 |
| `bad1e9e5/3f5f136a` | 17 / 18 | 12/24 | 23/24 | 1/2 |
| `056a1f86/0e9c6f95` | 20 / 21 | 12/24 | 23/24 | 1/2 |
| `056a1f86/8944f7ec` | 19 / 20 | 36/36 | 36/36 | 3/3 |
| `597d7169/be1e9b29` | 11 / 12 | 24/24 | 24/24 | 2/2 |
| `e76728cc/f02b6ab7` | 19 / 20 | 24/24 | 24/24 | 2/2 |
| `c5fb45ca/f922aa84` | 38 / 39 | 0/24 | 19/24 | 0/2 |
| **合计** | **355 / 369** | **219/372** | **349/372** | **18/31** |

这里的坐标覆盖只表示 ledger 能从公开动作回执引用该 actor 的到达证据，不是搜索面积覆盖。31 条路线中，18 条有完整有序前缀，11 条前缀为 0，2 条为部分前缀。23 个坐标没有可认定到达回执；另外 130 个坐标虽有后续到达证据，仍不会越过前面的证据缺口计作有序完成。

只有 6/14 题在此解释下具有全部路线和 landing 条款；**这不是 B0 重评分，也不说明其余 8 题历史执行失败**。原 B0 checker 的目的、容差、档案可见性以及本次空初态/串行解释可能不同。本次没有执行原 checker 或检查其隐藏状态。14/14 题仍将模板导语保留为 `unverified`，不得以条款齐备替代整题完成结论。

归档投影共保留 132 次公开观察尝试：85 次局部 `empty_targets`、46 次 `targets_observed`、1 次 `failed`。失败被保留；局部空结果没有变为全局“不存在”的结论。369 个读取点都保留 resume；两版字符总量均为 774,692，最大单次均为 2,366，低于设定上限。逐前缀完整输出见 [archived-replay.json](archived-replay.json)。

## 合成派生探针：独立语义预期

每题按原文本 source span 原样提取 route blocks，另造去掉导语的新指令；actor 名称和 ID 来自该题归档公开 `list_drones`。下表的预期直接来自原任务坐标顺序、指定 actor 与手工构造的公开回执规则，不以 v5 输出作为唯一真值。脚本 `synthetic_oracle` 独立遍历返回的 `successful_points`，不调用 ledger 的 route matcher 或 `receipt_points`；所有后续读取点均与该 oracle 对照。该 oracle **复用生产 `explicit_routes` 已解析出的条目**，仅对轨迹状态归约独立实现，未独立证明自然语言解析的完整性；主样本导语仍保留为 unverified。

| 合成类别（各 14 例） | 独立预期 | 实际最终语义 | 回执 / 读取点 |
|---|---|---|---:|
| `partial_prefix` | 每机只返回原顺序前 6/12 点；后 6 点未完成，未落地 | 31 条前缀均为 6，landing 0 | 45 / 59 |
| `wrong_actor` | 将各路线回执给下一 actor；不得归给要求它的 actor | 31 条前缀均为 0，landing 0 | 45 / 59 |
| `out_of_order` | 返回第 1 点后跳过第 2 点，随后返回第 3–12 点；后续覆盖不能填补第 2 点缺口 | 31 条有序前缀均为 1，landing 0 | 45 / 59 |
| `complete_then_land_without_coordinates` | 返回全部有序点，然后成功 land；landing 可确认，位置仍未知 | 31 条前缀均为 12，landing 31；31 个 terminal 位置均 unknown | 76 / 90 |
| `landing_then_unknown_action` | 完整路线并 land 后追加效果未知的 take_off；旧 landing 不能证明当前终态 | 路线前缀保持 12，landing 及 terminal 当前确认均为 0 | 107 / 121 |
| `empty_failed_unknown_observation` | 公开状态后给出完整空感知、失败、格式不足三种回执；感知不证明路线到达 | 每类观察状态各 14 次；31 条路线前缀均为 0 | 70 / 84 |
| **合计** | **84 个独立合成例** | **472 个读取点无检查失败** | **388 / 472** |

检查还包括：actor 及坐标 source span 对应原始文字；每个可见 source 引用存在且属于当前 command；到达证据对应 actor、事件及原始回执字段；有序证据按事件/回执点递增；可见完成/剩余行对应完整账本；剩余目标按原顺序显示且明确 `goals_omitted`；unverified 截断及行省略数量正确；不产生整题完成裁决。初始读取点尚未收到公开 actor 列表，仅做一致性及来源/预算检查，独立 actor oracle 从首条公开列表之后开始。

| 合成类别 | v5 字符总量 | v7 字符总量 | 变化 |
|---|---:|---:|---:|
| 半程 | 118,871 | 101,029 | −15.01% |
| 错 actor | 113,290 | 92,357 | −18.48% |
| 乱序缺点 | 120,646 | 102,897 | −14.71% |
| 完整路线后无坐标落地 | 164,846 | 139,883 | −15.14% |
| 落地后未知动作 | 219,913 | 179,266 | −18.48% |
| 空/失败/未知感知 | 165,332 | 139,806 | −15.44% |
| **合计** | **902,898** | **755,238** | **−16.35%** |

12/472 个读取点变长，分布在半程与乱序各 6 点；最大增量为 218 字符。最大单次长度为 v5 2,447、v7 2,460，均在 3900 上限内；两版 472/472 个读取点均保留 resume。总字符是等权累加每个审计前缀，不代表模型实际消费。完整合成输入及输出见 [synthetic-probes.json](synthetic-probes.json)。

## 失败、警告及保留能力

本次失败清单为空：真实组 0/369、合成组 0/472 读取点出现检查失败；两版 observer error 总数均为 0；公开 actor 来源缺失为 0；输入/源码完整性失败为 0。该结论只覆盖脚本列出的断言。

仍须保留的警告：

1. 原文 compact 覆盖 **0/14**；本批没有原任务 compact 性能证据。全部模板导语仍为未验证条款。
2. 真实回执存在上述 23 个坐标证据缺口和 13 条非完整路线；不能据空索引离线解释改判历史 B0 成败。
3. 缺少原始调用 ID、并发时序及真实预载状态，不能验证跨调用关联、真实并发或上下文重建后的等价性。
4. 本批是已有 B0 材料，先前已被分析；不是盲测或独立未见测试。局部几何合法不保证连续路径、电量、时间或整题可执行。
5. 合成回执不证明动作在仿真器中可执行；字符总量减少也不是逐点压缩保证。固定动作回放不能证明模型会正确使用记忆、减少调用或提高成功率。
6. 来源引用及合成 oracle 提供定向忠实性检查，未穷举所有真实工具返回格式、schema 漂移、记忆截断和状态冲突。原始回执字段的独立对照针对路线到达 proof；可见 `landing.position`、`position_source`、`checked_here`、`last_query` 内容及 `later_observed` 没有逐字段独立 raw-source oracle。无坐标落地断言检查完整 terminal 投影，未另对实际模型视图的 landing position 建独立 oracle，不能宣称全部可见事实均已独立证明。未运行在线对照。
7. 可见行检查比较已有字段的值，不是完整 schema 必填字段断言；缺失整个 actor/source 等字段可能不被该检查发现。无 resume 时也不会进入这一组检查，虽然本次全部 369＋472 个读取点实际都有 resume。此类审计器覆盖缺口不等于已发现生产回归。
8. 28 项封存覆盖主要算法文件与输入，没有封存全部传递导入依赖；`task_evidence_r2/r3` 导入的 `task_evidence_context.py` 未纳入执行前清单。本轮不实例化或调用其 middleware，仍应把它视为复现依赖记录缺口，不能事后宣称已做完整依赖冻结。

可保留为候选的能力是：普通任务精确回退 v5；在严格 route-only 输入内，压缩视图维持本次验证的 actor、顺序、完成/剩余、unknown、landing 及可追溯证据语义，并明确省略。原开发集 v7 token 结果与本报告分开解释，不合并计算收益。

在线前仍需先固定真实目标任务族，确认有 compact 资格且有实际使用需求；保存公开初态、调用 ID/并发及来源信息；再冻结任务、模型、预算、初态、评分和自然决策协议，对 v5/v7 做受控验证。当前证据不足以自动扩语法、切换默认或启动大批在线实验。本轮到此结束。

## 复现与文件

脚本沿用仓库既有 `experiments/analysis/` 组织方式：[audit_execution_memory_v7_cross_task.py](../../../experiments/analysis/audit_execution_memory_v7_cross_task.py)。依赖 Python 标准库与仓库内 execution_memory/task_evidence 模块及其导入；本次使用现有 `agent4drone/.venv/bin/python`。**先把协议和队列复制到新目录再复现**；脚本默认输出会覆盖本次结果，不应直接使用默认输出重跑。以下命令从仓库根目录运行：

```sh
audit_output="$(mktemp -d /tmp/execution-memory-v7-replay.XXXXXX)"
cp docs/results/execution-memory-v7-cross-task-20261023/PROTOCOL_CN.md docs/results/execution-memory-v7-cross-task-20261023/cohort.json "$audit_output/"
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python experiments/analysis/audit_execution_memory_v7_cross_task.py --output "$audit_output"
```

脚本读取所选输出目录中的冻结队列；任何已冻结输入/实现 hash 变化都会中止。新输出目录保留此次记录，不需要重新调用在线服务或重新生成轨迹。

**验收不能只看进程退出码。** 当前主函数在记录到语义 `issues` 时仍会写出结果并正常返回；复现者必须检查 `summary.json` 中两组 `issues` / `readpoints_with_issues` / `observer_error_count`、`missing_synthetic_sources` 及完整性状态。本次这些失败计数确实全部为 0、来源缺口列表为空。

- [summary.json](summary.json)：两组分母、长度、失败数和边界。
- [run.log](run.log)：一次执行日志。
- [integrity.json](integrity.json)：28 个输入/源码文件及协议、队列、脚本 hash。
- 冻结队列 SHA-256：`26d97a8aaa34ef8d136297baff1422cefc73a83c89c0be7c3487c4128f60a5a9`。
- 运行脚本 SHA-256：`c5ad0b70e2bfa580d2af70b7e74047ccfbf0b0e7770d11ba64c653696690a45d`。
