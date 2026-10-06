# DeepSeek Flash 正式中止实验：Harness 失败案例簿

本文件只分析正式 attempt `20261006-115922-harness-pilot-3f71f966`，协议 `harness-paired-deepseek-pilot-v1`，运行代码 `da62262`。它记录的是用户主动停止后的部分结果，**不是完成的 60 题成功率报告**。文档生成后不修改原始实验目录。

## 证据边界与计数口径

- [suite.json](./experiments/runs/20261006-115922-harness-pilot-3f71f966/suite.json) 的最终状态为 `invalid`，结束时间为北京时间 2026-10-06 12:51:07；两臂错误均为 `InterruptedError: Experiment interrupted`。停止发生时 B1 当前题 `23a9d67d`、Harness 当前题 `018d1d05` 缺少完整 worker 收尾证据，不能混入已完成案例。
- 原计划每臂 60 题、3 场景。本次只有第一个场景 `ad34350a` 出现完整计分记录：B1 18 题、12 题通过；Harness 11 题、0 题通过。其余未执行、中断项仍保留在原计划账本中，未删除或补跑。
- 本案例簿只深入分析 Harness 的 **11 条 `status=scored`、`continuation_safe=true`、`world_quiescent=true` 且独立 `agent-state.json` 中 `worker_finished=true` 的记录**。相同 task_id 的 B1 为 **8/11**，Harness 为 **0/11**。这只是同题已完成子集对照；它不是新 Harness 的 60 题最终通过率，也不应视为 11 个独立随机样本。
- Harness 这 11 题没有超时：10 题 `completion_status=incomplete`，1 题 `contract_rejected`，全部 `success=false`。没有“外部判题通过、内部只是不肯确认”的正式案例；11 题均未完成外部要求。
- 两臂同一场景按原序运行，后续环境状态可能随此前行为不同而分化。本文件以同一任务内的代码阻断和工具证据定位原因，不据此把所有差异解释为完全隔离的单因素因果效应。
- 冒烟的 3 题（B1 3/3、Harness 1/3）来自另一 attempt，不参与任何本文件计数。冒烟中曾有外部通过而内部未完成；**这不能用来解释正式 11 题的失败**。

原始总表：[B1 results](./experiments/runs/20261006-115922-harness-pilot-3f71f966/b1/results.json)；[Harness results](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/results.json)；[部分结果汇总](./experiments/runs/20261006-115922-harness-pilot-3f71f966/summary.json)。

## 逐题结果与执行程度

下表“动作”按 `trace.tool_receipts` 中 `is_mutation=true` 的工具收据计数，包括 completed、blocked、partial；一次复合工具可能调用多个底层命令，不把这些底层命令再重复计数。读取状态和感知不属于 mutation。

| task_id / 原始记录 | 指令简述 | B1 | Harness 实际动作 | 直接失败位置 |
|---|---|---|---|---|
| [b1eb0270](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/b1eb0270.agent-state.json) | 起飞 14 m → 朝向 353° → 悬停 | 通过 | 1 次起飞 | 谓词写成 state altitude；高度已到达却永久 unknown，旋转被前置条件拦截。 |
| [64b6560e](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/64b6560e.agent-state.json) | 起飞 14 m → 检查点 → 悬停 | 通过 | 1 次起飞 | 谓词 state altitude / state position 不受支持；后续导航被起飞高度前置拦截。 |
| [91e5a616](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/91e5a616.agent-state.json) | 起飞 25 m → 起点 → 终点 → 降落 | 通过 | 1 次起飞 | action_completed 带 target_altitude_m 多余参数，被判 unknown；另有 takeoff / take_off 命令名不一致。 |
| [95fef67d](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/95fef67d.agent-state.json) | 多航点飞行后降落 | 未通过 | 0 次 | 两轮合同审查分别要求补起飞高度、落地状态证据；contract_rejected，未进入行动。 |
| [89129044](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/89129044.agent-state.json) | 起飞 19 m → 指定点 → 悬停 → 拍照 | 通过 | 起飞 + 再次改高度 | state_altitude 不受支持；重复查询和调高不能修复固定合同，导航一直被拒。 |
| [3ce5d9bf](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/3ce5d9bf.agent-state.json) | 起飞 41 m → 观察点 → 悬停至少 19 s | 通过 | 1 次起飞 | state_altitude / state_position 错名；查状态后仍不能导航，悬停未执行。 |
| [529c0622](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/529c0622.agent-state.json) | 访问两个目标并拍照 | 未通过 | 0 次 | 探索步骤未填 condition_ids；整份计划在执行前被拒，合法起飞也没有执行。 |
| [3f601822](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/3f601822.agent-state.json) | 按序访问三个目标，各悬停拍照 | 通过 | 1 次起飞 | 先受高度谓词错名阻断；探索被 active focus 限制，后续又因空 condition_ids 被拒。 |
| [6294fe84](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/6294fe84.agent-state.json) | 取货点降落 → 再次起飞 → 送货 | 通过 | 0 次 | 起飞步骤绑定起飞事件及其高度后置条件；系统把后者依赖的起飞事件当执行前置，形成自锁。 |
| [53357761](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/53357761.agent-state.json) | 飞到中继点 → 广播至少 3 次 | 通过 | 7 次；到点后仅广播 1 次 | 广播语义被编译为 unsupported；第二、三条依赖该条件，第一次发送成功后仍被拒。 |
| [fa30ebff](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/fa30ebff.agent-state.json) | 两机起飞、定位与编队 | 未通过 | 0 次 | 单一 repair scope 只激活 Drone 19 条件链；包含 Drone 6 起飞的整份计划被判越界。 |

