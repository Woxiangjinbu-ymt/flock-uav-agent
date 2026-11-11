# FlockCue 研究项目长期上下文

更新于 2026-11-09。本文件记录后续协作沿用的决定，各开发步骤的要求在对应规格中维护。

## 研究与项目目标

- **系统。** FlockCue 是面向多无人机协作的执行反馈 Agent Harness。主 Agent 在给定工具、权限和预算下负责规划、分配、执行与重规划。Harness 记录各机的任务状态和工具回执，支持有范围的失败反馈复用与恢复。原工具可用，预定义执行子程序在适用任务上启用。机制的独立贡献有待对照与消融验证。
- **学生与第一篇论文。** 从 DeepSeek 教师轨迹蒸馏约 2B 的多无人机指挥模型，研究它在哪些轮次需要思考。由执行状态事件（受阻、效果未知、部分完成、失败、新信息、义务变化）监督思考，对照教师自选、RACE 的 LoGiC、固定间隔和两个端点。用户 2026-11-08 确认该主问题，并允许把 DeepSeek `reasoning_content` 用作 SFT 目标；旧 W3 样本契约及其 61 条样本保持冻结。[后训练方案](docs/POSTTRAINING_PLAN_CN.md)
- **版本与数据。** `general-agent-v5-local` 是当前可复现的本地脚本模型基线。11-02/03 的 DeepSeek 小批是限定场景下的真实开发证据，只用于开发，不进入训练。后续示范采集、SFT 和最终系统比较共用已确认的 30 个原基准场景、600 道题，保留每场 20 题原顺序。原 29 道 dev 继续用于已批准的 G1/G2 筛查。600 题有历史开发暴露，最终报告要说明每个学生在本轮训练中接触过哪些场景。[600 题冻结清单](experiments/flockcue-benchmark-600-v1/) · [采数与训练规格草案](docs/specs/02-benchmark600-sft/spec.md) · [FlockCue 入口](agent_harness/README.md)
- **当前工作。** 第 0 阶段先过三道门槛：G1 学生零样本有改进空间，G2 思考开关有差距，G3 执行事件有区分度；通过后才采教师数据。第 1 步规格、技术方案及预算上限已确认。G3 已通过。学生接入与 600 题中的首个 20 题连续会话均已通过 Mac 脚本模型验收。场景 `04cd6269` 已用于工程开发。Qwen3.5-2B 的固定工具回复已接入 Mac 仿真，已验证起飞悬停、整组截断时零动作和双机倒序回执归属。11 月 9 日，同题关思考对照中，2B 原组因起飞参数缺少 `input_json` 停在地面。仅补全工具示例后，目标机升至 12 米，原检查 2/2，两组关思考回合均未发出独立悬停指令。保留新示例再开思考，同题第 3 轮执行悬停并获得成功回执，第 5 轮才宣布完成。该证据限于一题、一个种子。[开关思考同题对照](docs/results/qwen35-hover-thinking-mac-20261109/REPORT_CN.md) · [工具示例对照](docs/results/qwen35-takeoff-example-mac-20261109/REPORT_CN.md) · [真实权重首跑](docs/results/qwen35-real-mac-20261109/REPORT_CN.md) · [工具执行验收](docs/results/qwen35-tools-mac-20261108/REPORT_CN.md) · [SFT 开发样例](docs/audit/qwen35-2b-input-20261108/REPORT_CN.md) 用户已将 Linux 的学生接入与连续会话两项复验顺延到租用 GPU 主机时，在该机先用 CPU 复验，再做 T37 真实权重测速。G1、G2 待测。[连续会话验收](docs/results/flockcue-mac-session-20261108/REPORT_CN.md) · [学生接入与迁移](docs/results/thinking-student-cpu-20261108/REPORT_CN.md) · [G3 复算报告](docs/results/thinking-events-g3-20261108/REPORT_CN.md) 各步依次确定 `spec.md`、`plan.md`、`task.md`、`checklist.md`，逐份确认后实施。研究窗口约 1–2 个月，不是训练或投稿保证。[当前规格](docs/specs/01-thinking-events/spec.md) · [技术方案](docs/specs/01-thinking-events/plan.md) · [任务清单](docs/specs/01-thinking-events/task.md) · [验收清单](docs/specs/01-thinking-events/checklist.md)
- **资源。** DeepSeek 为教师。11 月 9 日确认 G1/G2 主模型为 Qwen3.5-2B，沿用 Qwen3-4B 参照。29 道原开发题各做同题开关配对；当前仅在 Mac 上用模拟结果验收计分，真实批次待测。后训练优先验证同一 2B 学生。后训练拟用单卡 LoRA，40 GB 为主实验参考，80 GB 为上限。付费调用、租卡与训练在各步 plan.md 写明上限，用户批准后执行。
- **交付。** 第一篇论文：执行事件驱动的选择性思考。第二篇：轨迹验证器作过程奖励用于 GRPO。PenguinHarness 式 Harness 迭代作为不训练权重的对照臂。实习与秋招展示可复跑的工程、逐轮推理与事件回放和失败分析。研究以 CCF A/B 会议要求为目标，投稿时间和会议待定。硕士课题仍覆盖多类协作任务、局部观测、动态重规划及平台整合。
- **术语。** 沿用“观测与执行反馈”“预定义执行子程序”“失败恢复”。“接管”限于实际控制权或责任转移。历史代码名称和原始实验术语用于追溯。

