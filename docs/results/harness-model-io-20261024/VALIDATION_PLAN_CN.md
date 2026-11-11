# 实际模型 I/O 归档验证协议

2026-10-24。本轮只补可重建归档及本地正确性验证。默认关闭，不运行真实 provider、仿真 Benchmark、旧 360 次或任何训练，不增加既有任务预算。

## 实现验收

- 开关独立于既有 evidence/hash 与 toolchain 记录；显式关闭能覆盖环境变量，关闭时不产生正文文件或改变请求、工具和任务结果。
- 每个 execute 独立归档身份，不以 session/task ID 相同推断同一回合。文件不覆盖，关闭后不可追加；同步/异步回调绑定原归档对象。
- 权威输入是 SDK 序列化后的 HTTP request.content，含完整 messages、工具 schema 与实际参数；原字节、长度和 SHA 可核对。graph 输入另标层级，不冒充最终请求。
- response hook 不主动 read/aread。仅旁路记录调用方实际消费的响应字节，保留 handler 的结构化 assistant/tool-call 输出及工具回执。HTTP/内容解码层级、未消费和中断均须明确。
- 一个逻辑模型调用可关联多次 HTTP attempt，工具以原 call ID 和明确来源关联；重复/缺失关联不得猜测。请求、模型返回、工具执行是不同事实。
- 归档自身错误不改变任务调用/重试/预算，记录失败不得被标为完整。原模型或工具异常/取消按原路径传播，不伪造输出或成功。
- 仅从模型/工具公开消息与 HTTP 正文取证，不读取认证头、URL 查询、配置凭证、隐藏 checker 或私有仿真状态；正文原样保存，不承诺清除调用方主动写进正文的敏感内容。

## 本地验证矩阵

| 范围 | 独立真值及边界 |
| --- | --- |
| 输入/输出完整性 | MockTransport 实际见到的 request bytes、受控 response bytes、Unicode/工具 schema/参数、结构化多工具输出逐字核对 |
| 工具关系 | 并行工具、原 call ID、structured ToolMessage、下一模型请求中的结果关联；重复/未知 ID 显式标记 |
| 默认与行为 | 同脚本关闭/启用，比较请求正文、模型次数、动作和结果；独立于 toolchain 开关 |
| 重试与失败 | SDK 429/连接读取异常的实际重试，多 attempt 对同 logical；handler/tool 异常不被观察器吞掉 |
| 流与取消 | hook 不提前消费；正常耗尽、未消费、部分读取、读取异常与取消分别记录；关闭后晚到回调不可改 A 或 B |
| 回合隔离 | 相同 session/command 标签及不同实际 archive ID；A→B、保留 A request 的延迟响应、写盘失败与不可完整恢复 |

专项先执行一次并保留原始日志；遇到失败先诊断，修正后另存 attempt，不合并运行次数。稳定后执行相关既有回归，报告唯一测试项数量。此协议不证明真实 provider 兼容、完整世界 reset、跨 Agent 共享世界隔离、任务成功率提升或 Teacher 数据质量。
