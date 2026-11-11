# FlockCue 研究状态

更新于 2026-11-11。FlockCue 正式首遍与同日原版对照均已结束。当前论文工作是从可追溯的 DeepSeek 教师轨迹制作 SFT 数据，随后在同一 Harness 上验证 2B 学生和在线 GRPO 的整题成绩。[研究路线](RESEARCH_ROADMAP_CN.md) · [正式报告](results/flockcue-deployment600-20261111/REPORT_CN.md)

## Confirmed Decisions — 已确定

| 项目 | 现行口径 |
| --- | --- |
| 2B 学生目标 | 用户于 2026-11-11 确认先提高同一 Harness、同一评测设置下独立评分的整题 pass rate。Think/No-Think 等复杂训练场景延后。[后训练方案](research/posttraining.md) |
| 教师推理用作 SFT 目标 | 允许把 DeepSeek `reasoning_content` 作为学生的监督目标。用户 2026-11-08 确认。旧 W3 契约及其 61 条样本保持冻结，新样本另建版本。 |
| 后续研究 | 原方案中的执行事件驱动选择性思考、轨迹验证器过程奖励和仅迭代 Harness 的对照留待基础流程跑通后研究。 |
| 第 1 步技术方案与额度 | — |
| 当前启动范围 | 正式测当前 FlockCue 一遍，共 30 场 600 题。对照原版同题首次部署的历史结果，披露模型服务跨月的差异。[阶段与门槛](RESEARCH_ROADMAP_CN.md#阶段与门槛) |
| 正式题目 | 用户确认从原基准冻结的 30 个完整会话、600 题用于后续示范采集、SFT 和最终系统比较。原题有历史开发暴露，逐场保留原题序和状态继承。[600 题清单](../experiments/flockcue-benchmark-600-v1/) |
| 资源 | 教师 DeepSeek。11 月 9 日确认 G1/G2 主模型为 Qwen3.5-2B，沿用 Qwen3-4B 作参照。两者来自不同系列，真实门槛仅按 2B 的配对结果判定。单卡 LoRA，40 GB 优先、80 GB 为上限。600 题正式首遍已单独获准、不设金额上限。其他付费阶段按各自 plan.md 确认。 |

## Verified Results — 已有证据

- **首批教师轨迹。** 从正式首遍重建 A 折 300 题的 4,426 个决策轮，训练侧 240 题中有 157 题获本题实际动作支持。110 道题贡献了 386 个语义审阅通过、符合当前 2B 模板及长度的轮。B 折留作首个学生的折外评测。[构建报告](results/flockcue-teacher-trajectories-a-20261111/REPORT_CN.md)

- **同日、相同 DeepSeek 服务指纹的 200 题首遍。** FlockCue 通过 182/200（91%），原版 Agent4Drone 通过 168/200（84%）。20 题仅 FlockCue 过、6 题仅原版过，净增 14 题。巡逻、搜索、目标分配分别净增 2、1、11 题。两期这批题全部 3,110 和 2,846 次成功响应的后端指纹一致。白天与晚间 HTTP P95 分别为 23.68 和 36.00 秒；固定 500 秒动作窗口下，时延差异可能影响成绩。[同日正式对照](results/agent4drone-matched200-20261111/REPORT_CN.md)

- **冻结 600 题。** 当前 FlockCue 通过 544/600（90.67%），十月原版 506/600（84.33%）。巡逻 186/200、搜索 189/200、目标分配 169/200，分别净增 7、3、28 题。50 题由失转过、12 题由过转失。去掉十月 14 道终止性服务错误后，其余 586 题仍净增 30 题。两期预检指纹不同。[正式报告](results/flockcue-deployment600-20261111/REPORT_CN.md) · [不一致题的执行证据](results/flockcue-deployment600-20261111/MECHANISMS_CN.md)

- 同两场 40 题，十月原版通过 37/40，十一月 FlockCue 教师试采通过 26/40，本次原协议通过 40/40。试采失败的 14 题中有 12 道的正式成功轨迹会被试采的请求数或累计 token 限制截短。两场有 29 题的题初机队位置在试采和本次间不同。[原两场 40 题复核](results/flockcue-deployment600-20261111/FOCAL40_CN.md)

- **两场真实教师首遍已独立复算，首批 SFT 数据已生成。** 巡逻、搜索分别通过 16/20、10/20。3 道题开局已过，另有 1 题欠缺足时悬停证据。其余 22 题贡献 148 个合格决策轮，两种监督目标各 148 条。执行到评分结束历时 24 分 32 秒，GPU 0 卡时。[完整报告](results/deepseek-teacher-pilot40-live-20261111/REPORT_CN.md)

- **允许无参感知后，G1、G2 仍未通过。** Mac 在 29/29 个初态执行两种调用，回执、黑板和终态一致。A100 上 31 次无参感知均实际执行并取得回执。2B 开思考 1/29、关思考 0/29，58 个回合全部独立重评分，净增 1 题、1 个父场景净改善。教师真实 HTTP 入口已用本地服务验收，断线恢复未重复派发。该次验收的真实调用为零。[A100 结果](results/flockcue-a100-sense-default-20261111/REPORT_CN.md) · [教师入口](results/deepseek-teacher-service-mac-20261111/REPORT_CN.md)

- **教师连续会话已在 Mac 完成两场 40 题免费验收。** 固定回复的请求、历史思考、工具回执、世界交接与题初／终态独立评分可逐题回查。另行注入预算不足与用量缺失，均保留完整 40 个题位。该次固定回复验收的真实调用为零。[验收与开采条件](results/deepseek-teacher-pilot40-mac-20261111/REPORT_CN.md)

- **第 0 阶段的 116 个正式回合全部获得独立评分。** 单张 A100 40 GB 上，Transformers 通过 T37，2B 与 4B 各 20 次满长请求的 P95 分别为 68.41 秒、69.15 秒。在 5 个父场景的 29 道冻结 dev 题上，Qwen3.5-2B 开思考成功 1/29、关思考 0/29，开臂可归因的执行失败为 28/29，G1 未通过。开关净差为 +1 题，仅 1 个父场景净改善，G2 未通过。Qwen3-4B 开、关两臂均为 0/29。[独立评分](results/flockcue-a100-g1g2-20261110/REPORT_CN.md) · [T37 实测](results/flockcue-a100-g1g2-20261110/T37_CN.md)

- **已迁到租用的 A100 40 GB 主机。** 两项 Linux CPU 复验通过：117 项 Agent 与 29 项学生回归全部通过，整场 20 题的原检查与 Mac 一致。两模型官方修订权重已校验，候选 Transformers 环境和 BF16 硬件检查已通过。[目标机验收与路径](results/flockcue-a100-linux-20261110/REPORT_CN.md)

- **29 题真实批次的运行、独立评分与报告入口已在 Mac 验收。** 两种模型的真实 tokenizer 和固定回复驱动 5 场、29 题的四臂执行，先运行 2 槽，再恢复 114 槽，116 槽均由独立进程按原 checker 评分。已从 wire 重建 232 次请求的 token ID，两次生成的报告逐字节一致。固定回复只查询机队后声明完成，四臂均为 0/29。[验收与上机交接](results/qwen35-g1g2-execution-mac-20261110/README_CN.md)

- **Qwen3.5-2B 的 29 题配对计分已在 Mac 验收。** 新计划沿用五个父场景，每臂 29 题。10 组合成输入分别覆盖阈值、跨场景改善、超时偏向开臂、缺评分和全未启动；从保存的输入重新生成的报告均逐字节一致。[模拟报告](results/qwen35-g1g2-paired-mac-20261109/REPORT_CN.md) · [独立复算](results/qwen35-g1g2-paired-mac-20261109/verification.json)

- **DeepSeek 开发轨迹已整理成 16 条可回查的 SFT 样例。** 同一道悬停开发题的四个变体共 26 个决策轮，16 轮保留、10 轮剔除，每轮都有原始请求、回复、回执或评分依据及取舍理由。7 条保留了教师思考，9 条思考为空。已用 Qwen3.5-2B 分词器复核本轮监督范围，正式训练样本新增 0 条。[逐轮审阅与回查](results/deepseek-sft-dev-examples-20261109/REPORT_CN.md)

- **同一道悬停开发题，开思考组在宣布完成前实际执行了悬停。** 在修正起飞示例的两组中，开思考组第 3 轮向目标机派发独立 `hover` 并收到成功回执，第 5 轮才首次宣布完成；关思考组只执行起飞。原评分器均为 2/2。开思考用 385 个推理 token、53.25 秒，关思考用 0 个推理 token、45.75 秒。每组一个种子、一个回合。[配对动作与公开声明顺序](results/qwen35-hover-thinking-mac-20261109/REPORT_CN.md)

- **补全起飞示例后，2B 在同题上成功发出了起飞指令。** 两组关思考，前三轮消息和回执逐字相同。原组停在地面；改例组正确传参，目标机升至 12 米，原检查 2/2。改例组看到 `hovering` 状态便宣布完成，飞行动作日志里没有独立的 `hover`。本次每组一题一次。[配对验收与逐轮记录](results/qwen35-takeoff-example-mac-20261109/REPORT_CN.md)

- **Qwen3.5-2B 真实权重已在 Mac 自主跑完首个开发回合。** 关闭思考，独立评分 0/1 题成功。先查询机队和天气，随后选择正确的飞机及 12 米高度，但起飞指令遗漏外层 `input_json`，仿真记录零飞行动作。离线仅补参数包装即可通过同一解析器。[逐轮证据与原评分](results/qwen35-real-mac-20261109/REPORT_CN.md)

- **Qwen3.5-2B 格式的固定回复已接入 Mac 仿真。** 完整回复触发起飞和悬停，目标机终态为 12 米，独立检查 2/2 通过。第二条指令截断时整组零派发，目标机留在地面。两机同名状态查询的回执倒序进入下一轮，逐一对应到原机。两个回合共 8 次请求，输入和生成 token ID 均已独立复算。[工具执行验收](results/qwen35-tools-mac-20261108/REPORT_CN.md)
- **Qwen3.5-2B 的首条 SFT 格式样例已在 Mac 复算。** 同一条已见教师开发轮的思考、工具动作和损失边界可回查。两架飞机同名调用的合成例中，官方模板丢失回执归属；在回执中补入公开调用序号和参数后可以区分。模型在多机倒序回执后的决策能力待验证。[开发样例与复算](audit/qwen35-2b-input-20261108/REPORT_CN.md)

- **600 题中的一个完整会话已在 Mac 跑通。** 场景 `04cd6269` 用本地脚本模型连续做完 20 题，重开后 79 项原检查结果逐项相同。19 次题间交接均保留了黑板。超时和 HTTP 503 分别验证了继续做题与停止后保留分母。此场已用于工程开发。[整场验收](results/flockcue-mac-session-20261108/REPORT_CN.md)
- **学生推理链路通过 Mac 无卡回归。** 三个固定回复回合分别验证悬停完成、整组畸形拒绝和取消，10 次请求从归档重建的 token ID 全部一致。解包后 100 项回归通过。[验收报告](results/thinking-student-cpu-20261108/REPORT_CN.md)
- **G3 执行事件区分度通过。** 31 个开发回合的 183 个非首轮中，77 轮出现事件、106 轮为 E0、0 轮不可判，事件占比 42.08%。全部 31 个回合的第二轮都含新信息，第 3 轮以后仍有 46/152 轮出现事件。6 个效果未知轮均在故障注入条件下。[离线复算报告](results/thinking-events-g3-20261108/REPORT_CN.md)

- **FlockCue 本地基线。** `agent4drone/.venv/bin/python -m agent_harness check` 于 2026-11-08 整理前后均返回 0，核对 `general-agent-v5-local` 的 93 个源码文件、配置与依赖清单。v5 以本地脚本模型验收，真实模型表现另测。[复现入口](../agent_harness/README.md) · [v5 本地验收](results/hover-compact-contract-20261103/REPORT_CN.md)
- **教师推理已归档。** 4 个封存真实批次共 214 次响应，服务模型均为 `deepseek-flash`（thinking enabled / high）。推理 token 53,319，占输出 token 70.5%、全部 token 1.8%；69 次（32.2%）响应推理为 0，说明教师本身已在选择性思考。只覆盖开发任务，不是 train 侧教师数据。[复算脚本与汇总](audit/teacher-reasoning-usage-20261108/summary.json)
- **数据与训练状态。** 既有 174 题划分含 116/29/29 道训练、开发及测试题。G1/G2 保留其中 29 道开发题。冻结的 600 题已完成教师正式评测。用于训练的轨迹还需按场景归属、动作与回执及质量筛选，学生 SFT 与 RL 尚未启动。[划分与暴露记录](results/task-coverage-splits-20261103/REPORT_CN.md) · [正式报告](results/flockcue-deployment600-20261111/REPORT_CN.md)

### S01–S14 · 可复用能力与验证边界

以下沿用仓库的审计编号，方便从旧报告追溯已有基础。各批的分母、工具版本和评分标准以原件为准。[完整证据索引](EXPERIMENT_EVIDENCE_CN.md)

| ID | 已有基础与使用边界 |
| --- | --- |
| S01 平台与 Agent | 集中式主 Agent 调用多架无人机的工具，平台、仿真和可视化的复用来源在项目说明中列出。[系统范围](GENERAL_AGENT_SCOPE_CN.md) · [Agent 接线](../agent4drone/uav_agent.py) |
| S02 配置与版本 | 研究入口显式选择 v5 本地 profile。产品 API/GUI 走自己的 Agent 构造和默认开关。实验归属以实际使用的 profile 和封存清单为准。[版本说明](../agent_harness/README.md) |
| S03 任务证据与记忆 | 可从公开回执组织有限的任务状态。历史执行记忆压缩在 14 个跨任务开发题上全部回退，报告保留其适用范围。[跨任务回放](results/execution-memory-v7-cross-task-20261023/REPORT_CN.md) |
| S04 有界执行子程序 | 两类预定义子程序具备可中断执行状态和恢复接口。96 回合的对照使用夹具提交初始技能，结果只说明该条件下的机制行为。[机制报告](results/minimal-harness-v2-mechanism-20261016/FINAL_ANALYSIS_CN.md) |
| S05 跨机共享与局部修复 | 同一计划内，静态障碍的精确目的点冲突有受控复用案例。单机航段受阻的证据只支持其适用范围，相关无 LLM 实验按版本分别统计。[共享边界](results/harness-shared-endpoint-20261023/REPORT_CN.md) · [已见目标搜索](results/harness-observed-target-20261024/REPORT_CN.md) |
| S06 自然选择与恢复 | DeepSeek 在仿真中出现过收到不确定反馈后查询状态、继续完成悬停的实例。同条件原工具臂也能恢复，机制增量仍待检验。[执行契约 8 槽同期报告](results/deepseek-contract-pilot-20261103/REPORT_CN.md) |
| S07 回合隔离与初态 | 现有完整链每回合创建新 worker，从选定初态运行，退出后由独立进程评分。同进程多环境或重复训练 reset 尚待验证。[回合协议](results/harness-episode-contract-20261024/REPORT_CN.md) · [完整链验收](results/harness-full-episode-20261025/run-20261025-02/summary.json) |
| S08 终止与评分 | 已覆盖截止、取消、排空及保留未评分回合。验收和真实小批均有明确的场景及模型范围。[运行器验收](results/harness-natural-runner-20261025/revalidation-summary.json) · [执行契约](AGENT_EXECUTION_CONTRACT_CN.md) |
| S09 模型与工具归档 | 实际请求、响应、逻辑调用与工具回执有关联记录，截断回合可标为 partial。它们可作为逐步可见输入的来源。[归档验收](results/harness-model-io-20261024/REPORT_CN.md) |
| S10 Teacher 接入与成本 | — |
| S11 任务来源与划分 | 已冻结父场景划分，测试侧做过预定质量审计。共享模板、目标初始可见和少量测试父场景限制泛化结论。[划分报告](results/task-coverage-splits-20261103/REPORT_CN.md) |
| S12 历史动作样本 | 旧监督目标是主 Agent 的下一条回复，推理不进入输入与监督。22 个计划槽、98 次决策产出 61 条开发格式样本，已有 tokenizer、调用 ID 对齐和加载验证。新样本保留推理，使用新版转换器，旧样本不改写。[旧样本验收](results/agent-samples-student-input-20261103/REPORT_CN.md) · [同名工具 ID 修复](results/student-tool-ids-v2-20261103/REPORT_CN.md) |
| S13 学生 SFT | Qwen3-1.7B tokenizer 已用于开发输入长度检查；固定模板会渲染首条任务之后的历史推理。学生接入已通过 Mac 和 Linux 检查，两种原始权重已完成 29 题零样本筛查。SFT 参数更新及导出仍待实施。[Mac 验收](results/thinking-student-cpu-20261108/REPORT_CN.md) |
| S14 RL 与最终评测 | 有历史任务评分器、独立进程与公平对照协议可复用。尚无本项目 RL 结果。RL 接在 SFT 之后，轨迹验证器的过程奖励仍属后续研究。[公平验证协议](HARNESS_VALIDATION_PROTOCOL_CN.md) · [研究路线](RESEARCH_ROADMAP_CN.md) |

### 真实小批与本地修复的解释范围

11 月 2–3 日的真实小批使用当批的 DeepSeek 模型配置，在本地仿真中运行。v5 使用脚本模型做契约验收。以下结果分批保留。

| 批次 | 完整分母与已观察到的结果 |
| --- | --- |
| 11-02 行为试点 | 计划 6 回合，实际执行 5 回合，搜索触及 token 停线，配对另一臂未运行。该批不完整。[原报告](results/deepseek-behavior-pilot-20261102/REPORT_CN.md) |
| 11-03 执行契约 | `deepseek-flash`，4 条件 × 2 臂。两臂各成功 3/4，总 token 从 394,078 增至 439,846（+11.6%），两臂的受控故障回合都观察到无重放恢复。[原报告](results/deepseek-contract-pilot-20261103/REPORT_CN.md) |
| 11-03 compact/v9 | `deepseek-flash`，5 条件 × 2 臂。总 token 从 562,146 降至 441,582（−21.4%），原评分 4/5 → 5/5，预登记动作义务口径两臂均 4/5。新版困难搜索成功，短链缺少独立悬停动作回执。[原报告](results/deepseek-cost-pilot-20261103/REPORT_CN.md) |
| 11-03 悬停 v4 | `deepseek-flash`，4 条件 × 2 臂。三对受支持条件按统一标准两臂均 3/3。整批 token 增加 25.51%，一次只读并行被公开认证过严拒绝，另一对时长诊断无持续时间证据。[原报告](results/deepseek-hover-pilot-20261103/REPORT_CN.md) |
| 11-03 本地 v5 | — |


## Working Hypotheses — 待检验的贡献

当前待检验的是定型 Harness 上，可信 DeepSeek 示范能否改善 2B 的独立整题成功率，以及 RL 能否在 SFT 基础上进一步改善。

暂缓的选择性思考假设认为，小型指挥模型需要深思的轮次集中在执行状态发生变化的时刻。用执行事件监督思考，在相同成功率下比教师自选、LoGiC 和固定间隔消耗更少的推理 token，机队越大优势越明显。只做 SFT 的降幅可能有限，RACE 只做 SFT 时为 4.3–20.0%。若四种标签相当，结论改为"执行状态信号已被教师或似然信号隐含"。[研究路线](RESEARCH_ROADMAP_CN.md#研究主线)

## Open Questions — 待确定

- 哪些模块独立影响整题通过率，以及后续对 2B 学生“足够好”的通过率数值。
- 2B 的 58 个回合中有 56 个因工具参数格式错误停止。实际工具描述中的示例对这一行为影响多大，现有数据尚不能确定。[参数错误核查](results/flockcue-a100-g1g2-20261110/TOOL_ARGUMENTS_CN.md)
- 在扩大任务族后，效果未知和部分完成能否在自然条件下获得足够样本？当前开发回合的分层频次见 [G3 报告](results/thinking-events-g3-20261108/REPORT_CN.md)。
- 首个学生已按 A 折 12 场训练、3 场内部验证、B 折 15 场外部评测分开构建数据。反向训练的权重与配方仍待确定，两方向的选参须与相应折外场景隔离。[第 2 步规格草案](specs/02-benchmark600-sft/spec.md)

## Next Actions — 当前交接点

先用 A 折已审阅教师轮生成 SFT 样本，复核训练侧的完成声明、失败恢复和长上下文，定稿学生目标与损失掩码。以同一 Harness 下的独立整题通过率比较原始 2B、SFT 和后续 GRPO。[教师轨迹与当前覆盖](results/flockcue-teacher-trajectories-a-20261111/REPORT_CN.md)

同日 Harness 对照已完成。核查具体机制时，可从这 200 题的 26 个不一致题定位场景、动作和失败检查。[同日逐题配对](results/agent4drone-matched200-20261111/pairs.csv)

12 道旧过新失题中，8 题有指定机拍照成功但目标入镜检查未过的记录，5 题同时出现指定机的障碍回执和未满足的位置或路程要求，两项可重叠。12 题均未直接调用预定义子程序。先核查拍照、到位及完成声明是否有公开证据支撑。评估单个模块时固定题初状态与教师服务。[不一致题的执行证据](results/flockcue-deployment600-20261111/MECHANISMS_CN.md)

### 选择性思考前期交接记录

在原选择性思考方案中，教师采集的三道门槛仅 G3 通过。第 0 阶段交接材料为 116 个回合的独立评分、T37 测速及 2B 参数错误核查。11 月 11 日已重放 4B 全部 147 次请求，50 个因工具顶层字段错误而停止的回合均出现模型自填的调用 ID。[4B 全量核验](results/flockcue-a100-g1g2-20261110/TOOL_FIELDS_4B_CN.md) 2B 已定位到 24 道题、46 个回合的感知工具参数错误，并核实另 4 个回合有正确感知回执。这 50 个历史位置的 2B 配对推理已完成，旧、新说明中合法感知调用分别为 4/50、15/50，12 对改善、1 对退化。100 份输出均经 Mac 独立复算。随后已完成新说明下全部 58 个整题回合。2B 开思考仍为 1/29、关思考为 0/29，开臂有 26 个可归因失败，G1 未通过。开关净差 +1 且仅 1 个父场景改善，G2 未通过。感知工具参数导致的停机从 46 个回合降至 33 个，其他工具参数导致的停机从 10 个增至 22 个。两场 40 题的教师试采随后获得用户授权，沿用复测 C 接口和冻结清单。[整题复测](results/flockcue-a100-g1g2-retest-20261111/REPORT_CN.md) · [40 题试采预算](specs/04-deepseek-pilot40/plan.md)。[单轮正式结果](results/flockcue-a100-sense-example-20261111/REPORT_CN.md) · [验收记录](specs/03-sense-tool-example/checklist.md#验收记录) [感知工具复核](results/flockcue-a100-interface-20261111/README_CN.md) · [已确认规格](specs/03-sense-tool-example/spec.md) · [技术方案与卡时](specs/03-sense-tool-example/plan.md)。600 题的采数与训练方案仍为草案。[筛查报告](results/flockcue-a100-g1g2-20261110/REPORT_CN.md) · [错误核查](results/flockcue-a100-g1g2-20261110/TOOL_ARGUMENTS_CN.md) · [第 2 步规格草案](specs/02-benchmark600-sft/spec.md)

2026-11-08 的仓库整理记录见[清理清单](maintenance/cleanup-20261108.json)。旧报告中的 W 编号、"第五项"、验证器六步和当批"下一步"属于当时的编排，对照见[研究路线](RESEARCH_ROADMAP_CN.md#旧编号对照)。[实验依据](EXPERIMENT_EVIDENCE_CN.md) · [原始后训练调研](SMALL_MODEL_POSTTRAINING_SURVEY_CN.html)
