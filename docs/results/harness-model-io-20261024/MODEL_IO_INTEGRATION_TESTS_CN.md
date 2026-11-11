# 模型 I/O 归档专项：真实 SDK、本地回执

2026-10-24。新增 [test_model_io_archive_integration.py](../../../agent4drone/tests/test_model_io_archive_integration.py)，**26 个测试函数、49 个参数化用例全部通过**。这是功能验收，不是模型能力、真实任务成功率或 Teacher 数据质量评测；没有调用 provider、模拟器或外部网络，没有读取配置或实际凭证。

最终执行见 [attempt-02 日志](model-io-integration-attempt-02.log)及[元数据](model-io-integration-attempt-02.json)：pytest 3.55 秒，整个进程约 4.254 秒；12 个源码/测试输入在运行前后 hash 相同。唯一警告为已有 Starlette TestClient 的 AnyIO `BlockingPortal` 弃用提示。稳定入口 [tests.log](model-io-integration-tests.log) / [tests.json](model-io-integration-tests.json) 是同一次结果的副本，不增加计数。

## 检查范围

| 边界 | 独立检查方式 |
| --- | --- |
| 实际请求与开关行为 | 真实 `ChatOpenAI`、OpenAI SDK、Agent 构造/execute/LangGraph，HTTP 使用本地 `MockTransport`；以 transport 实际收到的 `request.content` 为真值，核对无损 bytes、SHA-256、长度、Unicode、工具 schema、生成参数及 reasoning 回传。off/on 的实际请求、公开工具调用、结果及 token 用量相同；toolchain recording 关闭时仍能单独归档 |
| 多工具身份 | 两个原始 tool-call ID 对应各自结构化 ToolMessage，来源指向实际产出它们的逻辑模型调用；后一 HTTP 请求中的工具回执逐项一致。重复 raw ID 标为 ambiguous，不猜测父调用 |
| HTTP 重试 | 同步、异步均走 SDK 自身的 429 / ReadTimeout 重试；仅将退避等待设为零。多个 attempt 对应同一 logical call；完整 429 后重试可完整，超时缺失响应的 attempt 保留为 partial |
| 响应消费 | 在归档 hook 后增设观察 hook，确认底层 stream 读取数仍为零；SDK 自己消费后，按 attempt 拼接原始 chunk。同步、异步、双协议 stream 均覆盖；gzip 原 stream bytes 与已缓存解码正文按明确 scope 区分 |
| 成功、错误及取消 | 完整错误 JSON、坏 JSON、半流异常、首 token 取消、escaped KeyboardInterrupt、observer 内取消、写盘失败均留真实边界；`complete` 表示捕获覆盖，不等于命令成功。正常 graph invoke 和 `step_callback` 图更新路径分别覆盖 |
| 回合归属 | 连续 A/B 命令使用不同身份；A 的晚到 request/response 不能改闭合 A 或写入活跃 B。同一可变 Request 在同回合、跨未关闭 A、跨已关闭 A 的误用被隔离，不重绑定到错误 owner；这些是明确的 hook 误用探针，不是自然 SDK 行为 |
| 非可见数据 | 合成 Authorization/query canary、AIMessage 的 response metadata headers、ToolMessage 的 private artifact 不进入归档；相邻假世界的 private checker 从不作为策略输入。真实响应正文仍按实际 bytes 保存，不把正文中的内容与非可见元数据混同 |
| 异常及校验器 | observer 普通 OSError/RuntimeError 不改变任务执行，但归档 incomplete；KeyboardInterrupt/CancelledError 在 middleware 边界传播。archive_end 写入失败不能宣称 complete。独立副本的正文、hash、序列、缺尾、半行破坏均被拒绝 |
| 未支持路径 | ScriptedModel 没有实际 HTTP 时保留结构化输出但标 `wire_missing`；裸 observation worker 即便继承父 ContextVar 也不归入父 archive，旧 hash audit 仍保留，整体 unsupported |

同步主要验证走公开 `UAVControlAgent.execute()`。异步部分是实际 `create_agent.ainvoke()`、SDK 和新 middleware 的受控组合，手动拥有 archive 起止；当前没有公开的 async execute 回合入口，不能据此宣称其生命周期已验收。少量归属误用和 observer 故障探针直接调用真实 hook/middleware，已在测试中标注；它们不混成任务样本。

## 必须保留的实际边界

- 常见 SSE `[DONE]` 会令 SDK 停止解析，未必再请求底层 EOF。即使助手已返回 `[TASK DONE]`，归档仍保守标为 partial；不会主动继续读流来提高完整率。另用自然 EOF 的 SSE 对照确认完整分支，半流和首 token 取消另列。
- 当前 LangGraph 将节点主动抛出的 `asyncio.CancelledError` 包装为 `NodeCancelledError`。因此同步 execute 返回既有失败结果，异步图抛包装异常；直接 middleware 边界仍传播原 CancelledError。这不是取消被当成成功，也未改变框架行为。
- 空 assistant 回复在既有 `_extract_final_output` 中会回退到用户消息，因此本次实际路径未进入空字符串重试，最终失败。专项只确认空输出原样留档，不修改该旧行为，不声称空回复恢复已覆盖。
- `valid` 与 `complete` 分别表示记录可核验和捕获覆盖；两者都不代表独立任务检查通过。完整 reset、私有评分关联、数据划分、轨迹筛选和训练输入构建仍需另行验收。

## 首次失败记录与计数

[attempt-01](model-io-integration-attempt-01.json)保留 **38 项中 30 通过、8 失败**及[完整日志](model-io-integration-attempt-01.log)，没有覆盖。失败包括：假 public client 不支持生产工具的 positional 参数（3 项）、Harness 追加证据后不能要求 user content 整条等于原命令（1 项）、上述空回复旧行为（1 项）、SSE DONE/EOF 区别（1 项）、LangGraph 取消包装语义（2 项）。这些修正测试假设，不为通过测试而修改既有 Agent 语义。

独立审查同时补齐 Request 重用隔离、双协议 stream 和终末写入异常等生产边界；最终增加 11 个用例，共 49 项。两轮源码各自在执行期间保持不变，版本差异见各自 hash。**最终分母只取 49，不与首次通过数或重复运行相加。**

本次 pytest 临时目录已按精确测试轮次复制到 [58 个合成输出的清单](model-io-integration-attempt-02-artifacts/MANIFEST.json)，约 4.15 MB；包括故意损坏的校验器副本及 partial/error 档案，不应批量当作完整轨迹。另提供[两份 schema 示例](examples/README_CN.md)。复制与 hash 核验未执行新测试。

## 复现

在仓库根目录使用本地锁定环境，选择新的临时输出目录，保留原记录：

```sh
PYTHONPATH="$PWD/agent4drone:$PWD/agent4drone/tests" \
  agent4drone/.venv/bin/python -m pytest \
  agent4drone/tests/test_model_io_archive_integration.py -q \
  --basetemp /tmp/model-io-integration-new-run
```

实际执行命令及输入 hash 以元数据为准。依赖版本：pytest 8.4.2、OpenAI 3.17.0、LangChain 1.4.2、langchain-core 1.6.4、langchain-openai 1.6.3、LangGraph 1.2.12、httpx 0.28.1、SDK 当前使用的 httpx2 2.13.0。测试在每例开始禁用 socket connect/connect_ex/create_connection，并拒绝调用配置加载函数；API key、query、header、artifact canary 均为固定合成字符串。
