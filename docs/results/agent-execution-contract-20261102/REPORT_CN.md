# 第二项：统一任务状态、失败恢复与完整运行链——本地验收

日期：2026-11-02。**第二项的最小运行契约已实现并通过本地验收。** 原工具和子程序使用同一公开状态投影；失败、部分完成和效果未知分开；回合终止、归档完整性、批次续行和独立任务评分分开。正常预算结束不再必然截断后续已排定槽位，未知用量或未排空仍停止整批。

本轮使用真实Agent/SDK与本地ASGI仿真，但模型响应为显式脚本，**新增真实provider调用为0**。没有修改前一轮DeepSeek原始结果、补跑搜索整合槽或选择新的最优Harness；没有生产Teacher数据或训练。协议见[执行契约](../../AGENT_EXECUTION_CONTRACT_CN.md)，配置和脚本见[用例目录](../../../experiments/agent-execution-v1/README.md)。

## 交付内容

1. [统一公开执行状态](../../../agent4drone/task_execution_state.py)：保存原文、有限义务、实体绑定、requested/applied/remaining、回执来源与frame游标。缺失效果为null，历史unknown不会因后续查询而改成成功；后续同请求成功或公开状态核实只作为跟进证据，避免继续建议重放旧问题。原工具与子程序实际调用均可追溯，`mission_completion=not_evaluated`始终保留。
2. [恢复接线](../../../agent4drone/harness_integration.py)：仅新v2配置启用有限的模型可见恢复投影；可以建议核查、原工具修复或恢复同一plan，但不自动retry。ready/paused、依赖失效、终态与全局停止分别处理。投影在最终消息哈希记录前加入，v1保持原输入语义。
3. [终止分类](../../../scripts/episode_termination.py)及[维护入口](../../../scripts/general_agent.py)：父进程验证join/退出封存后分类；可评分与可继续不同。token/HTTP上限及明确的任务总截止可结束本回合后继续既定槽位；partial轨迹可留作评分证据，不能自动作为Teacher样本。
4. [v2配置](../../../experiments/agent-execution-v1/profile-main.json)：仍选`evidence_harness + observed_target_v7`，新增`task_execution_state_v1`。调度按显式profile分别验收；`every_step`用于构造暂停机会，不是认定它比applicability更好。新协议增加持久start/exit记录；每回合新进程，执行停止后冻结、排空、封存，再启动独立scorer。

v2是新本地配置；原API/GUI默认与v1配置不变。**新v2契约尚未进行真实provider验收，通用在线采数入口也尚未完成**。

## 正式验收分母

8个批次全部在执行前冻结，90份运行源码相同。计划14回合，启动13回合，独立评分12回合；1个未知用量后的槽未运行，1个未排空worker被终止且不评分。8份封存合计1,169个文件项校验通过；不是1,169项独立行为实验。

| 批次 / 用例 | 实际观察 | 独立评分与批次行为 |
| --- | --- | --- |
| [main：raw_repair](main-run-01/summary.json) | 地面move_to收到明确IDLE错误；查询状态后完成起飞、转向、悬停；没有实际执行失败的移动 | checker通过，继续 |
| main：unknown_after_commit | 实际起飞后模拟丢回执；公开工具只返回中性超时；脚本先查询状态，再转向/悬停；起飞物理事件仅1次 | checker通过；历史起飞效果仍为unknown，公开查询单独记录 |
| main：terminal_resume | 三步子程序completed后再次resume被拒，实际动作没有重放 | checker通过，继续 |
| main：validation_repair | 感知参数缺失被拒；修正参数后继续原工具完成任务 | checker通过；工具普通返回不冒充调用正确 |
| [resume：pause_resume](resume-run-01/summary.json) | 真实暂停、恢复同一plan，cursor按实际执行推进，三项物理动作各1次 | checker通过，继续 |
| resume：invalidated_raw | 暂停后原工具改变依赖，旧plan变为needs_replan；resume被拒，再用原工具悬停完成 | checker通过；保留原plan失效原因 |
| [budget：token_stop / after_budget](budget-run-01/summary.json) | 第一个回合已知累计52个合成token触顶，后续请求被拒；第二槽仍启动并正常返回 | 两者checker均失败；第一档案partial、第二complete，均评分，不混淆返回与任务成功 |
| [unknown：unknown_usage / after_unknown](unknown-run-01/summary.json) | 首槽usage缺失，结束封存后停批；后一槽没有派发 | 首槽checker失败；后一槽未运行，不能算失败或零成本成功 |
| [cancel](cancel-run-01/summary.json) | 起飞后取消，后续转向/悬停未执行 | checker失败，停批 |
| [timeout](timeout-run-01/summary.json) | 单模型请求超时；迟到响应排空，冻结后的动作未执行 | checker失败，停批 |
| [deadline](deadline-run-01/summary.json) | 任务总截止，迟到响应排空且usage明确，后续动作未执行 | checker失败；分类为正常预算结束，本批无剩余槽 |
| [undrained](undrained-run-01/summary.json) | 请求未在排空期内退出，父进程kill并join；没有启动scorer | 未评分，停批；实际原因在process.kill_reason=transport_not_drained，分类器对缺完整worker报告返回invalid_worker |

六个功能正例checker通过，六个已评分停止/提前声明案例checker失败；这些是预设契约结果，不计算自然模型成功率。原checker只检查高度与航向；起飞次数、转向、悬停、恢复及无重放另外依据公开API、工具跨度和物理事件核验。所有用例使用同一已知开发任务`c00d8b78`，不代表F1–F6覆盖或独立泛化。

## 检查与可复核证据

- [独立验收](acceptance.json)：`valid=true / contract_passed=true`，13个执行案例的63条证据断言通过；不是额外pytest测试数。原始ledger、frame、模型出站、工具回执、物理事件、冻结边界、退出封存与评分相互核对。
- [验证记录](validation.json)：原目录前后不变；篡改scorer的副本被拒绝，不生成行为指标。审计器和依赖快照见`analysis-source/`。
- [集成回归](pytest-integrated.log)：818项通过；[独立SDK回归](pytest-adapter.log)：54项通过；合计872项，无跳过。SDK原始行为对照在独立解释器运行，避免Agent已有全局补丁污染；3条已有弃用提示保留。
- 新增模块/接线测试共222项：公开状态81、终止分类102、工作流边界31、真实消息接线8。部分完成、矛盾回执、异步重叠、错误执行主体等在这里验证，**本批没有ASGI部分完成后续做的完整槽**，不以unknown回执案例替代它。

开发联调曾发现“地面rotate实际允许，未构成预想失败”及“模拟回执丢失文字暴露受控标记”，均在正式冻结前修正；输入哈希追加顺序也经独立review修正。前期17个本地联调回合单独记录，不累计为正式样本；参见[开发与测试记录](development-notes.json)。没有删除旧负结果或改变旧DeepSeek分母。

## 完成边界与下一项

本轮完成的是统一任务状态、恢复建议与本地完整回合的最小实现和证据链。它没有证明模型会自主正确恢复，也没有证明成功率提高或token下降。通用自然语言完成判定、任意效果自动核实、同进程训练reset、正式Teacher转换与通用在线采数仍未完成。

后续可进入任务覆盖与独立划分，把正常任务、失败机会与机制适用性分开组织。真实模型验证需另冻新版本、任务与额度；不得将本批受控回复改称模型自主选择，也不得自动续用上批剩余额度。
