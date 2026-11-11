# FlockCue 研究项目长期上下文

更新于 2026-11-11。本文件记录后续协作沿用的决定，各开发步骤的要求在对应规格中维护。

## 仓库分工

FlockCue 实现与系统评测在 `flockcue/`，原版服务在 `agent4drone/`，轨迹构建、SFT 与 RL 在 `posttraining/` 对应子目录，训练与评测数据入口在 `datasets/`。在稳定的文件名上迭代，以 Git 提交或标签标识版本。新增运行产物使用 `outputs/`，已发布批次通过路径和校验清单追溯。[目录与版本约定](docs/maintenance/project-layout-20261111.md)

## 研究与项目目标

- **系统。** FlockCue 是面向多无人机协作的执行反馈 Agent Harness。主 Agent 在给定工具、权限和预算下负责规划、分配、执行与重规划。Harness 记录各机的任务状态和工具回执，支持有范围的失败反馈复用与恢复。原工具可用，预定义执行子程序在适用任务上启用。机制的独立贡献有待对照与消融验证。
- **当前优先级（2026-11-11 晚用户确认）。** 同日 200 题的实际 DeepSeek 服务指纹一致，FlockCue 通过 182 题、原版 Agent4Drone 通过 168 题。该结果作为系统对照保留。论文主线转入可信教师轨迹构建，随后生成 SFT 数据、训练 2B 学生、接续在线 GRPO，主指标为同一 Harness 下的整题 pass rate。首批复用正式 FlockCue 首遍的 A 折；数据按完整场景区分训练、内部验证和外部留出。[同日系统对照](docs/results/agent4drone-matched200-20261111/REPORT_CN.md) · [教师轨迹规格](docs/specs/02-benchmark600-sft/spec.md) · [首批场景角色](docs/results/flockcue-teacher-trajectories-a-20261111/scene-roles.json)
- **后训练。** Harness 定型后，构建能核对真实工具调用、回执、执行结果和独立评分的 DeepSeek 教师轨迹。在同一 Harness 和评测设置下，依次检验 2B 学生经 SFT、RL 后的整题 pass rate。Think/No-Think 和执行事件驱动的选择性思考留到流程跑通后。此前已允许使用 DeepSeek `reasoning_content` 作监督，旧 W3 契约及其 61 条样本保持冻结。[后训练方案](docs/POSTTRAINING_PLAN_CN.md)
- **版本与数据。** `general-agent-v5-local` 是当前可复现的本地脚本模型基线。11-02/03 的 DeepSeek 小批是限定场景下的真实开发证据，只用于开发，不进入训练。后续示范采集、SFT 和最终系统比较共用已确认的 30 个原基准场景、600 道题，保留每场 20 题原顺序。原 29 道 dev 已用于 G1/G2 筛查。600 题有历史开发暴露，最终报告要说明每个学生在本轮训练中接触过哪些场景。[600 题冻结清单](datasets/flockcue-600/) · [采数与训练规格草案](docs/specs/02-benchmark600-sft/spec.md) · [FlockCue 入口](flockcue/README.md)
- **选择性思考前期工作（历史记录）。** 原第 0 阶段设置三道门槛：G1 学生零样本有改进空间，G2 思考开关有差距，G3 执行事件有区分度；通过后才采教师数据。第 1 步规格、技术方案及预算上限已确认。G3 已通过。学生接入与 600 题中的首个 20 题连续会话均已通过 Mac 脚本模型验收。场景 `04cd6269` 已用于工程开发。Qwen3.5-2B 的固定工具回复已接入 Mac 仿真，已验证起飞悬停、整组截断时零动作和双机倒序回执归属。11 月 9 日，同题关思考对照中，2B 原组因起飞参数缺少 `input_json` 停在地面。仅补全工具示例后，目标机升至 12 米，原检查 2/2，两组关思考回合均未发出独立悬停指令。保留新示例再开思考，同题第 3 轮执行悬停并获得成功回执，第 5 轮才宣布完成。该证据限于一题、一个种子。[开关思考同题对照](docs/results/qwen35-hover-thinking-mac-20261109/REPORT_CN.md) · [工具示例对照](docs/results/qwen35-takeoff-example-mac-20261109/REPORT_CN.md) · [真实权重首跑](docs/results/qwen35-real-mac-20261109/REPORT_CN.md) · [工具执行验收](docs/results/qwen35-tools-mac-20261108/REPORT_CN.md) · [SFT 开发样例](docs/audit/qwen35-2b-input-20261108/REPORT_CN.md) 11 月 10 日在单张 A100 40 GB 上通过 Linux CPU 复验与 T37 真实权重测速，29 题四臂共 116 个回合均获独立评分。主模型 2B 开思考成功 1/29、关思考 0/29，净增 1 题，仅 1 个父场景净改善。4B 参照两臂均为 0/29。G1、G2 均未通过，正式教师采集与 SFT 尚未启动。[正式结果](docs/results/flockcue-a100-g1g2-20261110/REPORT_CN.md) · [T37 测速](docs/results/flockcue-a100-g1g2-20261110/T37_CN.md) · [2B 工具参数核查](docs/results/flockcue-a100-g1g2-20261110/TOOL_ARGUMENTS_CN.md) · [服务器验收与运行目录](docs/results/flockcue-a100-linux-20261110/REPORT_CN.md) · [G3 复算报告](docs/results/thinking-events-g3-20261108/REPORT_CN.md) 11 月 11 日仅修改感知工具公开示例，在 50 个已暴露前缀上使 2B 单轮合法感知调用从 4/50 升至 15/50，A100 共生成 100 次，Mac 复算一致。同日将新说明用于完整 29 题，2B 开、关仍为 1/29、0/29，G1、G2 均未通过。两场 40 题的教师试采方案、清单与预算于筛查后备妥。[整题复测](docs/results/flockcue-a100-g1g2-retest-20261111/REPORT_CN.md) · [教师试采预算](docs/specs/04-deepseek-pilot40/plan.md)。[单轮诊断](docs/results/flockcue-a100-sense-example-20261111/REPORT_CN.md) 各步依次确定 `spec.md`、`plan.md`、`task.md`、`checklist.md`，逐份确认后实施。研究窗口约 1–2 个月，不是训练或投稿保证。[原筛查规格](docs/specs/01-thinking-events/spec.md) · [技术方案](docs/specs/01-thinking-events/plan.md) · [任务清单](docs/specs/01-thinking-events/task.md) · [验收清单](docs/specs/01-thinking-events/checklist.md)
- **资源。** DeepSeek 为教师。11 月 9 日确认 G1/G2 主模型为 Qwen3.5-2B，沿用 Qwen3-4B 参照。29 道原开发题各做同题开关配对；Mac 已用固定回复和真实分词器跑通全部 116 槽的执行、独立评分和报告，A100 实测沿用冻结的配对计划。[本地批次与上机交接](docs/results/qwen35-g1g2-execution-mac-20261110/README_CN.md) 后训练优先验证同一 2B 学生。后训练拟用单卡 LoRA，40 GB 为主实验参考，80 GB 为上限。后续付费调用、租卡与训练按各步 plan.md 确认。
- **交付。** 600 题 Harness 正式评测已完成。当前以正式首遍的教师轨迹为依据，接续 SFT 数据、2B SFT、GRPO 和同设置整题评测。此前拟议的选择性思考论文、验证器过程奖励及仅迭代 Harness 的对照保留作后续方向。实习与秋招展示可复跑的工程、逐轮推理与事件回放和失败分析。研究以 CCF A/B 会议要求为目标，投稿时间和会议待定。硕士课题仍覆盖多类协作任务、局部观测、动态重规划及平台整合。
- **术语。** 沿用“观测与执行反馈”“预定义执行子程序”“失败恢复”。“接管”限于实际控制权或责任转移。历史代码名称和原始实验术语用于追溯。

