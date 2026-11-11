# 实际模型 I/O 可重建归档：本地验收

2026-10-24。**已补齐默认关闭的模型交互归档，并通过 49 项专项与 776 项既有回归，共 825 项不重复 pytest。** 专项使用真实 Agent 图、ChatOpenAI/SDK 和本地 MockTransport，阻断外部 socket；没有真实 provider 调用、Benchmark、训练或实验预算变化。

本轮验收的是模型交互层的正文和关联关系，不是完整仿真回合或合格 Teacher 数据。既有工具链摘要仍保留原格式，本次新增独立证据文件。

## 实现与默认行为

新增 [model_io_archive.py](../../../agent4drone/model_io_archive.py)，在 [uav_agent.py](../../../agent4drone/uav_agent.py) 接入：

- 默认关闭。构造参数 `model_io_archive=True` 或构造前环境变量 `UAV_MODEL_IO_ARCHIVE=1` 显式启用；显式 `False` 覆盖环境变量。`model_io_archive_dir` / `UAV_MODEL_IO_ARCHIVE_DIR` 选择目录，默认 `agent4drone/logs/model_io`。不依赖 `toolchain_json_recording`。
- 每次 execute 创建独立 archive_id 与不覆盖的 0600 JSONL 文件。命令结果返回 `model_io_archive` 路径、状态、完整性及缺口，可选 toolchain 记录引用同一文件。原始命令/session 标签相同也不合并。
- 最外层 middleware 记录逻辑模型调用、结构化 assistant 消息及工具执行/回执；ContextVar 和请求对象上的 owner 引用把 HTTP 重试归属到原调用与回合。关闭后拒绝追加，不向当前的新回合回填。
- 同一 HTTP Request 被手工复用时不重新绑定：标记歧义、停止归属该对象的后续响应，相关仍打开的归档记 incomplete。已关闭文件及信息不改写。旧 hash/Harness 响应头关联也跳过歧义对象；真实 SDK 使用新 Request 的重试不受影响。
- 普通归档错误不改变原执行/重试，结果显式 incomplete；取消和进程中断不会被观察器吞掉。真实 handler 原异常保持，LangGraph 在自身边界对取消的包装照原路径保留。

开关关闭/启用的同脚本对照在 `off` 与 `evidence_harness` 两模式下验证请求正文、调用次数、工具动作和结果一致。没有改变搜索策略、Harness 默认 revision、模型参数、API/轮数/修复/时间预算；同步文件写入的实际性能开销尚未做在线测量。

## 记录层级与可重建性

| 证据 | 内容与边界 |
| --- | --- |
| `model_start` | graph 的 system/messages，在内部 middleware 改写前；不冒充最终模型输入 |
| `http_request` | SDK 序列化后的实际 `request.content` 原字节、base64、SHA-256、长度，包含实际 messages、工具 schema、模型及请求参数 |
| HTTP attempt / response | 一次 logical 可关联多次 attempt；记录可观察状态码与被调用方消费的响应字节，不主动 read/aread 或排空流 |
| 响应字节层 | 流式路径为 HTTPX 内容解码前字节；已经缓冲的 response.content 另标 decoded/buffered scope。不是 TCP/HTTP 封包原文 |
| `model_end` | handler 返回的消息正文、原始/解析工具调用、reasoning 等公开模型消息字段及 usage；不保存任意 SDK 对象 |
| `tool_start/end` | 独立工具 span、原 call ID、明确源 logical、参数及结构化 ToolMessage；缺失、重复或回执 ID 不匹配显式记缺口，不猜测配对 |
| `archive_end` | 记录已观察范围及 incomplete 原因，不等同 `[TASK DONE]`、成功评分或训练数据质量 |

认证头、URL 查询、ToolMessage.artifact 和任意 response_metadata 不进入此归档；仅保留枚举化响应 content-encoding 以解释字节层。没有读取隐藏 checker 或私有仿真状态。实际请求/响应正文按原字节保存，因此不承诺清除调用方主动放进正文的敏感信息。

