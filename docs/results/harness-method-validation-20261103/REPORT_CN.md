# Harness 方法定义与本地验证收尾

日期：2026-11-03。当前用户要求暂不进入第五项。本轮完成方法定义、对照协议、代码/数据指纹和有界本地验证；新增真实 provider 请求 0、Teacher 数据 0、权重/训练 0。

## 结论与交付状态

**方法与本地合同可以交付；真实模型效果和论文定稿门槛仍未通过。** 最重要的新结论是：在本批相同子程序、硬守卫、公开反馈和外部事件下，当前 `applicability` 与普通 guarded macro 的控制行为一致。它保留为带守卫的执行组织方式，不单独宣称新调度算法。旧受控实验中的条件性模型往返节省仍有效，但不能证明相对于这类强工程基线的独有优势。

同时补齐了原来仅模块测试的部分完成路径：真实仿真先应用路径前缀，公开回执/状态明确其效果，脚本策略仅对剩余目的点恢复，独立评分确认完成。该结果证明合同能在完整本地链运作，不证明模型自然学会或遵守该恢复策略。

| 本轮产物 | 状态 | 依据 |
| --- | --- | --- |
| 状态、效果、守卫、恢复及共享范围定义 | 已完成；区分确定性规则和模型建议，列出可证伪主张/收缩条件 | [方法定义](../../HARNESS_METHOD_CN.md) |
| 公平评价与完整分母协议 | 已完成；自然整合包、同能力调度和本地合同分轨 | [验证协议](../../HARNESS_VALIDATION_PROTOCOL_CN.md) |
| 控制语义对照 | 12组×3臂=36个新进程全部完成；879项行为核验通过 | [正式结果](mechanism/run-01/report.json) |
| 部分完成恢复完整链 | 最终固定两例、2 worker与2独立scorer；28项核验通过 | [最终结果](partial-recovery-02/validation.json) |
| 最终相关代码回归 | 704项通过，3条既有依赖弃用警告 | [测试日志](regression.log)、[命令与元数据](regression.json) |
| 版本/数据复核 | 源码、依赖锁、验证脚本、fixture及18题已物化输入指纹；prepare/verify通过 | [审查包](review-packet/manifest.json) |
| 新版真实模型、因果收益、独立泛化 | 未执行，不由上述通过替代 | 审查包的相应readiness字段仍为false |

## Confirmed Decisions — 当前候选与方法收缩

配置采用既有v2本地profile：`evidence_harness + evidence_r2 + observed_target_v7 + task_execution_state_v1`，默认 `applicability`，不叠加 `execution_memory_v7`。原工具始终可用。当前是集中式主Agent控制多UAV，不是每机独立LLM协商。

方法明确两层：runtime/技能中的实际执行守卫，以及只读公开状态投影。后者保存requested/applied/remaining与效果未知，提供建议，不自动调用工具，也不是原工具路径的通用防重放拦截器。共享仅在同plan/scene/epoch、静态几何及精确XYZ等合同下转移明确目的点负证据。

本轮未改变生产Harness的调度、搜索或共享算法。新增的是验证器、脚本fixture辅助解析及规格封存工具。后续研究问题收敛到效果确定性、计划依赖、证据作用范围对真实恢复及任务完成的作用；不靠追加模块或后续SFT补写Harness新颖性。

## Verified Results — 控制语义对照

固定[12个案例](../../../experiments/harness-method-v1/method-cases.json)，每例三臂：独立测试用guarded macro、生产`applicability`、生产`failure`。macro共用生产技能/守卫，但不调用 `HarnessRuntime._drive`；F暂停后只由脚本立即resume。三臂均无LLM，使用真实ASGI公开接口；环境时钟固定，不估计模型等待时的动态效果。

这组探针直接驱动runtime，不经过LLM或模型请求中的状态投影；新统一状态的接线另由部分完成完整回合和回归验证。后验checker是同worker内执行后的独立评分函数，不能称为独立scorer进程；后面的部分完成实验才使用单独scorer进程。本轮未做状态投影或跨机共享的效果消融。

| 类型 | 例数 | 实际结果 |
| --- | ---: | --- |
| 正常短链、正常搜索 | 2 | 三臂均完成，无失败检查点 |
| 固定隐藏端点冲突、actor起飞能力不足 | 2 | 三臂最终完成；F各多一个暂停/脚本resume，A与macro持续执行 |
| 未知效果已应用、未应用 | 2 | 三臂均在一次未知动作后读取一次公开状态并退出，无自动重放 |
| actor依赖改变、scene epoch改变 | 2 | 三臂均在后续动作前退出 |
| 局部修复额度、API额度 | 2 | 三臂均按边界退出；修复额度案例F在首次失败后多一次暂停 |
| 额外拍照义务、缺静态合同 | 2 | 不丢弃额外要求，不执行不满足合同的子程序；零动作 |

12组内，A/macro完整公开调用与回执、实际公开API trace、frame、检查点、终态和物理状态一致。F立即脚本resume后也得到同一终态轨迹，只多3次检查点交回。逐次暂停的调用/动作位置与真实failed checkpoint一一对应；不能只按次数判断调度正确。

