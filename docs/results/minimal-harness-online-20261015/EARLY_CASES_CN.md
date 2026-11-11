# 在线接入早期案例

这些案例用于检查接口行为，不能替代五臂完整 100 题比较。模型、技能与提示保持冻结，未根据在线案例修改。

## 简单链选择及参数纠错

第一臂 `same_skill_fixed4`，任务 `e59d948d`，场景 `ef36e27a`。原任务要求 Drone 8 起飞至 11 米、旋转至 180 度并悬停。

实际过程：模型查询无人机列表，然后提交 `simple_chain`，但在 `start` 中额外指定了 `altitude`、`drone_id`、`drone_name` 和 `rotate_heading`。接口只接受 action 和 skill，因此返回 `rejected / Unexpected control fields`，此时没有计划提交或技能动作。模型随后使用 `{action: start, skill: simple_chain}`，运行时成功提交一次计划、执行三个原始动作并逐步核对公开状态，最后原 Checker 为 2/2。

该任务有 4 次窗口内逻辑模型调用、两次 start 尝试、一次计划提交。结论限于：模型选中了合适的技能，并能根据接口拒绝回执修正参数。多余参数产生了一次额外交互；本例没有展示暂停恢复或失败后任务接手。不能把两次 start 计为无人机动作重复。

证据：[真实 toolchain](../../../experiments/runs/20261015-154032-harness-same_skill_fixed4-f078526b/ef36e27a/agent-traces/toolchains/)。模型控制事件、计划提交、技能实际 API 和原检查响应分别保留在对应轨迹与场景记录中。

另一简单链任务 `b1eb0270` 也有一次计划提交并完成。两例只能确认真实模型路径已打通，完整成功率、调用减少、耗时和接手有效性仍需等待全部实验。