`verify_archive(path)` 独立核对顺序、身份、开始/结束、正文与分块 hash、工具来源及未执行声明，不只相信末行的 complete。`valid` 表示档案内部结构可核对，`complete` 还要求本作用域的正文与关联齐全；明确 partial 可以 valid。它不是防伪签名或任务质量评估器。

## 本地验证及保留的失败

[预先记录的验证协议](VALIDATION_PLAN_CN.md)明确不运行 provider 或训练；新增[专项测试源码](../../../agent4drone/tests/test_model_io_archive_integration.py)保留可重跑断言。[专项说明](MODEL_IO_INTEGRATION_TESTS_CN.md)列出执行范围与材料，[格式样例](examples/README_CN.md)保留一个完整多工具链和一个 SSE partial，均复制自本轮合成测试输出，不是训练数据或新增执行。

- [attempt-02](model-io-integration-attempt-02.json)：**49 passed / 0 failed**，运行前后 12 个输入源码 hash 一致。覆盖实际 SDK/Agent 的多工具链、Unicode/reasoning/schema、同步/异步 429 与 ReadTimeout 重试、step_callback、取消、SSE/压缩/双协议流、A→B 晚到事件、Request 复用、写盘/末行异常、缺 wire、重复工具 ID及损坏档案。
- [既有回归](regression-existing.json)：**776 passed / 0 failed**，24 个测试文件；运行前后源码不变。它包含上一轮 770 项及此次涉及 Agent 建图的 6 项既有集成用例，与本轮 49 项不重复。
- [独立审查](ARCHIVE_REVIEW_CN.md)及[21 个纯对象断言](independent-archive-probe.json)单列，不加入 pytest 或任务分母。
- [attempt-01](model-io-integration-attempt-01.json)的 **30 passed / 8 failed** 原样保留。修正了假 client 的参数签名和被 Harness 装饰提示的断言，并按实测保留 SDK SSE、LangGraph 取消与旧空回复行为；没有为了得到完整标记而主动读尽流。后续补入边界反例形成最终 49 项，不把两轮通过数相加。

两轮稳定测试各有一个既有 Starlette/AnyIO 弃用警告。[历史核验](historical-preservation.json)确认上一批 47 个封存文件及原 HTML 未变；新结果不混入旧批。

## 明确保留的限制

1. 常见 SSE `[DONE]` 会让 SDK 在底层 EOF 前停止读取。保存已消费字节和结构化返回，但保守标 partial；无 `[DONE]` 且自然 EOF 的本地流才在该检查下 complete。不为归档完整而额外消费数据。
2. 429 响应被完整消费后重试成功可以完整归档；ReadTimeout 没有可观察响应的 attempt 仍 partial，即使后续重试成功。记录可见事实，不编造 SDK 内部被吞掉的逐次传输异常。
3. 公共 UAVControlAgent 仍是同步 execute；异步测试验证实际 SDK/graph/middleware 路径，不宣称新增异步 execute 服务。Graph 会把部分 CancelledError 包装成自己的节点异常，观察器本身保持原传播。
4. Ollama、直接 observation-worker 模型调用、无 HTTP 的脚本模型等明确缺 wire/unsupported。worker 即使继承父 ContextVar，也不假归属父 logical，旧 hash 观察保留。旧空 assistant 的结果回退语义没有在本轮修改。
5. 未验收真实 provider、完整世界 reset、跨 Agent 共享世界、API 子动作到工具的全部父子关系或隐藏评分隔离的完整回合。没有生成 Teacher 数据、开始 SFT/RL，或将 I/O 正确性解释为任务成功率提升。

## 下一步

在现有每 trial 新进程隔离方式下验收 `初态恢复 → 模型可见输入/工具行动 → 终止 → 独立评分 → 下一回合`，把此归档作为模型交互证据。训练/测试划分、示范质量和模型职责另行确定；不自动续跑旧 360 次或启动模型训练。

研究入口已同步：[研究状态](../../RESEARCH_STATUS_CN.md)、[Roadmap](../../CSCWD2027_INCREMENTAL_RESEARCH_ROADMAP_CN.md)、[服务使用说明](../../../agent4drone/README_SERVICE.md#optional-model-io-archive)。稳定研究方向没有变化，未向 AGENTS.md 堆入本轮测试细节。
