# 通用 Agent 的范围与整合版本 v1

日期：**2026-11-02**。对应当前剩余工作的第一项：先固定系统覆盖目标、职责和一套可复查的整合配置。本页是首版范围规格；本地组合验收、自然模型任务能力、独立泛化分别成立，不能互相替代。总体进度见[研究状态](RESEARCH_STATUS_CN.md)，后续依赖见[研究路线](RESEARCH_ROADMAP_CN.md)。

## Confirmed Decisions — 首版范围与职责

**系统是一个集中式主 Agent，面向多无人机任务规划、分配与协作执行。** 主模型读取指令及允许的观测与执行反馈，选择执行主体、组织动作及后续恢复；原工具一直可用，匹配完整指令的预定义执行子程序可由模型显式选择。首版没有每架 UAV 独立 LLM 协商，也没有独立的通用任务图规划器。这里的“规划/分配”首先是主模型通过工具决策完成的职责，不暗示新增专门规划算法已经实现。[主 Agent](../agent4drone/uav_agent.py)、[工具入口](../agent4drone/uav_langchain_tools.py)、[Harness 接线](../agent4drone/harness_integration.py)。

“通用”限定为同一系统配置处理下表多类任务，不要求为每道题人工切换 Agent 或预先提交执行计划。**子程序不匹配只表示该捷径不可用，不表示 Agent 拒绝整条任务。** 同一任务中尚未支持的条款继续保留原文；不能将其删掉，也不能将其标为完成。

首轮整体评价目标为仿真环境中的 **1–5 架 UAV**，包含单机基础操作和至少两种多机任务。这个数量是待验收的任务规模范围，不是代码已强制的 fleet 上限，也不是已证实的规模泛化。首版静态障碍共享仅面向障碍几何不变、环境写入被隔离的回合；多机动作可按依赖顺序执行，不承诺连续时间上的精确同步编队。

### 任务族、执行路径与状态支持

下表是 **v1 整体可行性的目标覆盖表**。模板名称证明仓库有对应任务来源；工具存在证明有执行接口；它们均不证明整合版已经完成这些任务。完整模板见[任务模板](../controller/default_templates.py)，抽样入口见[自动任务生成](../controller/task_auto_generator.py)。开发覆盖集和独立 train/dev/test 仍按 W2 分开建设。

