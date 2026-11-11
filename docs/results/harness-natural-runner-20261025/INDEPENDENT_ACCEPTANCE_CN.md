# 本地真实墙钟自然入口：第二独立验收

审查日期：2026-10-25。对象是已经执行并封存的 [run-20261025-01](run-20261025-01/manifest.json)。本次只读取产物和源码，未导入项目运行时、未调用原 `acceptance_checks`、未反序列化世界快照、未启动 trial 或 provider，也未修改封存文件。

最终 [独立核验 JSON](independent-acceptance.json) 的 **329 项检查全部通过**，记录了 766 个读取输入的 SHA256，审查结束回验无变化。这些是静态证据检查，不是新增 pytest 用例、执行回合或独立任务样本，不能与其他测试分母相加。

## 进程、评分与两次强杀

直接对照 `results.json`、`process-ledger.jsonl`、worker/scorer 原始记录和退出封存，确认 **12 个 worker、10 个 scorer，共 22 个不同 PID**。日志与汇总中的进程记录一致；每个进程均记录已退出，上一进程的 `ended_ns` 不晚于下一进程的 `started_ns`。父源码在 `kill()` 后仍必须成功 `wait()` 才返回，worker 验证完成后才启动 scorer。

10 份评分分别绑定相应 worker-result 和私有快照 SHA256，worker/scorer 退出封存均完整。评分进程没有导入 Agent，网络尝试记录为空；私有 canary 阳性，原检查树一致，检查器本身无运行错误。原任务只有“航向 353°”“高度 14 m”两个检查叶子，结果如下。

| 本地场景 | 实际 delegate HTTP | 已提交物理命令 | 原检查叶子 | 模型交互归档 |
| --- | ---: | ---: | --- | --- |
| skill_success | 3 | 3 | 2/2，通过 | complete |
| raw_success | 5 | 3 | 2/2，通过 | complete |
| http_error | 3 | 1 | 1/2，不通过 | partial |
| request_timeout | 3 | 1 | 1/2，不通过 | partial |
| task_deadline | 3 | 1 | 1/2，不通过 | partial |
| cancel | 3 | 1 | 1/2，不通过 | partial |
| http_limit | 2 | 1 | 1/2，不通过 | partial |
| usage_missing | 3 | 1 | 1/2，不通过 | partial |
| usage_stop | 2 | 1 | 1/2，不通过 | partial |
| hung_drain | 3 | 1 | 未评分 | 已关闭、partial |
| watchdog_hang | 3 | 1 | 未评分 | 强杀时未关闭 |
| post_kill_B | 3 | 3 | 2/2，通过 | complete |

两个强杀场景都有独立传输日志的第 0、1、2 次请求及前两次响应；第三次响应未产生。第三个模型请求正文已经包含公开的成功起飞回执，公开 API 与物理事件均证实起飞至 14 m。因此被测的是请求在途时的强杀，不是导入或初始化失败。

- `hung_drain`：PID 9992，`transport_not_drained`，返回码 −9。虽然存在私有评分快照和关闭的 partial 归档，但没有完成 worker manifest，也没有评分目录或 scorer。
- `watchdog_hang`：PID 10005，`watchdog`，返回码 −9。第三请求后的归档没有终末记录，也没有完成 manifest、最终快照或评分。

两个被强杀 worker 的“不评分”是正确结果。不能把快照存在、归档关闭或无后续动作替代为完整生命周期验收通过。

## B 回合隔离与实际公开正文

`post_kill_B` 使用新 PID 10019、独立 command ID、archive ID 和实例 nonce。其初态、公开启动摘要及原检查树摘要与 `skill_success` 相同。首个 HTTP 请求的**完整正文为 46,064 字节，原始字节已经完全相同**，SHA256 为 `43df59672c0737165a389455ad2b32420c70f2084932e5ccc2515c9bcae8f19a`。规定的唯一归一化是逐回合 command ID，但该首请求中两者均出现 0 次；没有删除提示、工具 schema、参数或其他正文。

