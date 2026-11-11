# Provider 本地预检最终复核

2026-10-25，只读复核。未读取真实 settings 或凭据，未运行 SDK、模拟器、provider 或项目预检函数。[机器证据](independent-review.json) 的 **48 项核对全部通过**，527 个输入 hash 末尾回验一致；这些是产物核对，不增加测试或 trial 分母。本审查者编写了成本模块，对适配器与预检器进行了独立交叉检查；成本模块另经适配器作者复核。

- 当前成本模块、adapter、preflight 分别匹配其最终 **69 / 54 / 119** 项测试元数据与日志 hash。早期 adapter attempt 没有合并计数。
- adapter 在返回 AI 工具输出前检查缺失、空、重复 ID、非法参数及旧 `function_call`，并针对本次输入历史拒绝 ID 复用；没有跨调用共享的 ID 集合。6 组既有 MockTransport 请求/响应正文的长度与 hash 全部一致；正常 wire 证据未因最后修复改变。带 tools 的 thinking 历史跨 Human 保留 reasoning，输出字段为 `max_tokens`。这仍是本地 SDK 证据。
- [预检包](../../../experiments/harness-provider-preflight-v1/manifest.json) 恰为 6 文件，全部匹配 prepare/verify 元数据及 manifest。thinking、币种和两项金额仍为 null；20 HTTP、8192 输出、60 秒请求、5 秒排空、515 秒 watchdog 只是技术草案。
- 旧自然 runner 的 **415 个封存产物**、旧自然计划 **8 文件**和 **80 份既有运行源码**均保持原 hash。没有修改旧计划的待验证状态。

按本批[公开事实快照](public-provider-facts.json)，独立使用 Decimal 重算，未调用成本模块：


| 独立公布币种 | 每请求 | 每 trial（20 请求） | A/F 整对 | 14 槽条件估计 |
| --- | ---: | ---: | ---: | ---: |
| USD | 0.3244032 | 6.488064 | 12.976128 | 90.832896 |
| CNY | 2.162688 | 43.253760 | 86.507520 | 605.552640 |

两币种分别按各自价表计算，没有汇率换算或缓存、非峰时折扣。成本账本尚未接入真实传输；普通已知 usage 且归档完整的任务失败可结算，fatal provider/超时/取消需由未来上层明确标记异常并停批。

未发现影响当前“纯本地预检、零授权”用途的阻断缺口；不能据此宣布可以发送真实请求。