## 证据与维护规则

- 11 月 11 日，冻结 600 题已按十月原部署协议完成正式首遍。FlockCue + DeepSeek 通过 544/600，十月原版 506/600，50 题由失转过、12 题由过转失。原试采两场本次 40/40。两期教师服务指纹不同。当前审查局部退步，再决定模块取舍及数据筛选。[正式报告](docs/results/flockcue-deployment600-20261111/REPORT_CN.md) · [不一致题的轨迹](docs/results/flockcue-deployment600-20261111/MECHANISMS_CN.md)

- 11 月 11 日，无参感知与显式空对象的 29 对 Mac 执行结果一致。A100 的 58 个新回合全部独立评分，2B 开思考 1/29、关思考 0/29，G1、G2 仍未通过。教师真实 HTTP 入口已在本地验收断线恢复，随后用户单独放行两场 40 题真实教师可行性试采，二者归 A 训练折。试采终态独立通过 26/40，22 道题贡献了 148 个可用于 Qwen3.5-2B 的真实监督轮，已导出保留和去除本轮思考两份配对文件。权重训练尚未启动。[试采报告](docs/results/deepseek-teacher-pilot40-live-20261111/REPORT_CN.md)[门槛复测](docs/results/flockcue-a100-sense-default-20261111/REPORT_CN.md) · [教师恢复验收](docs/results/deepseek-teacher-service-mac-20261111/REPORT_CN.md)

