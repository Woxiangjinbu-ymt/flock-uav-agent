
2026-10-25。只读源码、冻结准备包及唯一指定的 `agent4drone/llm_settings.json`；没有导入 Agent/SDK、运行任务、请求 provider、读取其他凭据或增加线上预算。配置只记录以下白名单元数据，URL 已排除 userinfo、query、fragment；未保存原配置、密钥、密钥片段、密钥哈希或配置文件哈希。[安全元数据与非秘密源码哈希](config-audit-evidence.json)可复核本次范围。

## 可确定的配置与边界

| 来源 | Provider / 类型 | 模型 | 去敏 base URL | 凭据存在 |
| --- | --- | --- | --- | --- |
| 当前 selected_provider | bailian_qwen_flash / openai-compatible | qwen3.8-flash | https://dashscope.aliyuncs.com/compatible-mode/v1 | 是 |
| 同文件中匹配冻结计划的候选 | deepseek_official / openai-compatible | deepseek-flash | https://api.deepseek.com | 是 |

凭据存在不代表认证有效；配置中的模型字符串不证明当前服务可用、版本固定或参数受支持。冻结计划候选为 `deepseek-flash`、temperature=0.1，因此应显式绑定候选 profile，不能自动沿用当前 Qwen 选择。此次未检查环境变量凭据，不能推断其存在或优先级下的最终值。

`uav_agent.py:188` 的默认配置路径相对于工作目录；CLI 交互选择和直接构造 Agent 是不同入口。CLI 还会回退到环境变量；配置选择器会显示完整 base URL 和密钥末尾片段，因此不应调用它来生成预检报告。

`harness_natural_runner.py` 当前禁止读取实际配置，固定使用合成 key、`offline.invalid` 和注入 transport；有界 HTTP、非流式、零 SDK 重试等已通过本地验证，**尚不是可直接运行真实 provider 的入口**。干运行只应准备独立配置证据，不修改旧 runner、旧 80 源码或已封存准备包。

## SDK 映射与未决字段

本地安装源显示 `langchain_openai 1.6.3` 的 ChatOpenAI 在 `base.py:3711–3731` 把 `max_tokens` 转为 `max_completion_tokens`；`openai 3.17.0` 的 Chat Completions 入口发送 `/chat/completions`。因此仅检查构造参数不足以确认实际 wire 字段。需在无网络 transport 中核验最终 method/URL/body，并显式锁定 Chat Completions、`streaming=False`、`max_retries=0`、同步调用及禁用环境代理；本地序列化通过仍不证明供应商兼容。

当前 natural runner 没有显式传入 thinking 开关，`uav_agent.py` 保留 reasoning_content 的补丁也不等于关闭 thinking。不能从旧报告或 MockTransport 的合成 usage 推导。

`harness_request_limits.py` 只在完整响应后累加严格的三项 usage，并据此停后续派发。一个已经派发的请求可能超过剩余 token 停线。

## 预检拒绝条件与公开/私有分离

1. Provider、模型或显式 endpoint 与选定候选不一致；URL 含 userinfo/query/fragment、异常 scheme 或未允许的 host/path；profile/credential 不存在或类型不符。报告可去敏 URL，但不能把危险原 URL 静默清洗后当合法执行配置。
2. 实际请求转入 Responses、流式/异步、隐式重试、未冻结额外 body/header/query；输出上限缺失、类型非法或实际 wire 上限与约定不符。运行时准入器接受两种输出字段，不代表供应商也接受。
价格候选计算与本地技术检查不能把 `online_ready` 改成 true。
4. 准备包 hash、任务顺序、来源引用或 public catalog 白名单不符；私有 checker、评估注解、原状态 pickle、canary、凭据/认证头进入模型输入或公共配置档案。

公开材料只放脱敏的模型配置、任务公开文本和可验证请求参数。密钥仅以受控来源引用供未来获授权的执行器取用，不复制进计划/归档；评估树与状态保持独立 scorer 权限。实际模型 I/O 的正文归档也不得扩成 headers/认证记录。

冻结准备包 7 个内容文件哈希均匹配：7 个已知开发任务、14 个拟议槽、AF4/FA3，仅 scheduler 不同；不是新执行额度。`authorized_executions=0`、所有线上预算=0、`online_ready=false` 保持不变。旧包中的未完成 gate 是创建时记录；
