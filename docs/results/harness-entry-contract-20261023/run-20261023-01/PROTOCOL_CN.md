# Harness 入口契约 v2：真实模拟器离线配对验证协议

预定日期：2026-10-23。执行前写定本协议；原 full100 校准的 40 次及剩余 360 次均不续跑、不覆盖。用户已授权继续有价值的 Harness 验证，本轮仅使用本机真实 ASGI 模拟器与脚本控制，不调用供应商或创建 LLM。

## 问题、范围与比较

检验 opt-in `entry_contract_v2` 能否在空障碍黑板下，通过有界公开感知进入原搜索；检验终止计划的重复 start/resume 是否明确拒绝且不改变计划或重放动作。`legacy` 为并行保留的旧行为；两臂固定 `applicability`，不改变搜索算法、完整语法、任务、工具权限或原 checker。

| 类型 | 固定任务 ID | 作用 |
| --- | --- | --- |
| 三个不同搜索问题簇 | `7eba7390`、`24ac1838`、`b1ad4f3e` | 原始完整 95% 搜索语法；不以重复任务 ID 扩大独立样本数 |
| 简单链正对照 | `b1eb0270`、`898cfdca` | 起飞—旋转/爬升—悬停应维持正确效果 |
| 不支持语法负对照 | `784d71f8`、`1905e0ad` | 指定目标与 50%/70% 附加义务仍应拒绝，不能降格为完整 95% 搜索 |

共 7 题 × 2 revision = 14 次。任务顺序按上表，逐题交替 revision 先后；各 trial 是新进程、同一冻结完整初态、空黑板和新 R2 账本。驱动器显式调用 `start`，没有自然语言模型选择技能，没有原工具接手策略、故障注入或重跑挑选。

**这只能检验“技能被正确选择之后”的离线功能与本组物理任务结果。** 不能宣称 LLM 自主入口、模型恢复行为、端到端 Agent 成功率、门控独立收益或泛化提升。

## 初态、来源与隔离

从 `experiments/minimal-harness-full100-v1/fixtures/` 读取原 JSON、完整对象图 pickle 和场景时间线。先核对原 manifest 全文件哈希，再逐一核对 JSON 关联的快照、时间线、源场景及指令 SHA；校验后才反序列化可信本地文件。恢复后核对结构状态 SHA 和原公开初态 SHA。

公开初态哈希检查只属于评估准备：完成后重新恢复原快照，不把其观测灌入黑板。任务计时开始时黑板为空。每臂使用 `FullWorld`、`client_class`、`TaskEvidenceLedgerR2`、`PerceptionBlackboard` 与 `HarnessRuntime(make_skill_factory(board))`；只有技能自己发出的公开调用可形成在线证据。

运行前将 agent/server Python 源码及所需 runner 复制到新输出目录并保存 SHA。各 worker 从这份冻结副本导入；本轮中途的工作区改动不能改变后续 trial。执行前后检查冻结来源及原 fixture 哈希。输出目录必须不存在，不覆盖任何先前尝试。

worker 禁止 socket connect/connect_ex/create_connection；模拟器用本进程 ASGI TestClient，禁止网络和模型调用。公开调用白名单沿用原技能；隐藏 checker 只由隔离 evaluator 在结束快照上调用，不返回给技能。checker 返回错误属于验证错误，不替换成任务失败成绩。

## 预算与停止

沿用技能每次 500 秒 deadline、每计划 2,000 API、16 次局部修复及 16 轮搜索上限。applicability 应一次运行到 terminal；出现非预期 ready/paused/running 不自行添加恢复策略。外层每 trial 90 秒真实墙钟 watchdog，用于阻断离线卡死；提前触发记录为不完整，不假称经过 500 秒或任务失败。

固定 14 次，无自动重试、补样或追加样本。任何基础设施错误、哈希不符、网络尝试、checker 错误、控制探针产生动作/状态变化，停止剩余派发并保留已启动日志。技能自然 `needs_replan`、搜索耗尽、unsupported 或原 checker 不通过是有效观测，不能为了成功修改算法或任务。

## 指标与控制探针

保存完整公开 API/回执、Harness 计划/门控、物理事件与动作来源、初始/结束状态哈希、公开进度、原 checker 树、路径和耗电。结果同时记录技能 status/reason/cursor 与原任务检查，不用 `completed` 代替整题通过。

首次 start 返回后，对同技能重复 start，并对其 terminal plan_id resume；无计划的 unsupported 使用不存在的 plan_id作负对照。探针前后比较完整计划 frames、世界结构 hash、API 请求数与物理事件数，必须不变。v2对终态需明确 `status=rejected`、`resumable=false`；legacy只记录现状，不要求它返回新字段。日志增加不视为计划状态改变。

两臂报告搜索入口观察数、技能动作、最终进度、原检查、实际成本及控制探针；零模型调用及零网络请求单列。没有原工具接手，所以本试验中的 checker 失败不能外推为完整 LLM Agent 失败。无论正负结果都保留固定分母。
