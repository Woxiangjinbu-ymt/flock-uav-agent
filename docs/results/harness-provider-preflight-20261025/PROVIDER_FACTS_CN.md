# DeepSeek 官方公开资料核验

检索日期：**2026-10-25，Asia/Shanghai**。本记录只来自 DeepSeek 官方公开文档；未访问模型 API、账户、余额或凭据，未下载 tokenizer。它不选择模型、预算或推理参数，不增加执行授权。结构化事实见 [public-provider-facts.json](public-provider-facts.json)；参数及价格使用十进制字符串。

当前官方价目页把 `deepseek-flash` 对应为 **deepseek-flash**，把 `deepseek-v4-pro` 对应为 **DeepSeek-V4-Pro-0813**。[价目页](https://api-docs.deepseek.com/quick_start/pricing/)。10 月 17 日更新保留 Flash 调用名并升级后端，因此历史相同 alias 不能证明同一模型，也不能把旧成绩追溯重标为当前版本。[更新日志](https://api-docs.deepseek.com/updates/)。旧 Flash/Vision 名称目前属于兼容路由，不能作为旧权重锁定方式。[接口首页](https://api-docs.deepseek.com/)

两者公开规格为 1M 上下文、最大 384K 输出；模型列表文档**示例**给出 `1048576`/`393216`，不是本次实际 `/models` 回执。[模型元数据文档](https://api-docs.deepseek.com/api/list-models/)。Chat Completions 文档采用 `max_tokens`，范围 `1–393216`；省略时非思考/思考/最高思考档默认分别为 `8192/65536/131072`，输入和生成总长受上下文约束。[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)。该参考页未找到 `max_completion_tokens`，不能据此认定兼容或不兼容；Responses 的字段则是 `max_output_tokens`，包含可见输出与 reasoning。[Responses API](https://api-docs.deepseek.com/api/create-response/)

默认 thinking 开启、effort 为 high；OpenAI SDK 的 thinking 扩展放在 `extra_body`。thinking 下 temperature 不生效，因此历史候选 `0.1` 本身不能固定有效采样行为。Flash 的 top_p 仅思考模式有效且最低为 `0.95`；Pro 的规则不同，详见结构化记录。[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)

工具调用同时支持两种模式。thinking 配合 tools 时，后续请求需要完整带回先前 `reasoning_content`，包括未调用工具的轮次；文档警告遗漏可产生 400。[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。工具结果按 `tool_call_id` 关联；工具参数仍须本地校验。thinking 不支持强制 required/指定工具，带 tools 时默认 auto。[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)。strict 是 beta，需 beta URL 和受限 schema；Chat 不支持人为在历史中插入非模型生成的工具调用，不能把 Harness 内部 API 轨迹伪造成模型 tool_calls。[工具文档](https://api-docs.deepseek.com/guides/tool_calls/)

公开 usage 区分 prompt、completion、total 与输入缓存命中/未命中：prompt=hit+miss，total=prompt+completion；reasoning 是 completion 的明细，不能再加一次。Responses 字段名不同。[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)、[Responses API](https://api-docs.deepseek.com/api/create-response/)。实际 token 以返回 usage 为准，字符换算只可粗估。[Token 说明](https://api-docs.deepseek.com/quick_start/token_usage/)。

Chat 的 max_tokens 覆盖 reasoning 是依据“限制 completion”及 reasoning 属于其明细的 **schema 推断**；所查 Chat 参数段未逐字明确该点，不能写成实际验证。Responses 的对应字段则有明确说明。`finish_reason=length` 表示输出或上下文限额导致截断；未找到截断免单/退款保证，也不能把“输出字数”当作精确 token 数。[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)、[Responses API](https://api-docs.deepseek.com/api/create-response/)

以下均为**官方直连每百万 tokens**报价，三列顺序为缓存命中输入 / 未命中输入 / 输出；USD 与 CNY 是各自发布的价表，不作汇率换算，也不为第三方网关定价背书。

| 模型 | 币种 | 高峰 | 空闲 |
| --- | --- | --- | --- |
| deepseek-flash | USD | 0.006 / 0.30 / 1.20 | 0.003 / 0.15 / 0.60 |
| deepseek-v4-pro | USD | 0.044 / 1.32 / 3.96 | 0.022 / 0.66 / 1.98 |
| deepseek-flash | CNY | 0.04 / 2.00 / 8.00 | 0.02 / 1.00 / 4.00 |
| deepseek-v4-pro | CNY | 0.30 / 9.00 / 27.00 | 0.15 / 4.50 / 13.50 |

来源：[英文 USD 价目页](https://api-docs.deepseek.com/quick_start/pricing/)、[中文 CNY 价目页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。高峰为周一至周五 UTC 01:00–04:00、06:00–10:00，排除中国法定节假日；其余时间为空闲。是否按请求开始、完成或其他时刻决定跨边界费率，所查页未说明。可条件式估算 `(hit×命中价 + miss×未命中价 + completion×输出价)/1000000`，但本轮不代入新预算。

公开文档仍有需要保留的冲突和未知：

- 9 月 10 日发布页称 9 月 14 日起 Pro 请求转到 Flash；当前更新日志说继续提供 Pro，价目页另列 Pro-0813。保留两个说法，实际账户路由未核验。[旧发布页](https://api-docs.deepseek.com/news/news260910/)、[当前更新日志](https://api-docs.deepseek.com/updates/)
- 通用 Chat API 的 top_p 描述未区分 Pro，而思考指南给出分型号规则；目前记录指南的细分规则，未声称实测通过。[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)、[思考指南](https://api-docs.deepseek.com/guides/thinking_mode/)
- 文档示例显示的模型版本名不证明存在可固定该日期权重的请求 ID；未来需同时记录 requested model、返回 model、fingerprint、日期和实际参数，仍不能把 fingerprint 当权重校验和。
- 当前公开模型更新日期 10 月 17 日早于本地检索日；价目页另有 11 月 1 日 Search/Fetch 收费的未来通知，本记录不将其提前计入当前模型调用费。[价目页](https://api-docs.deepseek.com/quick_start/pricing/)
- 非流式连接也可能连续收到空行，服务端排队十分钟未开始推理才关闭连接；这不保证本地请求/任务截止，也不是完整 usage 到达。[限速与连接说明](https://api-docs.deepseek.com/quick_start/rate_limit/)
公开资料核验不能把 offline preflight 升格为 online ready。

