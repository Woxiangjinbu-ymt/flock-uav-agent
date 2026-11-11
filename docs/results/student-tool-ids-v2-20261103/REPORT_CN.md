# 同名并行工具输入限制：v2 方案与本地尝试

日期：2026-11-03。承接第四项的 10 条模板拒绝，采用“历史调用与回执显式 ID”的输入适配，已完成本地格式与关联验证。**98 条原请求均可准备，原先拒绝的 10 条全部通过；61 条原开发样本的当前监督目标逐 token 不变。** 这证明信息表示和标签链路兼容；尚未验证学生模型理解或任务成功率。

## Confirmed Decisions — 解决什么、选择什么

问题来自原生模板省略 call ID 与 v1 的保守准入规则，不是已经证明 Qwen3 不能处理同名工具。10 条请求来自同一个旧搜索回合的 model-4 至 model-13，反复包含 6 个同名并行调用组，每组 5 个不同 ID/无人机：1 组起飞，5 组导航感知。实际 wire 中的回执都有序；原始执行记录中有两组工具完成顺序不同（一个三工具组、一个五机导航组），随后请求均恢复调用顺序。因此真实证据证明同名并行，乱序输入兼容须单独用合成案例检验。

| 备选方案 | 判断 |
| --- | --- |
| 原生模板按位置关联 | 可用于严格有序、完整回执的限定协议，改动最小；需额外确认模型理解位置语义，不保留乱序回执的显式身份 |
| **历史调用与回执增加 ID** | **本轮实施**：保留并行与实际消息顺序，显式表达关联；需要统一训练/推理模板，并承担少量 token 增量 |
| 全部串行调用 | 会改变调度、时延与多机并行行为，本轮不采用 |

