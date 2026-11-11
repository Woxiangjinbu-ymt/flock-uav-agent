# Harness 无候选退出前进度确认：配对验证协议

日期：2026-10-23。**执行就绪，执行前冻结本协议与源码。** 基准`shared_endpoint_v5`，候选`terminal_progress_v6`；最小实现、四类边界专项、既有回归及独立runner审查已通过，根代理授权按固定14次执行。不得修改旧runner、旧协议或封存结果。

## 已知问题与唯一候选变化

上一批b1ad的[公开终止诊断](../harness-shared-endpoint-20261023/completion-reporting-diagnosis.json)显示：技能最后一次公开progress请求772为95%/19个目标；之后6个move、cursor到176，无动作空轮直接抛出`no_public_candidate`，绕过函数尾部的进度刷新。最终评估端公开请求834已100%/20个目标、原checker通过，但技能仍`needs_replan`。这是终止反馈缺口，不能把评估端结果倒灌成策略当时已知。

本候选只在原`not made_attempt and progress <= 95`无候选出口前增加一次正常公开`get_mission_progress`确认。读数有效且严格>95时，按现有public stop条件完成；有效但≤95仍保留无候选退出；读失败或无效不得猜测完成。读取通过现有runtime deadline/API/scene/epoch守卫，不增加预算、不重启搜索、不重试或接手。不改变每10次move的周期刷新、路径、候选、排名、repair、rounds、共享记忆和语法范围，不同时修复其他停止分支。

实现记录`frame.state.terminal_progress_check`：trigger=`no_public_candidate`、旧cached_progress_percent、status从`awaiting_public_read`到回执后`unavailable`并附evidence_ref，有效后`confirmed`并附progress_percent。严格>95更新缓存和public_stop_condition后正常return，得到`completed/skill_public_stop_condition`；≤95仍`needs_replan/no_public_candidate`；无效为`public_progress_unavailable`；无API余量为`api_budget_exhausted`且不invoke。状态字段只是待核对记录，不能代替独立raw公开回执。

[契约审计](CONTRACT_AUDIT_CN.md)及[输入hash](CONTRACT_AUDIT_SOURCES.json)无阻断；既有376项＋新增61项专项，共437项不重复测试通过，见[回归汇总](regression-validation.json)、[专项metadata](TERMINAL_PROGRESS_TESTS.json)。[runner自检](runner-diagnostics-check.log)仅读取旧7题v5的1,800条task请求及合成helper，核对raw进度、调用引用/hash、错误endpoint/未提交读取/跨plan引用/确认后动作反例，不计新仿真。最终runner SHA256=`5e082fa3b9f44b0bfba8bf12669d09e4cb8285e7450ae4bb2bc5151ca8f87a01`，独立只读审查确认无阻断。

## 专项验证四类边界

1. **已达阈值：** fresh公开进度严格>95，记录可追溯来源后完成；动作/cursor/剩余工作不得伪造变化，不能使用评估端读数或只看缓存。
2. **尚未达阈值：** 有效0–95（尤其95）仍保留`no_public_candidate`失败反馈；仅一次确认，不循环等待进度变化。
3. **读取失败或无效：** 异常、错误、缺失、矛盾或非法值不构成完成证据，保留真实错误路径，不自动重试。
4. **预算/生命周期不足：** API耗尽、deadline/取消/scene/epoch变化仍受原守卫约束；不得越界读取、额外动作或自动恢复。终态start/resume保持无副作用。

这些是单元/回归边界分母，与原任务14次仿真分开。真实固定fixture不保证覆盖四类反例；不得为制造覆盖改任务、fixture或注入返回值。

## 固定样本、初态与隔离

原7题保持文本、用途和顺序：搜索`7eba7390`、`24ac1838`、`b1ad4f3e`；短技能正对照`b1eb0270`、`898cfdca`；完整语法负对照`784d71f8`、`1905e0ad`。7题×2revision＝14次；偶数索引先v5后v6，奇数索引先v6后v5，每trial新进程。不得补样、挑选结果或自动重跑。

