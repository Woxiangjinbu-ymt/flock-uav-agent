# 自然 Agent 协议准备：已有失败检查点证据

2026-10-25。只读解析既有封存 JSON/JSONL 和源码字节；没有导入 Agent、运行模拟器、模型、checker 或新任务。可复核脚本：[audit_checkpoint_evidence.py](audit_checkpoint_evidence.py)；完整事件链接、方法及来源 SHA-256：[checkpoint-evidence.json](checkpoint-evidence.json)。原 campaign seal 未改。

**三题共有 27 个“移动受阻后仍在局部修复预算内”的检查点候选：2 / 16 / 9。它们不是已经发生的自然模型暂停或恢复机会。** 来源均为 `observed_target_v7`、`applicability`、脚本显式 start 的旧离线试验，实际模型调用及 paused exit 都为 0。旧 frame 的 `origin=explicit_model_tool_commit` 字样不能改变这个实验事实。

| 原题 | 受阻检查点候选 | 全部错误动作回执 | 第一个公开请求 / gate / cursor | 该脚本轨迹最终状态 |
|---|---:|---:|---|---|
| `7eba7390` | 2 | 2 | request 107 / event 356 / 21 | needs_replan，round_limit_below_threshold；公开进度 66% |
| `24ac1838` | 16 | 17 | request 79 / event 161 / 3 | needs_replan，local_repair_budget_exhausted；公开进度 51% |
| `b1ad4f3e` | 9 | 9 | request 43 / event 96 / 2 | completed，skill_public_stop_condition；公开进度 100% |

来源目录均在 [observed-target 封存批次](../harness-observed-target-20261024/run-20261024-01/trials/) 下的 `<task_id>-observed_target_v7/`。机器记录为每个候选列出 `harness.json` 中的 gate、plan_update、call start/end 序号、call/evidence ID，以及 `public-api.jsonl` 的精确行号和 request ID、动作前后状态、下一真实动作。

**计数方法不是只统计 reason 文本。** 先枚举 `blocked_candidate_handled`，再验证前一 `plan_update`、running 状态、同 plan/cursor、`last_move_outcome=blocked`、公开进度 ≤95、剩余时间 >0、API 未耗尽及 repairs ≤16；用 plan/tool/actor、调用序号和回执哈希唯一关联真实错误 move，并核验动作前后公开位置未变。回执哈希明确区分原 API 正文和适配器仅添加 `outcome` 的客户端正文，源码随来源一起哈希核对。

冻结 `harness_skills.py` 在这些点 yield `Checkpoint(..., failed=True)`；`harness_core.py` 仅令 failure 调度在该点 paused，applicability 则继续。这支持**相同状态到达该分支时**的候选资格；failure 一旦暂停且模型介入，后续路径和机会数不能从旧 A 轨迹外推。27 个相关事件也不等于 27 个独立任务。此方法只统计移动受阻子型，不覆盖所有可能的暂停/接手原因。

`24ac1838` 的第 17 次错误在 yield 前超出 repair budget，直接终止；因此 17 条错误中只有 16 个可暂停检查点。该终止态只能按剩余义务使用原工具接手，不能算同 plan 恢复；旧档案只支持进程内 generator 恢复，不能把已结束 run 的 JSON 当跨进程可恢复计划。

协议建议仍是原 7 个独立问题代表 × A/F，共 14 个 proposed slots，仅 scheduler 不同；自然模型从原任务自行观测及选技能，不预提交计划，不预灌 blackboard，不提供本诊断、适用性标签或私有注释。按实际出现的 paused 与 needs_replan 分别统计“同计划恢复”及“原工具接手”，分母为零则明确不可评价。AF 4 对 / FA 3 对只是不完全平衡的固定单次顺序。

旧 v2 `Faults.wrap` 的 block_moves / block_takeoff 会动态新增并删除障碍，违反 v7 静态障碍写入隔离契约，不能复用来制造本批自然机会。候选依据是现有静态任务本身的公开错误；是否自然进入这些点，要由另行冻结、授权的新实验回答。本次没有发起在线实验。

旧自然校准的成本依据也由同一只读脚本核对：[trial-metrics.json](../minimal-harness-full100-calibration-20261016/trial-metrics.json) 中本次候选已出现的 5 对 / 10 条实际轨迹，分别为 `b1eb0270` 128,256、`898cfdca` 114,934、`7eba7390` 5,275,260、`784d71f8` 5,231,508、`1905e0ad` 7,262,021 token，精确合计 **18,011,979 token**。机器证据附该来源文件 SHA、每条 trial ID 与原 usage；缓存输入不重复相加。`24ac1838`、`b1ad4f3e` 没有该批自然成本，这些历史用量既不是当前 v7 成本预测，也不是新额度。当前准备阶段仍为零 provider / 在线预算。