| ID / 目标任务族 | 可复用任务来源示例 | 常驻原工具路径 | 可选预定义执行子程序 | 默认状态支持及未覆盖部分 |
| --- | --- | --- | --- | --- |
| F1 基础飞行与有序动作 | `basic_takeoff`、`simple_rotation`、`simple_altitude_change`、`complete_flight_cycle` | `list_drones`、`get_drone_status`、`take_off`、`change_altitude`、`rotate`、`hover`、`return_home`、`land` | `simple_chain` 仅匹配指定单机的起飞→悬停、起飞→转向→悬停、起飞→爬升→悬停的三种完整英文语法；其他措辞/附加要求走原工具 | 记录实体、实际回执、顺序和失效信息；子程序有 cursor 和公开终态检查。默认 R2 ledger 不把任意动作序列解析成已证实义务图 |
| F2 坐标导航、路线与巡航 | `quick_checkpoint`、`fly_several_waypoints`、`patrol_mission`、单/双/三机 marathon 模板 | `navigate_to`、`navigate_to_and_sense`、`move_to`、`move_along_path` 及对应感知版本，配合飞行状态工具 | 无完整子程序；模型组织原工具 | 默认保留指令、逐次回执和位置证据；显式路线的剩余点/顺序视图属于 R3/Memory 候选，不能归到默认 R2 的能力。返航、降落等附加条款仍须分别核查 |
| F3 指定目标访问、观察与拍照 | `target_inspection`、`multiple_target_inspection`、`multi_target_sequential` | `get_nearby_entities` / `sense_nearby_entities`、`get_target_info`、导航、`move_within_distance`、`hover`、`take_photo` | 无完整子程序 | R1/R2 可为有限肯定句抽取 actor→target 访问关系，并保存来源；未发现目标不等于不存在。照片回执只能证明拍照动作，不能自动证明拍摄对象/质量 |
| F4 区域覆盖与搜索 | `area_coverage_search`、`dual_drone_area_coverage_search`、`triple_drone_area_coverage_search`、`target_area_full_coverage` | 本地感知、`get_target_info`、`generate_coverage_path`、路径执行及感知、状态查询 | 一般区域覆盖指令不匹配 `public_search`；走原工具。只有 F6 所述完整语法才可选择该子程序 | 黑板保留已观测区域与反馈；区域覆盖率、指定 UAV 的完成要求及任务整体阈值不能相互替代。规划路径生成成功不证明实际覆盖完成 |
| F5 多机目标分配、接力与组合后续动作 | `multi_target_survey`、`dual_drone_same_target`、`relay_find_and_support_target`、`cross_target_swap_after_discovery`、fleet target sweep/formation 模板 | 共享公开感知、按 UAV 绑定的导航与方向移动、状态查询，必要时调用通信工具 | 除 F6 完整语法外无通用协作子程序；模型组织原工具 | 记录来源 UAV、对象和顺序。单机访问不能替代另一机的访问；发现目标不能代替接力者到达；到达目标不能代替随后完整方向距离。不存在通用的动态责任转移实现 |
| F6 公开会话进度驱动的多机搜索/分配 | `target_assignment`；50%/70% 分配及 `area_search_task_completion` 作为不同阈值的原工具候选 | 感知、导航/移动、飞行状态，符合显式进度语法时追加 `get_mission_progress` | `public_search` 只支持完整指令 `Take off necessary drones and assigned them to all targets (within task radius) until the session task progress exceeds 95%.`；空白/大小写归一化，不支持任意阈值或附加义务 | R2 保存显式百分比、比较符号和公开进度来源；v7 子程序支持已见目标跟进及有界修复。公开会话进度大于 95% 只证明该公开停止条件，不能证明整题的其他要求 |

语法边界以 [`simple_plan` / `search_supported`](../agent4drone/harness_skills.py) 为准；语法别名没有自动扩展保证。状态边界以 [TaskProgress](../agent4drone/task_progress.py)、[访问关系](../agent4drone/task_evidence.py)、[公开进度与接触反馈 R2](../agent4drone/task_evidence_r2.py)、[路线 R3](../agent4drone/task_evidence_r3.py) 为准。表中 F1–F6 是为评价组织的任务族，不能与模板 category 数量或场景 task type 混算。

模板中还有 Tracking、Delivery、Battery Management、Communication 等类别。相关原工具可复用，但移动目标持续跟踪、真实载荷投递、长时能源保障和通信受限协商不作为首版整体能力保证；部分模板还显式排除随机生成。后续纳入时须分别检查执行语义、可解性和 checker，不能仅按模板标题宣布具备能力。

### 主模型、固定程序与评估职责

| 主体 | v1 职责 | 证据与限制 |
| --- | --- | --- |
| 主模型 | 解释原指令；选择 UAV/工具；确定顺序；显式 start/resume/cancel；根据可见结果重新组织后续动作；报告已确认与未确认要求 | 自然决策能力待整合版真实模型验证；脚本指定的选择不能算模型能力 |
| 原工具及仿真服务 | 参数校验、动作执行、已有导航/路径算法、公开观测与回执 | 工具返回成功与整题成功分开；路径规划是复用执行能力，不默认归因于主模型 |
| Ledger / 黑板 | 保存原文、实体绑定、反馈来源/顺序/有效性，提供 advisory 状态 | 不读隐藏答案，不做任意语言的完整完成判定；黑板已知对象只是已发现集合 |
| Harness | 执行已提交且语法支持的子程序；在作用域、效果、预算和依赖条件下继续/暂停/失效；实施已有局部修复 | 固定守卫和程序内部动作不是模型监督标签；单次 `resume` 不等于恢复成功 |
| 回合运行器 | 参数化任务和初态；限制请求与墙钟；取消、排空、封存；维护交互归档 | 新进程隔离路径可复用；超时不保证远端动作被撤销 |
| 独立 scorer | worker 退出/排空后，对封存状态运行原 checker，并保留版本和结果 | 只用于隔离评价；不向策略注入 checker、私有任务配置或 oracle 状态 |

