# Harness 无候选退出前进度确认 v6：离线配对结果

日期：2026-10-23。按[冻结前协议](PROTOCOL_CN.md)，独立[`run-20261023-01`](run-20261023-01/summary.json)一次完成原7题×2版本，共14次；没有修改样本、初态、预算或择优重跑。

**v6修复了本批b1ad的完成反馈：在无候选退出前增加一次公开进度读取，缓存95%更新为100%，由`needs_replan/no_public_candidate`转为`completed/skill_public_stop_condition`。** 七对任务的全部动作请求序列保持一致；b1ad实际API为818→819，其余六对无新增读取或终态变化。原搜索整题checker仍为1/3→1/3，目标访问数和路径不变；这是完成报告路径修复，不是新增搜索成功或模型能力提升。

本批使用真实本机模拟器和原checker，但技能由脚本明确选择并start，没有LLM、自然Agent选技能或raw接手。三个搜索问题、两个短技能正对照、两个完整语法负对照分开报告，不把混合7题比例当Benchmark成功率。

## 原因与最小实现

上一批[公开退出证据](previous-v5-public-exit-evidence.json)显示：最后一次技能进度读取为95%，之后6个move、cursor达到176；下一无动作轮直接抛出`no_public_candidate`，跳过函数尾部不足10次周期的进度刷新。评估端最终公开读数100%、原checker通过，但这些结束后证据不属于技能当时已知。

`terminal_progress_v6`只在原`not made_attempt and progress <= 95`分支插入一次普通`PublicCall("get_mission_progress")`。核验公开session/source/scope、错误与矛盾字段和数值后，严格>95才正常完成，≤95保留无候选失败；读失败不猜测完成、不重试。新增读沿用原deadline/API/scene/epoch/取消守卫，不给予尾部读取特权或额外预算。

`terminal_progress_check`保存trigger、旧缓存及读取状态：`awaiting_public_read`、`unavailable`、有效后的`confirmed`与evidence_ref/值。**confirmed只表示读数已确认，不代表超过阈值或任务完成。** 运行时status和原checker仍独立记录。实现与限制见[契约审计](CONTRACT_AUDIT_CN.md)和[最小补丁](minimal-fix.patch)。

本轮仅修改三个Harness生产文件，注册v6并继承v5共享/静态约束；默认legacy及历史revision保留。没有改server、搜索候选、范围、排名、动作、常规10次刷新周期、repair/round退出、语法、任务或checker。standalone启用为`HarnessRuntime(..., revision="terminal_progress_v6", static_obstacle_contract=True)`；集成须`UAV_HARNESS_REVISION=terminal_progress_v6`和精确`UAV_HARNESS_STATIC_OBSTACLES=1`。

## 四类边界：专项与原任务分开验收

[61项专项](TERMINAL_PROGRESS_TESTS.json)首次全部通过；加[既有376项回归](regression-existing.json)，共[437项不重复测试](regression-validation.json)。新测试使用脚本公开回执和实际runtime/候选逻辑，不调用模型或运行新仿真；不能与14次任务trial相加。

| 边界 | 已验证的专项行为 | 本批固定任务覆盖 |
|---|---|---|
| 有效且严格>95 | 一次读取后完成，记录实际公开ref；原动作、cursor和修复计数不变 | b1ad v6触发，读取100后完成 |
| 有效但≤95 | 95也不成功；确认读数后仍`no_public_candidate`，不再搜索/循环读 | 原7题未在该新分支产生此反例，依据专项 |
| 读取失败/无效 | 错误、异常、错误session/source、含100但带错误、非法/矛盾值均不得完成或用旧缓存兜底 | 原固定trial未注入错误，依据专项 |
| 预算/生命周期不足 | API无余量不invoke；最后一个槽可用于一次确认；读前/后deadline、取消、epoch变化均不能越界完成 | 原固定trial未触发这些边界，依据专项 |

专项另覆盖四种scheduler、旧v5无新增读、共享/static继承，以及终态控制无重放。边界数据和各输入before/after hash保留在metadata，不因本批一个成功路径就声称四类都已在真实任务中覆盖。

## 固定执行条件与完整性

