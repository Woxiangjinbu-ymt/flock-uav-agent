# 完整 episode 本地验收的独立产物审计

日期：2026-10-25。审计对象：[run-20261025-02](run-20261025-02/)，仅使用已封存文件及冻结源码；没有重新运行 worker、模拟器、模型、评分器或 pytest。

**结论：297 / 297 条独立产物检查通过，没有发现阻断该限定验收的证据问题。** 这 297 条是审计断言，既不是独立任务分母，也不是 pytest 数量。可复核脚本为 [independent_audit.py](independent_audit.py)，机器结果为 [independent-audit.json](independent-audit.json)。

## 独立核对内容

- 重算封存清单的 302 个文件、冻结及当前的 80 个源码文件、4 个输入引用和原 full100 manifest 的 410 个文件的 SHA-256，均一致；没有未列出的 campaign 文件。协议和 manifest 摘要一致，审计前后 campaign seal 未变化。
- 9 个预先指定回合对应 18 个不同 PID，均正常退出；每个 scorer 在对应 worker 退出后启动，下一 worker 在上一 scorer 退出后启动。A 回合的退出封存文件在全部 B 回合结束后仍一致。
- 对 9 份初态 JSON 独立去除唯一派生 canary 字段后，均恢复原 fixture 状态摘要；原 checker 树摘要一致。评分器读取的 snapshot 字节及 worker-result 摘要与退出封存一致。本次审计没有反序列化 pickle，也没有重新执行 checker。
- 9 个私有评分产物与评分 API 日志均检出 canary；9 个回合共 90 个 policy 文件及其中实际 HTTP 正文的 base64 解码内容均未检出 canary 或非空私有检查树。重组的响应正文也未检出 canary。全部策略侧 World API 记录均为 task 阶段、非 admin，没有私有检查调用。
- 按实际 MockTransport 独立 oracle 重新比对请求和响应的完整字节长度、SHA-256、响应状态及 chunk 重组结果；核对 model logical call、attempt、原始 tool call ID 和工具返回关联。成功归档完整；故障、超时、取消归档均已关闭但明确不完整。
- 4 个后续 B 的原始 HTTP 请求正文，仅替换本回合已知 command UUID 与 frame plan ID 后，与 reference-B **逐字节相同**；没有替换时间、状态、工具结果或其他内容。其初态、原 checker 结果、动作签名和记录成本也与 reference-B 相同。

## 四类终态与真实动作边界

| 回合条件 | 数量 | 实际成功提交的动作 | task API / 模型 HTTP 次数 | 原 checker | 归档 |
|---|---:|---|---|---|---|
| reference、success-A、4 个后续 B | 6 | take_off(14 m) → rotate(353°) → hover | 每回合 14 / 3 | 通过 | 完整、关闭 |
| failure-A | 1 | take_off(14 m) | 5 / 3，第三次 HTTP 500 | 未通过 | 不完整、关闭 |
| timeout-A | 1 | take_off(14 m) | 7 / 2 | 未通过 | 不完整、关闭 |
| cancel-A | 1 | take_off(14 m) | 7 / 2 | 未通过 | 不完整、关闭 |

每个起飞动作都可关联公开成功回执、committed 标志及物理 history：高度从 0 变为 14 m。超时和取消均在 request 38 的真实起飞提交后触发，后面没有新的任务 API；分别记录 `interrupted/deadline`、`interrupted/cancelled`。超时使用原 500 s 阈值的虚拟时钟，取消使用真实 cancel flag。故障回合在成功起飞后收到脚本指定的 HTTP 500，不能把故障归档计作完整示范。

预算保持 500 s、每 plan 2000 API、16 repair、16 search rounds，Agent 上限 600 iterations / 1800 recursion。所有 B 成功回合成本完全一致；这里没有推导吞吐、节能或模型能力收益。

## 限定解释与保留失败

这支持的是**一个原始 simple_chain 任务 b1eb0270 上，可信本地脚本经真实 Agent/SDK/工具链进入模拟器，结束后独立进程评分，再进入新进程 B 的闭合与隔离验收**。9 回合是固定生命周期案例，不是 9 道独立 benchmark；使用 MockTransport 和虚拟时间，没有自主 LLM、外部 provider、训练或泛化结论。

canary 证明本批实际记录的模型可见输入与评分资料隔离；worker 仍是能读取派生 fixture 的可信本地代码，这不是针对恶意代码的 OS 权限沙箱证明。迟到 callback 与终态动作屏蔽依据冻结实现、已执行 probe 标志及最终文件 seal 交叉核对，本次审计没有另发 callback 或追加任务。

[run-20261025-01/incomplete.json](run-20261025-01/incomplete.json) 及其日志完整保留：第一个 worker 在导入依赖时遇到 `TypedDict/extra_items` 混环境错误，退出码 1，完成 worker / scorer 均为 0。修正依赖路径优先级后使用独立 run-02；不能把 run-01 合入任务成功率或当成在线模型失败。

本报告的 297 条审计检查、runner 的 96 条验收检查及另行记录的代码测试具有不同分母，不相加为任务表现。
