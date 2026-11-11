# 模型 I/O 归档独立审查

2026-10-24。审查对象是默认关闭的模型交互层归档及 `uav_agent.py` 接线。本次审查没有修改生产代码，没有调用 provider、仿真、Benchmark、模型训练或私有 checker；合成 canary 仅用于证明字段隔离。

**对应下列源码版本，未发现主要阻断。** 这表示受限范围内的代码审查和本地功能证据成立，不表示生成了 Teacher 数据，也不是任务成功或泛化结果。

- `agent4drone/model_io_archive.py`：`9b49d4f970e067a3ec821ada8c95306e0bccf016746bbd9686235f8e8d6d8b35`
- `agent4drone/uav_agent.py`：`d5f210879d1ca9fcf4e4fd1b7f985abcf6c1554def21aa8f3443194d22e0b5cd`
- `agent4drone/model_request_audit.py`：`303a0ae805ebd7d6079437b1b041709edf750383735b65f1e5cc0bd6221c315a`

完整 hash 和断言见[独立探针结果](independent-archive-probe.json)及[探针源码](independent_archive_probe.py)。探针以临时目录中的真实归档对象、HTTP Request/Response 对象和字节流运行，阻断 socket，**21 个断言通过；它们不是 pytest 用例，不能与测试数相加**。另已只读核对执行代理的[49 项专项测试 metadata](model-io-integration-attempt-02.json)及[日志](model-io-integration-attempt-02.log)，源码 hash 相同且测试期间未变化；该测试使用已安装 SDK、实际 Agent graph 和 MockTransport，没有外部 HTTP。

## 已核对的契约

1. **默认关闭。** 单独的开关控制正文归档；既有 evidence/hash audit 不会自行启用正文。关闭时不创建归档器、不增加归档 middleware。启用后，观察器普通异常只使归档不完整，不替代原模型/工具执行；返回结果中的归档信息不冒充独立任务评分。
2. **输入来源准确。** 实际请求记录来自 SDK 序列化后的 `request.content`，以 Base64、长度和 SHA-256 保留原字节，不用重新序列化的 JSON 或黑板补造。外层 middleware 输入另标为 inner middleware 之前的状态，不能代替实际 wire 输入。
3. **响应被动收集。** response hook 不调用 `read/aread`；已缓冲的内容标为 `buffered_httpx_content`，未缓冲流标为 `httpx_stream_before_content_decoding`。同步、异步及双协议流保留相应消费接口，正常消费后才标完整；提前关闭只留已消费部分，不主动 drain。它们不是 TCP/HTTP 帧；压缩前后的层次不能混称原始 JSON。
4. **身份及生命周期。** 每回合有独立文件及身份，logical call 使用 ContextVar，重试有独立 attempt，response 使用 Request 上绑定的 owner，不取当前回合替代。原 tool-call ID 与模型输出、工具开始/结束之间有来源关系；缺失、歧义、不匹配及声明后未执行会使归档不完整。已绑定 Request 被再次使用时不重绑，而是隔离该对象；后续 response 同时跳过归档及旧 Harness 头部关联。已经关闭的 archive 不接受迟到写入。
5. **异常与取消。** handler/stream 原异常继续传播；观察器普通异常不能掩盖正常结果。KeyboardInterrupt/取消类 BaseException 不再被普通 fail-open 吞掉。结束写入失败或结束期间被取消，返回的 `complete` 不为真。文件按顺序逐行 flush；硬终止留下缺少完整结束的记录，由校验器拒绝其完整性。
6. **字段隔离。** 不导出认证 headers、完整带 query URL、SDK/client 对象、任意异常 repr。消息使用专门投影，排除 `response_metadata` 和 `ToolMessage.artifact`；因此这些非模型可见容器中的合成认证/私有 checker canary 不入档。实际请求/响应正文按其真实内容保存，归档器不是对正文的自动脱敏器。
7. **独立校验。** `verify_archive` 检查身份/顺序、所有已使用 ID、请求正文、响应生命周期/分块 hash、模型/工具终态及工具来源，不只信任 `archive_end.complete`。`valid` 可包含明确的 partial；`complete` 还要求当前作用域的 wire 和关联链齐全。该函数是内部一致性校验，不是外部真实性认证或训练数据质量判定。

## 审查发现及处理

早期版本的纯对象探针确认了三类反例：全量消息序列化保存了非可见 metadata/artifact；观察器吞掉 KeyboardInterrupt；结束写入失败时可能返回 `complete=True`。另代码检查发现复用同一个 Request 会覆盖 owner，以及双协议流被包装成仅异步流的风险。当前上述源码版本已修正，独立探针及专项反例测试均覆盖相应边界。旧版本失败不能当成最终实现仍有问题，修复通过也不能外推到未测试 provider。

## 保留范围与缺口

- 目前完整覆盖目标是显式启用的 OpenAI-compatible 主 Agent 调用。Ollama 和直接调用 LLM 的 observation worker 被标为 unsupported/incomplete；后者不借用父 middleware context 冒充主调用。
- 这是模型交互层可重建。世界初态/reset、数据划分、独立评分引用、复合 API 请求到上层工具调用的完整父子关系仍未补齐；没有据此宣布完整 Teacher episode、SFT 或 RL 流程完成。
- 迟到与重用保证针对已绑定的归档 Request、关闭的 owner 和专项构造边界；不声称任意第三方修改 Request/extensions 或独立裸调用均得到自动身份恢复。
- 测试与探针使用本地可控响应，未测真实 provider 的所有压缩、流、断连或存储故障组合；没有本次线上性能、耗时、成本或任务成功率收益。
- 审查过程中另提出 `os.open` 成功而 `os.fdopen` 失败时的描述符清理检查点；该极窄构造失败路径未在本次独立探针中验证，不据此宣称已证明正常 SDK 调用存在泄漏。

本审查未修改此前 episode-contract 审计及任何封存 Harness campaign。旧审计记载的是其冻结源码的缺口，本报告描述本轮新增实现的局部验收，二者应按版本阅读。