协议与预定检查见[方案](../../STUDENT_TOOL_ID_ADAPTER_CN.md)和[冻结计划](../../../experiments/student-tool-ids-v2/plan.json)。沿用固定 tokenizer，新增[模板](../../../training/templates/qwen3-tool-ids-v2.jinja)和[适配器](../../../scripts/student_input_v2.py)。官方支持自定义 chat template，但模型实际表现取决于训练/推理格式是否匹配，参见 [Transformers 4.57.3 文档](https://huggingface.co/docs/transformers/v4.57.3/en/chat_templating_writing)。这里的 ID 包装是本地新增协议。

## 实际格式与多轮接续

按完整历史中调用出现的顺序分配 `call_1`、`call_2`，并把原始 ID 与规范编号的映射保存在审计 sidecar。只改外层关联 ID，业务参数和回执字符串不改写。历史调用和回执分别呈现为：

```text
<tool_call>
{"id":"call_1","name":"get_drone_status","arguments":{"input_json":"…无人机A…"}}
</tool_call>
<tool_call>
{"id":"call_2","name":"get_drone_status","arguments":{"input_json":"…无人机B…"}}
</tool_call>
<tool_response>
{"id":"call_2","name":"get_drone_status","content":"…B的回执…"}
</tool_response>
<tool_response>
{"id":"call_1","name":"get_drone_status","content":"…A的回执…"}
</tool_response>
```

以上是说明用合成示例，省略号不属于真实数据。回执顺序可与调用顺序不同，关联按 ID 验证，不能按工具名或参数猜测。即使工具名和参数都相同，不同 ID 仍是不同调用。缺失、重复、未知、跨轮和名称不匹配的回执拒绝。

当前 assistant 输出仍使用原生 `name/arguments` 结构，**不要求模型生成编号**。严格解码器解析并验证全部调用后，运行时分配接续编号；它返回数据，不执行工具。下一轮只接续已规范化的历史，避免供应商 ID 恰叫 `call_N` 时发生混淆。带 prior 的接续必须保留原 canonical 历史前缀，不能先重新编号再比较，从而防止交换已有 ID 被重编号掩盖。

`prepare_history/prepare_prompt` 同时服务编码和推理前缀；完整 token 前缀必须一致。JSON 包装中的角括号转义后可还原原内容，解码工具参数采用完整 JSON 解析，不按字符串中第一个闭标签截断。未知字段、重复 JSON key、损坏尾部和生成 ID 不静默接受；不返回半个有效调用列表供执行。这里校验格式、关联、已声明工具及参数对象，不替代工具业务 JSON Schema 校验和执行结果判断。

## Verified Results — 固定全分母

| 检查对象 | v1 | v2 | 证据身份 |
| --- | ---: | ---: | --- |
| 固定 wire prompt | 88/98 可准备，10 拒绝 | **98/98 可准备** | 60 条脚本 run 归档 + 38 条旧真实 provider run 归档；后者含 1 条未实际派发记录 |
| 原转换样本编码 | 61/61 | **61/61** | 37 条脚本开发 + 24 条旧真实开发，均非 Teacher train |
| 当前监督 token | 原标签 | **61/61 完全相同** | 包含当前输出与 EOS；历史 ID/回执包装全部 masked |
| 合成边界案例 | 本轮单列 | **12/12 符合预期** | 4 正例、8 负例；包括同名、同参数、反序回执和损坏关联 |

逐请求对比、逐样本 token 对比、并行组清单与合成检查见[正式包](run-01/manifest.json)和[汇总](run-01/comparison.json)。编码产物保持通用 token 数组 schema，以不同 adapter/template_id 区分版本，原 v1 加载器可直接验证；没有修改 v1 源码或历史封存。

原始 22 计划槽、98 决策、61 导出、37 排除的转换分母不变。搜索失败/partial 的 10 条 prompt 兼容后仍只作诊断，不新增标签或改称成功示范。正式 Teacher 数仍为 0。

## Verified Results — token 增量和上下文

同一组 61 条完整序列的 token 总量由 736,669 增至 749,200，增加 12,531（**1.70%**）；单条增量为 27–567，p50=205。增加来自 ID、回执包装和协议说明，不是模型生成更多推理。该统计是学生 tokenizer 下的格式成本，不能等同 DeepSeek 账单、GPU 开销或任务执行耗时。

61 条完整序列现在为 9,988–14,885 token，全部仍可放入 16k。全部 98 条 prompt 则为 9,957–30,218 token，p95=18,638；原来拒绝的历史现在可以测量，显现出 6 条超过 16k 的长搜索输入。

| 长度预算 | 61 条完整样本可容纳 | 98 条 prompt 可容纳 | prompt + 2048 输出预留可容纳 |
| --- | ---: | ---: | ---: |
| 4096 | 0 | 0 | 0 |
| 8192 | 0 | 0 | 0 |
| 16384 | 61 | 92 | 89 |
| 32768 | 61 | 98 | 98 |

超过 16k 的 6 条均为旧搜索 model-8 至 model-13；最长 model-13 是已记录序列化内容但未派发的请求。**16k 只能作为当前 61 条样本的 smoke 档位；本批全请求若保留完整历史，32k 加 2048 输出预留可以容纳。** 这不是未来任意任务的长度保证或 GPU 配置结论。输入未截断，也未降低任务要求；2048 是预定诊断档位，最终生成预算待采数配置冻结。

## 验收、复现与边界

[专项与回归日志](pytest.log)共 **265 项通过，0 失败、0 跳过**：新增 corpus 21 项和 v2 适配 41 项，加上 v1 的 203 项回归。[加载检查](loading-smoke.json)覆盖 61 条、31 批；[独立复核](final-review.json)和[最终汇总](final-verification.json)绑定来源 seal、token/label、模板与脚本哈希。开发中间包位于 tmp；独立复核发现的 prior ID 交换缺口修复后才进入正式封存。[v1 原验收重核](v1-reverification.json)通过，原包和依赖文件保持原 hash，新划分 test 未用于本轮输入开发。

在仓库根目录运行，输出必须采用新目录：

```sh
training/.venv-input/bin/python scripts/validate_student_tool_ids.py \
  --output tmp/student-tool-ids-v2-new
agent4drone/.venv/bin/python scripts/student_sample_loader.py \
  --package docs/results/student-tool-ids-v2-20261103/run-01 \
  --pad-token-id 151643 --output tmp/student-tool-ids-v2-loading-new.json
```

本轮没有网络 provider 派发、模型权重、forward 或训练。适配层证明输入关联、标签和多轮数据接口成立，尚未接入真实学生生成服务。自定义模板能否被基座或 SFT 模型正确理解、能否改善工具选择/成功率，需要后续同 Harness 模型评价。

## Next Actions

建议把本 v2 作为后续采数与 SFT 输入候选：训练和推理同时采用同一适配器，并保留 v1 作为对照。正式配置需决定是否为长搜索保留 32k 及输出预算，或另立可审计的历史压缩方案；不能静默截断后继续声称完整对应。通用 provider driver、真实接口验收、新 train 来源及质量准入、采数额度仍按主线推进；这次修复没有解除这些门槛。
