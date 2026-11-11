# 无参感知与显式空对象的 Mac 等价验证

2026-11-11。在原 29 道开发题的冻结初态上，对 `sense_nearby_entities` 分别执行不带参数的调用和 `{"input_json":{}}`。**29/29 对的实际 HTTP 请求和响应、工具回执、完整共享感知黑板及终态逐项相等**。机队规模为 1–5 架，各种写法均发出 118 个本地 GET。29 个任务的黑板都发生了更新。仿真未发起外部网络连接。[逐题摘要](simulate.json) · [两种写法的原始返回、请求和黑板](arm-evidence.jsonl.gz)

## 公开接口和执行过程

条件 C 沿用上次整题复测的感知说明 B。仅对这个工具移除公开 JSON Schema 中 `input_json` 的必填要求，并把默认示例改为省略参数或传显式空对象均可。选机及其他参数仍放在 `input_json` 内。Qwen 原解析器对每个原生工具调用做完整校验，通过整组后，实验入口只将其中感知工具的空参数字典编码为执行端支持的显式空对象。调用 ID、批内顺序、模型思考、已有合法动作和工具回执按原流程传递。[变体实现](../../../scripts/thinking_events/sense_default.py) · [原感知工具](../../../agent4drone/uav_langchain_tools.py)

每次从同一个原始世界快照恢复，以空黑板开始，经过版本化的参数适配与真实工具处理函数。实际请求先获取机队清单，再以 Agent 权限依次访问名单中每架机的本地 nearby 端点，记录返回结果及黑板的第一次感知更新。世界仿真钟固定，API 审计用 UTC 时刻和请求 UUID 在配对内重置成相同值，因此可以直接比较包含请求日志的完整终态。逐题原始结果同时保留两个分支。[验证程序](verify.py)

## 冻结生成的静态重放

对条件 B 的 58 个回合，使用已锁定的官方 Qwen3.5 分词器和原整组解析器，重放 159 条请求中可用的原生成 token。保持生成不变，改变公开接口后得到如下结果。[逐请求摘要与测试样例](parse.json)

| 旧请求的结果 | 条数 |
| --- | ---: |
| 原本合法，完整助手消息保持一致 | 102 |
| 原本因缺少感知参数而被拒绝，条件 C 接受整组 | 32 |
| 在条件 C 下继续拒绝 | 23 |
| 预算已截止，无原生成可重放 | 2 |

固定生成的正反例在开、关思考两种模式均验证。示例包含显式空对象、指定机号、混填或重复参数、非法数据类型、未闭合调用，以及同组感知后跟缺参起飞。任一调用出错时，整个助手消息均未发往执行端。公开感知说明条件 C 的 SHA-256 为 `27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227`，入口实现的 SHA-256 为 `ad43be8e31cf6da899f56020da619861d3cb47b0870477bda9533fa2d20813e9`。32 个被接受的历史末轮只说明解析行为发生变化，G1 和 G2 由条件 C 下重新生成的完整 58 个回合独立评分。[上次整题基线](../flockcue-a100-g1g2-retest-20261111/REPORT_CN.md)

## 复跑

在项目根目录依次运行以下命令。第一个环境提供固定的离线分词器，第二个环境提供实际 Agent 工具和仿真服务。

```sh
~/.cache/flockcue/venvs/qwen35-mlx-py311/bin/python docs/results/flockcue-sense-default-mac-20261111/verify.py parse --out /tmp/flockcue-sense-check
agent4drone/.venv/bin/python docs/results/flockcue-sense-default-mac-20261111/verify.py simulate --out /tmp/flockcue-sense-check
```
