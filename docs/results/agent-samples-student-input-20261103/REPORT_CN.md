# 第四项：样本转换与学生输入首版验收

日期：2026-11-03。工作包 W3 的有界本地交付已完成：受封存的实际交互归档可转成逐次决策样本，经独立审计后，用固定 Qwen3-1.7B tokenizer 生成输入与当前 assistant 标签，再完成加载和 padding 检查。**本批只有 development 格式验证数据，正式 Teacher 样本数为 0。** 没有新增 provider 调用、下载模型权重、训练或学生能力评价。

## Confirmed Decisions — 转换与监督范围

实施前固定[22 槽来源计划](../../../experiments/agent-samples-v1/source-plan.json)，协议见[样本契约](../../AGENT_SAMPLE_CONTRACT_CN.md)和[学生输入说明](../../../training/README_INPUT_CN.md)。一步是一次逻辑模型决策，不是一次物理动作。实际 HTTP wire 请求提供完整 messages/tools，实际 assistant 响应提供唯一当前目标；模型开始时的中间消息不能代替最终 wire。

主 Agent 的原工具调用、子程序 start/resume/cancel 和最终文本属于首版职责候选。历史 system/user/assistant/tool、工具定义、角色前缀和 padding 均不参与 loss；仅当前 assistant 内容/工具调用及结束 token 参与。固定 Harness 内部的动作不扩成模型标签。provider reasoning 字段从当前目标和历史输入同时移除，只保留移除位置、字符数及哈希；原始封存不改写，工具业务参数中同名字段不误删。

导出门槛是正常返回、排空与退出封存、完整归档、独立原题 checker 通过、wire/模型响应/执行 span 的 ID/名称/参数匹配。首版多 attempt、流式和未知内容格式明确拒绝。当前决策含失败、参数错误、partial 或未知工具效果时，整条当前目标排除；失败及其回执仍可作为后续修正/恢复样本的 masked 历史。整题评分通过不是逐步最优性或人工质量审核。

旧开发轨迹保持原身份；未来 train 身份验证辅助尚未接通完整新采集初态/质量证明。生产训练导出与加载入口均显式禁用，不能靠更改 split/eligibility 字段晋升旧数据，见[负向门禁验收](production-gates.json)。

## Verified Results — 全分母与筛选

来源为第二项八批 14 槽、第三项维护入口 2 槽、旧 DeepSeek pilot 6 槽，合计 10 个 run、22 个计划槽。源清单在本轮转换前已固定，包含取消、超时、预算截断、未排空、未知 usage、评分失败和未运行槽。

| 来源 | 计划槽 | 可导出回合 | 排除回合 | 未运行 | 逻辑决策 | 导出样本 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 本地脚本模型开发归档 | 16 | 8 | 7 | 1 | 60 | 37 |
| 历史真实 provider 开发归档 | 6 | 4 | 1 | 1 | 38 | 24 |
| 合计 | 22 | 12 | 8 | 2 | 98 | 61 |

61 条中，49 条当前目标含工具调用，12 条为最终文本。两类来源分开标识，真实日志中的 24 条也不是新采集 train。历史真实模型及协议仍以[DeepSeek 原报告](../deepseek-behavior-pilot-20261102/REPORT_CN.md)为准；98 条是逻辑调用/wire 记录数，不能当作本轮或历史真实派发次数。

37 次排除决策完整保留：26 次来自非正常结束回合，3 次来自缺失 worker 报告的未排空回合，1 次来自整题 checker 未通过回合，7 次是正常成功回合中的失败/无效/未知工具决策。后者包括原工具修复前的失败、未知效果、参数错误、终态误 resume、失效技能 resume，以及历史 DeepSeek 导航中的错误并行调用；不从一个并行决策里只截取成功工具。

[转换包](conversion-run-01/manifest.json)保存 samples、episodes、decision_inventory、exclusions 和协议/转换器/来源计划快照。[独立审计](sample-audit.json)不用转换器生成的中间解释替代原证据，重建所有来源 seal、决策关联、回执一致性、推理剥离与 mask，22/98/61 分母通过。94 条推理移除记录包含历史在多个样本中重复出现的字段，不是 94 个独立推理过程。

## Verified Results — 学生输入、长度与加载