沿用原full100完整快照/时间线，反序列化前核验manifest、任务、快照和源场景SHA；恢复结构及公开初态hash一致。公开准备核验后再次恢复，空黑板、新R2账本；同为`applicability`，500秒deadline、2,000 API、16repair、16round，90秒watchdog。驱动器脚本显式选择技能并start至terminal，无LLM、自然Agent选技能、raw接手或额外恢复策略。

**两臂均静态契约True且受相同writer隔离。** 新进程本机ASGI TestClient、不启lifespan/background simulator、socket阻断；相同task allowlist仅公开读和原命令、拒绝admin/地图写入。冻结时间线只含second/targets/environments/rng。独立评估端核验初尾障碍ID/几何digest与原fixture及配对一致；私有几何/digest不进policy、黑板或账本。公开已见同ID几何变化守卫不能可靠检测不可见外部修改，静态writer隔离仍是两版本的前提。隐藏checker仅在技能结束后scoring阶段隔离执行。

## 指标与证据区分

原整题checker、runtime状态与公开session progress分别记录。三个搜索问题单列0–3分母；短技能与拒绝负对照单列，混合7题比例不当Benchmark成功率。报告全部正负结果、总错误/严格共享重复/新失败签名、动作尝试、API、路径、耗电与实际用时。相关错误事件不当统计独立样本。

本轮新增`terminal-progress-diagnostics.json`：独立解析raw task skill的公开progress读取，要求GET `/sessions/current`、action=False、committed=True，记录request ID、session、值、有效性、动作前后时序及对应Harness调用引用与normalized receipt hash；confirmed终止确认须来自同frame，且之后没有skill动作。单列末动作之后的技能读取、最后一次技能值/缓存值、最终runtime终态与终止原因。评估端最终公开读数和原checker只用于独立核验，不作为技能当时证据。诊断引用/hash/完成证据不一致作为基础设施审计失败停止后续派发。

配对比较所有公开请求与首次差异，尤其实际API/技能progress读取增量；不得以“只有一次额外读”的预期代替实测。比较物理动作序列与成本：若动作未变而终态反馈改变，只说明报告路径修复，不声称新增完成能力、搜索分数或效率收益。没有额外动作不等于零成本，新增读取计入原API/时间预算。

终态继续做重复start/terminal resume探针，要求frames、世界、API、物理事件不变。基础设施/hash/网络/预算/静态契约/checker错误或控制副作用停止剩余派发；普通needs_replan、unsupported、预算耗尽与checker失败是有效结果，不触发重试。

## 历史复现、冻结与交付

只读对照上一批`harness-shared-endpoint-20261023/run-20261023-01`的v5：同初态、动作序列、进度、终态、原checker、成本、共享指标与新终止诊断口径。旧批不能代替本批基准。不强求wall-time或耗电逐字一致；`advance(elapsed)`使同初始化也不保证动作逐秒锁步，所有实际差额保留，不择优重跑。

实现与测试就绪后核验source hash，复制同一份源码、协议与manifest再执行。新runner：`scripts/validate_harness_terminal_progress.py`。预留输出：`docs/results/harness-terminal-progress-20261023/run-20261023-01`，必须不存在。封存14次raw API/动作/控制/Harness/三类旧诊断及新增终止诊断、静态契约、原checker、配对和历史比较。最终报告与独立审计置于run外层，不改封存。

冻结前再核对生产源码与测试hash、确认输出目录不存在，再执行一次：

```sh
PYTHONDONTWRITEBYTECODE=1 agent4drone/.venv/bin/python scripts/validate_harness_terminal_progress.py --output docs/results/harness-terminal-progress-20261023/run-20261023-01
```

依赖既有agent Python 3.11虚拟环境及server虚拟环境本地包；无pip安装、模型服务或外网。未来复现须另用不存在的新输出目录，不覆盖本批。
