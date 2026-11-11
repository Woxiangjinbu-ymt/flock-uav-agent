# 第一项：通用 Agent 范围与本地整合版本验收

日期：2026-11-02。**第一项的范围规格与本地整合候选已完成。** 交付六类目标任务覆盖表、一套可保存的 profile、参数化维护入口、三层结果和实际回合验收。本报告不将 W0/W1 的其余工作整体标为完成。

## 实现与版本

- [范围规格](../../GENERAL_AGENT_SCOPE_CN.md)：F1–F6 任务族、集中式模型职责、原工具/子程序边界、输入权限、来源与暂缓项。1–5 UAV 是后续整体评价目标，不是本批验证规模。
- [profile](../../../experiments/general-agent-v1/profile.json) 与[严格配置模块](../../../agent4drone/agent_profile.py)：`general-agent-v1-local`，固定 `evidence_harness + observed_target_v7 + applicability`；保留 R2 ledger，不叠加 execution_memory_v7。字段错误、非法值及实际实现预算漂移会拒绝运行。
- [维护入口](../../../scripts/general_agent.py)：`prepare / run / verify`，参数化 profile、任务来源、脚本案例及输出；冻结当次源码、任务、snapshot、timeline 和公开脚本。执行冻结的 coordinator→新 worker→独立 scorer；已有目录和日志拒绝覆盖/自动续跑。
- [本地脚本传输](../../../scripts/general_agent_local.py)：只用脚本响应与 MockTransport；执行主体按实际公开工具回执绑定，不能从隐藏 fixture 给模型喂 actor ID。脚本耗尽/调用未公布工具即报错。
- [独立验收器](../../../scripts/audit_general_agent.py)：不导入 Agent、运行器、SDK 或仿真器，对固定案例的原始记录复算。封存校验与行为验收分别进行。

默认 profile 指纹：`29dc52337261c118ea486b8600f5d726a8f63016cb238468d2bbc79f15a8c2bd`。真实 Agent/SDK 和本地 ASGI 仿真服务执行了工具调用；模型选择、最终陈述和 usage 均为合成脚本。禁止外部 socket，所有 worker/scorer 记录的网络尝试为空，真实 provider 调用为 **0**。

回合墙钟上限 500 秒、单请求 5 秒、排空 0.5 秒、HTTP attempts 8、输出 256 token、响应后累计 token 停线 1000；watchdog 30 秒是本地试验保险，可能早于任务截止。已有迭代 600、递归 1800、每计划 API 2000、修复 16、搜索轮数 16 纳入配置核对。

## 本次实际证据

固定 [8 案例](../../../experiments/general-agent-v1/cases.json)，复用两个已暴露的 F1 开发任务 `b1eb0270 / 898cfdca`，不改原指令、初态或 checker。本批不是六类任务覆盖实验，也不代表多机自然协作已通过。正式运行见 [run-01](run-01/manifest.json)，全部回合见 [results.json](run-01/results.json)。

| 案例 | 实际路径 / 停止 | 动作数 | 独立 checker |
| --- | --- | --- | --- |
| raw | 原工具起飞→转向→悬停 | 3 | 通过 |
| skill | `simple_chain` 起飞→转向→悬停 | 3 | 通过 |
| second-task | 不同任务/初态，`simple_chain` 起飞→升高→悬停 | 3 | 通过 |
| premature | 脚本宣称完成且 Agent 正常返回，没有执行动作 | 0 | 失败 |
| http_error | 起飞后，第3次模型请求返回 HTTP 500，停止原因 `http_error` | 1 | 失败 |
| cancel | 起飞后，第3次请求处取消并冻结，停止原因 `cancelled` | 1 | 失败 |
| request_timeout | 起飞后，第3次请求超过真实5秒时限；迟到响应排空，停止原因 `request_timeout` | 1 | 失败 |
| usage_missing | 起飞后，第3次响应缺 usage；阻止交付模型并停止，原因 `unknown_usage` | 1 | 失败 |