使用官方 [Qwen3-1.7B 固定 revision](https://huggingface.co/Qwen/Qwen3-1.7B/tree/70d244cc86ccca08cf5af4e1e306ecf908b1ad5e) 的 tokenizer/config/原生工具模板，只下载 8 个小文件（共 15,907,343 字节），未下载权重。tokenizer manifest、每个文件和模板哈希固定；独立 Python 3.11.16 环境及依赖锁见[输入环境](../../../training/README_INPUT_CN.md)。模型卡 context 为 32768，固定 config 的 max_position_embeddings 为 40960，本轮保守适配上限为 32768，差异不用于扩大训练配置。

原生非思考模板没有可靠的 generation mask 标记，因此分别渲染 generation prefix 和完整消息，实测文本与 token 前缀完全一致，再以完整当前后缀生成 labels。模型角色前缀和空思考 scaffold 仍在 masked prompt；没有教师推理监督。工具调用 arguments 的外层 JSON 只解析一次，内部业务字符串保持语义。默认完整输入或整条拒绝，没有静默截断、packing、删工具或压缩 schema。

| 预算 | 61 条完整样本可容纳 | 超长 | 模板拒绝 |
| --- | ---: | ---: | ---: |
| 4096 | 0 | 61 | 0 |
| 8192 | 0 | 61 | 0 |
| 16384 | 61 | 0 | 0 |
| 32768 | 61 | 0 | 0 |

完整序列最短 9,961、最长 14,595，p50=12,043，p95=13,509；当前监督目标为 11–288 token，p50=38。真实开发来源 24 条为 9,961–14,093，脚本来源 37 条为 11,288–14,595。结果见[长度报告](student-input-run-01/length-report.json)和[编码包](student-input-run-01/manifest.json)。**16k 是本批最小可容纳输入档位，不是已冻结训练 context，也未证明未来任务或实际 GPU 可承受。** 输入明显长于当前目标，mask 不会消除长 prompt 的训练计算与显存成本。

为避免成功筛选隐藏困难输入，另对固定来源的全部 98 次逻辑调用/98 条 serialized HTTP 请求作[仅 prompt 诊断](context-diagnostic.json)：88 条兼容、prompt 为 9,930–14,584 token；另外 10 条（旧搜索 baseline 的 model-4 至 model-13）因同名工具并行历史不兼容而拒绝，未伪造目标或生成标签。末次 wire 记录不等于实际 provider 已派发。不能把 88 条的长度结论推广到全部 98 条，也未为未来回答预留空间。

原生 Qwen 模板不输出 call ID 或回执名称。首版只接受有序、工具名不同的并行历史，同名并行和乱序回执明确拒绝；ID 保留在审计 sidecar，不能声称进入了 token。这是当前输入版本的重要限制，正式采集前需选择保留 ID 的模板版本或明确约束动作空间，并保持师生训练/推理一致；本轮没有偷偷重排历史。

[加载 smoke](loading-smoke.json)覆盖全部 61 条、31 批（最后一批 1 条），检查数据完整性、当前目标边界、token/label 对齐、padding ID、长度上限和重复样本。模型输入只含 input_ids/attention_mask/labels，评估私有元数据不送入输入。实现是无框架 Python 数组校验；没有 PyTorch/GPU dataloader、forward、optimizer 或训练吞吐验证。

## 代码验收与复现

[核心测试](pytest-core.log) 150 项通过：转换器 52、独立审计 46、加载器 44、全请求 prompt 诊断 8。[实际 tokenizer 专项](pytest-student.log) 53 项通过；共 **203 项通过，0 失败、0 跳过**。覆盖伪造/缺失关联、重复调用、重试、失败目标、reasoning、mask 错位、模板损失、并行歧义、超长拒绝、源码/封存变动、padding、test 拒绝和伪晋升训练字段等边界。

[转换复现](conversion-reproducibility.json)用正式封存转换器重新处理相同历史输入，9 个输出文件逐字节一致。[源码快照](source-manifest.json)保留 17 个本轮文件；[最终独立复核](final-review.json)及[最终验收汇总](final-verification.json)绑定正式产物和测试结果。历史 run 的 seal 与新划分锚点不变；本轮不读取新 test 题作为学生样本。开发中间产物留在 tmp，不混入正式结果。

在仓库根目录执行；输出必须采用新路径，不能覆盖封存包：

```sh
agent4drone/.venv/bin/python scripts/convert_agent_samples.py \
  --source-plan experiments/agent-samples-v1/source-plan.json \
  --output tmp/w3-reproduction-new
agent4drone/.venv/bin/python scripts/audit_agent_samples.py \
  --dataset docs/results/agent-samples-student-input-20261103/conversion-run-01 \
  --output tmp/w3-independent-audit-new.json
training/.venv-input/bin/python scripts/student_input.py \
  --samples docs/results/agent-samples-student-input-20261103/conversion-run-01/samples.jsonl \
  --source-manifest-sha256 9377805432cdcd1041305d2e2e7d689f91686388867acb42da8c1a12bbabf2be \
  --output tmp/w3-student-input-new
agent4drone/.venv/bin/python scripts/student_sample_loader.py \
  --package docs/results/agent-samples-student-input-20261103/student-input-run-01 \
  --pad-token-id 151643 --output tmp/w3-loading-new.json
training/.venv-input/bin/python scripts/audit_student_context.py \
  --output tmp/w3-context-new.json
```

## Open Questions / Next Actions

首版验收证明样本转换与输入标签链路可用，不证明示范最优、模型会使用工具、失败恢复收益或训练已开始。旧 checker 的语义局限和样本来源偏差仍在。Qwen3-1.7B 是约 2B 输入候选，正式学生基座、职责、context、输出预算、GPU 和训练配置未冻结。

接下来 W4 先补齐通用在线 driver 与 v2 真实接口验收，绑定 W2 train 的实际初态、来源和质量审核；同时处理同名并行模板语义及长度/输出预留，冻结小批 Teacher 协议与额度。完成这些条件后才能采数、审核并开启生产训练导出。W5 再做模型权重加载、少量训练步数、训练产物导出和同 Harness 对照；
