# DeepSeek 候选适配器本地验证

新增独立 `scripts/harness_deepseek_adapter.py`，未接入旧 runner、未修改旧 80 源码，也未调用真实 provider。工厂只接受当前 SDK 的 MockTransport，固定合成 API key；没有实际凭据读取或线上执行入口。thinking 必须显式选择 enabled/disabled，此处不替用户冻结模式。

enabled 要求显式 `reasoning_effort=low/high/max`，请求不发送 temperature；disabled 不允许同时指定 effort，temperature 默认 0.1。两种模式均固定 Chat Completions、非流式、零 SDK 重试、禁用环境代理与重定向。实际请求使用 `max_tokens`，不用 SDK 默认改写后的 `max_completion_tokens`；允许范围 1..393216。仅支持文本消息和完整、无歧义的工具调用/结果关联，异步、流式、未知角色、缺失/重复/错配 tool ID 及未覆盖请求覆盖项会拒绝。

当前官方 [thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)要求：启用 thinking 且请求带 tools 时，保留**全部历史** assistant reasoning_content，包括新 HumanMessage 之前的轮次；不能沿用“新用户回合一律清除”的旧假设。候选将响应 reasoning 保存在 `AIMessage.additional_kwargs` 并在上述条件下回传；无 tools 或 disabled 时清除请求中的 reasoning。缺少所需历史 reasoning 时拒绝，不补写推测内容。具体公开事实与来源见 [Provider 核对](PROVIDER_FACTS_CN.md)。

[54 项专项](adapter-tests-attempt03.json)全部通过，真实已安装 LangChain/OpenAI SDK 对合成 MockTransport 执行；socket connect 与 DNS 在测试中被阻断。首轮 46 通过、1 失败的日志保留：SDK 公共 `.stream()` 可回退到普通 invoke，补充公共入口拒绝后第 2 轮 47 项通过。独立审查随后要求在返回新 AIMessage 前拒绝坏工具输出；第 3 轮增加缺失/空/重复 ID、坏参数、legacy function 和复用历史 ID 等 7 项检查，总计 54 个不重复测试。历史重复运行不累加计数；这些修订没有调用真实服务。

[正文对照证据](adapter-wire-evidence-attempt03.json)保留 2 次 baseline 和 4 次 candidate 的实际 HTTP 请求/响应字节、长度与 SHA-256，不记录 headers。未 patch 的 ChatOpenAI baseline 使用 `max_completion_tokens` 并丢失响应 reasoning；candidate 改用 `max_tokens`、保留响应 reasoning、工具续轮与跨用户轮次的完整 reasoning 和原 tool ID，输入历史未被原地修改。新增响应验证后，这六条正常请求/响应的证据哈希与第 2 轮一致。

该 baseline 是**未 patch 的 SDK**，不是旧完整 UAV Agent。旧 `uav_agent.py` 已有独立的全局 reasoning 补丁，因此不能由此证明旧完整 Agent 必然丢失 reasoning。新候选不全局修改 SDK，按当前官方 schema 建立局部兼容逻辑，不追溯改变历史实验口径。

这些结果只证明本地序列化、解析和拒绝边界。没有新增自然模型成绩或线上预算。
