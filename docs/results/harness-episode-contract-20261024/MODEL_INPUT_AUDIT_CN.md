# Episode 模型输入与轨迹可重建性审计

2026-10-24。仅读取代码，冻结 11 个相关源码并运行纯函数/日志对象探针；没有调用模型、provider、HTTP、仿真或私有 checker，没有读取配置凭据，没有修改生产代码。[来源与 hash](model-input-audit-01/source-manifest.json)、[探针](model-input-audit-01/probe.py)、[机器结果](model-input-audit-01/probe-results.json)。探针通过意味着**确认当前缺口**，不是修复完成。

**当前日志适合审计摘要及部分工具执行，尚不能逐字重建模型在每一步实际收到的输入，也不能直接当作 Teacher 数据。** 本轮先处理单 Agent 生命周期互斥；正文归档暂缓，保留为后续有明确边界的候选，不扩展搜索策略或 RL 框架。

## 现有链条及缺失字段

| 环节 | 当前确实保存的内容 | 重建缺口 |
| --- | --- | --- |
| Episode / command | toolchain 的 command_id、原指令、开始/结束、status、provider/model、session_id、最大迭代、模式；独立实验 manifest 另有源码/初态/预算 | 通用 toolchain 没有完整 reset/初态摘要、冻结配置/工具版本/预算引用；不能把独立实验 fixture 自动视为每次 agent.execute 的初态 |
| 模型输入准备 | `TaskEvidenceMiddleware` 保存 system+messages 的 hash、segment、注入后缀全文和原消息 hash | 没有准备后的消息全文；后缀和 hash 无法恢复丢失的原 graph messages 或完整 system prompt |
| SDK 实际请求 | `request_record` 在序列化后记录 body hash/bytes、逐消息 hash、tools hash、model/stream；Harness 可关联 logical request/HTTP attempt | **没有 body、消息、工具 schema 或完整生成参数正文**。只覆盖启用 audit 的 OpenAI-compatible 分支；Ollama 在 hook 安装前返回，不能默认为也被记录 |
| 模型输出 | Harness 的 model_end 有 usage/tool_call_ids；最终 output 文本；记忆 v3+ 的 query_decisions 保存选定声明/感知调用审计 | 不保存完整结构化 assistant 输出、完整 tool_calls 或所有 reasoning/其他消息字段；query_decisions 不是原始模型回复。response hook 只记状态码/到达时间，明确不是响应正文完成 |
| 工具执行与回执 | evidence ledger 可保存 tool_call_id、工具名/参数、开始/结束序号、payload；API history 有请求序号/时间/参数/回执 | toolchain 的 steps 提取与导出丢掉 tool_call_id 和 message_log；API history 没有通用 parent tool-call/model-request ID，复合工具可能对应多次 API。命令回执在 client 层经过 sanitize/compact，不能称为原始服务响应或必然等于模型最终看到的 ToolMessage |
| 下一次模型输入 | 成功正常继续时，后续请求中实际会包含 assistant/tool messages | 当前仅留 hash；不能用最终黑板、旧回执或现版本 prompt 生成器代替当时发送内容 |
| 结束/中断 | execute 正常、step_limit、stream_error 等进入统一 toolchain 写入；Harness 另有终态 | JSON 主要在 execute 结束时落盘。非 streaming 异常路径把 intermediate_steps 重设为空；硬终止可能连最终 artifact 都没有。`[TASK DONE]` 的 agent success 也不是独立 checker 成功 |

相关生产位置：[wire hash](../../../agent4drone/model_request_audit.py:22)、[prepared hash](../../../agent4drone/task_evidence_context.py:103)、[steps 提取](../../../agent4drone/uav_agent.py:811)、[最终记录](../../../agent4drone/uav_agent.py:1122)、[工具链导出](../../../agent4drone/toolchain_recorder.py:74)、[API 记录](../../../agent4drone/uav_api_client.py:125)、[响应头观察](../../../agent4drone/harness_core.py:362)。冻结源码在 `model-input-audit-01/source/`，后续生产修改不改变本审计所指版本。

## 已离线确认的两个具体缺口