- 原full100完整对象图和时间线，反序列化前验证manifest与来源SHA；恢复结构/公开初态hash、空黑板、新R2账本一致。逐题交替版本先后，每trial新进程。
- 两臂同`applicability`、500秒deadline、2,000 API、16repair、16round、90秒watchdog；14次实际用时0.0138–4.5746秒，未触发deadline、watchdog或API上限。这不是实机飞行耗时。
- 两臂均`static_obstacle_contract=True`：隔离本机ASGI、未启动lifespan/background simulator、socket阻断、相同task allowlist拒绝admin/地图写入。时间线只推进targets/environment及时钟/RNG。评估端初尾障碍digest和7对初态一致，14次writer违规、模型调用、网络连接尝试均0；私有几何与digest不进入policy。
- 公开变化守卫无法可靠检测不可见地图更新，writer隔离仍是适用前提；初尾digest相等本身不证明任意动态世界中间没有变化。
- 14/14完整性通过；冻结76份源码、19项fixture依赖及协议，原manifest全410项运行后不变。329项[本批seal](run-20261023-01/campaign-seal.json)重验一致，当前源码无漂移。
- 28次重复start/终态resume均未改变frames、世界、API或物理事件；有plan的20次明确拒绝，另8次为unsupported/不存在plan负对照。正常completed同样不可重放。
- [冻结前核验](preflight-validation.json)与[历史保护记录](preservation-validation.json)保留旧四批256/273/287/315项seal不变、HTML不变及初始11,564个非Markdown文件无缺失。累积只改原4个Harness/policy文件，本轮只改其中3个Harness文件。
- [独立批次审计](INDEPENDENT_AUDIT_CN.md)的[352项不变量核验](independent-verification.json)全部通过；另7项历史cost诊断为5项相等、2项仅耗电不同，合计357项相符/2项差异。它们是静态证据核对，不是两个无效trial，也不与437项测试或14次trial相加。

## 原7题的完整结果

以下v5→v6来自本批同时冻结的两臂；API包括技能结束后的那一次评估端公开session读取，checker子检查单列。

| 任务 | 公开进度/访问数 | 原checker子检查 | 动作尝试 | API | runtime结果 |
|---|---|---|---:|---:|---|
| `7eba7390` | 均33%、1/3 | 0/1→0/1 | 85→85 | 346→346 | 均`needs_replan/round_limit_below_threshold` |
| `24ac1838` | 均38%、38/100 | 0/1→0/1 | 156→156 | 612→612 | 均`needs_replan/local_repair_budget_exhausted` |
| `b1ad4f3e` | 均100%、20/20 | 1/1→1/1 | 191→191 | 818→819 | `needs_replan/no_public_candidate`→`completed/skill_public_stop_condition` |
| `b1eb0270` | 均0% | 2/2→2/2 | 3→3 | 11→11 | 均`completed/skill_public_stop_condition` |
| `898cfdca` | 均0% | 2/2→2/2 | 3→3 | 11→11 | 均`completed/skill_public_stop_condition` |
| `784d71f8` | 均0% | 0/4→0/4 | 0→0 | 1→1 | 均`unsupported_skill_or_full_task_grammar` |
| `1905e0ad` | 均0% | 0/6→0/6 | 0→0 | 1→1 | 均`unsupported_skill_or_full_task_grammar` |

原搜索整题checker两臂均1/3；两个短技能两臂均2/2整题通过，其任务不要求session搜索进度；两个附加义务负对照继续拒绝且零动作，不改写成成功。详细数据见[`results.json`](run-20261023-01/results.json)。

7/7动作参数序列一致，无首次动作分歧、新增/消失失败签名或改变公开访问数。搜索move错误分别为0、17、10，两臂相同；共享跨actor重复及同actor重复仍为0。原修复预算退出和覆盖不足仍未解决，不能因新的完成状态名称而更新这些成绩。

## b1ad的一次新增读取及证据链

本批两臂前817个task公开请求，按method/endpoint/params/body及origin kind/tool规范化后相同，包含191个动作；比较忽略plan ID等运行标识，不表示回执、时间戳或能耗逐字相同。最后一个动作均为请求807；新差异发生在请求834的**调用主体与时机**：v5此时已经退出，834是评估端读取；v6的834仍属于技能`get_mission_progress`，之后才是评估端835及隔离checker836。

v6请求834是committed=True、action=False的GET `/sessions/current`，返回当前session `35ce64d8`、100%、20/20。它对应Harness call `...:818`、evidence `e1635`，call-start/end序号2810/2811；从raw公开字段独立重建的normalized receipt SHA与日志均为`6c601a4d8aaeeedc7595a5b5fd8709d4731f7242c39a18a3f7156f4cf02b7ade`。读发生在同frame，之后没有任何skill动作。

[独立审计](INDEPENDENT_AUDIT_CN.md)确认之后event2813为completed plan_update、2814为completed skill_exit。该读取的history_events与energy均为空。本批没有另存ledger正文，证据关联来自raw公开回执、冻结投影规则和Harness ref/回执hash交叉核验。

[聚焦公开完成审计](PUBLIC_COMPLETION_AUDIT_CN.md)的[233项检查](PUBLIC_COMPLETION_AUDIT.json)无失败，59个输入与seal及回读hash一致；确认read-end2811之后才有STOP2812、completed2813。七对每臂438个动作请求及回执状态一致，411个已记录物理效果的actor、起终点与路径几何一致；该比较明确排除时钟、持续时间和耗电，不声称完整物理日志逐字相同。233项是静态审计断言，不增加测试或任务样本分母。

