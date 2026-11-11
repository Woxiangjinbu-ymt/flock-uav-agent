# Harness 失败候选记忆：同初态离线配对协议

日期：2026-10-23。**执行就绪：候选语义及公开诊断已确认，既有259项与专项54项，共313项不重复测试通过；本协议在14次trial之前冻结。** 专项的独立重复核验不增加测试分母。候选名为 `blocked_candidate_v4`，功能测试通过仍不代表整题表现改善。

进入执行的依据：[24ac1838诊断](DIAGNOSIS_CN.md)、[b1ad4f3e诊断](DIAGNOSIS_B1AD_CN.md)、[既有回归metadata](regression-existing.json)、[专项回归metadata](regression-blocked-candidate.json)。执行前核对两份metadata的生产源码SHA与当前文件一致；已封存的diagnosis目录保持只读。

## 问题与候选实施门槛

上一批 `flight_state_v3` 的 `24ac1838` 已消除 IDLE move 错误，但 17 次障碍错误耗尽修复预算。公开回执中有两组同 actor、同目的点再次被拒绝：`4e22b83f` 请求 293/532 的 `(1715,175,20)`，以及 `869a8e16` 请求 313/553 的 `(1715,1155,20)`。需要先区分目的点冲突与依赖起点的路径冲突，检查既有 `tried_edges`、候选选择及公开障碍更新，不能将所有障碍回执一律永久禁止。

已确认的最小语义如下：

- 只接受完整匹配的公开回执：`command=move_to`、`status=error`、`drone_id`与请求一致、`command_outcome=blocked`，且 message 全文为 `Target position conflicts with obstacle: Position conflicts with (circle|ellipse|polygon) obstacle '<name>'`。泛化的blocked、路径冲突、缺字段、错actor或不确定消息不能生成终点不可用记忆。
- 经紧随动作的公开状态读取确认XYZ均未改变，才在本plan的 `frame.state.blocked_endpoints` 保存 actor＋候选index、请求XYZ（本技能z=20）及原move证据。策略只对该actor过滤相应候选index，不跨actor共享或永久改变候选表。
- 换起点、空nearby、增加无关障碍不清除已确认目的点记录；没有TTL。在当前generator/plan内有效，计划结束、epoch改变或外部动作导致失效后退出，不跨计划复用。这是静态fixture范围；既有策略不处理同ID障碍的几何更新，本次不声称动态地图支持。
- 路径冲突继续使用原来与起点有关的 `tried_edges`，不升级为目的点屏蔽。不改变格网、几何碰撞、排名公式、搜索范围或预算。

旧v3日志还显示 `b1ad4f3e` 有一组同actor同目的点重复冲突，纳入同一固定任务与指标，不另扩分支或增样。

候选只处理已诊断的失败候选重复选择；继承 `flight_state_v3` 的公开入口、飞行状态与终态控制。基准为 `flight_state_v3`，候选为 opt-in `blocked_candidate_v4`；默认 `legacy`、v2、v3 保持原行为。不得改变任务语法、搜索区域、候选格网、评分/分配、轮数、起飞高度、修复/API预算、checker 或 server；不加入目标利用、私有障碍、raw 接手或额外恢复策略。

## 固定任务、初态与执行

| 类型 | 任务 ID | 固定用途 |
|---|---|---|
| 三个原搜索问题簇 | `7eba7390`、`24ac1838`、`b1ad4f3e` | 整题搜索、重复失败及非触发行为 |
| 简单链正对照 | `b1eb0270`、`898cfdca` | 保留原完整动作链 |
| 不支持语法负对照 | `784d71f8`、`1905e0ad` | 指定目标和50%/70%义务不能被删除 |

固定 7 题 × 2 revision＝14 次，按表顺序。偶数索引先 v3 后 v4，奇数索引先 v4 后 v3；每次独立进程。全部使用原 full100 完整快照及时间线，并按原 manifest 验证哈希后反序列化。逐次验证结构初态与公开初态 SHA；公开初态准备检查后再次恢复快照，不能将初态私有对象灌入策略。使用新 R2 账本与空黑板。

脚本明确选择技能并 start 到 terminal；不调用 LLM 或外部服务，不评估自然选技能。只使用本机真实 ASGI/server；socket 连接被阻断。最后才由隔离评估器执行原隐藏 checker，checker 和私有状态不能反流到策略。

