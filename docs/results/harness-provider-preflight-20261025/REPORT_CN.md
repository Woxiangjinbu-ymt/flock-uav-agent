
日期：2026-10-25。已完成独立的配置预检包、MockTransport 限定的 DeepSeek 适配候选与纯成本账本。**真实模型 API 0 次、模拟器 trial 0 次、新增线上授权及预算均 0；未启动训练。** 公开资料核验访问了官方文档网页，没有访问模型、余额或账户 API。

## 当前配置与真正发现的问题

当前应用选择的是 `bailian_qwen_flash / qwen3.8-flash`；冻结自然验证计划沿用 `deepseek-flash`。同一配置文件已有 `deepseek_official`，地址为官方 `https://api.deepseek.com`，凭据存在。新预检显式引用这一候选，**没有修改应用选择，也没有验证凭据有效性**。输出只含白名单元数据、凭据是否存在及字段引用，不保存密钥、密钥哈希或完整配置文件哈希。详见[配置审计](CONFIG_AUDIT_CN.md)。

官方当前将 `deepseek-flash` 列为 `deepseek-flash`，同名别名会滚动升级；历史同名任务不能直接视为当前同版本对照。thinking 默认开启，默认 effort 为 high；该模式下 temperature 不生效。因此历史 `temperature=0.1` 本身不足以冻结推理行为。[官方价格与模型页](https://api-docs.deepseek.com/quick_start/pricing/)、[thinking 说明](https://api-docs.deepseek.com/guides/thinking_mode/)

本地 SDK 会把 `max_tokens` 改写为 `max_completion_tokens`，后者未列入当前 DeepSeek Chat 参考；不能据此断言服务一定拒绝，但必须核对实际请求。SDK 本身不保存 DeepSeek reasoning 字段，**旧 UAV Agent 已有全局兼容补丁**，所以本次没有认定旧完整 Agent 必然丢失这些字段。新候选改为显式、局部的兼容逻辑；未修改旧 Agent 或其补丁。

当前官方要求 thinking 且带 tools 的后续请求保留全部历史 reasoning，包括跨新用户轮次；候选据此验证，不沿用“新用户回合统一清除”的假设。更多参数、usage、定价、文档差异与未验证项见[公开资料核验](PROVIDER_FACTS_CN.md)。公开文档支持的是接口依据，不等于当前账户实际可用或服务端接受已验证。

## 已实现和验证

| 交付 | 验证结果 | 边界 |
| --- | --- | --- |
| `scripts/harness_deepseek_adapter.py` | 最终 **54/54** 专项通过；实际 SDK 的 2 条未 patch baseline、4 条候选请求/响应正文有 base64、长度及 hash | 只接受 SDK MockTransport 和合成 key；同步、非流式、文本消息，不是在线入口 |
| `scripts/harness_cost_admission.py` | — | 仅单 worker 串行账本；不执行网络、不核验账户、不提供持久化/分布式预算 |
| `scripts/harness_provider_preflight.py` | **119/119** 合成测试通过；实体 prepare/verify 同样通过 | 无 run/launch 命令；任何配置下都不授予在线执行权限 |

三组测试共 242 个不同测试项；重复执行轮次不增加分母，也不计成研究任务成绩。适配器首次 46/47 的失败发现 `.stream()` 会在 SDK 中回退同步 invoke，已明确拒绝公共流式入口；随后 47/47，通过交叉审查再补 7 项新响应工具 ID/参数边界，最终 54/54。所有轮次保留。响应工具调用在返回 Agent 前检查无缺失、重复或历史冲突 ID，避免到下一次请求才发现错误。

[最终独立审查](INDEPENDENT_REVIEW_CN.md)的 48 项静态检查通过，527 个输入哈希在审查后保持一致；未读取真实凭据、运行 SDK、模拟器或项目 verifier。它与代码测试有不同分母，不相加为任务样本。

[SDK 对照说明](DEEPSEEK_ADAPTER_CN.md)及 `adapter-wire-evidence-attempt03.json` 保留实际正文。候选使用 `max_tokens`，显式 thinking/effort；enabled 时省略无效 temperature，有 tools 时保留所需 reasoning 和 tool-call 关联，无 tools/disabled 时移除 reasoning。归档材料使用合成文本，不是 Teacher 轨迹。

成本账本先为完整一对 A/F 各自预留同样的最大 HTTP 次数和金额，任一臂先运行均不侵占另一臂资源。每请求先预留，再按已知 usage 以峰值 cache-miss 价保守结算；已知完整的普通任务失败可保留并结束。**provider 错误的分类仍需要上层明确传递**；


[实体预检包](../../../experiments/harness-provider-preflight-v1/manifest.json)共 6 个文件；候选技术值来自本轮草案，尚未投入执行：

| 项目 | 草案 |
| --- | --- |
| 路由 / 模型 | 显式 `deepseek_official / deepseek-flash`，保留文档版本记录 |
| 输出 / HTTP | 每请求最多 8,192 输出 token，每回合最多 20 次 HTTP |
| 时限 | 请求 60 秒、排空 5 秒、worker watchdog 515 秒；原任务窗口仍为 500 秒 |
| 模式 | thinking 尚未选择；enabled 时再明确 low/high/max，disabled 时沿用 temperature=0.1 |
| 币种和金额 | — |
| 权限 | — |

这些值是把下一次讨论变成具体方案，**不是已选定的正式实验配置**。20 次 HTTP、8,192 输出限制会影响任务表现，后续采用时必须同时固定两臂并记录截断，不能与旧未设这些限额的成绩作无条件对比。未定项不按官方默认值悄悄补齐，也不覆盖旧 14 槽准备包。

为避免提前消费不确定的缓存/时段折扣，条件上界取文档模型列表示例的 1,048,576 上下文 token 全作输入，再加 8,192 输出；逐请求按峰值 cache-miss 价计算。两个币种采用各自官方价表，**不是汇率换算**。上下文数来自文档示例，本轮未向账户查询 `/models`。

| 条件上界 |
| --- |
| 每请求 |
| 每回合 20 请求 |
| 一对 A/F 预留 |
| 14 槽全部达到上界 |

若预算不能覆盖整对预留，应拒绝开始该对；更紧且可靠的输入上界或不同技术上限需另行验证，不能直接把保守估计改小来放行。

## 状态与下一步

仍未完成：

1. 确定 thinking/effort、币种和整批上限；单回合上限结合技术边界及整批额度计算后形成新版本。本轮尚未收到这些选择，v1 保留 null；这不阻碍下一项的本地 MockTransport 接线测试。
2. 将候选 SDK 和成本预留接到实际 worker 的传输边界，先以 MockTransport 验证“拒绝即无派发”、异常锁额、跨回合账本与停批；本轮三个模块仍独立，不能把纯测试通过等同于该接线已完成。
3. 冻结滚动模型身份记录/变化处理规则、新执行配置和资源授权，再验证真实 provider 的认证与参数接受；自然选择、恢复、接手仍按原 7 题/14 槽协议进行，不补题、不续跑 360 次、不自动进入训练。

此批为新的独立证据目录，旧研究成绩、旧封存和原始 HTML 不修改。研究状态与 Roadmap 同步到以上边界；短期参数不堆入 AGENTS。最终源码、测试和两份总览副本在 `delivery-source/`，当前目录及外部 6 文件预检包的哈希由 `artifact-seal.json` 记录。
