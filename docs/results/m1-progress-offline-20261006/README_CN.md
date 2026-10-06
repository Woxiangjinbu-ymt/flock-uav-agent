# M1：冻结 B1 工具轨迹离线旁路验收

本次只验证 M1 对已有工具证据的处理，不调用模型、API 或 checker，也不评价在线成功率。固定 B1 attempt 为 `20261006-134416-b1-remedies60-1ee84ea7`，方法提交为 `cb80fde8269a09795782a6ef046d04c381b3f3c9`。完整统计、逐题映射、输入和源码 SHA-256 见 [replay.json](replay.json)；脚本为 [replay_m1_progress.py](../../../experiments/analysis/replay_m1_progress.py)。

| 验收项 | 结果 |
|---|---:|
| 原计划 / 已计分 / 未计分 | 60 / 57 / 3 题 |
| 已计分任务唯一匹配原 job 与 trace | 57 / 57 |
| 有完整工具序列可回放 | 56 题，1204 工具步 |
| 观察器异常 | 0 |
| 原 tool_input 与 observation 回放前后哈希一致 | 1204 / 1204 |
| 摘要字符上限 / 实测最大值 | 4000 / 4000 |
| 摘要完整 JSON 核验 | 1204 / 1204 |
| 整任务或要求完成断言 | 0；均保留 `not_evaluated` |
| 冻结证据文件回放前后未变 | 118 / 118 |

三个场景分别回放 17、20、19 题，共 435、391、378 工具步。`ad34350a/529c0622` 虽已计分且能唯一找到异常 trace，但其中 `tool_chain=[]`，因此明确列为不可逐工具回放；没有拿该题 305 条底层 API 记录、别题或后题替代。其余三题 `ad34350a/23a9d67d`、`ad34350a/7e926272`、`3973d0d6/7eba7390` 未计分，保留在固定 60 题账本中。

匹配同时要求原场景一致、job ID 与场景结果记录一致、原指令 SHA-256 与冻结计划一致、trace 指令逐字相同、起始时间距 job 创建时间小于一秒且候选唯一。实际最大时间差为 0.00953 秒；同一 trace 不允许用于两题。计划与 results 只用于固定题目和身份，检查定义、评分标签、模型推理、终答、before/after 世界快照及 API-call history 均不传入 ledger。

每题从空 ledger 开始，逐步消费原 `command`、`tool`、`tool_input`、`observation`。实际工具回复内的累计 blackboard 可补充 inherited 知识，但不冒充本步新观测，也不导入前题动作事件。1190 条 observation 可解析为 JSON，14 条为文本，均保留原回执；文本和失败回执不会被解释为目标不存在或动作没有副作用。

本轮提取 139 个“每题去重的显式标准实体名称”，最终 131 个精确绑定、8 个仍 unresolved；没有模糊名称替换。56 条指令中 2 条没有显式标准实体名，115 个原文分句中 51 个没有这种名称。它们仍完整保留，没有被删除或强制解释。这些数字是**名称绑定覆盖**，不是自然语言任务解析准确率；M1 尚未实现完整的责任、代词、布尔关系及顺序条件判定。

实际工具回执分类为 observed 351、completed 664、blocked 138、partial 28、error 18、unknown 5。这里的 completed 仅指工具回执或相对距离关系，不代表整任务完成。典型边界包括：

- `ad34350a/91e5a616` 第 10 步保留路径已完成 1 点、未完成 5 点的 partial 证据；不改写为到达。
- `ad34350a/64b6560e` 第 6 步保留 `arrival_verified=false` 和真实返回位置；请求坐标不能当执行事实。
- `ad34350a/89129044` 第 7 步记录拍照回执，但拍摄对象仍为 unknown；附近目标不自动成为照片内容。
- 未发现的 `Polygon Obstacle 16` 等名称保持 unresolved；不推断不存在。

真实回放发现并修正了相对距离工具的解析边界：`move_within_distance` 顶层报告关系结果，其 `navigation_result` 可以为 null，或仅表示辅助路径执行。现在只有真实 `*_and_sense` 工具解包内部 movement/navigation 回执；关系工具顶层证据得以保留。另经独立源码审查补上低电量错误可能已触发强制降落的保守失效，并保留 target 的 `reached_by` 与会话历史范围，避免只展示无责任限定的 `is_reached`。三个 M1 测试文件最终合计 **64 项通过**。

摘要按原文片段、实体绑定、近期工具证据、关键状态分配空间，输出完整 JSON，并显式记录 omitted 数量。它不修改原指令或原工具历史。回放沿原 toolchain index 顺序进行，不能从这种序列恢复真实工具并发重叠；并发事实的新鲜度边界由独立单元测试覆盖。每题首个工具之前的会话黑板也未在本次模拟恢复。

复现与只读复核：

```sh
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python experiments/analysis/replay_m1_progress.py
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python experiments/analysis/replay_m1_progress.py --check
```

该结果支持进入受控在线 M1 验证，不能证明摘要已经改变模型行为、改善任务成功率，或与 B1 在线逐动作等价。源码或输入发生变化后应重新生成本报告；不得把固定回执重放当作新的模型实验。