11 题中 **4 题没有 mutation**，**6 题只起飞或起飞后重复调高度**，只有通信中继题完成了水平导航并广播一次。合计 **14 次 mutation 工具调用**，不是 14 次成功行动：其中中继题含一次 blocked 导航、三次 partial 路径移动。

共 60 条 `trace.plans` 记录，55 条存在非空 `error`。**不能写成“55 条计划完全没执行”**：运行时前置条件失败可出现在已执行合法前缀之后，例如起飞执行后导航被拒。总计 96 次 `trace.model_calls`。10 份已安装合同中，7 份至少含一个状态机不接受的非 `unsupported` 谓词名称；合同拒绝题没有已安装合同，不进入这个分母。

## 五条代表性因果链

### 1. b1eb0270：实际已起飞，合同语法错误却否定了高度证据

原指令为：“Drone Drone 3 takes off to 14 meters altitude, then rotates to face heading 353 degrees and hovers.”

合同 c1 的 `kind=state`，但 `predicate="state altitude"`。工具收据显示 `take_off` 已完成，随后的 `get_drone_status` 返回 z=14；外部检查中高度也通过。内部 `harness_state.evaluation.conditions.c1-altitude-14m` 却为：

```text
status: unknown
reason: unsupported predicate: state altitude
```

状态机只认 `altitude`，不认带 kind 前缀的字符串。旋转步骤依赖 c1，因此代码不断抛出 `has unmet required conditions`，最终三轮连续无有效执行后退出。物理动作能力并未失败，**合同接口错名经硬前置规则放大成真实的旋转未执行**。B1 同题通过。

### 2. 91e5a616：编译阶段接受了执行时必然无效的参数

任务要求起飞 25 m，经给定起点到达终点并降落。第一轮实际执行了 `take_off` 和状态查询，接着导航被 c1/c2 前置阻断。c1 为 `action_completed`，但参数同时包含 `command="takeoff"` 与 `target_altitude_m=25`。

运行时只允许该谓词的 `{command}` 参数，所以 c1 被评为 `unsupported predicate kind or parameters`，这是已发生的直接阻断。实际事件名为 `take_off`，合同为 `takeoff`，源码又执行逐字匹配；**即使只删除多余参数，命令别名问题仍未解决**。这一句是从确定的比较逻辑推导出的反事实，不是本次执行过的修复结果。

模型此后提交的导航路线并非没有动作思路，而是系统不允许执行。B1 同题通过。

### 3. 6294fe84：执行器要求“起飞前证明已起飞”

任务要求先飞到取货点降落，然后再次起飞送达并降落。首个 `take_off` 步骤的 `condition_ids` 同时列出 c1（起飞事件）与 c2（起飞后高度），c2 依赖 c1。该步骤自身 `requires=[]`，模型没有主动要求预先起飞。

执行器将每个目标条件的依赖一律加入动作前置集合，于是 c2 的依赖 c1 成了 `take_off` 的调用前置。错误为：

```text
Step s1_takeoff_15 has unmet required conditions: ['c1_takeoff_first']
```

连续三份计划皆如此，mutation 为 0。这是**后置结果与执行前置混淆造成的自锁**，不是环境阻挡或工具失败。B1 同题通过。合同另有 `takeoff` 命令错名及不可验证交接语义，但它们不需要先发生就已被此自锁截断。

### 4. 53357761：不能验证广播，最终变成不能继续广播