从原始公开 API 日志重新提取的三条动作均一致：起飞 14 m、转向 353°、悬停；端点、参数、提交状态和成功状态相同。两个原 checker 结果也相同。这里不声称所有日志时间戳、回执字节或墙钟耗时一致。

独立扫描了全部 **103 个 policy 文件、12 份模型归档、38 个请求正文、30 个响应 chunk/重组响应**，验证正文 base64、长度和 hash，并在解码与重组后扫描私有 canary 和检查树字段，未发现泄漏。B 的模型归档中也不存在其他已完成回合的 command ID。私有评分目录中 canary 为阳性，避免以“canary 根本不存在”得到假阴性。

38 个归档请求事件不能当成 38 次 delegate：`http_limit` 与 `usage_stop` 的 hook 均保存第三个请求意图，但准入层只实际发出前两次。第三意图无响应起始或正文，关闭时明确记录 `response_observed=false`、长度 0 的 partial。归档观察点与实际发送分母必须分开。

## 静态地图前提、取消与截止

全部原始 task API 记录均为公开请求，无管理权限、检查器调用或地图/会话写入。冻结源码先验证独占、无 lifespan 的本机 ASGI，时间线 schema 仅允许 `second/targets/environments/rng`，之后才启用静态障碍契约。task writer guard 保留无人机命令和碰撞查询，拒绝其他写入。

10 个完成 worker 的私有几何审计记录初尾一致：100 个障碍，几何摘要均为 `f2f845f33d28f900f936239680dd8c09f042175106a5106373ff64d5fd7a1a48`，501 条时间线事件中无障碍更新。两次强杀没有最终几何记录，不能写成“12/12 初尾几何验证”。本审查核对封存摘要、公开请求与计算源码，没有另行反序列化快照重算几何；静态 writer 隔离也不是动态地图或外部并发写入保证。

取消记录满足 `requested_at <= effective_at`，评分边界原因是 `cancellation_effective`，之后没有已提交动作。1 秒任务截止探针的边界恰为 1.0 秒，晚到模型响应未触发后续转向。本批这两个边界都发生在模型等待期间；锁内动作与取消竞争的覆盖来自单独纯测试，不能扩大为本集成覆盖所有竞态，也不能称为完整 500 秒压力测试。

## 初次审计误判与完整性

原 [summary.json](run-20261025-01/summary.json) 保留 **335/336**，唯一失败是 `hung_drain:oracle:undelivered_response_2_absent_from_archive`。原条件要求完全没有 `http_response_end`，但该回合正常关闭归档，合法写入 `complete=false`、`response_observed=false`、`chunk_count=0`、`length=0` 的 partial 终末。这不代表产生或交付了响应；真正被强杀而没有终末的是 `watchdog_hang`。本独立审查直接核实两者原始记录，没有覆盖该历史 summary。

本审查自己的 [首版只读结果](independent-acceptance-attempt01.json) 也保留 **325/327**：两条误判来自要求归档请求数等于实际 delegate 数。修正为“逐条核对实际 delegate 前缀，并单独验证被准入拒绝的意图没有响应正文”后，最终 329 项全部通过。两次修订都只涉及审计口径，没有重跑 trial 或修改运行器。

本批 **378 个封存文件、86 份冻结源码**均匹配；此前完整回合的 **302 个封存文件**仍匹配，其 **80 份运行源码**与当前工作区及本批冻结副本均一致。本次没有新增真实模型证据：它验证的是脚本化 MockTransport 经真实 Agent、SDK、模拟器与新进程评分的本机接线及生命周期；不构成自然模型选择能力、Benchmark 成功率、Teacher 数据或训练成果。同步非流式 JSON 是当前支持边界，旧归档器的 SSE partial 行为不等于该 runner 已支持流式执行。
