# FlockCue

**基于执行反馈的多无人机协作智能体框架**

*An Agent Harness for Multi-UAV Coordination with Execution Feedback*

Flock 指协同执行任务的机群，Cue 指一次动作返回后，能指导下一步决策的线索。例如，A 机的到达回执已确认，B 机的动作回执超时。Agent 可以保留 A 的进度，先查询 B 的状态，再决定是否重试。

从这里启动研究用的 Harness。当前基线是 **`general-agent-v5-local`**，用于本地复现和轨迹验证器的数据准备。主 Agent 通过工具控制无人机；Harness 记录谁执行了什么、工具返回了什么，以及哪些任务要求还有待确认。未来的验证器可以消费这些记录。真实 DeepSeek 教师已完成两场 40 题试采，当前 600 题正式测评采用原全量设置。[试采报告](../docs/results/deepseek-teacher-pilot40-live-20261111/REPORT_CN.md) · [基线锁](baseline.lock.json) · [v5 配置](../experiments/hover-compact-contract-v2/profile.json)

## 从仓库根目录运行

先准备 Agent 与仿真服务的两个 Python 环境。[依赖说明](../docs/DEPENDENCY_SETUP_CN.md)

```bash
agent4drone/.venv/bin/python -m agent_harness check
agent4drone/.venv/bin/python -m agent_harness smoke --output experiments/runs/harness-v5-smoke-01
```

`check` 按实际运行器的规则准备临时快照，对照已封存的源码、配置、输入与执行协议，并核对 Python 版本和两个组件的依赖清单。源码有增删或修改时，命令会列出对应路径。底层进程仍从本机两个虚拟环境加载软件包，跨机器复现还需记录和核对实际安装环境。

`smoke` 在指定的新目录准备快照，用脚本模型实际驱动 Agent 和本地仿真，分别启动两个任务回合及其独立评分进程，再验证结果。重复执行时换一个新目录。两条轨迹的任务都是“让 Drone 4 起飞至 12 米，然后执行一次悬停指令”。

| 轨迹 | 原任务评分器 | Harness 对“已按指令完成”的判定 |
| --- | --- | --- |
| 到达高度，缺少显式悬停动作回执 | 通过 | 证据不足 |
| 到达高度，随后有显式悬停动作回执 | 通过 | 完成得到验证 |

打开新运行的 `summary.json` 可看两项判定，`manifest.json` 可查实际执行的源码和 profile。各回合 `trials/<trial_id>/policy/` 下有工具与模型记录，`evaluation/` 存放独立评分。制作验证器输入时按字段和时间截取当步可见的信息；完整目录含后续事件和诊断，不能直接送入学生。两条脚本轨迹用于开发验收。[动作案例](../experiments/hover-compact-contract-v2/cases-action.json) · [验证器数据方案](../docs/audit/trajectory-verifier-plan-20261107/REPORT_CN.md)

## 实现在哪里

| 要查的问题 | 源码入口 |
| --- | --- |
| 本次使用哪套模型、工具、调度与预算 | [研究启动入口](__main__.py) · [配置解析](../agent4drone/agent_profile.py) |
| 主 Agent 如何接入 Harness、执行或中断子程序 | [Agent 接线](../agent4drone/harness_integration.py) · [运行时](../agent4drone/harness_core.py) · [执行子程序](../agent4drone/harness_skills.py) |
| 动作回执如何变成任务进度、恢复依据和完成判定 | [执行状态](../agent4drone/task_execution_state.py) · [悬停判定](../agent4drone/hover_completion.py) |
| 如何封存请求与回应、运行独立评分 | [模型 I/O 归档](../agent4drone/model_io_archive.py) · [快照、worker 与 scorer](../scripts/general_agent.py) |

底层 `general_agent.py prepare` 默认选择早期 v1，产品 API/GUI 也有自己的 Agent 构造方式。研究复现使用本目录显式选择的版本。部分名称含 `r2`、`v2` 或 `validate` 的文件仍被运行器导入。物理迁移这些实现时，需要一起更新快照收集和子进程导入路径，在新进程中比较工具定义、公开回执、结束原因及独立评分。旧版本的配置、轨迹和原评分可以按原批次查阅。[本地 v5 验收](../docs/results/hover-compact-contract-20261103/REPORT_CN.md) · [真实模型 v4 对照](../docs/results/deepseek-hover-pilot-20261103/REPORT_CN.md)

## 与后训练的衔接

先以冻结的 600 题实测当前 FlockCue + DeepSeek。根据独立评分和失败轨迹决定模块取舍。Harness 定型后，将真实教师轨迹绑定到运行版本、场景、回合、逐轮调用与回执，核验本题动作及终态，再依次训练和评测 2B 的 SFT、RL 权重。[本批规格](../docs/specs/05-deepseek-harness-ab600/spec.md) · [后训练方案](../docs/POSTTRAINING_PLAN_CN.md)

原 29 题的真实学生权重筛查已结束，G1、G2 未通过，G3 已在独立批次通过。执行事件与选择性思考资料保留作后续研究。[学生筛查](../docs/results/flockcue-a100-g1g2-20261110/REPORT_CN.md) · [G3 复算](../docs/results/thinking-events-g3-20261108/REPORT_CN.md)
