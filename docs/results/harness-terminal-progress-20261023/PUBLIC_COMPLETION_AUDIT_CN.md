# terminal_progress_v6 公开完成证据独立审计

2026-10-23。只读核验已封存的 [14 次配对执行](run-20261023-01/summary.json)，未重跑仿真、模型或测试，未修改源码及封存目录。[机器记录](PUBLIC_COMPLETION_AUDIT.json)共 **233 项检查，0 项失败**；消费的 59 个文件均与 campaign seal 一致，审计结束回读 hash 未变。完整初态、全量封存和成本审计由[另一份独立审计](independent-verification.json)记录。

七对的动作请求顺序、actor、参数、提交标志及回执状态全部相同：每臂 438 次动作尝试；记录到的 411 个物理效果，其 actor、起终点、几何元数据与路径长度也逐项相同。此比较明确排除时钟、持续时间和耗电，不声称完整物理日志逐字相同。`b1ad4f3e` 为 191 次动作、181 个物理效果，cursor=176、units=176、repair=10 均不变。去除新增的终止读取后，两臂整个 task 请求序列一致；其余六对没有终止确认记录，API 与动作序列未改变。

## b1ad 的公开证据链

来源：[v6 原始公开轨迹](run-20261023-01/trials/b1ad4f3e-terminal_progress_v6/public-api.jsonl)、[Harness 事件](run-20261023-01/trials/b1ad4f3e-terminal_progress_v6/harness.json)。

- 技能 #772 读取 95%（19/20），其后动作 #774、#779、#783、#787、#800、#807 与 v5 相同；最后一个 running frame 仍缓存 95%，cursor 已为 176。
- 新增技能 #834 是已提交、无动作的 `GET /sessions/current`，来自同一 plan。原始 session `35ce64d8` 明确返回 100%、20/20；按冻结 `public_progress` 投影后，摘要为 `6c601a4d8aaeeedc7595a5b5fd8709d4731f7242c39a18a3f7156f4cf02b7ade`，与调用 818 的 `public_call_end.receipt_sha256` 完全一致。start/end 与 frame 的 `evidence_ref=e1635` 一致。
- 事件顺序为 **read start 2810 → read end 2811 → STOP 2812 → completed frame 2813 → skill exit 2814**。没有提前完成、确认后动作或 cursor/repair 增量。最终为 `completed / skill_public_stop_condition`，缓存从 95 更新为 100。
- 技能 API 817→818，含评估端公开读的 task API 818→819；唯一新增请求就是上述技能读取。仍在原 2,000 API 上限内，STOP 时原 deadline 尚余约 497.20 秒，没有延预算。评估端 #835 是之后的独立读取，不是策略确认来源。

本批没有保存 normalized ledger 正文。因此本审计采用“**原始公开 session → 冻结适配器投影 → Harness 调用顺序、摘要与 evidence ref**”的方法，不声称直接读取了 ledger 正文。除新增终止记录、公开停止标记和进度缓存外，frame state 在排除跨臂 plan ID 与回执时间戳后相同；终态重复 start/resume 的 before/after 控制快照亦不变。

## 结论与边界

未发现阻断问题。本批支持一个固定脚本任务的**完成反馈修复**：v5 已到 100% 且原 checker 通过，v6 用自身公开读取确认后正确停止。它没有增加目标访问或整题通过数，不能据此宣称搜索能力、效率、自然模型选择能力或泛化提升。[能耗诊断](energy-reproducibility-diagnosis.json)记录了真实差额：24ac 的 5 处、b1ad 的 1 处对应跨整数秒的环境推进。这里没有把物理回执中的耗电或电量差额判为相等，frame state 的窄比较也不代表整个世界状态或电量相同。

真实 14 次执行中只出现 **1 个 fresh >95 的终止确认案例**，其余 6 对没有触发。fresh ≤95、读取失败/无效、预算及生命周期不足的三类边界仍属于[此前 61 项专项代码测试](TERMINAL_PROGRESS_TESTS.json)的证据，不能写成已被本批真实任务全部覆盖。
