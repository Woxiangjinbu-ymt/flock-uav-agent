# observed_target_v7 实现独立审查

日期：2026-10-24。结论：**发现的三项契约问题均已修复，当前实现未发现阻断本批冻结验证的问题。** 此结论限于代码、纯函数反例及专项测试证据，不是实际任务收益、自然 Agent 行为或泛化结论；审查者未改生产或测试、未调用模型或仿真器。

审查范围为 [observed_target_search.py](../../../agent4drone/observed_target_search.py) 与 [skills](../../../agent4drone/harness_skills.py)、[core](../../../agent4drone/harness_core.py)、[integration](../../../agent4drone/harness_integration.py) 的 v7 接线，并对照 [原策略](../../../agent4drone/coordinated_search_r2.py) 及上一封存 v6 源码。复核版本与 [95 项专项记录](observed-target-unit.json) 的六份源码/测试文件 hash 一致；专项由另一代理执行，本审查未重复累加通过数。

## 发现与修复

| 发现 | 具体反例与影响 | 已复核修复 |
| --- | --- | --- |
| 旧 grid 索引 → 新定向索引绕过 tried | 同 actor、同起点已尝试 grid index 0 的 `(35,35)`；新目标同 XY 被 append 成 index 9，原实现仍会选择，绕过路径尝试限制 | 定向选择在 append 前按 actor、当前起点、旧索引实际 XY 检查 tried。纯 helper 反例现在返回 None；换起点仍遵循原路径规则 |
| 无效几何/类型导致同名歧义漏记 | 合格 `t1/Fixed Target 1` 与不同 ID 的同名目标同时可见，后者坐标缺失；原实现丢弃后者，误把前者当唯一身份 | `observed_name_ids` 独立记录规范名称与 ID/未知 ID，不依赖几何或 fixed 资格；多 ID 或未知身份永久禁止该名称下已登记目标的本计划定向。已复核坏坐标、非 fixed 与未知 ID 情形 |
| 新定向索引 → 旧 grid 别名再次尝试 | 原 grid index 4 为 `(105,105)`，定向 append 为 9；路径 blocked 且位置未变后仅记 tried index 9，普通 choose 排除 9 却再次选 4 | 仅 v7 的普通选择额外排除当前 actor/当前起点 tried 目的点对应的全部索引别名。不会把路径错误推广到其他起点或 actor；专项通过真实技能选择/accept 路径覆盖双向别名回归 |

前两类与反向别名均先以原确定性 helper/policy 复现，无模型、仿真器或网络。修复由根代理实施；[专项测试](../../../agent4drone/tests/test_harness_observed_target.py) 的95项包含这些回归及运行时边界。它们是代码测试，不新增真实任务样本。

## 核验的契约

- **来源和身份：**只从当前 plan 的有效公开 nearby 及其 evidence_ref 登记 fixed、有限 XYZ 和规范名称，不从继承黑板取目标。首个合格公开观察者成为 owner；不在 actor 间转派。同 ID 的位置/name/type 变化，以及同名歧义，禁用后不会因之后旧值重现而恢复。
- **数值和限制：**使用当前 fresh 状态的 task_radius，拒绝 bool、非数值、非有限值与非正数；固定命令 z=20，只在 `abs(20-target_z)<=task_radius` 时适用。公共障碍、shared exact XYZ、同 actor/起点 tried 几何守卫保留；只尝试观察坐标，不保证目标仍在或真实可达。
- **索引与覆盖：**目标 XY 对应独立稳定索引，candidate_cover 按真实目的点计算；普通探索排除这些定向索引。成功后仍由既有 accept 更新实际位置、selected 与覆盖，不把未执行的 fallback grid 当成完成。双向索引别名均已处理。
- **预算与运行：**定向选择替换正常 actor 槽，不另加动作队列、API 读、轮次或修复额度。PublicCall 仍经过原 API/deadline/cancel/scene-epoch 守卫、飞行状态检查、效果核验和未知效果不可重放。scheduled 在动作前记入本 plan，每 target ID 至多一次调度，不因暂停恢复而重复。
- **完成与证据：**`is_reached=False` 不参与 remaining 判断。完整成功 move_to 回执需 actor、command、status、command_outcome 及规范名单匹配，才记录名称抑制；它不建立 target ID 完成事实。实际 completed 仍来自原有效公开 progress 严格 >95，整题 checker 单列。
- **旧版边界：**v7 加入 runtime、入口、飞行状态、共享、终态控制和工具说明分支；默认仍 legacy。新选择逻辑受 v7 条件保护，旧 v6 工具描述不变；移动 battery 检查的控制流对原有效 policy 返回等价。原 lattice、评分、PublicSearchPolicy 生产文件与冻结 v6 相同。是否得到相同真实轨迹仍由本批 v6 基准及历史对照核验，不能只凭静态阅读宣称所有结果相同。

## 报告必须保留的口径

当前实现还将 nearby 中 `is_reached=True` 记录为 `positive_flag_ref`，用于保守抑制可选定向；应与成功 move 的名称抑制分别报告。该 true 标记也不产生 target ID completed、remaining 数量或整题成功事实；false 不覆盖已有抑制证据。

`attempts` 列表先记录 `status=scheduled`。takeoff 失败、动作前 API/生命周期阻止，或 `_checked_action` 遇到 unknown 后先退出，都可能使该项没有 `action_ref`。因此必须分别统计调度项与实际发出的定向 move，后者由原始公开请求独立关联；不能用 `len(attempts)` 充当实际动作分母。未知效果时 raw 调用仍保留，不可把缺少 action_ref 写成从未调用或自动重试的理由。

公开 source、动作 ref、执行后位置、正向消息与终止进度需要在后验诊断中独立核对。target 观测只是候选依据；确定性 skill 内部动作与脚本 start 不等于 Teacher 的模型决策。保持同7题、两版本、原预算及所有正负结果，不把本次代码审查或95项测试写成自然模型收益。

## 审查版本

| 文件 | SHA-256 |
| --- | --- |
| observed_target_search.py | `f1d66ee7c449991f2556aeadfd79d2464fd1039402d19e9b2a43239753848b51` |
| harness_core.py | `ae663ef64ba4d35e93e01450bd49790fc082dc455fd3d444bf5ae66fe4a8c3fb` |
| harness_skills.py | `49c239714b83aa60611254be278ebb67451f152dbe9f9766c798a9729a55170f` |
| harness_integration.py | `13bed02a04992b76e2512457bbed28c7d6e2c711433593ddf947a6c6b35284d5` |
| coordinated_search_r2.py | `693093f3ffeba3f5aeaaa3bd747dff5b846f4d8672c02208f90d88366efa4f14` |

冻结前仍须核对最终全量回归、runner 诊断与上述源码无漂移；本审查不代替该执行门槛。