首版保留完整主 Agent 的决策空间作为系统对象；**学生监督职责尚未冻结**。W3 应据任务覆盖、轨迹质量和约 2B 候选输入预算，决定监督全部主模型工具决策，还是其中明确子集。不能仅因两类子程序已有接口就把学生任务默认为二选一分类，也不能将未训练候选描述为已完成能力迁移。

### 输入、权限与预算

每个可复查回合至少绑定：原指令及任务 ID、场景/初态与哈希、来源与开发/训练/测试身份、profile 版本、源码/工具/评分版本、预算、模型配置及独立输出目录。原始指令进入策略；fixture、全量世界快照、checker 树、私有 canary 和评分结果只供恢复/隔离评估。可见权限不仅依赖 prompt，还须由运行入口的 API 权限与写入隔离落实。

原工具清单以 [`create_uav_tools` 的返回列表](../agent4drone/uav_langchain_tools.py) 为准，当前是 30 个工具；源码中定义了 `get_session_info`，但它未在该列表返回，不能按定义数量统计工具权限。`get_mission_progress` 只在识别到显式公开会话进度要求时追加；其输出经过 [R2 allowlist](../agent4drone/task_evidence_r2.py)，不包含隐藏目标坐标。目标/障碍细节按公开发现 ID 和服务权限使用；任务运行不获得管理接口或隐藏检查接口。

它们含义不同，不能将某一项当作其余上限。累计 token 在完整响应后结算；历史 `500s / 2000 API / 16 repairs` 等数值只是既有技术配置来源，保存到新 profile 后仍不构成付费执行授权。

## 整合候选及结果契约

第一项的整合候选放在 [profile](../experiments/general-agent-v1/profile.json)，由 [配置模块](../agent4drone/agent_profile.py)校验，维护入口为 [`scripts/general_agent.py`](../scripts/general_agent.py) 的 `prepare / run / verify`。它以本地脚本响应、真实 Agent/SDK、本地 ASGI 世界和独立 worker/scorer 贯通配置与封存；这是 **local-only 整合候选**，不是在线 Teacher 运行入口。具体字段和复现命令以 profile、入口帮助与本轮验收记录为准。

### 本地验收与复现

本批选用两个已知 F1 开发任务的初态，固定 [8 个案例](../experiments/general-agent-v1/cases.json)：`raw / skill / second-task` 检验三次实际动作和独立评分；`premature` 要求保留“正常返回但 checker 失败”；`http_error / cancel / request_timeout / usage_missing` 在第 3 个 HTTP 响应处检验停止原因及已执行起飞的保留。原工具、子程序、不同初态和异常均有明确预期，不以 coordinator 正常退出代替行为验收。这两个任务不是 F1–F6 跨任务族覆盖集。

```bash
agent4drone/.venv/bin/python scripts/general_agent.py prepare --output tmp/general-agent-v1-new-run
agent4drone/.venv/bin/python scripts/general_agent.py run --run tmp/general-agent-v1-new-run
agent4drone/.venv/bin/python scripts/general_agent.py verify --run tmp/general-agent-v1-new-run
agent4drone/.venv/bin/python scripts/audit_general_agent.py --run tmp/general-agent-v1-new-run --output tmp/general-agent-v1-audit.json
```

`prepare` 可通过 `--profile / --study / --cases` 参数更换本地配置、来源任务及脚本回合；任务、snapshot、timeline 和公开脚本复制到本次 `inputs/`，当次维护源码复制到 `source/`，并保存哈希。`run` 使用冻结 coordinator、worker 和 scorer，拒绝覆盖/自动续跑已有尝试；`verify` 复核文件和三层结果。最后的审计只验收此处固定的 8 案例协议，不用于任意自定义任务，也不读取线上凭据。