- 11 月 9 日已将 4 个悬停开发回合的 26 个决策轮逐条核验，保留 16 条 Qwen3.5-2B 格式的 SFT 开发样例。该批只供格式和取舍规则回归。[样例与逐轮审阅](docs/results/deepseek-sft-dev-examples-20261109/REPORT_CN.md)
- [研究路线](docs/RESEARCH_ROADMAP_CN.md)维护阶段、门槛与交付，[研究状态](docs/RESEARCH_STATUS_CN.md)维护已完成、待验证与待决事项。目录分工与冻结协议清单见[文档导航](docs/README.md)，实验数字从[实验依据](docs/EXPERIMENT_EVIDENCE_CN.md)追溯。被代码按路径读取或封存哈希引用的文档不改名不移动。
- 始终区分 **Confirmed Decisions / Verified Results / Working Hypotheses / Open Questions / Next Actions**。设计不等于实现，离线接口通过不等于在线能力，开发 pilot 涨分不等于泛化。
- 首批已审阅的教师决策数据覆盖 110 道训练题、386 轮。SFT 训练样本、训练权重及在线 GRPO 成绩仍待完成。[轨迹构建报告](docs/results/flockcue-teacher-trajectories-a-20261111/REPORT_CN.md)用户既往 TRL + GRPO + Qwen 入门实验是学习经历，不能当作本项目闭环成果。
- 以独立评分的任务成功率为主指标，同时报告推理 token、全部 token、P50/P95 时延与训练成本；比较思考标签时控制思考比例或总推理 token。控制模型、Harness、权限、初态、顺序、预算和评分，区分系统与模型贡献。引用成绩需附报告、模型、分母、协议及限制，不拼接不同批次最好值。上游 57.9% 和排行榜不是本项目复现成绩。
- 论文/研究报告以本文系统与方法为主体，不使用“本项目基于 MultiUAV-Plat 与 Agent4Drone 实现”作为主叙述；平台、数据集、基线及复用组件在相关工作、实验设置或实现说明中规范引用，准确保留来源与本地新增边界，不宣称整套系统全部自研。
- 策略可见的观测与执行反馈可进入策略；隐藏检查和私有状态仅用于隔离评估或经明确设计的奖励，不进入策略输入/示范。正式采数前冻结场景/种子/任务族划分；已开发过的数据不能改称未见测试。
- 用户已授权清理过期文档、历史/失败尝试和无关内容，不要求无条件保留所有旧材料。删除前核对当前依赖与结论来源，优先清除重复说明和退役入口；必要原件可外置恢复。不能通过删除负结果改变保留结论的分母或制造提升。清理记录见 `docs/maintenance/`。
- 原始后训练 HTML 仍是研究依据，后续决定作增量修订；本轮保留其原件。新证据更新状态并链接原报告，短期逐题细节不堆入本文件。

2026-11-02 至 11-03 的行为、执行契约、成本及悬停小批均已结束。旧文中的 W 编号、“第五项”和 11-07 的验证器六步只用于查原件，对照见[研究路线](docs/RESEARCH_ROADMAP_CN.md#旧编号对照)。2026-11-08 的整理记录见 `docs/maintenance/cleanup-20261108.json`。