每臂任务评分为**4/12成功、8/12因预定拒绝或退出而未完成**。879项核验全部通过表示边界行为符合预声明预期，不是“任务成功率100%”。本批没有模型调用/token下降结论；3次交回是程序层机会，不能当作实际节省了3次模型请求。

物理状态比较仅忽略请求历史内的随机request_id及墙钟timestamp，原记录仍保存。固定障碍在初态已存在，首次观察不可见；不使用违反v7静态合同的临时障碍注入。actor/epoch改变另列为故意失效探针。取消、墙钟截止、并发和任意generator不在本批等价范围。

正式批36/36独立worker完成、串行join；[只读复核](mechanism-verification.json)重新计算行为断言并核验封存，共1066项通过。执行方式是工作区源码运行前封存、结束后核对hash未变，未声称正式子进程由封存目录启动。此前[开发预跑](../../../tmp/harness-method-dev-01/report.json)也是固定同12组×3臂，原件保留，不与正式批合并成72个独立样本，也未据成绩替换fixture。

## Verified Results — 部分完成与剩余恢复

新建一个development场景，不使用已有train/dev/test测试集。UAV起飞至20米，沿A=(200,100,20)、B=(400,100,20)执行路径；固定障碍使第一段真实应用、第二段受阻。服务返回真实 `partial_success`，明确成功/未成功点各1。随后公开状态读取确认当前位置为A。

| 最终预定案例 | 决策与实际行为 | 运行结果 | 独立整题评分 |
| --- | --- | --- | --- |
| partial-positive | 从公开partial回执及后续status解析唯一剩余B，使用原有navigate_to公开感知与规划绕障；6段实际恢复运动，未再次执行到A | 正常返回、归档complete；6次Mock模型HTTP请求 | 4/4条件通过，整题成功 |
| partial-negative | 同初态、同partial与status；省略恢复，使用与正例相同的终止文本 | 正常返回、归档complete；5次Mock请求 | 仅2/4条件通过，整题失败 |

28项核验覆盖实际前缀应用、回执分区、模型wire中的partial建议、公开状态绑定、恢复目标仅B、已到达A不重放、同初态、进程隔离及独立评分。模型请求为合成响应，usage也为合成值，不用于效率比较。

`general_agent_local.partial_remaining_point` 仅是脚本fixture辅助：要求真实call ID/actor绑定，明确请求点被回执成功与剩余列表完整分区，后续同actor公开位置与已应用尾点一致；只支持唯一剩余点。它没有接入生产Agent自动决策，也不允许把私有场景/评分读作策略提示。

**失败尝试保留。** [首轮包](partial-recovery/validation.json)计划2槽，实际启动/评分1槽，负例未运行。正例物理恢复及checker已4/4，但最后脚本回复遗漏既有`[TASK DONE]`协议标记，Agent报错并使父进程按合同停批。首轮16项核验有3项全批完整性失败，原件未覆盖。修正两条fixture的同一终止文本、动作序列不变后，以新目录封存最终两例。因此两次尝试总计4个计划槽、3个已运行评分、1个未运行，不能只保留第二批而称从未失败。

## Working Hypotheses / Open Questions

现在可以据实说：执行合同在声明的本地边界内可运行；当前调度属于带守卫持续执行；partial的公开反馈能够支撑只处理剩余工作。仍不能说：新状态提示使真实模型更可靠、整体成本更低、范围化共享具有稳定净收益、当前框架已经获得独立泛化或论文创新性确认。

自然系统评价清单固定为旧开发覆盖18题×两臂×两次重复的72个**拟议**槽，顺序交替/反转，全部 `not_authorized_not_run`。它不是本轮真实模型结果，不是Teacher生产清单，也不是统计功效保证。机制实证的正式场景和独立评价场景尚未冻结，不能拿12个本地合同fixture自动替代。

## Next Actions — 剩余收尾边界

1. 在同一候选上补新版真实provider入口/执行契约验收；明确模型、参数、预算与额度后再封存小批，不复用旧停批授权。
补充原checker未覆盖的义务审计。
3. 在公平故障暴露下验证效果表示/继续与交回/范围化共享；只为实际论文主张增加必要消融，避免弱基线。
4. 在未调参场景上完成与主张相符的评价。若仅局部收益，则按范围交付；若与强基线等价，就把相应部分保留为实现基础。

以上不要求先做SFT/RL，也不自动进入第五项。工程与本地合同收尾不等于论文效果收尾。

## 复核

在仓库根目录使用已有Agent环境；以下三条命令只读、不调用provider：

```bash
agent4drone/.venv/bin/python scripts/validate_harness_method.py --verify --output docs/results/harness-method-validation-20261103/mechanism/run-01
agent4drone/.venv/bin/python scripts/validate_harness_partial_recovery.py --verify --output docs/results/harness-method-validation-20261103/partial-recovery-02
agent4drone/.venv/bin/python scripts/freeze_harness_validation.py verify --output docs/results/harness-method-validation-20261103/review-packet
```

重新执行本地实验须给不存在的新output目录，保留原目录。最终测试的完整参数记录在[regression.json](regression.json)；研究状态、路线、贡献说明、证据索引与导航已同步。更新记录见[维护记录](../../maintenance/harness-method-validation-20261103.json)。
