# FlockCue 的研究贡献与项目展示

2026-11-08。**FlockCue** 是基于执行反馈的多无人机协作智能体框架（*An Agent Harness for Multi-UAV Coordination with Execution Feedback*）。一个集中式 Agent 选择飞机、分配任务、调用公开工具并决定后续行动。FlockCue 记录每个调用属于哪架飞机、回执支持了什么结论，以及哪些任务要求仍待核实。[系统与代码入口](../agent_harness/README.md)

第一篇论文从 DeepSeek 教师轨迹蒸馏约 2B 的指挥模型，研究**它在哪些轮次需要思考**。FlockCue 已有的回执解析给出执行状态事件（受阻、效果未知、部分完成、失败、新信息、义务变化），作为"思考或不思考"的标签来源之一。[研究路线](RESEARCH_ROADMAP_CN.md) · [第 1 步规格](specs/01-thinking-events/spec.md)

## 与已有工作的关系

依据仓库内两份调研：[小模型后训练调研](SMALL_MODEL_POSTTRAINING_SURVEY_CN.html)（2026-10-17）和[过程评估可行性调研](audit/agent-verifier-feasibility-20261107/REPORT_CN.md)（2026-11-07）。投稿前需重新核对各工作的最新版本。

| 工作 | 改动的对象 | 学习信号 | 本项目的差异 |
| --- | --- | --- | --- |
| RACE（When Should Agents Think，arXiv 2610.12061） | 策略模型权重 | LoGiC：去掉某轮推理后，后续参考动作的似然下降 | 本项目的信号来自执行回执，并检验多机场景特有的机队规模效应。LoGiC 作为对照标签。RACE 只做 SFT 时推理 token 降 4.3–20.0%，大幅下降依赖 RL。 |
| PenguinHarness 的自动迭代流程 | Harness 说明、技能、工具、钩子，权重不动 | 整题评分提高才接受候选 | 作为"不训练权重"的对照臂，回答训练是否必要。 |
| 轨迹验证器（本项目 11-07 草案） | 独立诊断模型 | 逐机执行效果、义务归属、证据范围 | 第二篇论文训练成过程奖励。以 DeepSeek 为主 Agent 时出错少，挽回空间小，因此不放在第一篇。 |

## 怎样算研究贡献

- 有一个可被证伪的机制假设，而不是"搭了一个系统"。
- 文献中有代表另一种解释的基线：教师自选、LoGiC、固定间隔、RSI。
- 只改一个变量：同一批教师轨迹、同一基座、同一 Harness，只换思考标签来源。
- 负结果有信息量：若各标签相当，说明执行状态已被教师或似然信号隐含。
- 开发筛查以 Qwen3.5-2B 为主，Qwen3-4B 作跨系列参照。后续评测留出父场景，并按机队规模分组。

以下属于工程尝试，不能当作论文贡献：把 SFT 加 GRPO 流水线直接搬到无人机任务；在 DeepSeek 上加提醒器看成功率是否涨一点；只报告 Harness 改提示后分数上升而不与训练权重比较。

## 已能展示的内容

| 可展示的交付 | 复核材料 | 适用范围 |
| --- | --- | --- |
| 固定基线运行与独立评分 | 从 FlockCue 入口启动两个悬停正反例，比较公开动作回执与评分器输出。[运行说明](REPRODUCTION_RUNBOOK_CN.md) | `general-agent-v5-local`，本地脚本模型。 |
| 教师逐轮推理用量 | 从封存的真实 DeepSeek 归档复算每轮推理 token，可对照同轮的执行事件。[用量审计](audit/teacher-reasoning-usage-20261108/summary.json) | 4 个开发批次、214 次响应，不是 train 侧教师数据。 |
| 公开执行状态与失败恢复 | 任务状态、可中断子程序、局部修复、范围受限的反馈共享均有实现，部分路径恢复有正反例。[方法与历史验证](results/harness-method-validation-20261103/REPORT_CN.md) | 局部机制的适用条件和历史成本负结果随展示保留。[实验依据](EXPERIMENT_EVIDENCE_CN.md) |

学生模型训练、导出并回接 FlockCue 后，再增加逐轮推理与事件的回放演示。模型展示需给出权重、数据版本、对照、分母与实测成本。

## 实现与归属

| 入口 | 工作与来源 |
| --- | --- |
| [FlockCue](../agent_harness/README.md) | 研究基线的配置锁、校验与本地复现。 |
| [主 Agent](../agent4drone/uav_agent.py) · [Harness 接线](../agent4drone/harness_integration.py) · [模型 I/O 归档](../agent4drone/model_io_archive.py) | 复用 Agent4Drone 执行循环与工具能力。本地工作包括执行子程序、效果记录、恢复反馈、工具与模型调用关联。 |
| [回执解析](../agent4drone/command_results.py) · [执行状态](../agent4drone/task_execution_state.py) | 执行事件抽取器的基础。 |
| [上游平台](upstream/README_CN.md) | MultiUAV-Plat 提供仿真、任务、控制与可视化。论文中在相关工作与实验设置里引用，不作为本文方法主体。 |
| [训练准备](../training/README_INPUT_CN.md) | 固定 tokenizer 与旧版输入检查，新样本另建版本。 |