## 证据与维护规则

- 11 月 11 日，无参感知与显式空对象的 29 对 Mac 执行结果一致。A100 的 58 个新回合全部独立评分，2B 开思考 1/29、关思考 0/29，G1、G2 仍未通过。教师真实 HTTP 入口已在本地验收断线恢复，40 题付费试采待用户决定。[门槛复测](docs/results/flockcue-a100-sense-default-20261111/REPORT_CN.md) · [教师恢复验收](docs/results/deepseek-teacher-service-mac-20261111/REPORT_CN.md)

- 11 月 9 日已将 4 个悬停开发回合的 26 个决策轮逐条核验，保留 16 条 Qwen3.5-2B 格式的 SFT 开发样例。该批只供格式和取舍规则回归。[样例与逐轮审阅](docs/results/deepseek-sft-dev-examples-20261109/REPORT_CN.md)
- [研究路线](docs/RESEARCH_ROADMAP_CN.md)维护阶段、门槛与交付，[研究状态](docs/RESEARCH_STATUS_CN.md)维护已完成、待验证与待决事项。目录分工与冻结协议清单见[文档导航](docs/README.md)，实验数字从[实验依据](docs/EXPERIMENT_EVIDENCE_CN.md)追溯。被代码按路径读取或封存哈希引用的文档不改名不移动。
- 始终区分 **Confirmed Decisions / Verified Results / Working Hypotheses / Open Questions / Next Actions**。设计不等于实现，离线接口通过不等于在线能力，开发 pilot 涨分不等于泛化。
- 本项目 Teacher 数据集、SFT、Agentic RL、约 2B 训练后模型仍未完成。用户既往 TRL + GRPO + Qwen 入门实验是学习经历，不能当作本项目闭环成果。
- 以独立评分的任务成功率为主指标，同时报告推理 token、全部 token、P50/P95 时延与训练成本；比较思考标签时控制思考比例或总推理 token。控制模型、Harness、权限、初态、顺序、预算和评分，区分系统与模型贡献。引用成绩需附报告、模型、分母、协议及限制，不拼接不同批次最好值。上游 57.9% 和排行榜不是本项目复现成绩。
- 论文/研究报告以本文系统与方法为主体，不使用“本项目基于 MultiUAV-Plat 与 Agent4Drone 实现”作为主叙述；平台、数据集、基线及复用组件在相关工作、实验设置或实现说明中规范引用，准确保留来源与本地新增边界，不宣称整套系统全部自研。
- 策略可见的观测与执行反馈可进入策略；隐藏检查和私有状态仅用于隔离评估或经明确设计的奖励，不进入策略输入/示范。正式采数前冻结场景/种子/任务族划分；已开发过的数据不能改称未见测试。
- 用户已授权清理过期文档、历史/失败尝试和无关内容，不要求无条件保留所有旧材料。删除前核对当前依赖与结论来源，优先清除重复说明和退役入口；必要原件可外置恢复。不能通过删除负结果改变保留结论的分母或制造提升。清理记录见 `docs/maintenance/`。
- 原始后训练 HTML 仍是研究依据，后续决定作增量修订；本轮保留其原件。新证据更新状态并链接原报告，短期逐题细节不堆入本文件。

2026-11-02 至 11-03 的行为、执行契约、成本及悬停小批均已结束。旧文中的 W 编号、“第五项”和 11-07 的验证器六步只用于查原件，对照见[研究路线](docs/RESEARCH_ROADMAP_CN.md#旧编号对照)。2026-11-08 的整理记录见 `docs/maintenance/cleanup-20261108.json`。