1. 两段语义和工具参数相同、仅 `tool_call_id` 分别为 A/B 的公开消息，经冻结 `_extract_intermediate_steps` 与 `tool_chain_from_steps` 导出后**完全相同**。因此 toolchain 本身无法反推出调用身份/并行批次；最终 assistant 消息也不在该链中。ledger 在启用时可以补部分关联，但不能补回完整 model I/O。
2. Harness 以单一 `self._logical_request` 关联 wire。执行 `model_start(A) → model_start(B) → wire(A) → model_end(A) → wire(B)`，真实日志方法把 A 的请求记为 B，再把 B 的请求记为 None；串行对照正确。**冻结的修复前版本** `agent_api_service` 后台与同步入口共用全局 `agent_instance`，没有同实例 execute 准入锁。此处证明的是可构造的交错风险，**没有声称既有串行封存实验发生过串轨**。只给 logger 加 ContextVar 不能同时修复 execute 对 ledger、blackboard、记录列表的共享覆盖。

## 迟到 response 的归属边界

追加探针使用冻结的真实 `_audit_model_request/_audit_model_response`、`HarnessRuntime` 和 `httpx.Request/Response` 对象，**没有执行 HTTP send**。A 请求开始后将 agent 切到 B，旧 response 回调只读取当前 `self._harness`：正常不同 episode UUID 时，旧 attempt ID 不在 B 的表里，事件被静默丢弃，A/B 都不新增 response 记录；这不是已证实的正常跨回合混记。若调用者复用 task_id，两个 runtime 的 attempt ID 相同，则旧 response 会误标 B 的同名 attempt；这是防御性边界，不代表当前 `execute()` 的 UUID 行为。

本轮决定**不修改 wire owner**：正常 execute 每次新 UUID，旧 attempt 不进入新 runtime；本轮互斥另行消除两个活跃 execute 的重叠。手工复用 task_id、直接调用已 close 的原 runtime.wire_response 仍追加事件到 task_end 之后，是待独立确认的契约边界。若后续采用原 owner 绑定方案，需要同时设计 closed/no-op 规则，不能只把旧 response 改投原 runtime 就宣称终态不可变。该议题未实现，也不能写成已证实的正常跨回合混记。

## 当前修复与后续候选

当前最小契约应先明确同一 Agent 只允许一个活跃 execute，重入/并发不覆盖已有 ledger、黑板、wire 列表或 Harness；开始、异常、取消、结束后的互斥释放均需脚本测试。本轮只处理准入互斥，不修改 wire owner。这一修复可独立验收，**不会自动补齐模型输入正文或形成可训练轨迹**。

正文归档另行排期时，可限定为**单实例单活跃 episode、OpenAI-compatible、显式启用的可见 I/O 归档**，默认保持旧 hash-only 行为。实际正文与哈希同时保留：在已有 request hook 内保存已序列化 body 原字节的无损表示，包含消息和工具 schema，并关联 episode、logical call、HTTP attempt、segment/重试序号。不得保存 headers、认证字段、完整带 query 的 URL、环境变量或私有评分对象；归档器不得自行读取世界、checker 或把评估记录补进模型消息。

输出在已有 model handler 返回后记录结构化 assistant 消息与 tool-call ID、usage、成功/失败/取消边界，保留字段作用域；**不在 httpx response hook 主动 read/consume 流**。wire 请求正文和解析后的 model 输出是两种不同层次，分别标注，不能把后者称为字节级原始响应。异常记录只保存限定类型/状态，不把含请求上下文的任意异常 repr 或 headers 写入。顺序写入的 episode 专属文件应能保留已开始、未结束、失败及取消；未完成的记录显式不可当作训练样本。新增记录不改变模型输入、重试策略或工具执行。

最低可交付链条是：`episode/初态与配置引用 → 逻辑模型调用 → 实际请求正文/工具schema → 结构化assistant输出/tool_call_id → 公开工具回执与调用引用 → 下一实际请求 → 终态 → 独立评估引用`。本轮可以先验收前半段；未完成 reset、动作父子关联和独立终态时，必须继续标记“局部可重建”，不要声称完整 Teacher pipeline。

验收仅用无网络的 MockTransport 或纯脚本回执：连续两次请求、SDK 重试、空响应新 segment、一次工具结果、最后 assistant 输出；检查正文 hash 逐字匹配且行为未变。补失败/取消时记录保留、关闭开关完全不存正文、不同 episode 不交叉、响应流不被提前消费、合成认证/私有 checker canary 不进入归档等反例。若首版只支持非流式，应对流式明确拒绝归档资格或标为 unsupported，不静默声称完整。当前探针尚未实现这些功能，也未生成训练数据。