任务要求到达 `(542,421,19)`，全程至少广播三条消息。此题正确使用 `altitude` 与 `position`；在一次 blocked 导航及三次 partial 路径之后，第四次路径移动 completed，新鲜观测证实到点。说明在合同接口正确时，这套运行时并非绝对不能推进导航。

c3“发送广播以协调其他无人机”和 c4“总计至少三条消息”均被编译为 `unsupported`，c4 依赖 c3。第 11 轮第一条广播 completed；第二条广播被 `has unmet required conditions: ['c3']` 拦截。后续三轮仍然被拦，最终只发送一条。

这是**证明能力不足阻止可执行动作**的直接实例：可以如实说明协调效果未被证明，但该不确定性不应自动阻断发送剩余消息。B1 同题通过。没有依据把这个失败解释为纯粹“内部完成判定更保守”。

### 5. 3f601822：导航被挡后，探索又被另一层规则挡住

任务要求按序访问三个目标，每处悬停并拍照。模型首份计划是起飞、飞往已经观察到的第一个目标、悬停；实际只完成起飞。合同高度写成 `state altitude`，后续导航被挡。

随后模型试图探索未绑定目标，两次收到 `Exploration must address an active unresolved entity: c_reach_circle_target_1`，再下一份探索计划因 `Mutating step s2-explore-corridor must refer to a task condition` 被拒。整个任务没有导航或照片动作。B1 同题通过。

确定事实是多种门控连续拒绝了动作；“这些规则形成无法脱离错误合同的封闭恢复路径”是基于该轨迹和合同固定机制的解释。不能由此断言删除其中任意一条规则就一定完成整题；未绑定目标、unsupported 访问与悬停、照片参数等仍可能造成后续问题。

## 其他已核实机制

- **全计划准入拒绝扩大损失。** `529c0622` 的合法起飞与未满足schema的探索放在同份计划中，验证探索失败使起飞也没有执行。`fa30ebff` 合法的两机起飞被单focus修复范围拒绝，三轮零动作。它们在 B1 也未通过，故不把它们称为新增回归成功对照，但可直接定位新增架构阻断。
- **同模型审查缺乏运行时语义检查。** 审查多次要求把高度容差从 1.0 收紧到 1e-6，或把悬停标为 unsupported，却批准了状态机不能执行的谓词拼写与参数。`89129044` 编译首版的悬停位置描述被审查要求改为 unsupported；修改后的条件又成为照片的前置。这是审查在“证明完备”方向强化合同的事实，不是其提高任务成功率的证据。
- **备注与真正未决条件混在一起。** `unresolved` 中包含排序说明、坐标是用户明确给定而非实体绑定等解释性文字；源码只要该列表非空就阻止完成。它是确定的潜在完成障碍，但本批尚未有任务执行到外部全部通过，因此不能将其计作此次 11 个实际失败的唯一原因。
- **Memory 已读到事实，却不能改正合同。** 高度错名案例即使有新鲜状态查询也仍 unknown；继续查询和重新起飞没有解决路径。不能把它们归为“观测不够”，也没有证据说明跨任务 Memory 污染是这些失败的主因。

## 源码定位：仅限正式运行归档

当前工作区已按用户要求回退 B1。下列文件名与行号均来自正式 attempt 的 `source.zip`，**不是当前工作区源码位置**。阅读归档不向工作目录恢复或执行旧代码。

源码归档：[source.zip](./experiments/runs/20261006-115922-harness-pilot-3f71f966/source.zip)。

| 归档内路径及行号 | 与证据对应的实现 |
|---|---|
| `agent4drone/harness/models.py:34,36` | `predicate: str`、`params: dict[str, Any]`；没有有限谓词及对应参数的联合类型。 |
| `agent4drone/harness/models.py:58` | `validate_for` 验证结构、引用、引文与逻辑树，未验证谓词运行时可执行性。 |
| `agent4drone/harness/planner.py:28–32` | 提示以“state altitude”列举能力，未明确 kind/predicate 的字段拆分。 |
| `agent4drone/harness/planner.py:194–248` | compile/review 传入指令、context、合同/schema；不同于 plan，没有传入工具描述目录。 |
| `agent4drone/harness/state.py:587` | 谓词参数包含白名单之外字段，直接评为 unknown。 |
| `agent4drone/harness/state.py:510` | `event["command"] == command`，命令名逐字匹配。 |
| `agent4drone/harness/executor.py:692–697` | 把 step 所关联条件的 depends_on 加到调用前置集合，未区分本步同时产生的后置结果。 |
| `agent4drone/harness/controller.py:65` | `contract.unresolved` 非空即阻止确认完成。 |