机器结果与限制见[本地整合验收报告](results/general-agent-integration-20261102/REPORT_CN.md)。任务状态直接导出原 ledger 的指令、绑定、回执和有限投影，不新增完整语言解析器；旧的模型训练/在线门槛保持独立。

| 配置选择 | 首版依据 | 不可推出的结论 |
| --- | --- | --- |
| `task_progress_mode=evidence_harness` | 复用当前自然 runner 的 R2 反馈与 Harness 组合，保留原工具 | 不是所有义务都已结构化；也不是已完成整合版跨任务自然验证 |
| `revision=observed_target_v7` | 复用现有入口、飞行状态、候选修复、终止前进度和已见目标跟进实现 | 局部修复存在不等于搜索稳定成功或成本下降 |
| `scheduler=applicability` | 作为待评价的执行状态感知调度候选；同工具能力下可对照 `failure / fixed / every_step` | 历史受控机制成绩不能当作新版自然调度收益 |
| 静态障碍契约、回合内共享 | 仅同计划、场景、epoch 内的精确 XYZ 终点冲突可复用；入口隔离环境写入，观察到几何改变则失效 | 不能扩展为动态障碍共享、一般路径阻挡共享或其他 UAV 的完成证明 |
| 每回合新 worker；不共享上一回合黑板 | 复用已有隔离与独立评分路径，限制跨任务证据污染 | 不承诺同进程可重复 reset、跨进程恢复旧 plan 或并行训练环境 |
| 完整模型 I/O 归档开启 | 保存请求/响应与 logical/attempt/tool 关联，供后续审计和转换 | 完整交互归档仍不自动成为合格 Teacher 样本 |
| `execution_memory_v7` 不叠加为默认 | 它是独立状态投影组件；有限坐标路线可 compact，其他任务退回 v5；联合配置尚无自然实证 | “最新版本叠加”不是有效性证据。需要输入长度和覆盖检查后另建有版本的候选 |

结果固定分为三层，缺失值保留 `unknown / not_evaluated` 或等价的明确状态，不使用假值代替未知：

| 层 | 回答的问题 | 可接受证据 | 禁止替代 |
| --- | --- | --- | --- |
| `run` | 运行是否正常返回、失败、超时或取消？是否排空并封存？ | worker/请求控制/归档的状态、错误、退出与哈希 | `Agent.success=True` 不能当整题成功 |
| `public_evidence` | Agent 可见反馈确认了什么？还有哪些不确定或未核验条款？ | 原指令、ledger 实际证据、公开进度和 frame 状态及来源；模型最终陈述单独保留 | 不把模型声称、程序 `completed` 或未解析条款自动升级为任务完成 |
| `independent_evaluation` | 对封存后的同一回合，隔离 checker 是否判整题完成？ | 独立 scorer、封存状态哈希、原 checker 版本、通过/失败/未评分原因 | 不用运行正常返回或公开会话阈值代替 checker；未排空回合不强行评分 |

此处 `public_evidence` 是证据视图，不是新训练标签或独立通用 verifier。完整自然语言义务判定尚未实现；不能将“ledger 没有列出未完成项”解释成“没有剩余要求”。

### 两条路径的最小失败恢复语义

| 触发 | 允许的后续行为 | 必须保留的区分 |
| --- | --- | --- |
| 明确动作失败或部分完成 | 核对执行主体与实际效果；在已有子程序修复范围内处理，或交回模型用原工具完成剩余工作 | requested / applied / remaining 分开；失败后不能默认所有动作均未发生 |
| 超时、传输异常或效果未知 | 在总时限允许时读取公开状态；不能安全确认时保留未知并交回模型/结束 | 不把未知归为无副作用，也不盲目重放整个计划 |
| 原工具改变相关状态、场景/epoch 或依赖改变 | 使旧计划失效；模型根据新反馈重新组织后续动作 | 原计划 resume、原工具修复和新决策分开记录 |
| `needs_replan / completed / cancelled` 上 resume | 拒绝终态恢复，保持原 plan 状态与原因；必要时使用原工具处理未完成条款 | 控制请求被拒绝不是新执行失败，也不产生“成功恢复”机会 |
| 回合硬截止或取消 | 停止准入，排空或终止 worker，封存；满足条件才评分 | 全局截止后没有可继续的恢复机会；cancel 不撤销已经执行的动作 |