表中 3 个 checker 通过和 5 个预期失败全部保留；它们用于验证契约，不计算为自然任务成功率。`premature` 明确证明正常返回、文本 `[TASK DONE]` 与整题评分不同。失败后的已执行起飞保留在封存世界中，没有回滚或误当作未执行。

| 验证层 | 结果 | 原始记录 |
| --- | --- | --- |
| 新增 profile/入口边界与协调测试 | 92 项通过 | 同下完整测试记录，包含49项 profile 与43项入口测试 |
| 既有请求限制、回合、Harness、归档等相关回归 | 381 项通过；合计 **473**，无失败/跳过 | [pytest.log](pytest.log)、[pytest.xml](pytest.xml) |
| 实际运行链 | 8 个 worker + 8 个独立 scorer，均先退出/join再启动下一进程 | [process-ledger.jsonl](run-01/process-ledger.jsonl) |
| 独立行为核验 | **248/248** 通过；审计加严后同产物复核仍通过，不增加分母 | [冻结审计结果](acceptance.json)、[当前审计复核](acceptance-current.json) |
| 封存完整性 | **324** 个文件哈希及清单一致；三层结果可从源记录复算 | [campaign-seal.json](run-01/campaign-seal.json)、[verify.json](verify.json) |

测试有一条既有 Starlette/AnyIO 弃用提示；没有为本任务改依赖。248 项核验覆盖实际动作来源、物理起飞、技能帧、trace/HTTP归档/请求控制关联、故障真正触发、三层语义、canary 隔离、静态障碍约束和进程串行退出。未在本批实际触发未排空强杀或父进程中断；相应回归/既有证据和新场景证据不能混算。

run-01 冻结后，审计器在已有响应交付断言中补查 `http_attempt_end.complete` 与 `delivered_to_sdk` 一致；只读重验同一批产物仍为248项通过。88份冻结源码中仅审计器有此单行加严，87份运行/评估实现未变。冻结审计器与原结果保持原样，当前审计器快照见 [audit-current.py](audit-current.py)。

## 状态与结果含义

每个 worker 将原 ledger 导出为 `policy/task-state.json`，保留原始指令、有限条款/实体绑定、观测与执行事件、公开进度及来源。无法完整解释的要求仍以原文保留；不存在“未列出来就算完成”的规则。

[summary.json](run-01/summary.json) 中，`run` 记录正常返回/停止/取消/超时及归档完整性；`public_evidence` 保存公开证据、原文与模型陈述，整题仍为 `not_evaluated`；`independent_evaluation` 单独保存 scorer 结论。合并结果属于回合结束后的评价产物，不回流到策略输入。`verify.valid` 只证明封存一致，固定行为验收以 `acceptance.passed` 为准。

本次复用已有静态时间线审查、任务 API 权限限制、障碍几何前后核对、归档闭合与独立评分；新增完整源码/输入清单校验，拒绝冻结后插入未声明文件或符号链接。原 API/GUI 默认配置未改变，原始后训练 HTML 和历史实验封存未修改。

## 复现与剩余边界

按[规格中的命令](../../GENERAL_AGENT_SCOPE_CN.md#本地验收与复现)创建新的输出目录。正式证据只读复核：

```bash
agent4drone/.venv/bin/python scripts/general_agent.py verify --run docs/results/general-agent-integration-20261102/run-01
agent4drone/.venv/bin/python docs/results/general-agent-integration-20261102/run-01/source/scripts/audit_general_agent.py --run docs/results/general-agent-integration-20261102/run-01 --output tmp/general-agent-v1-audit-new.json
```

运行依赖本机 Agent/server 环境；manifest 记录绝对位置，移动仓库时应重新 prepare，不承诺旧运行目录可搬移执行。开发联调 `tmp/general-agent-integration-dev-01` 同为8案例且独立核验通过；它不是新增独立分母，正式结论只引用 run-01。

再用固定开发覆盖集验证 F1–F6 的自然能力。父进程中断虽有 kill/join 清理，若中断发生在 launch 返回前，现有 process journal 不能完整追溯该已启动 PID；本批不主张这一边界已验收。Teacher 数据、SFT、RL、独立测试和真实无人机能力均未因此完成。
