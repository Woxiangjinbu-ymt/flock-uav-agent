# FlockCue 后训练方案：执行事件驱动的选择性思考

更新于 2026-11-09。学生是一个约 2B 的多无人机指挥模型，从 DeepSeek 教师轨迹蒸馏而来，自己选择工具、分配飞机和重规划。研究问题是每一轮该不该输出推理。第 1 步规格、技术方案及预算上限已确认。G3 已通过，G1、G2 待测。[G3 复算报告](results/thinking-events-g3-20261108/REPORT_CN.md) 正式训练集和权重尚未产生。[研究路线](RESEARCH_ROADMAP_CN.md) · [第 1 步规格](specs/01-thinking-events/spec.md) · [技术方案](specs/01-thinking-events/plan.md) · [任务清单](specs/01-thinking-events/task.md) · [验收清单](specs/01-thinking-events/checklist.md)

## 学生学什么

每条 SFT 样本对应教师的一个轮次。输入是截至该轮的公开历史：任务原文、当轮工具定义、此前各轮的回复与推理、工具回执。目标是该轮的回复和工具调用；标签为"思考"时目标前加推理段，标签为"不思考"时推理段为空。只有目标部分计入 loss。

| 组成 | 规定 |
| --- | --- |
| 推理文本 | 用教师 `reasoning_content` 原文作目标（用户 2026-11-08 确认）。四个封存批次中 32.2% 的响应推理 token 为 0。[用量审计](audit/teacher-reasoning-usage-20261108/summary.json) |
| 思考标签 | 执行事件、教师自选、LoGiC、固定间隔四种来源，外加每轮思考、从不思考两个端点。定义见[第 1 步规格](specs/01-thinking-events/spec.md#四种思考标签)。 |
| 标签要求思考但教师推理正文为空 | 保留该轮及其动作目标，记录标签与正文的冲突，有效监督按“不思考”计。[第 1 步技术方案](specs/01-thinking-events/plan.md) |
| 隔离 | 私有状态、隐藏检查器、整题评分和后续轮次不进入输入或目标。整题评分只用于筛选轨迹，筛选规则在采数前冻结。 |
| 本轮题池 | 教师示范和最终的系统对比均使用已确认的原基准 600 题。建议把同一会话的全部任务、重复执行与逐轮样本放在同一侧，两侧各 300 题，交替训练和评测。[冻结题单](../experiments/flockcue-benchmark-600-v1/) · [采数与训练草案](specs/02-benchmark600-sft/spec.md) |

旧的 W3 样本契约禁止推理进入学生输入或监督，它的 61 条开发样本保持冻结，不改写。新样本使用新版转换器和加载器，复用旧版的调用 ID 关联、wire 哈希核对、私有字段清理和 tokenizer 检查。[旧契约](AGENT_SAMPLE_CONTRACT_CN.md) · [ID 适配](STUDENT_TOOL_ID_ADAPTER_CN.md) · [旧输入环境](../training/README_INPUT_CN.md)

## 对照设计

| 臂 | 训练数据 | 回答的问题 |
| --- | --- | --- |
| 从不思考 / 每轮思考 | 同一批轨迹，推理全删或全留。全留与教师自选若形成相同监督，报告二者重合 | 两种端点可提供的监督 |
| 执行事件 | 事件轮保留推理 | 本文方法 |
| 教师自选 | 原样保留教师的推理与空推理 | 直接模仿教师是否已经足够 |
| LoGiC | 原第 1 步固定 Qwen3-1.7B 评分，平均每 token 对数似然下降阈值为 0.001 nat/token。采用新学生时，评分模型与阈值在采集和对照评测前另行登记 | 文献信号与执行状态信号谁更好 |
| 固定间隔 | 每 *k* 轮保留推理，在共同可判轮次上尽量匹配执行事件的思考比例，报告实际偏差 | 收益是否只来自思考次数 |
| RSI（权重不动） | 不训练，只按 dev 错例迭代 Harness 说明 | 训练权重是否必要 |

同一折的训练方法共用教师轨迹、基座、训练设置和 Harness。Qwen3.5-2B 已确认为 G1/G2 的主模型，也是后训练优先验证的学生。正式评测拟按完整会话重复 3 次，并按原题池的任务族、场景难度和实际机队规模报告。在相同协议下配对比较原 Agent4Drone 与 FlockCue（同一 DeepSeek），以及 FlockCue 中的原版和蒸馏学生。[600 题系统对比](specs/02-benchmark600-sft/spec.md#最终怎样比较)

## 资源与预算

教师采数与训练部分为后续阶段的启动前估算，按对应批次的实测和当时报价更新。

- **教师采数。** 首档拟在 30 个完整场景各采一遍，共 600 个教师题次。二遍为 1,200 题次，四遍为 2,400 题次。[历史开发成本](results/deepseek-cost-pilot-20261103/REPORT_CN.md)
- **训练。** 单卡 LoRA，单卡上限 80 GB，40 GB 更好。实际峰值显存、吞吐和上下文长度在第 1 阶段实测。旧 68 个请求中有 18 个超过 4,096 token，长度分布需在新样本上重测。
- **学生推理。** 原 Qwen3 接入已完成 Mac 无卡回归。Qwen3.5-2B 的模板、真实权重单题执行均已在 Mac 验证，Linux 兼容性和 29 题真实配对待测。600 题教师采数、训练与最终评测单独核算。[学生接入报告](results/thinking-student-cpu-20261108/REPORT_CN.md) · [第 1 步技术方案](specs/01-thinking-events/plan.md)

付费调用和 GPU 在各步 plan.md 中写明上限，用户批准后执行。

## 已有组件的用途

| 已有依据 | 用途 |
| --- | --- |
| FlockCue `general-agent-v5-local` | 本地复现、模型 I/O 封存、独立进程评分。[本地 v5 报告](results/hover-compact-contract-20261103/REPORT_CN.md) |
| 回执解析与执行状态 | 执行事件抽取器的基础。[command_results.py](../agent4drone/command_results.py) · [task_execution_state.py](../agent4drone/task_execution_state.py) |
| 4 个封存真实批次 | 事件抽取器与标签格式的开发数据，只用于开发，不进入训练。[实验依据](EXPERIMENT_EVIDENCE_CN.md) |
| 场景数据 | 后训练与系统评测使用冻结的 600 题。生成的 174 题仍按原协议封存，其中 29 道开发题供第 0 阶段筛查。[600 题原件](../experiments/flockcue-benchmark-600-v1/sessions-600.zip) · [原划分报告](results/task-coverage-splits-20261103/REPORT_CN.md) |
| 固定 tokenizer | Qwen3-1.7B 输入检查。模板会渲染首条任务之后的历史推理。[tokenizer 目录](../training/tokenizers/qwen3-1.7b-input-v1/) |

## 第二篇论文的衔接

第一篇产出的小模型和执行事件定义，是第二篇的起点：轨迹验证器把事件扩展为按机、按作用范围的诊断，再作为过程奖励用于 GRPO。第一篇不训练验证器。[2026-11-07 验证器草案](history/verifier-plan-20261107/spec.md)