现有行为依据 [Harness Runtime](../agent4drone/harness_core.py)、[技能](../agent4drone/harness_skills.py)、[原工具反馈 ledger](../agent4drone/task_progress.py) 和[回合请求控制](../scripts/harness_request_limits.py)。本表统一术语，不表示已有一个能替代全部原工具语义的通用恢复器。任务成功分母、真实恢复机会分母、被执行的恢复行为及最终完成数必须分别报告。

## Verified Results — 本页核查得到的事实

- 代码有集中式主 Agent、30 个常驻工具、两类完整语法子程序、有限证据 ledger，以及每回合新 worker/独立 scorer 的本地链。不同 API/GUI/CLI 不会自动读取新 profile；本次只确立一个维护入口。
- `controller/default_templates.py` 当前有 58 个模板，归为 8 个 category；分类是任务来源信息，不是 58 道整合版成功样本。生成器可依据场景类型/对象绑定筛选模板，仍需 W2 核查可解性与独立划分。
- `execution_memory_v7` 的 compact 选择基于完整路线语法。既有跨任务回放 14 原题均回退 v5，见[原报告](results/execution-memory-v7-cross-task-20261023/REPORT_CN.md)。因此首版不据名称将它与 `observed_target_v7` 自动叠加。
- 历史机制、自然校准、局部搜索及本地运行器证据保留各自协议和分母，见[实验依据](EXPERIMENT_EVIDENCE_CN.md)。本页不新增自然任务成功率，也不重新解释历史负结果。

## Working Hypotheses — 待检验主张

核心候选问题仍是：**执行状态感知的调度与失败恢复，能否在多类多无人机任务中减少无效继续、重复执行和失败后遗漏，同时改善成功率或成本？** 先检验统一系统整体可行性，再评价调度、状态表示和有范围的跨机反馈复用；平台执行能力与模型决策能力分别归因。机制新增点、与相关工作的差异及其创新性还需独立分析，不能仅由本页范围规格推出。

## Open Questions / Next Actions — 完成边界与后续门槛

| 门槛 | 需要交付的证据 | 当前含义 |
| --- | --- | --- |
| 第一项：范围与本地整合候选 | 本覆盖表、可保存/校验 profile、参数化维护入口、两路径及异常收尾的本地组合记录、三层结果与封存复核 | 本轮工作的验收范围；具体通过数量以本轮机器记录为准 |
| W1 在线派发接线 | — | 本地候选完成后仍须推进；不是只填一个预算数字 |
| W2 / W6a 整体自然能力 | 固定开发覆盖集与全分母；同一版本覆盖 F1–F6、原工具及子程序；记录模型自然选择和真实恢复机会 | 脚本回合不能替代；无子程序匹配的任务也须计入整体评价 |
| W2 / W3 后训练准备 | 新独立划分、约 2B 候选输入/模板核验、正式监督职责、样本转换/过滤/mask | 当前尚未冻结，既有开发题不能改称未见测试 |

暂缓任意自然语言义务图、每 UAV 独立 LLM 协商、动态障碍共享/动态责任转移、跨进程恢复旧 plan、持续移动目标跟踪、硬实时编队及真实无人机验证。必要性由主问题和失败证据决定，不因“通用”一词无界扩展实现。

系统叙述以上述统一框架和本地机制为主体。仿真平台、任务来源、原 Agent/工具链和复用算法须按[上游来源](upstream/README.md)规范归属；本地新增范围参考[项目指南](PROJECT_GUIDE_CN.md)与各实现报告。公开展示应明确区分复用组件、本人新增、脚本验收、真实模型结果和未来工作。
