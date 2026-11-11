# 决策样本与学生输入契约 v1

日期：2026-11-03。第四项 / W3 的有界本地交付协议。沿用[任务范围](GENERAL_AGENT_SCOPE_CN.md)、[执行契约](AGENT_EXECUTION_CONTRACT_CN.md)、[数据身份](TASK_COVERAGE_AND_SPLITS_CN.md)；不启动新的付费采集、模型权重下载、SFT 或 RL。

## Confirmed Decisions — 监督什么

首版面向主 Agent 的下一次 assistant 决策，覆盖原工具调用、`run_harness` 的 start/resume/cancel 及最终文本。固定 Harness 内部的动作、环境生成的回执、私有 checker 和最终评分不作为模型标签。这里冻结的是转换格式和职责候选；正式学生训练配置、Teacher 数据质量和模型能力仍需后续验收。

一条样本对应一个已闭合的 logical model call：实际 HTTP wire 请求提供 messages/tools，匹配的实际 assistant 响应提供当前目标。`model_start` 只用于关联和一致性核对，不能代替经过全部 middleware 后的真实请求。当前 assistant 前的全部消息和工具定义仅作输入；训练 loss 只覆盖当前 assistant 的内容、工具调用及结束 token。历史 assistant 也不重复计为当前标签。

`reasoning_content`、`reasoning`、`thinking` 不进入学生输入或监督文本，原件保留在受封存的证据中；转换审计仅记录移除位置、数量和哈希，不复制推理正文。非空推理标签文本或不支持的内容类型应明确拒绝，不能猜测性地截出一段作为答案。

## 来源、完整性与质量门槛

预定读取第二项的八批14个计划槽、第三项维护入口2槽、旧DeepSeek行为pilot6槽，共22个计划槽。未运行、被终止而无归档、partial、失败与不支持的情况均进入清单，不能只列成功回合。所有来源保持 development；Mock脚本和真实provider开发轨迹分别标识，当前合格Teacher train数为0。

先复核 manifest、源码/输入/worker/scorer封存及退出、完整归档和原评分对象关联，再提取决策。首版只导出正常返回、完整归档且独立整题checker通过的回合；完整性不等于示范质量。一个logical包含多个HTTP attempt时明确排除，不取最后一个响应，也不把重试扩成多个标签。流式或未知编码/内容形态未支持时拒绝并记录。

wire assistant、model_end、tool_start 的 ID、工具名称及参数必须一致，每个 tool call 只能关联一个执行 span；并行工具按 ID 关联，不按返回顺序猜测。实际请求中的 tool 回执与已记录输出核对，已知 middleware 的公开advisory装饰单独核验来源并保留，不将评估信息补到输入中。

若当前决策包含明确工具失败、参数校验错误、partial 或效果未知，不将该决策作为正向标签；同一次决策的多个工具不能只截取成功部分。错误决策和反馈仍可出现在后续决策的 masked 历史中，后续查询、修正或恢复可单独验收。未执行要求不靠私有最终状态补全，模型的完成声明也不能替代checker；旧checker局限继续保留。

样本记录原run/manifest/archive/HTTP字节哈希、trial/task/scene、logical/attempt/tool关联和过滤理由；评分与私有元数据只用于外部审计，不拼入messages。未来训练准入必须核对W2冻结train的任务、场景、指令/初态身份，真实provider来源及质量审阅记录；旧开发题、dev、test和脚本样本不能因命令行改名进入train。首版只实现未来身份字段的验证辅助，尚未接通新采集产物的完整初态/质量证据核验；生产训练导出显式禁用，不把辅助函数当作Teacher管线已就绪。

## 学生输入与长度

输入兼容候选采用 Qwen3-1.7B，固定官方仓库commit、文件和模板哈希，只下载小型tokenizer/config/template文件，使用独立依赖环境、`trust_remote_code=False`；细节见[学生输入协议](../training/README_INPUT_CN.md)。选择候选不等于冻结最终训练基座或验证其工具能力。

首版使用候选原生工具模板的非思考模式。外层tool arguments按严格JSON解析为object，`input_json`内部字符串保持原语义。若模板不打印调用ID，则先核对消息关联；回执乱序、同一轮重复工具名称等无法在模板中可靠区分的情况单独报告为不兼容，不悄悄重排动作或隐藏丢失。

不直接假设模板提供assistant mask。分别渲染输入的generation prefix与包含当前assistant的完整序列，逐token验证前缀相同，只有完整目标后缀参与loss。角色头、system/user/tool、历史assistant、padding均为`-100`；输入/推理模板与监督模板的边界要实测一致。

测量4096、8192、16384、32768四档总序列预算，分别记录prompt、当前标签及整条长度；正式训练context/GPU配置未冻结。**默认超长拒绝，不截断。** 不能截断当前标签、原任务或破坏工具调用/回执配对。裁剪/压缩若未来实施，应另定义版本和推理时相同变换，并重新检验能力；本轮不能用静默删历史制造“可装下”。

## 验收与后续

交付版本化样本、全部回合/决策过滤清单、独立等价性审计、真实tokenizer编码、loss mask与padding加载smoke、长度分布及失败边界。另对固定来源的全部可恢复wire请求作仅prompt长度诊断，包含失败/partial回合，不为它们生成标签，避免成功样本筛选掩盖长上下文风险。加载验收是无框架Python数组校验，不等于已验证训练框架、GPU或优化器。合成样本用于加载/标签检查，真实开发轨迹用于格式和长度检查，两者都不算正式Teacher训练集；不读取新test任务作为学生开发样本。

下一项W4仍需通用在线driver、v2真实provider验收、学生职责/模板/预算与采数额度落实。W5再进行权重加载、训练、导出和同Harness回接；本轮不以tokenizer成功或loss标签正确宣称学生已会规划、恢复或使用工具。