## 预算与上下文：次要影响，不能替代直接原因

以下只比较共同已完成的 11 个 task_id。tokens/calls 取 `results.json.observed_token_usage`；

| 指标 | B1 | Harness |
|---|---:|---:|
| 模型调用 | 213 | 96 |
| 可观测总 token | 21,496,501 | 13,563,614 |
| job 时长合计（秒） | 1552.17 | 2921.53 |
| job 时长中位数（秒） | 106.22 | 251.66 |

不能据此声称 Harness 总 token 更多。简单旋转题两边均 7 次调用，B1 86,117 tokens，Harness 971,621 tokens；Harness 每次 prompt 为约 12.5–13.5 万 tokens，compile 与 review 的 completion 合计 25,797 tokens。该题的请求上下文显著变大，是可观测事实；但没有做上下文大小消融，不能量化它对失败的独立贡献。正式 Harness 11 题没有超时，首先应该处理可复现的合同/门控错误，而不是把这批失败归因为预算耗尽。

## 原始任务与执行轨迹映射

`task_id` 是实验任务 ID；`execution_id` 是新 Harness 每次调用生成的执行 ID。`agent-state.json` 中另有内部 `task_id`，不要混用。下表 trace 链接均指向正式实验中已保存的原始文件。

| 实验 task_id | Harness execution_id | 原始 trace | 原始外部判题 |
|---|---|---|---|
| `b1eb0270` | `1c5f9804-29e1-47d9-96cf-d7b6c9a60453` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/1c5f9804-29e1-47d9-96cf-d7b6c9a60453.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/b1eb0270.checks.json) |
| `64b6560e` | `a8e43102-6491-4da0-824b-5fa5f17ed01f` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/a8e43102-6491-4da0-824b-5fa5f17ed01f.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/64b6560e.checks.json) |
| `91e5a616` | `830b8320-b1ac-4d91-9662-42c93308195f` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/830b8320-b1ac-4d91-9662-42c93308195f.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/91e5a616.checks.json) |
| `95fef67d` | `7d53772c-4fec-4905-b5ec-48326d2c3841` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/7d53772c-4fec-4905-b5ec-48326d2c3841.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/95fef67d.checks.json) |
| `89129044` | `91385c7e-e4df-440f-9fa2-2fe7f4b8bd98` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/91385c7e-e4df-440f-9fa2-2fe7f4b8bd98.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/89129044.checks.json) |
| `3ce5d9bf` | `10d67ea5-3ea8-46b5-93d7-e875fe065e31` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/10d67ea5-3ea8-46b5-93d7-e875fe065e31.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/3ce5d9bf.checks.json) |
| `529c0622` | `2965103f-0865-4c38-933c-80bff9b1df5b` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/2965103f-0865-4c38-933c-80bff9b1df5b.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/529c0622.checks.json) |
| `3f601822` | `df3cb584-0315-4d64-b2c0-d37c6e4e783f` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/df3cb584-0315-4d64-b2c0-d37c6e4e783f.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/3f601822.checks.json) |
| `6294fe84` | `115c6dbf-939f-40d3-9a9b-922742abcd52` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/115c6dbf-939f-40d3-9a9b-922742abcd52.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/6294fe84.checks.json) |
| `53357761` | `92dc7394-e258-44a0-a4a4-397effc23716` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/92dc7394-e258-44a0-a4a4-397effc23716.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/53357761.checks.json) |
| `fa30ebff` | `9a8f7b81-192a-41f4-801b-b0e817260bc1` | [trace](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/agent-traces/harness/9a8f7b81-192a-41f4-801b-b0e817260bc1.trace.json) | [checks](./experiments/runs/20261006-115922-harness-pilot-3f71f966/harness-v2/ad34350a/fa30ebff.checks.json) |

## 对下一步的含义

证据支持的判断是：新版过早把“证明所有语义成立”放到了“允许执行”的上游，且合同编译器、状态判定器、执行器没有共享一致的严格接口。错误合同通过审查后固定下来，多层门控忠实地放大错误，阻止了模型原本已经提出的可执行动作。

回到 B1 后，优先以旁路形式重新验证观测、日志和诊断；若以后重引入合同，先证明其词汇、参数、命令与工具完全匹配，区分行动前置、行动后置和不可验证语义，并为错误合同提供可控修订路径。上述建议不是已验证有效的修复，也不授权自动修改架构或重新发起模型实验。