随后`terminal_progress_check`由旧缓存95记录为confirmed、progress=100；frame缓存变100、设置`session_progress_strictly_above_95`并正常completed。cursor/units仍176、repairs仍10，最后一条move及共享记录不改变。技能API817→818，技能progress读取18→19，全部task API818→819，均为实测+1，不是把评估端读取移作策略证据。

v5仍保留最后技能读取772=95、之后6个move、无新终止读与`no_public_candidate`；它的最终评估progress100/checker通过不倒灌进frame。新增[`terminal-progress-diagnostics.json`](run-20261023-01/trials/b1ad4f3e-terminal_progress_v6/terminal-progress-diagnostics.json)记录raw来源、call/ref/hash、动作时序、frame及评估读数；全部14次该诊断issues为0。配对API增量及首次公开请求差异见[summary](run-20261023-01/summary.json)。

## 成本、运行差异与历史v5对照

| 指标 | `24ac1838` v5→v6 | `b1ad4f3e` v5→v6 |
|---|---:|---:|
| 动作尝试 / 成功 / 错误 | 156/139/17→相同 | 191/181/10→相同 |
| 技能API / 全部task API | 611/612→相同 | 817/818→818/819 |
| XY累计路径（m） | 12,424.064934→相同 | 30,809.959906→相同 |
| XYZ累计路径（m） | 13,041.064934→相同 | 31,109.959906→相同 |
| 实测耗电（mAh） | 89.855796108→89.785821351 | 171.585948488→171.575591072 |
| 任务实际墙钟用时（s） | 4.574564→4.281361 | 3.102037→3.112986 |

没有额外动作不代表零成本：b1ad的公开读占用一次原API和时间预算。耗电小差在相同动作/路径下出现，不能解释为新终止读取节能；新增读取在全部动作之后，不可能反向改变之前的动作能耗。原`advance(elapsed)`仍按墙钟推进环境，同初始化不保证每个动作处于同一模拟秒。报告实测差额，不作精细耗电/运行时间的策略收益归因。

[历史复现对照](run-20261023-01/historical-baseline-comparison.json)显示：本批7个v5与上一封存v5的初态、动作序列、公开进度、runtime reason、原checker、错误/共享/新终止诊断指标均一致。全部非能耗cost字段一致；五题耗电也一致，另两题为：

| 任务 | 历史v5耗电 | 本批v5耗电 | 差额（mAh） |
|---|---:|---:|---:|
| `24ac1838` | 89.8538387614 | 89.8557961075 | +0.0019573461 |
| `b1ad4f3e` | 171.6356798984 | 171.5859484883 | -0.0497314101 |

elapsed同样按实际保留，不强求逐字复现。未为了匹配历史能耗重跑，也未将本批两臂较低的耗电值选作系统优化成绩。

[逐动作能耗核验](energy-reproducibility-diagnosis.json)进一步确认：以`1e-8 mAh`区分浮点舍入量级，同批24ac/b1ad分别5/1个动作有差异，历史v5比较分别9/7个动作；这些相同动作回执均位于不同模拟整数秒，差额总和匹配汇总。冻结时间推进与能耗源码支持跨环境时间边界的解释，未读取或虚构具体隐藏天气值。24ac没有触发新确认，b1ad确认又发生在动作之后，均不支持“新读取节能”的因果说法。

## 可保留结论与剩余范围

可保留的是：在原静态writer隔离条件、原预算和完整支持语法下，无候选退出前的一次公开确认能使本批已经完成的任务被Harness正确报告，并有真实ref和有效阈值证据；没有新增动作或改整题成绩。未完成、读失败与预算不足的保守行为由61项专项覆盖，固定任务成绩与测试分母独立。

仍未解决7eba覆盖不足、24ac错误预算耗尽、其他停止分支的状态设计、自然Agent选技能与真实动态环境；不扩大本次修复为全TaskState重构、额外恢复循环或模型后训练成果。保持旧失败与限制，不自动追加在线实验。

runner：[`scripts/validate_harness_terminal_progress.py`](../../../scripts/validate_harness_terminal_progress.py)，SHA256 `5e082fa3b9f44b0bfba8bf12669d09e4cb8285e7450ae4bb2bc5151ca8f87a01`；源码/fixture/解释器与依赖来源见[manifest](run-20261023-01/manifest.json)。复现需不存在的新输出目录，不覆盖本批：

```sh
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python scripts/validate_harness_terminal_progress.py --output /absolute/path/to/a-new-terminal-progress-run
```

封存后的[完整性与成本审计](INDEPENDENT_AUDIT_CN.md)、[公开完成证据审计](PUBLIC_COMPLETION_AUDIT_CN.md)均已保存于本目录外层；run内源码、结果与seal保持不变。