两臂预算完全相同：500 秒任务 deadline、每计划 2,000 API、16 次修复预算、16 轮搜索，调度 `applicability`。第17次失败导致预算退出仍算原16预算，不改分母。外层90秒 watchdog 属基础设施保护，触发记不完整，不能声称经过500秒或整题判失败。实际墙钟、路径及能耗均原样记录。

诊断与专项测试通过后，runner 才复制并哈希 agent/server/runner 及依赖脚本；同一批次使用同一份源码，不中途修改。新输出目录必须不存在；固定14次，不因正负结果补跑/替换。基础设施、哈希、预算、网络、checker 或终态控制完整性错误停止剩余派发并保留产物。普通 needs_replan、unsupported、预算退出及 checker 不通过不触发重试。

## 指标定义

主结果仍是原整题 checker 与子检查通过数，同时报告公开进度、已访问目标、status/reason/cursor/units/repairs、API、全部动作尝试及成功/错误数、路径、耗电和实际用时。减少重复错误不代表任务成功，也可能仅将预算转移到其他错误上。

独立诊断器只关联 task 阶段 skill 的公开状态、动作请求和返回，记录 request ID、同actor最近公开起点、请求终点及原始回执。诊断分类不作为策略输入，也不调用候选自己的分类器来证明正确性。

- **目的点冲突**：`status=error` 且公开 message 以 `Target position conflicts with obstacle:` 开头；其他格式不猜测为目的点冲突。
- **路径冲突**：`status=error` 且公开 message 含 `Flight path intersects with`；保留其起点依赖，不能等同目的点不可到达。
- **其他错误 / 部分结果 / 未知**：分别保留；不为了降低障碍数吞掉失败。
- **同actor同目的点重复冲突**：在同trial/plan按 actor＋请求XYZ终点分组，仅统计目的点冲突；每组首次以外的事件为重复。保留所有request IDs及公开起点，另列分组数。这个计数不把不同actor错误混在一起。
- **路径冲突重试**：同actor＋请求终点下，起点也相同的事件才计同edge重复；换公开起点再次尝试另列，不能先验判为无益重复。另列跨actor/起点的同目的点路径冲突组，避免“同终点失败多次”被误读为同一失败状态。
- **证据缺口**：缺失公开起点或请求终点单独计数，不填补私有坐标，也不将缺失起点当同edge。
- **独立失败及新增失败**：目的点冲突按actor＋XYZ去重；路径冲突按actor＋公开起点XYZ＋终点XYZ去重；其他错误另加入原message。同题配对时忽略revision产生的planID差异，报告候选相对v3新增、共同、消失的失败签名及request IDs。新增失败可能来自原预算继续探索不同位置，不自动称为回归；同时报告总错误和任务效果。缺坐标的记录不与其他记录合并，并披露此项比较限制。
- **首次动作分歧**：比较规范化 `method/endpoint/params/body` 请求序列，报告共同前缀、首次不同动作及公开前置证据；请求ID、时间戳、planID差异不是策略差异。
- **终态幂等**：每次结束后重复start和终态resume；完整frames、世界hash、API和物理事件数必须不变。有plan时需明确拒绝且不可恢复，不支持语法的无plan情况单列。

两项简单链单独报告原checker；两项负对照单独报告拒绝且零动作。不得把混合7题的通过比例包装为Benchmark成功率。三搜索题固定0–3分母，不据进度或错误变少改任务完成定义。

## 历史一致性与交付

新批次v3与上一批 `docs/results/harness-flight-state-20261023/run-20261023-01` 的v3对照：原初态hash、规范化动作序列、公开进度、最终原因、完整checker、动作/路径/耗电成本，以及按同一新诊断器计算的重复错误指标。历史输入先核验原封存，不修改历史runner或结果；历史只作复现性诊断，不能替代本批v3基准。发现不同需披露，不为追求一致重跑。

保留协议、输入/源码manifest、14次完整API与物理事件、harness/门控、最终状态、原checker、重复失败分类、首次分歧、历史核对及整批封存。报告可包括功能修复成立而整题仍失败、重复变少但总错误/成本未改善等负结果。

runner：`scripts/validate_harness_blocked_candidate.py`。执行使用尚不存在的目录 `docs/results/harness-blocked-candidate-20261023/run-20261023-01`，固定一次14次trial。此研究仅为固定已知任务上的条件性功能及整题结果验证，不是在线LLM端到端性能、独立泛化或模型训练结果。
